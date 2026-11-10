"""
message/call_ops.py - X1: observability of 1:1 / group CALLS (the SLO side, same idea as post/feed_ops.py).

What it answers (all numbers are per 5-minute bucket, summed over any window):

    call setup time       client: tap (initiate / accept) -> LiveKit room connected          (ms histogram)
    connect-success %     client: connected / (connected + failed) per participant attempt   (SLO >= 95 %)
    reconnect count       client: reconnect cycles per call (+ how many ended in a re-connect vs a lost call)
    ring -> answer rate   server: answered / (answered + rejected + missed + cancelled)      (busy tracked on its own)
    ring -> answer time   server: seconds from `started_at` to the first accept              (seconds histogram)
    ICE / connect failure reason   client classifies the connect error into a SMALL fixed set (FAIL_REASONS)

WHO RECORDS WHAT
    server  (views/calls.py `_dispatch_result`, tasks.py `_finish_stale_call`, CallInitiateView):
            initiated, ring outcomes, ring -> answer seconds.         -> exact, nothing to trust from the client
    client  (POST /message/calls/<id>/telemetry/, lib/message/services/call_telemetry.dart):
            connect ok/fail + setup ms + failure reason, reconnect counts.  -> only a participant of that call can
            report; one `connect` and one `summary` per (call, user); values clamped; reason whitelisted.

STORAGE: identical to feed_ops - plain integer counters in 5-minute cache buckets (Redis in prod, keys
`callops:<bucket>:<name>`, 40 days). No migration. If the cache is down they fall back to a small per-process dict
and ONE rate-limited ERROR a minute is logged. Recording must NEVER raise into a request / task.

BLIND SPOTS (see deploy/monitoring/README.md): a client that is killed before it can POST, a push that never reached
the callee (no ring at all) and a LiveKit server that is totally down before the client even gets a token are NOT in
the connect-success number - keep the LiveKit / uptime probes for those.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Iterable, List, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300
TTL_SECONDS = 40 * 86400
DEDUPE_TTL_SECONDS = 6 * 3600

SETUP_BOUNDS_MS = (500, 1000, 2000, 3000, 5000, 10000)      # +Inf implicit
RING_BOUNDS_S = (3, 6, 10, 15, 20, 30, 45)                   # +Inf implicit

# server side: how a ring ended
RING_OUTCOMES = ("answered", "rejected", "missed", "cancelled", "busy")
# client side: why a connect attempt failed (fixed set => bounded Prometheus label cardinality)
FAIL_REASONS = (
    "timeout",          # connect / negotiation timed out
    "ice_failed",       # ICE / peer connection / DTLS / negotiation could not be established
    "signal_ws",        # signalling websocket / TLS / DNS / server unreachable
    "token_auth",       # token expired / invalid / unauthorised / room closed
    "permission",       # mic / camera permission denied (never reached the network)
    "network_offline",  # device had no network
    "server",           # LiveKit / backend answered with an error (5xx, room full, ...)
    "other",
)

DEFAULT_SLO = {
    "connect_success_pct": 95.0,   # alert when LOWER than this ...
    "alert_window_minutes": 15,
    "alert_min_attempts": 20,      # ... over at least this many connect attempts in the window
}

_local: Dict[tuple, int] = {}
_local_lock = threading.Lock()
_last_warn = [0.0]


def get_slo() -> dict:
    cfg = dict(DEFAULT_SLO)
    cfg.update(getattr(settings, "CALL_SLO", None) or {})
    return cfg


def _bucket(ts: Optional[float] = None) -> int:
    return int((time.time() if ts is None else ts) // BUCKET_SECONDS)


def _key(bucket: int, name: str) -> str:
    return f"callops:{bucket}:{name}"


def _warn_cache_down() -> None:
    now = time.monotonic()
    if now - _last_warn[0] > 60:
        _last_warn[0] = now
        logger.error("call_ops: cache unavailable - call counters are process-local until it is back", exc_info=True)


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
    """True the first time `token` is seen (cache.add). Cache down => True (count rather than lose it)."""
    try:
        return bool(cache.add(f"callops:once:{token}", 1, DEDUPE_TTL_SECONDS))
    except Exception:
        return True


def _hist_name(prefix: str, bounds: Iterable[int], value: float) -> str:
    for bound in bounds:
        if value <= bound:
            return f"{prefix}_le_{bound}"
    return f"{prefix}_inf"


def _breadcrumb(message: str, **data) -> None:
    """Sentry breadcrumb (shows on the next event of this worker/request). No-op without sentry."""
    try:
        import sentry_sdk

        sentry_sdk.add_breadcrumb(category="call", message=message, level="info", data=data or None)
    except Exception:
        pass


# --------------------------------------------------------------------------
# recording - server side
# --------------------------------------------------------------------------
def note_initiated(is_group: bool = False) -> None:
    """A call actually started ringing (not a refused / busy / already-in-call attempt). Never raises."""
    try:
        _incr("initiated")
        _incr("initiated_group" if is_group else "initiated_direct")
    except Exception:  # pragma: no cover
        pass


def note_ring_outcome(outcome: str, call_id=None, ring_seconds: Optional[float] = None) -> bool:
    """One call's ring ended as `outcome` (RING_OUTCOMES). With `call_id` it is counted ONCE per call (accept /
    reject / cancel / sweeper can race or repeat). Returns True when counted. Never raises."""
    try:
        if outcome not in RING_OUTCOMES:
            return False
        if call_id is not None and not _once(f"ring:{call_id}"):
            return False
        _incr(f"ring_{outcome}")
        if outcome == "answered" and ring_seconds is not None:
            _incr(_hist_name("ringlat", RING_BOUNDS_S, max(0.0, float(ring_seconds))))
            _incr("ringlat_sum_s", int(max(0.0, float(ring_seconds))))
        _breadcrumb(f"ring {outcome}", call_id=str(call_id) if call_id else None,
                    ring_seconds=None if ring_seconds is None else round(float(ring_seconds), 1))
        return True
    except Exception:  # pragma: no cover
        return False


def note_call_result(result) -> None:
    """Hook for `views.calls._dispatch_result`: derive the ring outcome from a call_state.ActionResult.
    accepted -> answered (+ ring seconds) | rejected AND call now REJECTED -> rejected | cancelled -> cancelled."""
    try:
        if result is None or not getattr(result, "changed", False) or getattr(result, "call", None) is None:
            return
        call = result.call
        code = getattr(result, "code", "")
        if code == "accepted":
            from django.utils import timezone

            started = getattr(call, "started_at", None)
            secs = (timezone.now() - started).total_seconds() if started else None
            note_ring_outcome("answered", call_id=call.id, ring_seconds=secs)
        elif code == "rejected" and getattr(call, "status", "") == "rejected":   # TextChoices == str
            note_ring_outcome("rejected", call_id=call.id)
        elif code == "cancelled":
            note_ring_outcome("cancelled", call_id=call.id)
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------
# recording - client reports (validated by CallTelemetryView)
# --------------------------------------------------------------------------
def normalize_reason(reason) -> str:
    r = str(reason or "").strip().lower()
    return r if r in FAIL_REASONS else "other"


def record_connect(call_id, user_id, ok: bool, setup_ms=None, reason=None) -> bool:
    """Client's first connect attempt of this call finished. Once per (call, user). Returns True when counted."""
    try:
        if not _once(f"conn:{call_id}:{user_id}"):
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
            _breadcrumb("connect ok", call_id=str(call_id), setup_ms=None if ms is None else int(ms))
        else:
            r = normalize_reason(reason)
            _incr("conn_fail")
            _incr(f"fail_{r}")
            _breadcrumb("connect FAILED", call_id=str(call_id), reason=r)
        return True
    except Exception:  # pragma: no cover
        return False


def record_summary(call_id, user_id, reconnects=0, reconnect_ok=0, reconnect_fail=0) -> bool:
    """End-of-call numbers of one participant. Once per (call, user). Returns True when counted."""
    try:
        if not _once(f"sum:{call_id}:{user_id}"):
            return False

        def clamp(v):
            try:
                return min(max(int(v), 0), 50)
            except (TypeError, ValueError):
                return 0

        n, ok, bad = clamp(reconnects), clamp(reconnect_ok), clamp(reconnect_fail)
        _incr("calls_reported")
        if n:
            _incr("calls_with_reconnect")
            _incr("reconnect_events", n)
        if ok:
            _incr("reconnect_ok", ok)
        if bad:
            _incr("reconnect_fail", bad)
            _breadcrumb("reconnect failed", call_id=str(call_id), reconnects=n, failed=bad)
        return True
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
_NAMES: List[str] = (
    ["initiated", "initiated_group", "initiated_direct",
     "conn_ok", "conn_fail", "setup_inf", "setup_sum_ms", "setup_n",
     "ringlat_inf", "ringlat_sum_s",
     "calls_reported", "calls_with_reconnect", "reconnect_events", "reconnect_ok", "reconnect_fail"]
    + [f"setup_le_{b}" for b in SETUP_BOUNDS_MS]
    + [f"ringlat_le_{b}" for b in RING_BOUNDS_S]
    + [f"ring_{o}" for o in RING_OUTCOMES]
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
    """Upper bound of the histogram bucket holding quantile q (None = beyond the last bound / no data)."""
    if not total:
        return None
    running = 0
    for bound in bounds:
        running += raw[f"{prefix}_le_{bound}"]
        if running >= q * total:
            return bound
    return None


def summarize(start_ts: float, end_ts: float) -> dict:
    """Totals + SLIs for [start_ts, end_ts) (unix seconds, rounded to 5-minute buckets)."""
    first, last = _bucket(start_ts), _bucket(end_ts - 1) if end_ts > start_ts else _bucket(start_ts)
    raw = _sum_buckets(range(first, last + 1))
    slo = get_slo()

    attempts = raw["conn_ok"] + raw["conn_fail"]
    success = round(100.0 * raw["conn_ok"] / attempts, 2) if attempts else 100.0
    setup_total = sum(raw[f"setup_le_{b}"] for b in SETUP_BOUNDS_MS) + raw["setup_inf"]
    ring_total = sum(raw[f"ringlat_le_{b}"] for b in RING_BOUNDS_S) + raw["ringlat_inf"]
    answerable = sum(raw[f"ring_{o}"] for o in ("answered", "rejected", "missed", "cancelled"))
    rc_total = raw["reconnect_ok"] + raw["reconnect_fail"]

    return {
        "window": {"start": start_ts, "end": end_ts},
        "initiated": raw["initiated"],
        "initiated_group": raw["initiated_group"],
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
        "ring": {
            "outcomes": {o: raw[f"ring_{o}"] for o in RING_OUTCOMES},
            "answer_rate_pct": _pct(raw["ring_answered"], answerable) if answerable else None,
            "to_answer_s": {
                "samples": ring_total,
                "avg": round(raw["ringlat_sum_s"] / ring_total, 1) if ring_total else None,
                "p50": _quantile(raw, "ringlat", RING_BOUNDS_S, ring_total, 0.50),
                "p95": _quantile(raw, "ringlat", RING_BOUNDS_S, ring_total, 0.95),
            },
        },
        "reconnect": {
            "calls_reported": raw["calls_reported"],
            "calls_with_reconnect": raw["calls_with_reconnect"],
            "events": raw["reconnect_events"],
            "per_call": round(raw["reconnect_events"] / raw["calls_reported"], 3) if raw["calls_reported"] else None,
            "recovered": raw["reconnect_ok"],
            "lost": raw["reconnect_fail"],
            "recovered_pct": _pct(raw["reconnect_ok"], rc_total) if rc_total else None,
        },
        "slo": {
            "connect_success_target_pct": slo["connect_success_pct"],
            "connect_success_ok": success >= slo["connect_success_pct"],
        },
    }


def summarize_last(minutes: int) -> dict:
    now = time.time()
    return summarize(now - minutes * 60, now)


def breaches(summary: dict) -> List[str]:
    out = []
    s = summary["slo"]
    if summary["connect"]["attempts"] <= 0:
        return out
    if not s["connect_success_ok"]:
        reasons = {k: v for k, v in summary["connect"]["failure_reasons"].items() if v}
        top = ", ".join(f"{k}={v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:3]) or "no reason reported"
        out.append(f"connect-success {summary['connect']['success_pct']}% < {s['connect_success_target_pct']}% (top reasons: {top})")
    return out


def check_and_alert(window_minutes: Optional[int] = None, min_attempts: Optional[int] = None) -> dict:
    """Fast-burn check (celery beat `message-call-slo-watch`, every 5 min). On a breach with enough attempts it logs
    ONE ERROR and sends a Sentry message with a stable fingerprint (same channel as post/feed_ops.py)."""
    slo = get_slo()
    window_minutes = window_minutes or slo["alert_window_minutes"]
    min_attempts = slo["alert_min_attempts"] if min_attempts is None else min_attempts
    summary = summarize_last(window_minutes)
    problems = breaches(summary) if summary["connect"]["attempts"] >= min_attempts else []
    summary["problems"] = problems
    if problems:
        msg = (f"CALL SLO ALERT (last {window_minutes} min, {summary['connect']['attempts']} connect attempts): "
               + "; ".join(problems))
        logger.error(msg)
        _sentry(msg, problems)
    return summary


def _sentry(msg: str, problems: List[str]) -> None:
    try:
        import sentry_sdk

        new_scope = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with new_scope() as scope:
            scope.set_tag("alert_type", "call_slo")
            scope.fingerprint = ["call-slo", problems[0].split(" ")[0]]
            sentry_sdk.capture_message(msg, level="error")
    except Exception:  # sentry not installed / not configured
        pass


# --------------------------------------------------------------------------
# Prometheus text (GET /message/calls/ops/?format=prom)
# --------------------------------------------------------------------------
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

    windows = {"5m": 5, "1h": 60, "24h": 1440}
    sums = {w: summarize_last(m) for w, m in windows.items()}

    def per_window(fn):
        return [({"window": w}, fn(s)) for w, s in sums.items()]

    gauge("learnscroll_call_initiated", "calls that started ringing in the window",
          per_window(lambda s: s["initiated"]))
    gauge("learnscroll_call_connect_attempts", "client connect attempts (ok + failed) in the window",
          per_window(lambda s: s["connect"]["attempts"]))
    gauge("learnscroll_call_connect_success_pct", "connected / attempts, % (SLO >= 95)",
          [({"window": w}, s["connect"]["success_pct"]) for w, s in sums.items() if s["connect"]["attempts"]])
    gauge("learnscroll_call_connect_failures", "failed connect attempts by classified reason",
          [({"reason": r, "window": w}, n) for w, s in sums.items() for r, n in s["connect"]["failure_reasons"].items()])
    gauge("learnscroll_call_setup_ms", "call setup time (tap -> room connected), ms; histogram bucket upper bound / mean",
          [({"stat": st, "window": w}, s["setup_ms"][st]) for w, s in sums.items() for st in ("avg", "p50", "p95")
           if s["setup_ms"][st] is not None])
    gauge("learnscroll_call_ring_outcomes", "calls by how the ring ended",
          [({"outcome": o, "window": w}, n) for w, s in sums.items() for o, n in s["ring"]["outcomes"].items()])
    gauge("learnscroll_call_ring_answer_rate_pct", "answered / (answered+rejected+missed+cancelled), %",
          [({"window": w}, s["ring"]["answer_rate_pct"]) for w, s in sums.items()])
    gauge("learnscroll_call_ring_to_answer_s", "seconds from ring start to first answer (histogram bucket upper bound / mean)",
          [({"stat": st, "window": w}, s["ring"]["to_answer_s"][st]) for w, s in sums.items() for st in ("avg", "p50", "p95")
           if s["ring"]["to_answer_s"][st] is not None])
    gauge("learnscroll_call_reconnect_events", "reconnect cycles started in the window",
          per_window(lambda s: s["reconnect"]["events"]))
    gauge("learnscroll_call_reconnects_per_call", "reconnect cycles / reported calls",
          [({"window": w}, s["reconnect"]["per_call"]) for w, s in sums.items()])
    gauge("learnscroll_call_reconnect_recovered_pct", "reconnect cycles that recovered, %",
          [({"window": w}, s["reconnect"]["recovered_pct"]) for w, s in sums.items()])
    return "\n".join(lines) + "\n"
