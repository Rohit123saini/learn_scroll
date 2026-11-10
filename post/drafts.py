"""
post/drafts.py  (H20)

Server side of "Drafts": one unsent post / reel / story / resource per row, synced across the user's devices.
Only the form fields (`data`) and a media manifest (`media`: name / size / type, NO bytes) live here - the picked files
stay on the phone that picked them.

    GET    /post/drafts/            list my drafts (newest edit first, `?since=<iso>` also returns tombstones)
    PUT    /post/drafts/<client_id>/  create or update (optimistic concurrency via `base_version`)
    GET    /post/drafts/<client_id>/  one draft
    DELETE /post/drafts/<client_id>/  soft delete (tombstone, so other devices drop it too)

Conflict rule: the client sends the `base_version` it started editing from. If the server is already past it the PUT is
refused with 409 and the server copy, so the app can show "edited on another device" instead of silently losing text.
A retried PUT (lost response) carries the same base_version + the same content, so it is accepted again and is harmless.

Written by reading the code only - nothing was executed.
"""
import json
import logging
import uuid

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import Draft, UploadSession

logger = logging.getLogger(__name__)

MAX_DRAFTS_PER_USER = 50
MAX_DATA_BYTES = 64 * 1024
MAX_MEDIA_ITEMS = 11  # 10 media + 1 cover
ALLOWED_KINDS = {k for k, _ in UploadSession.KIND_CHOICES}
# keys that can never be stored in a draft: files come from the phone, identity from the token.
_FORBIDDEN_DATA_KEYS = {"media_files", "files", "reel_cover", "user", "id", "pk"}
_MEDIA_KEYS = ("name", "size", "role", "content_type")


class DraftError(Exception):
    """A refusal with a stable `code` the app maps to Hindi / English text."""

    def __init__(self, code, message, http_status=400, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.extra = extra


def parse_client_id(raw):
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        raise DraftError("draft_id_invalid", "Draft id must be a UUID.")


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_draft_body(body):
    """-> (kind, data, media, base_version, client_updated_at). Raises DraftError."""
    if not isinstance(body, dict):
        raise DraftError("draft_invalid", "Send a JSON object.")
    kind = body.get("kind")
    if kind not in ALLOWED_KINDS:
        raise DraftError("kind_invalid", "kind must be one of: post, reel, story, resource.")

    data = body.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise DraftError("draft_data_invalid", "data must be an object.")
    try:
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        raise DraftError("draft_data_invalid", "data must be JSON.")
    if size > MAX_DATA_BYTES:
        raise DraftError("draft_too_large", "This draft is too large to sync.", 413)
    data = {str(k): v for k, v in data.items() if str(k) not in _FORBIDDEN_DATA_KEYS}

    raw_media = body.get("media")
    if raw_media is None:
        raw_media = []
    if not isinstance(raw_media, list) or len(raw_media) > MAX_MEDIA_ITEMS:
        raise DraftError("draft_media_invalid", "Too many media items in this draft.")
    media = []
    for item in raw_media:
        if not isinstance(item, dict):
            raise DraftError("draft_media_invalid", "Every media item must be an object.")
        media.append({k: item[k] for k in _MEDIA_KEYS if k in item and isinstance(item[k], (str, int, float, bool))})

    base_version = _to_int(body.get("base_version"), 0)
    if base_version < 0:
        raise DraftError("base_version_invalid", "base_version cannot be negative.")

    client_updated_at = timezone.now()
    raw_time = body.get("client_updated_at")
    if raw_time:
        parsed = parse_datetime(str(raw_time))
        if parsed is not None:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            # a phone with a wrong clock must not put a draft in the far future / past of everything else
            if abs((parsed - timezone.now()).total_seconds()) < 3 * 24 * 3600:
                client_updated_at = parsed
    return kind, data, media, base_version, client_updated_at


def draft_payload(draft):
    return {
        "id": str(draft.client_id),
        "kind": draft.kind,
        "data": draft.data,
        "media": draft.media,
        "version": draft.version,
        "client_updated_at": draft.client_updated_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
        "deleted": bool(draft.is_deleted),
    }


def list_drafts(user, since=None):
    qs = Draft.objects.filter(user=user)
    if since is None:
        qs = qs.filter(is_deleted=False)
    else:
        qs = qs.filter(updated_at__gt=since)  # includes tombstones so other devices drop them
    return list(qs.order_by("-client_updated_at")[:MAX_DRAFTS_PER_USER * 2])


def get_draft(user, client_id):
    draft = Draft.objects.filter(user=user, client_id=client_id, is_deleted=False).first()
    if draft is None:
        raise DraftError("draft_not_found", "Draft not found.", 404)
    return draft


def save_draft(user, client_id, body):
    """-> (draft, created). Raises DraftError (409 `draft_conflict` carries the server copy)."""
    kind, data, media, base_version, client_updated_at = validate_draft_body(body)

    with transaction.atomic():
        draft = Draft.objects.select_for_update().filter(user=user, client_id=client_id).first()
        if draft is None:
            live = Draft.objects.filter(user=user, is_deleted=False).count()
            if live >= MAX_DRAFTS_PER_USER:
                raise DraftError("too_many_drafts", "You have too many drafts. Delete one first.", 429)
            try:
                with transaction.atomic():
                    draft = Draft.objects.create(
                        user=user, client_id=client_id, kind=kind, data=data, media=media,
                        client_updated_at=client_updated_at)
            except IntegrityError:  # parallel PUT of the same draft won the race: treat as an update below
                draft = Draft.objects.select_for_update().get(user=user, client_id=client_id)
            else:
                return draft, True

        if draft.is_deleted:
            # editing a draft that was discarded elsewhere: bring it back only if the client knows it was deleted
            # (base_version 0 = "new draft with this id"); otherwise tell the app it is gone.
            if base_version != 0:
                raise DraftError("draft_deleted", "This draft was deleted on another device.", 410)
            draft.is_deleted = False
            draft.deleted_at = None
        elif base_version != draft.version:
            # same content re-sent after a lost response is NOT a conflict
            if draft.kind == kind and draft.data == data and draft.media == media:
                return draft, False
            raise DraftError("draft_conflict", "This draft was changed on another device.", 409,
                             server=draft_payload(draft))

        draft.kind = kind
        draft.data = data
        draft.media = media
        draft.client_updated_at = client_updated_at
        draft.version = F("version") + 1
        draft.save()
        draft.refresh_from_db()
        return draft, False


def delete_draft(user, client_id):
    """Soft delete. Idempotent: a draft that is already gone is a success (Discard may be retried)."""
    updated = Draft.objects.filter(user=user, client_id=client_id, is_deleted=False).update(
        is_deleted=True, deleted_at=timezone.now(), updated_at=timezone.now(), version=F("version") + 1)
    return bool(updated)
