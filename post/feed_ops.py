"""
post/feed_ops.py - H10: request-level health numbers of GET /post/feed/ (the SLO side of feed_metrics).

`feed_metrics.py` answers "is the feed GOOD?" (CTR, dwell, quick-skip ... from PostEvent). This module answers
"is the feed UP and FAST?" - what Grafana / the SLO alert need and PostEvent cannot tell:

    availability  = 1 - errors / requests     (a degraded-but-200 answer counts as available)
    error rate    = 5xx answers / requests
    degraded rate = answers served from the fail-soft fallback / requests
    p95 latency   = from a fixed-bucket histogram (50/100/200/400/800/1600/3200 ms/+Inf)

SLO (settings.FEED_SLO): availability >= 99.5 %, p95 <= 800 ms.

STORAGE: 5-minute buckets of plain integer counters in the Django cache (Redis in prod), so every gunicorn /
daphne worker adds into the same numbers and nothing needs a migration. Keys expire after 40 days.
If the cache itself is down the counters fall back to a small per-process dict (still readable by the same
process) and a rate-limited ERROR is logged - recording must NEVER raise into a request.

LIMIT: only requests that reach HomeFeedView are seen. A worker that is dead / a load balancer 502 / a timeout
in gunicorn BEFORE the view never shows up here - keep the LB / uptime-probe alert for that (see
deploy/monitoring/README.md).
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
LATENCY_BOUNDS_MS = (50, 100, 200, 400, 800, 1600, 3200)  # +Inf is implicit

OUTCOME_OK = "ok"
OUTCOME_DEGRADED = "degraded"  # 200, but built by the fail-soft fallback
OUTCOME_ERROR = "error"        # 5xx

# Alert-worthy happenings (each is also a counter): see feed_resilience.py for who calls note().
EVENTS = (
    "pool_error",           # ranking / build_pool_ids raised
    "pool_timeout",         # ... because the DB statement timeout hit
    "fallback_failed",      # even the simple fallback failed -> empty page
    "snapshot_store_down",  # Redis snapshot write/read failed (page still served)
    "lock_wait_timeout",    # waited for another worker's pool build and gave up
    "throttle_cache_down",  # throttle could not reach the cache -> failed open
)

DEFAULT_SLO = {
    "availability_pct": 99.5,
    "p95_ms": 800,
    "alert_window_minutes": 15,   # fast-burn window checked by post.tasks.feed_slo_watch
    "alert_min_requests": 50,     # below this a window is too small to judge
}

_local: Dict[tuple, int] = {}
_local_lock = threading.Lock()
_last_warn = [0.0]


def get_slo() -> dict:
    cfg = dict(DEFAULT_SLO)
    cfg.update(getattr(settings, "FEED_SLO", None) or {})
    return cfg


def _bucket(ts: Optional[float] = None) -> int:
    return int((time.time() if ts is None else ts) // BUCKET_SECONDS)


def _key(bucket: int, name: str) -> str:
    return f"feedops:{bucket}:{name}"


def _warn_cache_down() -> None:
    now = time.monotonic()
    if now - _last_warn[0] > 60:  # one line a minute per process, not one per request
        _last_warn[0] = now
        logger.error("feed_ops: cache unavailable - feed SLO counters are process-local until it is back", exc_info=True)


def _incr(name: str, by: int = 1, bucket: Optional[int] = None) -> None:
    bucket = _bucket() if bucket is None else bucket
    key = _key(bucket, name)
    try:
        cache.add(key, 0, TTL_SECONDS)
        cache.incr(key, by)
    except Exception:
        _warn_cache_down()
        with _local_lock:
            if len(_local) > 5000:  # bounded: drop everything older than a day
                cutoff = bucket - 288
                for k in [k for k in _local if k[0] < cutoff]:
                    _local.pop(k, None)
            _local[(bucket, name)] = _local.get((bucket, name), 0) + by


def _latency_name(latency_ms: float) -> str:
    for bound in LATENCY_BOUNDS_MS:
        if latency_ms <= bound:
            return f"lat_le_{bound}"
    return "lat_inf"


def record_request(latency_ms: float, outcome: str = OUTCOME_OK) -> None:
    """One finished GET /post/feed/ (any page). Never raises."""
    try:
        _incr("total")
        _incr(_latency_name(latency_ms))
        if outcome == OUTCOME_DEGRADED:
            _incr("degraded")
        elif outcome == OUTCOME_ERROR:
            _incr("errors")
    except Exception:  # pragma: no cover - belt and braces
        pass


def note(event: str, by: int = 1) -> None:
    """Count an alert-worthy event (see EVENTS). Never raises."""
    try:
        _incr(f"ev_{event}", by)
    except Exception:  # pragma: no cover
        pass


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
_NAMES: List[str] = (
    ["total", "degraded", "errors", "lat_inf"]
    + [f"lat_le_{b}" for b in LATENCY_BOUNDS_MS]
    + [f"ev_{e}" for e in EVENTS]
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
    with _local_lock:  # numbers this process could not push to the (down) cache
        for (b, n), v in _local.items():
            if b in wanted and n in out:
                out[n] += v
    return out


def summarize(start_ts: float, end_ts: float) -> dict:
    """Totals + SLIs for [start_ts, end_ts) (unix seconds, rounded to 5-minute buckets)."""
    first, last = _bucket(start_ts), _bucket(end_ts - 1) if end_ts > start_ts else _bucket(start_ts)
    raw = _sum_buckets(range(first, last + 1))
    total = raw["total"]
    slo = get_slo()

    def pct(a, b):
        return round(100.0 * a / b, 4) if b else 0.0

    cumulative, running = {}, 0
    for bound in LATENCY_BOUNDS_MS:
        running += raw[f"lat_le_{bound}"]
        cumulative[bound] = running
    p95_ms = None  # upper bound of the bucket that holds the 95th percentile (None = beyond the last bound)
    if total:
        for bound in LATENCY_BOUNDS_MS:
            if cumulative[bound] >= 0.95 * total:
                p95_ms = bound
                break
    target = slo["p95_ms"]
    # smallest histogram bound >= target: exact when the target IS a bound (800), conservative otherwise
    check_bound = next((b for b in LATENCY_BOUNDS_MS if b >= target), None)
    within = (cumulative[check_bound] / total) if (total and check_bound) else (1.0 if total else 0.0)
    availability = round(100.0 - pct(raw["errors"], total), 4) if total else 100.0
    return {
        "window": {"start": start_ts, "end": end_ts},
        "requests": total,
        "errors": raw["errors"],
        "degraded": raw["degraded"],
        "availability_pct": availability,
        "error_rate_pct": pct(raw["errors"], total),
        "degraded_rate_pct": pct(raw["degraded"], total),
        "p95_ms": p95_ms,
        "within_p95_target_pct": round(100.0 * within, 2) if total else 0.0,
        "latency_histogram_ms": {**{str(b): raw[f"lat_le_{b}"] for b in LATENCY_BOUNDS_MS}, "+Inf": raw["lat_inf"]},
        "events": {e: raw[f"ev_{e}"] for e in EVENTS},
        "slo": {
            "availability_target_pct": slo["availability_pct"],
            "p95_target_ms": target,
            "availability_ok": availability >= slo["availability_pct"],
            "p95_ok": (within >= 0.95) if total else True,
        },
    }


def summarize_last(minutes: int) -> dict:
    now = time.time()
    return summarize(now - minutes * 60, now)


def breaches(summary: dict) -> List[str]:
    out = []
    s = summary["slo"]
    if summary["requests"] <= 0:
        return out
    if not s["availability_ok"]:
        out.append(f"availability {summary['availability_pct']}% < {s['availability_target_pct']}%")
    if not s["p95_ok"]:
        out.append(f"p95 > {s['p95_target_ms']} ms ({summary['within_p95_target_pct']}% of requests within target, need 95%)")
    return out


def check_and_alert(window_minutes: Optional[int] = None, min_requests: Optional[int] = None) -> dict:
    """Fast-burn SLO check (run every 5 min by post.tasks.feed_slo_watch). On a breach with enough traffic it
    logs ONE ERROR and sends a Sentry message with a stable fingerprint (same channel as core/ops_health.py)."""
    slo = get_slo()
    window_minutes = window_minutes or slo["alert_window_minutes"]
    min_requests = slo["alert_min_requests"] if min_requests is None else min_requests
    summary = summarize_last(window_minutes)
    problems = breaches(summary) if summary["requests"] >= min_requests else []
    ev = summary["events"]
    if ev.get("snapshot_store_down"):
        problems.append(f"snapshot store (Redis) failed {ev['snapshot_store_down']}x - feed served without frozen snapshots")
    if ev.get("fallback_failed"):
        problems.append(f"fallback feed ALSO failed {ev['fallback_failed']}x (users got an empty page)")
    summary["problems"] = problems
    if problems:
        msg = f"FEED SLO ALERT (last {window_minutes} min, {summary['requests']} req): " + "; ".join(problems)
        logger.error(msg)
        _sentry(msg, problems)
    return summary


def _sentry(msg: str, problems: List[str]) -> None:
    try:
        import sentry_sdk

        new_scope = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope
        with new_scope() as scope:
            scope.set_tag("alert_type", "feed_slo")
            scope.fingerprint = ["feed-slo", problems[0].split(" ")[0]]
            sentry_sdk.capture_message(msg, level="error")
    except Exception:  # sentry not installed / not configured
        pass


# --------------------------------------------------------------------------
# Prometheus text (scraped by Prometheus -> Grafana; see deploy/monitoring/)
# --------------------------------------------------------------------------
def prometheus_text() -> str:
    lines: List[str] = []

    def gauge(name, help_, samples):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            if value is None:
                continue
            lab = ",".join(f'{k}="{v}"' for k, v in labels.items())
            lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")

    windows = {"5m": 5, "1h": 60, "24h": 1440}
    sums = {w: summarize_last(m) for w, m in windows.items()}
    gauge("learnscroll_feed_requests", "GET /post/feed/ requests in the window",
          [({"window": w}, s["requests"]) for w, s in sums.items()])
    gauge("learnscroll_feed_errors", "5xx answers in the window", [({"window": w}, s["errors"]) for w, s in sums.items()])
    gauge("learnscroll_feed_degraded", "answers from the fail-soft fallback in the window",
          [({"window": w}, s["degraded"]) for w, s in sums.items()])
    gauge("learnscroll_feed_availability_pct", "100 - error rate (SLO 99.5)",
          [({"window": w}, s["availability_pct"]) for w, s in sums.items()])
    gauge("learnscroll_feed_p95_ms", "p95 latency, upper bound of the histogram bucket (SLO 800)",
          [({"window": w}, s["p95_ms"] if s["p95_ms"] is not None else 99999) for w, s in sums.items() if s["requests"]])
    gauge("learnscroll_feed_within_p95_target_pct", "% of requests faster than the p95 target (need >= 95)",
          [({"window": w}, s["within_p95_target_pct"]) for w, s in sums.items() if s["requests"]])
    gauge("learnscroll_feed_events", "alert-worthy events in the window (5m)",
          [({"event": e, "window": "5m"}, n) for e, n in sums["5m"]["events"].items()])

    # yesterday's quality numbers from the daily job (post.tasks.feed_daily_metrics)
    try:
        from datetime import date, datetime, timedelta, timezone as dt_tz

        day = (datetime.now(dt_tz.utc).date() - timedelta(days=1)).isoformat()
        daily = cache.get(f"feed:metrics:{day}")
    except Exception:
        daily = None
    if isinstance(daily, dict):
        metrics = ("impressions", "ctr", "avg_dwell_ms", "long_dwell_rate", "quick_skip_rate", "show_fewer_per_1000",
                   "avg_session_seconds")
        gauge("learnscroll_feed_daily", f"feed quality, UTC day {day} (post.tasks.feed_daily_metrics)",
              [({"metric": m, "day": day}, daily.get(m)) for m in metrics])
    return "\n".join(lines) + "\n"
