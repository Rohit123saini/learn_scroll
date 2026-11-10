"""
post/creator_feedback.py  (H15 - "your note helped students" weekly feedback to the author)

Once a week the author of a Notes / Resource post hears what the last 7 days did with their material:

    "Your notes reached students"  /  "12 students read, 3 saved, 5 found it helpful"

Where each number comes from (all distinct students, the author's own activity never counts):
  read     ReadingProgress rows opened in the window      (H14 - the PDF viewer reports a page)
  saved    PostSave rows created in the window            (any collection, incl. 'revision')
  helpful  PostHelpful rows created in the window         (H15 - POST /post/<id>/helpful/)

3 grouped queries for the whole week, no per-post loop. An author with all-zero numbers gets nothing (no empty
"0 students" message). Notification type: core `creator_feedback`; data = {read, saved, helpful, days} so the app
can word / localise it. Idempotent: an author who already got one in the last 6 days is skipped, so a duplicate beat
run does not double-send.

Beat entry (settings.CELERY_BEAT_SCHEDULE): "post-creator-feedback-weekly" -> post.creator_feedback.send_creator_feedback
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Count, F
from django.utils import timezone

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
_SKIP_IF_SENT_WITHIN_DAYS = 6


def week_numbers(since):
    """{author_id: {"read": n, "saved": n, "helpful": n}} for resource posts, activity after `since`."""
    from .models import Post, PostHelpful, PostSave, ReadingProgress

    out = {}

    def _fold(rows, key):
        for r in rows:
            out.setdefault(r["post__user_id"], {"read": 0, "saved": 0, "helpful": 0})[key] = r["n"]

    base = dict(post__kind=Post.KIND_RESOURCE, post__is_deleted=False, post__is_archived=False)
    _fold(ReadingProgress.objects.filter(last_opened_at__gte=since, **base).exclude(user_id=F("post__user_id"))
          .values("post__user_id").annotate(n=Count("user_id", distinct=True)), "read")
    _fold(PostSave.objects.filter(created_at__gte=since, **base).exclude(user_id=F("post__user_id"))
          .values("post__user_id").annotate(n=Count("user_id", distinct=True)), "saved")
    _fold(PostHelpful.objects.filter(created_at__gte=since, **base).exclude(user_id=F("post__user_id"))
          .values("post__user_id").annotate(n=Count("user_id", distinct=True)), "helpful")
    return out


def _message(n):
    parts = []
    if n["read"]:
        parts.append(f"{n['read']} student{'s' if n['read'] != 1 else ''} read")
    if n["saved"]:
        parts.append(f"{n['saved']} saved")
    if n["helpful"]:
        parts.append(f"{n['helpful']} found it helpful")
    return ", ".join(parts)


@shared_task
def send_creator_feedback():
    """Celery-beat entry point (weekly). Never raises for one bad author."""
    from core.models import Notification
    from core.services import create_notification
    from django.contrib.auth import get_user_model

    now = timezone.now()
    numbers = {uid: n for uid, n in week_numbers(now - timedelta(days=WINDOW_DAYS)).items() if any(n.values())}
    if not numbers:
        return {"authors": 0, "sent": 0}

    recent = set(Notification.objects.filter(
        notif_type="creator_feedback", recipient_id__in=list(numbers),
        created_at__gte=now - timedelta(days=_SKIP_IF_SENT_WITHIN_DAYS),
    ).values_list("recipient_id", flat=True))

    User = get_user_model()
    sent = 0
    for user in User.objects.filter(id__in=[u for u in numbers if u not in recent], is_active=True).iterator(chunk_size=500):
        n = numbers[user.id]
        try:
            create_notification(
                user, "creator_feedback", "Your notes are being used", _message(n),
                data={**n, "days": WINDOW_DAYS},
            )
            sent += 1
        except Exception:
            logger.exception("send_creator_feedback: failed for user_id=%s", user.id)
    return {"authors": len(numbers), "sent": sent}
