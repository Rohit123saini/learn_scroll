# common/attachment_validators.py
"""
Shared attachment validation — used by any app's FileField that accepts a
user-uploaded document/image (originally lived in `testseries/models.py`,
where it validated `Question.attachment` / `QuestionResponse.
answer_attachment`; `assignment` reuses the exact same rules instead of
redefining them).

Behavior is UNCHANGED from the original `testseries` version — same
settings keys, same defaults, same error message — this is a pure move,
not a rewrite:
  1. FileField(validators=[...]) — enforced by full_clean().
  2. Serializer-level validate_attachment() hooks — clean 400 on a bad
     upload that goes through DRF.
  3. View-level checks for uploads read straight off request.FILES
     (never touch a serializer) — same clean-400 treatment before any
     DB write happens.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator

ATTACHMENT_ALLOWED_EXTENSIONS = getattr(
    settings, "TESTSERIES_ATTACHMENT_EXTENSIONS", ["pdf", "jpg", "jpeg", "png", "webp"]
)
ATTACHMENT_MAX_SIZE_MB = getattr(settings, "TESTSERIES_ATTACHMENT_MAX_MB", 10)

attachment_extension_validator = FileExtensionValidator(allowed_extensions=ATTACHMENT_ALLOWED_EXTENSIONS)


def validate_attachment_size(file):
    max_bytes = ATTACHMENT_MAX_SIZE_MB * 1024 * 1024
    if file.size > max_bytes:
        raise ValidationError(f"File too large — max {ATTACHMENT_MAX_SIZE_MB}MB.")