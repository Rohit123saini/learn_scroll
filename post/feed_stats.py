"""
post/feed_stats.py - H2: opt-in feed counters for the load test.

Two numbers the Locust client cannot see:

  * Redis hit-rate of the feed caches   (feed_cache = candidate cache, feed_snapshot = frozen pools)
  * DB queries per feed page            (page 1 vs. next pages, split by snapshot hit / miss)

OFF by default. Switch on ONLY on the staging web workers (env FEED_LOADTEST_STATS=1, see settings.py):
it wraps each feed request in Django's query capture (debug cursor), which costs memory and CPU.
When off, every function here returns immediately (one settings lookup), so production is unaffected.

Counters live in the Django cache (Redis on staging), so all gunicorn/daphne workers add into the same
numbers. Read / reset them with:

    python manage.py feed_stats --reset                 # before the load test
    python manage.py feed_stats --json --out loadtest/out/feed_stats.json   # after it

Nothing here may ever raise into a request: every cache error is swallowed.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

PREFIX = "feedstats:"

# lookups of the two feed caches
CACHE_NAMES = ("cand", "snap")  # cand = feed_cache (candidate pools), snap = feed_snapshot (cursor pages)
# request labels for "DB queries per page"
LABELS = ("page1", "next_hit", "next_miss", "legacy")
#   page1      no cursor: a new scrolling session (pools built, or served from the 30 s candidate cache)
#   next_hit   cursor, snapshot found in Redis  -> the normal "page 2 / 3 / ..." path
#   next_miss  cursor, snapshot gone            -> pools rebuilt (expired / flushed cache)
#   legacy     ?page=N (N > 1) old-app path, or FEED_SNAPSHOT disabled


def enabled() -> bool:
    return bool(getattr(settings, "FEED_LOADTEST_STATS", False))


def _key(*parts: str) -> str:
    return PREFIX + ":".join(parts)


def _incr(key: str, by: int = 1) -> None:
    try:
        try:
            cache.incr(key, by)
        except ValueError:  # key does not exist yet (LocMem and django-redis both raise ValueError)
            cache.add(key, 0, None)  # no expiry: the run's totals must survive until they are read
            cache.incr(key, by)
    except Exception:  # pragma: no cover - a stats counter must never break a request
        logger.debug("feed_stats incr failed", exc_info=True)


def hit(name: str) -> None:
    """A lookup in feed cache `name` ('cand' | 'snap') found something."""
    if enabled():
        _incr(_key(name, "hit"))


def miss(name: str) -> None:
    if enabled():
        _incr(_key(name, "miss"))


def record_queries(label: str, n_queries: int) -> None:
    """One feed request of kind `label` ran `n_queries` SQL statements."""
    if not enabled():
        return
    _incr(_key("q", label, "requests"))
    _incr(_key("q", label, "queries"), int(n_queries))


def measure(fn):
    """Run `fn()` and return (result, number_of_SQL_statements_it_executed)."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as ctx:
        result = fn()
    return result, len(ctx)


def _get_int(key: str) -> int:
    try:
        return int(cache.get(key) or 0)
    except Exception:  # pragma: no cover
        return 0


def read() -> dict:
    """All counters + derived rates (pure read; works even when enabled() is False)."""
    out: dict = {"caches": {}, "queries_per_page": {}}
    total_hit = total_miss = 0
    for name in CACHE_NAMES:
        h, m = _get_int(_key(name, "hit")), _get_int(_key(name, "miss"))
        total_hit += h
        total_miss += m
        out["caches"][name] = {"hits": h, "misses": m, "hit_rate": round(h / (h + m), 4) if (h + m) else None}
    out["feed_cache_hit_rate"] = round(total_hit / (total_hit + total_miss), 4) if (total_hit + total_miss) else None
    for label in LABELS:
        req, q = _get_int(_key("q", label, "requests")), _get_int(_key("q", label, "queries"))
        out["queries_per_page"][label] = {
            "requests": req, "queries": q, "avg_queries": round(q / req, 2) if req else None,
        }
    # overall average across every feed request (what "DB queries/page" means when one number is wanted)
    all_req = sum(v["requests"] for v in out["queries_per_page"].values())
    all_q = sum(v["queries"] for v in out["queries_per_page"].values())
    out["avg_queries_all_pages"] = round(all_q / all_req, 2) if all_req else None
    return out


def reset() -> None:
    keys = [_key(n, kind) for n in CACHE_NAMES for kind in ("hit", "miss")]
    keys += [_key("q", label, kind) for label in LABELS for kind in ("requests", "queries")]
    keys += [_key("redis_base")]
    for key in keys:
        try:
            cache.delete(key)
        except Exception:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Redis server-wide keyspace hit-rate (INFO stats). Secondary number: it also counts DRF throttles,
# sessions, Celery... so the feed-only rate above is the one to quote; this one shows the cache is healthy overall.
# ---------------------------------------------------------------------------
def redis_keyspace() -> dict | None:
    try:
        from django_redis import get_redis_connection

        info = get_redis_connection("default").info("stats")
        return {"hits": int(info.get("keyspace_hits", 0)), "misses": int(info.get("keyspace_misses", 0))}
    except Exception:
        return None  # LocMem cache (dev) or Redis unreachable


def mark_redis_baseline() -> bool:
    snap = redis_keyspace()
    if snap is None:
        return False
    try:
        cache.set(_key("redis_base"), snap, None)
        return True
    except Exception:  # pragma: no cover
        return False


def redis_delta() -> dict | None:
    """Server-wide keyspace hits/misses since `mark_redis_baseline()` (None when unavailable)."""
    now, base = redis_keyspace(), None
    try:
        base = cache.get(_key("redis_base"))
    except Exception:  # pragma: no cover
        pass
    if now is None or not base:
        return None
    h, m = now["hits"] - base["hits"], now["misses"] - base["misses"]
    if h < 0 or m < 0:  # Redis restarted during the run - the delta is meaningless
        return None
    return {"hits": h, "misses": m, "hit_rate": round(h / (h + m), 4) if (h + m) else None}
