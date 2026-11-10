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

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .device_info import extract_device_info
from .models import AuthToken


def _enforce_session_cap(user, keep_jti) -> None:
    """L1 gap 11 — keep at most N live sessions per user (least recently used go first)."""
    cap = int(getattr(settings, "AUTH_MAX_SESSIONS_PER_USER", AuthToken.MAX_SESSIONS_PER_USER))
    if cap < 1:
        return
    surplus = list(
        AuthToken.objects.filter(user=user)
        .order_by("-last_used_at", "-id")
        .values_list("id", flat=True)[cap:]
    )
    if surplus:
        AuthToken.objects.filter(id__in=surplus).exclude(jti=keep_jti).delete()


def issue_tokens_for_user(user, request=None) -> dict:
    """Mint a refresh+access pair for `user` and create the AuthToken
    session row that lets SlidingSessionAuthentication keep it alive
    while active. Returns {"refresh": str, "access": str} — same shape
    every call site already returned in its response body.

    `request` (optional) is the login/signup request: its X-Device-* headers,
    User-Agent and IP are stored on the session row so the device list can show
    them (L1 gap 11). Without it the row is created with blank device fields."""

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

    info = extract_device_info(request) if request is not None else {}
    with transaction.atomic():
        # Signing in again on the SAME device replaces that device's old session
        # (the app overwrote its stored refresh token anyway). Scoped to this
        # user, so a client-supplied device_id can never touch anyone else's rows.
        if info.get("device_id"):
            AuthToken.objects.filter(user=user, device_id=info["device_id"]).delete()
        AuthToken.objects.create(
            user=user,
            jti=session_jti,
            last_used_at=timezone.now(),
            **info,
        )
        _enforce_session_cap(user, keep_jti=session_jti)

    return {
        "refresh": str(refresh),
        "access": str(access),
    }


def revoke_user_sessions(user, keep_jti=None) -> int:
    """End this user's sessions server-side; returns how many were ended.

    Deleting an ``AuthToken`` row is what kills a session: the access token
    carries ``session_jti``, so ``SlidingSessionAuthentication`` answers
    TOKEN_EXPIRED the moment its row is gone, and ``SessionTokenRefreshView``
    (login/token_views.py) refuses to mint a new access token from the matching
    refresh token. Used by password reset (all sessions) and password change
    (every session except the one making the change, via ``keep_jti``).

    Tokens that pre-date sliding sessions have no ``session_jti`` / row and
    cannot be revoked this way; they age out on their own (short access
    lifetime / 30 day refresh).
    """
    qs = AuthToken.objects.filter(user=user)
    if keep_jti:
        qs = qs.exclude(jti=keep_jti)
    deleted, _ = qs.delete()
    return deleted
