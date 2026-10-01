# assigments/throttling.py
"""
[HARDENING] — not in the functional design doc, added per
PRODUCTION_DESIGN.md §1.1/§6. The public share page (`PublicSubmissionView`)
is the one `AllowAny`, unauthenticated surface in this entire app — slug
entropy (`secrets.token_urlsafe(24)`, 192 bits) makes guessing any *one*
specific slug infeasible, but says nothing about a bot simply hammering
the endpoint with volume to find *some* valid slug, or scraping every
slug it's been handed at high speed. Rate limiting is the actual control
for that; entropy and rate limiting solve different problems and this
endpoint needs both.

A dedicated scope (`assigments_public_page`) rather than reusing DRF's
built-in `anon` scope, so tuning this rate in settings can never
accidentally change the limit on unrelated public endpoints elsewhere in
the project.
"""
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class assigmentsPublicPageThrottle(AnonRateThrottle):
    scope = "assigments_public_page"


class assigmentsExploreThrottle(UserRateThrottle):
    """Explore lists every public assignment — a scraping target. Rate lives in
    DEFAULT_THROTTLE_RATES["assigments_explore"]."""

    scope = "assigments_explore"
