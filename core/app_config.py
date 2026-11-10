# core/app_config.py
"""
T1.8 (R8) — remote app config (force-update / soft-update / maintenance).

`build_app_config(platform, client_version)` DB row (`core.AppConfig`, admin
se editable — release ke bina) ko settings/env defaults ke upar overlay karta
hai:

    field blank/absent in DB  ->  settings.APP_MIN_SUPPORTED_VERSION etc.

Version compare dotted-numeric hai ("1.10.0" > "1.9.3"); pubspec ka "+build"
aur "-beta" suffix ignore hota hai. Parse na ho paye (client ne kachra bheja
ya config me typo) to FAIL-OPEN: `update_required=False` — galat config
poore user-base ko app se lock-out na kar de. (Maintenance mode isse alag
hai: wo sirf explicit `maintenance_mode=True` pe lagta hai.)
"""
import re
from typing import Optional, Tuple

from django.conf import settings
from django.utils import timezone
from common.feature_flags import coin_cashout_enabled, flags_for_client  # P-G1 / P-J2

from .models import AppConfig

PLATFORMS = (AppConfig.Platform.ANDROID.value, AppConfig.Platform.IOS.value)
_VERSION_RE = re.compile(r"^\s*v?(\d+(?:\.\d+){0,3})")


def parse_version(raw) -> Optional[Tuple[int, ...]]:
    """'1.4.2+17' -> (1, 4, 2); '2' -> (2,); garbage/None -> None."""
    if not raw:
        return None
    m = _VERSION_RE.match(str(raw))
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def _cmp(a: Tuple[int, ...], b: Tuple[int, ...]) -> int:
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    return (a > b) - (a < b)


def is_older(client, target) -> bool:
    """True sirf jab dono parse ho jaayein aur client < target."""
    c, t = parse_version(client), parse_version(target)
    if c is None or t is None:
        return False
    return _cmp(c, t) < 0


def normalize_platform(raw) -> str:
    value = (raw or "").strip().lower()
    return value if value in PLATFORMS else ""


def _setting(name, default=""):
    return getattr(settings, name, default) or default


def _store_url(platform: str) -> str:
    if platform == AppConfig.Platform.IOS.value:
        return _setting("APP_STORE_URL")
    return _setting("PLAY_STORE_URL")


def feed_preview_seconds() -> int:
    """T5 - seconds of a feed video played before "Watch full". Bad / missing value -> 15; clamped to 3..120."""
    try:
        value = int(getattr(settings, "FEED_PREVIEW_SECONDS", 15))
    except (TypeError, ValueError):
        value = 15
    return max(3, min(120, value))


def _clamped_setting(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def post_video_max_seconds() -> int:
    """P3-4 - longest video (s) the editor accepts for a post. Bad / missing -> 90; clamped to 5..600."""
    return _clamped_setting("POST_VIDEO_MAX_SECONDS", 90, 5, 600)


def story_video_max_seconds() -> int:
    """P3-4 - longest video (s) the editor accepts for a story. Bad / missing -> 60; clamped to 5..600."""
    return _clamped_setting("STORY_VIDEO_MAX_SECONDS", 60, 5, 600)


def reel_video_max_seconds() -> int:
    """P3 follow-up - longest video (s) the reel composer accepts. Bad / missing -> 180; clamped to 5..600."""
    return _clamped_setting("REEL_VIDEO_MAX_SECONDS", 180, 5, 600)


def build_app_config(platform_raw, client_version=None, bucket_id=None) -> dict:
    platform = normalize_platform(platform_raw)
    row = AppConfig.objects.filter(platform=platform).first() if platform else None

    def pick(field, setting_name, default=""):
        value = getattr(row, field, "") if row else ""
        return value or _setting(setting_name, default)

    min_version = pick("min_supported_version", "APP_MIN_SUPPORTED_VERSION")
    latest_version = pick("latest_version", "APP_LATEST_VERSION")
    store_url = (row.store_url if row and row.store_url else "") or _store_url(platform)

    maintenance_on = bool(row.maintenance_mode) if row else bool(getattr(settings, "APP_MAINTENANCE_MODE", False))
    maintenance_msg = pick("maintenance_message", "APP_MAINTENANCE_MESSAGE")
    maintenance_msg_hi = pick("maintenance_message_hi", "APP_MAINTENANCE_MESSAGE_HI")

    update_required = is_older(client_version, min_version)
    # "required" implies "available"; latest < min (misconfig) never hides it.
    update_available = update_required or is_older(client_version, latest_version)

    return {
        "platform": platform or None,
        "min_supported_version": min_version or None,
        "latest_version": latest_version or None,
        "update_required": update_required,
        "update_available": update_available,
        "store_url": store_url or None,
        "message": pick("update_message", "APP_UPDATE_MESSAGE") or None,
        "message_hi": pick("update_message_hi", "APP_UPDATE_MESSAGE_HI") or None,
        "maintenance": {
            "active": maintenance_on,
            "message": maintenance_msg or None,
            "message_hi": maintenance_msg_hi or None,
        },
        "feed_preview_seconds": feed_preview_seconds(),
        "post_video_max_seconds": post_video_max_seconds(),
        "story_video_max_seconds": story_video_max_seconds(),
        "reel_video_max_seconds": reel_video_max_seconds(),
        "coin_cashout_enabled": coin_cashout_enabled(),  # P-G1: False -> the app hides the withdraw UI (kept for old builds)
        # P-J2: remote kill switches + staged rollout. `bucket_id` = the app's X-Device-Id (stable per install), so a 10%
        # rollout shows the same 10% of devices every time. Every flag key is always present.
        "feature_flags": flags_for_client(platform, client_version, bucket_id),
        "server_time": timezone.now().isoformat(),
    }
