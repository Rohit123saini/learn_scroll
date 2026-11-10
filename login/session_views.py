# login/session_views.py
"""
L1 gaps 10 + 11 -- server-side logout and device (session) management.

    POST   /login/auth/logout/               end THIS session on the server
    GET    /login/auth/sessions/             devices where the user is signed in
    DELETE /login/auth/sessions/<id>/        sign ONE device out
    POST   /login/auth/logout-all/           sign out of every device
                                             ({"keep_current": true} = every OTHER device)

A session is one ``AuthToken`` row; deleting it is what kills the session
(SlidingSessionAuthentication answers TOKEN_EXPIRED, SessionTokenRefreshView
refuses to refresh, the websocket middleware refuses to connect). The logout
endpoint also blacklists the refresh token it was given (gap 9).
"""
import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from . import audit  # L1 gap 19 -- audit trail
from .errors import UnifiedErrorsMixin, api_error  # L1 gap 16 -- one error envelope
from .models import AuthToken
from .token_issuance import revoke_user_sessions

logger = logging.getLogger(__name__)


def _current_session_jti(request):
    """session_jti of the access token that made this request (None for legacy tokens)."""
    auth = getattr(request, "auth", None)
    if auth is not None and hasattr(auth, "get"):
        return auth.get("session_jti")
    return None


def _body(request):
    data = request.data
    return data if isinstance(data, dict) else {}


def _truthy(value):
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _serialize(session, current_jti):
    return {
        "id": session.id,
        "device_name": session.device_name or "Unknown device",
        "platform": session.platform or "other",
        "ip_address": session.ip_address,
        "created_at": session.created_at,
        "last_used_at": session.last_used_at,
        "is_current": bool(current_jti) and session.jti == current_jti,
    }


class LogoutView(UnifiedErrorsMixin, APIView):
    """End this session on the server.

    Deliberately NOT behind IsAuthenticated: the app's access token is only
    minutes long, so by the time the user taps Logout it has often expired --
    the refresh token in the body is what identifies the session then. Always
    answers 200 (logging out an already-dead session is a success), so the app
    can treat this as best-effort and never block the local sign-out on it.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "logout"

    def post(self, request):
        session_jti = None

        raw_refresh = _body(request).get("refresh")
        if isinstance(raw_refresh, str) and raw_refresh:
            try:
                refresh = RefreshToken(raw_refresh)  # TokenError: bad / expired / already blacklisted
            except TokenError:
                refresh = None
            if refresh is not None:
                session_jti = refresh.get("session_jti")
                try:
                    refresh.blacklist()  # gap 9: this refresh token can never be used again
                except Exception:  # token_blacklist app missing / row race -- the AuthToken delete still ends the session
                    logger.warning("logout: could not blacklist refresh token", exc_info=True)

        if not session_jti:
            # No (valid) refresh token sent -- fall back to the access token header.
            parts = request.META.get("HTTP_AUTHORIZATION", "").split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                try:
                    session_jti = AccessToken(parts[1]).get("session_jti")
                except TokenError:
                    session_jti = None

        ended = 0
        if session_jti:
            owner_id = AuthToken.objects.filter(jti=session_jti).values_list("user_id", flat=True).first()
            ended, _ = AuthToken.objects.filter(jti=session_jti).delete()
            if ended:  # an unknown / already-dead session is not worth a row (anyone can call this endpoint)
                audit.record(audit.Event.LOGOUT, request, user=owner_id)

        return Response(
            {"status": True, "message": "Logged out.", "sessions_ended": ended},
            status=status.HTTP_200_OK,
        )


class SessionListView(UnifiedErrorsMixin, APIView):
    """Devices where the signed-in user currently has a live session."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "session_manage"

    def get(self, request):
        current_jti = _current_session_jti(request)
        cutoff = timezone.now() - AuthToken.SLIDING_EXPIRY
        sessions = AuthToken.objects.filter(user=request.user, last_used_at__gt=cutoff).order_by(
            "-last_used_at", "-id"
        )
        data = [_serialize(s, current_jti) for s in sessions]
        data.sort(key=lambda s: not s["is_current"])  # this device first (stable sort keeps recency order)
        return Response({"status": True, "count": len(data), "sessions": data})


class SessionDetailView(UnifiedErrorsMixin, APIView):
    """Sign ONE device out."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "session_manage"

    def delete(self, request, pk):
        # Filtered by user: another user's session id is just a 404.
        session = AuthToken.objects.filter(user=request.user, pk=pk).first()
        if session is None:
            return api_error(
                "That device is already signed out.",
                "session_not_found",
                status.HTTP_404_NOT_FOUND,
            )
        was_current = session.jti == _current_session_jti(request)
        device_name, platform = session.device_name, session.platform
        session.delete()
        audit.record(
            audit.Event.SESSION_REVOKED, request, user=request.user,
            was_current=was_current, revoked_device=device_name, revoked_platform=platform,
        )
        return Response(
            {"status": True, "message": "Device signed out.", "was_current": was_current},
            status=status.HTTP_200_OK,
        )


class LogoutAllView(UnifiedErrorsMixin, APIView):
    """Sign out of every device. `{"keep_current": true}` leaves this one signed in."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "session_manage"

    def post(self, request):
        keep_current = _truthy(_body(request).get("keep_current", False))
        keep_jti = _current_session_jti(request) if keep_current else None
        ended = revoke_user_sessions(request.user, keep_jti=keep_jti)
        audit.record(
            audit.Event.LOGOUT_ALL, request, user=request.user,
            sessions_ended=ended, keep_current=bool(keep_jti),
        )
        return Response(
            {
                "status": True,
                "message": "Signed out of other devices." if keep_current else "Signed out of all devices.",
                "sessions_ended": ended,
                "current_kept": bool(keep_jti),
            },
            status=status.HTTP_200_OK,
        )
