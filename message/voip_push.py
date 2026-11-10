# message/voip_push.py
#
# C5 — iOS PushKit (VoIP) push for incoming calls.
#
# WAJAH: FCM ka data-only message iOS par app killed/background hone par
# reliably deliver nahi hota (Apple silent push ko throttle karta hai), isliye
# "WhatsApp jaisa" ring sirf PushKit/VoIP push se aati hai — wo app ko jagata
# hai aur CallKit incoming-call UI turant dikhata hai (lock screen par bhi).
# FCM VoIP push support nahi karta, isliye hum APNs ko seedha HTTP/2 par call
# karte hain (token-based auth, .p8 key).
#
# Apple ka rule: har VoIP push par app ko CallKit call REPORT karni hi padti
# hai (ios AppDelegate me — platform_snippets/ios_AppDelegate.swift.txt), warna
# iOS baad me VoIP push dena band kar deta hai. Isi liye:
#   * yahan sirf `incoming_call` VoIP bheja jaata hai, "call_cancelled" NAHI
#     (cancel ko VoIP se bhejna = ek aur phantom call report karna padega).
#     Cancel normal FCM push + ring timeout se handle hota hai.
#   * jis device par VoIP push chali gayi, wahan FCM incoming_call NAHI bheji
#     jaati (warna ek hi phone par do baar ring).
#
# Settings (LearnScroll/settings.py / env) — sab optional; koi missing ho to
# `is_configured()` False aur code purane FCM-only behaviour par girta hai:
#   APNS_VOIP_KEY_PATH   .p8 file ka path (Apple Developer > Keys, APNs enabled)
#   APNS_KEY_ID          10-char key id
#   APNS_TEAM_ID         10-char team id
#   APNS_BUNDLE_ID       app ka bundle id (topic = "<bundle>.voip")
#   APNS_USE_SANDBOX     True => api.sandbox.push.apple.com (Xcode debug builds)
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

logger = logging.getLogger(__name__)

APNS_PROD_HOST = "https://api.push.apple.com"
APNS_SANDBOX_HOST = "https://api.sandbox.push.apple.com"

# Apple: provider token 20 min se pehle refresh mat karo, 60 min se purana
# reject hota hai.
_JWT_TTL_SECONDS = 40 * 60

# In reasons par token hamesha ke liye dead hai -> DB se hata do.
_DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}

_lock = threading.Lock()
_jwt_cache = {"token": None, "issued_at": 0.0}
_clients = {}


def _cfg(name, default=None):
    import os
    value = getattr(settings, name, None)
    if value in (None, ""):
        value = os.getenv(name)
    return default if value in (None, "") else value


def _use_sandbox():
    raw = _cfg("APNS_USE_SANDBOX", None)
    if raw is None:
        return bool(getattr(settings, "DEBUG", False))
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def is_configured():
    return all(_cfg(n) for n in ("APNS_VOIP_KEY_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID"))


def _provider_token():
    import jwt  # PyJWT[crypto]

    with _lock:
        now = time.time()
        if _jwt_cache["token"] and now - _jwt_cache["issued_at"] < _JWT_TTL_SECONDS:
            return _jwt_cache["token"]
        with open(str(_cfg("APNS_VOIP_KEY_PATH")), "r") as fh:
            key = fh.read()
        token = jwt.encode(
            {"iss": str(_cfg("APNS_TEAM_ID")), "iat": int(now)},
            key,
            algorithm="ES256",
            headers={"kid": str(_cfg("APNS_KEY_ID"))},
        )
        _jwt_cache.update(token=token, issued_at=now)
        return token


def _invalidate_provider_token():
    with _lock:
        _jwt_cache.update(token=None, issued_at=0.0)


def _client():
    import httpx  # httpx[http2]

    host = APNS_SANDBOX_HOST if _use_sandbox() else APNS_PROD_HOST
    with _lock:
        c = _clients.get(host)
        if c is None:
            c = httpx.Client(base_url=host, http2=True, timeout=5.0)
            _clients[host] = c
        return c


def _build_payload(*, call_id, caller_name, call_type, conversation_id, channel_name, caller_avatar,
                   call_waiting=False):
    is_video = call_type == "video"
    extra = {
        "call_id": str(call_id),
        "conversation_id": str(conversation_id) if conversation_id else "",
        "channel_name": channel_name or "",
        "call_type": call_type,
        "caller_name": caller_name or "",
        "caller_avatar": caller_avatar or "",
        "call_waiting": "true" if call_waiting else "false",
    }
    # Top-level keys flutter_callkit_incoming ke AppDelegate snippet padhta hai.
    return {
        "id": str(call_id),  # CallKit UUID — CallSession.id UUID hai, valid
        "nameCaller": caller_name or "Unknown",
        "handle": "Video Call" if is_video else "Voice Call",
        "isVideo": is_video,
        "avatar": caller_avatar or "",
        "extra": extra,
    }


def _send_one(voip_token, payload_bytes, *, call_id, ttl_seconds):
    """Returns (delivered: bool, token_is_dead: bool)."""
    headers = {
        "authorization": f"bearer {_provider_token()}",
        "apns-topic": f"{_cfg('APNS_BUNDLE_ID')}.voip",
        "apns-push-type": "voip",
        "apns-priority": "10",
        # Purani ring baad me achanak na bajne lage: phone offline tha to bhi
        # APNs ye push ttl ke baad drop kar dega.
        "apns-expiration": str(int(time.time()) + int(ttl_seconds)),
        # Same call ki dobara-bheji push purani ko replace kare.
        "apns-collapse-id": str(call_id)[:64],
    }
    try:
        resp = _client().post(f"/3/device/{voip_token}", content=payload_bytes, headers=headers)
    except Exception as exc:  # network / http2 / import error
        logger.warning("APNs VoIP send failed (%s): %s", type(exc).__name__, exc)
        return False, False

    if resp.status_code == 200:
        return True, False

    reason = ""
    try:
        reason = (resp.json() or {}).get("reason", "")
    except Exception:
        pass
    if resp.status_code == 403:
        # ExpiredProviderToken / InvalidProviderToken — agli baar naya JWT banega.
        _invalidate_provider_token()
    dead = resp.status_code == 410 or reason in _DEAD_TOKEN_REASONS
    logger.warning("APNs VoIP rejected: status=%s reason=%s dead=%s", resp.status_code, reason or "?", dead)
    return False, dead


def send_voip_call_push(devices, *, call_id, caller_name, call_type, conversation_id,
                        channel_name, caller_avatar=None, ttl_seconds=30, call_waiting=False):
    """
    devices: iterable of (fcm_token, voip_token) — sirf iOS rows jinke paas
    voip_token hai.

    Returns: set of FCM tokens jinke device par VoIP push SAFAL gayi. Baaki ke
    liye caller FCM fallback chalayega (APNs config missing / network error /
    token dead). Dead VoIP tokens yahin DB se saaf ho jaate hain.
    """
    devices = [(f, v) for f, v in devices if v]
    if not devices or not is_configured():
        if devices:
            logger.warning("APNS_* settings missing — iOS VoIP push skipped, FCM fallback use hoga.")
        return set()

    try:
        payload_bytes = json.dumps(
            _build_payload(
                call_id=call_id, caller_name=caller_name, call_type=call_type,
                conversation_id=conversation_id, channel_name=channel_name,
                caller_avatar=caller_avatar, call_waiting=call_waiting,
            ),
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        logger.exception("VoIP payload build failed")
        return set()

    def _job(pair):
        fcm_token, voip_token = pair
        try:
            ok, dead = _send_one(voip_token, payload_bytes, call_id=call_id, ttl_seconds=ttl_seconds)
        except Exception:
            logger.exception("VoIP push crashed")
            return fcm_token, voip_token, False, False
        return fcm_token, voip_token, ok, dead

    with ThreadPoolExecutor(max_workers=min(8, len(devices))) as pool:
        results = list(pool.map(_job, devices))

    delivered = {f for f, _v, ok, _d in results if ok}
    dead_tokens = [v for _f, v, _ok, dead in results if dead]
    if dead_tokens:
        from .models import DeviceToken
        DeviceToken.objects.filter(voip_token__in=dead_tokens).update(voip_token=None)
    return delivered
