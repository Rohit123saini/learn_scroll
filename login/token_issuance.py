# login/token_issuance.py
"""
Single place that mints a refresh+access token pair AND creates the
matching AuthToken row (see models.py) that backs sliding-expiry
sessions.

Before this file existed, `RefreshToken.for_user(user)` was called
directly in four places in views.py (Login, Signup, GoogleAuthView,
VerifyOTPView's OTP-login branch) — each hand-building the same
`{"refresh": ..., "access": ...}` dict. Sliding-expiry needs every one
of those call sites to ALSO create an AuthToken row and stamp the same
`session_jti` claim onto the access token, so duplicating that logic
four times over would be exactly the kind of drift this codebase's own
comments elsewhere warn about (see tuitionclass/exceptions.py's docstring)
— one call site forgetting the AuthToken row would silently produce a
token that SlidingSessionAuthentication treats as "no session claim,
skip the check" (harmless — falls back to the JWT's own fixed exp) but
still means that login path never benefits from sliding expiry. Route
every token mint through here instead.
"""
from rest_framework_simplejwt.tokens import RefreshToken

from django.utils import timezone

from .models import AuthToken


def issue_tokens_for_user(user) -> dict:
    """Mint a refresh+access pair for `user` and create the AuthToken
    session row that lets SlidingSessionAuthentication keep it alive
    while active. Returns {"refresh": str, "access": str} — same shape
    every call site already returned in its response body."""

    refresh = RefreshToken.for_user(user)

    session_jti = str(refresh["jti"])
    # Set on the REFRESH token's own payload — not just the access
    # token — and BEFORE deriving `.access_token` below. This matters
    # more than it looks: this app's existing frontend (auth_service.
    # dart's getValidToken()) proactively calls the stock
    # /auth/token/refresh/ endpoint (SimpleJWT's TokenRefreshView) to
    # mint a fresh access token from the refresh token shortly before
    # the old access token's 1-day exp — completely outside any view
    # this file touches. SimpleJWT's `RefreshToken.access_token`
    # property copies every custom claim already present on the refresh
    # token's payload onto each new access token it derives (it's
    # stateless — the claim lives inside the signed refresh token
    # itself, so it survives any number of future /refresh/ calls over
    # that token's 30-day life). Setting it only on the initial access
    # token (as an earlier version of this function did) would mean the
    # VERY FIRST silent client-side refresh produces an access token
    # with no `session_jti` at all — SlidingSessionAuthentication would
    # then treat it as a legacy/session-less token and skip the
    # inactivity check entirely, silently defeating this whole feature
    # for any user whose app stays open long enough to refresh once.
    refresh["session_jti"] = session_jti
    access = refresh.access_token

    AuthToken.objects.create(
        user=user,
        jti=session_jti,
        last_used_at=timezone.now(),
    )

    return {
        "refresh": str(refresh),
        "access": str(access),
    }