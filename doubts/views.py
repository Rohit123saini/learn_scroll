"""
doubts/views.py — the unified Doubts hub API (T3.4 / D8).

    GET  /doubt-hub/                       merged list   ?source=&status=&role=&q=&parent_id=&limit=&cursor=
    GET  /doubt-hub/summary/               counts per source/status + "to answer" badge
    GET  /doubt-hub/targets/               places the viewer can ask (Ask picker)
    POST /doubt-hub/ask/                   {source, target_id, [subject_id|session_id], text, is_anonymous, is_public}
    GET  /doubt-hub/<source>:<id>/         one doubt + its thread
    POST /doubt-hub/<source>:<id>/<verb>/  verb = answer|upvote|unupvote|resolve|reopen|reveal|mark_best

Every view only authenticates, validates and delegates to services.py; the
rules that decide who may see/do what stay inside each source app.
"""
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import hub, services
from .throttles import DoubtsHubReadThrottle, DoubtsHubWriteThrottle

_TAG = ["Doubts Hub"]


def _payload(request) -> dict:
    """request.data as a plain {str: value} dict (QueryDict -> .dict(), so
    form posts don't turn every value into a one-item list)."""
    data = request.data
    if hasattr(data, "dict"):
        return data.dict()
    return dict(data) if isinstance(data, dict) else {}


class _HubAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, hub.HubError):
            return Response({"detail": exc.detail, "code": exc.code}, status=exc.status)
        return super().handle_exception(exc)


class DoubtHubListAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubReadThrottle]

    @extend_schema(summary="Unified doubts list (all five doubt systems)", tags=_TAG, responses=OpenApiTypes.OBJECT)
    def get(self, request):
        flt = hub.HubFilter.from_params(request.query_params)
        return Response(services.list_hub(request.user, flt, request))


class DoubtHubSummaryAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubReadThrottle]

    @extend_schema(summary="Doubt counts per source/status + to-answer badge", tags=_TAG, responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(services.get_summary(request.user, request))


class DoubtHubTargetsAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubReadThrottle]

    @extend_schema(summary="Where can I ask a doubt?", tags=_TAG, responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response({"results": services.get_targets(request.user, request)})


class DoubtHubAskAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubWriteThrottle]

    @extend_schema(summary="Ask a doubt in any system", tags=_TAG, request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request):
        body, code = services.ask(request, _payload(request))
        return Response(body, status=code)


class DoubtHubDetailAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubReadThrottle]

    @extend_schema(summary="One doubt with its answer thread", tags=_TAG, responses=OpenApiTypes.OBJECT)
    def get(self, request, hub_id):
        return Response(services.get_detail(request.user, hub_id, request))


class DoubtHubActionAPIView(_HubAPIView):
    throttle_classes = [DoubtsHubWriteThrottle]

    @extend_schema(summary="Answer / upvote / resolve / reopen / reveal / mark_best", tags=_TAG,
                   request=OpenApiTypes.OBJECT, responses=OpenApiTypes.OBJECT)
    def post(self, request, hub_id, action):
        body, code = services.act(request, hub_id, action, _payload(request))
        return Response(body, status=code)
