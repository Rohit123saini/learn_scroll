"""
post/reading_progress.py  (H14 - "Continue learning": resume a Notes / PDF where the student stopped)

  PUT /post/attachments/<id>/progress/   {page, page_count?}  - the in-app PDF viewer reports the page when the
                                                               user pauses / leaves it (debounced on the client)
  GET /post/attachments/<id>/progress/                         - the saved page of THIS attachment (resume on
                                                               another device / after reinstall)
  GET /post/reading/continue/                                  - the last PDF I was reading, not finished yet
                                                               (what the Home "Continue learning" strip shows)

Rules
  * PDFs only (the one kind with pages). The post must be visible to the caller (same gate as the resource list:
    `resource_views._visible_resources` = approved, not deleted / archived / scheduled, audience rules).
  * `page` is 1-based and clamped to >= 1; `page_count` (optional) is clamped to >= `page`. A page number the
    server cannot prove wrong is stored as sent - the viewer is the only one who knows the real page count.
  * "Finished" = last_page >= page_count: a finished PDF is not offered as "continue" (the row stays, so reopening
    it still resumes on the last page).
  * An old row (> RESUME_MAX_AGE_DAYS) is not offered either: "continue" must mean "what you did recently".
  * The write is one UPSERT per call and the client sends at most one per ~5 s (see the Flutter ReadingProgressStore),
    plus a per-user throttle here, so it cannot become a write storm.
Every error is `{success: False, code, message}` like the rest of the resource API.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import PostAttachment, ReadingProgress
from .resource_views import _err, _visible_resources
from .serializers import PostAttachmentSerializer

RESUME_MAX_AGE_DAYS = 45
MAX_PAGE = 100_000  # sanity ceiling: a bigger number is a buggy client, not a real PDF


class ReadingProgressThrottle(UserRateThrottle):
    scope = "reading_progress"
    rate = "120/min"


def _int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _attachment_for(user, attachment_id):
    """The PDF attachment if the caller may see its post, else None (same 404 for 'missing' and 'not yours to see')."""
    att = PostAttachment.objects.select_related("post").filter(pk=attachment_id, kind="pdf").first()
    if att is None:
        return None
    if not _visible_resources(user).filter(pk=att.post_id).exists():
        return None
    return att


def progress_fraction(last_page, page_count):
    """0..1; 0 when the page count is unknown."""
    if not page_count:
        return 0.0
    return round(min(1.0, max(0.0, last_page / float(page_count))), 3)


def is_finished(last_page, page_count):
    return bool(page_count) and last_page >= page_count


def continue_item(user, request=None):
    """The one 'continue reading' item for `user` (dict) or None. Also used by core/continue_learning.py."""
    cutoff = timezone.now() - timedelta(days=RESUME_MAX_AGE_DAYS)
    rows = list(
        ReadingProgress.objects.filter(user=user, last_opened_at__gte=cutoff)
        .select_related("post", "attachment")
        .order_by("-last_opened_at")[:10]
    )
    if not rows:
        return None
    visible = set(_visible_resources(user).filter(pk__in=[r.post_id for r in rows]).values_list("pk", flat=True))
    for r in rows:
        if r.post_id not in visible or is_finished(r.last_page, r.page_count or r.attachment.pages):
            continue
        count = r.page_count or r.attachment.pages
        return {
            "post_id": str(r.post_id),
            "attachment_id": str(r.attachment_id),
            "title": r.post.title or r.attachment.original_name,
            "subject": r.post.subject or "",
            "page": r.last_page,
            "page_count": count,
            "progress": progress_fraction(r.last_page, count),
            "last_opened_at": r.last_opened_at.isoformat(),
            # signed, expiring viewer link + thumbnail - minted here because the permission check above passed
            "attachment": PostAttachmentSerializer(r.attachment, context={"request": request}).data,
        }
    return None


class AttachmentProgressAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ReadingProgressThrottle]

    def get(self, request, attachment_id):
        att = _attachment_for(request.user, attachment_id)
        if att is None:
            return _err("not_found", "File not found.", status.HTTP_404_NOT_FOUND)
        row = ReadingProgress.objects.filter(user=request.user, attachment=att).first()
        if row is None:
            return Response({"success": True, "data": None})
        return Response({"success": True, "data": {
            "page": row.last_page, "page_count": row.page_count or att.pages,
            "last_opened_at": row.last_opened_at.isoformat(),
        }})

    @transaction.atomic
    def put(self, request, attachment_id):
        att = _attachment_for(request.user, attachment_id)
        if att is None:
            return _err("not_found", "File not found.", status.HTTP_404_NOT_FOUND)
        page = _int(request.data.get("page"))
        if page is None or page < 1 or page > MAX_PAGE:
            return _err("invalid_page", "page must be a number between 1 and %d." % MAX_PAGE)
        count = _int(request.data.get("page_count"))
        if count is not None:
            count = min(MAX_PAGE, max(count, page)) if count > 0 else None
        count = count or att.pages  # the server's own count (set at upload) when the client did not send one
        row, created = ReadingProgress.objects.get_or_create(
            user=request.user, attachment=att,
            defaults={"post_id": att.post_id, "last_page": page, "page_count": count},
        )
        if not created:
            row.last_page = page
            row.page_count = count or row.page_count
            row.last_opened_at = timezone.now()
            row.save(update_fields=["last_page", "page_count", "last_opened_at"])
        return Response({"success": True, "data": {
            "page": row.last_page, "page_count": row.page_count,
            "finished": is_finished(row.last_page, row.page_count),
        }})


class ContinueReadingAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ReadingProgressThrottle]

    def get(self, request):
        return Response({"success": True, "data": continue_item(request.user, request)})
