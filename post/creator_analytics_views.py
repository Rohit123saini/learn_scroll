"""GET /post/creator-analytics/?days=7|30|90 - see post/creator_analytics.py. Own data only, 10 min cache."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import creator_analytics
from .insights_views import CreatorInsightsThrottle


class CreatorAnalyticsAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreatorInsightsThrottle]

    @extend_schema(
        summary="Creator analytics - best time, watch time, followers, top posts (cached 10 min)",
        parameters=[OpenApiParameter(name="days", type=int, required=False, description="1..90, default 7")],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Creator insights"],
    )
    def get(self, request):
        return Response(creator_analytics.get(request.user, request.query_params.get("days")))
