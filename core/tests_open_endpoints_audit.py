"""P-B3 -- lock the audit of open (no-login) endpoints.

Rule enforced: every view reachable without a login (AllowAny or an empty permission_classes) must set its OWN
`throttle_classes` (a scoped / webhook / ops throttle) -- it must not silently rely on the 20/min anonymous default,
which is both too tight for real traffic and unrelated to the abuse risk of that endpoint.
A new open view that forgets this fails here; a genuinely exempt one is added to EXEMPT with a reason.
Docs: P-B3_OPEN_ENDPOINTS_AUDIT.md
"""
from django.test import SimpleTestCase
from django.urls import get_resolver
from rest_framework.permissions import AllowAny
from rest_framework.settings import api_settings

# class path -> why it may keep the default throttle
EXEMPT = {
    "tuitionclass.views.dashboard.HealthCheckView": "load-balancer health probe",
}


def _walk(patterns):
    for p in patterns:
        sub = getattr(p, "url_patterns", None)
        if sub is not None:
            yield from _walk(sub)
        else:
            yield p


def _open_view_classes():
    seen = {}
    for p in _walk(get_resolver().url_patterns):
        cb = p.callback
        cls = getattr(cb, "cls", None)
        if cls is None or getattr(cb, "actions", None):  # plain APIView / api_view only (ViewSet actions: see the audit doc)
            continue
        perms = list(getattr(cls, "permission_classes", []) or [])
        is_open = (not perms) or any(isinstance(x, type) and issubclass(x, AllowAny) for x in perms)
        if is_open:
            seen[f"{cb.__module__}.{cb.__name__}" if cls.__name__ == "WrappedAPIView" else f"{cls.__module__}.{cls.__name__}"] = cls
    return seen


class OpenEndpointsThrottleLockTests(SimpleTestCase):
    def test_every_open_view_has_its_own_throttle(self):
        default = list(api_settings.DEFAULT_THROTTLE_CLASSES)
        missing = sorted(
            name for name, cls in _open_view_classes().items()
            if name not in EXEMPT and list(getattr(cls, "throttle_classes", default)) == default
        )
        self.assertEqual(missing, [], "open views relying on the default anon throttle: %s" % missing)

    def test_new_scopes_have_rates(self):
        from django.conf import settings

        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        for scope in ("login", "signup", "google_auth", "public_read", "freesound_search",
                      "unsubscribe", "channel_webhook", "gateway_webhook", "token_refresh"):
            self.assertIn(scope, rates)

    def test_open_throttle_scopes_match_classes(self):
        from common import open_throttles as ot

        for cls in (ot.PublicReadThrottle, ot.FreesoundSearchThrottle, ot.LoginThrottle, ot.SignupThrottle,
                    ot.GoogleAuthThrottle, ot.UnsubscribeThrottle, ot.ChannelWebhookThrottle, ot.GatewayWebhookThrottle,
                    ot.TokenRefreshThrottle):
            self.assertTrue(cls.scope)
