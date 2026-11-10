"""
T3.8 -- recording + reporting of the lifecycle funnel.

record():      idempotent, NEVER raises (analytics must not break signup / asking a doubt).
funnel_report: install -> signup -> onboarding -> first doubt/test  +  D1 / D7 retention.

Definitions (all dates in settings.TIME_ZONE):
  * install     distinct devices whose first launch fell in the window            (FunnelEvent INSTALL)
  * signup      installs whose device later logged in as a user who SIGNED UP around/after that install
                (a returning user logging in on a new phone is NOT a signup)
  * onboarding  of those users, finished OR skipped the onboarding flow (skipped is reported separately)
  * activated   of those users, asked a first doubt OR started a first test  (both reported separately)
  * D1 / D3 / D7  share of signed-up users that opened the app EXACTLY 1 / 3 / 7 days after their signup day.
                Only users old enough to have reached that day are in the denominator.
                (H17: the same milestones are also stored as `retention_d1|d3|d7` rows, written by the server the
                moment the matching `app_open` arrives - see `_record_retention`.)
  * H17 cold start  `interests_saved` + `first_feed` rows carry `seconds_since_signup`; the report shows how many
                new users got their first personalised feed within COLD_START_TARGET_SECONDS (60 s).
Each funnel step is a strict subset of the previous one, so conversion can never exceed 100%.
"""
import logging
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models_funnel import FunnelEvent

logger = logging.getLogger(__name__)
E = FunnelEvent.Event

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
PLATFORMS = {"android", "ios", "web", "other"}
SIGNUP_BEFORE_INSTALL_GRACE = timedelta(days=1)  # clock skew / signup right before the first launch is logged

# H17 -------------------------------------------------------------------------------------------
COLD_START_TARGET_SECONDS = 60          # signup -> interests saved -> first personalised feed
RETENTION_DAYS = (1, 3, 7)              # D1 / D3 / D7
# P-A: the app also measures how long the user waited at Home (feed request started -> first card >= 50 % visible)
# and reports it ONCE for the first feed of a new user. It is stored on the server's own `first_feed` row, never as a
# new client event (so a client still cannot create funnel rows), and only while that row is fresh.
CLIENT_FIRST_FEED_WINDOW = timedelta(minutes=30)
CLIENT_FIRST_FEED_MAX_MS = 120_000
CLIENT_DEVICE_CLASSES = ("low_end", "standard")
_RETENTION_EVENT = {1: E.RETENTION_D1, 3: E.RETENTION_D3, 7: E.RETENTION_D7}


def _today():
    return timezone.localdate()


def record(event, *, user=None, device_id="", dedupe_key=None, props=None, platform="", app_version="",
           created_at=None):
    """Insert one funnel row. Returns True if a NEW row was stored, False if duplicate or on any error."""
    try:
        with transaction.atomic():
            FunnelEvent.objects.create(
                event=event,
                user=user if getattr(user, "pk", None) else None,
                device_id=(device_id or "")[:64],
                platform=(platform or "")[:16],
                app_version=(app_version or "")[:32],
                props=props or {},
                dedupe_key=dedupe_key,
                created_at=created_at or timezone.now(),
            )
        return True
    except IntegrityError:
        return False  # duplicate dedupe_key -> already recorded
    except Exception:  # noqa: BLE001
        logger.exception("funnel.record(%s) failed", event)
        return False


def record_once_per_user(event, user, props=None):
    """signup / onboarding_completed / first_doubt / first_test -- exactly one row per user."""
    if not getattr(user, "pk", None):
        return False
    return record(event, user=user, dedupe_key=f"{event}:{user.pk}", props=props)


def record_install(device_id, platform="", app_version="", user=None):
    return record(E.INSTALL, user=user, device_id=device_id, platform=platform, app_version=app_version,
                  dedupe_key=f"install:{device_id}")


def record_app_open(device_id, user=None, platform="", app_version=""):
    day = _today().isoformat()
    key = f"open:{user.pk}:{day}" if getattr(user, "pk", None) else f"open:d:{device_id}:{day}"
    stored = record(E.APP_OPEN, user=user, device_id=device_id, platform=platform, app_version=app_version,
                    dedupe_key=key)
    if stored and getattr(user, "pk", None):
        _record_retention(user, device_id=device_id, platform=platform, app_version=app_version)
    return stored


def _signup_date(user):
    """Local date the account was created: the `signup` funnel row if there is one (it is what funnel_report
    uses), else the user's own created/joined timestamp. None when neither is known."""
    try:
        row = FunnelEvent.objects.filter(event=E.SIGNUP, user=user).order_by("created_at").values_list(
            "created_at", flat=True).first()
        if row is None:
            row = getattr(user, "date_joined", None) or getattr(user, "created_at", None)
        return _local_date(row) if row else None
    except Exception:  # noqa: BLE001
        logger.exception("funnel._signup_date failed")
        return None


def _record_retention(user, *, device_id="", platform="", app_version=""):
    """H17: today's first app_open of `user` is exactly 1 / 3 / 7 days after their signup day -> store ONE
    `retention_dN` row per user (dedupe_key `retention_dN:<user>`). Server-side, so it cannot be spoofed and the
    client needs no change. Never raises."""
    try:
        sdate = _signup_date(user)
        if sdate is None:
            return
        n = (_today() - sdate).days
        event = _RETENTION_EVENT.get(n)
        if event is None:
            return
        record(event, user=user, device_id=device_id, platform=platform, app_version=app_version,
               dedupe_key=f"{event}:{user.pk}", props={"day": n})
    except Exception:  # noqa: BLE001
        logger.exception("funnel._record_retention failed")


def seconds_since_signup(user):
    """Whole seconds between the user's signup row and now (None when unknown). Used by the H17 events."""
    try:
        row = FunnelEvent.objects.filter(event=E.SIGNUP, user=user).order_by("created_at").values_list(
            "created_at", flat=True).first()
        if row is None:
            row = getattr(user, "date_joined", None) or getattr(user, "created_at", None)
        return max(0, int((timezone.now() - row).total_seconds())) if row else None
    except Exception:  # noqa: BLE001
        return None


def record_interests_saved(user, *, interests=0, study_class="", target_exam="", subjects=0):
    """H17: the user saved class / exam / subjects / interests. First time only (a later edit is not the
    cold-start moment)."""
    secs = seconds_since_signup(user)
    return record_once_per_user(E.INTERESTS_SAVED, user, props={
        "seconds_since_signup": secs,
        "within_target": None if secs is None else secs <= COLD_START_TARGET_SECONDS,
        "interests": int(interests), "subjects": int(subjects),
        "has_class": bool(study_class), "has_exam": bool(target_exam),
    })


def record_first_feed(user, *, count, personalised, cold_start_backfill=False):
    """H17: the first home-feed page served to this user. `count` is the size of the pool (0 would mean an empty
    feed - the cold-start guarantee says that must not happen, the report shows it if it ever does)."""
    secs = seconds_since_signup(user)
    return record_once_per_user(E.FIRST_FEED, user, props={
        "seconds_since_signup": secs,
        "within_target": None if secs is None else secs <= COLD_START_TARGET_SECONDS,
        "count": int(count), "personalised": bool(personalised),
        "backfill": bool(cold_start_backfill), "empty": int(count) == 0,
    })


def record_client_first_feed(user, *, ms, device_class=""):
    """P-A: the phone's own measurement of the user's first feed (milliseconds the user waited at Home).

    Merged into the props of the user's server-side `first_feed` row as `client_ms` (+ `client_device_class`).
    Returns a short status string (the endpoint echoes it; nothing here raises):
      stored | already | no_first_feed | too_late | bad_value | anonymous | error
    Rules: only the FIRST report counts; the `first_feed` row must exist and be younger than
    CLIENT_FIRST_FEED_WINDOW (an old user logging in on a new phone is not a cold start); 0 < ms <= 120 000.
    """
    if not getattr(user, "pk", None):
        return "anonymous"
    if isinstance(ms, bool):
        return "bad_value"
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        return "bad_value"
    if not 0 < ms <= CLIENT_FIRST_FEED_MAX_MS:
        return "bad_value"
    device_class = device_class if device_class in CLIENT_DEVICE_CLASSES else ""
    try:
        with transaction.atomic():
            row = FunnelEvent.objects.select_for_update().filter(event=E.FIRST_FEED, user=user).first()
            if row is None:
                return "no_first_feed"
            if row.created_at < timezone.now() - CLIENT_FIRST_FEED_WINDOW:
                return "too_late"
            props = dict(row.props or {})
            if "client_ms" in props:
                return "already"
            props["client_ms"] = ms
            if device_class:
                props["client_device_class"] = device_class
            row.props = props
            row.save(update_fields=["props"])
        return "stored"
    except Exception:  # noqa: BLE001 - analytics must never break the app
        logger.exception("funnel.record_client_first_feed failed")
        return "error"


def _percentile(sorted_values, q):
    """Nearest-rank percentile of an already sorted list (None for an empty list)."""
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def link_device(user, device_id):
    """The device just authenticated as `user`: attach the anonymous install (and a fresh signup) to them."""
    if not (getattr(user, "pk", None) and device_id):
        return
    try:
        FunnelEvent.objects.filter(device_id=device_id, user__isnull=True).update(user=user)
        # the signup row is written server-side without a device id -> fill it in right after signup
        FunnelEvent.objects.filter(event=E.SIGNUP, user=user, device_id="",
                                   created_at__gte=timezone.now() - timedelta(hours=6)).update(device_id=device_id)
    except Exception:  # noqa: BLE001
        logger.exception("funnel.link_device failed")


# --------------------------------------------------------------------------------------------- report
def _local_date(dt):
    return timezone.localtime(dt).date()


def _pct(num, den):
    return round(100.0 * num / den, 1) if den else None


def funnel_report(days=30, platform=None, today=None):
    days = max(1, min(int(days), 365))
    today = today or _today()
    start_date = today - timedelta(days=days - 1)
    tz_start = timezone.make_aware(datetime.combine(start_date, time.min))

    installs_qs = FunnelEvent.objects.filter(event=E.INSTALL, created_at__gte=tz_start)
    if platform:
        installs_qs = installs_qs.filter(platform=platform)
    installs = list(installs_qs.values("device_id", "user_id", "created_at"))

    user_ids = {i["user_id"] for i in installs if i["user_id"]}

    def per_user(event):
        out = {}
        if not user_ids:
            return out
        for row in FunnelEvent.objects.filter(event=event, user_id__in=user_ids).values("user_id", "created_at", "props"):
            out.setdefault(row["user_id"], row)
        return out

    signup = per_user(E.SIGNUP)
    onboarding = per_user(E.ONBOARDING_COMPLETED)
    doubt = per_user(E.FIRST_DOUBT)
    test = per_user(E.FIRST_TEST)

    # step 2: signed-up users (one install row per user -- the earliest -- so nobody is counted twice)
    first_install_of_user = {}
    for i in sorted(installs, key=lambda r: r["created_at"]):
        uid = i["user_id"]
        if uid and uid not in first_install_of_user:
            first_install_of_user[uid] = i
    signed = {uid: inst for uid, inst in first_install_of_user.items()
              if uid in signup and signup[uid]["created_at"] >= inst["created_at"] - SIGNUP_BEFORE_INSTALL_GRACE}

    step_onboarded = {u for u in signed if u in onboarding}
    step_active = {u for u in step_onboarded if u in doubt or u in test}
    skipped = sum(1 for u in step_onboarded if (onboarding[u].get("props") or {}).get("skipped"))

    counts = [len(installs), len(signed), len(step_onboarded), len(step_active)]
    labels = ["Install", "Signup", "Onboarding done/skipped", "First doubt or test"]
    steps = []
    for n, (label, count) in enumerate(zip(labels, counts)):
        steps.append({
            "step": label,
            "count": count,
            "pct_of_previous": _pct(count, counts[n - 1]) if n else 100.0 if count else None,
            "pct_of_installs": _pct(count, counts[0]),
        })

    # retention: exact-day open after signup day
    opens = defaultdict(set)
    if signed:
        uids = list(signed)
        for chunk_start in range(0, len(uids), 5000):
            chunk = uids[chunk_start:chunk_start + 5000]
            for uid, ts in FunnelEvent.objects.filter(event=E.APP_OPEN, user_id__in=chunk).values_list("user_id", "created_at"):
                opens[uid].add(_local_date(ts))
    retention = {}
    for n in RETENTION_DAYS:
        eligible = retained = 0
        for uid in signed:
            sdate = _local_date(signup[uid]["created_at"])
            if sdate + timedelta(days=n) <= today:
                eligible += 1
                retained += (sdate + timedelta(days=n)) in opens[uid]
        retention[f"d{n}"] = {"eligible": eligible, "retained": retained, "pct": _pct(retained, eligible)}

    # H17 cold start: of the signed-up users, how fast did they get a personalised first feed?
    def _first_event_props(event):
        out = {}
        if not signed:
            return out
        uids = list(signed)
        for chunk_start in range(0, len(uids), 5000):
            chunk = uids[chunk_start:chunk_start + 5000]
            for uid, props in FunnelEvent.objects.filter(event=event, user_id__in=chunk).values_list("user_id", "props"):
                out.setdefault(uid, props or {})
        return out

    saved = _first_event_props(E.INTERESTS_SAVED)
    first_feed = _first_event_props(E.FIRST_FEED)
    secs = sorted(p["seconds_since_signup"] for p in first_feed.values()
                  if isinstance(p.get("seconds_since_signup"), (int, float)))
    cold_start = {
        "target_seconds": COLD_START_TARGET_SECONDS,
        "signed_up": len(signed),
        "interests_saved": len(saved),
        "interests_saved_pct": _pct(len(saved), len(signed)),
        "first_feed": len(first_feed),
        "first_feed_within_target": sum(1 for p in first_feed.values() if p.get("within_target")),
        "first_feed_within_target_pct": _pct(sum(1 for p in first_feed.values() if p.get("within_target")), len(first_feed)),
        "first_feed_personalised": sum(1 for p in first_feed.values() if p.get("personalised")),
        "first_feed_backfilled": sum(1 for p in first_feed.values() if p.get("backfill")),
        "first_feed_empty": sum(1 for p in first_feed.values() if p.get("empty")),
        "median_seconds_to_first_feed": secs[len(secs) // 2] if secs else None,
    }
    # P-A: what the phones measured (ms the user waited at Home for the first feed), reported by the app once
    client_ms = sorted(int(p["client_ms"]) for p in first_feed.values()
                       if isinstance(p.get("client_ms"), (int, float)) and not isinstance(p.get("client_ms"), bool))
    by_class = {}
    for p in first_feed.values():
        v = p.get("client_ms")
        if isinstance(v, (int, float)) and not isinstance(v, bool) and p.get("client_device_class") in CLIENT_DEVICE_CLASSES:
            by_class.setdefault(p["client_device_class"], []).append(int(v))
    cold_start["client_first_feed"] = {
        "count": len(client_ms),
        "median_ms": _percentile(client_ms, 0.5),
        "p75_ms": _percentile(client_ms, 0.75),
        "p95_ms": _percentile(client_ms, 0.95),
        "median_ms_by_device_class": {k: _percentile(sorted(v), 0.5) for k, v in sorted(by_class.items())},
    }

    daily = defaultdict(lambda: {"installs": 0, "signups": 0})
    for i in installs:
        daily[_local_date(i["created_at"]).isoformat()]["installs"] += 1
    for uid in signed:
        daily[_local_date(signup[uid]["created_at"]).isoformat()]["signups"] += 1

    return {
        "window": {"start": start_date.isoformat(), "end": today.isoformat(), "days": days, "platform": platform or "all"},
        "steps": steps,
        "details": {
            "onboarding_skipped": skipped,
            "onboarding_finished": len(step_onboarded) - skipped,
            "first_doubt": sum(1 for u in step_onboarded if u in doubt),
            "first_test": sum(1 for u in step_onboarded if u in test),
            "installs_not_logged_in": len(installs) - len({i["user_id"] for i in installs if i["user_id"]}),
        },
        "retention": retention,
        "cold_start": cold_start,
        "daily": [{"date": d, **v} for d, v in sorted(daily.items())],
    }
