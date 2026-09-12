"""
post/services.py

⚠️ RENAMED from the uploaded `Services.py` — apps.py does
`import post.signals` (lowercase), and this module gets imported the same
way. On a case-sensitive filesystem (Linux/prod) a capitalized
`Services.py` / `Signals.py` / `Tasks.py` is a DIFFERENT file to Python
than `services.py` / `signals.py` / `tasks.py` — the import would raise
`ModuleNotFoundError` at runtime. It only "worked" by accident on
case-insensitive dev filesystems (Windows/macOS default). Same rename
applied to signals.py and tasks.py.

⚠️ CRITICAL FIX — the uploaded file imported `Hashtag`, `PostHashtag`,
`Like`, `SavedPost`, `Comment` from `.models`. None of these exist.
Real models.py has: Post, PostMedia, PostLike, PostComment, CommentMedia,
PostShare, PostView, PostSave, ChunkedUpload, CommentLike.

- `attach_hashtags()` / the whole Hashtag/PostHashtag idea — REMOVED.
  Hashtags aren't a separate model here: `Post.hashtags` is a plain
  `JSONField(default=list)`, populated directly inside
  `PostCreateSerializer.create()` via `re.findall(r"#(\w+)", content)`
  (see serializers.py, and post_app.md §13.1). There's nothing left for a
  service function to do.
  ⚠️ serializers.py currently STILL has a dead/broken call —
      hashtags = validated_data.get("hashtags")
      if hashtags:
          from .services import attach_hashtags
          attach_hashtags(post, hashtags)
  left over from this same wrong assumption. That will raise
  ImportError/AttributeError the moment anyone creates a post with
  hashtags, since this function no longer exists (and shouldn't).
  Delete those lines from `PostCreateSerializer.create()` —
  `validated_data["hashtags"]` is already what gets saved on the row.
- `toggle_like()` / `toggle_save()` / `add_comment()` / `delete_comment()`
  — REMOVED. These duplicated logic already implemented, correctly,
  directly in views.py / comment_view.py against the real models
  (`PostLike` / `PostSave` / `PostComment`), with counters kept in sync by
  the `@receiver` signals already living in models.py
  (`update_reaction_counts`, `update_saves_count`, etc — see models.py's
  own "NOTE (fix...)" comment on why a second counter-update path is a
  correctness bug, not just redundant work). Nothing in this app calls
  `services.toggle_like` etc. today — keeping them as dead code against
  nonexistent models was the actual problem, not a missing feature.

What's kept below is genuinely additive — logic the views/signals don't
already provide:

- The `core.create_notification` hookup (checklist item 63 / Phase 3's
  hub) — fixed to use `post.user` (the real FK) instead of the
  nonexistent `post.author`, and to take a `PostComment` instance instead
  of the nonexistent `Comment`.
  ⚠️ TASK 11 FIX — this used to probe `core.notifications.create_notification`,
  a module that doesn't exist (the real function is
  `core.services.create_notification`), so the probe's `except ImportError`
  always fired and every call silently fell through to the debug-log
  no-op stub below — no bell row was ever created, even after this
  function started being called. On top of that, the stub's own
  signature (`recipient, actor, verb, target_type, target_id, payload`)
  never matched the real `core.services.create_notification`'s signature
  (`recipient, notif_type, title, message=None, data=None` — see
  `core/tests.py` for confirmed call shapes), so fixing only the import
  path would have raised a `TypeError` on the very first real call.
  Both fixed below: the import now points at `core.services`, and
  `notify_post_liked`/`notify_post_commented` build the
  (notif_type, title, message, data) shape that function actually
  expects, using the new `Notification.NotifType.POST_LIKED`/
  `POST_COMMENTED` choices added in `core/models.py`.
  Now wired: `notify_post_liked` is called from
  `PostReactionAPIView.post()` (only the `status_msg == "liked"` branch)
  and `notify_post_commented` from `CommentCreateAPIView.post()` (only
  for new top-level comments, i.e. `parent is None`) — see those files.
- `share_post_to_conversation()` (checklist item 61) — fixed to use
  `post.user` / `post.content` instead of `post.author` / `post.caption`,
  and `PostShare.objects.get_or_create` instead of `.create()` (the real
  model has `unique_together = ['post', 'user']`, so a second share by
  the same user would raise `IntegrityError`). Also stopped hand-rolling
  a `Post.share_count` F()-update against a field that doesn't exist —
  the real counter is `shares_count`, already kept in sync by
  `update_shares_count` in models.py whenever a `PostShare` row is
  created. Still unwired — no view/url calls this yet (see urls.py: no
  `/share/` route exists). Add one once the `message` app's real
  send-function path is confirmed.
"""
import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TASK 27 — cloud-storage-safe file access for external binaries (ffmpeg,
# and anything else that needs a real local path: virus scanners, image
# processors, etc).
#
# The old `auto_generate_video_thumbnail` read `instance.file.path`
# directly. `.path` only exists for `FileSystemStorage` — it raises
# `NotImplementedError` on `storages.backends.s3.S3Storage` (there is no
# local filesystem path for a remote object), and the old code caught
# that failure with a bare `print()` instead of `logger`, so the moment
# `USE_S3_STORAGE=true` (task 26) was flipped on, thumbnail generation
# started silently no-op-ing for every video with nothing showing up in
# Sentry/logs to say why.
#
# `.open("rb")` + chunked read, below, works identically for every
# storage backend Django/django-storages supports — local disk today,
# S3 after task 26, GCS/Azure if this ever moves again — because it goes
# through the storage API instead of assuming a local filesystem.
# ---------------------------------------------------------------------------
def download_storage_file_to_temp(file_field, suffix=""):
    """Copy a Django FileField's content to a local NamedTemporaryFile,
    regardless of which storage backend is behind it, and return the
    local path. ffmpeg (and most other external binaries) need an actual
    path on disk to read from — they have no concept of S3/GCS.

    Caller owns the returned path and MUST delete it (e.g. in a
    `finally:` block) once done — this function only creates it.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        with file_field.open("rb") as src:
            for chunk in src.chunks():
                tmp.write(chunk)
    finally:
        tmp.close()
    return tmp.name


def generate_video_thumbnail_file(video_path, time_offset="00:00:01", timeout=30):
    """Run ffmpeg against a LOCAL video file path (already downloaded via
    `download_storage_file_to_temp` above — ffmpeg has no concept of S3)
    and return the local path to a generated JPEG thumbnail, or `None` if
    generation failed for any reason. Every failure path is logged via
    `logger` (not `print()`, task 27's other reported gap) so a bad
    upload or a missing ffmpeg binary actually shows up in production
    logs/Sentry instead of silently vanishing.

    Caller owns the returned path and MUST delete it once done, same as
    `download_storage_file_to_temp`.
    """
    if shutil.which("ffmpeg") is None:
        # Infra problem (ffmpeg not installed in the app image/container),
        # not a per-file problem — log once per call so it's loud in
        # aggregated logs, but don't raise: one video with no thumbnail
        # yet is a much better failure mode than crashing the upload.
        logger.error(
            "ffmpeg binary not found on PATH — cannot generate video "
            "thumbnails. Install ffmpeg in the app image/container."
        )
        return None

    thumb_fd, thumb_path = tempfile.mkstemp(suffix=".jpg")
    os.close(thumb_fd)  # ffmpeg writes the actual bytes; we only needed the path

    cmd = [
        "ffmpeg", "-y",
        "-ss", time_offset,
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=480:-1",
        thumb_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg timed out (%ss) generating thumbnail for %s", timeout, video_path)
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None
    except OSError as exc:
        # e.g. ffmpeg binary present in `which` but not actually executable,
        # or disappeared between the check above and this call.
        logger.exception("ffmpeg failed to start for %s: %s", video_path, exc)
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None

    if result.returncode != 0 or not os.path.exists(thumb_path) or os.path.getsize(thumb_path) == 0:
        logger.error(
            "ffmpeg failed generating thumbnail for %s (rc=%s): %s",
            video_path,
            result.returncode,
            result.stderr.decode(errors="replace")[:500] if result.stderr else "",
        )
        if os.path.exists(thumb_path):
            os.unlink(thumb_path)
        return None

    return thumb_path

# ---------------------------------------------------------------------------
# Notification hookup (checklist item 63 / Phase 3's hub).
#
# TASK 11 FIX: `core.notifications` never existed — the real module is
# `core.services`, and its `create_notification()` takes
# `(recipient, notif_type, title, message=None, data=None)`, not the
# `(recipient, actor, verb, target_type, target_id, payload)` shape this
# file's fallback stub used to have. Both are fixed below. The
# `except ImportError` guard is kept (not because `core.services` is
# expected to be missing — it isn't, `core` is a required app — but so
# this app degrades to a logged no-op instead of a hard crash on every
# like/comment in the unlikely event the `core` app isn't installed in a
# given environment, e.g. a stripped-down test settings module).
# ---------------------------------------------------------------------------
try:
    from core.services import create_notification as _create_notification_row
except ImportError:  # pragma: no cover - only if the `core` app isn't installed
    def _create_notification_row(recipient, notif_type, title, message=None, data=None):
        logger.debug(
            "core.services.create_notification not available — skipping notification "
            "(%s: %s -> %s)", notif_type, title, recipient,
        )
        return None


# PRODUCTION FIX — `notify_post_liked`/`notify_post_commented` used to do
# `from core.models import Notification` as an *unguarded* local import.
# If `core` genuinely isn't installed in some environment (the exact case
# the try/except above claims to handle gracefully), that unguarded
# import raised ImportError straight out of every single like and every
# top-level comment — i.e. it crashed the two hottest write paths in this
# app, which is a much worse outcome than the "log + no-op" the module
# docstring promises. Guarded the same way as `_create_notification_row`
# above, so `core` being absent degrades this to a no-op everywhere, not
# just in the create_notification call itself.
try:
    from core.models import Notification as _Notification
except ImportError:  # pragma: no cover - only if the `core` app isn't installed
    _Notification = None


def notify_post_liked(post, actor):
    """Call from PostReactionAPIView.post(), only on the branch where a new
    PostLike was just created (status_msg == 'liked') — not on unlike or
    reaction-change."""
    if post.user_id == actor.id:
        return  # don't notify yourself
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    actor_name = actor.get_full_name() or actor.username
    _create_notification_row(
        post.user,
        _Notification.NotifType.POST_LIKED,
        f"{actor_name} liked your post",
        data={"post_id": str(post.id), "actor_id": str(actor.id)},
    )


def notify_post_commented(post, comment):
    """Call from CommentCreateAPIView.post() after a new top-level
    PostComment is created. `comment` is a PostComment instance."""
    if post.user_id == comment.user_id:
        return
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    actor_name = comment.user.get_full_name() or comment.user.username
    _create_notification_row(
        post.user,
        _Notification.NotifType.POST_COMMENTED,
        f"{actor_name} commented on your post",
        (comment.content or "")[:200],
        data={"post_id": str(post.id), "comment_id": str(comment.id)},
    )


# ---------------------------------------------------------------------------
# Share a post into a chat conversation (checklist item 61).
#
# Reuses the `message` app's existing attachment-message flow rather than
# reimplementing message-sending. The exact function name/signature in
# `message` wasn't visible when this was written — the call below is a
# best-guess based on the message-app's documented flow
# (`MessageViewSet`, attachment upload via `upload_view.py`). ADJUST the
# import + call to match your actual `message/services.py` (or wherever
# send-message logic lives) once you wire this up — everything else here
# stays the same.
# ---------------------------------------------------------------------------
def share_post_to_conversation(post, sender, conversation_id):
    """Raises NotImplementedError with a clear message if the message app's
    send function isn't available yet, so this fails loudly instead of
    silently doing nothing."""
    from .models import PostShare

    try:
        from message.services import send_message  # ADJUST to your real path
    except ImportError as exc:
        raise NotImplementedError(
            "message.services.send_message not found — wire this to your "
            "actual message-sending function (see comment in post/services.py)."
        ) from exc

    first_media = post.media.first()
    attachment_url = first_media.file.url if first_media else None

    message = send_message(
        conversation_id=conversation_id,
        sender=sender,
        text=post.content or "",
        shared_post_id=str(post.id),
        attachment_url=attachment_url,
    )

    # unique_together=['post', 'user'] on PostShare, mirroring how
    # PostLike/PostSave behave — get_or_create so re-sharing the same post
    # doesn't raise IntegrityError. `shares_count` updates itself via the
    # `update_shares_count` signal in models.py; no manual F() needed here.
    PostShare.objects.get_or_create(post=post, user=sender)

    return message