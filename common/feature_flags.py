"""
common/feature_flags.py - P-G1: one place that answers "is this risky feature switched on?".

P-G1: the coin CASH-OUT switch (`settings.COIN_CASHOUT_ENABLED`, default False).
P-J2: remote kill switches + staged rollout for five risky features (live, reels_upload, coin_purchase, ai_features, cashout),
      edited in Django admin (`core.FeatureFlag`) and published to the app inside GET /core/app-config/ -> `feature_flags`.

Why a helper and not `settings.X` everywhere:
  * the flag is read AT CALL TIME, so `override_settings(COIN_CASHOUT_ENABLED=True)` works in tests and a restart with a
    changed env var takes effect with no code change;
  * a missing / odd value counts as OFF (fail closed) - an unset or mistyped env var can never switch money-out on;
  * every endpoint refuses with the SAME body, so the Flutter app has one code to look for: "feature_disabled".
"""
from __future__ import annotations

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

FEATURE_DISABLED = "feature_disabled"
DISABLED_HEADER = "X-Feature-Disabled"  # P-J2: response header naming the switched-off flag (read by the app's ApiClient)
COIN_CASHOUT = "coin_cashout"

CASHOUT_DISABLED_MESSAGE = "Coin cash-out abhi available nahi hai - jaldi aa raha hai (Coming soon)."


def _cashout_env_enabled() -> bool:
    """The P-G1 switch alone: True ONLY when COIN_CASHOUT_ENABLED is the boolean True (or a clear 'true' string)."""
    value = getattr(settings, "COIN_CASHOUT_ENABLED", False)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return value is True


def coin_cashout_enabled() -> bool:
    """True ONLY when the environment switch is ON *and* nobody switched `cashout` off in Django admin (P-J2: the admin
    row can only tighten it). Every existing cash-out check calls this, so the remote kill switch needs no other edit."""
    if not _cashout_env_enabled():
        return False
    return server_blocked(CASHOUT) is None


def cashout_disabled_response() -> Response:
    """403 in the project's usual {status, message} envelope + a stable machine code for the app."""
    return Response(
        {
            "status": False,
            "code": FEATURE_DISABLED,
            "feature": COIN_CASHOUT,
            "message": CASHOUT_DISABLED_MESSAGE,
        },
        status=status.HTTP_403_FORBIDDEN,
    )


# =====================================================================================================================
# P-J2 - remote feature flags (kill switches + staged rollout)
# =====================================================================================================================
# Two different questions, two different functions:
#   * `evaluate()`        - "should THIS app (platform, version, device) SHOW the feature?"  -> goes to the app in app-config.
#                           Honours the on/off switch, per-platform rows, min app version and the rollout percentage.
#   * `server_blocked()`  - "must the SERVER refuse the call, whatever the app says?"        -> used by the gate middleware.
#                           Only the on/off kill switch (env or admin) counts: a rollout percentage is about who SEES a new
#                           feature, not a security boundary, and the server has no stable device id on every request.
# Fail-safe rules: a broken database / cache never switches a feature off by accident (kill switches are explicit), EXCEPT
# cash-out, which is OFF unless the environment says true (P-G1) and a row can only tighten it.
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

LIVE = "live"
REELS_UPLOAD = "reels_upload"
COIN_PURCHASE = "coin_purchase"
AI_FEATURES = "ai_features"
CASHOUT = "cashout"  # same flag as COIN_CASHOUT above, under its remote name
SOCIAL_LEARNING = "social_learning"  # P-F5: SERVER-side kill switch only (social_learning/conf.py). NOT in ALL_FLAGS on purpose:
# the app never hides it via app-config (it already reads the 403 `feature_disabled` + X-Feature-Disabled header), so the
# app-config contract / seed command stay unchanged. Create a row in Django admin (key = social_learning) or set
# FEATURE_FLAGS_OFF=social_learning to switch it off.
SPACED_REPETITION = "spaced_repetition"  # P-F8: SERVER-side kill switch only (spaced_repetition/conf.py); same convention as
# SOCIAL_LEARNING above (not in ALL_FLAGS; the app reads the 403 `feature_disabled` body).
ALL_FLAGS = (LIVE, REELS_UPLOAD, COIN_PURCHASE, AI_FEATURES, CASHOUT)

FLAG_MESSAGES = {
    LIVE: ("Live abhi band hai - thodi der baad try karein.", "Live abhi band hai - thodi der baad try karein."),
    REELS_UPLOAD: ("Reel upload abhi band hai - thodi der baad try karein.", "Reel upload abhi band hai - thodi der baad try karein."),
    COIN_PURCHASE: ("Coins kharidna abhi band hai - thodi der baad try karein.", "Coins kharidna abhi band hai - thodi der baad try karein."),
    AI_FEATURES: ("AI helper abhi band hai - thodi der baad try karein.", "AI helper abhi band hai - thodi der baad try karein."),
    CASHOUT: (CASHOUT_DISABLED_MESSAGE, CASHOUT_DISABLED_MESSAGE),
    SOCIAL_LEARNING: ("Study seasons / partner abhi band hai - thodi der baad try karein.", "Study seasons / partner abhi band hai - thodi der baad try karein."),
}

_CACHE_KEY = "feature_flags:rows:v1"
_CACHE_SECONDS = 30  # a flip in admin reaches every web worker within this time (and at once on the worker that saved it)


@dataclass(frozen=True)
class FlagState:
    key: str
    enabled: bool
    reason: str  # "on" | "env_off" | "cashout_env_off" | "admin_off" | "app_too_old" | "not_in_rollout"
    message: str = ""
    message_hi: str = ""


def _env_off_set() -> set:
    """settings.FEATURE_FLAGS_OFF = 'live,ai_features' - emergency OFF with no database (env var + restart)."""
    raw = getattr(settings, "FEATURE_FLAGS_OFF", "") or ""
    if isinstance(raw, (list, tuple, set)):
        return {str(x).strip().lower() for x in raw if str(x).strip()}
    return {x.strip().lower() for x in str(raw).split(",") if x.strip()}


def _load_rows() -> Dict[str, Dict[str, dict]]:
    """{key: {platform: {enabled, rollout_percent, min_app_version, message, message_hi}}} - cached ~30 s.
    A database problem returns {} (= defaults) and is logged; it is never cached, so the next call retries."""
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return cached
    rows: Dict[str, Dict[str, dict]] = {}
    try:
        from core.models import FeatureFlag

        for r in FeatureFlag.objects.all():
            rows.setdefault(r.key, {})[r.platform] = {
                "enabled": bool(r.enabled),
                "rollout_percent": max(0, min(100, int(r.rollout_percent))),
                "min_app_version": (r.min_app_version or "").strip(),
                "message": r.message or "",
                "message_hi": r.message_hi or "",
            }
    except Exception:
        logger.exception("feature_flags: could not read core.FeatureFlag rows; using defaults")
        return {}
    try:
        cache.set(_CACHE_KEY, rows, _CACHE_SECONDS)
    except Exception:
        logger.warning("feature_flags: cache write failed", exc_info=True)
    return rows


def invalidate_cache() -> None:
    """Called when a FeatureFlag row is saved / deleted (core/signals.py) so the change is live immediately."""
    try:
        cache.delete(_CACHE_KEY)
    except Exception:
        logger.warning("feature_flags: cache delete failed", exc_info=True)


def _row_for(key: str, platform: str, rows=None) -> Optional[dict]:
    rows = _load_rows() if rows is None else rows
    per_platform = rows.get(key) or {}
    return per_platform.get((platform or "").strip().lower()) or per_platform.get("all")


def _bucket(key: str, bucket_id) -> Optional[int]:
    """Stable 0..99 per (flag, device). None when the caller has no id (-> cannot be placed in a partial rollout)."""
    raw = str(bucket_id or "").strip()
    if not raw:
        return None
    digest = hashlib.sha256(f"{key}:{raw}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _messages(key: str, row: Optional[dict]):
    en, hi = FLAG_MESSAGES.get(key, ("", ""))
    if row:
        en = row.get("message") or en
        hi = row.get("message_hi") or row.get("message") or hi
    return en, hi


def server_blocked(key: str, platform: str = "", rows=None) -> Optional[FlagState]:
    """The kill-switch half: returns a disabled FlagState when the server must refuse `key`, else None."""
    if key in _env_off_set():
        en, hi = _messages(key, None)
        return FlagState(key, False, "env_off", en, hi)
    if key == CASHOUT and not _cashout_env_enabled():
        en, hi = _messages(key, None)
        return FlagState(key, False, "cashout_env_off", en, hi)
    row = _row_for(key, platform, rows)
    if row is not None and not row["enabled"]:
        en, hi = _messages(key, row)
        return FlagState(key, False, "admin_off", en, hi)
    return None


def evaluate(key: str, platform: str = "", version: str = "", bucket_id=None, rows=None) -> FlagState:
    """The app-facing answer for one flag (switch + platform + min version + rollout percentage)."""
    blocked = server_blocked(key, platform, rows)
    if blocked is not None:
        return blocked
    row = _row_for(key, platform, rows)
    en, hi = _messages(key, row)
    if row is None:
        return FlagState(key, True, "on")
    min_version = row.get("min_app_version") or ""
    if min_version:
        from core.app_config import is_older, parse_version

        # No / garbled client version while a floor is set -> not eligible (a staged feature must never leak to unknown builds).
        if parse_version(version) is None or is_older(version, min_version):
            return FlagState(key, False, "app_too_old", en, hi)
    percent = row["rollout_percent"]
    if percent >= 100:
        return FlagState(key, True, "on")
    if percent <= 0:
        return FlagState(key, False, "not_in_rollout", en, hi)
    bucket = _bucket(key, bucket_id)
    if bucket is None or bucket >= percent:
        return FlagState(key, False, "not_in_rollout", en, hi)
    return FlagState(key, True, "on")


def flags_for_client(platform: str = "", version: str = "", bucket_id=None) -> dict:
    """JSON for GET /core/app-config/ -> `feature_flags`: {key: {"enabled": bool, "reason": str, "message"?, "message_hi"?}}.
    Every known flag is always present, so the app never has to guess a missing key."""
    out = {}
    for key in ALL_FLAGS:
        state = evaluate(key, platform, version, bucket_id)
        item = {"enabled": state.enabled, "reason": state.reason}
        if not state.enabled:
            item["message"] = state.message
            item["message_hi"] = state.message_hi
        out[key] = item
    return out


def has_partial_rollout() -> bool:
    """True when any flag row is a partial rollout or has a version floor -> the app-config answer then depends on the
    caller's device / version, so it must not sit in a shared (CDN) cache."""
    for per_platform in _load_rows().values():
        for row in per_platform.values():
            if row["enabled"] and (row["rollout_percent"] < 100 or row["min_app_version"]):
                return True
    return False


def flag_disabled_response(state_or_key, platform: str = "") -> Response:
    """403 in the project's {status, code, feature, message} envelope - the same body the cash-out refusal always had, so
    the Flutter app has ONE shape to look for."""
    state = state_or_key if isinstance(state_or_key, FlagState) else server_blocked(state_or_key, platform)
    key = state.key if state else str(state_or_key)
    en, hi = (state.message, state.message_hi) if state else _messages(key, None)
    response = Response(
        {"status": False, "success": False, "code": FEATURE_DISABLED, "feature": key, "message": en, "message_hi": hi},
        status=status.HTTP_403_FORBIDDEN,
    )
    response[DISABLED_HEADER] = key
    return response
