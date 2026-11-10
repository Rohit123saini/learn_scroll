"""
post/upload_sessions.py  (H20)

ONE chunked-upload path for EVERY media kind (post, reel, story, resource). The old chunked path
(`/post/chunked/*`) carried ONE file and a handful of fields (no alt text, topics, cover, captions, tags, music/stickers,
poll validation) and its `complete` was not repeatable. This replaces it for the app's UploadQueue:

    POST   /post/uploads/                       init: key + kind + payload (ALL form fields) + files manifest
    POST   /post/uploads/<id>/chunk/            one chunk of one file (md5 verified, size checked)
    GET    /post/uploads/<id>/                  status: which chunks the server has, per file (resume)
    POST   /post/uploads/<id>/complete/         assemble -> run the REAL create endpoint -> post/story/resource
    DELETE /post/uploads/<id>/                  cancel (Discard in the app) + free the chunks

Why nothing is re-implemented: `complete` assembles the files, wraps them in a request-like object (`_ShimRequest`:
same `.user` / `.META` / `.build_absolute_uri`, but `.data` and `.FILES` built from the session) and calls the SAME view
method the normal multipart request would reach (PostCreateAPIView.post / StoryCreateAPIView.post /
ResourceListCreateAPIView.post). Every rule - copyright gate, serializers, alt text, topics, tags, hashtags, cover, poll,
stickers, music, resource limits - therefore behaves exactly like a normal request, and stays in step when those rules change.

Duplicate-submit guard: (user, idempotency_key) is unique. `init` with a known key returns the SAME session; `complete` on
a finished session replays the stored answer instead of creating a second post. A 4xx from the create endpoint (validation)
keeps the uploaded chunks (so "Edit" can fix the text and call complete again, optionally with a new `payload`).

Written by reading the code only - nothing was executed.
"""
import json
import logging
import math
import mimetypes
import os
import re
import shutil
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import QueryDict
from django.utils import timezone
from django.utils.datastructures import MultiValueDict

from .models import Draft, IdempotencyRecord, UploadSession
from .services import CHUNK_UPLOAD_MAX_SIZE, _chunk_dir, list_received_chunks, save_uploaded_chunk

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(hours=48)
MAX_ACTIVE_SESSIONS_PER_USER = 20
MIN_CHUNK_SIZE = 256 * 1024
MAX_CHUNK_SIZE = 16 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024
MAX_PAYLOAD_BYTES = 512 * 1024  # resources carry on-device OCR text (ocr_texts) in the payload
COVER_MAX_BYTES = 10 * 1024 * 1024
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")

# payload keys the client may NOT set: files only come from chunks, identity only from the token.
RESERVED_PAYLOAD_KEYS = {"media_files", "files", "media", "reel_cover", "user", "id", "pk", "draft_id"}
# `draft_id` stays in the stored payload (complete() deletes that draft) but is never sent to the create endpoint.
_STRIPPED_PAYLOAD_KEYS = RESERVED_PAYLOAD_KEYS - {"draft_id"}

# kind -> {role: form field the real create endpoint reads}, limits
KIND_SPECS = {
    UploadSession.KIND_POST: {"fields": {"media": "media_files", "cover": "reel_cover"}, "max_media": 10, "media_required": False},
    UploadSession.KIND_REEL: {"fields": {"media": "media_files", "cover": "reel_cover"}, "max_media": 1, "media_required": True},
    UploadSession.KIND_STORY: {"fields": {"media": "media"}, "max_media": 1, "media_required": True},
    UploadSession.KIND_RESOURCE: {"fields": {"media": "files"}, "max_media": 10, "media_required": True},
}


# A post (kind=post) may also carry poll OPTION images: role `poll_option_<i>` (i = 0..5, the option's position) goes to
# the form field `poll_option_image_<i>` that `polls.clean_poll_payload` reads. They are not "media": they do not count
# against max_media, there is at most one per option, and each is small (an option image is capped at 5 MB).
POLL_OPTION_ROLE_RE = re.compile(r"^poll_option_([0-5])$")
POLL_OPTION_MAX_BYTES = 5 * 1024 * 1024


def _field_for_role(kind, spec, role):
    """The form field a file of `role` is delivered as, or None when this kind does not take that role."""
    field = spec["fields"].get(role)
    if field is not None:
        return field
    if kind == UploadSession.KIND_POST:
        m = POLL_OPTION_ROLE_RE.match(role or "")
        if m:
            return "poll_option_image_%s" % m.group(1)
    return None


class UploadError(Exception):
    """A refusal with a stable `code` the app maps to Hindi / English text."""

    def __init__(self, code, message, http_status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.extra = extra


# ------------------------------------------------------------------ validation
def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_payload(payload):
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise UploadError("payload_invalid", "payload must be an object.")
    try:
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise UploadError("payload_invalid", "payload must be JSON.")
    if size > MAX_PAYLOAD_BYTES:
        raise UploadError("payload_too_large", "payload is too large.")
    return {str(k): v for k, v in payload.items() if str(k) not in _STRIPPED_PAYLOAD_KEYS}


def validate_init(data):
    """-> (key, kind, chunk_size, payload, files). Raises UploadError. Cheap checks first, so a bad request fails
    BEFORE gigabytes are uploaded."""
    key = str(data.get("idempotency_key") or "").strip()
    if not KEY_RE.match(key):
        raise UploadError("idempotency_key_invalid", "idempotency_key must be 8-64 characters: letters, digits, . _ : -")
    kind = data.get("kind")
    spec = KIND_SPECS.get(kind)
    if spec is None:
        raise UploadError("kind_invalid", "kind must be one of: post, reel, story, resource.")
    chunk_size = _to_int(data.get("chunk_size"), DEFAULT_CHUNK_SIZE)
    if not MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE:
        raise UploadError("chunk_size_invalid", "chunk_size must be between 256 KB and 16 MB.")
    payload = validate_payload(data.get("payload"))

    raw_files = data.get("files") or []
    extra_slots = 7 if kind == UploadSession.KIND_POST else 1  # cover (+ up to 6 poll option images for a post)
    if not isinstance(raw_files, list) or len(raw_files) > spec["max_media"] + extra_slots:
        raise UploadError("too_many_files", "Too many files for this upload.")
    files, media_count, cover_count = [], 0, 0
    option_roles = set()
    for index, raw in enumerate(raw_files):
        if not isinstance(raw, dict):
            raise UploadError("file_invalid", "Every file needs name and size.")
        role = raw.get("role") or "media"
        if _field_for_role(kind, spec, role) is None:
            raise UploadError("file_role_invalid", "This upload cannot have a '%s' file." % role)
        name = os.path.basename(str(raw.get("name") or "")).strip()[:255]
        if not name:
            raise UploadError("file_invalid", "Every file needs a name.")
        size = _to_int(raw.get("size"))
        if size <= 0:
            raise UploadError("file_empty", "'%s' is empty." % name)
        if size > CHUNK_UPLOAD_MAX_SIZE:
            raise UploadError("file_too_large", "File too large. Max 4GB allowed.")
        if role == "cover":
            cover_count += 1
            if size > COVER_MAX_BYTES:
                raise UploadError("cover_too_large", "The cover image can be at most 10 MB.")
        elif POLL_OPTION_ROLE_RE.match(role):
            if role in option_roles:
                raise UploadError("too_many_files", "Only one image per poll option.")
            option_roles.add(role)
            if size > POLL_OPTION_MAX_BYTES:
                raise UploadError("poll_option_image_too_large", "A poll option image can be at most 5 MB.")
        else:
            media_count += 1
        total_chunks = int(math.ceil(size / float(chunk_size)))
        if _to_int(raw.get("total_chunks"), total_chunks) != total_chunks:
            raise UploadError("chunk_count_mismatch", "total_chunks does not match size / chunk_size.")
        content_type = str(raw.get("content_type") or mimetypes.guess_type(name)[0] or "application/octet-stream")[:100]
        files.append({"index": index, "role": role, "name": name, "size": size,
                      "total_chunks": total_chunks, "content_type": content_type, "upload_id": ""})
    if media_count > spec["max_media"]:
        raise UploadError("too_many_files", "At most %d media file(s) for this upload." % spec["max_media"])
    if spec["media_required"] and media_count == 0:
        raise UploadError("media_required", "This upload needs a media file.")
    if cover_count > 1:
        raise UploadError("too_many_files", "Only one cover image is allowed.")
    if cover_count and not media_count:
        raise UploadError("cover_without_media", "A cover needs a video.")

    _validate_payload_early(kind, payload, media_count)
    return key, kind, chunk_size, payload, files


def _validate_payload_early(kind, payload, media_count):
    """The cheap rules of the create endpoint, checked before the upload so the user does not wait for 2 GB to be
    told the category is missing. The full validation still runs again at complete()."""
    if kind in (UploadSession.KIND_POST, UploadSession.KIND_REEL):
        from .serializers import category_error_message, normalize_category

        raw_category = payload.get("category")
        if raw_category not in (None, "") and normalize_category(raw_category) is None:
            raise UploadError("category_invalid", category_error_message(raw_category))
        post_type = payload.get("post_type") or ("video" if kind == UploadSession.KIND_REEL else "text")
        content = str(payload.get("content") or "")
        if post_type in ("text", "doubt") and not content.strip():
            raise UploadError("content_required", "Please write something first.")
        if post_type in ("image", "video", "document") and media_count == 0:
            raise UploadError("media_required", "A %s post needs a file." % post_type)


# ------------------------------------------------------------------ init
def _refuse_if_reels_off(kind, platform=""):
    """P-J2 kill switch `reels_upload`: refuse to START or FINISH a reel upload while the admin switch is OFF. Posts, stories
    and resources are untouched. Chunks of a session that is already running are still accepted (so a switch flip does not
    corrupt it) - the reel is simply not created until the switch is back ON; the upload resumes then."""
    if kind != UploadSession.KIND_REEL:
        return
    from common import feature_flags as ff

    state = ff.server_blocked(ff.REELS_UPLOAD, platform)
    if state is not None:
        raise UploadError(ff.FEATURE_DISABLED, state.message, 403, feature=ff.REELS_UPLOAD, message_hi=state.message_hi)


def init_session(user, data, platform=""):
    """-> (session, created). Same (user, idempotency_key) = the SAME session (the app calls this again after a restart)."""
    # P-J2: the kill switch is checked BEFORE validation. A switched-off reel upload must answer 403 feature_disabled
    # (the app shows the "reels are paused" message) - not a 400 about a payload the server is not going to accept anyway.
    _refuse_if_reels_off(data.get("kind") if hasattr(data, "get") else None, platform)
    key, kind, chunk_size, payload, files = validate_init(data)

    existing = UploadSession.objects.filter(user=user, idempotency_key=key).first()
    if existing is not None:
        if existing.kind != kind:
            raise UploadError("idempotency_key_reused", "This key was already used for something else.", 409)
        return existing, False

    now = timezone.now()
    active = UploadSession.objects.filter(
        user=user, status=UploadSession.STATUS_UPLOADING, expires_at__gt=now).count()
    if active >= MAX_ACTIVE_SESSIONS_PER_USER:
        raise UploadError("too_many_uploads", "Too many uploads in progress. Wait for one to finish.", 429)

    from copyrights.services import can_upload

    allowed, why = can_upload(user)
    if not allowed:
        raise UploadError("copyright_upload_blocked", why, 403)

    try:
        with transaction.atomic():
            session = UploadSession.objects.create(
                user=user, idempotency_key=key, kind=kind, payload=payload, files=files,
                chunk_size=chunk_size, expires_at=now + SESSION_TTL)
            for f in files:
                f["upload_id"] = "%s_%d" % (session.id.hex, f["index"])
            session.files = files
            session.save(update_fields=["files"])
    except IntegrityError:  # parallel init with the same key won the race
        return UploadSession.objects.get(user=user, idempotency_key=key), False

    for f in files:
        os.makedirs(_chunk_dir(f["upload_id"]), exist_ok=True)
    return session, True


# ------------------------------------------------------------------ lookup / status
def get_session(user, session_id):
    session = UploadSession.objects.filter(pk=session_id, user=user).first()
    if session is None:
        raise UploadError("session_not_found", "Upload not found.", 404)
    return session


def _ensure_open(session):
    if session.status == UploadSession.STATUS_CANCELLED:
        raise UploadError("session_cancelled", "This upload was cancelled.", 409)
    if session.status == UploadSession.STATUS_COMPLETED:
        raise UploadError("session_completed", "This upload is already finished.", 409)
    if session.expires_at <= timezone.now():
        raise UploadError("session_expired", "This upload expired. Please start again.", 410)


def session_payload(session, with_chunks=True):
    files = []
    for f in session.files or []:
        item = {"index": f["index"], "role": f["role"], "name": f["name"], "size": f["size"], "total_chunks": f["total_chunks"]}
        if with_chunks and session.status == UploadSession.STATUS_UPLOADING:
            item["received_chunks"] = list_received_chunks(f["upload_id"])
        files.append(item)
    body = {
        "session_id": str(session.id),
        "kind": session.kind,
        "status": session.status,
        "chunk_size": session.chunk_size,
        "expires_at": session.expires_at.isoformat(),
        "files": files,
    }
    if session.status == UploadSession.STATUS_COMPLETED:
        body["object_id"] = session.result_object_id
        body["result"] = session.result_body
    return body


# ------------------------------------------------------------------ chunk
def _file_entry(session, file_index):
    for f in session.files or []:
        if f["index"] == file_index:
            return f
    raise UploadError("file_index_invalid", "Unknown file in this upload.")


def save_chunk(user, session_id, file_index, chunk_index, chunk_file, chunk_hash):
    session = get_session(user, session_id)
    _ensure_open(session)
    entry = _file_entry(session, file_index)
    total = entry["total_chunks"]
    if not 0 <= chunk_index < total:
        raise UploadError("chunk_index_invalid", "Chunk %d is outside 0..%d." % (chunk_index, total - 1))
    expected = session.chunk_size if chunk_index < total - 1 else entry["size"] - session.chunk_size * (total - 1)
    if chunk_file is None or chunk_file.size != expected:
        raise UploadError("chunk_size_mismatch", "Chunk %d must be %d bytes." % (chunk_index, expected))
    if not chunk_hash:
        raise UploadError("chunk_hash_required", "chunk_hash (md5) is required.")
    try:
        save_uploaded_chunk(entry["upload_id"], chunk_index, chunk_file, expected_hash=str(chunk_hash).lower())
    except ValueError:
        raise UploadError("chunk_hash_mismatch", "Chunk %d was damaged on the way. Sending it again." % chunk_index)
    return len(list_received_chunks(entry["upload_id"]))


# ------------------------------------------------------------------ complete
class _ShimRequest:
    """Looks like the DRF request of a normal multipart create: same user / headers / host, but `.data` and `.FILES`
    come from the session. The Idempotency-Key header is hidden so the wrapped endpoint does not claim a second key."""

    def __init__(self, real, data, files):
        self._real = real
        self.data = data
        self.POST = data
        self.FILES = files
        self.query_params = QueryDict()
        meta = dict(getattr(real, "META", {}) or {})
        meta.pop("HTTP_IDEMPOTENCY_KEY", None)
        self.META = meta

    def __getattr__(self, name):  # user, auth, build_absolute_uri, get_host ...
        return getattr(self._real, name)


def _view_class(kind):
    if kind in (UploadSession.KIND_POST, UploadSession.KIND_REEL):
        from .views import PostCreateAPIView
        return PostCreateAPIView
    if kind == UploadSession.KIND_STORY:
        from .views import StoryCreateAPIView
        return StoryCreateAPIView
    from .resource_views import ResourceListCreateAPIView
    return ResourceListCreateAPIView


def _run_create_view(kind, shim):
    """Calls the real create view's `post` directly (not through `dispatch`), so the DRF exceptions that dispatch would
    turn into a 400 (the story / resource serializers use `raise_exception=True`) are turned into the same JSON here.
    Runs in its own savepoint: an exception leaves no half-written rows behind."""
    from rest_framework.exceptions import APIException
    from rest_framework.response import Response

    try:
        with transaction.atomic():
            return _view_class(kind)().post(shim)
    except APIException as exc:
        from .views import _first_error_text

        detail = exc.detail
        message = _first_error_text(detail) if isinstance(detail, (dict, list)) else str(detail)
        return Response(
            {"success": False, "code": str(getattr(exc, "default_code", "") or "error"),
             "message": message or "Validation failed", "errors": detail},
            status=exc.status_code)


def _assemble_keep(upload_id, total_chunks, dest_path):
    """Like services.assemble_chunks but the chunks stay on disk, so a validation error can be fixed and complete()
    called again without re-uploading."""
    chunk_dir = _chunk_dir(upload_id)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "wb") as out:
        for i in range(total_chunks):
            part = os.path.join(chunk_dir, "chunk_%d" % i)
            if not os.path.exists(part):
                raise UploadError("chunks_missing", "Chunk %d is missing." % i, 409)
            with open(part, "rb") as src:
                shutil.copyfileobj(src, out, length=1024 * 1024)


def _request_data(session):
    data = QueryDict(mutable=True)
    for key, value in (session.payload or {}).items():
        if key in RESERVED_PAYLOAD_KEYS or value is None:
            continue
        if isinstance(value, bool):
            data[key] = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            data[key] = json.dumps(value, ensure_ascii=False)  # same encoding the app uses for multipart list fields
        else:
            data[key] = str(value)
    if session.kind == UploadSession.KIND_REEL:
        data["post_type"] = "video"
        if "media_types" not in data:
            data["media_types"] = json.dumps(["video"])
    return data


def _missing_chunks(session):
    for f in session.files or []:
        have = set(list_received_chunks(f["upload_id"]))
        missing = [i for i in range(f["total_chunks"]) if i not in have]
        if missing:
            raise UploadError("chunks_missing", "Some parts have not arrived yet.", 409,
                              file_index=f["index"], missing=missing[:50])


def _object_id(body):
    if isinstance(body, dict):
        if body.get("id"):
            return str(body["id"])
        inner = body.get("data")
        if isinstance(inner, dict) and inner.get("id"):
            return str(inner["id"])
    return ""


def _remove_chunk_dirs(session):
    for f in session.files or []:
        if f.get("upload_id"):
            shutil.rmtree(_chunk_dir(f["upload_id"]), ignore_errors=True)


def _soft_delete_draft(user, payload):
    raw = (payload or {}).get("draft_id")
    if not raw:
        return
    try:
        client_id = uuid.UUID(str(raw))
    except ValueError:
        return
    Draft.objects.filter(user=user, client_id=client_id, is_deleted=False).update(
        is_deleted=True, deleted_at=timezone.now(), updated_at=timezone.now(), version=F("version") + 1)


def complete_session(user, session_id, real_request, payload_override=None):
    """-> (http_status, body, replayed). Runs the real create endpoint once. Safe to call again (replays the answer).

    T2.2 / T2.5 - transient "database is locked" par poora complete 3 baar retry hota hai. Safe kyunki:
      * har attempt ek `transaction.atomic()` hai - lock par poora rollback (post / story / resource row bhi), to koi
        aadha-bana post nahi bachta;
      * chunks disk par rehte hain (`_assemble_keep`), dobara upload nahi karna padta;
      * jo attempt commit ho gaya uska session `completed` hota hai, isliye agla call (retry ya app ka dobara bhejna)
        usi session ka stored jawab replay karta hai - NAYA post kabhi nahi banta (idempotent, `replayed=True`).
    """
    from common.db_retry import DEFAULT_WRITE_RETRIES, retry_on_db_lock

    return retry_on_db_lock(
        lambda: _complete_session_once(user, session_id, real_request, payload_override),
        retries=DEFAULT_WRITE_RETRIES, delay=0.2,
    )


def _complete_session_once(user, session_id, real_request, payload_override=None):
    from .idempotency import json_safe

    with transaction.atomic():
        session = UploadSession.objects.select_for_update().filter(pk=session_id, user=user).first()
        if session is None:
            raise UploadError("session_not_found", "Upload not found.", 404)
        if session.status == UploadSession.STATUS_COMPLETED:
            return session.result_status or 200, session.result_body, True
        _ensure_open(session)
        _refuse_if_reels_off(session.kind, (getattr(real_request, "headers", None) or {}).get("X-Platform", ""))  # P-J2

        if payload_override is not None:  # "Edit" after a validation error: new text, same uploaded files
            session.payload = validate_payload(payload_override)
            session.save(update_fields=["payload", "updated_at"])

        _missing_chunks(session)

        spec = KIND_SPECS[session.kind]
        # assembled OUTSIDE MEDIA_ROOT: an unvalidated file must never be reachable by URL while it is being checked
        tmp_root = os.path.join(
            getattr(settings, "CHUNKED_UPLOAD_TMP_ROOT", settings.MEDIA_ROOT), "assembled", session.id.hex)
        opened = []
        try:
            data = _request_data(session)
            files = MultiValueDict()
            for f in sorted(session.files or [], key=lambda x: x["index"]):
                path = os.path.join(tmp_root, "%d_%s" % (f["index"], f["name"]))
                _assemble_keep(f["upload_id"], f["total_chunks"], path)
                if os.path.getsize(path) != f["size"]:
                    raise UploadError("size_mismatch", "'%s' arrived with the wrong size. Please upload it again." % f["name"], 409)
                handle = open(path, "rb")
                opened.append(handle)
                files.appendlist(_field_for_role(session.kind, spec, f["role"]), UploadedFile(
                    file=handle, name=f["name"], content_type=f["content_type"], size=f["size"]))
            for field, items in files.lists():
                data.setlist(field, items)

            response = _run_create_view(session.kind, _ShimRequest(real_request, data, files))
        finally:
            for handle in opened:
                try:
                    handle.close()
                except OSError:
                    pass
            shutil.rmtree(tmp_root, ignore_errors=True)

        body = json_safe(getattr(response, "data", None))
        code = response.status_code
        if code >= 500:
            # the create endpoint turned an exception into a 500 after maybe writing rows: undo them, the app retries
            transaction.set_rollback(True)
            return code, body, False
        if 200 <= code < 300:
            session.status = UploadSession.STATUS_COMPLETED
            session.result_status = code
            session.result_body = body
            session.result_object_id = _object_id(body)
            session.last_error_code = ""
            session.save()
            _soft_delete_draft(user, session.payload)
            transaction.on_commit(lambda: _remove_chunk_dirs(session))
        else:
            session.last_error_code = str((body or {}).get("code") or "")[:64] if isinstance(body, dict) else ""
            session.save(update_fields=["last_error_code", "updated_at"])
        return code, body, False


# ------------------------------------------------------------------ cancel / purge
def cancel_session(user, session_id):
    session = UploadSession.objects.filter(pk=session_id, user=user).first()
    if session is None:
        return None  # already gone: Discard is idempotent
    if session.status == UploadSession.STATUS_UPLOADING:
        session.status = UploadSession.STATUS_CANCELLED
        session.save(update_fields=["status", "updated_at"])
        _remove_chunk_dirs(session)
    return session


def purge_stale(now=None):
    """Housekeeping (post.tasks.purge_stale_uploads, daily): chunks of expired sessions, old finished sessions, old
    idempotency records, drafts deleted > 30 days ago. -> counts."""
    now = now or timezone.now()
    counts = {"sessions_expired": 0, "sessions_deleted": 0, "idempotency_deleted": 0, "drafts_deleted": 0}
    for session in UploadSession.objects.filter(status=UploadSession.STATUS_UPLOADING, expires_at__lte=now)[:500]:
        _remove_chunk_dirs(session)
        session.status = UploadSession.STATUS_CANCELLED
        session.save(update_fields=["status", "updated_at"])
        counts["sessions_expired"] += 1
    old = UploadSession.objects.exclude(status=UploadSession.STATUS_UPLOADING).filter(updated_at__lte=now - timedelta(days=7))
    counts["sessions_deleted"] = old.count()
    old.delete()
    counts["idempotency_deleted"] = IdempotencyRecord.objects.filter(created_at__lte=now - timedelta(hours=48)).delete()[0]
    counts["drafts_deleted"] = Draft.objects.filter(is_deleted=True, deleted_at__lte=now - timedelta(days=30)).delete()[0]
    return counts
