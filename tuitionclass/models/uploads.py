# tuitionclass/models/uploads.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import uuid
from login.models import User
from django.db import models


# ---------------------------------------------------------------------------
# NOTE (task 42 — core-app migration): Notification, NotificationPreference,
# create_notification(), and create_bulk_notifications() used to live here.
# They have moved to core/models.py and core/services.py respectively (the
# DB tables are unchanged — see core/migrations/0001_move_notification_models.py
# for the zero-data-copy SeparateDatabaseAndState move). Every call-site in
# this app now imports them from `core.models` / `core.services` instead of
# `.models`. See core_app_documentation.md for the full design.
# ---------------------------------------------------------------------------
# 12. CHUNKED UPLOAD (large-file uploads in pieces, assembled server-side)
# ---------------------------------------------------------------------------
# Why this exists: DATA_UPLOAD_MAX_MEMORY_SIZE is 10MB (see settings.py —
# deliberately capped there as a DoS guard on the request body size Django
# will parse at all). ClassMaterial.file allows up to 100MB and
# assigments.attachment / assigmentsSubmission.file allow up to 50MB — all
# three would be rejected outright as a single request well before hitting
# their own MaxFileSizeValidator. Chunked upload splits a big file into
# small pieces (each comfortably under the 10MB request-body cap), uploads
# them one at a time, and assembles the final file server-side only once
# every piece has arrived — at which point the *existing* size/extension
# validators run against the assembled file exactly as they always have.
#
# One row = one in-flight (or finished/aborted/expired) upload attempt.
# Temp chunks are NOT stored under MEDIA_ROOT (see settings.py
# CHUNKED_UPLOAD_TMP_ROOT) specifically so a partially-uploaded file can
# never be served/guessed at via the media URL while assembly is still in
# progress. See chunked_upload_views.py for the init/chunk/complete/abort
# endpoints that operate on this model.
class ChunkedUpload(models.Model):
    class Purpose(models.TextChoices):
        COVER_IMAGE = "cover_image", "Classroom Cover Image"
        MATERIAL = "material", "Class Material"
        assigments_ATTACHMENT = "assigments_attachment", "assigments Attachment"
        SUBMISSION_FILE = "submission_file", "assigments Submission File"

    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"       # accepting chunks
        PROCESSING = "processing", "Processing"           # complete() claimed it, assembling
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"                        # assembly/validation error — see error_message
        ABORTED = "aborted", "Aborted"                      # client-cancelled
        EXPIRED = "expired", "Expired"                      # swept by cleanup_stale_chunked_uploads

    upload_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="chunked_uploads")
    purpose = models.CharField(max_length=30, choices=Purpose.choices)

    # Sanitized (basename-only, no path separators) original name — used
    # only to derive the extension and for display; never trusted as a
    # filesystem path (see _safe_extension() in chunked_upload_views.py).
    original_file_name = models.CharField(max_length=255)
    file_extension = models.CharField(max_length=10)  # lowercase, no leading dot
    total_chunks = models.PositiveIntegerField()
    total_size = models.PositiveBigIntegerField()

    # Everything complete() needs to know WHERE the assembled file goes,
    # captured once at init() (and permission-checked again at both ends —
    # see chunked_upload_views.py). Shape depends on purpose:
    #   cover_image           -> {"classroom_id": <id>}
    #   material               -> {"classroom_id": <id>, "title": str,
    #                              "material_type": str, "session_id": <id>|None}
    #   assigments_attachment -> {"assigments_id": <id>}
    #   submission_file        -> {"assigments_id": <id>}
    extra_data = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # cleanup_stale_chunked_uploads sweeps exactly this shape of query.
            models.Index(fields=["status", "created_at"]),
            # per-user in-progress count check at init() (caps concurrent
            # uploads per user — see MAX_IN_PROGRESS_UPLOADS_PER_USER).
            models.Index(fields=["user", "status"]),
        ]

    def __str__(self):
        return f"{self.purpose} upload {self.upload_id} ({self.status})"
