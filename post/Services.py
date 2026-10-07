r"""
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
import hashlib
import io
import logging
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings as django_settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TASK 3 — shared chunk-storage helpers.
#
# Both the comment chunked-upload flow (comment_view.py's
# chunked_upload_init/_chunk/_complete, backed by `ChunkedUpload`) and the
# new post chunked-upload flow (views.py's post_chunked_upload_*, backed by
# `PostChunkedUpload`) write chunks to, and assemble them from, the exact
# same on-disk layout: `MEDIA_ROOT/temp_chunks/<upload_id>/chunk_<index>`.
# That disk logic — write one chunk, list which chunks exist, stitch them
# into a final file — doesn't care whether the upload_id belongs to a
# comment or a post, so it lives here once instead of being copy-pasted a
# second time into views.py. comment_view.py's three functions still own
# everything that DOES differ per-kind (which model to look up, which
# target Post/PostComment to validate against, is_comments_disabled
# checks, building the PostComment/Post row at the end).
# ---------------------------------------------------------------------------

# Same ceiling both chunked_upload_init (comment) and post_chunked_upload_init
# (post) enforce — kept in one place so the two can't quietly drift apart.
CHUNK_UPLOAD_MAX_SIZE = 4 * 1024 * 1024 * 1024  # 4GB


def _chunk_dir(upload_id):
    from django.conf import settings
    return os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id)


def save_uploaded_chunk(upload_id, chunk_index, chunk_file, expected_hash=None):
    """Write one chunk to MEDIA_ROOT/temp_chunks/<upload_id>/chunk_<index>.

    If `expected_hash` is given (the post flow's api_service.dart sends an
    MD5 `chunk_hash` field with every chunk; the comment flow's
    comment_service.dart doesn't send one), the chunk is hashed before
    being written and a ValueError is raised on mismatch — callers turn
    that into a 400 so a corrupted chunk gets rejected and re-sent instead
    of silently baked into the final file.
    """
    chunk_dir = _chunk_dir(upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f'chunk_{chunk_index}')

    if expected_hash:
        data = chunk_file.read()
        actual_hash = hashlib.md5(data).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError("Chunk hash mismatch")
        with open(chunk_path, 'wb') as f:
            f.write(data)
    else:
        with open(chunk_path, 'wb') as f:
            for c in chunk_file.chunks():
                f.write(c)


def list_received_chunks(upload_id):
    """Sorted list of chunk indices already on disk for this upload_id.

    Used by post_chunked_upload_status (TASK 4) and, client-side, by
    ApiService.createPostWithChunkedUpload's resume logic
    (getChunkedUploadStatus -> received_chunks).
    """
    chunk_dir = _chunk_dir(upload_id)
    if not os.path.isdir(chunk_dir):
        return []
    indices = []
    for name in os.listdir(chunk_dir):
        if name.startswith('chunk_'):
            try:
                indices.append(int(name.split('_', 1)[1]))
            except ValueError:
                continue
    return sorted(indices)


def assemble_chunks(upload_id, total_chunks, dest_dir, dest_filename):
    """Stitch the numbered chunk files for `upload_id` into one final file
    at `dest_dir/dest_filename`, verifying every chunk is present first.
    Returns the final absolute path. Raises FileNotFoundError (with the
    missing index in the message) if any chunk is missing — callers turn
    that into a 400, same wording chunked_upload_complete (comment) used
    inline before this was factored out.
    """
    temp_dir = _chunk_dir(upload_id)
    for i in range(total_chunks):
        if not os.path.exists(os.path.join(temp_dir, f'chunk_{i}')):
            raise FileNotFoundError(f"Missing chunk {i}")

    os.makedirs(dest_dir, exist_ok=True)
    final_path = os.path.join(dest_dir, dest_filename)
    with open(final_path, 'wb') as final_file:
        for i in range(total_chunks):
            chunk_path = os.path.join(temp_dir, f'chunk_{i}')
            with open(chunk_path, 'rb') as cf:
                shutil.copyfileobj(cf, final_file, length=1024 * 1024)
            os.remove(chunk_path)
    try:
        os.rmdir(temp_dir)
    except OSError:
        pass
    return final_path


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
# C4-BE — image size variants (320px / 720px) + BlurHash.
#
# Pure functions, no Django models and no storage access in here: they take a
# LOCAL file path (already fetched with `download_storage_file_to_temp` above,
# so this works on S3/GCS exactly like the video thumbnail does) and return
# bytes + a string. `post.tasks.generate_image_variants` does the storage
# read/write around them, which keeps this part trivially unit-testable with
# nothing but Pillow.
#
# Pillow is imported lazily inside the functions: it is already a hard
# dependency (PostMedia.thumbnail is an ImageField), but importing it here at
# module level would make every `from .services import ...` in the app pay for
# it, including views that never touch an image.
# ---------------------------------------------------------------------------
IMAGE_THUMB_WIDTH = 320
IMAGE_MEDIUM_WIDTH = 720
IMAGE_VARIANT_JPEG_QUALITY = 82
# Refuse to decode anything bigger than this many pixels (~50 MP — a 8000x6000
# photo is 48 MP). Pillow's own decompression-bomb check only kicks in at ~179 MP,
# and a single 179 MP RGB decode is ~500 MB of RAM inside a Celery worker.
IMAGE_MAX_PIXELS = 50_000_000
# BlurHash is a ~28 char low-frequency sketch; encoding it from a 32px-wide
# sample is visually identical to encoding the full image and ~1000x cheaper.
_BLURHASH_SAMPLE_SIZE = 32


class ImageVariantError(Exception):
    """The source can't be turned into variants (corrupt file, not actually an
    image, too many pixels). PERMANENT — retrying the same bytes can't help, so
    the Celery task logs and gives up instead of retrying."""


@dataclass
class ImageVariants:
    width: int            # true display size of the original (EXIF rotation applied)
    height: int
    thumb: bytes          # JPEG, <= IMAGE_THUMB_WIDTH wide
    medium: "bytes | None"  # JPEG, <= IMAGE_MEDIUM_WIDTH wide; None for animated images
    blurhash: str


_BLURHASH_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz#$%*+,-.:;=?@[]^_{|}~"


def _b83(value, length):
    return "".join(
        _BLURHASH_ALPHABET[(value // 83 ** (length - i)) % 83] for i in range(1, length + 1)
    )


def _srgb_to_linear(channel):
    x = channel / 255.0
    return x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(value):
    v = max(0.0, min(1.0, value))
    if v <= 0.0031308:
        return int(v * 12.92 * 255 + 0.5)
    return int((1.055 * v ** (1 / 2.4) - 0.055) * 255 + 0.5)


def _sign_pow(value, exp):
    return math.copysign(abs(value) ** exp, value)


def encode_blurhash(pixels, width, height, components_x=4, components_y=3):
    """BlurHash (https://blurha.sh) of an image given as a flat, row-major
    sequence of (r, g, b) 0-255 tuples. Straight port of the reference
    algorithm, written out here instead of adding a pip dependency for ~50
    lines (same call task 27 made for ffmpeg-python). Keep the sample small
    (see `_BLURHASH_SAMPLE_SIZE`) — this is O(width * height * components)."""
    if not (1 <= components_x <= 9 and 1 <= components_y <= 9):
        raise ValueError("BlurHash components must be between 1 and 9")
    if width * height != len(pixels):
        raise ValueError("pixels length does not match width * height")

    linear = [(_srgb_to_linear(r), _srgb_to_linear(g), _srgb_to_linear(b)) for r, g, b in pixels]
    cos_x = [[math.cos(math.pi * i * x / width) for x in range(width)] for i in range(components_x)]
    cos_y = [[math.cos(math.pi * j * y / height) for y in range(height)] for j in range(components_y)]

    factors = []
    for j in range(components_y):
        for i in range(components_x):
            norm = 1.0 if (i == 0 and j == 0) else 2.0
            r = g = b = 0.0
            for y in range(height):
                row = y * width
                cy = cos_y[j][y]
                for x in range(width):
                    basis = norm * cos_x[i][x] * cy
                    lr, lg, lb = linear[row + x]
                    r += basis * lr
                    g += basis * lg
                    b += basis * lb
            scale = 1.0 / (width * height)
            factors.append((r * scale, g * scale, b * scale))

    dc, ac = factors[0], factors[1:]
    result = _b83((components_x - 1) + (components_y - 1) * 9, 1)

    if ac:
        actual_max = max(abs(c) for f in ac for c in f)
        quantised_max = int(max(0, min(82, math.floor(actual_max * 166 - 0.5))))
        max_value = (quantised_max + 1) / 166.0
    else:
        quantised_max, max_value = 0, 1.0
    result += _b83(quantised_max, 1)

    result += _b83(
        (_linear_to_srgb(dc[0]) << 16) + (_linear_to_srgb(dc[1]) << 8) + _linear_to_srgb(dc[2]), 4,
    )

    def quant(v):
        return int(max(0, min(18, math.floor(_sign_pow(v / max_value, 0.5) * 9 + 9.5))))

    for r, g, b in ac:
        result += _b83(quant(r) * 19 * 19 + quant(g) * 19 + quant(b), 2)
    return result


def _flatten_to_rgb(img):
    """RGB copy of `img`. Transparency is flattened onto white — the variants
    are JPEG (no alpha), and a black background under a transparent PNG logo
    looks like a bug."""
    from PIL import Image

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _fit_width(img, max_width):
    """`img` scaled down to at most `max_width` wide (aspect kept). NEVER scales
    up: a 200px-wide upload keeps its 200px instead of being blown up to 320 —
    bigger file, blurrier picture, zero benefit. Returns `img` itself when no
    resize is needed."""
    from PIL import Image

    if img.width <= max_width:
        return img
    height = max(1, round(img.height * max_width / img.width))
    resample = getattr(Image, "Resampling", Image).LANCZOS
    return img.resize((max_width, height), resample, reducing_gap=2.0)


def _jpeg_bytes(img):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=IMAGE_VARIANT_JPEG_QUALITY, optimize=True, progressive=True)
    return buf.getvalue()


def blurhash_for_image(img):
    """BlurHash of a PIL image (any size — it is downsampled first). Portrait
    images get 3x4 components, everything else 4x3, so the longer side always
    gets the extra detail."""
    from PIL import Image

    sample = _flatten_to_rgb(img)
    sample.thumbnail((_BLURHASH_SAMPLE_SIZE, _BLURHASH_SAMPLE_SIZE), getattr(Image, "Resampling", Image).BILINEAR)
    components = (3, 4) if sample.height > sample.width else (4, 3)
    return encode_blurhash(list(sample.getdata()), sample.width, sample.height, *components)


def build_image_variants(local_path):
    """Open the image at `local_path` and return an `ImageVariants` (320px
    thumb, 720px medium, BlurHash, true width/height). Raises
    `ImageVariantError` for anything that can't be processed.

    - EXIF orientation is applied, so a phone photo taken sideways comes out
      upright AND `width`/`height` are the displayed size, not the raw sensor
      size (the client uses them to reserve the right box before the image
      loads, together with the BlurHash).
    - Animated images (GIF / animated WebP / APNG): only a still first-frame
      `thumb` + BlurHash are produced; `medium` is None so the caller keeps
      serving the original animation instead of a frozen frame.
    """
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(local_path) as src:
            if src.width * src.height > IMAGE_MAX_PIXELS:
                raise ImageVariantError(
                    f"image is {src.width}x{src.height} ({src.width * src.height} px), "
                    f"over the {IMAGE_MAX_PIXELS} px limit"
                )
            animated = bool(getattr(src, "is_animated", False))
            upright = ImageOps.exif_transpose(src)  # copy; first frame if animated
            rgb = _flatten_to_rgb(upright)
    except ImageVariantError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
        # SyntaxError: Pillow raises it for some truncated PNG/TIFF streams.
        raise ImageVariantError(f"cannot decode image: {exc}") from exc

    width, height = rgb.size
    medium_img = None if animated else _fit_width(rgb, IMAGE_MEDIUM_WIDTH)
    # Thumb is derived from the (already smaller) medium when there is one:
    # same visual result, much less work than a second resize from 12 MP.
    thumb_img = _fit_width(medium_img if medium_img is not None else rgb, IMAGE_THUMB_WIDTH)

    return ImageVariants(
        width=width,
        height=height,
        thumb=_jpeg_bytes(thumb_img),
        medium=_jpeg_bytes(medium_img) if medium_img is not None else None,
        blurhash=blurhash_for_image(thumb_img),
    )

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
    def _create_notification_row(recipient, notif_type, title, message=None, data=None, actor=None):
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
    from core.notification_batching import (
        batch_title as _batch_title,
        create_batched_notification as _create_batched_notification,
        get_batch_max_age as _get_batch_max_age,
        get_batch_window as _get_batch_window,
        remove_actor_from_batch as _remove_actor_from_batch,
    )
except ImportError:  # pragma: no cover - only if the `core` app isn't installed
    _Notification = None


def hidden_commenter_ids(post_owner_id, viewer_id=None):
    """Issue #2 (RestrictUser) — ids whose comments must be hidden on
    `post_owner_id`'s posts: everyone the post owner has restricted, minus
    the viewer themself (a restricted user keeps seeing their own comments
    exactly as before — restrict is invisible to them). Used by every
    comment-reading endpoint so they all agree."""
    from user_profile.services import restricted_ids_by

    ids = restricted_ids_by(post_owner_id)
    ids.discard(viewer_id)
    # Block (either direction): a blocked person's comments/replies are
    # invisible to me and mine to them, on anyone's post. Hidden at read
    # time, so unblocking brings them back.
    if viewer_id is not None:
        from user_profile.services import blocked_user_ids

        ids |= blocked_user_ids(viewer_id)
    return ids


# ---------------------------------------------------------------------------
# FIX_TASKS.md Task 3 — notifications_screen.dart shows a small post-preview
# thumbnail next to post_liked/post_commented/query_answered rows. That
# needs a media URL in the notification's `data` payload, which wasn't
# there before (only post_id/actor_id/comment_id). Added here rather than
# on the client because notify_post_liked/_commented/_answered below are
# the single place all three of those notif_types get created.
# ---------------------------------------------------------------------------
def _absolute_media_url(field):
    """Best-effort absolute URL for a FileField/ImageField outside a
    request context — notify_post_liked/_commented/_answered run from
    plain view code with no `request` threaded through to them. Same
    fallback chain as `message/user_display.py`'s `get_profile_photo_url`:
    `settings.MEDIA_ABSOLUTE_BASE_URL` if configured, else the bare
    relative URL (still usable if the client already prefixes a base
    host, same as chat file_urls sometimes do)."""
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    base = getattr(django_settings, "MEDIA_ABSOLUTE_BASE_URL", None)
    if base:
        return base.rstrip("/") + url
    return url


def _post_preview_media(post):
    """First attachment (by display_order) for the notification-list
    thumbnail. Returns (media_url, media_type), both None for a text-only
    post. Prefers the generated `thumbnail` (videos — see TASK 27 in
    models.py/tasks.py) and falls back to the raw `file` for images/gifs.
    A video whose thumbnail hasn't been generated yet (Celery task still
    queued/running) comes back as `(None, "video")` — not a crash, not a
    fallback to the raw video file — so the client can show a
    video-placeholder icon instead of trying to render a video file as
    a static image."""
    media = post.media.order_by("display_order").first()
    if media is None:
        return None, None
    if media.thumbnail:
        return _absolute_media_url(media.thumbnail), media.media_type
    if media.media_type in ("image", "gif"):
        return _absolute_media_url(media.file), media.media_type
    return None, media.media_type


def _actor_preview(user):
    """N1-BE — one `actors_preview` item on a batched row: id, username,
    photo (absolute URL, None if the user has no photo)."""
    return {
        "id": user.id,
        "username": getattr(user, "username", None),
        "photo": _absolute_media_url(getattr(user, "profile_photo", None)),
    }


def _push_row(notification):
    """Rich push for a saved bell row (message.push_utils.send_push_for_notification:
    push toggle / muted types / quiet hours honoured there). Never raises."""
    if notification is None:
        return
    try:
        from message.push_utils import send_push_for_notification

        send_push_for_notification(notification)
    except Exception:  # pragma: no cover - push must never break the action
        logger.exception("push for notification %s failed", getattr(notification, "id", None))


def _liked_title(count, actors):
    return _batch_title(actors, count, "liked your post")


def _commented_title(count, actors):
    return _batch_title(actors, count, "commented on your post")


def _story_reacted_title(count, actors):
    return _batch_title(actors, count, "reacted to your story")


def _reposted_title(count, actors):
    return _batch_title(actors, count, "reposted your post")


def notify_post_liked(post, actor, send_push_fn=None, send_push_row_fn=_push_row):
    """Call from PostReactionAPIView.post(), only on the branch where a new
    PostLike was just created (status_msg == 'liked') — not on unlike or
    reaction-change.

    N1-BE — burst likes on one post collapse into ONE bell row ("X and 4
    others liked your post"), key = (post owner, POST_LIKED, post.id). Row
    `data` carries actor_ids / actor_count / actors_preview (first 3: id,
    username, photo); the title is rebuilt on every like. `send_push_fn`
    (optional) fires only for the first like of a batch."""
    if post.user_id == actor.id:
        return  # don't notify yourself
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    media_url, media_type = _post_preview_media(post)
    _create_batched_notification(
        recipient=post.user,
        notif_type=_Notification.NotifType.POST_LIKED,
        actor=actor,  # batching helper skips it if the post owner restricted `actor`
        target_id=post.id,
        title_fn=_liked_title,
        extra_data={
            "post_id": str(post.id),
            "media_url": media_url, "media_type": media_type,
        },
        actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("post_liked"),
        max_age_seconds=_get_batch_max_age("post_liked"),
        send_push_fn=send_push_fn,
        send_push_row_fn=send_push_row_fn,
    )


def unnotify_post_liked(post, actor):
    """N1-BE — call on a real unlike (like row deleted). Drops `actor` from
    the post's OPEN like batch (count falls, row deleted if they were the
    only liker). No-op once the batch window has closed."""
    if _Notification is None or post.user_id == actor.id:
        return
    _remove_actor_from_batch(
        recipient=post.user,
        notif_type=_Notification.NotifType.POST_LIKED,
        actor=actor,
        target_id=post.id,
        title_fn=_liked_title,
        actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("post_liked"),
    )


def notify_post_commented(post, comment, send_push_fn=None, send_push_row_fn=_push_row):
    """Call from CommentCreateAPIView.post() after a new top-level
    PostComment is created. `comment` is a PostComment instance.

    N2-BE — batched per post like likes; the row's `message` and
    `data.comment_id` always point at the LATEST comment so a tap lands on
    it."""
    if post.user_id == comment.user_id:
        return
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    media_url, media_type = _post_preview_media(post)
    _create_batched_notification(
        recipient=post.user,
        notif_type=_Notification.NotifType.POST_COMMENTED,
        actor=comment.user,  # skipped if the post owner restricted the commenter
        target_id=post.id,
        title_fn=_commented_title,
        message_fn=lambda count, actors, latest: (comment.content or "")[:200],
        data_fn=lambda count, actors, latest: {"comment_id": str(comment.id)},
        extra_data={
            "post_id": str(post.id), "comment_id": str(comment.id),
            "media_url": media_url, "media_type": media_type,
        },
        actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("post_commented"),
        max_age_seconds=_get_batch_max_age("post_commented"),
        send_push_fn=send_push_fn,
        send_push_row_fn=send_push_row_fn,
    )


def notify_story_reacted(story, actor, emoji=None, send_push_fn=None, send_push_row_fn=_push_row):
    """N2-BE — call right after a NEW story reaction is saved. Batched per
    story (key = story.id). ⚠️ Assumes `NotifType.STORY_REACTION` exists in
    core/models.py; if it doesn't (enum + migration needed), this logs and
    does nothing rather than crash the reaction request."""
    if story.user_id == actor.id:
        return
    if _Notification is None:
        return
    notif_type = getattr(_Notification.NotifType, "STORY_REACTION", None)
    if notif_type is None:
        logger.warning("NotifType.STORY_REACTION missing — story reaction not notified.")
        return

    media_url = _absolute_media_url(story.media) if story.media_type == "image" else None
    _create_batched_notification(
        recipient=story.user,
        notif_type=notif_type,
        actor=actor,
        target_id=story.id,
        title_fn=_story_reacted_title,
        data_fn=lambda count, actors, latest: {"emoji": emoji} if emoji else {},
        extra_data={
            "story_id": str(story.id),
            "media_url": media_url, "media_type": story.media_type,
        },
        actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("story_reaction"),
        max_age_seconds=_get_batch_max_age("story_reaction"),
        send_push_fn=send_push_fn,
        send_push_row_fn=send_push_row_fn,
    )


def unnotify_story_reacted(story, actor):
    """Task 3.4 - the actor removed their reaction: drop them from the story's
    OPEN reaction batch (row deleted if they were the only one)."""
    if _Notification is None or story.user_id == actor.id:
        return
    notif_type = getattr(_Notification.NotifType, "STORY_REACTION", None)
    if notif_type is None:
        return
    _remove_actor_from_batch(
        recipient=story.user, notif_type=notif_type, actor=actor, target_id=story.id,
        title_fn=_story_reacted_title, actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("story_reaction"),
    )


def notify_post_reposted(repost, send_push_fn=None, send_push_row_fn=_push_row):
    """Task 3.4 - call after a NEW repost row is created. Batched per ORIGINAL
    post ("X and 2 others reposted your post"); `data.post_id` is the original
    so a tap opens it. Own reposts never notify."""
    original = repost.original_post
    if original is None or original.user_id == repost.user_id:
        return
    if _Notification is None:
        return
    notif_type = getattr(_Notification.NotifType, "POST_REPOSTED", None)
    if notif_type is None:
        return

    media_url, media_type = _post_preview_media(original)
    _create_batched_notification(
        recipient=original.user,
        notif_type=notif_type,
        actor=repost.user,
        target_id=original.id,
        title_fn=_reposted_title,
        data_fn=lambda count, actors, latest: {"repost_id": str(repost.id)},
        extra_data={
            "post_id": str(original.id), "repost_id": str(repost.id),
            "media_url": media_url, "media_type": media_type,
        },
        actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("post_reposted"),
        max_age_seconds=_get_batch_max_age("post_reposted"),
        send_push_fn=send_push_fn,
        send_push_row_fn=send_push_row_fn,
    )


def unnotify_post_reposted(repost):
    """Task 3.4 - the repost was deleted: drop the actor from the original's
    OPEN repost batch."""
    original = repost.original_post
    if _Notification is None or original is None or original.user_id == repost.user_id:
        return
    notif_type = getattr(_Notification.NotifType, "POST_REPOSTED", None)
    if notif_type is None:
        return
    _remove_actor_from_batch(
        recipient=original.user, notif_type=notif_type, actor=repost.user, target_id=original.id,
        title_fn=_reposted_title, actor_preview_fn=_actor_preview,
        window_seconds=_get_batch_window("post_reposted"),
    )


def notify_post_answered(post, answer):
    """TASK G6 — call from PostAnswerListCreateAPIView.create() after a
    new PostAnswer is saved. Reuses core's existing QUERY_ANSWERED notif
    type (already used by the message-app "Doubt Queue" group feature)
    rather than adding a near-duplicate new one."""
    if post.user_id == answer.user_id:
        return  # don't notify yourself for answering your own doubt
    if _Notification is None:
        return  # `core` app not installed — nothing to notify with

    actor_name = answer.user.get_full_name() or answer.user.username
    media_url, media_type = _post_preview_media(post)
    _create_notification_row(
        post.user,
        _Notification.NotifType.QUERY_ANSWERED,
        f"{actor_name} answered your doubt",
        (answer.content or "")[:200],
        data={
            "post_id": str(post.id), "answer_id": str(answer.id),
            "media_url": media_url, "media_type": media_type,
        },
        actor=answer.user,  # lets core skip it if the post owner restricted the answerer
    )


def notify_story_mentions(story, mentioned_users):
    """STORIES UPGRADE - PART 2a. Call once right after a story with @mention
    stickers has been created. One in-app (bell) row per tagged person, type
    `story_mention`; tapping it opens that story (the client reads
    `data.story_id`). Never notifies the author, and a person the author is
    restricted by / has restricted is skipped by core.create_notification (its
    `actor=` check), same as likes and comments.

    `media_url` is only set for image stories (a video has no thumbnail) so
    the notification list can show a small preview like it does for posts."""
    if _Notification is None:
        return  # `core` app not installed - nothing to notify with

    actor = story.user
    actor_name = actor.get_full_name() or actor.username
    media_url = _absolute_media_url(story.media) if story.media_type == "image" else None

    seen = set()
    for user in mentioned_users:
        if user is None or user.id == actor.id or user.id in seen:
            continue
        seen.add(user.id)
        _create_notification_row(
            user,
            _Notification.NotifType.STORY_MENTION,
            f"{actor_name} mentioned you in their story",
            (story.caption or "")[:200],
            data={
                "story_id": str(story.id), "actor_id": str(actor.id),
                "media_url": media_url, "media_type": story.media_type,
            },
            actor=actor,
        )


# ---------------------------------------------------------------------------
# Share a post into a chat conversation (checklist item 61 — "internal
# share" / forward-to-chat, NOT the OS share sheet singlepost.dart already
# wires up via `share_plus`, which never touches the backend).
#
# ⚠️ FIX — this used to import `send_message` from `message.services`,
# which doesn't exist anywhere in that module (see message/services.py) —
# every call raised ImportError, and on top of that nothing called this
# function and no URL routed to it either (see PostShareAPIView / urls.py
# for that other half of the fix). The real send-and-broadcast function is
# `create_message_and_broadcast` — the same one every other message-
# creating codepath in this app (REST + WebSocket) already goes through,
# so a shared post gets identical delivery (per-recipient unread counts,
# MessageStatus rows, the `chat_<id>` WS broadcast, push) to a normal
# message instead of a second, parallel send path.
# ---------------------------------------------------------------------------
def share_post_to_conversation(post, sender, conversation_id):
    """Forwards `post` into an existing conversation as a `post_share`
    message. Raises `Conversation.DoesNotExist` for a bad/foreign
    conversation_id, and `PermissionDenied` if `sender` isn't a
    participant of it — PostShareAPIView (views.py) turns both into the
    right HTTP status."""
    from message.models import Conversation, ConversationParticipant, MessageType
    from message.services import create_message_and_broadcast

    from .models import PostShare

    conversation = Conversation.objects.get(id=conversation_id)
    if not ConversationParticipant.objects.filter(conversation=conversation, user=sender).exists():
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied("You're not a participant in this conversation.")

    first_media = post.media.first()
    thumbnail_url = None
    if first_media is not None:
        thumbnail_url = first_media.thumbnail.url if first_media.thumbnail else first_media.file.url

    message = create_message_and_broadcast(
        conversation=conversation,
        sender=sender,
        message_type=MessageType.POST_SHARE,
        text=post.content or "",
        thumbnail_url=thumbnail_url,
        # No new Message columns needed for this — `meta` is exactly what
        # it's for ("file ka extra data", see the field's comment on
        # Message in message/models.py). The chat UI's `post_share`
        # branch (wherever MessageType.storyReply is already handled on
        # the client) reads `shared_post_id` back out to render the
        # tappable post-preview card and to deep-link into the post.
        meta={
            "shared_post_id": str(post.id),
            "shared_post_author": post.user.username,
            "shared_post_title": post.title,
        },
    )

    # unique_together=['post', 'user'] on PostShare, mirroring how
    # PostLike/PostSave behave — get_or_create so re-sharing the same post
    # doesn't raise IntegrityError. `shares_count` updates itself via the
    # `update_shares_count` signal in models.py; no manual F() needed here.
    PostShare.objects.get_or_create(post=post, user=sender)

    return message


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 1: one place that applies "Not interested"
# (PostHide) and "Mute this account" (MutedAccount) to a Post queryset.
# Sub-selects, not python id sets, so the cost doesn't grow with the size of
# the user's hide/mute lists.
# ---------------------------------------------------------------------------
def exclude_hidden_and_muted(qs, user):
    """Drop, for `user`: posts they hid (and reposts OF a hidden post), and
    posts written by accounts they muted (also reposts whose ORIGINAL author
    is muted). Anonymous / missing user -> qs unchanged."""
    if not user or not getattr(user, 'pk', None):
        return qs
    from user_profile.models import BlockUser

    from .models import MutedAccount, PostHide

    hidden = PostHide.objects.filter(user=user).values('post_id')
    muted = MutedAccount.objects.filter(user=user).values('muted_user_id')
    # Block, EITHER direction, as sub-selects (no extra round trip). This
    # runs on every request — including when a frozen feed snapshot is
    # re-hydrated — so a block takes effect on the very next page instead of
    # after the snapshot's 15-minute TTL. Reposts whose ORIGINAL author is in
    # a block relationship are dropped too.
    i_blocked = BlockUser.objects.filter(blocker=user).values('blocked_id')
    blocked_me = BlockUser.objects.filter(blocked=user).values('blocker_id')
    return (
        qs.exclude(pk__in=hidden)
        .exclude(original_post_id__in=hidden)
        .exclude(user_id__in=muted)
        .exclude(original_post__user_id__in=muted)
        .exclude(user_id__in=i_blocked)
        .exclude(user_id__in=blocked_me)
        .exclude(original_post__user_id__in=i_blocked)
        .exclude(original_post__user_id__in=blocked_me)
    )


# ---------------------------------------------------------------------------
# FEED FEEDBACK CONTROLS - PART 2: "Show fewer like this".
# `apply_show_fewer` = hide the post (Part 1's PostHide) AND add a decaying
# negative weight (FeedFeedback) to what the user chose to dampen: the post's
# category, one of its hashtags and/or its author. The weights become minus
# points in feed_mix.build_pool_ids (recommended + trending only).
# ---------------------------------------------------------------------------
MAX_SHOW_FEWER_TARGETS = 5


class ShowFewerError(ValueError):
    """A target the caller asked to dampen doesn't fit the post (-> HTTP 400)."""


def normalize_hashtag(tag):
    """'#Python ' -> 'python' (same normalisation PostCreateSerializer applies)."""
    return str(tag or "").strip().lstrip("#").lower()


def resolve_feedback_key(post, kind, raw_key=None):
    """The FeedFeedback.key for (post, kind).

    category / author are DERIVED from the post (a client can't dampen a
    category the post isn't in), a supplied `raw_key` is ignored for them.
    hashtag needs `raw_key` and it must be one of the post's own hashtags."""
    from .models import FeedFeedback

    if kind == FeedFeedback.Kind.CATEGORY:
        return post.category
    if kind == FeedFeedback.Kind.AUTHOR:
        return str(post.user_id)
    if kind == FeedFeedback.Kind.HASHTAG:
        tag = normalize_hashtag(raw_key)
        if not tag:
            raise ShowFewerError("Choose which hashtag you want to see fewer of.")
        if tag not in {normalize_hashtag(t) for t in (post.hashtags or [])}:
            raise ShowFewerError(f"#{tag} is not a hashtag of this post.")
        return tag
    raise ShowFewerError("Unknown target kind.")


def prune_stale_feedback(user, now=None):
    """Delete the user's FeedFeedback rows that decayed for good (older than
    `feed_mix.feedback_horizon_days`; they can't reach `min_effective` any
    more). Keeps the table bounded without a cron job; called on every write."""
    from datetime import timedelta

    from django.utils import timezone

    from . import feed_mix
    from .models import FeedFeedback

    horizon = feed_mix.feedback_horizon_days(feed_mix.get_feedback_config())
    if horizon is None:
        return 0
    now = now or timezone.now()
    deleted, _ = FeedFeedback.objects.filter(user=user, updated_at__lt=now - timedelta(days=horizon)).delete()
    return deleted


def record_feed_feedback(user, kind, key, now=None):
    """Add ONE "show fewer" step to (user, kind, key) and return the row.

    New row: weight = step. Existing row: its weight is first decayed to `now`
    and then bumped by `step` (capped at `max_weight`), so tapping again after
    months starts from what is really left, and tapping repeatedly can't grow
    the penalty without bound. Last write wins on a concurrent double-tap."""
    from django.utils import timezone

    from . import feed_mix
    from .models import FeedFeedback

    cfg = feed_mix.get_feedback_config()
    now = now or timezone.now()
    row, created = FeedFeedback.objects.get_or_create(
        user=user, kind=kind, key=key,
        defaults={"weight": feed_mix.bump_weight(0.0, cfg["step"], cfg["max_weight"])},
    )
    if not created:
        age_days = (now - row.updated_at).total_seconds() / 86400.0
        decayed = feed_mix.decay_weight(row.weight, age_days, cfg["half_life_days"])
        row.weight = feed_mix.bump_weight(decayed, cfg["step"], cfg["max_weight"])
        row.save(update_fields=["weight", "updated_at"])  # auto_now needs updated_at listed
    return row


def apply_show_fewer(user, post, targets, reason):
    """"Show fewer like this": hide `post` + dampen `targets`.

    `targets` = iterable of (kind, raw_key). All targets are validated BEFORE
    anything is written, so a bad hashtag never leaves a half-applied request.
    Returns (hide, hide_created, [FeedFeedback, ...]) - one row per distinct
    (kind, key), in request order. Raises ShowFewerError."""
    from django.db import transaction

    from .models import PostHide

    resolved = []
    for kind, raw_key in targets:
        item = (kind, resolve_feedback_key(post, kind, raw_key))
        if item not in resolved:
            resolved.append(item)
    if not resolved:
        raise ShowFewerError("Choose what you want to see fewer of.")

    with transaction.atomic():
        hide, hide_created = PostHide.objects.get_or_create(user=user, post=post, defaults={"reason": reason})
        if not hide_created and hide.reason != reason:
            hide.reason = reason
            hide.save(update_fields=["reason"])
        prune_stale_feedback(user)
        rows = [record_feed_feedback(user, kind, key) for kind, key in resolved]
    return hide, hide_created, rows


# ---------------------------------------------------------------------------
# TASK 1.1-BE — guaranteed media dimensions / duration.
#
# Before this, `PostMedia.width/height/duration_seconds` were only ever filled
# for IMAGES, and only after the Celery `generate_image_variants` task had
# run; videos/audio never got any of them (nothing called ffprobe), so the
# feed could not reserve the right frame size and reels had no duration.
#
# Everything here is split the same way the image-variant code above is:
#   - pure parsing (`parse_ffprobe_output`, `probe_image_size`) — unit-testable
#     with no ffmpeg binary and no database;
#   - `probe_media_file(local_path, media_type)` — runs the probe on a LOCAL
#     file;
#   - `fill_media_metadata(media)` — storage/DB glue used by the post_save
#     receiver (in-request, local files only), the Celery task `probe_media_
#     metadata` (any storage) and `manage.py backfill_media_dimensions`.
# It only ever FILLS blanks (None/0) — a value a client or another code path
# already stored is never overwritten.
# ---------------------------------------------------------------------------
FFPROBE_TIMEOUT_SECONDS = 15
PROBE_MEDIA_TYPES = ("image", "gif", "video", "audio")


def media_metadata_missing(media):
    """True if `media` (a PostMedia) still lacks a value we can probe for."""
    kind = media.media_type
    if kind in ("image", "gif"):
        return not media.width or not media.height
    if kind == "video":
        return not media.width or not media.height or not media.duration_seconds
    if kind == "audio":
        return not media.duration_seconds
    return False


def _stream_rotation(stream):
    """Display rotation in degrees (0/90/180/270) from an ffprobe stream.
    Phone videos are usually stored landscape + a 90deg rotation flag; the
    frame the viewer actually sees is portrait."""
    rot = (stream.get("tags") or {}).get("rotate")
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            rot = side["rotation"]
    try:
        return int(round(float(rot))) % 360
    except (TypeError, ValueError):
        return 0


def _positive_int(value):
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_ffprobe_output(data, media_type):
    """Pure: ffprobe `-print_format json -show_streams -show_format` output ->
    {"width", "height", "duration_seconds"} (only keys that are known).

    - width/height are the DISPLAYED size (rotation flag applied) and are only
      returned for `video` (an audio file's cover-art "video stream" is not a
      frame).
    - duration is rounded to whole seconds with a floor of 1 — the column is a
      PositiveIntegerField and 0 is treated as "unknown" everywhere.
    """
    out = {}
    streams = data.get("streams") or []

    if media_type == "video":
        video = next(
            (
                s for s in streams
                if s.get("codec_type") == "video"
                and not (s.get("disposition") or {}).get("attached_pic")
            ),
            None,
        )
        if video:
            width, height = _positive_int(video.get("width")), _positive_int(video.get("height"))
            if width and height:
                if _stream_rotation(video) in (90, 270):
                    width, height = height, width
                out["width"], out["height"] = width, height

    if media_type in ("video", "audio"):
        candidates = [(data.get("format") or {}).get("duration")]
        candidates += [s.get("duration") for s in streams]
        for raw in candidates:
            try:
                seconds = float(raw)
            except (TypeError, ValueError):
                continue
            if seconds > 0:
                out["duration_seconds"] = max(1, int(round(seconds)))
                break
    return out


def probe_image_size(source):
    """(width, height) of an image path/file object, EXIF orientation applied
    (a portrait phone photo stored sideways reports its displayed size).
    Reads the header only. Raises on a corrupt / non-image file."""
    from PIL import Image

    with Image.open(source) as img:
        width, height = img.size
        try:
            orientation = img.getexif().get(0x0112)
        except Exception:
            orientation = None
    if orientation in (5, 6, 7, 8):
        width, height = height, width
    return width, height


def _run_ffprobe(path, timeout=FFPROBE_TIMEOUT_SECONDS):
    """Run ffprobe on a LOCAL path; parsed JSON dict or None (always logged)."""
    import json

    if shutil.which("ffprobe") is None:
        logger.error(
            "ffprobe binary not found on PATH — cannot read video/audio "
            "dimensions or duration. It ships in the same package as ffmpeg."
        )
        return None
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        logger.error("ffprobe timed out (%ss) for %s", timeout, path)
        return None
    except OSError as exc:
        logger.exception("ffprobe failed to start for %s: %s", path, exc)
        return None
    if result.returncode != 0:
        logger.error(
            "ffprobe failed for %s (rc=%s): %s", path, result.returncode,
            result.stderr.decode(errors="replace")[:500] if result.stderr else "",
        )
        return None
    try:
        return json.loads(result.stdout.decode("utf-8", "replace"))
    except ValueError:
        logger.error("ffprobe returned non-JSON output for %s", path)
        return None


def probe_media_file(local_path, media_type):
    """Probe a LOCAL file -> {"width","height","duration_seconds"} (known keys
    only; {} when nothing could be read)."""
    if media_type in ("image", "gif"):
        width, height = probe_image_size(local_path)
        return {"width": width, "height": height} if width and height else {}
    if media_type in ("video", "audio"):
        data = _run_ffprobe(local_path)
        return parse_ffprobe_output(data, media_type) if data else {}
    return {}


def fill_media_metadata(media, *, allow_download=True, raise_errors=False):
    """Fill the blank width/height/duration_seconds of a PostMedia and return
    the dict of fields written ({} if nothing changed).

    allow_download=False -> only probes files that already sit on local disk
    (FileSystemStorage) — what the in-request post_save receiver uses so an
    upload never waits on a remote download. S3/GCS rows are filled by the
    Celery task / backfill command instead (allow_download=True).
    raise_errors=True (task) lets a transient storage error propagate so
    Celery can retry; otherwise errors are logged and swallowed."""
    if not media.file or not media_metadata_missing(media):
        return {}

    local_path, downloaded = None, False
    try:
        try:
            local_path = media.file.path  # FileSystemStorage only
        except (NotImplementedError, ValueError, AttributeError):
            local_path = None
        if local_path is None or not os.path.exists(local_path):
            if not allow_download:
                return {}
            _, ext = os.path.splitext(media.file.name)
            local_path = download_storage_file_to_temp(media.file, suffix=ext)
            downloaded = True
        found = probe_media_file(local_path, media.media_type)
    except Exception:
        logger.exception("fill_media_metadata: probing failed for PostMedia %s", media.pk)
        if raise_errors:
            raise
        return {}
    finally:
        if downloaded and local_path:
            try:
                os.unlink(local_path)
            except OSError:
                pass

    updates = {k: v for k, v in found.items() if v and not getattr(media, k)}
    if not updates:
        return {}
    from .models import PostMedia  # local import, same as the other helpers in this module

    # .update(): a row deleted meanwhile affects 0 rows instead of raising.
    PostMedia.objects.filter(pk=media.pk).update(**updates)
    for key, value in updates.items():
        setattr(media, key, value)
    return updates
