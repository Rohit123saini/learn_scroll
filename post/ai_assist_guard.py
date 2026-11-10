"""
post/ai_assist_guard.py  (composer AI helpers - the COST GUARD)

Every AI call of the composer (caption ideas, tags, 3-line summary, alt text, translate, step hints) goes
through `reserve()` BEFORE the model is called. Nothing here talks to the AI; it only counts and refuses, so
the layers a request passes (cheapest first) are:

    1. DRF burst throttle         per user, per minute          (post/ai_assist_views.py)
    2. result cache               same input -> same answer, costs nothing, is NOT counted
    3. kill switches              AI_ASSIST_ENABLED / AI_ASSIST_DISABLED_FEATURES
    4. provider back-off          after a failed / timed-out model call the model is skipped for a few seconds,
                                  so an outage never makes every composer wait for a timeout
    5. GLOBAL daily budget        AI_ASSIST_GLOBAL_DAILY_CALLS  (all users together - the real money cap)
    6. per-user daily TOTAL       AI_ASSIST_USER_DAILY_TOTAL
    7. per-user daily per FEATURE AI_ASSIST_DAILY_LIMITS[feature]

Counters live in the Django cache (Redis in production - see settings.CACHES - so they are shared by all
workers; LocMem in dev behaves per process, which is fine for a single dev server). No model, no migration.
The "day" is the server's local day (settings.TIME_ZONE, Asia/Kolkata) so the limits reset at local midnight.

`reserve()` returns a `Reservation`. If the model call then fails or returns nothing usable the caller calls
`reservation.refund()`: the USER gets the unit back (a failure must not eat a student's quota), the GLOBAL
counter is NOT refunded (a timed-out call may still have been billed, so the money cap stays conservative).

Pure + small: everything is unit-testable with the cache only.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

# AI-backed features (cost money, are counted). `quality` is rule-based, free and never counted.
AI_FEATURES = ("caption", "tags", "summary", "alt_text", "translate", "hint", "questions")
FREE_FEATURES = ("quality",)
ALL_FEATURES = AI_FEATURES + FREE_FEATURES

# Per user, per day. Image calls cost the most, so they are the tightest. Override in settings
# (AI_ASSIST_DAILY_LIMITS) or per environment - see LearnScroll/settings.py.
DEFAULT_DAILY_LIMITS = {
    "caption": 30,
    "tags": 40,
    "summary": 20,
    "alt_text": 30,
    "translate": 30,
    "hint": 20,
    # TS2: a call writes up to 10 questions = the biggest text answer of all helpers, so the tightest text limit.
    "questions": 8,
}
DEFAULT_USER_DAILY_TOTAL = 100
DEFAULT_GLOBAL_DAILY_CALLS = 20000
DEFAULT_BACKOFF_SECONDS = 20

_DAY_TTL = 26 * 3600  # a counter outlives its day a little, then Redis drops it by itself
_BACKOFF_KEY = "ai_assist:backoff"


# --------------------------------------------------------------------------- errors
class GuardRefused(Exception):
    """The guard says no. `code` is machine readable, `http` the status the view answers with."""

    def __init__(self, code, message, http=429, retry_after=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http
        self.retry_after = retry_after


# --------------------------------------------------------------------------- settings readers
def enabled():
    """Master switch. false -> every AI helper answers 503 `ai_disabled` (the rule-based `quality` still works)."""
    return bool(getattr(settings, "AI_ASSIST_ENABLED", True))


def disabled_features():
    raw = getattr(settings, "AI_ASSIST_DISABLED_FEATURES", ()) or ()
    if isinstance(raw, str):
        raw = raw.split(",")
    return {str(f).strip() for f in raw if str(f).strip()}


def feature_enabled(feature):
    if feature in FREE_FEATURES:
        return True
    return enabled() and feature not in disabled_features()


def daily_limit(feature):
    limits = dict(DEFAULT_DAILY_LIMITS)
    limits.update(getattr(settings, "AI_ASSIST_DAILY_LIMITS", None) or {})
    try:
        return max(int(limits.get(feature, 0)), 0)
    except (TypeError, ValueError):
        return 0


def user_daily_total():
    try:
        return max(int(getattr(settings, "AI_ASSIST_USER_DAILY_TOTAL", DEFAULT_USER_DAILY_TOTAL)), 0)
    except (TypeError, ValueError):
        return DEFAULT_USER_DAILY_TOTAL


def global_daily_calls():
    try:
        return max(int(getattr(settings, "AI_ASSIST_GLOBAL_DAILY_CALLS", DEFAULT_GLOBAL_DAILY_CALLS)), 0)
    except (TypeError, ValueError):
        return DEFAULT_GLOBAL_DAILY_CALLS


def backoff_seconds():
    try:
        return max(int(getattr(settings, "AI_ASSIST_BACKOFF_SECONDS", DEFAULT_BACKOFF_SECONDS)), 0)
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF_SECONDS


# --------------------------------------------------------------------------- counters
def _today():
    return timezone.localtime(timezone.now()).strftime("%Y%m%d")


def seconds_until_reset():
    """Seconds to the next local midnight (what `retry_after` says when a DAILY limit is hit)."""
    now = timezone.localtime(timezone.now())
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(int((tomorrow - now).total_seconds()), 1)


def _user_key(user_id, feature):
    return f"ai_assist:u:{user_id}:{_today()}:{feature}"


def _total_key(user_id):
    return f"ai_assist:u:{user_id}:{_today()}:_total"


def _global_key():
    return f"ai_assist:g:{_today()}"


def _read(key):
    try:
        return int(cache.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _incr(key):
    """Atomic +1 that creates the key (with its TTL) on first use. Returns the new value."""
    try:
        cache.add(key, 0, _DAY_TTL)
        return int(cache.incr(key))
    except ValueError:  # the key was evicted between add() and incr()
        cache.set(key, 1, _DAY_TTL)
        return 1


def _decr(key):
    try:
        if _read(key) > 0:
            cache.decr(key)
    except ValueError:  # already gone - nothing to give back
        pass


class Reservation:
    """One counted AI call. `refund()` gives the USER's units back (never the global one)."""

    def __init__(self, user_id, feature):
        self.user_id = user_id
        self.feature = feature
        self._refunded = False

    def refund(self):
        if self._refunded:
            return
        self._refunded = True
        _decr(_user_key(self.user_id, self.feature))
        _decr(_total_key(self.user_id))


# --------------------------------------------------------------------------- the guard
def reserve(user, feature):
    """Count one AI call for `user` or raise GuardRefused. Call right before the model is called
    (after the result cache was checked - a cache hit must not use up quota)."""
    if feature not in AI_FEATURES:
        raise GuardRefused("unknown_feature", "Unknown AI feature.", http=404)
    if not enabled():
        raise GuardRefused("ai_disabled", "AI help is switched off right now.", http=503)
    if feature in disabled_features():
        raise GuardRefused("feature_disabled", "This AI help is switched off right now.", http=503)

    if cache.get(_BACKOFF_KEY):
        raise GuardRefused("ai_busy", "AI is busy, please try again in a moment.", http=503,
                           retry_after=_backoff_retry_after())

    uid = getattr(user, "id", None)
    taken = []  # (key) rolled back if a later check refuses
    try:
        g_key = _global_key()
        if _incr(g_key) > global_daily_calls():
            taken.append(g_key)
            raise GuardRefused("budget_exhausted", "AI help has reached its limit for today. Please try tomorrow.",
                               http=503, retry_after=seconds_until_reset())
        taken.append(g_key)

        t_key = _total_key(uid)
        if _incr(t_key) > user_daily_total():
            taken.append(t_key)
            raise GuardRefused("quota_exceeded", "You have used all your AI help for today.", http=429,
                               retry_after=seconds_until_reset())
        taken.append(t_key)

        f_key = _user_key(uid, feature)
        if _incr(f_key) > daily_limit(feature):
            taken.append(f_key)
            raise GuardRefused("quota_exceeded", "You have used this AI help as much as allowed for today.",
                               http=429, retry_after=seconds_until_reset())
        taken.append(f_key)
    except GuardRefused:
        # A refused request must not use up anything: give back every unit taken for it, global included
        # (nothing is going to be sent to the model).
        for key in taken:
            _decr(key)
        raise
    return Reservation(uid, feature)


def _backoff_retry_after():
    """Seconds left of the back-off (django-redis knows it; LocMem does not -> the configured length)."""
    try:
        ttl = cache.ttl(_BACKOFF_KEY)
    except Exception:  # LocMem has no ttl()
        ttl = None
    return ttl if isinstance(ttl, int) and ttl > 0 else (backoff_seconds() or 10)


def note_failure():
    """The model call failed / timed out: skip the model for a few seconds (see reserve -> ai_busy)."""
    seconds = backoff_seconds()
    if seconds > 0:
        cache.set(_BACKOFF_KEY, 1, seconds)


# --------------------------------------------------------------------------- reporting (config endpoint)
def remaining(user, feature):
    """Calls the user may still make today for `feature` (min of the feature and the daily total)."""
    if feature in FREE_FEATURES:
        return None
    uid = getattr(user, "id", None)
    left_feature = daily_limit(feature) - _read(_user_key(uid, feature))
    left_total = user_daily_total() - _read(_total_key(uid))
    return max(min(left_feature, left_total), 0)


def snapshot(user):
    """{feature: {enabled, daily_limit, remaining}} for the composer (hides buttons that are off / used up)."""
    out = {}
    for feature in ALL_FEATURES:
        out[feature] = {
            "enabled": feature_enabled(feature),
            "daily_limit": None if feature in FREE_FEATURES else daily_limit(feature),
            "remaining": remaining(user, feature),
        }
    return out
