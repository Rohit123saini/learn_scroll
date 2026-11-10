# campus/resource_rules.py
"""
Class resources (PDF / video / documents / links, subject-wise folders) —
the ONE place that knows which files are allowed and how big they may be.

Why not reuse `common.attachment_validators`: that one is made for homework
attachments (pdf + images, 10 MB). Class resources also carry videos, so the
limits are separate and per kind:

  * documents / pdf / images -> `CAMPUS_RESOURCE_MAX_MB`        (default 25)
  * videos                   -> `CAMPUS_RESOURCE_VIDEO_MAX_MB`  (default 200)
  * thumbnails               -> `CAMPUS_RESOURCE_THUMB_MAX_MB`  (default 2)

All three are optional settings (getattr with defaults), so nothing has to
change in settings.py for the models to work.

This module must not import `campus.models` (models import this file).
The kind strings below are the same values as `ClassResource.Kind`.
"""
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

KIND_PDF = "pdf"
KIND_VIDEO = "video"
KIND_DOCUMENT = "document"
KIND_IMAGE = "image"
KIND_LINK = "link"

PDF_EXTENSIONS = ["pdf"]
DOCUMENT_EXTENSIONS = ["doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt"]
IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]
# Phone-friendly containers only; the feed-video transcode (H.264/faststart)
# can be hooked on later without changing this list.
VIDEO_EXTENSIONS = ["mp4", "m4v", "mov", "webm"]

RESOURCE_ALLOWED_EXTENSIONS = PDF_EXTENSIONS + DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS + VIDEO_EXTENSIONS

RESOURCE_MAX_MB = getattr(settings, "CAMPUS_RESOURCE_MAX_MB", 25)
RESOURCE_VIDEO_MAX_MB = getattr(settings, "CAMPUS_RESOURCE_VIDEO_MAX_MB", 200)
RESOURCE_THUMB_MAX_MB = getattr(settings, "CAMPUS_RESOURCE_THUMB_MAX_MB", 2)

resource_extension_validator = FileExtensionValidator(allowed_extensions=RESOURCE_ALLOWED_EXTENSIONS)
thumbnail_extension_validator = FileExtensionValidator(allowed_extensions=IMAGE_EXTENSIONS)


def _ext(name) -> str:
    return os.path.splitext(name or "")[1].lstrip(".").lower()


def kind_for_filename(name):
    """'pdf' / 'document' / 'image' / 'video' for an uploaded file name, else None."""
    ext = _ext(name)
    if ext in PDF_EXTENSIONS:
        return KIND_PDF
    if ext in DOCUMENT_EXTENSIONS:
        return KIND_DOCUMENT
    if ext in IMAGE_EXTENSIONS:
        return KIND_IMAGE
    if ext in VIDEO_EXTENSIONS:
        return KIND_VIDEO
    return None


def max_bytes_for_filename(name) -> int:
    mb = RESOURCE_VIDEO_MAX_MB if _ext(name) in VIDEO_EXTENSIONS else RESOURCE_MAX_MB
    return mb * 1024 * 1024


def validate_resource_size(file):
    limit = max_bytes_for_filename(getattr(file, "name", ""))
    if file.size > limit:
        raise ValidationError(f"File too large — max {limit // (1024 * 1024)}MB for this type.")


def validate_thumbnail_size(file):
    if file.size > RESOURCE_THUMB_MAX_MB * 1024 * 1024:
        raise ValidationError(f"Thumbnail too large — max {RESOURCE_THUMB_MAX_MB}MB.")
