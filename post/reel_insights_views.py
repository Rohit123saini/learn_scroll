"""
post/reel_insights_views.py - `GET /post/<post_id>/reel-insights/?days=7` (owner only, video posts only).

Same 404 for "not yours" / "does not exist" / "not a video" (never confirms someone else's post id), same throttle and
`days` clamp as the generic post insights (post/insights_views.py). A scheduled / archived reel still has insights.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reel_insights
from .insights_views import _DAYS, CreatorInsightsThrottle
from .models import Post


class ReelInsightsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreatorInsightsThrottle]

    @extend_schema(
        summary="Reel insights - views, watch time, retention, shares, follows gained",
        parameters=[_DAYS], responses={200: OpenApiTypes.OBJECT}, tags=["Creator insights"],
    )
    def get(self, request, post_id):
        post = (
            Post.objects.filter(pk=post_id, user=request.user, is_deleted=False, post_type="video")
            .prefetch_related("media").first()
        )
        if post is None:
            raise NotFound("Reel not found.")
        return Response(reel_insights.reel_insights(post, request.query_params.get("days")))
