"""P-B3 -- throttles for the endpoints that are (or must stay) reachable without a login.

Why a new file: DRF's ScopedRateThrottle only works on class-based views that carry `throttle_scope`; the open
function views (`@api_view`) and the unauthenticated webhooks need a throttle that carries its own scope.

Every class below is keyed by user id when the caller is logged in and by client IP otherwise, and FAILS OPEN when the
cache is down (a cache outage must not turn the login screen or a payment webhook into a 429 wall).
Rates live in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] under the same scope name.
"""

import logging

from rest_framework.throttling import SimpleRateThrottle

logger = logging.getLogger(__name__)


class OpenScopeThrottle(SimpleRateThrottle):
    scope = None  # set by each subclass

    def get_cache_key(self, request, view):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            ident = f"u{user.pk}"
        else:
            ident = f"ip{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}

    def allow_request(self, request, view):
        try:
            return super().allow_request(request, view)
        except Exception:  # noqa: BLE001 - cache backend down -> fail open, never a 500/429 wall
            logger.warning("open throttle %s skipped: cache unavailable", self.scope, exc_info=True)
            return True


class PublicReadThrottle(OpenScopeThrottle):
    """Public GET endpoints that cost a DB read (comments, replies, categories, ad config, sound policy)."""

    scope = "public_read"


class FreesoundSearchThrottle(OpenScopeThrottle):
    """GET /post/music/search/ -- every call is a paid/limited request to Freesound with OUR api key."""

    scope = "freesound_search"


class LoginThrottle(OpenScopeThrottle):
    scope = "login"


class SignupThrottle(OpenScopeThrottle):
    scope = "signup"


class GoogleAuthThrottle(OpenScopeThrottle):
    scope = "google_auth"


class UnsubscribeThrottle(OpenScopeThrottle):
    scope = "unsubscribe"


class ChannelWebhookThrottle(OpenScopeThrottle):
    """Generous on purpose: SES/SNS can burst. It only exists so the shared secret cannot be guessed at line speed."""

    scope = "channel_webhook"


class TokenRefreshThrottle(OpenScopeThrottle):
    """POST /login/auth/token/refresh/ -- reachable without an access token by design (that is what it renews).
    Real apps refresh once per access-token lifetime per device, so the rate is roomy; it exists so a stolen or
    guessed refresh token cannot be hammered at line speed."""

    scope = "token_refresh"


class GatewayWebhookThrottle(OpenScopeThrottle):
    """BuyCoinConfirmView (gateway-agnostic webhook). Generous for provider retries; stops signature guessing floods."""

    scope = "gateway_webhook"
