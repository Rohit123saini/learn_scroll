# testseries/throttling.py
"""
Rate limits for the two UNAUTHENTICATED surfaces of this app. Same reasoning
as `assigments/throttling.py`: the slug / certificate code is unguessable
(entropy), but entropy does not stop a bot hammering the endpoint with volume.

Each scope needs a rate in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
(see settings.py -> "TESTSERIES — ADVANCED CONFIG"). A scope WITHOUT a
configured rate makes DRF raise ImproperlyConfigured on the first request —
which is exactly how `assigments_public_page` was silently broken before.
"""
from rest_framework.throttling import AnonRateThrottle


class TestSeriesPublicPageThrottle(AnonRateThrottle):
    scope = "testseries_public_page"
    __test__ = False


class CertificateVerifyThrottle(AnonRateThrottle):
    scope = "testseries_certificate_verify"
    __test__ = False
