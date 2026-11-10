"""
tuitionclass/live_abuse.py  -  X12 LIVE CHAT ABUSE GUARDS

The three things that stop ONE person from taking a live class hostage, all cache-based (no DB write on the hot chat
path) and all FAIL OPEN: if the cache is down the guard steps aside - the DRF scope throttle `chat_message_create`
(20/min) and slow mode still apply, so a cache blip never silences a whole room.

    1. FLOOD GUARD      check_flood()       more than LIVE_CHAT_FLOOD_MAX lines in LIVE_CHAT_FLOOD_WINDOW_SECONDS -> 429
    2. DUPLICATE GUARD  check_duplicate()   the same line again inside DUPLICATE_WINDOW_SECONDS -> 409
    3. STRIKES          register_strike()   a blocked line, a flood breach or a repeated line is one strike;
                                            LIVE_STRIKE_LIMIT strikes inside LIVE_STRIKE_WINDOW_SECONDS -> automatic
                                            chat mute (live_moderation.apply_chat_mute, kind auto_mute). Every further
                                            auto mute of the same person in the same class doubles the time (capped x8).
    4. REPORT BUDGET    consume_report_budget()  how many NEW reports one person may file (per class and per hour) -
                                            stops report-brigading and report-spam. Re-reporting the same thing is free.
    5. MUTE CHECK       ensure_can_chat()   a muted person gets 403 chat_muted (+ muted_until) instead of posting.

Host tier (teacher / co-teacher / moderator / on-stage co-host guest) is exempt from 1-3: they are the ones who fix a
room. Nobody is exempt from the mute check itself - a mute only ever lands on people below the host tier
(live_moderation refuses to mute anyone else).

Error codes (the `code` of the standard envelope, see exceptions.py): chat_flood, duplicate_message, chat_muted,
report_limit. `retry_after` (seconds) is filled on the 429s by the exception handler.

SETTINGS (all optional, defaults in brackets)
    LIVE_CHAT_FLOOD_WINDOW_SECONDS   [10]     LIVE_CHAT_FLOOD_MAX        [6]
    LIVE_DUP_GRACE_SECONDS           [3]      (a repeat inside this is a double tap: rejected, but NOT a strike)
    LIVE_STRIKE_WINDOW_SECONDS       [600]    LIVE_STRIKE_LIMIT          [3]
    LIVE_AUTO_MUTE_MINUTES           [5]      LIVE_AUTO_MUTE_MAX_MINUTES [60]
    LIVE_REPORT_MAX_PER_SESSION      [5]      LIVE_REPORT_MAX_PER_HOUR   [20]
    LIVE_MUTE_CACHE_SECONDS          [20]     (0 = look the mute up in the DB on every message)
"""

import logging
import time
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import APIException, Throttled

from .live_cache import live_cache as cache      # L-D #3: shared (optionally separate) Redis, see live_cache.py
from .live_safety import DUPLICATE_WINDOW_SECONDS, text_fingerprint

logger = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(settings, name, default)


class LiveSafetyError(APIException):
    """A refusal with its own stable `code` (+ optional extra top-level fields). Passing a dict as `detail` with a
    string `detail` key is the shape exceptions.py keeps at the top level of the envelope."""

    status_code = 400
    default_detail = "Not allowed."

    def __init__(self, message, *, code, status_code=None, **extra):
        if status_code is not None:
            self.status_code = status_code
        self.machine_code = code
        super().__init__(detail={"detail": message, "code": code, **extra})


def _throttled(message, code, wait):
    exc = Throttled(wait=max(1, int(wait)), detail=message)
    exc.machine_code = code
    return exc


# ---------------------------------------------------------------------------
# cache helpers (fail open)
# ---------------------------------------------------------------------------
def _incr(key, ttl):
    """Atomic counter with a fixed TTL set on the first hit. Returns the new count, 0 when the cache is unusable."""
    try:
        if cache.add(key, 1, ttl):
            return 1
        try:
            return int(cache.incr(key))
        except ValueError:          # key expired between add() and incr()
            cache.set(key, 1, ttl)
            return 1
    except Exception:
        logger.exception("live_abuse: cache counter failed (%s) - guard skipped", key)
        return 0


def exempt(session, user) -> bool:
    """Host tier + on-stage co-host guest: never flood-limited, never struck."""
    try:
        from .views.access import _can_cohost_moderate

        return bool(_can_cohost_moderate(session, user))
    except Exception:
        logger.exception("live_abuse.exempt failed (session %s)", getattr(session, "id", None))
        return False


# ---------------------------------------------------------------------------
# 5. mute check
# ---------------------------------------------------------------------------
def _mute_key(session, user):
    return f"tc:live_mute:{session.id}:{user.id}"


def _mute_cache_seconds():
    try:
        return max(0, int(_cfg("LIVE_MUTE_CACHE_SECONDS", 20)))
    except (TypeError, ValueError):
        return 20


def _query_chat_mute_until(session, user):
    now = timezone.now()
    return (
        session.participants.filter(user=user, chat_muted_until__gt=now)
        .order_by("-chat_muted_until").values_list("chat_muted_until", flat=True).first()
    )


def chat_mute_until(session, user):
    """When this person's chat mute ends, or None. Looked up across ALL their rows of the session, so leaving and
    rejoining (which makes a NEW SessionParticipant row) does not wash a mute off.

    L-D #4: this runs on EVERY chat message, so the answer (also "not muted") is cached per (class, person) for
    LIVE_MUTE_CACHE_SECONDS. apply_chat_mute / clear_chat_mute refresh the cache the moment a moderator acts
    (remember_mute / forget_mute), so on a shared cache a mute bites on the very next message. The cache only ever
    holds an end time, never a longer mute than the DB, and a cache failure just means the DB is asked (fail open)."""
    ttl = _mute_cache_seconds()
    key = _mute_key(session, user)
    now_ts = time.time()
    if ttl:
        try:
            hit = cache.get(key)
        except Exception:
            logger.exception("live_abuse: mute cache read failed (session %s) - asking the DB", getattr(session, "id", None))
            hit = None
        if hit is not None:
            ts = float(hit)
            if ts <= 0:
                return None                     # cached "not muted"
            if ts > now_ts:
                return datetime.fromtimestamp(ts, tz=dt_timezone.utc)
            # the cached mute already ended -> ask the DB (it may have been renewed)
    until = _query_chat_mute_until(session, user)
    if ttl:
        try:
            # `add`, not `set`: a mute that a moderator wrote in the meantime must never be overwritten by this older read.
            if until is None:
                cache.add(key, 0.0, ttl)
            else:
                cache.add(key, until.timestamp(), max(1, min(ttl, int(until.timestamp() - now_ts))))
        except Exception:
            logger.exception("live_abuse: mute cache write failed (session %s)", getattr(session, "id", None))
    return until


def remember_mute(session, user, until):
    """A moderator (or the strike system) just muted `user` until `until`: make the next message see it."""
    ttl = _mute_cache_seconds()
    if not ttl:
        return
    try:
        cache.set(_mute_key(session, user), until.timestamp(), max(1, min(ttl, int(until.timestamp() - time.time()))))
    except Exception:
        logger.exception("live_abuse: remember_mute failed (session %s)", getattr(session, "id", None))


def forget_mute(session, user):
    """The mute was lifted: drop the cached answer so the next message asks the DB."""
    try:
        cache.delete(_mute_key(session, user))
    except Exception:
        logger.exception("live_abuse: forget_mute failed (session %s)", getattr(session, "id", None))


def ensure_can_chat(session, user):
    until = chat_mute_until(session, user)
    if until is None:
        return
    # `muted_until` is an ISO time; the app counts down from it itself (extra fields of an APIException detail are
    # stringified by DRF, so no integer is sent here).
    raise LiveSafetyError(
        "A moderator has muted your chat for now. You can still watch and listen.",
        code="chat_muted", status_code=403, muted_until=until.isoformat(),
    )


# ---------------------------------------------------------------------------
# 1 + 2. flood / duplicate
# ---------------------------------------------------------------------------
def check_flood(session, user):
    """Raise 429 chat_flood when this person is typing faster than the room allows. The FIRST breach of a window is a
    strike (spamming 30 lines must not mean 30 strikes)."""
    window = max(2, int(_cfg("LIVE_CHAT_FLOOD_WINDOW_SECONDS", 10)))
    limit = max(1, int(_cfg("LIVE_CHAT_FLOOD_MAX", 6)))
    now = time.time()
    bucket = int(now // window)
    count = _incr(f"tc:live_flood:{session.id}:{user.id}:{bucket}", window + 1)
    if count <= limit:
        return
    if count == limit + 1:
        register_strike(session, user, "chat_flood")
    wait = window - (now % window)
    raise _throttled("You are sending messages too fast. Slow down a little.", "chat_flood", wait)


def check_duplicate(session, user, text):
    """Raise 409 duplicate_message when the very same line was sent a moment ago. A repeat inside the grace seconds is
    a double tap (rejected silently, no strike); a deliberate repeat after that is a strike."""
    grace = float(_cfg("LIVE_DUP_GRACE_SECONDS", 3))
    key = f"tc:live_dup:{session.id}:{user.id}:{text_fingerprint(text)}"
    now = time.time()
    try:
        if cache.add(key, now, DUPLICATE_WINDOW_SECONDS):
            return
        last = cache.get(key)
    except Exception:
        logger.exception("live_abuse: duplicate check failed (session %s) - skipped", getattr(session, "id", None))
        return
    if last is None or (now - float(last)) > grace:
        register_strike(session, user, "duplicate_message")
    raise LiveSafetyError("You already sent that message.", code="duplicate_message", status_code=409)


# ---------------------------------------------------------------------------
# 3. strikes -> automatic mute
# ---------------------------------------------------------------------------
def register_strike(session, user, reason):
    """Count one strike. Returns the auto-mute info dict {"minutes", "until"} when this strike crossed the limit (the
    person is muted by the time this returns), else None. Never raises."""
    try:
        if exempt(session, user):
            return None
        window = int(_cfg("LIVE_STRIKE_WINDOW_SECONDS", 600))
        limit = max(1, int(_cfg("LIVE_STRIKE_LIMIT", 3)))
        key = f"tc:live_strikes:{session.id}:{user.id}"
        count = _incr(key, window)
        if count < limit:
            return None
        try:
            cache.delete(key)
        except Exception:
            pass
        from . import live_moderation

        base = max(1, int(_cfg("LIVE_AUTO_MUTE_MINUTES", 5)))
        cap = max(base, int(_cfg("LIVE_AUTO_MUTE_MAX_MINUTES", 60)))
        prior = live_moderation.count_events(session, user, "auto_mute")
        minutes = min(cap, base * (2 ** min(prior, 3)))
        until = live_moderation.apply_chat_mute(
            session, user, minutes, actor=None, kind="auto_mute", reason=reason,
            detail=f"{limit} strikes in {window // 60} min",
        )
        return {"minutes": minutes, "until": until}
    except Exception:
        logger.exception("live_abuse.register_strike failed (session %s user %s)",
                         getattr(session, "id", None), getattr(user, "id", None))
        return None


# ---------------------------------------------------------------------------
# 4. report budget
# ---------------------------------------------------------------------------
def consume_report_budget(session, user):
    """Spend one NEW-report credit or raise 429 report_limit. child-safety reports are never limited by the caller
    (live_moderation.file_report skips this for them)."""
    per_session = max(1, int(_cfg("LIVE_REPORT_MAX_PER_SESSION", 5)))
    per_hour = max(1, int(_cfg("LIVE_REPORT_MAX_PER_HOUR", 20)))
    in_session = _incr(f"tc:live_report_s:{session.id}:{user.id}", 6 * 3600)
    if in_session > per_session:
        raise _throttled("You have reported enough in this class. A moderator will look at what you sent.",
                         "report_limit", 600)
    in_hour = _incr(f"tc:live_report_h:{user.id}", 3600)
    if in_hour > per_hour:
        raise _throttled("You have filed many reports in the last hour. Please try again a bit later.",
                         "report_limit", 900)
