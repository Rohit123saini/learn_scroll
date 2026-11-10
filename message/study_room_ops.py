"""
message/study_room_ops.py - X1: observability of STUDY ROOMS (client-reported media health).

Why a separate module (not call_ops): a study room has no CallSession / call_id, and its numbers must never leak into
the 1:1 / group CALL connect-success SLO (different load shape: long sessions, many participants, whiteboard + notes).
Same storage idea as call_ops / post/feed_ops: plain integer counters in 5-minute cache buckets (keys
`srops:<bucket>:<name>`, 40 days, Redis in prod). No migration. Recording must NEVER raise into a request.

WHAT THE CLIENT REPORTS (POST /message/study-room/<conversation_id>/telemetry/, StudyRoomTelemetryView)
    {"kind": "connect", "attempt": "<random id of this join>", "ok": true,  "setup_ms": 2100}
    {"kind": "connect", "attempt": "...", "ok": false, "reason": "ice_failed"}       # reason in FAIL_REASONS, else "other"
    {"kind": "summary", "attempt": "...", "reconnects": 2, "reconnect_ok": 2, "reconnect_fail": 0, "duration_s": 1800}

One `connect` and one `summary` are counted per (conversation, user, attempt). `attempt` is a client-made id of ONE join
(a rejoin after a kick / leave is a new attempt). Policy denials (room locked / full / removed / waiting room / rules not
accepted) are NOT connect failures and are never reported - only "we were let in but the media connection failed".

PRIVACY: only the fixed reason strings and clamped numbers are stored - no conversation id, user id, name, token or error
text goes into a counter key.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Dict, Iterable, List, Optional

from django.core.cache import cache

from .call_ops import FAIL_REASONS, normalize_reason  # same 8 fixed reasons as calls (one classifier on the client)

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300
TTL_SECONDS = 40 * 86400
DEDUPE_TTL_SECONDS = 12 * 3600

SETUP_BOUNDS_MS = (500, 1000, 2000, 3000, 5000, 10000)      # +Inf implicit
DURATION_BOUNDS_S = (60, 300, 900, 1800, 3600, 7200)         # +Inf implicit (session length of one participant)
MAX_DURATION_S = 12 * 3600

_ATTEMPT_RE = re.compile(r'^[A-Za-z0-9_-]{6,64}$')

_local: Dict[tuple, int] = {}
_local_lock = threading.Lock()
_last_warn = [0.0]

DEFAULT_SLO_PCT = 95.0


def _bucket(ts: Optional[float] = None) -> int:
    return int((time.time() if ts is None else ts) // BUCKET_SECONDS)


def _key(bucket: int, name: str) -> str:
    return f"srops:{bucket}:{name}"


def _warn_cache_down() -> None:
    now = time.monotonic()
    if now - _last_warn[0] > 60:
        _last_warn[0] = now
        logger.error("study_room_ops: cache unavailable - counters are process-local until it is back", exc_info=True)


def _incr(name: str, by: int = 1, bucket: Optional[int] = None) -> None:
    bucket = _bucket() if bucket is None else bucket
    key = _key(bucket, name)
    try:
        cache.add(key, 0, TTL_SECONDS)
        cache.incr(key, by)
    except Exception:
        _warn_cache_down()
        with _local_lock:
            if len(_local) > 5000:
                cutoff = bucket - 288
                for k in [k for k in _local if k[0] < cutoff]:
                    _local.pop(k, None)
            _local[(bucket, name)] = _local.get((bucket, name), 0) + by


def _once(token: str) -> bool:
    """True the first time `token` is seen. Cache down => True (count rather than lose it)."""
    try:
        return bool(cache.add(f"srops:once:{token}", 1, DEDUPE_TTL_SECONDS))
    except Exception:
        return True


def _hist_name(prefix: str, bounds: Iterable[int], value: float) -> str:
    for bound in bounds:
        if value <= bound:
            return f"{prefix}_le_{bound}"
    return f"{prefix}_inf"


def clean_attempt(value) -> Optional[str]:
    """A usable attempt id or None (the view answers 400 `invalid_attempt`)."""
    v = str(value or '').strip()
    return v if _ATTEMPT_RE.match(v) else None


def _breadcrumb(message: str, **data) -> None:
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(category="study_room", message=message, level="info", data=data or None)
    except Exception:
        pass


# --------------------------------------------------------------------------
# recording (validated by StudyRoomTelemetryView)
# --------------------------------------------------------------------------
def record_connect(conversation_id, user_id, attempt: str, ok: bool, setup_ms=None, reason=None) -> bool:
    """First media connect of one join finished. Once per (conversation, user, attempt). True when counted."""
    try:
        if not _once(f"conn:{conversation_id}:{user_id}:{attempt}"):
            return False
        if ok:
            _incr("conn_ok")
            try:
                ms = min(max(float(setup_ms), 0.0), 120000.0) if setup_ms is not None else None
            except (TypeError, ValueError):
                ms = None
            if ms is not None:
                _incr(_hist_name("setup", SETUP_BOUNDS_MS, ms))
                _incr("setup_sum_ms", int(ms))
                _incr("setup_n")
            _breadcrumb("connect ok", setup_ms=None if ms is None else int(ms))
        else:
            r = normalize_reason(reason)
            _incr("conn_fail")
            _incr(f"fail_{r}")
            _breadcrumb("connect FAILED", reason=r)
        return True
    except Exception:  # pragma: no cover
        return False


def record_summary(conversation_id, user_id, attempt: str, reconnects=0, reconnect_ok=0, reconnect_fail=0,
                   duration_s=None) -> bool:
    """End-of-session numbers of one participant's join. Once per (conversation, user, attempt). True when counted."""
    try:
        if not _once(f"sum:{conversation_id}:{user_id}:{attempt}"):
            return False

        def clamp(v):
            try:
                return min(max(int(v), 0), 50)
            except (TypeError, ValueError):
                return 0

        n, ok, bad = clamp(reconnects), clamp(reconnect_ok), clamp(reconnect_fail)
        _incr("sessions_reported")
        if n:
            _incr("sessions_with_reconnect")
            _incr("reconnect_events", n)
        if ok:
            _incr("reconnect_ok", ok)
        if bad:
            _incr("reconnect_fail", bad)
            _breadcrumb("reconnect failed", reconnects=n, failed=bad)
        try:
            dur = min(max(float(duration_s), 0.0), float(MAX_DURATION_S)) if duration_s is not None else None
        except (TypeError, ValueError):
            dur = None
        if dur is not None:
            _incr(_hist_name("dur", DURATION_BOUNDS_S, dur))
            _incr("dur_sum_s", int(dur))
            _incr("dur_n")
        return True
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
_NAMES: List[str] = (
    ["conn_ok", "conn_fail", "setup_inf", "setup_sum_ms", "setup_n",
     "sessions_reported", "sessions_with_reconnect", "reconnect_events", "reconnect_ok", "reconnect_fail",
     "dur_inf", "dur_sum_s", "dur_n"]
    + [f"setup_le_{b}" for b in SETUP_BOUNDS_MS]
    + [f"dur_le_{b}" for b in DURATION_BOUNDS_S]
    + [f"fail_{r}" for r in FAIL_REASONS]
)


def _sum_buckets(buckets: Iterable[int]) -> Dict[str, int]:
    buckets = list(buckets)
    keys = {(b, n): _key(b, n) for b in buckets for n in _NAMES}
    try:
        found = cache.get_many(list(keys.values()))
    except Exception:
        _warn_cache_down()
        found = {}
    out = {n: 0 for n in _NAMES}
    for (b, n), k in keys.items():
        try:
            out[n] += int(found.get(k) or 0)
        except (TypeError, ValueError):
            pass
    wanted = set(buckets)
    with _local_lock:
        for (b, n), v in _local.items():
            if b in wanted and n in out:
                out[n] += v
    return out


def _pct(a, b) -> float:
    return round(100.0 * a / b, 2) if b else 0.0


def _quantile(raw: Dict[str, int], prefix: str, bounds, total: int, q: float):
    if not total:
        return None
    running = 0
    for bound in bounds:
        running += raw[f"{prefix}_le_{bound}"]
        if running >= q * total:
            return bound
    return None


def summarize(start_ts: float, end_ts: float) -> dict:
    first, last = _bucket(start_ts), _bucket(end_ts - 1) if end_ts > start_ts else _bucket(start_ts)
    raw = _sum_buckets(range(first, last + 1))
    attempts = raw["conn_ok"] + raw["conn_fail"]
    success = round(100.0 * raw["conn_ok"] / attempts, 2) if attempts else 100.0
    setup_total = sum(raw[f"setup_le_{b}"] for b in SETUP_BOUNDS_MS) + raw["setup_inf"]
    dur_total = sum(raw[f"dur_le_{b}"] for b in DURATION_BOUNDS_S) + raw["dur_inf"]
    rc_total = raw["reconnect_ok"] + raw["reconnect_fail"]
    return {
        "window": {"start": start_ts, "end": end_ts},
        "connect": {
            "attempts": attempts,
            "ok": raw["conn_ok"],
            "failed": raw["conn_fail"],
            "success_pct": success,
            "failure_reasons": {r: raw[f"fail_{r}"] for r in FAIL_REASONS},
        },
        "setup_ms": {
            "samples": setup_total,
            "avg": round(raw["setup_sum_ms"] / raw["setup_n"], 1) if raw["setup_n"] else None,
            "p50": _quantile(raw, "setup", SETUP_BOUNDS_MS, setup_total, 0.50),
            "p95": _quantile(raw, "setup", SETUP_BOUNDS_MS, setup_total, 0.95),
            "histogram": {**{str(b): raw[f"setup_le_{b}"] for b in SETUP_BOUNDS_MS}, "+Inf": raw["setup_inf"]},
        },
        "reconnect": {
            "sessions_reported": raw["sessions_reported"],
            "sessions_with_reconnect": raw["sessions_with_reconnect"],
            "events": raw["reconnect_events"],
            "per_session": round(raw["reconnect_events"] / raw["sessions_reported"], 3) if raw["sessions_reported"] else None,
            "recovered": raw["reconnect_ok"],
            "lost": raw["reconnect_fail"],
            "recovered_pct": _pct(raw["reconnect_ok"], rc_total) if rc_total else None,
        },
        "session_s": {
            "samples": dur_total,
            "avg": round(raw["dur_sum_s"] / raw["dur_n"], 1) if raw["dur_n"] else None,
            "p50": _quantile(raw, "dur", DURATION_BOUNDS_S, dur_total, 0.50),
            "p95": _quantile(raw, "dur", DURATION_BOUNDS_S, dur_total, 0.95),
        },
        "slo": {"connect_success_target_pct": DEFAULT_SLO_PCT, "connect_success_ok": success >= DEFAULT_SLO_PCT},
    }


def summarize_last(minutes: int) -> dict:
    now = time.time()
    return summarize(now - minutes * 60, now)


def prometheus_text() -> str:
    lines: List[str] = []

    def gauge(name, help_, samples):
        samples = [(labels, v) for labels, v in samples if v is not None]
        if not samples:
            return
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    sums = {w: summarize_last(m) for w, m in {"5m": 5, "1h": 60, "24h": 1440}.items()}
    gauge("learnscroll_studyroom_connect_attempts", "study room media connect attempts (ok + failed)",
          [({"window": w}, s["connect"]["attempts"]) for w, s in sums.items()])
    gauge("learnscroll_studyroom_connect_success_pct", "study room connected / attempts, %",
          [({"window": w}, s["connect"]["success_pct"]) for w, s in sums.items() if s["connect"]["attempts"]])
    gauge("learnscroll_studyroom_connect_failures", "failed study room connects by classified reason",
          [({"reason": r, "window": w}, n) for w, s in sums.items() for r, n in s["connect"]["failure_reasons"].items()])
    gauge("learnscroll_studyroom_setup_ms", "study room join -> media connected, ms (histogram bucket upper bound / mean)",
          [({"stat": st, "window": w}, s["setup_ms"][st]) for w, s in sums.items() for st in ("avg", "p50", "p95")
           if s["setup_ms"][st] is not None])
    gauge("learnscroll_studyroom_reconnect_events", "study room reconnect cycles started",
          [({"window": w}, s["reconnect"]["events"]) for w, s in sums.items()])
    gauge("learnscroll_studyroom_reconnects_per_session", "reconnect cycles / reported sessions",
          [({"window": w}, s["reconnect"]["per_session"]) for w, s in sums.items()])
    gauge("learnscroll_studyroom_reconnect_recovered_pct", "study room reconnect cycles that recovered, %",
          [({"window": w}, s["reconnect"]["recovered_pct"]) for w, s in sums.items()])
    gauge("learnscroll_studyroom_session_s", "one participant's session length, s (histogram bucket upper bound / mean)",
          [({"stat": st, "window": w}, s["session_s"][st]) for w, s in sums.items() for st in ("avg", "p50", "p95")
           if s["session_s"][st] is not None])
    return "\n".join(lines) + "\n"
