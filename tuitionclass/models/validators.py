# tuitionclass/models/validators.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible


# ---------------------------------------------------------------------------
# NOTE (fix): none of the FileField/ImageField columns below (cover_image,
# class materials, assigments attachments/submissions, certificate files)
# had any size limit. An authenticated user could upload an arbitrarily
# large file to any of these — slow requests, unbounded storage/bandwidth
# cost, and an easy denial-of-service vector against disk/object-storage
# quota. @deconstructible so Django can serialize this into migrations
# (a plain closure/lambda can't be).
#
# NOTE (fix — file-type spoofing): the four plain FileFields below (material,
# assigments attachment, assigments submission, certificate) had NO
# extension restriction at all beyond the size cap — a student could upload
# a renamed .exe/.php/.js/.sh as their "assigments submission" or a teacher
# could do the same as "class material", which then sits in storage and
# gets served back to every other student/teacher who opens it (a stored-
# malware / drive-by vector, not something the size cap touches at all).
# cover_image is already safe on this front for free: Django's ImageField
# actually opens the upload with Pillow to confirm it's a real, decodable
# image (a renamed .exe fails that outright, and SVG is rejected too since
# Pillow can't decode it — closing the classic SVG/XSS upload vector as a
# side effect). The other four are plain FileFields with no such built-in
# check, so each now also gets a FileExtensionValidator scoped to what that
# field is actually supposed to hold — safelist, not a blocklist, so a
# brand-new dangerous extension can't slip through just because nobody
# thought to blocklist it yet.
# ---------------------------------------------------------------------------
DOCUMENT_MEDIA_EXTENSIONS = [
    "pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
    "png", "jpg", "jpeg", "gif", "webp",
    "mp4", "mov", "webm",
    "zip",
]


@deconstructible
class MaxFileSizeValidator:
    def __init__(self, max_mb: int):
        self.max_mb = max_mb

    def __call__(self, file):
        limit_bytes = self.max_mb * 1024 * 1024
        if file.size > limit_bytes:
            raise ValidationError(f"File too large — max {self.max_mb}MB.")

    def __eq__(self, other):
        return isinstance(other, MaxFileSizeValidator) and self.max_mb == other.max_mb
