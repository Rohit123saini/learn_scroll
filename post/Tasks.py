"""
post/tasks.py

⚠️ RENAMED from the uploaded `Tasks.py` (case-sensitivity — see
services.py's docstring).

⚠️ PREVIOUSLY BLOCKED ON — a `Story` model that didn't exist anywhere in
this app. That's now added in models.py (checklist items 54/55/57/60),
so this task's original premise is valid again. Rewritten against the
real field names on the new `Story` model (`user`, not `author`;
`soft_delete()` is a real method now, not assumed).

Wired in settings.py CELERY_BEAT_SCHEDULE (F2): "post-expire-old-stories" (every 10 min) and
"post-purge-ancient-stories" (weekly, Sunday 03:05) -> `post.tasks.expire_old_stories` /
`post.tasks.hard_delete_ancient_stories`.

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


# F2 - the sweep soft-deletes in batches of this many rows (one UPDATE per batch) instead of one save() per story.
STORY_EXPIRE_BATCH_SIZE = 500
# Upper bound of batches per run, so one huge backlog can never pin a worker; the next beat tick carries on.
STORY_EXPIRE_MAX_BATCHES = 200


@shared_task
def expire_old_stories():
    """Soft-delete every story whose `expires_at` has passed. Returns how many rows were swept (an int).

    F2: one UPDATE per batch (`is_deleted=True, deleted_at=now`) - the same two fields `Story.soft_delete()` writes -
    instead of a `save()` per story. `Story` has no post_save/post_delete receiver, so nothing is skipped by using
    `.update()`. `deleted_at` is the sweep time, which is always >= `expires_at`; post/highlights.py relies on exactly
    that to tell "ended by itself" (archive-able) from "owner removed it early" (`deleted_at < expires_at`).
    Idempotent and safe to run concurrently: the `is_deleted=False` filter is repeated inside each UPDATE, so two
    overlapping runs can never count the same row twice."""
    from .models import Story

    now = timezone.now()
    total = 0
    for _ in range(STORY_EXPIRE_MAX_BATCHES):
        ids = list(
            Story.objects.filter(expires_at__lte=now, is_deleted=False)
            .order_by("expires_at")
            .values_list("id", flat=True)[:STORY_EXPIRE_BATCH_SIZE]
        )
        if not ids:
            break
        total += Story.objects.filter(id__in=ids, is_deleted=False).update(is_deleted=True, deleted_at=now)
        if len(ids) < STORY_EXPIRE_BATCH_SIZE:
            break
    logger.info("expire_old_stories: soft-deleted %s expired stories", total)
    return total


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
        # P-E2: an OLD row may still lack its BlurHash (made before video BlurHash existed): derive it from the poster.
        if not media.blur_hash:
            _attach_video_blurhash(media)
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

        # P-E2: BlurHash of the poster frame (the reels / feed player draws it until the first video frame).
        # Set before save() so it lands in the same UPDATE as the thumbnail.
        from .services import blurhash_for_file

        media.blur_hash = blurhash_for_file(thumb_path) or media.blur_hash
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


def _attach_video_blurhash(media):
    """P-E2: BlurHash from the already stored poster (`media.thumbnail`) of a video row. Best effort: returns True when
    saved, never raises (the thumbnail task must not fail because of a placeholder string)."""
    from .models import PostMedia
    from .services import blurhash_for_file, download_storage_file_to_temp

    local = None
    try:
        if not media.thumbnail:
            return False
        local = download_storage_file_to_temp(media.thumbnail, suffix=".jpg")
        value = blurhash_for_file(local)
        if not value:
            return False
        PostMedia.objects.filter(pk=media.pk).update(blur_hash=value)
        return True
    except Exception:
        logger.warning("video blurhash: failed for PostMedia %s", getattr(media, "pk", None), exc_info=True)
        return False
    finally:
        if local and os.path.exists(local):
            os.unlink(local)


# ---------------------------------------------------------------------------
# V1 — "feed me video nahi chal rahi": make every uploaded video playable.
#
# Phone cameras record H.265/HEVC (often 10-bit / HDR) which a lot of Android
# phones can't decode, and many MP4s keep their `moov` atom at the END of the
# file so streaming can't start until everything is downloaded. After every new
# video upload (enqueued from post.signals.queue_video_thumbnail_on_create) this
# task probes the file and, only if needed, converts it to
#     MP4 . H.264 yuv420p . AAC . +faststart      (longer side <= 1280px)
# `PostMedia.processing_status`: pending -> processing -> ready | failed.
# A video that already plays everywhere is left byte-for-byte alone.
#
# Same shape as generate_video_thumbnail above: off-request, storage-backend
# agnostic (works on local disk and S3), ffmpeg failures are logged and mark the
# row `failed` WITHOUT retry (the original file keeps being served), transient
# storage errors get a bounded Celery retry. Idempotent: a `ready` row is a
# no-op unless force=True (used by `manage.py process_videos --all`).
#
# The converted file is saved under a NEW name (`<id>_stream.mp4`) rather than
# overwriting the original, so players/CDNs that cached the old URL for a year
# (Cache-Control: max-age=31536000) never mix bytes of two different files. The
# original is deleted later (VIDEO_ORIGINAL_DELETE_DELAY_SECONDS, default 6h)
# so an app that loaded a feed page just before the swap doesn't hit a 404;
# set VIDEO_TRANSCODE_KEEP_ORIGINAL=True to keep originals forever.
#
# FAILURE HANDLING (round F2-reels): every failed conversion is recorded in `PostMedia.metadata["pipeline"]`
# ({attempts, last_error, failed_at, started_at, notified}). `retry_failed_video_processing` (beat, every 10 min) puts
# a `failed` row back in the queue after a back-off (attempts x VIDEO_PROCESSING_RETRY_BACKOFF_MINUTES) until
# VIDEO_PROCESSING_MAX_ATTEMPTS (3) is used up, and re-queues rows stuck in pending / processing for more than
# VIDEO_PROCESSING_STUCK_MINUTES (worker died). When the LAST attempt fails the owner gets ONE bell + push
# notification ("could not process your video") with a Retry action (`POST /post/media/<id>/retry-processing/`).
#
# THUMBNAIL SPRITE: after the MP4 is ready `_attach_sprite` builds one jpeg grid of small frames for the seek-bar
# preview (`metadata["sprite"]`, API `sprite`). Best effort like HLS.
#
# HLS (360p / 540p / 720p): once the row is `ready`, `_attach_hls` builds an adaptive rendition from the
# playable MP4 and stores the master playlist in `PostMedia.hls_playlist` (API: `hls_url`). Best effort -
# it never changes `processing_status`; the MP4 stays the fallback. Old rows: `process_videos --hls`.
# ---------------------------------------------------------------------------
@shared_task
def delete_replaced_media_file(storage_name):
    """Delete a file that a PostMedia no longer points at (the pre-conversion
    original). Refuses if some row still references it."""
    from .models import PostMedia

    if not storage_name:
        return False
    if PostMedia.objects.filter(file=storage_name).exists():
        return False
    try:
        PostMedia._meta.get_field("file").storage.delete(storage_name)
    except Exception:
        logger.exception("delete_replaced_media_file: could not delete %s", storage_name)
        return False
    return True


@shared_task
def delete_replaced_hls(prefix):
    """Delete an HLS folder that no PostMedia points at any more (replaced by a newer build)."""
    from . import services as svc
    from .models import PostMedia

    if not prefix or not prefix.startswith("posts/hls/"):
        return 0
    if PostMedia.objects.filter(hls_playlist__startswith=prefix + "/").exists():
        return 0
    return svc.delete_storage_tree(PostMedia._meta.get_field("file").storage, prefix)


def _attach_hls(media_id, local_path, *, force=False):
    """Build the HLS ladder (360p / 540p / 720p) from the LOCAL, already playable MP4 `local_path`, upload
    it and save the master playlist on the row (`PostMedia.hls_playlist`).

    BEST EFFORT by design: the MP4 is already `ready` and keeps playing, so every failure here is logged
    and returns False without touching `processing_status`. Skipped when HLS is disabled, the video is
    longer than VIDEO_HLS_MAX_SECONDS, or the row already has a playlist (unless force). Returns True when
    a playlist was saved.
    """
    import shutil
    import uuid

    from django.conf import settings

    from . import services as svc
    from .models import PostMedia

    out_dir = None
    try:
        if not svc.video_hls_enabled():
            return False
        media = PostMedia.objects.filter(pk=media_id).only("id", "media_type", "hls_playlist", "metadata").first()
        if media is None or media.media_type != "video":
            return False
        if media.hls_playlist and not force:
            return False
        info = svc._run_ffprobe(local_path)
        if not info:
            return False
        probed = svc.parse_ffprobe_output(info, "video")
        max_seconds = int(getattr(settings, "VIDEO_HLS_MAX_SECONDS", 900))
        if max_seconds > 0 and (probed.get("duration_seconds") or 0) > max_seconds:
            logger.info("hls: %s is longer than %ss, MP4 only", media_id, max_seconds)
            return False
        built = svc.transcode_video_hls(local_path, info)
        if built is None:
            return False
        out_dir = built["dir"]
        storage = PostMedia._meta.get_field("file").storage
        prefix = f"posts/hls/{media_id}/{uuid.uuid4().hex[:8]}"
        master = svc.upload_hls_directory(storage, out_dir, prefix)

        meta = dict(media.metadata or {})
        meta["hls"] = {"rungs": [r["name"] for r in built["rungs"]], "segment_seconds": int(getattr(settings, "VIDEO_HLS_SEGMENT_SECONDS", 4))}
        old = media.hls_playlist
        if not PostMedia.objects.filter(pk=media_id).update(hls_playlist=master, metadata=meta):
            svc.delete_storage_tree(storage, prefix)  # row deleted while we were encoding
            return False
        old_prefix = svc.hls_prefix_of(old)
        if old_prefix:
            delay = int(getattr(settings, "VIDEO_ORIGINAL_DELETE_DELAY_SECONDS", 6 * 3600))
            try:
                delete_replaced_hls.apply_async(args=[old_prefix], countdown=delay)
            except Exception:
                logger.warning("could not schedule deletion of %s (left in storage)", old_prefix, exc_info=True)
        logger.info("hls: %s -> %s (%s)", media_id, master, ", ".join(meta["hls"]["rungs"]))
        return True
    except Exception:
        logger.exception("hls: failed for PostMedia %s (MP4 still plays)", media_id)
        return False
    finally:
        if out_dir:
            shutil.rmtree(out_dir, ignore_errors=True)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, soft_time_limit=3900, time_limit=4020)  # P-C3: HLS ffmpeg timeout is 3600 s
def build_hls_rendition(self, media_id, force=False):
    """HLS only (no MP4 conversion): for videos that were processed before HLS existed
    (`manage.py process_videos --hls`). Downloads the current MP4 and runs the same best-effort build."""
    from . import services as svc
    from .models import PostMedia

    media = PostMedia.objects.filter(pk=media_id).first()
    if media is None or media.media_type != "video" or not media.file:
        return "skipped"
    if media.hls_playlist and not force:
        return "already-has-hls"
    if not svc.video_hls_enabled():
        return "disabled"
    _, ext = os.path.splitext(media.file.name)
    src = None
    try:
        src = svc.download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
        return "built" if _attach_hls(media_id, src, force=force) else "not-built"
    except Exception as exc:  # transient storage error while downloading
        logger.exception("build_hls_rendition: failed for PostMedia %s", media_id)
        if self.request.retries >= self.max_retries:
            return "error"
        raise self.retry(exc=exc)
    finally:
        if src and os.path.exists(src):
            os.unlink(src)


# ---------------------------------------------------------------------------
# Failure bookkeeping + notification, thumbnail sprite
# ---------------------------------------------------------------------------
def _pipeline_max_attempts():
    from django.conf import settings

    return max(1, int(getattr(settings, "VIDEO_PROCESSING_MAX_ATTEMPTS", 3)))


def _pipeline_meta_update(media_id, **changes):
    """Merge `changes` into `PostMedia.metadata["pipeline"]` (read-modify-write of that one key). Returns the new dict
    or None when the row is gone. Never raises."""
    from .models import PostMedia

    try:
        media = PostMedia.objects.filter(pk=media_id).only("id", "metadata").first()
        if media is None:
            return None
        meta = dict(media.metadata or {})
        pipe = dict(meta.get("pipeline") or {})
        pipe.update(changes)
        meta["pipeline"] = pipe
        PostMedia.objects.filter(pk=media_id).update(metadata=meta)
        return pipe
    except Exception:
        logger.exception("could not update the pipeline info of PostMedia %s", media_id)
        return None


def _mark_processing_failed(media_id, reason):
    """Row -> `failed`, remember why (attempt count, error text, time). The LAST allowed attempt also notifies the
    owner (once). Returns the attempt number."""
    from .models import PostMedia

    PostMedia.objects.filter(pk=media_id).update(processing_status=PostMedia.PROCESSING_FAILED)
    media = PostMedia.objects.filter(pk=media_id).only("id", "metadata").first()
    pipe = dict(((media.metadata or {}).get("pipeline") if media else None) or {})
    attempts = int(pipe.get("attempts") or 0) + 1
    final = attempts >= _pipeline_max_attempts()
    pipe = _pipeline_meta_update(
        media_id, attempts=attempts, last_error=str(reason)[:200], failed_at=timezone.now().isoformat(),
    ) or {}
    if final and not pipe.get("notified"):
        if notify_video_processing_failed(media_id):
            _pipeline_meta_update(media_id, notified=True)
    return attempts


def notify_video_processing_failed(media_id):
    """One bell row + push to the video's owner: processing failed for good. True when a row was written.
    Uses the GENERIC notification type (data.kind tells the app what it is), so no enum / migration change."""
    from .models import PostMedia

    try:
        media = PostMedia.objects.filter(pk=media_id).select_related("post", "post__user").first()
        if media is None or media.post_id is None or media.post.is_deleted:
            return False
        from core.models import Notification
        from core.services import create_notification

        from .services import _push_row

        row = create_notification(
            media.post.user,
            Notification.NotifType.GENERIC,
            "We could not process your video",
            "Your video could not be prepared for playback. Open it and tap Retry, or upload it again.",
            data={
                "kind": "video_processing_failed",
                "post_id": str(media.post_id),
                "media_id": str(media.id),
                "action": "retry_processing",
            },
        )
        _push_row(row)
        return row is not None
    except Exception:
        logger.exception("notify_video_processing_failed: could not notify for PostMedia %s", media_id)
        return False


def _attach_sprite(media_id, local_path, *, force=False):
    """Seek-bar preview: build ONE jpeg grid of small frames from the LOCAL playable MP4 `local_path`, upload it and
    record it in `PostMedia.metadata["sprite"]` ({name, interval, frames, cols, rows, thumb_w, thumb_h}).

    BEST EFFORT, same as `_attach_hls`: a failure is logged and returns False; the video keeps playing without a
    scrub preview. Skipped when disabled or the row already has one (unless force). True when saved."""
    import uuid

    from django.core.files.base import ContentFile

    from . import services as svc
    from .models import PostMedia

    sprite_path = None
    try:
        if not svc.video_sprite_enabled():
            return False
        media = PostMedia.objects.filter(pk=media_id).only("id", "media_type", "metadata", "width", "height", "duration_seconds").first()
        if media is None or media.media_type != "video":
            return False
        if svc.sprite_info(media) and not force:
            return False
        info = svc._run_ffprobe(local_path)
        probed = svc.parse_ffprobe_output(info, "video") if info else {}
        duration = probed.get("duration_seconds") or media.duration_seconds
        plan = svc.plan_video_sprite(duration, probed.get("width") or media.width, probed.get("height") or media.height)
        if plan is None:
            return False
        sprite_path = svc.generate_video_sprite_file(local_path, plan)
        if sprite_path is None:
            return False
        storage = PostMedia._meta.get_field("file").storage
        with open(sprite_path, "rb") as fh:
            name = storage.save(f"posts/sprites/{media_id}/{uuid.uuid4().hex[:8]}.jpg", ContentFile(fh.read()))
        fresh = PostMedia.objects.filter(pk=media_id).only("id", "metadata").first()
        if fresh is None:
            storage.delete(name)  # row deleted while we were working
            return False
        meta = dict(fresh.metadata or {})
        old = (meta.get("sprite") or {}).get("name") if isinstance(meta.get("sprite"), dict) else None
        meta["sprite"] = dict(plan, name=name)
        PostMedia.objects.filter(pk=media_id).update(metadata=meta)
        if old and old != name:
            try:
                storage.delete(old)
            except Exception:
                logger.warning("could not delete the replaced sprite %s", old, exc_info=True)
        return True
    except Exception:
        logger.exception("sprite: failed for PostMedia %s (video still plays)", media_id)
        return False
    finally:
        if sprite_path and os.path.exists(sprite_path):
            os.unlink(sprite_path)


def _attach_extras(media_id, local_path, *, force=False):
    """After the MP4 is ready: HLS ladder, then the thumbnail sprite. Both best effort, one after the other (they
    both write the row's metadata)."""
    _attach_hls(media_id, local_path, force=force)
    _attach_sprite(media_id, local_path, force=force)


@shared_task(bind=True, max_retries=1, default_retry_delay=60, soft_time_limit=600, time_limit=660)  # P-C3
def build_video_sprite(self, media_id, force=False):
    """Sprite only (old videos, `process_videos --sprite`): downloads the current MP4 and runs `_attach_sprite`."""
    from . import services as svc
    from .models import PostMedia

    media = PostMedia.objects.filter(pk=media_id).first()
    if media is None or media.media_type != "video" or not media.file:
        return "skipped"
    if svc.sprite_info(media) and not force:
        return "already-has-sprite"
    if not svc.video_sprite_enabled():
        return "disabled"
    _, ext = os.path.splitext(media.file.name)
    src = None
    try:
        src = svc.download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
        return "built" if _attach_sprite(media_id, src, force=force) else "not-built"
    except Exception as exc:
        logger.exception("build_video_sprite: failed for PostMedia %s", media_id)
        raise self.retry(exc=exc)
    finally:
        if src and os.path.exists(src):
            os.unlink(src)


@shared_task(bind=True, max_retries=2, default_retry_delay=60, soft_time_limit=7000, time_limit=7200)  # P-C3: transcode 1800 + HLS 3600 + sprite 300
def process_video_for_streaming(self, media_id, force=False):
    """Convert a video `PostMedia` to a universally playable MP4 if needed.
    Returns a short status string (handy in logs / `process_videos --sync`)."""
    from django.conf import settings
    from . import services as svc
    from .models import PostMedia

    def _status(value):
        PostMedia.objects.filter(pk=media_id).update(processing_status=value)

    try:
        media = PostMedia.objects.get(id=media_id)
    except PostMedia.DoesNotExist:
        logger.warning("process_video_for_streaming: PostMedia %s no longer exists", media_id)
        return "missing"
    if media.media_type != "video" or not media.file:
        return "skipped"
    if media.processing_status == PostMedia.PROCESSING_READY and not force:
        return "already-ready"
    if not svc.video_transcode_enabled():
        _status(PostMedia.PROCESSING_READY)
        return "disabled"

    _status(PostMedia.PROCESSING_RUNNING)
    _pipeline_meta_update(media_id, started_at=timezone.now().isoformat())
    old_name = media.file.name
    _, ext = os.path.splitext(old_name)
    src = out = None
    try:
        src = svc.download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
        info = svc._run_ffprobe(src)
        if not info:
            # Corrupt file or ffprobe missing (already logged). Not retried.
            _mark_processing_failed(media_id, "probe-failed")
            return "probe-failed"
        plan = svc.plan_video_processing(info, ext=ext, moov_first=svc.mp4_moov_before_mdat(src))
        action = plan["action"]
        if action == "skip":
            logger.warning("process_video_for_streaming: %s has no video stream", media_id)
            _mark_processing_failed(media_id, "no-video-stream")
            return "no-video-stream"
        if action == "none":
            _status(PostMedia.PROCESSING_READY)
            _pipeline_meta_update(media_id, last_error="", notified=False)
            _attach_extras(media_id, src, force=force)  # HLS + sprite, best effort, never changes the status
            return "already-playable"

        if action == "remux":
            out = svc.remux_video_faststart(src, audio_copy=plan["audio_copy"])
            if out is None:  # odd streams that can't be copied -> full re-encode
                out = svc.transcode_video_h264(src)
        else:
            out = svc.transcode_video_h264(src)
        if out is None:
            _mark_processing_failed(media_id, "ffmpeg-failed")
            return "ffmpeg-failed"
        probed = svc.verify_streamable_video(out)
        if probed is None:
            logger.error("process_video_for_streaming: output for %s failed verification", media_id)
            _mark_processing_failed(media_id, "verify-failed")
            return "verify-failed"

        with open(out, "rb") as fh:
            media.file.save(f"{media.id}_stream.mp4", File(fh), save=False)
        new_name = media.file.name
        video = svc._first_stream(info, "video") or {}
        meta = dict(media.metadata or {})
        meta["video_pipeline"] = {
            "action": action,
            "reasons": plan["reasons"],
            "source": {
                "codec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
                "width": video.get("width"), "height": video.get("height"),
                "name": old_name,
            },
        }
        updates = {
            "file": new_name,
            "mime_type": "video/mp4",
            "file_size_bytes": os.path.getsize(out),
            "processing_status": PostMedia.PROCESSING_READY,
            "metadata": meta,
        }
        # Probed from the OUTPUT, so rotation is already applied.
        for key in ("width", "height", "duration_seconds"):
            if probed.get(key):
                updates[key] = probed[key]
        if not PostMedia.objects.filter(pk=media.pk).update(**updates):
            # Row deleted while we were encoding -> don't leak the new file.
            PostMedia._meta.get_field("file").storage.delete(new_name)
            return "row-deleted"

        if old_name and old_name != new_name and not getattr(settings, "VIDEO_TRANSCODE_KEEP_ORIGINAL", False):
            delay = int(getattr(settings, "VIDEO_ORIGINAL_DELETE_DELAY_SECONDS", 6 * 3600))
            try:
                delete_replaced_media_file.apply_async(args=[old_name], countdown=delay)
            except Exception:
                logger.warning("could not schedule deletion of %s (left on disk)", old_name, exc_info=True)
        _pipeline_meta_update(media_id, last_error="", notified=False)
        logger.info("process_video_for_streaming: %s -> %s (%s: %s)", media_id, new_name, action, "; ".join(plan["reasons"]))
        _attach_extras(media_id, out, force=force)  # HLS + sprite, best effort, never changes the status
        return action
    except Exception as exc:
        logger.exception("process_video_for_streaming: failed for PostMedia %s", media_id)
        if self.request.retries >= self.max_retries:
            _mark_processing_failed(media_id, "error: %s" % exc)
            return "error"
        raise self.retry(exc=exc)
    finally:
        for path in (src, out):
            if path and os.path.exists(path):
                os.unlink(path)


@shared_task
def retry_failed_video_processing(batch_size=50):
    """Beat task (every 10 minutes). (1) `failed` videos with attempts left and an expired back-off go back in the
    queue; (2) videos stuck in `pending` / `processing` for VIDEO_PROCESSING_STUCK_MINUTES (the worker died) too.
    Returns {"retried": n, "stuck": n, "exhausted": n}."""
    from datetime import datetime

    from django.conf import settings

    from .models import PostMedia

    if not getattr(settings, "VIDEO_TRANSCODE_ENABLED", True):
        return {"retried": 0, "stuck": 0, "exhausted": 0}
    now = timezone.now()
    max_attempts = _pipeline_max_attempts()
    backoff = max(1, int(getattr(settings, "VIDEO_PROCESSING_RETRY_BACKOFF_MINUTES", 10)))
    stuck_after = timedelta(minutes=max(10, int(getattr(settings, "VIDEO_PROCESSING_STUCK_MINUTES", 120))))
    retried = stuck = exhausted = 0
    requeued_ids = set()  # R-B6: rows the first loop just put back in the queue (they are `pending` now)

    def _when(value):
        try:
            dt = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return timezone.make_aware(dt) if timezone.is_naive(dt) else dt

    def _requeue(media_id):
        PostMedia.objects.filter(pk=media_id).update(processing_status=PostMedia.PROCESSING_PENDING)
        try:
            process_video_for_streaming.delay(str(media_id))
            return True
        except Exception:
            logger.exception("retry_failed_video_processing: could not enqueue %s", media_id)
            PostMedia.objects.filter(pk=media_id).update(processing_status=PostMedia.PROCESSING_FAILED)
            return False

    failed = PostMedia.objects.filter(media_type="video", processing_status=PostMedia.PROCESSING_FAILED).only("id", "metadata", "created_at").order_by("created_at")[: int(batch_size) * 4]
    for media in failed:
        if retried >= batch_size:
            break
        pipe = (media.metadata or {}).get("pipeline") or {}
        attempts = int(pipe.get("attempts") or 0)
        if attempts >= max_attempts:
            exhausted += 1
            continue
        failed_at = _when(pipe.get("failed_at"))
        if failed_at is not None and now - failed_at < timedelta(minutes=backoff * max(1, attempts)):
            continue
        if _requeue(media.id):
            retried += 1
            requeued_ids.add(media.id)

    cutoff = now - stuck_after
    waiting = PostMedia.objects.filter(
        media_type="video", processing_status__in=[PostMedia.PROCESSING_PENDING, PostMedia.PROCESSING_RUNNING], created_at__lt=cutoff,
    ).only("id", "metadata", "created_at").order_by("created_at")[: int(batch_size)]
    for media in waiting:
        if media.id in requeued_ids:
            continue  # R-B6: just re-queued above - an old upload must not be queued a second time as "stuck"
        started = _when(((media.metadata or {}).get("pipeline") or {}).get("started_at"))
        if started is not None and started > cutoff:
            continue  # a worker really is on it
        if _requeue(media.id):
            stuck += 1
    if retried or stuck:
        logger.info("retry_failed_video_processing: retried=%s stuck=%s exhausted=%s", retried, stuck, exhausted)
    return {"retried": retried, "stuck": stuck, "exhausted": exhausted}


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
    N1b note below re: preferences / mutes on this path.)

    Uses `core.services.create_bulk_notifications` (one bulk INSERT)
    rather than looping `create_notification` once per follower — the
    latter would also mean one `is_restricted_between` query per
    follower for the actor-restrict check, which doesn't scale to a
    fan-out that can be thousands of rows. The restrict exclusion below
    does the same thing `create_notification` would have per-recipient,
    but as a single bulk query up front instead.

    N1b - MUTES / PREFERENCES ON THIS PATH (resolved, no longer "flagged"): this task only writes bell rows
    (`create_bulk_notifications`) and never sends push / email / sms / whatsapp itself, so the only switch that can
    apply per recipient is the matrix "In-app" chip for `new_post_from_followed`. `create_bulk_notifications` already
    enforces it for every recipient with ONE query (`core.services.in_app_blocked_ids`) - a follower who switched
    In-app off for this type gets no row; everyone else is untouched. Legacy `muted_types` deliberately does NOT drop
    the row ("a muted type keeps its bell row" - NotificationPreference.allowed_channels_for): the old per-recipient
    mute filter that used to live here was wrong for exactly that reason and stays out. If a push is ever added for this
    type, gate it with `NotificationPreference.channel_allowed(type, "push")` per recipient like the other call-sites.
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
    if post.is_deleted or post.moderation_status != "approved":
        return  # H23: held / removed before the worker ran - nobody is told about a hidden post
    from .audience import class_recording_classroom_id

    if post.visibility == "class_campus" and class_recording_classroom_id(post) is not None:
        return  # X12: a class-only recording is for that classroom's members - the author's followers are not told

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
    """Delete `PostEvent` rows older than `days` (default 30). Returns the number of rows deleted.
    H10: the work lives in post/feed_retention.py (time-budgeted, pauses between batches). `batch_size` is kept for
    backwards compatibility with existing callers / tests."""
    from . import feed_retention

    result = feed_retention.prune_post_events(days=days, batch_size=batch_size)
    logger.info("prune_old_post_events: deleted %s events older than %sd", result["rows"], days)
    return result["rows"]


# H10 - the two other write-heavy feed tables (see post/feed_retention.py for what is kept and why).
@shared_task
def prune_seen_post_views():
    """Delete 'seen in the feed' markers older than the seen window (+7 d). Counted views are never touched."""
    from . import feed_retention

    result = feed_retention.prune_seen_post_views()
    logger.info("prune_seen_post_views: %s", result)
    return result["rows"]


@shared_task
def scrub_post_view_pii():
    """Blank ip_address / user_agent on PostView rows older than 90 days (row stays for counts / insights)."""
    from . import feed_retention

    result = feed_retention.scrub_post_view_pii()
    logger.info("scrub_post_view_pii: %s", result)
    return result["rows"]


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


# ---------------------------------------------------------------------------
# T1 item 9 -- nightly feed summary (post/feed_metrics.py). Wired in settings.py
# CELERY_BEAT_SCHEDULE ("post-feed-daily-metrics", 02:45, before the 03:15 event prune).
# ---------------------------------------------------------------------------
@shared_task
def feed_daily_metrics(date_str=None):
    """Compute yesterday's (UTC) feed summary per A/B variant, log it and keep it in the cache under
    `feed:metrics:<YYYY-MM-DD>` (35 days) so a dashboard can read it. Returns the dict."""
    from datetime import date, datetime, timedelta, timezone as dt_timezone

    from django.core.cache import cache

    from . import feed_metrics

    day = date.fromisoformat(date_str) if date_str else datetime.now(dt_timezone.utc).date() - timedelta(days=1)
    start = datetime(day.year, day.month, day.day, tzinfo=dt_timezone.utc)
    summary = feed_metrics.compute_metrics(start, start + timedelta(days=1))
    summary["date"] = day.isoformat()
    # H10: request-level health for the same UTC day (availability / error rate / p95 / degraded rate) + SLO verdict
    try:
        from . import feed_ops

        ops = feed_ops.summarize(start.timestamp(), (start + timedelta(days=1)).timestamp())
        ops["breaches"] = feed_ops.breaches(ops)
        summary["ops"] = ops
        if ops["breaches"]:
            logger.error("feed_daily_metrics %s: SLO missed - %s", day.isoformat(), "; ".join(ops["breaches"]))
    except Exception:
        logger.warning("feed_daily_metrics: ops summary failed", exc_info=True)
    logger.info("feed_daily_metrics %s: %s", day.isoformat(), summary)
    try:
        cache.set(f"feed:metrics:{day.isoformat()}", summary, 35 * 86400)
    except Exception:
        logger.warning("feed_daily_metrics: caching the result failed", exc_info=True)
    return summary


@shared_task
def feed_slo_watch():
    """H10: fast-burn SLO check every 5 minutes (availability >= 99.5 %, p95 <= 800 ms over the last 15 min, plus
    "Redis snapshot store failing" / "fallback failed" events). Logs ONE ERROR + a Sentry message on trouble.
    Returns the summary (so it is visible in the Celery result / flower)."""
    from . import feed_ops

    summary = feed_ops.check_and_alert()
    return {k: summary[k] for k in ("requests", "errors", "degraded", "availability_pct", "p95_ms", "problems")}


# ---------------------------------------------------------------------------
# Scheduled posts: flip due posts live. Runs every minute (beat entry
# "post-publish-scheduled"). Until this existed a post created with
# `scheduled_at` stayed `is_scheduled=True` forever, and the fan-out /
# tag notifications that were skipped at create time never went out.
# ---------------------------------------------------------------------------
@shared_task
def publish_scheduled_posts(batch_size=200):
    """Publish every scheduled post whose time has come. Returns how many went live.
    Each post is claimed with a conditional UPDATE, so overlapping runs can't publish (or notify) twice."""
    from django.db import transaction

    from .models import Post
    from .tagging import notify_post_tags

    now = timezone.now()
    due = list(
        Post.objects.filter(is_scheduled=True, is_deleted=False, published_at__lte=now)
        .order_by("published_at").values_list("id", "published_at")[:batch_size]
    )
    published = 0
    for post_id, published_at in due:
        with transaction.atomic():
            old_created = Post.objects.filter(pk=post_id).values_list("created_at", flat=True).first()
            new_created = published_at or now
            # created_at drives every feed's ordering, so it becomes the moment the post goes live
            claimed = Post.objects.filter(pk=post_id, is_scheduled=True).update(
                is_scheduled=False, created_at=new_created)
            if claimed and old_created is not None and new_created > old_created:
                # A scheduled poll's "duration" must start when the post goes live, not when it was composed:
                # shift expires_at by the same wait (a poll scheduled 2 days ahead with 24h would be over at launch).
                from django.db.models import F

                from .models import PostPoll

                PostPoll.objects.filter(post_id=post_id, expires_at__isnull=False).update(
                    expires_at=F("expires_at") + (new_created - old_created))
        if not claimed:
            continue
        published += 1
        notify_followers_new_post.delay(post_id)
        try:
            post = Post.objects.get(pk=post_id)
            if post.moderation_status == "approved":
                notify_post_tags(post)
                from .collaborators import notify_pending_invites  # round 5: invites held back while it was scheduled
                notify_pending_invites(post)
                if post.post_type == "doubt":
                    notify_teachers_new_doubt.delay(str(post_id))
        except Exception:
            logger.exception("publish_scheduled_posts: tag notification failed for %s", post_id)
    return published


# ---------------------------------------------------------------------------
# H20 - UploadQueue housekeeping. Wire into settings.py CELERY_BEAT_SCHEDULE (done: "post-purge-stale-uploads").
# ---------------------------------------------------------------------------
@shared_task
def purge_stale_uploads():
    """Daily: free the chunk files of expired / abandoned upload sessions (disk!), delete finished sessions older than
    7 days, idempotency records older than 48 h and drafts deleted more than 30 days ago.
    Returns the counts (shown in the Celery result / Sentry cron check-in)."""
    from .upload_sessions import purge_stale

    counts = purge_stale()
    if any(counts.values()):
        logger.info("purge_stale_uploads: %s", counts)
    return counts


# ---------------------------------------------------------------------------
# H23 - publish gate housekeeping (duplicate-content memory). Beat entry: "post-purge-publish-fingerprints".
# ---------------------------------------------------------------------------
@shared_task
def purge_publish_fingerprints():
    """Daily: delete PublishFingerprint rows older than PUBLISH_FINGERPRINT_KEEP_DAYS (30). Returns the count."""
    from .publish_gate import purge_old_fingerprints

    deleted = purge_old_fingerprints()
    if deleted:
        logger.info("purge_publish_fingerprints: deleted %s", deleted)
    return deleted


# ---------------------------------------------------------------------------
# Doubt post upgrade (post/doubts.py)
# ---------------------------------------------------------------------------
@shared_task
def notify_teachers_new_doubt(post_id):
    """Tell the verified teachers whose subject matches a new doubt (once per doubt). Returns how many were told."""
    from .doubts import notify_teachers_for_doubt

    return notify_teachers_for_doubt(post_id)


@shared_task
def refund_stale_doubt_bounties():
    """Hourly (beat "post-refund-doubt-bounties"): give held bounties back when a doubt got no best answer in
    DOUBT_BOUNTY_EXPIRY_DAYS or the post was deleted / rejected. Returns how many were refunded."""
    from .doubts import refund_stale_bounties

    refunded = refund_stale_bounties()
    if refunded:
        logger.info("refund_stale_doubt_bounties: refunded %s", refunded)
    return refunded


# ---------------------------------------------------------------------------
# Video lectures (round 4): automatic Hindi / English captions (post/lectures.py, post/lecture_views.py)
#
#   generate_video_captions(track_id)   start ONE AWS Transcribe job for a VideoCaptionTrack(source=auto, status=pending)
#   poll_video_caption_jobs()           beat "post-poll-video-captions" (every 2 min): finished job -> WebVTT file on the track
#
# Settings (all optional except the bucket):
#   CAPTIONS_S3_BUCKET                    bucket Transcribe writes into (falls back to AWS_STORAGE_BUCKET_NAME)
#   AWS_REGION                            falls back to AWS_S3_REGION_NAME, then ap-south-1
#   LECTURE_AUTO_CAPTIONS_ENABLED         master switch (default True)
#   LECTURE_AUTO_CAPTION_MAX_SECONDS      longest video that gets auto captions (default 3 h)
#   LECTURE_CAPTION_JOB_TIMEOUT_HOURS     a job still running after this is marked failed (default 6)
# Transcribe reads the video from S3. With S3 media storage the existing object is used as is; on local-disk storage the
# file is copied to <bucket>/lecture-captions-input/<track id>/ first and removed again when the job ends.
# ---------------------------------------------------------------------------
_CAPTION_LANGUAGE_CODES = {"hi": "hi-IN", "en": "en-IN"}
_TRANSCRIBE_MEDIA_FORMATS = {"mp3", "mp4", "wav", "flac", "ogg", "webm", "m4a", "amr"}
_CAPTION_INPUT_PREFIX = "lecture-captions-input/"
_CAPTION_OUTPUT_PREFIX = "lecture-captions/"


def _captions_bucket():
    from django.conf import settings

    return getattr(settings, "CAPTIONS_S3_BUCKET", "") or getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or ""


def _captions_region():
    from django.conf import settings

    return getattr(settings, "AWS_REGION", "") or getattr(settings, "AWS_S3_REGION_NAME", "") or "ap-south-1"


def _caption_media_uri(media, track_id, s3, bucket):
    """s3:// URI Transcribe can read for `media`. Uses the object itself on S3 storage, else uploads a temporary copy."""
    import uuid

    from . import services as svc

    storage = media.file.storage
    storage_bucket = getattr(storage, "bucket_name", "") or ""
    if storage_bucket:
        location = (getattr(storage, "location", "") or "").strip("/")
        key = f"{location}/{media.file.name}" if location else media.file.name
        return f"s3://{storage_bucket}/{key}", False
    _, ext = os.path.splitext(media.file.name)
    src = svc.download_storage_file_to_temp(media.file, suffix=ext or ".mp4")
    try:
        key = f"{_CAPTION_INPUT_PREFIX}{track_id}/{uuid.uuid4().hex}{ext or '.mp4'}"
        s3.upload_file(src, bucket, key)
    finally:
        if os.path.exists(src):
            os.unlink(src)
    return f"s3://{bucket}/{key}", True


def _fail_caption_track(track_id, reason):
    from .models import VideoCaptionTrack

    VideoCaptionTrack.objects.filter(pk=track_id).update(status="failed", error=str(reason)[:200], updated_at=timezone.now())


@shared_task(bind=True, max_retries=0)
def generate_video_captions(self, track_id):
    """Start the speech-to-text job of one auto caption track. Only STARTS it - poll_video_caption_jobs collects the result.
    Returns 'started' | 'skipped' | 'failed'."""
    import uuid

    from .models import VideoCaptionTrack

    # claim: only a pending auto track moves on (a second delivery of the same message does nothing)
    if not VideoCaptionTrack.objects.filter(pk=track_id, source="auto", status="pending").update(
            status="processing", error="", updated_at=timezone.now()):
        return "skipped"
    track = VideoCaptionTrack.objects.select_related("media").get(pk=track_id)
    media = track.media
    bucket = _captions_bucket()
    code = _CAPTION_LANGUAGE_CODES.get(track.language)
    if not bucket or not code or media.media_type != "video" or not media.file:
        _fail_caption_track(track_id, "not_configured" if not bucket else "unsupported")
        return "failed"
    try:
        import boto3

        region = _captions_region()
        s3 = boto3.client("s3", region_name=region)
        transcribe = boto3.client("transcribe", region_name=region)
        uri, _copied = _caption_media_uri(media, track.id, s3, bucket)
        job_name = f"lecture-{track.id}-{uuid.uuid4().hex[:8]}"
        args = dict(
            TranscriptionJobName=job_name, Media={"MediaFileUri": uri}, LanguageCode=code,
            OutputBucketName=bucket, OutputKey=f"{_CAPTION_OUTPUT_PREFIX}{track.id}/",
            Subtitles={"Formats": ["vtt"], "OutputStartIndex": 1},
        )
        ext = os.path.splitext(media.file.name)[1].lower().lstrip(".")
        if ext in _TRANSCRIBE_MEDIA_FORMATS:
            args["MediaFormat"] = ext
        transcribe.start_transcription_job(**args)
        VideoCaptionTrack.objects.filter(pk=track_id).update(job_name=job_name, updated_at=timezone.now())
        return "started"
    except Exception as exc:
        logger.exception("generate_video_captions: could not start the job for track %s", track_id)
        _fail_caption_track(track_id, f"start_failed: {type(exc).__name__}")
        return "failed"


def _read_caption_vtt(s3, bucket, track_id, job_name, job):
    """Raw subtitle text of a finished job (the .vtt Transcribe wrote next to the transcript)."""
    from urllib.parse import urlparse

    key = f"{_CAPTION_OUTPUT_PREFIX}{track_id}/{job_name}.vtt"
    try:
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8-sig")
    except Exception:
        uris = ((job.get("Subtitles") or {}).get("SubtitleFileUris") or [])
        if not uris:
            raise
        parsed = urlparse(uris[0])
        path = parsed.path.lstrip("/")
        if parsed.scheme == "s3":
            alt_bucket, alt_key = parsed.netloc, path
        elif path.startswith(bucket + "/"):
            alt_bucket, alt_key = bucket, path[len(bucket) + 1:]
        else:
            alt_bucket, alt_key = bucket, path
        return s3.get_object(Bucket=alt_bucket, Key=alt_key)["Body"].read().decode("utf-8-sig")


def _delete_caption_input(s3, bucket, track_id):
    """Remove the temporary copy of the video (local-disk storage only; harmless when there is none)."""
    try:
        listed = s3.list_objects_v2(Bucket=bucket, Prefix=f"{_CAPTION_INPUT_PREFIX}{track_id}/")
        for obj in listed.get("Contents") or []:
            s3.delete_object(Bucket=bucket, Key=obj["Key"])
    except Exception:
        logger.warning("could not remove the temporary caption input of track %s", track_id, exc_info=True)


@shared_task
def poll_video_caption_jobs(batch_size=100):
    """Beat (every 2 min): check each in-flight auto caption job; ready -> save the VTT, failed / too old -> mark failed.
    Returns how many tracks were settled."""
    from django.conf import settings
    from django.core.files.base import ContentFile

    from .lectures import LectureError, srt_to_vtt
    from .models import VideoCaptionTrack

    now = timezone.now()
    timeout = timedelta(hours=int(getattr(settings, "LECTURE_CAPTION_JOB_TIMEOUT_HOURS", 6)))

    # queued but never started (broker lost the message / worker crashed before the claim)
    VideoCaptionTrack.objects.filter(source="auto", status="pending", updated_at__lt=now - timedelta(minutes=30)).update(
        status="failed", error="not_started", updated_at=now)

    tracks = list(VideoCaptionTrack.objects.filter(source="auto", status="processing").order_by("updated_at")[:batch_size])
    if not tracks:
        return 0
    bucket = _captions_bucket()
    try:
        import boto3

        region = _captions_region()
        transcribe = boto3.client("transcribe", region_name=region)
        s3 = boto3.client("s3", region_name=region)
    except Exception:
        logger.exception("poll_video_caption_jobs: AWS client unavailable")
        return 0

    settled = 0
    for track in tracks:
        try:
            if not track.job_name:   # claimed, the job never got a name: the start call died
                if now - track.updated_at > timedelta(minutes=15):
                    _fail_caption_track(track.id, "not_started")
                    settled += 1
                continue
            job = transcribe.get_transcription_job(TranscriptionJobName=track.job_name)["TranscriptionJob"]
            state = job.get("TranscriptionJobStatus")
            if state == "COMPLETED":
                try:
                    vtt = srt_to_vtt(_read_caption_vtt(s3, bucket, track.id, track.job_name, job))
                except LectureError:
                    _fail_caption_track(track.id, "no_speech")
                    _delete_caption_input(s3, bucket, track.id)
                    settled += 1
                    continue
                old = track.file.name if track.file else ""
                track.file.save("captions.vtt", ContentFile(vtt.encode("utf-8")), save=False)
                track.status, track.error = "ready", ""
                track.save(update_fields=["file", "status", "error", "updated_at"])
                if old:
                    try:
                        track.file.storage.delete(old)
                    except Exception:
                        logger.warning("could not delete the replaced caption file %s", old, exc_info=True)
                _delete_caption_input(s3, bucket, track.id)
                settled += 1
            elif state == "FAILED":
                _fail_caption_track(track.id, job.get("FailureReason") or "transcribe_failed")
                _delete_caption_input(s3, bucket, track.id)
                settled += 1
            elif now - track.updated_at > timeout:   # QUEUED / IN_PROGRESS for far too long
                _fail_caption_track(track.id, "timeout")
                _delete_caption_input(s3, bucket, track.id)
                settled += 1
        except Exception:
            logger.exception("poll_video_caption_jobs: could not check track %s", track.id)
    if settled:
        logger.info("poll_video_caption_jobs: settled %s caption job(s)", settled)
    return settled
