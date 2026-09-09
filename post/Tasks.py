"""
post/tasks.py

⚠️ RENAMED from the uploaded `Tasks.py` (case-sensitivity — see
services.py's docstring).

⚠️ PREVIOUSLY BLOCKED ON — a `Story` model that didn't exist anywhere in
this app. That's now added in models.py (checklist items 54/55/57/60),
so this task's original premise is valid again. Rewritten against the
real field names on the new `Story` model (`user`, not `author`;
`soft_delete()` is a real method now, not assumed).

Wire into settings.py CELERY_BEAT_SCHEDULE:

    CELERY_BEAT_SCHEDULE = {
        ...
        "expire-old-stories": {
            "task": "post.tasks.expire_old_stories",
            "schedule": crontab(minute="*/15"),  # every 15 min is plenty
        },
        "purge-ancient-stories": {
            "task": "post.tasks.hard_delete_ancient_stories",
            "schedule": crontab(hour=3, minute=0, day_of_week=0),  # weekly
        },
    }

Note this task is pure housekeeping, not a visibility gate: `Story`
listing endpoints already filter `expires_at__gt=timezone.now()` in
real time (see `StoryListAPIView.get_queryset()` in views.py), so an
expired-but-not-yet-soft-deleted story is already invisible to users
even before this task runs. This task's only job is to stop expired rows
piling up forever and to soft-delete them so their `StoryView` rows are
eventually eligible for cleanup too.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def expire_old_stories():
    from .models import Story

    now = timezone.now()
    expired = Story.objects.filter(expires_at__lte=now, is_deleted=False)
    count = expired.count()
    for story in expired.iterator():
        story.soft_delete()
    logger.info("expire_old_stories: soft-deleted %s expired stories", count)
    return count


@shared_task
def hard_delete_ancient_stories(days=30):
    """Permanently remove stories soft-deleted more than `days` ago, so the
    DB doesn't grow forever with dead rows. Run weekly — not required for
    correctness (listing/visibility never depends on this task running)."""
    from .models import Story

    cutoff = timezone.now() - timedelta(days=days)
    old = Story.objects.filter(is_deleted=True, deleted_at__lte=cutoff)
    count = old.count()
    old.delete()
    logger.info("hard_delete_ancient_stories: purged %s stories older than %sd", count, days)
    return count