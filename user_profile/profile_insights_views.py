"""user_profile/profile_insights_views.py - P9. NOT RUN.   GET /profile/insights/?days=7|30|90   (own profile only)

There is no `username` in the URL on purpose: nobody can ask for another person's insights. Same throttle as the post
insights screen (post.insights_views.CreatorInsightsThrottle, 60/min)."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from post.insights_views import CreatorInsightsThrottle

from . import profile_insights


class ProfileInsightsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [CreatorInsightsThrottle]

    @extend_schema(
        summary="Profile insights - visits, follower growth, reach, top posts (my own profile)",
        parameters=[OpenApiParameter(name="days", type=int, required=False, description="Window in days, 1..90 (default 7).")],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Creator insights"],
    )
    def get(self, request):
        return Response(profile_insights.build(request.user, request.query_params.get("days")))
