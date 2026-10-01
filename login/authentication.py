# login/authentication.py
"""
Sliding-expiry ("stay signed in while active") layer on top of
rest_framework_simplejwt's stateless JWTAuthentication.

WHY THIS EXISTS:
SimpleJWT's access/refresh tokens are stateless — their lifetime is
whatever's baked into SIMPLE_JWT["ACCESS_TOKEN_LIFETIME"] /
["REFRESH_TOKEN_LIFETIME"] (settings.py: 1 day / 30 days) at the moment
they're minted. There's no way to change "how long until this SPECIFIC
token dies" afterwards, and no notion of "the user is still active, so
keep it alive" — a refresh token issued today is dead in exactly 30
days whether the user opened the app yesterday or never again.

Product requirement: a session should only die after AuthToken.
SLIDING_EXPIRY (10 days) of TRUE inactivity — any authenticated request
inside that window pushes expiry back out again, indefinitely, with no
separate "refresh" call required from the client. That needs one piece
of server-side state per session (AuthToken.last_used_at, in
models.py) — stateless JWTs alone can't express it.

HOW IT WORKS:
1. login.token_issuance.issue_tokens_for_user() (used by every
   login/signup/Google-auth/OTP-login path) copies the refresh token's
   own `jti` claim onto the access token as a `session_jti` claim, and
   creates one AuthToken row (jti=that value, last_used_at=now).
2. SlidingSessionAuthentication.authenticate() runs the normal
   JWTAuthentication first — so a bad signature, malformed header, or a
   token past its own JWT `exp` is still rejected exactly as before;
   this only adds a check ON TOP, it never loosens what already existed.
3. It then reads `session_jti` off the validated access token:
     - missing entirely (a token minted before this feature shipped, or
       any token deliberately issued without a session) -> nothing to
       slide-check -> request proceeds, still governed only by the
       JWT's own fixed exp. This is a deliberate soft-landing: it means
       shipping this doesn't instantly log out every already-signed-in
       user the moment it deploys — old tokens just age out normally
       within their original 1-day/30-day window instead.
     - present but no matching AuthToken row (expired-and-deleted
       already, or removed out-of-band, e.g. an admin/support "log this
       device out" action) -> 401 TOKEN_EXPIRED.
     - present, row found, `is_expired()` true (>10 days since
       last_used_at) -> delete the row (it's dead; no reason to keep
       scanning it later) -> 401 TOKEN_EXPIRED.
     - present, row found, not expired -> `touch()` (last_used_at =
       now — the silent renew) -> request proceeds normally.
"""
from rest_framework import exceptions
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import AuthToken


class TokenExpiredError(exceptions.AuthenticationFailed):
    """A distinct subclass — not just a plain AuthenticationFailed with a
    custom message — so:

    1. The client can tell "your session timed out from inactivity, log
       in again" apart from every other 401 (bad/missing/malformed
       token), which matters here specifically because the frontend is
       meant to show a 3-second splash before redirecting for THIS case
       only, not for a generic auth failure.
    2. tuitionclass/exceptions.py's global error-envelope handler (the
       project-wide EXCEPTION_HANDLER, despite the module name) can
       stamp a stable "TOKEN_EXPIRED" `code` onto the JSON body instead
       of the generic "authentication_failed" every other
       AuthenticationFailed gets — see that file's _CODE_BY_EXC.
    """

    default_detail = "Your session has expired. Please log in again."
    default_code = "token_expired"


class SlidingSessionAuthentication(JWTAuthentication):
    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            # No credentials supplied at all (no/blank Authorization
            # header) — let the permission layer (IsAuthenticated)
            # produce the usual 401, same as plain JWTAuthentication.
            return None

        user, validated_token = result

        session_jti = validated_token.get("session_jti")
        if not session_jti:
            # Legacy / session-less token — see module docstring point 3.
            return result

        try:
            session = AuthToken.objects.get(jti=session_jti)
        except AuthToken.DoesNotExist:
            raise TokenExpiredError()

        if session.is_expired():
            session.delete()
            raise TokenExpiredError()

        session.touch()
        return user, validated_token