"""
post/feed_snapshot.py - frozen ranked snapshot + opaque cursor for HomeFeedView.

TASK "Cursor pagination" - PART 2.

WHY: HomeFeedView rebuilt its 3 ranked id pools (following / recommended /
trending) on EVERY request and sliced them by page number. Scores move while
the user scrolls (likes, comments, the 3h/12h/48h freshness boost), so the
pools re-sorted between page 1 and page 2 and posts jumped up/down.

NOW: the first request (no `cursor`) builds the pools ONCE and stores the
ordered id lists in the Django cache (django-redis when REDIS_URL is set, see
settings.CACHES) under `feed:snap:<user_id>:<snapshot_id>` for
`FEED_SNAPSHOT["ttl_seconds"]` (default 15 min). Every later page is a pure
slice of that frozen list, addressed by the cursor:

    cursor = base64url(json{v, s: snapshot_id, o: {source: offset}, c: seen_cutoff})

so the order is 100% stable for the whole scrolling session, and page N costs
one `id IN (...)` query instead of rebuilding three ranked pools.

FALLBACK (never a 500, never an endless loop): if the snapshot is gone (TTL
expired, cache flushed/down, other worker with a per-process cache), the pools
are rebuilt with the SAME `seen_cutoff` carried in the cursor and the stored
offsets are applied to them - i.e. the old, best-effort behaviour. The rebuilt
snapshot is saved again under the same snapshot_id.

A snapshot is keyed by user id, so a cursor copied to another account simply
misses and takes the fallback path; it can never expose another user's list.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from typing import Dict, List, Optional

from django.conf import settings
from django.core.cache import cache

from .feed_mix import SOURCES

logger = logging.getLogger(__name__)

CURSOR_VERSION = 1
DEFAULT_CONFIG = {
    "enabled": True,       # False -> HomeFeedView keeps the old page/offset behaviour
    "ttl_seconds": 900,    # how long a scrolling session's ranking stays frozen
}
_SNAPSHOT_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def get_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(getattr(settings, "FEED_SNAPSHOT", None) or {})
    try:
        cfg["ttl_seconds"] = max(30, int(cfg["ttl_seconds"]))
    except (TypeError, ValueError):
        cfg["ttl_seconds"] = DEFAULT_CONFIG["ttl_seconds"]
    return cfg


def is_enabled() -> bool:
    return bool(get_config()["enabled"])


def new_snapshot_id() -> str:
    return uuid.uuid4().hex


def cache_key(user_id, snapshot_id: str, namespace: Optional[str] = None) -> str:
    """Home keeps `feed:snap:<user>:<id>`; other surfaces (Reels) pass a
    `namespace` -> `feed:snap:<namespace>:<user>:<id>` so they can never
    collide with, or read, a Home snapshot."""
    if namespace:
        return f"feed:snap:{namespace}:{user_id}:{snapshot_id}"
    return f"feed:snap:{user_id}:{snapshot_id}"


# --------------------------------------------------------------------------
# cursor
# --------------------------------------------------------------------------
def encode_cursor(snapshot_id: str, offsets: Dict[str, int], seen_cutoff: Optional[str]) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "s": snapshot_id,
        "o": {src: int(offsets.get(src, 0)) for src in SOURCES},
        "c": seen_cutoff,
    }
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(token: str) -> dict:
    """token -> {"snapshot_id", "offsets", "seen_cutoff"}; garbage / tampered
    cursors raise NotFound (same convention as DRF's CursorPagination)."""
    from rest_framework.exceptions import NotFound

    try:
        raw = base64.urlsafe_b64decode(str(token).encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION:
            raise ValueError("version")
        snapshot_id = data["s"]
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.match(snapshot_id):
            raise ValueError("snapshot id")
        raw_offsets = data["o"]
        if not isinstance(raw_offsets, dict):
            raise ValueError("offsets")
        offsets = {}
        for src in SOURCES:
            value = raw_offsets.get(src, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("offset value")
            offsets[src] = value
        cutoff = data.get("c")
        if cutoff is not None and not isinstance(cutoff, str):
            raise ValueError("cutoff")
        return {"snapshot_id": snapshot_id, "offsets": offsets, "seen_cutoff": cutoff}
    except Exception:
        raise NotFound("Invalid cursor.")


# --------------------------------------------------------------------------
# storage (cache failures degrade to "no snapshot", never to an error)
# --------------------------------------------------------------------------
def save(user_id, snapshot_id: str, pools: Dict[str, List], ratios: Dict[str, float]) -> bool:
    payload = json.dumps({
        "v": CURSOR_VERSION,
        "ratios": {src: float(ratios.get(src, 0.0)) for src in SOURCES},
        "pools": {src: [str(pid) for pid in pools.get(src, [])] for src in SOURCES},
    }, separators=(",", ":"))
    try:
        cache.set(cache_key(user_id, snapshot_id), payload, get_config()["ttl_seconds"])
        return True
    except Exception:  # pragma: no cover - depends on the cache backend being down
        logger.warning("feed snapshot save failed", exc_info=True)
        return False


def load(user_id, snapshot_id: str) -> Optional[dict]:
    """-> {"pools": {src: [UUID, ...]}, "ratios": {...}} or None (miss/corrupt)."""
    try:
        raw = cache.get(cache_key(user_id, snapshot_id))
    except Exception:  # pragma: no cover
        logger.warning("feed snapshot load failed", exc_info=True)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        pools = {src: [uuid.UUID(x) for x in data["pools"].get(src, [])] for src in SOURCES}
        ratios = {src: float(data["ratios"].get(src, 0.0)) for src in SOURCES}
        return {"pools": pools, "ratios": ratios}
    except Exception:
        logger.warning("feed snapshot corrupt, ignoring", exc_info=True)
        return None


# --------------------------------------------------------------------------
# Single-list snapshots (namespaced) - used by the Reels feed (post/reels.py)
#
# Home freezes THREE pools and pages them with per-source offsets. Reels has
# ONE ranked pool, so its cursor carries a single integer offset and the
# snapshot is a plain ordered id list, stored under its own namespace:
#
#     cursor = base64url(json{v, n: "reels", s: snapshot_id, o: offset, c: seen_cutoff})
#
# Same guarantees as Home: keyed by user id (a copied cursor just misses),
# cache failures degrade to "no snapshot" (caller rebuilds), garbage cursors
# raise NotFound. A Home cursor is rejected here and a Reels cursor is
# rejected by Home's decode_cursor (its `o` is an int, not a dict).
# --------------------------------------------------------------------------
def encode_list_cursor(namespace: str, snapshot_id: str, offset: int, seen_cutoff: Optional[str]) -> str:
    payload = {"v": CURSOR_VERSION, "n": namespace, "s": snapshot_id, "o": int(offset), "c": seen_cutoff}
    raw = json.dumps(payload, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_list_cursor(token: str, namespace: str) -> dict:
    """token -> {"snapshot_id", "offset", "seen_cutoff"}; anything else -> NotFound."""
    from rest_framework.exceptions import NotFound

    try:
        raw = base64.urlsafe_b64decode(str(token).encode("ascii")).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("v") != CURSOR_VERSION or data.get("n") != namespace:
            raise ValueError("version / namespace")
        snapshot_id = data["s"]
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID_RE.match(snapshot_id):
            raise ValueError("snapshot id")
        offset = data["o"]
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset")
        cutoff = data.get("c")
        if cutoff is not None and not isinstance(cutoff, str):
            raise ValueError("cutoff")
        return {"snapshot_id": snapshot_id, "offset": offset, "seen_cutoff": cutoff}
    except Exception:
        raise NotFound("Invalid cursor.")


def save_list(namespace: str, user_id, snapshot_id: str, ids: List, ttl_seconds: Optional[int] = None) -> bool:
    payload = json.dumps({"v": CURSOR_VERSION, "ids": [str(pid) for pid in ids]}, separators=(",", ":"))
    try:
        cache.set(cache_key(user_id, snapshot_id, namespace), payload, ttl_seconds or get_config()["ttl_seconds"])
        return True
    except Exception:  # pragma: no cover - depends on the cache backend being down
        logger.warning("%s snapshot save failed", namespace, exc_info=True)
        return False


def load_list(namespace: str, user_id, snapshot_id: str) -> Optional[List[uuid.UUID]]:
    """-> ordered [UUID, ...] or None (miss / corrupt / cache down)."""
    try:
        raw = cache.get(cache_key(user_id, snapshot_id, namespace))
    except Exception:  # pragma: no cover
        logger.warning("%s snapshot load failed", namespace, exc_info=True)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return [uuid.UUID(x) for x in data["ids"]]
    except Exception:
        logger.warning("%s snapshot corrupt, ignoring", namespace, exc_info=True)
        return None
