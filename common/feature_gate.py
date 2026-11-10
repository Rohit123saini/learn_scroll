"""common/feature_gate.py - P-J2: the SERVER half of the remote kill switches (+ an optional hard min-version gate).

The app hides a switched-off feature (lib/services/feature_flags.dart), but an old build, a cached screen or a script would
still call the API. This middleware refuses those calls centrally: one route table, one place to audit, no per-view edits.

    flag OFF (admin row `enabled=False`, or env FEATURE_FLAGS_OFF)  ->  403 {"code": "feature_disabled", "feature": "<key>"}

What is deliberately NOT gated (a kill switch must never strand money or a running session):
  * payment webhooks / confirm / reconcile (an order the user already paid must still settle),
  * ending / leaving a live session, LiveKit webhooks, reading anything (GET), receipts, ledger,
  * AI config + the free "quality tips" call.
Cash-out is enforced inside its own views through `coin_cashout_enabled()` (P-G1) - the admin row feeds that same function.

Optional hard gate (env APP_HARD_MIN_VERSION_GATE=true, default OFF): a request that carries X-App-Version older than the
platform's `min_supported_version` gets 426 {"code": "UPDATE_REQUIRED"}, which makes the app (ApiClient.onUpdateRequired)
show the blocking "Update now" dialog even in a session that never reopens the app. Requests with no X-App-Version header
(web pages, webhooks, admin, curl) and the app-config / health / legal paths are never blocked. Fail-open on any error.

Placement: after ApiVersionMiddleware, so `/v1/...` has already been rewritten to the unversioned path.
"""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from common import feature_flags as ff

logger = logging.getLogger(__name__)

_POST = ("POST",)
_WRITE = ("POST", "PUT", "PATCH")
_UUID = r"[0-9a-fA-F-]{32,36}"

# (flag, allowed-to-be-blocked HTTP methods, compiled path regex on the unversioned path)
ROUTES = [
    # ---- live: start a live, get a LiveKit token, join (incl. parent observer) ------------------------------------------
    (ff.LIVE, _POST, re.compile(r"^/tuitionclass/live/go/$")),
    (ff.LIVE, _POST, re.compile(r"^/tuitionclass/sessions/[^/]+/(join|token|parent-join|start)/$")),
    # a class's own "Enter class / Start class" button and the free-trial join both hand out a LiveKit token
    (ff.LIVE, _POST, re.compile(r"^/tuitionclass/classrooms/[^/]+/(start-or-join|trial-join)/$")),
    # ---- reels upload: start / chunk / finish of a REEL upload session is gated in post/upload_sessions.py (the kind is in
    #      the JSON body, which a URL table cannot see). Nothing here on purpose.
    # ---- coin purchase: create the order. NOT confirm / webhook / admin-confirm / reconcile ----------------------------
    (ff.COIN_PURCHASE, _POST, re.compile(r"^/profile/buy-coin/(order/)?$")),
    # ---- AI features (Gemini): every call that spends AI quota --------------------------------------------------------
    (ff.AI_FEATURES, _POST, re.compile(r"^/post/ai-assist/(?!quality/|config/)[^/]+/$")),
    (ff.AI_FEATURES, _POST, re.compile(r"^/post/" + _UUID + r"/ai-study/$")),
    (ff.AI_FEATURES, _POST, re.compile(r"^/message/ai/(smart-replies|classroom-copilot|ask-doubt|study-buddy|transcribe)/$")),
    (ff.AI_FEATURES, _POST, re.compile(r"^/message/ai-study-room/$")),
    (ff.AI_FEATURES, _POST, re.compile(r"^/message/study-room/" + _UUID + r"/revision-deck/$")),
]

# never hard-gated by version: the very endpoints an old build needs in order to learn it must update, plus infra.
_VERSION_GATE_EXEMPT = re.compile(
    r"^/(core/app-config/|healthz|readyz|health|ready|legal/|media/|pmedia/|static/|admin|swagger|redoc|api/schema|robots\.txt|sitemap\.xml|\.well-known/)"
)
_MIN_CACHE_KEY = "feature_gate:min_version:%s"


def matching_flag(method: str, path: str):
    """The flag key guarding (method, path), or None. Pure function - unit tested without a database."""
    method = (method or "").upper()
    for key, methods, pattern in ROUTES:
        if method in methods and pattern.match(path):
            return key
    return None


def _platform_of(request) -> str:
    return (request.headers.get("X-Platform") or "").strip().lower()


def _min_version(platform: str) -> str:
    """The platform's min_supported_version (DB row, else env), cached 60 s so the hard gate costs no query per request."""
    cached = cache.get(_MIN_CACHE_KEY % platform)
    if cached is not None:
        return cached
    from core.app_config import build_app_config

    value = build_app_config(platform, None).get("min_supported_version") or ""
    cache.set(_MIN_CACHE_KEY % platform, value, 60)
    return value


class FeatureFlagGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            blocked = self._check(request)
        except Exception:  # a bug here must never take the API down: log it and let the request through
            logger.exception("feature_gate: check failed; request allowed")
            blocked = None
        return blocked if blocked is not None else self.get_response(request)

    def _check(self, request):
        path = request.path_info
        key = matching_flag(request.method, path)
        if key is not None:
            state = ff.server_blocked(key, _platform_of(request))
            if state is not None:
                logger.info("feature_gate: refused %s %s (%s, %s)", request.method, path, key, state.reason)
                response = JsonResponse(
                    {"status": False, "success": False, "code": ff.FEATURE_DISABLED, "feature": key,
                     "message": state.message, "message_hi": state.message_hi},
                    status=403,
                )
                # The app's ApiClient reads this header on EVERY response, so a switched-off feature is switched off in the UI
                # at once, without each screen having to parse the body (and without mistaking this 403 for "buy a pass").
                response[ff.DISABLED_HEADER] = key
                return response
        if getattr(settings, "APP_HARD_MIN_VERSION_GATE", False):
            return self._version_gate(request, path)
        return None

    @staticmethod
    def _version_gate(request, path):
        client_version = request.headers.get("X-App-Version")
        if not client_version or _VERSION_GATE_EXEMPT.match(path):
            return None
        from core.app_config import is_older

        platform = _platform_of(request)
        if platform not in ("android", "ios"):
            return None
        if is_older(client_version, _min_version(platform)):
            return JsonResponse(
                {"status": False, "success": False, "code": "UPDATE_REQUIRED",
                 "message": "Please update the app to continue."},
                status=426,
            )
        return None
