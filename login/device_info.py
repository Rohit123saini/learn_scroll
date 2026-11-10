# login/device_info.py
"""
L1 gap 11 -- what we remember about the device a session was created on.

The Flutter app sends three headers on every request to our API
(services/session_http_interceptor.dart + services/device_info_service.dart):

    X-Device-Id    random per-install id (survives logout)
    X-Device-Name  e.g. "Android 13"
    X-Platform     android | ios | web | windows | macos | linux

Everything here is INFORMATIONAL (shown in "Devices where you're signed in").
It is client-supplied, so it is never used to decide who may do what: it is
length-capped, stripped of control characters, and `platform` is checked
against a fixed list. Missing headers (old app versions, a browser, curl)
fall back to a label guessed from the User-Agent.
"""
import ipaddress
import re

from .login_guard import client_ip

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

PLATFORMS = ("android", "ios", "web", "windows", "macos", "linux", "other")

_DEFAULT_NAMES = {
    "android": "Android device",
    "ios": "iPhone / iPad",
    "web": "Web browser",
    "windows": "Windows PC",
    "macos": "Mac",
    "linux": "Linux PC",
    "other": "Unknown device",
}


def _clean(value, limit):
    if not isinstance(value, str):
        return ""
    return _CONTROL_CHARS.sub("", value).strip()[:limit]


def _platform_from_user_agent(user_agent):
    ua = user_agent.lower()
    if "android" in ua:  # before "linux": Android UAs contain both
        return "android"
    if "iphone" in ua or "ipad" in ua or "ios" in ua:
        return "ios"
    if "windows" in ua:
        return "windows"
    if "macintosh" in ua or "mac os" in ua:
        return "macos"
    if "linux" in ua:
        return "linux"
    if "mozilla" in ua:
        return "web"
    return "other"


def extract_device_info(request):
    """Return kwargs for AuthToken(...): device_id, device_name, platform,
    ip_address, user_agent. Never raises."""
    try:
        meta = request.META
    except Exception:
        return {}

    user_agent = _clean(meta.get("HTTP_USER_AGENT", ""), 255)

    platform = _clean(meta.get("HTTP_X_PLATFORM", ""), 20).lower()
    if platform not in PLATFORMS:
        platform = _platform_from_user_agent(user_agent)

    device_name = _clean(meta.get("HTTP_X_DEVICE_NAME", ""), 120) or _DEFAULT_NAMES[platform]

    # Same alphabet as a uuid / hex id; anything else is dropped rather than stored.
    device_id = _clean(meta.get("HTTP_X_DEVICE_ID", ""), 64)
    if not re.fullmatch(r"[A-Za-z0-9._-]{8,64}", device_id):
        device_id = ""

    ip = client_ip(request)
    try:
        ip_address = str(ipaddress.ip_address(ip))
    except ValueError:
        ip_address = None

    return {
        "device_id": device_id,
        "device_name": device_name,
        "platform": platform,
        "ip_address": ip_address,
        "user_agent": user_agent,
    }
