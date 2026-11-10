"""
post/insights_views.py - the two API endpoints in front of post/insights.py.

    GET /post/insights/?days=7            creator overview: all MY live posts together + top posts
    GET /post/<post_id>/insights/?days=7  insights of ONE of my posts

`days` is clamped to 1..90 by insights.clamp_days (a bad value falls back to 7, it is never a 400).
Only the owner can read a post's insights: anybody else - including staff - gets the same 404 as
for a post that does not exist, so the endpoint never confirms that someone else's post id is real.
Every number comes from post/insights.py (the creator's own activity is already excluded there).
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import insights
from .models import Post


class CreatorInsightsThrottle(UserRateThrottle):
    """Own scope + own rate (defined here, so no settings.py edit is needed). The queries behind
    this are the heaviest read in the app, and a creator screen never needs more than this."""

    scope = "creator_insights"
    rate = "60/min"


_DAYS = OpenApiParameter(name="days", type=int, required=False, description="Window in days, 1..90 (default 7).")


class CreatorInsightsOverviewAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreatorInsightsThrottle]

    @extend_schema(
        summary="Creator insights - all my posts together",
        parameters=[_DAYS],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Creator insights"],
    )
    def get(self, request):
        return Response(insights.creator_overview(request.user, request.query_params.get("days")))


class PostInsightsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreatorInsightsThrottle]

    @extend_schema(
        summary="Creator insights - one of my posts",
        parameters=[_DAYS],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Creator insights"],
    )
    def get(self, request, post_id):
        post = Post.objects.filter(pk=post_id, user=request.user, is_deleted=False).first()
        if post is None:
            raise NotFound("Post not found.")
        return Response(insights.post_insights(post, request.query_params.get("days")))
