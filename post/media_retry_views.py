"""
post/media_retry_views.py - POST /post/media/<uuid:media_id>/retry-processing/

The OWNER of a video whose conversion failed (`processing_status == "failed"`) taps "Retry" (in the "could not
process your video" notification, or on the failed state of their own reel). This puts the row back in the queue
with a fresh attempt budget, the same job the beat sweeper uses (`post.tasks.retry_failed_video_processing`).

* Only the post's author. Anybody else gets 404 (never reveals that the media exists).
* Only videos. Anything else -> 400.
* Idempotent: already `pending` / `processing` -> 200 with the current status, nothing is queued twice;
  already `ready` -> 200 (nothing to do).
* Not abusable: a manual retry is allowed at most VIDEO_PROCESSING_MANUAL_RETRY_LIMIT (default 5) times per video.
Response: {"media_id", "processing_status", "queued"}. No migration: counters live in `PostMedia.metadata["pipeline"]`.
"""
import logging

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import PostMedia

logger = logging.getLogger(__name__)


def _manual_retry_limit():
    return max(1, int(getattr(settings, "VIDEO_PROCESSING_MANUAL_RETRY_LIMIT", 5)))


class MediaRetryProcessingAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Retry the processing of my failed video", request=None, responses={200: dict})
    def post(self, request, media_id):
        media = (
            PostMedia.objects.filter(pk=media_id, post__user=request.user, post__is_deleted=False)
            .only("id", "media_type", "processing_status", "metadata")
            .first()
        )
        if media is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if media.media_type != "video":
            return Response({"detail": "Only videos can be re-processed."}, status=status.HTTP_400_BAD_REQUEST)

        current = media.processing_status
        if current != PostMedia.PROCESSING_FAILED:
            # ready / pending / processing: nothing to retry, tell the app where things stand.
            return Response({"media_id": str(media.id), "processing_status": current, "queued": False})

        from . import tasks

        pipe = dict((media.metadata or {}).get("pipeline") or {})
        manual = int(pipe.get("manual_retries") or 0)
        if manual >= _manual_retry_limit():
            return Response(
                {"detail": "Too many retries for this video. Please upload it again.", "processing_status": current},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        if not getattr(settings, "VIDEO_TRANSCODE_ENABLED", True):
            return Response(
                {"detail": "Video processing is switched off right now.", "processing_status": current},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # fresh attempt budget; `notified` cleared so a new final failure can notify again.
        tasks._pipeline_meta_update(media.id, attempts=0, notified=False, manual_retries=manual + 1, last_error="")
        PostMedia.objects.filter(pk=media.id).update(processing_status=PostMedia.PROCESSING_PENDING)
        try:
            tasks.process_video_for_streaming.delay(str(media.id), True)
        except Exception:
            logger.exception("retry-processing: could not enqueue PostMedia %s", media.id)
            PostMedia.objects.filter(pk=media.id).update(processing_status=PostMedia.PROCESSING_FAILED)
            return Response(
                {"detail": "Could not start processing. Try again in a minute.", "processing_status": PostMedia.PROCESSING_FAILED},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"media_id": str(media.id), "processing_status": PostMedia.PROCESSING_PENDING, "queued": True})
