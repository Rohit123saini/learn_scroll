# post/views/feed_ads.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import permission_classes
from django.conf import settings
from common.open_throttles import PublicReadThrottle  # P-B3


# ===================== FEED AD/INTERSTITIAL CONFIG =====================
# NEW — SujhaavFayda1 item 3. kAdEveryPosts / kInterstitialEveryPosts used
# to be hardcoded Dart consts — changing the cadence meant an app release.
# The client now fetches this once per session and falls back to its own
# hardcoded defaults if the call fails.
#
# Reads from settings for now (override per-environment via env vars,
# still needs a process restart to change — NOT true hot A/B testing). For
# real without-a-release A/B testing, swap the body of get() for a lookup
# against an admin-editable model, django-waffle, or whatever feature-flag
# service this project standardizes on — no such model was in the files
# reviewed for this pass, so it wasn't guessed at here.
def build_feed_ad_config(user=None) -> dict:
    """Payload of `GET /post/feed/ad-config/` (also embedded in `GET /core/home-bootstrap/`).

    Minors see no ads. The public endpoint can only know the viewer when the app sends the token; the app ALSO
    hides ads from /core/me/context/ (`ads_enabled`), so either is enough. 0 = "no ad slots" (the app treats
    <= 0 as off)."""
    ad_every = getattr(settings, "FEED_AD_EVERY_POSTS", 5)
    if user is not None and user.is_authenticated and user.is_minor and getattr(settings, "ADS_OFF_FOR_MINORS", True):
        ad_every = 0
    return {
        "ad_every_posts": ad_every,
        "interstitial_every_posts": getattr(settings, "FEED_INTERSTITIAL_EVERY_POSTS", 18),
    }


class FeedAdConfigAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicReadThrottle]  # P-B3

    @extend_schema(
        summary="Feed ad/interstitial cadence config",
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def get(self, request):
        return Response(build_feed_ad_config(getattr(request, "user", None)))
