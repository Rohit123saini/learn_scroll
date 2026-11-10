"""
message/study_room_state_ops.py

S3 — whiteboard (`StudyRoomState`) ke liye role check, snapshot history aur undo/restore.

Pehle `StudyRoomStateView` me koi bhi member `PUT` se poora board overwrite kar sakta tha (last-write-wins),
`DELETE` se sabka board + sticky notes ud jaate the, aur payload size ki koi limit nahi thi.

Yahan:
  * `can_manage_room`     — host / moderator (delete + restore ka haq).
  * `create_snapshot`     — board JSON (+ optional sticky notes) ka history row (`StudyRoomStateSnapshot`).
  * `maybe_snapshot_before_overwrite` — autosave PUT se pehle (rate-limited) purana board bacha lo.
  * `restore_snapshot`    — snapshot se board (+ notes) wapas; pehle current ka backup.
Settings (sab optional, defaults neeche):
  STUDY_ROOM_STATE_MAX_BYTES (2 MB), STUDY_ROOM_STATE_MAX_PAGES (200), STUDY_ROOM_STATE_REQUIRE_VERSION (True),
  STUDY_ROOM_SNAPSHOT_MIN_INTERVAL_SECONDS (300), STUDY_ROOM_SNAPSHOT_KEEP (20), STUDY_ROOM_UNDO_WINDOW_SECONDS (86400).
"""

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from . import sticky_notes
from .group_rules import is_group_admin_or_mod
from .models import (
    ConversationType,
    StudyRoomNote,
    StudyRoomSession,
    StudyRoomState,
    StudyRoomStateSnapshot,
)

logger = logging.getLogger(__name__)

REASON_PERIODIC = StudyRoomStateSnapshot.Reason.PERIODIC
REASON_DELETE = StudyRoomStateSnapshot.Reason.DELETE
REASON_RESTORE = StudyRoomStateSnapshot.Reason.RESTORE


def max_state_bytes() -> int:
    return int(getattr(settings, 'STUDY_ROOM_STATE_MAX_BYTES', 2 * 1024 * 1024))


def max_pages() -> int:
    return int(getattr(settings, 'STUDY_ROOM_STATE_MAX_PAGES', 200))


def require_version() -> bool:
    return bool(getattr(settings, 'STUDY_ROOM_STATE_REQUIRE_VERSION', True))


def _min_interval() -> timedelta:
    return timedelta(seconds=int(getattr(settings, 'STUDY_ROOM_SNAPSHOT_MIN_INTERVAL_SECONDS', 300)))


def _keep() -> int:
    return int(getattr(settings, 'STUDY_ROOM_SNAPSHOT_KEEP', 20))


def undo_window() -> timedelta:
    return timedelta(seconds=int(getattr(settings, 'STUDY_ROOM_UNDO_WINDOW_SECONDS', 24 * 3600)))


# ----------------------------------------------------------------------
# Role
# ----------------------------------------------------------------------
def can_manage_room(conversation, user) -> bool:
    """
    Board clear / restore ka haq:
      * 1-1 (DM) study room: dono participants (sirf 2 log hain).
      * group (S4): effective role host / co-host — group admin / moderator (default co-host), current
        session ka host, ya host ne jise co-host banaya. Removed / waiting-room wale ko nahi.
    """
    if conversation.type != ConversationType.GROUP:
        return True
    from . import study_room_roles   # local import — circular se bachne ke liye
    role, _policy = study_room_roles.role_and_policy(conversation, user.id)
    return study_room_roles.is_manager(role)


# ----------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------
def snapshot_notes(conversation_id):
    """Abhi ke (non-deleted) sticky notes wire-format me."""
    return [sticky_notes.note_to_wire(n) for n in StudyRoomNote.objects.filter(room_id=conversation_id)]


def _state_size(state) -> int:
    import json
    try:
        return len(json.dumps(state, separators=(',', ':'), ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError):
        return 0


def create_snapshot(conversation, user, reason, state, version, notes=None):
    from common.db_retry import DEFAULT_WRITE_RETRIES, retry_on_db_lock

    # T2.2 - transient "database is locked" par retry (atomic block ke andar helper khud retry nahi karta; wahan
    # caller ka savepoint + try/except use sambhalta hai).
    snap = retry_on_db_lock(
        lambda: StudyRoomStateSnapshot.objects.create(
            conversation=conversation,
            created_by=user if getattr(user, 'id', None) else None,
            reason=reason,
            version=version or 0,
            state=state if isinstance(state, dict) else {},
            notes=notes,
            size_bytes=_state_size(state),
        ),
        retries=DEFAULT_WRITE_RETRIES,
    )
    try:
        prune_snapshots(conversation.id)
    except Exception:
        logger.exception("study room snapshot prune failed conv=%s", conversation.id)
    return snap


def maybe_snapshot_before_overwrite(conversation, row, user, now=None):
    """Autosave PUT se pehle purana board (rate-limited: har ~5 min me ek). Khali board ka snapshot nahi."""
    if row is None or not isinstance(row.state, dict) or not row.state.get('pages'):
        return None
    now = now or timezone.now()
    last = (
        StudyRoomStateSnapshot.objects.filter(conversation_id=conversation.id, reason=REASON_PERIODIC)
        .order_by('-created_at').first()
    )
    if last is not None and now - last.created_at < _min_interval():
        return None
    # Periodic snapshot me `notes=None` — restore sticky notes ko chhedta nahi.
    return create_snapshot(conversation, user, REASON_PERIODIC, row.state, row.version, notes=None)


def prune_snapshots(conversation_id, now=None):
    """Periodic/restore: sirf naye `STUDY_ROOM_SNAPSHOT_KEEP`; delete-snapshots: undo window ke baad hata do."""
    now = now or timezone.now()
    qs = StudyRoomStateSnapshot.all_objects.filter(conversation_id=conversation_id)
    expired = qs.filter(reason=REASON_DELETE, created_at__lt=now - undo_window())
    expired.delete()
    others = list(
        qs.exclude(reason=REASON_DELETE).order_by('-created_at').values_list('id', flat=True)
    )
    extra = others[_keep():]
    if extra:
        StudyRoomStateSnapshot.all_objects.filter(id__in=extra).delete()


def snapshot_restorable(snapshot, now=None) -> bool:
    now = now or timezone.now()
    if snapshot.reason == REASON_DELETE:
        return now - snapshot.created_at <= undo_window()
    return True


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def restore_snapshot(conversation, snapshot, user, now=None):
    """
    Snapshot se board wapas. Pehle current board (+ notes) ka backup snapshot (reason=restore) banta hai
    taaki restore bhi undo ho sake. Returns (new_version, notes_restored).
    """
    now = now or timezone.now()
    with transaction.atomic():
        sticky_notes._lock_room(conversation.id)
        row = StudyRoomState.objects.select_for_update().filter(conversation=conversation).first()
        cur_version = row.version if row else 0
        create_snapshot(
            conversation, user, REASON_RESTORE,
            row.state if row else {}, cur_version, notes=snapshot_notes(conversation.id),
        )
        new_version = cur_version + 1
        state = snapshot.state if isinstance(snapshot.state, dict) else {}
        if row is None:
            StudyRoomState.objects.create(
                conversation=conversation, state=state, version=new_version, updated_by=user,
            )
        else:
            row.state = state
            row.version = new_version
            row.updated_by = user
            row.save(update_fields=['state', 'version', 'updated_by', 'updated_at'])

        restored = 0
        if snapshot.notes is not None:
            StudyRoomNote.all_objects.filter(room_id=conversation.id).delete()
            raw = [n for n in (snapshot.notes or []) if isinstance(n, dict)][: sticky_notes.MAX_NOTES_PER_ROOM]
            User = get_user_model()
            author_ids = {_parse_uuid(n.get('userId')) for n in raw} - {None}
            valid_authors = set(User.objects.filter(id__in=author_ids).values_list('id', flat=True)) if author_ids else set()
            objs = []
            for n in raw:
                try:
                    note_id = _parse_uuid(n.get('id')) or uuid.uuid4()
                    author = _parse_uuid(n.get('userId'))
                    objs.append(StudyRoomNote(
                        id=note_id,
                        room_id=conversation.id,
                        created_by_id=author if author in valid_authors else None,
                        page_id=str(n.get('pageId') or 'page_1')[:64],
                        x=float(n.get('x', 0)), y=float(n.get('y', 0)),
                        width=float(n.get('width', 160)), height=float(n.get('height', 160)),
                        color=int(n.get('color', 0xFFFFF59D)),
                        text=str(n.get('text') or '')[: sticky_notes.MAX_TEXT_LENGTH],
                        z_index=int(n.get('zIndex', 0)),
                        field_ts={k: int(v) for k, v in (n.get('fieldTs') or {}).items()},
                    ))
                except (TypeError, ValueError):
                    continue
            if objs:
                StudyRoomNote.objects.bulk_create(objs)
            restored = len(objs)
        return new_version, restored
