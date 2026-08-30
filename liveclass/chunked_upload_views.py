"""
liveclass/chunked_upload_views.py

Production chunked-upload endpoints for liveclass. Save this as a NEW file
next to models.py/views.py/urls.py (do not paste into views.py — kept
separate on purpose, same pattern the reference app used with
comment_view.py, so this can be reviewed/tested/modified in isolation).

Four purposes supported (see ChunkedUpload.Purpose in models.py):
    cover_image             -> updates Classroom.cover_image (must already exist)
    material                 -> creates a new ClassMaterial row
    assignment_attachment   -> updates Assignment.attachment (must already exist)
    submission_file          -> creates a new AssignmentSubmission row

Design notes (read before changing anything):
  - Temp chunks are written OUTSIDE MEDIA_ROOT (settings.CHUNKED_UPLOAD_TMP_ROOT)
    so a half-uploaded file is never reachable via the /media/ URL.
  - Every purpose is permission-checked TWICE: once at init() (so we don't
    accept a client's bandwidth for an upload that can never succeed) and
    again at complete() (defense in depth — access can legitimately change
    between init and complete, e.g. a pass expiring mid-upload, or being
    banned).
  - complete() claims the row with an atomic conditional UPDATE
    (status=IN_PROGRESS -> PROCESSING) before doing any file I/O, so two
    concurrent complete() calls for the same upload_id can't both assemble
    the file / create two DB rows.
  - The final assembled file is re-validated against the SAME
    MaxFileSizeValidator / FileExtensionValidator the model field already
    declares — chunking never bypasses those checks, it just spreads the
    upload across many small requests.
  - cover_image additionally gets a Pillow decode check at complete() (same
    protection Django's ImageField gives non-chunked uploads for free) since
    chunked assembly writes straight to disk, bypassing ImageField's normal
    upload-time validation path.
"""

import os
import re
import shutil
import uuid
import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes, parser_classes, throttle_classes
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from .models import (
    Assignment,
    AssignmentSubmission,
    ChunkedUpload,
    Classroom,
    ClassMaterial,
    ClassSession,
    DOCUMENT_MEDIA_EXTENSIONS,
)
from .views import _can_manage_classroom, _can_view_classroom_internals

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-purpose limits — kept in lockstep with the actual model field
# validators (Classroom.cover_image=5MB, ClassMaterial.file=100MB,
# Assignment.attachment=50MB, AssignmentSubmission.file=50MB). If those
# validators ever change, update this dict in the same pass.
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = ["png", "jpg", "jpeg", "webp", "gif"]

PURPOSE_LIMITS = {
    ChunkedUpload.Purpose.COVER_IMAGE: {"max_mb": 5, "extensions": IMAGE_EXTENSIONS},
    ChunkedUpload.Purpose.MATERIAL: {"max_mb": 100, "extensions": DOCUMENT_MEDIA_EXTENSIONS},
    ChunkedUpload.Purpose.ASSIGNMENT_ATTACHMENT: {"max_mb": 50, "extensions": DOCUMENT_MEDIA_EXTENSIONS},
    ChunkedUpload.Purpose.SUBMISSION_FILE: {"max_mb": 50, "extensions": DOCUMENT_MEDIA_EXTENSIONS},
}

# Bounds independent of purpose — sanity caps against abuse, not business logic.
MAX_SINGLE_CHUNK_BYTES = 8 * 1024 * 1024  # 8MB — comfortably under the 10MB
                                            # DATA_UPLOAD_MAX_MEMORY_SIZE cap
MAX_TOTAL_CHUNKS = 5000
MAX_IN_PROGRESS_UPLOADS_PER_USER = 5

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _tmp_root() -> str:
    root = getattr(settings, "CHUNKED_UPLOAD_TMP_ROOT", None)
    if not root:
        # Fail loudly rather than silently falling back into MEDIA_ROOT,
        # which would defeat the whole point (see module docstring).
        raise RuntimeError(
            "settings.CHUNKED_UPLOAD_TMP_ROOT is not configured — see "
            "3_settings_additions.py."
        )
    return str(root)


def _upload_tmp_dir(upload_id) -> str:
    return os.path.join(_tmp_root(), str(upload_id))


def _safe_extension(file_name: str) -> str:
    """basename-only, lowercase, no dot — never trust the client's path."""
    base = os.path.basename((file_name or "").strip())
    base = _FILENAME_SAFE_RE.sub("_", base)
    _, ext = os.path.splitext(base)
    return ext.lstrip(".").lower()


def _error(message, code=http_status.HTTP_400_BAD_REQUEST):
    return Response({"error": message}, status=code)


# =============================================================================
# INIT — validate + reserve, before any bytes are sent
# =============================================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, FormParser, MultiPartParser])
@throttle_classes([ScopedRateThrottle])
def chunked_upload_init(request):
    request.throttle_scope = "chunked_upload_init"
    user = request.user
    data = request.data

    purpose = data.get("purpose")
    if purpose not in PURPOSE_LIMITS:
        return _error(f"purpose must be one of {list(PURPOSE_LIMITS)}.")

    file_name = data.get("file_name")
    if not file_name:
        return _error("file_name is required.")

    try:
        total_chunks = int(data.get("total_chunks", 0))
        total_size = int(data.get("total_size", 0))
    except (TypeError, ValueError):
        return _error("total_chunks and total_size must be integers.")

    if total_chunks <= 0 or total_chunks > MAX_TOTAL_CHUNKS:
        return _error(f"total_chunks must be between 1 and {MAX_TOTAL_CHUNKS}.")
    if total_size <= 0:
        return _error("total_size must be a positive number of bytes.")

    limits = PURPOSE_LIMITS[purpose]
    max_bytes = limits["max_mb"] * 1024 * 1024
    if total_size > max_bytes:
        return _error(f"File too large for '{purpose}' — max {limits['max_mb']}MB.")

    ext = _safe_extension(file_name)
    if ext not in limits["extensions"]:
        return _error(
            f"'.{ext}' is not allowed for '{purpose}'. Allowed: {limits['extensions']}."
        )

    # Cap concurrent in-progress uploads per user — bounds worst-case temp
    # disk usage to MAX_IN_PROGRESS_UPLOADS_PER_USER * that purpose's max
    # size per user, regardless of how many init calls a client fires.
    in_progress = ChunkedUpload.objects.filter(
        user=user, status=ChunkedUpload.Status.IN_PROGRESS
    ).count()
    if in_progress >= MAX_IN_PROGRESS_UPLOADS_PER_USER:
        return _error(
            "Too many in-progress uploads. Finish, abort, or wait for an "
            "existing upload to expire before starting another.",
            code=http_status.HTTP_429_TOO_MANY_REQUESTS,
        )

    # ---- purpose-specific permission check + extra_data shape -------------
    extra_data, err = _validate_and_build_extra_data(purpose, data, user)
    if err:
        return err

    upload = ChunkedUpload.objects.create(
        upload_id=uuid.uuid4(),
        user=user,
        purpose=purpose,
        original_file_name=os.path.basename(file_name)[:255],
        file_extension=ext,
        total_chunks=total_chunks,
        total_size=total_size,
        extra_data=extra_data,
        status=ChunkedUpload.Status.IN_PROGRESS,
    )
    os.makedirs(_upload_tmp_dir(upload.upload_id), exist_ok=True)

    return Response(
        {
            "upload_id": str(upload.upload_id),
            "total_chunks": total_chunks,
            "chunk_endpoint": "chunked-upload-chunk",
            "complete_endpoint": "chunked-upload-complete",
        },
        status=http_status.HTTP_201_CREATED,
    )


def _validate_and_build_extra_data(purpose, data, user):
    """Returns (extra_data dict, None) on success, or (None, error Response)."""

    def _int_or_none(value):
        if value in (None, "", "null", "None"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return "invalid"

    if purpose == ChunkedUpload.Purpose.COVER_IMAGE:
        classroom_id = _int_or_none(data.get("classroom_id"))
        if not classroom_id or classroom_id == "invalid":
            return None, _error("classroom_id is required for purpose=cover_image.")
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom:
            return None, _error("Classroom not found.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(classroom, user):
            return None, _error(
                "Only the classroom's teacher, co-teacher, or moderator can "
                "set the cover image.", code=http_status.HTTP_403_FORBIDDEN,
            )
        return {"classroom_id": classroom_id}, None

    if purpose == ChunkedUpload.Purpose.MATERIAL:
        classroom_id = _int_or_none(data.get("classroom_id"))
        if not classroom_id or classroom_id == "invalid":
            return None, _error("classroom_id is required for purpose=material.")
        classroom = Classroom.objects.filter(pk=classroom_id).first()
        if not classroom:
            return None, _error("Classroom not found.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(classroom, user):
            return None, _error(
                "Only the classroom's teacher, co-teacher, or moderator can "
                "upload materials.", code=http_status.HTTP_403_FORBIDDEN,
            )
        title = (data.get("title") or "").strip()
        if not title:
            return None, _error("title is required for purpose=material.")
        material_type = data.get("material_type")
        if material_type not in ClassMaterial.MaterialType.values:
            return None, _error(
                f"material_type must be one of {ClassMaterial.MaterialType.values}."
            )
        session_id = _int_or_none(data.get("session_id"))
        if session_id == "invalid":
            return None, _error("session_id must be an integer.")
        if session_id and not ClassSession.objects.filter(
            pk=session_id, classroom_id=classroom_id
        ).exists():
            return None, _error("session_id does not belong to this classroom.")
        return {
            "classroom_id": classroom_id,
            "title": title[:150],
            "material_type": material_type,
            "session_id": session_id,
        }, None

    if purpose == ChunkedUpload.Purpose.ASSIGNMENT_ATTACHMENT:
        assignment_id = _int_or_none(data.get("assignment_id"))
        if not assignment_id or assignment_id == "invalid":
            return None, _error("assignment_id is required for purpose=assignment_attachment.")
        assignment = Assignment.objects.filter(pk=assignment_id).select_related("classroom").first()
        if not assignment:
            return None, _error("Assignment not found.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(assignment.classroom, user):
            return None, _error(
                "Only the classroom's teacher, co-teacher, or moderator can "
                "attach a file to this assignment.", code=http_status.HTTP_403_FORBIDDEN,
            )
        return {"assignment_id": assignment_id}, None

    if purpose == ChunkedUpload.Purpose.SUBMISSION_FILE:
        assignment_id = _int_or_none(data.get("assignment_id"))
        if not assignment_id or assignment_id == "invalid":
            return None, _error("assignment_id is required for purpose=submission_file.")
        assignment = Assignment.objects.filter(pk=assignment_id).select_related("classroom").first()
        if not assignment:
            return None, _error("Assignment not found.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_view_classroom_internals(assignment.classroom, user):
            return None, _error(
                "A pass (active or expired) is required to submit this assignment.",
                code=http_status.HTTP_403_FORBIDDEN,
            )
        if AssignmentSubmission.objects.filter(assignment=assignment, student=user).exists():
            return None, _error(
                "You've already submitted this assignment — update your "
                "existing submission instead."
            )
        return {"assignment_id": assignment_id}, None

    return None, _error("Unsupported purpose.")


# =============================================================================
# CHUNK — receive one piece
# =============================================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
@throttle_classes([ScopedRateThrottle])
def chunked_upload_chunk(request):
    request.throttle_scope = "chunked_upload_chunk"
    upload_id = request.data.get("upload_id")
    chunk_index = request.data.get("chunk_index")
    chunk_file = request.FILES.get("chunk")

    if not upload_id or chunk_file is None or chunk_index is None:
        return _error("upload_id, chunk_index, and chunk are required.")

    try:
        chunk_index = int(chunk_index)
    except (TypeError, ValueError):
        return _error("chunk_index must be an integer.")

    upload = get_object_or_404(
        ChunkedUpload, upload_id=upload_id, user=request.user, status=ChunkedUpload.Status.IN_PROGRESS
    )

    if not (0 <= chunk_index < upload.total_chunks):
        return _error(f"chunk_index out of range (expected 0..{upload.total_chunks - 1}).")

    if chunk_file.size <= 0 or chunk_file.size > MAX_SINGLE_CHUNK_BYTES:
        return _error(f"Each chunk must be 1 byte..{MAX_SINGLE_CHUNK_BYTES} bytes.")

    chunk_dir = _upload_tmp_dir(upload.upload_id)
    os.makedirs(chunk_dir, exist_ok=True)
    chunk_path = os.path.join(chunk_dir, f"chunk_{chunk_index:06d}")

    # Write to a temp name then atomic-rename, so a client retry/timeout
    # mid-write never leaves a half-written chunk file that complete()
    # would silently assemble as-is.
    tmp_path = chunk_path + ".part"
    with open(tmp_path, "wb") as f:
        for piece in chunk_file.chunks():
            f.write(piece)
    os.replace(tmp_path, chunk_path)

    received = len([n for n in os.listdir(chunk_dir) if n.startswith("chunk_") and not n.endswith(".part")])
    # touch updated_at so the stale-upload sweep uses recent activity, not
    # just creation time, as its staleness clock.
    ChunkedUpload.objects.filter(pk=upload.pk).update(updated_at=timezone.now())

    return Response(
        {
            "received_chunk": chunk_index,
            "received_count": received,
            "total_chunks": upload.total_chunks,
            "progress_percent": round((received / upload.total_chunks) * 100, 1),
        }
    )


# =============================================================================
# COMPLETE — assemble, validate, persist
# =============================================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([JSONParser, FormParser])
@throttle_classes([ScopedRateThrottle])
def chunked_upload_complete(request):
    request.throttle_scope = "chunked_upload_complete"
    upload_id = request.data.get("upload_id")
    if not upload_id:
        return _error("upload_id is required.")

    upload = get_object_or_404(ChunkedUpload, upload_id=upload_id, user=request.user)

    if upload.status == ChunkedUpload.Status.COMPLETED:
        return _error("This upload was already completed.", code=http_status.HTTP_409_CONFLICT)

    # Atomic claim: only one concurrent request can flip IN_PROGRESS ->
    # PROCESSING. A second, racing complete() call gets 0 rows updated and
    # bails out here instead of double-assembling / double-creating rows.
    claimed = ChunkedUpload.objects.filter(
        pk=upload.pk, status=ChunkedUpload.Status.IN_PROGRESS
    ).update(status=ChunkedUpload.Status.PROCESSING)
    if not claimed:
        return _error(
            f"Upload is not ready to complete (status={upload.status}).",
            code=http_status.HTTP_409_CONFLICT,
        )

    try:
        final_path, final_size = _assemble_chunks(upload)
    except _AssemblyError as exc:
        ChunkedUpload.objects.filter(pk=upload.pk).update(
            status=ChunkedUpload.Status.IN_PROGRESS  # let the client retry missing chunks
        )
        return _error(str(exc))

    try:
        result_data = _finalize_purpose(upload, final_path, final_size, request)
    except _FinalizeError as exc:
        _cleanup_tmp_dir(upload.upload_id)
        ChunkedUpload.objects.filter(pk=upload.pk).update(
            status=ChunkedUpload.Status.FAILED, error_message=str(exc)
        )
        return _error(str(exc), code=exc.code)
    except Exception:
        logger.exception("chunked_upload_complete: unhandled error for upload_id=%s", upload_id)
        _cleanup_tmp_dir(upload.upload_id)
        ChunkedUpload.objects.filter(pk=upload.pk).update(
            status=ChunkedUpload.Status.FAILED, error_message="Internal error while finalizing upload."
        )
        return _error("Could not finalize upload.", code=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    _cleanup_tmp_dir(upload.upload_id)
    ChunkedUpload.objects.filter(pk=upload.pk).update(status=ChunkedUpload.Status.COMPLETED)

    return Response(result_data, status=http_status.HTTP_201_CREATED)


class _AssemblyError(Exception):
    pass


class _FinalizeError(Exception):
    def __init__(self, message, code=http_status.HTTP_400_BAD_REQUEST):
        super().__init__(message)
        self.code = code


def _assemble_chunks(upload: ChunkedUpload):
    chunk_dir = _upload_tmp_dir(upload.upload_id)
    for i in range(upload.total_chunks):
        if not os.path.exists(os.path.join(chunk_dir, f"chunk_{i:06d}")):
            raise _AssemblyError(f"Missing chunk {i} — resend it and call complete again.")

    final_name = f"{uuid.uuid4().hex}.{upload.file_extension}"
    final_path = os.path.join(chunk_dir, final_name)

    total_written = 0
    with open(final_path, "wb") as out:
        for i in range(upload.total_chunks):
            chunk_path = os.path.join(chunk_dir, f"chunk_{i:06d}")
            with open(chunk_path, "rb") as cf:
                shutil.copyfileobj(cf, out, length=1024 * 1024)
            total_written += os.path.getsize(chunk_path)

    if total_written != upload.total_size:
        try:
            os.remove(final_path)
        except OSError:
            pass
        raise _AssemblyError(
            f"Assembled size ({total_written} bytes) does not match the "
            f"declared total_size ({upload.total_size} bytes)."
        )

    return final_path, total_written


def _finalize_purpose(upload: ChunkedUpload, final_path: str, final_size: int, request):
    """Re-checks permission, re-validates the assembled file against the
    SAME validators the target model field already has, then saves it onto
    the real model. Returns the JSON-serializable dict to send back."""
    user = request.user
    extra = upload.extra_data or {}
    file_name = f"{upload.original_file_name or ('upload.' + upload.file_extension)}"

    if upload.purpose == ChunkedUpload.Purpose.COVER_IMAGE:
        classroom = Classroom.objects.filter(pk=extra.get("classroom_id")).first()
        if not classroom:
            raise _FinalizeError("Classroom no longer exists.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(classroom, user):
            raise _FinalizeError("You no longer have permission to edit this classroom.", code=http_status.HTTP_403_FORBIDDEN)
        _assert_is_real_image(final_path)
        django_file = File(open(final_path, "rb"), name=file_name)
        try:
            classroom.cover_image = django_file
            classroom.full_clean(validate_unique=False)  # runs MaxFileSizeValidator again
            classroom.save(update_fields=["cover_image"])
        finally:
            django_file.close()
        return {"classroom_id": classroom.id, "cover_image": classroom.cover_image.url if classroom.cover_image else None}

    if upload.purpose == ChunkedUpload.Purpose.MATERIAL:
        classroom = Classroom.objects.filter(pk=extra.get("classroom_id")).first()
        if not classroom:
            raise _FinalizeError("Classroom no longer exists.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(classroom, user):
            raise _FinalizeError("You no longer have permission to upload materials here.", code=http_status.HTTP_403_FORBIDDEN)
        django_file = File(open(final_path, "rb"), name=file_name)
        try:
            material = ClassMaterial(
                classroom=classroom,
                session_id=extra.get("session_id"),
                uploaded_by=user,
                title=extra.get("title", "")[:150],
                material_type=extra.get("material_type"),
                file=django_file,
            )
            material.full_clean(validate_unique=False)  # MaxFileSizeValidator + FileExtensionValidator
            material.save()
        finally:
            django_file.close()
        return {"material_id": material.id, "file": material.file.url if material.file else None}

    if upload.purpose == ChunkedUpload.Purpose.ASSIGNMENT_ATTACHMENT:
        assignment = Assignment.objects.filter(pk=extra.get("assignment_id")).select_related("classroom").first()
        if not assignment:
            raise _FinalizeError("Assignment no longer exists.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_manage_classroom(assignment.classroom, user):
            raise _FinalizeError("You no longer have permission to edit this assignment.", code=http_status.HTTP_403_FORBIDDEN)
        django_file = File(open(final_path, "rb"), name=file_name)
        try:
            assignment.attachment = django_file
            assignment.full_clean(validate_unique=False)
            assignment.save(update_fields=["attachment"])
        finally:
            django_file.close()
        return {"assignment_id": assignment.id, "attachment": assignment.attachment.url if assignment.attachment else None}

    if upload.purpose == ChunkedUpload.Purpose.SUBMISSION_FILE:
        assignment = Assignment.objects.filter(pk=extra.get("assignment_id")).select_related("classroom").first()
        if not assignment:
            raise _FinalizeError("Assignment no longer exists.", code=http_status.HTTP_404_NOT_FOUND)
        if not _can_view_classroom_internals(assignment.classroom, user):
            raise _FinalizeError("A pass (active or expired) is required to submit this assignment.", code=http_status.HTTP_403_FORBIDDEN)
        if AssignmentSubmission.objects.filter(assignment=assignment, student=user).exists():
            raise _FinalizeError(
                "You've already submitted this assignment — update your existing submission instead.",
                code=http_status.HTTP_409_CONFLICT,
            )
        django_file = File(open(final_path, "rb"), name=file_name)
        try:
            submission = AssignmentSubmission(assignment=assignment, student=user, file=django_file)
            submission.full_clean(validate_unique=False)
            with transaction.atomic():
                try:
                    submission.save()
                except IntegrityError:
                    raise _FinalizeError(
                        "You've already submitted this assignment — update your existing submission instead.",
                        code=http_status.HTTP_409_CONFLICT,
                    )
        finally:
            django_file.close()
        # Same notification the normal (non-chunked) submission path fires —
        # see AssignmentSubmissionViewSet.perform_create in views.py.
        try:
            from .models import Notification
            from .views import create_notification, _safe_delay
            from .tasks import notify_submission_received

            create_notification(
                recipient=assignment.classroom.teacher,
                notif_type=Notification.NotifType.SUBMISSION_RECEIVED,
                title="New submission to grade",
                message=(
                    f"{user.get_full_name() or user.username} submitted "
                    f"'{assignment.title}' in '{assignment.classroom.title}'."
                ),
                classroom=assignment.classroom,
            )
            _safe_delay(notify_submission_received, submission.id)
        except Exception:
            logger.exception("chunked submission notification failed for submission_id=%s", submission.id)
        return {"submission_id": submission.id, "file": submission.file.url if submission.file else None}

    raise _FinalizeError("Unsupported purpose.")


def _assert_is_real_image(path: str):
    """Chunked assembly writes straight to disk, bypassing ImageField's
    normal Pillow-decode check on the upload — restore that same guarantee
    here so a renamed non-image can't slip through as a cover image."""
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
    except Exception:
        raise _FinalizeError("File is not a valid image.")


def _cleanup_tmp_dir(upload_id):
    try:
        shutil.rmtree(_upload_tmp_dir(upload_id), ignore_errors=True)
    except Exception:
        logger.warning("Failed to clean up temp dir for upload_id=%s", upload_id, exc_info=True)


# =============================================================================
# ABORT — client-cancelled, free temp disk immediately
# =============================================================================
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def chunked_upload_abort(request):
    upload_id = request.data.get("upload_id")
    if not upload_id:
        return _error("upload_id is required.")
    upload = get_object_or_404(
        ChunkedUpload, upload_id=upload_id, user=request.user,
        status__in=[ChunkedUpload.Status.IN_PROGRESS],
    )
    _cleanup_tmp_dir(upload.upload_id)
    upload.status = ChunkedUpload.Status.ABORTED
    upload.save(update_fields=["status", "updated_at"])
    return Response({"status": "aborted"})