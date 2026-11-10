# login/token_views.py
"""
Refresh-token endpoint that respects server-side session revocation.

Stock SimpleJWT ``TokenRefreshView`` only checks the refresh token's own
signature and expiry. Since this app now ends sessions server-side (password
reset / change delete the ``AuthToken`` row -- see token_issuance.
revoke_user_sessions), a stolen refresh token would otherwise keep minting
access tokens (each one then fails with TOKEN_EXPIRED on first use, but the app
would believe it was still signed in). This view refuses at the refresh step
instead, with a plain 401, which the Flutter ``AuthService._doRefresh`` already
turns into "log in again".

Refresh tokens without a ``session_jti`` claim (issued before sliding sessions
shipped) behave exactly as before.
"""
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from common.open_throttles import TokenRefreshThrottle  # P-B3 -- own scope, not the 20/min anon default
from . import audit  # L1 gap 19 -- audit trail
from .models import AuthToken


class SessionTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        # Raises TokenError for a bad/expired token -> the view returns 401.
        refresh = RefreshToken(attrs["refresh"])

        session_jti = refresh.get("session_jti")
        if session_jti:
            session = AuthToken.objects.filter(jti=session_jti).first()
            if session is None:
                # L1 gap 19 -- someone still holds a refresh token for a session that was ended
                # (logout / password reset / "sign out everywhere"): worth knowing, e.g. a stolen token.
                audit.record(
                    audit.Event.TOKEN_REFRESH_DENIED, self.context.get("request"), outcome=audit.BLOCKED,
                    user=refresh.get("user_id"), reason="session_ended",
                )
                raise TokenError("Your session has ended. Please log in again.")
            if session.is_expired():
                owner_id = session.user_id
                session.delete()
                audit.record(
                    audit.Event.TOKEN_REFRESH_DENIED, self.context.get("request"), outcome=audit.BLOCKED,
                    user=owner_id, reason="session_expired",
                )
                raise TokenError("Your session has expired. Please log in again.")
            # A refresh is real activity -- keep the sliding window alive.
            session.touch()

        return super().validate(attrs)


class SessionTokenRefreshView(TokenRefreshView):
    serializer_class = SessionTokenRefreshSerializer
    throttle_classes = [TokenRefreshThrottle]
