"""
post/feed_cache.py - T1 Part 5: short-TTL per-user candidate cache.

Building the pools (3 ranked queries + signal / context / exploration queries)
is the expensive part of opening the feed. A user who opens the app twice in a
minute, or an app that retries a failed first request, pays for it twice. This
caches the BUILT pools (`{source: [ids]}` + exploration meta) per user for
`ttl_seconds` (default 30) in the Django cache (Redis in production), so a new
snapshot inside that window is created from the cached lists.

Rules that keep it correct:
* `?refresh=1` (pull-to-refresh) always bypasses the cache and rebuilds.
* Anything that changes WHAT a user may see calls `invalidate(user_id)`
  ("show fewer", hide, mute) - it bumps a per-user version in the key.
* Block / delete / moderation need no invalidation: ids are re-filtered through
  the "safe to show" queryset every time a page is serialized.
* Callers that pin an explicit `seen_cutoff` skip the cache (their pools must
  match the cutoff exactly).
* Cache down / corrupt -> behaves as a miss. Never raises.

settings.FEED_CANDIDATE_CACHE = {"enabled": True, "ttl_seconds": 30}
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Dict, List, Optional

from django.conf import settings
from django.core.cache import cache

from .feed_mix import SOURCES

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"enabled": True, "ttl_seconds": 30}


def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(getattr(settings, "FEED_CANDIDATE_CACHE", None) or {})
    try:
        cfg["ttl_seconds"] = max(0, int(cfg["ttl_seconds"]))
    except (TypeError, ValueError):
        cfg["ttl_seconds"] = DEFAULT_CONFIG["ttl_seconds"]
    return cfg


def is_enabled() -> bool:
    cfg = get_config()
    return bool(cfg["enabled"]) and cfg["ttl_seconds"] > 0


def _ver_key(user_id) -> str:
    return f"feed:cand:ver:{user_id}"


def _version(user_id) -> int:
    try:
        return int(cache.get(_ver_key(user_id)) or 0)
    except Exception:  # pragma: no cover
        return 0


def salt_for(user) -> int:
    """Account-instance salt (microsecond join time): a reused / re-created id
    (deleted account, or a test DB that restarts its autoincrement) can never
    read another account's cached candidates."""
    joined = getattr(user, "date_joined", None)
    return int(joined.timestamp() * 1_000_000) if joined else 0


def _key(user_id, category: Optional[str], rec_ratio: float, salt: int = 0) -> str:
    return f"feed:cand:{user_id}:{salt}:v{_version(user_id)}:{category or '-'}:r{int(round(rec_ratio * 100))}"


def invalidate(user_id) -> None:
    """Drop every cached candidate list of this user (version bump)."""
    try:
        cache.set(_ver_key(user_id), _version(user_id) + 1, 86400)
    except Exception:  # pragma: no cover
        logger.warning("feed candidate cache invalidate failed", exc_info=True)


def get(user_id, category: Optional[str], rec_ratio: float, salt: int = 0) -> Optional[dict]:
    """-> {"pools": {src: [UUID]}, "meta": {...}} or None."""
    if not is_enabled():
        return None
    try:
        raw = cache.get(_key(user_id, category, rec_ratio, salt))
        if not raw:
            return None
        data = json.loads(raw)
        pools = {src: [uuid.UUID(x) for x in data["pools"].get(src, [])] for src in SOURCES}
        return {"pools": pools, "meta": dict(data.get("meta") or {})}
    except Exception:
        logger.warning("feed candidate cache read failed", exc_info=True)
        return None


def put(user_id, category: Optional[str], rec_ratio: float, pools: Dict[str, List], meta: dict, salt: int = 0) -> bool:
    if not is_enabled():
        return False
    try:
        payload = json.dumps({
            "pools": {src: [str(p) for p in pools.get(src, [])] for src in SOURCES},
            "meta": meta or {},
        }, separators=(",", ":"))
        cache.set(_key(user_id, category, rec_ratio, salt), payload, get_config()["ttl_seconds"])
        return True
    except Exception:  # pragma: no cover
        logger.warning("feed candidate cache write failed", exc_info=True)
        return False
