"""GET /tuitionclass/creator-earnings/?days=7|30|90 - see tuitionclass/creator_earnings.py. Own data only."""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from . import creator_earnings


class CreatorEarningsThrottle(UserRateThrottle):
    scope = "creator_earnings"
    rate = "60/min"


class CreatorEarningsView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CreatorEarningsThrottle]

    @extend_schema(
        summary="Creator earnings dashboard - class + referral coins, window vs previous (cached 10 min)",
        parameters=[OpenApiParameter(name="days", type=int, required=False, description="1..90, default 30")],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Creator insights"],
    )
    def get(self, request):
        return Response(creator_earnings.get(request.user, request.query_params.get("days")))
