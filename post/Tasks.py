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
import os
from datetime import timedelta

from celery import shared_task
from django.core.files import File
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


# ---------------------------------------------------------------------------
# TASK 27 — video thumbnail generation, cloud-storage-safe + async.
#
# Moved here (as a Celery task, enqueued from signals.py's
# `queue_video_thumbnail_on_create`) instead of running inline in the
# `post_save` signal or the upload view, for two independent reasons:
#   1. ffmpeg is a slow subprocess call (real videos, real disk I/O) —
#      running it synchronously inside the request/signal cycle that
#      creates a `PostMedia` row would block `PostCreateAPIView`'s
#      response for however long ffmpeg takes.
#   2. It needs to be retryable on its own schedule (transient S3 read
#      hiccup, ffmpeg momentarily OOM-killed under load) without retrying
#      the whole post-creation request.
#
# Every failure path here is `logger.exception`/`logger.error`, not
# `print()` — `print()` output only ever reaches whatever happens to be
# tailing stdout at that exact moment; `logger.error`/`.exception` is
# what Sentry's `LoggingIntegration` (settings.py) actually captures as
# an event, which is the whole point of task 27's "silently fails in
# production" complaint.
#
# ⚠️ ADJUST if your `PostMedia` model (models.py) doesn't already have a
# nullable `thumbnail` ImageField/FileField — this task assumes one
# exists to write into. Field name used below: `thumbnail`.
# ---------------------------------------------------------------------------
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_video_thumbnail(self, media_id):
    """Generate and save a JPEG thumbnail for a video `PostMedia` row.

    Storage-backend agnostic: downloads the source video to a local temp
    file via `services.download_storage_file_to_temp` (works the same
    whether `default_storage` is local disk or S3 — see task 26/
    settings.py's `USE_S3_STORAGE`), runs ffmpeg against that local copy,
    then saves the resulting thumbnail back through the model field so it
    lands in whichever storage backend is currently active — no
    S3-specific code needed here at all, exactly because it never touches
    `.path` directly.
    """
    from .models import PostMedia
    from .services import download_storage_file_to_temp, generate_video_thumbnail_file

    try:
        media = PostMedia.objects.get(id=media_id)
    except PostMedia.DoesNotExist:
        # Media row (or its parent Post) was deleted between enqueue and
        # run — nothing to do, and not an error worth retrying.
        logger.warning("generate_video_thumbnail: PostMedia %s no longer exists", media_id)
        return

    if media.media_type != "video":
        return
    if getattr(media, "thumbnail", None):
        # Already has one — avoids redoing work if this task is ever
        # retried or the signal somehow fires twice for the same row.
        return
    if not media.file:
        logger.warning("generate_video_thumbnail: PostMedia %s has no file", media_id)
        return

    _, ext = os.path.splitext(media.file.name)
    local_video_path = None
    thumb_path = None
    try:
        local_video_path = download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
        thumb_path = generate_video_thumbnail_file(local_video_path)
        if thumb_path is None:
            # Already logged inside generate_video_thumbnail_file (missing
            # ffmpeg binary, ffmpeg failure, or timeout) — nothing more to
            # do here. Not retried: a video that ffmpeg can't decode won't
            # decode any better on retry #2.
            return

        with open(thumb_path, "rb") as f:
            media.thumbnail.save(f"{media.id}_thumb.jpg", File(f), save=True)
        logger.info("generate_video_thumbnail: thumbnail generated for PostMedia %s", media_id)
    except Exception as exc:
        # Genuinely unexpected failure (e.g. a transient storage-read
        # error downloading the source video) — worth a bounded retry
        # via Celery's own backoff, unlike the ffmpeg-level failures
        # above which are handled (and intentionally not retried) inside
        # generate_video_thumbnail_file.
        logger.exception("generate_video_thumbnail: failed for PostMedia %s", media_id)
        raise self.retry(exc=exc)
    finally:
        for path in (local_video_path, thumb_path):
            if path and os.path.exists(path):
                os.unlink(path)


# ---------------------------------------------------------------------------
# TASK 3 — "new post from someone you follow" fan-out.
#
# Enqueued (never called inline) from
# signals.py::queue_new_post_notification_fanout — same "don't block the
# request" reasoning as generate_video_thumbnail above, but for a much
# more common trigger (every post, not just video posts): a popular
# account's follower list can run into the thousands, and a synchronous
# loop inside PostCreateAPIView's request/response cycle would make
# every single post-create slow in direct proportion to follower count.
# This task does the actual Follow-table read and the notification
# fan-out off the request path.
#
# `post` app already imports `user_profile.Follow` directly elsewhere
# (the feed query — same precedent this reuses), so that import is at
# normal top-of-function level here too, not behind a try/except the way
# `core` is guarded in services.py — `user_profile` is a required app,
# not an optional one.
# ---------------------------------------------------------------------------
@shared_task
def notify_followers_new_post(post_id):
    """Notify every ACCEPTED follower of `post.user` that a new post went
    up. MVP version per the design doc: no per-follower "bell" opt-in
    yet — every accepted follower gets notified on every post. That's a
    deliberate, documented noise trade-off for a later pass.

    Uses `core.services.create_bulk_notifications` (one bulk INSERT)
    rather than looping `create_notification` once per follower — the
    latter would also mean one `is_restricted_between` query per
    follower for the actor-restrict check, which doesn't scale to a
    fan-out that can be thousands of rows. The restrict exclusion below
    does the same thing `create_notification` would have per-recipient,
    but as a single bulk query up front instead.
    """
    from core.models import Notification
    from core.services import create_bulk_notifications
    from user_profile.models import Follow, RestrictUser

    from .models import Post

    try:
        post = Post.objects.select_related("user").get(id=post_id)
    except Post.DoesNotExist:
        # Post (or its author) was deleted/removed between enqueue and
        # run — nothing left to notify about.
        logger.warning("notify_followers_new_post: Post %s no longer exists", post_id)
        return

    follower_ids = set(
        Follow.objects.filter(
            following_id=post.user_id, status=Follow.Status.ACCEPTED
        ).values_list("follower_id", flat=True)
    )
    if not follower_ids:
        return

    # Restrict is defined to be invisible to the restricted user (see
    # create_notification's own docstring in core/services.py) — that
    # must hold here too, not just on the single-recipient path. One
    # bulk query for every follower who has restricted this post's
    # author, instead of one is_restricted_between() call per follower.
    restricting_follower_ids = set(
        RestrictUser.objects.filter(
            user_id__in=follower_ids, restricted_id=post.user_id
        ).values_list("user_id", flat=True)
    )
    recipient_ids = follower_ids - restricting_follower_ids
    if not recipient_ids:
        return

    actor_name = post.user.get_full_name() or post.user.username
    create_bulk_notifications(
        recipient_ids,
        Notification.NotifType.NEW_POST_FROM_FOLLOWED,
        f"{actor_name} shared a new post",
        (post.content or "")[:200],
        data={"post_id": str(post.id), "actor_id": str(post.user_id)},
    )
    logger.info(
        "notify_followers_new_post: notified %d follower(s) for post %s",
        len(recipient_ids), post_id,
    )