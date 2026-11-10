"""N10 - notification observability: sent / delivered / opened / skipped / failed per notif type.

Why this exists
    A push that "does not arrive" has ten possible causes (user switched it off, quiet hours, DND, class muted, daily
    cap, dead token, FCM down, ...). Without numbers every complaint is a guess. This module counts what happened to
    every push and WHY it did not go out, per notification type, and exposes it to Prometheus / Grafana and to staff.

Events (every one is a counter, per notif type)
    sent        a push was handed to FCM for a token (first attempt; retries are counted as `retried`)
    delivered   FCM ACCEPTED it. This is "accepted by FCM", not "shown on the phone" - FCM gives no device receipt.
    failed      FINAL failure for a token, with the reason = FCM error code (unregistered, unavailable, ...)
    retried     a transient failure was queued for another try (Celery backoff)
    skipped     we did not push on purpose, with the reason (see SKIP_REASONS)
    opened      the user tapped it (surface = push | banner | list), reported by the app

STORAGE
    Cumulative counters (never reset, no migration) in the Django cache (Redis in prod) - the Prometheus idiom: scrape
    them as `counter`, use rate()/increase(). Plus 5-minute bucket counters (40 days) with no type label for the
    "last N minutes" staff summary and the SLO alert. If the cache is down recording falls back to a bounded
    per-process dict and logs one ERROR a minute - recording NEVER raises into a push or a request.
    Cache flush = counters restart at 0; Prometheus treats that as a normal counter reset.

CARDINALITY
    The `type` label is normalised to the known NotifType values + a few chat / call push types; anything else is
    "other". Reasons / codes / surfaces come from fixed lists. So a bug can never create unbounded label values.

Likha gaya sirf code padh kar - chalaya nahi.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300
BUCKET_TTL_SECONDS = 40 * 86400
MAX_SUMMARY_MINUTES = 24 * 60

EV_SENT = "sent"
EV_DELIVERED = "delivered"
EV_FAILED = "failed"
EV_RETRIED = "retried"
EV_SKIPPED = "skipped"
EV_OPENED = "opened"
EVENTS = (EV_SENT, EV_DELIVERED, EV_FAILED, EV_RETRIED, EV_SKIPPED, EV_OPENED)

# ---- why a push was NOT sent --------------------------------------------------------------------------------------
SKIP_PUSH_DISABLED = "push_disabled"      # master push switch off
SKIP_TYPE_MUTED = "type_muted"            # type in muted_types
SKIP_CHANNEL_OFF = "channel_off"          # N1 matrix: push chip off for this type
SKIP_FOCUS = "focus"                      # N5 focus / exam mode
SKIP_LOW_PRIORITY = "low_priority"        # N5 low tier rolled up into the daily summary
SKIP_DND = "dnd"                          # pause-all (do not disturb) is on
SKIP_QUIET_HOURS = "quiet_hours"          # inside the daily quiet window
SKIP_SCOPE_MUTED = "scope_muted"          # N9 this class / chat / post is muted
SKIP_REMINDER_CAP = "reminder_cap"        # N6 daily reminder cap reached
SKIP_REMINDER_COLLAPSED = "reminder_collapsed"  # N6 same-type reminder already pushed a moment ago
SKIP_DEDUPE = "dedupe"                    # N3 same event already sent to this token
SKIP_NO_TOKENS = "no_tokens"              # user has no registered device
SKIP_NOT_ALLOWED = "not_allowed"          # allowed_channels_for said no, reason not itemised (should stay ~0)
SKIP_ERROR = "error"                      # the decision code itself raised
SKIP_REASONS = (
    SKIP_PUSH_DISABLED, SKIP_TYPE_MUTED, SKIP_CHANNEL_OFF, SKIP_FOCUS, SKIP_LOW_PRIORITY, SKIP_DND, SKIP_QUIET_HOURS,
    SKIP_SCOPE_MUTED, SKIP_REMINDER_CAP, SKIP_REMINDER_COLLAPSED, SKIP_DEDUPE, SKIP_NO_TOKENS, SKIP_NOT_ALLOWED,
    SKIP_ERROR,
)

#: NotificationPreference.push_block_reason() -> our reason
_BLOCK_TO_SKIP = {"focus": SKIP_FOCUS, "low": SKIP_LOW_PRIORITY, "dnd": SKIP_DND, "quiet": SKIP_QUIET_HOURS}

# ---- why a push FAILED (normalised FCM error code) -----------------------------------------------------------------
FAIL_UNREGISTERED = "unregistered"
FAIL_CODES = (
    FAIL_UNREGISTERED, "not_found", "sender_id_mismatch", "invalid_argument", "unavailable", "internal",
    "resource_exhausted", "deadline_exceeded", "third_party_auth_error", "unknown", "fcm_down", "other",
)
#: dead token = normal churn (uninstall / token rotation): not counted against the delivery SLO.
DEAD_CODES = frozenset({FAIL_UNREGISTERED, "not_found", "sender_id_mismatch"})

OPEN_SURFACES = ("push", "banner", "list", "other")

#: push `type` values that are not core NotifTypes but are real, separate kinds of push (message app / calls).
EXTRA_TYPES = (
    "chat_message", "chat_digest", "announcement", "announcement_digest", "incoming_call", "call_cancelled",
    "missed_call", "mention", "reaction", "badge_sync", "message_request",
)
OTHER_TYPE = "other"

_SAFE = re.compile(r"[^a-z0-9_]")
_known_types_cache: List[str] = []
_known_types_set: set = set()

_local: Dict[str, int] = {}
_local_lock = threading.Lock()
_last_warn = [0.0]


# ---------------------------------------------------------------------------------------------------------------------
# normalisation (pure)
# ---------------------------------------------------------------------------------------------------------------------
def known_types() -> List[str]:
    """Every value the `type` label may take (stable order, "other" last)."""
    if not _known_types_cache:
        try:
            from .models import Notification

            core = [str(v) for v in Notification.NotifType.values]
        except Exception:  # pragma: no cover - model import problem must not break recording
            core = []
        seen, out = set(), []
        for v in list(core) + list(EXTRA_TYPES):
            if v not in seen:
                seen.add(v)
                out.append(v)
        out.append(OTHER_TYPE)
        _known_types_cache[:] = out
        _known_types_set.clear()
        _known_types_set.update(out)
    return list(_known_types_cache)


def normalize_type(value) -> str:
    t = _SAFE.sub("", str(value or "").strip().lower())
    if not _known_types_set:
        known_types()
    return t if t and t in _known_types_set else OTHER_TYPE


def type_of_push(data) -> str:
    """Push `data` dict -> type label. Rich pushes carry `notif_type`; chat / call pushes carry `type`."""
    if not isinstance(data, dict):
        return OTHER_TYPE
    return normalize_type(data.get("notif_type") or data.get("type"))


def normalize_skip_reason(reason) -> str:
    r = str(reason or "").strip().lower()
    return r if r in SKIP_REASONS else SKIP_NOT_ALLOWED


def normalize_fail_code(code) -> str:
    c = _SAFE.sub("_", str(code or "").strip().lower()).strip("_")
    if c in FAIL_CODES:
        return c
    # firebase_admin sometimes reports the python class name ("UnregisteredError", "QuotaExceededError" ...)
    c2 = c.replace("_error", "").replace("error", "").strip("_")
    aliases = {
        "unregistered": FAIL_UNREGISTERED, "senderidmismatch": "sender_id_mismatch", "quotaexceeded": "resource_exhausted",
        "quota_exceeded": "resource_exhausted", "thirdpartyauth": "third_party_auth_error",
        "third_party_auth": "third_party_auth_error", "invalidargument": "invalid_argument",
        "deadlineexceeded": "deadline_exceeded", "resourceexhausted": "resource_exhausted", "notfound": "not_found",
    }
    return aliases.get(c2) or (c2 if c2 in FAIL_CODES else "other")


def normalize_surface(surface) -> str:
    s = str(surface or "").strip().lower()
    return s if s in OPEN_SURFACES else "other"


# ---------------------------------------------------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------------------------------------------------
def _bucket(ts: Optional[float] = None) -> int:
    return int((time.time() if ts is None else ts) // BUCKET_SECONDS)


def _ckey(event: str, ntype: str, reason: str = "") -> str:
    return f"notifops:c:{event}:{ntype}:{reason}"


def _bkey(bucket: int, event: str, reason: str = "") -> str:
    return f"notifops:b:{bucket}:{event}:{reason}"


def _warn_cache_down() -> None:
    now = time.monotonic()
    if now - _last_warn[0] > 60:
        _last_warn[0] = now
        logger.error("notification_ops: cache unavailable - counters are process-local until it is back", exc_info=True)


def _incr(key: str, by: int = 1, ttl: Optional[int] = None) -> None:
    try:
        cache.add(key, 0, ttl)  # ttl None = never expires (cumulative counters)
        cache.incr(key, by)
    except Exception:
        _warn_cache_down()
        with _local_lock:
            if len(_local) > 20000:  # bounded
                _local.clear()
            _local[key] = _local.get(key, 0) + by


def _read_many(keys: Iterable[str]) -> Dict[str, int]:
    keys = list(keys)
    out: Dict[str, int] = {}
    if not keys:
        return out
    try:
        for i in range(0, len(keys), 1000):
            got = cache.get_many(keys[i:i + 1000])
            for k, v in got.items():
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    pass
    except Exception:
        _warn_cache_down()
    with _local_lock:
        for k in keys:
            if k in _local:
                out[k] = out.get(k, 0) + _local[k]
    return out


def reset_local() -> None:
    """Tests only: forget the per-process fallback counters."""
    with _local_lock:
        _local.clear()


# ---------------------------------------------------------------------------------------------------------------------
# recording (never raises)
# ---------------------------------------------------------------------------------------------------------------------
def record(event: str, notif_type, *, reason: str = "", n: int = 1) -> None:
    """Count `n` occurrences of `event` for `notif_type` (+ `reason` for skipped / failed / opened)."""
    try:
        if event not in EVENTS or n <= 0:
            return
        ntype = normalize_type(notif_type)
        if event == EV_SKIPPED:
            reason = normalize_skip_reason(reason)
        elif event == EV_FAILED:
            reason = normalize_fail_code(reason)
        elif event == EV_OPENED:
            reason = normalize_surface(reason)
        else:
            reason = ""
        _incr(_ckey(event, ntype, reason), n)
        b = _bucket()
        _incr(_bkey(b, event), n, BUCKET_TTL_SECONDS)
        if reason:
            _incr(_bkey(b, event, reason), n, BUCKET_TTL_SECONDS)
    except Exception:  # pragma: no cover - belt and braces
        logger.exception("notification SLO counter increment failed")


def record_skip(notif_type_or_data, reason: str, n: int = 1) -> None:
    """A push we chose not to send. Accepts a push `data` dict or a plain type string."""
    t = type_of_push(notif_type_or_data) if isinstance(notif_type_or_data, dict) else notif_type_or_data
    record(EV_SKIPPED, t, reason=reason, n=n)


def record_opened(notif_type, surface: str = "push") -> None:
    record(EV_OPENED, notif_type, reason=surface)


def record_send(data, *, attempted: int, ok: int, failed_by_code: Optional[Dict[str, int]] = None,
                retried: int = 0, retry_attempt: int = 0) -> None:
    """One multicast finished. `attempted` tokens were sent, `ok` accepted by FCM, `failed_by_code` = FINAL failures per
    FCM code, `retried` transient ones queued for another try. Retry attempts do not count as `sent` again."""
    try:
        ntype = type_of_push(data)
        if attempted and not retry_attempt:
            record(EV_SENT, ntype, n=attempted)
        if ok:
            record(EV_DELIVERED, ntype, n=ok)
        if retried:
            record(EV_RETRIED, ntype, n=retried)
        for code, count in (failed_by_code or {}).items():
            record(EV_FAILED, ntype, reason=code, n=count)
    except Exception:  # pragma: no cover
        logger.exception("notification SLO batch record failed")


# ---------------------------------------------------------------------------------------------------------------------
# the one decision helper the sender calls (pure given a preference row)
# ---------------------------------------------------------------------------------------------------------------------
def push_skip_reason(pref, notif_type, now=None) -> Optional[str]:
    """None = the preferences allow a push; else the reason (one of SKIP_REASONS). `pref` None = user has no row yet
    = defaults = allowed. PARITY: returns None exactly when 'push' is in pref.allowed_channels_for(notif_type, now)
    (tests_notification_qa checks this over a whole matrix)."""
    if pref is None:
        return None
    t = str(notif_type)
    locked = t in pref.NEVER_MUTE_TYPES
    if not pref.push_enabled:
        return SKIP_PUSH_DISABLED
    if t in (pref.muted_types or []) and not locked:
        return SKIP_TYPE_MUTED
    if not locked and pref.override_for(t).get("push", True) is False:
        return SKIP_CHANNEL_OFF
    block = pref.push_block_reason(t, now)
    if block:
        return _BLOCK_TO_SKIP.get(block, SKIP_NOT_ALLOWED)
    if "push" not in pref.allowed_channels_for(t, now):
        return SKIP_NOT_ALLOWED
    return None


# ---------------------------------------------------------------------------------------------------------------------
# reading: staff summary + SLO
# ---------------------------------------------------------------------------------------------------------------------
DEFAULT_SLO = {
    "delivery_success_pct": 97.0,   # delivered / (delivered + failed, dead tokens excluded)
    "alert_window_minutes": 15,
    "alert_min_attempts": 50,       # below this a window is too small to judge
    "max_not_allowed_pct": 5.0,     # skips we could not explain, of all skips (instrumentation hole)
}


def get_slo() -> dict:
    cfg = dict(DEFAULT_SLO)
    cfg.update(getattr(settings, "NOTIFICATION_SLO", None) or {})
    return cfg


def _window_buckets(minutes: int, now: Optional[float] = None) -> List[int]:
    minutes = max(1, min(MAX_SUMMARY_MINUTES, int(minutes)))
    last = _bucket(now)
    count = max(1, -(-minutes // (BUCKET_SECONDS // 60)))  # ceil
    return list(range(last - count + 1, last + 1))


def summarize_last(minutes: int = 15, now: Optional[float] = None) -> dict:
    """Totals over the last `minutes` (5-minute resolution). Never raises."""
    buckets = _window_buckets(minutes, now)
    names: List[Tuple[str, str]] = [(e, "") for e in EVENTS]
    names += [(EV_SKIPPED, r) for r in SKIP_REASONS]
    names += [(EV_FAILED, c) for c in FAIL_CODES]
    names += [(EV_OPENED, s) for s in OPEN_SURFACES]
    keys = {(b, e, r): _bkey(b, e, r) for b in buckets for e, r in names}
    values = _read_many(keys.values())
    total: Dict[Tuple[str, str], int] = {}
    for (b, e, r), key in keys.items():
        total[(e, r)] = total.get((e, r), 0) + values.get(key, 0)

    def g(e, r=""):
        return total.get((e, r), 0)

    failed_by_code = {c: g(EV_FAILED, c) for c in FAIL_CODES if g(EV_FAILED, c)}
    failed_dead = sum(v for c, v in failed_by_code.items() if c in DEAD_CODES)
    failed_real = g(EV_FAILED) - failed_dead
    delivered = g(EV_DELIVERED)
    judged = delivered + failed_real
    success = round(100.0 * delivered / judged, 2) if judged else None
    return {
        "minutes": max(1, min(MAX_SUMMARY_MINUTES, int(minutes))),
        "sent": g(EV_SENT),
        "delivered": delivered,
        "failed": g(EV_FAILED),
        "failed_dead_tokens": failed_dead,
        "failed_other": failed_real,
        "retried": g(EV_RETRIED),
        "skipped": g(EV_SKIPPED),
        "opened": g(EV_OPENED),
        "delivery_success_pct": success,
        "open_rate_pct": round(min(100.0, 100.0 * g(EV_OPENED) / delivered), 2) if delivered else None,
        "skipped_by_reason": {r: g(EV_SKIPPED, r) for r in SKIP_REASONS if g(EV_SKIPPED, r)},
        "failed_by_code": failed_by_code,
        "opened_by_surface": {s: g(EV_OPENED, s) for s in OPEN_SURFACES if g(EV_OPENED, s)},
    }


def breaches(summary: dict, slo: Optional[dict] = None) -> List[str]:
    """Human-readable SLO problems for a summary (empty list = healthy or too little traffic to judge)."""
    slo = slo or get_slo()
    problems: List[str] = []
    judged = summary["delivered"] + summary["failed_other"]
    if judged >= slo["alert_min_attempts"] and summary["delivery_success_pct"] is not None:
        if summary["delivery_success_pct"] < slo["delivery_success_pct"]:
            codes = ", ".join(f"{c}={n}" for c, n in sorted(summary["failed_by_code"].items(), key=lambda kv: -kv[1])
                              if c not in DEAD_CODES) or "no code"
            problems.append(
                f"push delivery {summary['delivery_success_pct']}% < {slo['delivery_success_pct']}% ({codes})"
            )
    skipped = summary["skipped"]
    if skipped >= slo["alert_min_attempts"]:
        unexplained = summary["skipped_by_reason"].get(SKIP_NOT_ALLOWED, 0) + summary["skipped_by_reason"].get(SKIP_ERROR, 0)
        pct = 100.0 * unexplained / skipped
        if pct > slo["max_not_allowed_pct"]:
            problems.append(f"{round(pct, 1)}% of skipped pushes have no clear reason (not_allowed / error)")
    return problems


def totals_by_type(top: int = 25) -> List[dict]:
    """Cumulative numbers per type (since the counters started), busiest first. For the staff table."""
    types = known_types()
    keys = []
    for t in types:
        keys += [_ckey(EV_SENT, t), _ckey(EV_DELIVERED, t), _ckey(EV_RETRIED, t)]
        keys += [_ckey(EV_OPENED, t, s) for s in OPEN_SURFACES]
        keys += [_ckey(EV_SKIPPED, t, r) for r in SKIP_REASONS]
        keys += [_ckey(EV_FAILED, t, c) for c in FAIL_CODES]
    v = _read_many(keys)
    rows = []
    for t in types:
        sent, delivered = v.get(_ckey(EV_SENT, t), 0), v.get(_ckey(EV_DELIVERED, t), 0)
        opened = sum(v.get(_ckey(EV_OPENED, t, s), 0) for s in OPEN_SURFACES)
        skipped = {r: v.get(_ckey(EV_SKIPPED, t, r), 0) for r in SKIP_REASONS}
        failed = {c: v.get(_ckey(EV_FAILED, t, c), 0) for c in FAIL_CODES}
        if not (sent or delivered or opened or sum(skipped.values()) or sum(failed.values())):
            continue
        rows.append({
            "type": t, "sent": sent, "delivered": delivered, "opened": opened,
            "open_rate_pct": round(min(100.0, 100.0 * opened / delivered), 2) if delivered else None,
            "retried": v.get(_ckey(EV_RETRIED, t), 0),
            "skipped": {r: n for r, n in skipped.items() if n},
            "failed": {c: n for c, n in failed.items() if n},
        })
    rows.sort(key=lambda r: (r["sent"] + sum(r["skipped"].values())), reverse=True)
    return rows[: max(1, top)]


# ---------------------------------------------------------------------------------------------------------------------
# Prometheus text (scraped by Prometheus -> Grafana; deploy/monitoring/prometheus_notification_rules.yml)
# ---------------------------------------------------------------------------------------------------------------------
def prometheus_text() -> str:
    lines: List[str] = []
    types = known_types()

    def head(name, help_, kind):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")

    def sample(name, labels, value):
        if value is None:
            return
        lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
        lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    plain = [(EV_SENT, "learnscroll_notification_push_sent_total", "pushes handed to FCM (one per device token)"),
             (EV_DELIVERED, "learnscroll_notification_push_delivered_total",
              "pushes ACCEPTED by FCM (not a device receipt)"),
             (EV_RETRIED, "learnscroll_notification_push_retried_total", "transient failures queued for a retry")]
    dims = [
        (EV_OPENED, "learnscroll_notification_opened_total", "notifications the user opened", "surface", OPEN_SURFACES),
        (EV_SKIPPED, "learnscroll_notification_push_skipped_total", "pushes deliberately not sent, by reason", "reason",
         SKIP_REASONS),
        (EV_FAILED, "learnscroll_notification_push_failed_total", "final push failures, by FCM error code", "code",
         FAIL_CODES),
    ]
    keys = []
    for t in types:
        keys += [_ckey(e, t) for e, _, _ in plain]
        for e, _, _, _, values in dims:
            keys += [_ckey(e, t, v) for v in values]
    v = _read_many(keys)

    for e, name, help_ in plain:
        head(name, help_, "counter")
        for t in types:
            n = v.get(_ckey(e, t), 0)
            if n:
                sample(name, {"type": t}, n)
    for e, name, help_, label, values in dims:
        head(name, help_, "counter")
        for t in types:
            for val in values:
                n = v.get(_ckey(e, t, val), 0)
                if n:
                    sample(name, {"type": t, label: val}, n)

    windows = {"15m": 15, "1h": 60, "24h": 1440}
    sums = {w: summarize_last(m) for w, m in windows.items()}
    head("learnscroll_notification_delivery_success_pct",
         "delivered / (delivered + failed, dead tokens excluded) in the window (SLO 97)", "gauge")
    for w, s in sums.items():
        sample("learnscroll_notification_delivery_success_pct", {"window": w}, s["delivery_success_pct"])
    head("learnscroll_notification_open_rate_pct", "opened / delivered in the window", "gauge")
    for w, s in sums.items():
        sample("learnscroll_notification_open_rate_pct", {"window": w}, s["open_rate_pct"])
    head("learnscroll_notification_window_events", "events in the window (sum over all types)", "gauge")
    for w, s in sums.items():
        for e in ("sent", "delivered", "failed", "skipped", "opened", "retried"):
            sample("learnscroll_notification_window_events", {"window": w, "event": e}, s[e])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------------------------------
# alert (Celery beat task core.notification_slo_watch)
# ---------------------------------------------------------------------------------------------------------------------
def check_slo_and_alert(window_minutes: Optional[int] = None) -> dict:
    """Look at the last window; log an ERROR (+ Sentry message when installed) for every breach."""
    slo = get_slo()
    minutes = int(window_minutes or slo["alert_window_minutes"])
    summary = summarize_last(minutes)
    problems = breaches(summary, slo)
    summary["breaches"] = problems
    if problems:
        msg = f"NOTIFICATION SLO ALERT (last {minutes} min): " + "; ".join(problems)
        logger.error(msg)
        _sentry(msg, problems)
    return summary


def _sentry(msg: str, problems: List[str]) -> None:
    try:
        import sentry_sdk

        new_scope = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with new_scope() as scope:
            scope.set_tag("alert_type", "notification_slo")
            scope.fingerprint = ["notification-slo", problems[0].split(" ")[0]]
            sentry_sdk.capture_message(msg, level="error")
    except Exception:  # sentry not installed / not configured
        pass
