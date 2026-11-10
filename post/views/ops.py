# post/views/ops.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from ..feed_resilience import FeedOpsThrottle
from django.http import HttpResponse
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import permission_classes
from django.conf import settings
from rest_framework import status
from django.utils import timezone


# ===================== FEED METRICS (T1 Part 5) =====================
class FeedOpsAPIView(APIView):
    """H10: `GET /post/feed/ops/?minutes=15` -> availability / error + degraded rate / p95 / alert events of the home
    feed vs the SLO (post/feed_ops.py). `?format=prom` -> Prometheus text for Grafana (deploy/monitoring/).

    Access: staff users (JWT) OR `Authorization: Bearer <settings.FEED_OPS_SCRAPE_TOKEN>` (for the Prometheus
    scraper; disabled while the token is empty). Numbers only - no user data."""
    permission_classes = [AllowAny]
    authentication_classes = []  # a scrape token is not a user JWT; staff users pass their JWT in the header below
    throttle_classes = [FeedOpsThrottle]  # scope `feed_ops`; fails open if the cache is down

    @staticmethod
    def _allowed(request):
        import hmac

        user = getattr(request, 'user', None)  # already authenticated (e.g. forced in tests / future auth backends)
        if user is not None and getattr(user, 'is_authenticated', False) and (user.is_staff or user.is_superuser):
            return True
        header = request.META.get('HTTP_AUTHORIZATION', '')
        token = getattr(settings, 'FEED_OPS_SCRAPE_TOKEN', '') or ''
        if token and header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip(), token):
            return True
        if header.startswith('Bearer '):  # a staff user's normal JWT
            try:
                from login.authentication import SlidingSessionAuthentication  # the project's JWT auth

                auth = SlidingSessionAuthentication().authenticate(request)
                return bool(auth and (auth[0].is_staff or auth[0].is_superuser))
            except Exception:
                return False
        return False

    @extend_schema(summary="Home feed SLO numbers (staff / scrape token)", tags=["Feed"])
    def get(self, request):
        from .. import feed_ops

        if not self._allowed(request):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        if request.query_params.get("format") == "prom":
            return HttpResponse(feed_ops.prometheus_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
        try:
            minutes = min(1440, max(1, int(request.query_params.get("minutes", 15))))
        except (TypeError, ValueError):
            minutes = 15
        summary = feed_ops.summarize_last(minutes)
        summary["breaches"] = feed_ops.breaches(summary)
        return Response({"success": True, "data": summary})


class FeedMetricsAPIView(APIView):
    """`GET /post/feed/metrics/?days=7&surface=feed` - staff only. Same numbers
    as `manage.py feed_metrics` (CTR, dwell, session length, show-fewer rate,
    diversity - overall and per A/B variant)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Feed quality metrics (staff)", tags=["Feed"])
    def get(self, request):
        from datetime import timedelta as _td
        from .. import feed_metrics

        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        try:
            days = min(90, max(1, int(request.query_params.get("days", 1))))
        except (TypeError, ValueError):
            days = 1
        surface = request.query_params.get("surface", "feed")
        if surface not in ("feed", "reels", "profile", "explore"):
            return Response({"success": False, "message": "Invalid surface."}, status=status.HTTP_400_BAD_REQUEST)
        end = timezone.now()
        return Response({"success": True, "data": feed_metrics.compute_metrics(end - _td(days=days), end, surface)})
