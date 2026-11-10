"""
post/upload_views.py  (H20)

HTTP layer of the UploadQueue + Drafts. All rules live in upload_sessions.py / drafts.py; these views only parse the
request and turn UploadError / DraftError into the stable JSON the app maps to Hindi / English text:

    {"success": false, "code": "<stable_code>", "message": "<English, for logs>", ...extra}

Routes (post/urls.py):
    POST   /post/uploads/                        init
    GET    /post/uploads/<uuid>/                 status (resume)
    DELETE /post/uploads/<uuid>/                 cancel (Discard)
    POST   /post/uploads/<uuid>/chunk/           one chunk  (multipart: file_index, chunk_index, chunk_hash, chunk)
    POST   /post/uploads/<uuid>/complete/        assemble + create  (JSON body optional: {"payload": {...}} for "Edit")
    GET    /post/drafts/                         list (?since=<iso> also returns deleted ones)
    GET|PUT|DELETE /post/drafts/<uuid>/          one draft

Written by reading the code only - nothing was executed.
"""
import logging

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema
from rest_framework import parsers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import drafts as draft_service
from . import upload_sessions as sessions
from .drafts import DraftError
from .upload_sessions import UploadError

logger = logging.getLogger(__name__)


def _fail(exc):
    body = {"success": False, "code": exc.code, "message": exc.message}
    body.update(getattr(exc, "extra", None) or {})
    response = Response(body, status=exc.http_status)
    if exc.code == "feature_disabled" and body.get("feature"):
        response["X-Feature-Disabled"] = str(body["feature"])  # P-J2: same header as common/feature_gate.py
    return response


def _body_dict(request):
    data = request.data
    return data if isinstance(data, dict) else {}


def _json_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ upload sessions
class UploadSessionCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser]

    @extend_schema(summary="Start a resumable upload (post / reel / story / resource)", tags=["Uploads"])
    def post(self, request):
        try:
            session, created = sessions.init_session(request.user, _body_dict(request), platform=request.headers.get("X-Platform", ""))
        except UploadError as exc:
            return _fail(exc)
        body = sessions.session_payload(session)
        body["success"] = True
        body["created"] = created
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UploadSessionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Upload status: which chunks the server already has", tags=["Uploads"])
    def get(self, request, session_id):
        try:
            session = sessions.get_session(request.user, session_id)
        except UploadError as exc:
            return _fail(exc)
        body = sessions.session_payload(session)
        body["success"] = True
        return Response(body)

    @extend_schema(summary="Cancel an upload (Discard)", tags=["Uploads"])
    def delete(self, request, session_id):
        sessions.cancel_session(request.user, session_id)  # an unknown / finished session is a success too
        return Response({"success": True}, status=status.HTTP_200_OK)


class UploadChunkAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Upload one chunk of one file", tags=["Uploads"])
    def post(self, request, session_id):
        file_index = _json_int(request.data.get("file_index", 0))
        chunk_index = _json_int(request.data.get("chunk_index"))
        if file_index is None or chunk_index is None:
            return Response({"success": False, "code": "chunk_index_invalid",
                             "message": "file_index and chunk_index must be numbers."},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            received = sessions.save_chunk(
                request.user, session_id, file_index, chunk_index,
                request.FILES.get("chunk"), request.data.get("chunk_hash"))
        except UploadError as exc:
            return _fail(exc)
        return Response({"success": True, "file_index": file_index, "chunk_index": chunk_index,
                         "received_count": received})


class UploadCompleteAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser]

    @extend_schema(summary="Finish an upload: assemble the files and create the post / story / resource", tags=["Uploads"])
    def post(self, request, session_id):
        body = _body_dict(request)
        override = body.get("payload") if "payload" in body else None
        try:
            code, result, replayed = sessions.complete_session(
                request.user, session_id, request, payload_override=override)
        except UploadError as exc:
            return _fail(exc)
        response = Response(result, status=code)
        if replayed:
            response["Idempotent-Replay"] = "true"
        return response


# ------------------------------------------------------------------ drafts
class DraftListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My drafts (synced across devices)", tags=["Drafts"])
    def get(self, request):
        since = None
        raw = request.query_params.get("since")
        if raw:
            since = parse_datetime(raw)
            if since is None:
                return Response({"success": False, "code": "since_invalid",
                                 "message": "since must be an ISO date-time."},
                                status=status.HTTP_400_BAD_REQUEST)
        items = draft_service.list_drafts(request.user, since=since)
        return Response({"success": True, "results": [draft_service.draft_payload(d) for d in items]})


class DraftDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser]

    @extend_schema(summary="One draft", tags=["Drafts"])
    def get(self, request, client_id):
        try:
            draft = draft_service.get_draft(request.user, client_id)
        except DraftError as exc:
            return _fail(exc)
        body = draft_service.draft_payload(draft)
        body["success"] = True
        return Response(body)

    @extend_schema(summary="Create or update a draft (base_version = optimistic lock)", tags=["Drafts"])
    def put(self, request, client_id):
        try:
            draft, created = draft_service.save_draft(request.user, client_id, _body_dict(request))
        except DraftError as exc:
            return _fail(exc)
        body = draft_service.draft_payload(draft)
        body["success"] = True
        return Response(body, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(summary="Delete a draft (soft: other devices see the tombstone)", tags=["Drafts"])
    def delete(self, request, client_id):
        draft_service.delete_draft(request.user, client_id)  # already gone = success
        return Response({"success": True}, status=status.HTTP_200_OK)
