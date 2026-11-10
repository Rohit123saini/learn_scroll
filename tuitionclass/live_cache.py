"""
tuitionclass/live_cache.py  -  X12 L-D #3: the cache the LIVE CHAT hot path uses

Slow mode, the flood / duplicate / strike counters, report budgets and the mute lookup all keep tiny counters with a
short TTL. They need ONE cache shared by every web process (a per-process LocMemCache would let a student send once
per worker in slow mode). settings.py already points CACHES["default"] at Redis whenever REDIS_URL is set and refuses to
boot in production without it, so by default `live_cache` simply IS the default cache.

Optional isolation: set LIVE_REDIS_URL (for example redis://host:6379/3) and settings.py adds CACHES["live"]. Then these
hot chat keys live in their own Redis DB, so a `cache.clear()` / eviction storm of the feed cache can never wipe the
slow-mode and mute state of a running class. Nothing else changes - same code, same key names.

`live_cache` is a thin proxy that looks the alias up on every call (so tests that use override_settings(CACHES=...) work
and nothing is bound at import time). It has the same API as django's cache (add / get / set / incr / delete ...).
"""

from django.conf import settings
from django.core.cache import DEFAULT_CACHE_ALIAS, caches

LIVE_CACHE_ALIAS = "live"


def live_cache_alias() -> str:
    return LIVE_CACHE_ALIAS if LIVE_CACHE_ALIAS in getattr(settings, "CACHES", {}) else DEFAULT_CACHE_ALIAS


class _LiveCache:
    def __getattr__(self, name):
        return getattr(caches[live_cache_alias()], name)


live_cache = _LiveCache()
