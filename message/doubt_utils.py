# message/doubt_utils.py
#
# T1.1 — Doubt photo attachment: upload validation + sanitising.
#
# Student ka doubt-photo sabse zyada minors ke phone se aata hai (camera se
# seedha), isliye raw file kabhi storage me nahi jaati:
#   1. size cap            (settings.DOUBT_IMAGE_MAX_BYTES, default 5 MB)
#   2. magic-bytes sniff   (JPEG / PNG / WEBP only — extension/Content-Type
#                           pe bharosa nahi, `ai_service.sniff_image_mime`
#                           wahi helper jo Ask-AI photo flow use karta hai)
#   3. real decode         (Pillow `Image.open().load()` — truncated/forged
#                           file yahin 400 ban jaati hai)
#   4. re-encode           (EXIF — GPS location, device model — strip hota
#                           hai; orientation pehle apply, phir >2048px
#                           downscale taaki storage/bandwidth na phoole)
#
# Plain `ValueError` raise karta hai (DRF-free, services.py jaisa convention);
# `DoubtCreateSerializer.validate_image` ise `serializers.ValidationError`
# me convert karta hai.

import io
import logging
import uuid

from django.conf import settings
from django.core.files.base import ContentFile

from .ai_service import MAX_DOUBT_IMAGE_BYTES, sniff_image_mime

logger = logging.getLogger(__name__)

#: Longest side after downscale. Handwriting/diagram padhne ke liye kaafi,
#: par ek 12MP camera photo (4000px) ko ~10x chhota kar deta hai.
DOUBT_IMAGE_MAX_SIDE = getattr(settings, "DOUBT_IMAGE_MAX_SIDE", 2048)

_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_FORMAT_BY_MIME = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}


def max_doubt_image_bytes() -> int:
    return int(getattr(settings, "DOUBT_IMAGE_MAX_BYTES", MAX_DOUBT_IMAGE_BYTES))


def sanitize_doubt_image(uploaded) -> ContentFile:
    """`uploaded`: Django `UploadedFile`. Returns a clean `ContentFile`
    (named `<uuid>.<ext>`) ready to assign to `DoubtQuestion.image`.

    Raises `ValueError` with a user-facing message on any problem."""
    size = getattr(uploaded, "size", None)
    if size is None or size <= 0:
        raise ValueError("Photo khaali hai.")
    limit = max_doubt_image_bytes()
    if size > limit:
        raise ValueError(f"Photo {limit // (1024 * 1024)}MB se badi nahi ho sakti.")

    head = uploaded.read(16)
    uploaded.seek(0)
    mime = sniff_image_mime(head)
    if mime is None:
        raise ValueError("Sirf JPEG, PNG ya WEBP photo allowed hai.")

    try:
        from PIL import Image, ImageOps
    except ImportError:  # pragma: no cover - Pillow is a hard project dependency (post app)
        logger.error("Pillow missing — doubt photo upload disabled")
        raise ValueError("Photo upload abhi available nahi hai.")

    try:
        img = Image.open(uploaded)
        img.load()  # full decode — truncated / forged files fail here
    except Exception:
        raise ValueError("Ye photo padhi nahi ja saki — dusri photo try karo.")

    # EXIF orientation pehle apply (phone photos), uske baad re-encode
    # saari metadata (GPS etc.) hata deta hai.
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    if max(img.size) > DOUBT_IMAGE_MAX_SIDE:
        img.thumbnail((DOUBT_IMAGE_MAX_SIDE, DOUBT_IMAGE_MAX_SIDE))

    fmt = _FORMAT_BY_MIME[mime]
    save_kwargs = {}
    if fmt == "JPEG":
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        save_kwargs = {"quality": 85, "optimize": True}
    elif fmt == "WEBP":
        save_kwargs = {"quality": 85}
    else:  # PNG
        save_kwargs = {"optimize": True}

    buf = io.BytesIO()
    try:
        img.save(buf, format=fmt, **save_kwargs)
    except Exception:
        raise ValueError("Ye photo process nahi ho saki — dusri photo try karo.")

    return ContentFile(buf.getvalue(), name=f"{uuid.uuid4().hex}.{_EXT_BY_MIME[mime]}")
