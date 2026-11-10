"""
tuitionclass/standalone_live_views.py  -  X12 L-C: endpoints of the standalone "Go Live" (see standalone_live.py)

    GET    live/go/policy/              what the go-live sheet may offer me (audiences, limits, minor restriction)
    POST   live/go/                     create a live (now / scheduled)   multipart or JSON, optional `thumbnail`
    GET    live/upcoming/               live now + scheduled lives I may see (feed cards)
    POST   live/<session_id>/remind/    "Remind me"            DELETE = remove the reminder
    POST   live/<session_id>/cancel/    host cancels a scheduled live

Errors are {"detail": "...", "code": "..."} (+ extras, e.g. already_live -> session_id) with a matching status.
After POST live/go/ the app opens the normal live room (green room -> POST sessions/<id>/join/) - nothing else.
"""
import logging

from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import standalone_live as sl
from .models import ClassSession
from .serializers import ClassSessionSerializer

logger = logging.getLogger(__name__)


def _error(exc: sl.StandaloneLiveError) -> Response:
    return Response(exc.payload(), status=exc.status)


class StandaloneLivePolicyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(sl.policy_for(request.user))


class StandaloneLiveCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        data = request.data
        try:
            session = sl.create_live(
                request.user,
                title=data.get("title"),
                topic=data.get("topic", ""),
                audience=data.get("audience"),
                start_at=data.get("start_at"),
                tz_name=data.get("timezone"),
                duration_minutes=data.get("duration_minutes"),
                thumbnail=request.FILES.get("thumbnail") if hasattr(request, "FILES") else None,
            )
        except sl.StandaloneLiveError as exc:
            return _error(exc)
        body = ClassSessionSerializer(session, context={"request": request}).data
        return Response({"session": body, "go_now": session.live_go_now}, status=http.HTTP_201_CREATED)


class StandaloneLiveUpcomingView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 20) or 20)
        except (TypeError, ValueError):
            limit = 20
        return Response({"results": sl.upcoming_for(request.user, limit=limit)})


class _SessionAction(APIView):
    permission_classes = [IsAuthenticated]

    def _session(self, session_id):
        return get_object_or_404(
            ClassSession.objects.select_related("classroom", "classroom__teacher"),
            pk=session_id, live_standalone=True,
        )


class StandaloneLiveRemindView(_SessionAction):
    def post(self, request, session_id):
        try:
            on = sl.set_reminder(self._session(session_id), request.user, True)
        except sl.StandaloneLiveError as exc:
            return _error(exc)
        return Response({"reminded": on})

    def delete(self, request, session_id):
        try:
            on = sl.set_reminder(self._session(session_id), request.user, False)
        except sl.StandaloneLiveError as exc:
            return _error(exc)
        return Response({"reminded": on})


class StandaloneLiveCancelView(_SessionAction):
    def post(self, request, session_id):
        try:
            session = sl.cancel_live(self._session(session_id), request.user)
        except sl.StandaloneLiveError as exc:
            return _error(exc)
        return Response({"status": session.status})
