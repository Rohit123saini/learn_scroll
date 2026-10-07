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
    # STORIES UPGRADE - PART 3b: a story that is in a Highlight is permanent -
    # the highlight points at this very row (post/highlights.py).
    old = Story.objects.filter(is_deleted=True, deleted_at__lte=cutoff, highlight_items__isnull=True)
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
# C4-BE — image size variants + BlurHash.
#
# Same shape as generate_video_thumbnail above (async off the upload request,
# storage-backend agnostic, bounded retry on transient errors) for the same
# reasons; the actual pixel work lives in services.build_image_variants.
#
# Idempotent: a row that already has `thumb_320` + `blur_hash` is skipped, so a
# duplicate enqueue / retry / backfill re-run costs one SELECT. `force=True`
# (used by `backfill_image_variants --force`) regenerates anyway and deletes the
# files it replaces. All three outputs are written to storage first and then
# recorded with ONE UPDATE, so a row never ends up half-done (thumb but no
# medium) — it either has all of it or none of it.
# ---------------------------------------------------------------------------
@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def generate_image_variants(self, media_id, force=False):
    """Create the 320px thumb, 720px medium and BlurHash for an image
    `PostMedia`, and fill `width`/`height` if they are still empty. Returns
    True if variants were written, False if skipped/failed permanently."""
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage

    from .models import PostMedia
    from .services import ImageVariantError, build_image_variants, download_storage_file_to_temp

    try:
        media = PostMedia.objects.get(id=media_id)
    except PostMedia.DoesNotExist:
        logger.warning("generate_image_variants: PostMedia %s no longer exists", media_id)
        return False

    if media.media_type not in ("image", "gif"):
        return False
    if not media.file:
        logger.warning("generate_image_variants: PostMedia %s has no file", media_id)
        return False
    if not force and media.thumb_320 and media.blur_hash:
        return False

    _, ext = os.path.splitext(media.file.name)
    local_path = None
    saved_names = []  # files written to storage by THIS run (for cleanup on failure)
    try:
        local_path = download_storage_file_to_temp(media.file, suffix=ext or ".jpg")
        try:
            variants = build_image_variants(local_path)
        except ImageVariantError as exc:
            # Corrupt / not-an-image / too big: the same bytes will fail the same
            # way on every retry, so don't retry. Logged as a warning (bad
            # user input), not an exception (not a bug in our code).
            logger.warning("generate_image_variants: skipping PostMedia %s: %s", media_id, exc)
            return False

        old_names = [f.name for f in (media.thumb_320, media.medium_720) if f]

        media.thumb_320.save(f"{media.id}_320.jpg", ContentFile(variants.thumb), save=False)
        saved_names.append(media.thumb_320.name)
        if variants.medium is not None:
            media.medium_720.save(f"{media.id}_720.jpg", ContentFile(variants.medium), save=False)
            saved_names.append(media.medium_720.name)
        elif force:
            media.medium_720 = None  # regenerating an animated image: don't keep a stale still medium around

        fields = {
            "thumb_320": media.thumb_320.name,
            "medium_720": media.medium_720.name if media.medium_720 else None,
            "blur_hash": variants.blurhash,
        }
        # Only fill dimensions that are missing — never overwrite a value the
        # client/another code path already stored.
        if media.width is None:
            fields["width"] = variants.width
        if media.height is None:
            fields["height"] = variants.height

        # .update() (not .save()): if the row was deleted while we were busy,
        # this affects 0 rows instead of raising — and we then clean up the
        # files we just wrote instead of leaving orphans in storage.
        if PostMedia.objects.filter(pk=media.pk).update(**fields) == 0:
            logger.warning("generate_image_variants: PostMedia %s deleted mid-task", media_id)
            for name in saved_names:
                default_storage.delete(name)
            return False
        saved_names = []  # now referenced by the row — the failure cleanup below must never touch them

        # Replaced files (force regenerate only) are now unreferenced.
        for name in old_names:
            try:
                default_storage.delete(name)
            except Exception:  # cleanup must never fail the task
                logger.warning("generate_image_variants: could not delete old file %s", name, exc_info=True)

        logger.info("generate_image_variants: variants generated for PostMedia %s", media_id)
        return True
    except Exception as exc:
        # Unexpected (transient storage read/write error, worker OOM...) — worth
        # a bounded retry. Drop anything already written so a retry starts clean.
        logger.exception("generate_image_variants: failed for PostMedia %s", media_id)
        for name in saved_names:
            try:
                default_storage.delete(name)
            except Exception:
                pass
        raise self.retry(exc=exc)
    finally:
        if local_path and os.path.exists(local_path):
            os.unlink(local_path)


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
    deliberate, documented noise trade-off for a later pass. (See the
    correction note below re: `NotificationPreference.muted_types` —
    it doesn't apply here yet either, for a confirmed reason, not an
    oversight.)

    Uses `core.services.create_bulk_notifications` (one bulk INSERT)
    rather than looping `create_notification` once per follower — the
    latter would also mean one `is_restricted_between` query per
    follower for the actor-restrict check, which doesn't scale to a
    fan-out that can be thousands of rows. The restrict exclusion below
    does the same thing `create_notification` would have per-recipient,
    but as a single bulk query up front instead.

    🔧 CORRECTED (this pass, post_app.md §14 item 12) — a mute-check
    was added here in an earlier pass, excluding any follower who had
    `NEW_POST_FROM_FOLLOWED` in `NotificationPreference.muted_types`
    from the bulk INSERT entirely. That was wrong and has been reverted,
    now that `core/services.py` itself is available to confirm why:
    `create_bulk_notifications()` — like `create_notification()` — is a
    tested, documented choke point that ONLY ever writes the
    `Notification` bell row; it never sends push/email/sms/whatsapp
    (see that module's own docstring and its
    `test_never_sends_a_push_itself` regression test). Every real
    channel-send in this codebase happens as a SEPARATE call the
    call-site makes alongside `create_notification`/
    `create_bulk_notifications`, not inside either of them. `Notification
    Preference.allowed_channels_for()`'s own docstring is explicit that
    a muted type still gets its in-app bell row — only the
    push/email/sms/whatsapp *send* is meant to be skipped. This
    function never makes that second, channel-send call for
    `NEW_POST_FROM_FOLLOWED` at all (unlike the two-call pattern the
    `core/services.py` module docstring shows for other notif types) —
    so there is currently no channel-dispatch step here for a mute to
    gate. Filtering muted followers out of the bulk INSERT therefore
    didn't skip an interruption they'd opted out of; it silently deleted
    their in-app history for this type, which is the exact outcome the
    model's own contract says muting must never cause.

    ⚠️ STILL OPEN, FLAGGED NOT GUESSED AT: this means `muted_types` is
    currently a no-op for `NEW_POST_FROM_FOLLOWED` specifically, since
    nothing reads it on this path. If/when a real push-dispatch call is
    added here (the `send_notification(...)`-style second call other
    call-sites make), THAT is where `allowed_channels_for()` /
    `muted_types` should be checked per-recipient before sending —
    mirroring the existing pattern exactly, not a new one. Whether
    `NEW_POST_FROM_FOLLOWED` even needs a push channel at all (versus
    staying in-app-only, in which case there is nothing to mute here and
    this whole line item resolves itself) is a product call for whoever
    owns notification UX, not something this task can decide on its own.
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


# ---------------------------------------------------------------------------
# C1-BE — daily retention prune for the `PostEvent` analytics log.
#
# Wire into settings.py CELERY_BEAT_SCHEDULE (done — "post-prune-old-events"):
#
#     "post-prune-old-events": {
#         "task": "post.tasks.prune_old_post_events",
#         "schedule": crontab(hour=3, minute=15),  # daily, off-peak
#     },
#
# `PostEvent` is append-only and by far the highest-volume table in this app
# (every impression is a row), so it is deleted in bounded batches: one giant
# `DELETE ... WHERE created_at < cutoff` on millions of rows would hold a long
# lock and bloat the WAL. Purely housekeeping — nothing reads events older than
# the retention window, and the endpoint's correctness never depends on this
# running.
# ---------------------------------------------------------------------------
POST_EVENT_RETENTION_DAYS = 30
POST_EVENT_PRUNE_BATCH = 5000


@shared_task
def prune_old_post_events(days=POST_EVENT_RETENTION_DAYS, batch_size=POST_EVENT_PRUNE_BATCH):
    """Delete `PostEvent` rows older than `days` (default 30). Returns the
    number of rows deleted."""
    from .models import PostEvent

    cutoff = timezone.now() - timedelta(days=days)
    total = 0
    while True:
        ids = list(
            PostEvent.objects.filter(created_at__lt=cutoff)
            .order_by()
            .values_list("pk", flat=True)[:batch_size]
        )
        if not ids:
            break
        deleted, _ = PostEvent.objects.filter(pk__in=ids).delete()
        total += deleted
    logger.info("prune_old_post_events: deleted %s events older than %sd", total, days)
    return total



@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def probe_media_metadata(self, media_id):
    """TASK 1.1-BE — fill a PostMedia's blank width/height/duration_seconds
    (any storage backend). Safety net behind the in-request fill in
    post.signals, and the worker for `backfill_media_dimensions`. Returns True
    if something was written."""
    from .models import PostMedia
    from .services import fill_media_metadata

    try:
        media = PostMedia.objects.get(id=media_id)
    except PostMedia.DoesNotExist:
        logger.warning("probe_media_metadata: PostMedia %s no longer exists", media_id)
        return False
    try:
        return bool(fill_media_metadata(media, allow_download=True, raise_errors=True))
    except Exception as exc:
        # Transient storage/ffprobe trouble -> bounded retry. (A corrupt file
        # just yields {} and is NOT retried.)
        raise self.retry(exc=exc)
