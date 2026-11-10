"""doubts/throttles.py — per-user buckets for the hub. Rates live in
settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'] (check_config_drift
verifies both scopes below have an entry).

NOTE: write actions are dispatched into the SOURCE app's own view, which
applies ITS throttle as well (e.g. campus_doubt_post) — so a hub write is
limited by whichever bucket is tighter, never looser."""
from rest_framework.throttling import ScopedRateThrottle


class DoubtsHubReadThrottle(ScopedRateThrottle):
    scope = "doubts_hub_read"


class DoubtsHubWriteThrottle(ScopedRateThrottle):
    scope = "doubts_hub_write"
