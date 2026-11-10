"""
post/feed_resilience.py - H10: GET /post/feed/ must never answer 500 because ranking, Redis or a slow query broke.

Four small pieces, all used by post.views.HomeFeedView:

1. FALLBACK FEED  (`fallback_pools`)
   `feed_mix.build_pool_ids` raised / hit the DB timeout -> serve "people you follow, newest first" + "recent public
   posts of people you don't follow" instead. Same pattern `feed_exam.load_profile` already uses ("fail soft"), now
   for the whole feed. Same "safe to show" queryset (blocked / hidden / moderated / reported posts stay out), same
   Exam Mode narrowing (if the filter itself fails we serve an EMPTY page, never non-study content to an
   Exam-Mode student). The fallback pools are frozen into a normal snapshot, so page 2+ stay stable, and they are
   NOT put into the 30 s candidate cache, so the next session already retries real ranking.
   Response header `X-Feed-Degraded: 1` tells clients / dashboards which kind of page it was.

2. DB TIMEOUT  (`statement_timeout`)
   PostgreSQL only: `SET LOCAL statement_timeout` around the pool build, so a runaway ranking query is cancelled
   by the database (Python cannot interrupt a running query) and lands in (1). Other engines: no-op.

3. SINGLE-FLIGHT LOCK  (`single_flight`)
   Cache stampede: the same user firing N page-1 calls at once (double tap, retries, two devices) used to build the
   pools N times. Now one caller builds, the others poll the cache for the result (bounded wait), and anyone who
   times out simply builds too - the lock can slow a request by at most `lock_wait_seconds`, never deadlock or
   fail it. Lock = `cache.add` (atomic in Redis) with a TTL, so a crashed worker cannot hold it forever.
   Cache down -> no lock, just build (fail open).

4. FAIL-OPEN THROTTLE  (`FeedHomeThrottle`)
   DRF throttles keep their counters in the cache; with Redis down the stock throttle raises -> 500 on EVERY request.
   This one allows the request instead (and counts `throttle_cache_down`).

settings.FEED_RESILIENCE (all optional, env-overridable there).
"""
from __future__ import annotations

import contextlib
import logging
import time
import uuid
from datetime import timedelta
from typing import Callable, Dict, List, Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.utils import timezone
from rest_framework.throttling import ScopedRateThrottle

from . import feed_ops
from .feed_mix import SOURCE_FOLLOWING, SOURCE_RECOMMENDED, SOURCE_TRENDING, SOURCES

logger = logging.getLogger(__name__)

DEGRADED_HEADER = "X-Feed-Degraded"
THROTTLE_SCOPE = "feed_home"

DEFAULT_CONFIG = {
    "enabled": True,                 # False -> old behaviour (errors propagate)
    "pool_timeout_ms": 4000,         # Postgres statement_timeout while building pools (0 = off)
    "lock_enabled": True,
    "lock_ttl_seconds": 20,          # a crashed builder frees the lock after this
    "lock_wait_seconds": 3.0,        # max time a follower waits for the builder's result
    "lock_poll_ms": 100,
    "fallback_following_cap": 150,   # newest posts of followed accounts
    "fallback_public_cap": 100,      # newest public posts of everyone else
    "fallback_public_window_days": 14,
}


def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(getattr(settings, "FEED_RESILIENCE", None) or {})
    return cfg


def is_enabled() -> bool:
    return bool(get_config()["enabled"])


# --------------------------------------------------------------------------
# 2) DB statement timeout
# --------------------------------------------------------------------------
def is_timeout_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "statement timeout" in text or "canceling statement" in text or "query_canceled" in text


@contextlib.contextmanager
def statement_timeout(ms: Optional[int] = None):
    """`SET LOCAL statement_timeout` for the queries inside the block (PostgreSQL). Restores the previous value on
    exit so it cannot leak into the rest of an enclosing transaction (tests / ATOMIC_REQUESTS)."""
    ms = get_config()["pool_timeout_ms"] if ms is None else ms
    if not is_enabled() or not ms or ms <= 0 or connection.vendor != "postgresql":
        yield
        return
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            previous = cur.fetchone()[0]
            cur.execute("SET LOCAL statement_timeout = %s", [int(ms)])
        try:
            yield
        finally:
            try:
                with connection.cursor() as cur:
                    cur.execute("SELECT set_config('statement_timeout', %s, true)", [previous])
            except Exception:  # the block already failed and aborted the txn - atomic() rolls it back anyway
                pass


# --------------------------------------------------------------------------
# 1) fallback feed
# --------------------------------------------------------------------------
def guarded_build(builder: Callable[[], Dict[str, list]]) -> Tuple[Optional[Dict[str, list]], bool]:
    """Run the real ranking (the DB timeout is applied by the builder itself, around its queries only - never
    around a lock wait). -> (result, False) on success, (None, True) when it failed or timed out (the caller then
    uses `fallback_pools`). With resilience switched off errors propagate as before."""
    if not is_enabled():
        return builder(), False
    try:
        return builder(), False
    except Exception as exc:
        timed_out = is_timeout_error(exc)
        feed_ops.note("pool_timeout" if timed_out else "pool_error")
        logger.error("feed: ranking %s - serving the fallback feed", "timed out" if timed_out else "failed", exc_info=True)
        return None, True


def fallback_pools(user, base_qs, following_ids: set, meta: Optional[dict] = None) -> Dict[str, list]:
    """following by recency + recent public. Never raises: worst case -> empty pools (an empty 200 page)."""
    cfg = get_config()
    pools: Dict[str, list] = {src: [] for src in SOURCES}
    if meta is not None:
        meta["degraded"] = True
    try:
        qs = base_qs
        # Exam Mode is a promise ("distraction-free"): if it is on and we cannot apply it, serve nothing.
        try:
            from . import feed_exam

            profile = feed_exam.load_profile(user)
            if profile.exam_mode:
                qs = feed_exam.apply_exam_mode(user, qs, None)
        except Exception:
            logger.error("feed fallback: exam-mode filter failed - empty page", exc_info=True)
            return pools
        if following_ids:
            pools[SOURCE_FOLLOWING] = list(
                qs.filter(user_id__in=following_ids, visibility__in=["public", "connections"])
                .order_by("-created_at", "-id")
                .values_list("id", flat=True)[: int(cfg["fallback_following_cap"])]
            )
        since = timezone.now() - timedelta(days=int(cfg["fallback_public_window_days"]))
        pools[SOURCE_TRENDING] = list(
            qs.filter(visibility="public", user__is_private=False, created_at__gte=since)
            .exclude(user_id__in=following_ids)
            .order_by("-created_at", "-id")
            .values_list("id", flat=True)[: int(cfg["fallback_public_cap"])]
        )
    except Exception:
        feed_ops.note("fallback_failed")
        logger.error("feed fallback: even the simple feed failed - empty page", exc_info=True)
        if meta is not None:
            meta["fallback_failed"] = True
        return {src: [] for src in SOURCES}
    return pools


def fallback_ratios() -> Dict[str, float]:
    """Mostly followed accounts, with public posts mixed in (and used alone when you follow nobody)."""
    return {SOURCE_FOLLOWING: 0.7, SOURCE_RECOMMENDED: 0.0, SOURCE_TRENDING: 0.3}


# --------------------------------------------------------------------------
# 3) single-flight lock
# --------------------------------------------------------------------------
def single_flight(lock_key: str, probe: Callable[[], Optional[object]], build: Callable[[], object]):
    """Make sure one process builds what many are asking for.

    probe() -> a ready result (cache hit) or None.   build() -> the result (and is expected to store it so probe()
    finds it next time). Returns whichever result we end up with. Never blocks longer than lock_wait_seconds and
    never raises because of the lock itself."""
    cfg = get_config()
    if not (is_enabled() and cfg["lock_enabled"]):
        return build()
    token = uuid.uuid4().hex
    try:
        acquired = cache.add(lock_key, token, int(cfg["lock_ttl_seconds"]))
    except Exception:
        feed_ops.note("snapshot_store_down")
        return build()  # cache down: no lock, just build
    if acquired:
        try:
            ready = probe()  # a racer may have finished between our first miss and our lock
            return ready if ready is not None else build()
        finally:
            _release(lock_key, token)
    deadline = time.monotonic() + float(cfg["lock_wait_seconds"])
    poll = max(0.01, int(cfg["lock_poll_ms"]) / 1000.0)
    while time.monotonic() < deadline:
        time.sleep(poll)
        ready = probe()
        if ready is not None:
            return ready
    feed_ops.note("lock_wait_timeout")
    return build()  # the builder was slow / died: do it ourselves rather than fail


def _release(lock_key: str, token: str) -> None:
    try:
        if cache.get(lock_key) == token:  # don't free a lock someone else took after our TTL ran out
            cache.delete(lock_key)
    except Exception:
        pass


def lock_key_for(*parts) -> str:
    return "feed:lock:" + ":".join(str(p) for p in parts)


# --------------------------------------------------------------------------
# 4) throttle that fails open
# --------------------------------------------------------------------------
class FeedHomeThrottle(ScopedRateThrottle):
    """Explicit per-user rate limit for GET /post/feed/ (scope `feed_home`, rate in settings
    REST_FRAMEWORK.DEFAULT_THROTTLE_RATES). Replaces the project-wide default throttles on HomeFeedView, so an
    abusive client is limited by THIS scope only. Cache (Redis) down -> allow, never 500."""

    fixed_scope = THROTTLE_SCOPE
    scope = THROTTLE_SCOPE

    def allow_request(self, request, view):
        self.scope = self.fixed_scope  # ScopedRateThrottle would read it from the view; ours is fixed
        try:
            self.rate = self.get_rate()
            self.num_requests, self.duration = self.parse_rate(self.rate)
            return super(ScopedRateThrottle, self).allow_request(request, view)
        except Exception as exc:
            from rest_framework.exceptions import APIException

            if isinstance(exc, APIException):
                raise
            feed_ops.note("throttle_cache_down")
            logger.error("feed throttle: cache unavailable - allowing the request", exc_info=True)
            return True


class FeedOpsThrottle(FeedHomeThrottle):
    """Same fail-open behaviour for GET /post/feed/ops/ - the endpoint you open DURING a Redis outage must not 500."""

    fixed_scope = "feed_ops"
    scope = "feed_ops"
