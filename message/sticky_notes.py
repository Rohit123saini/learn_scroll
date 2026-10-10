# message/sticky_notes.py
"""
Collaborative sticky notes for the Study Room — saara business logic yahan
hai taaki `consumers.py` (WebSocket) aur `views.py` (REST GET) dono ek hi
source of truth use karein.

Transport (consumers.py):
    Client -> server, `{"type": "study_room_event", "action": <A>, "data": {...}}`
        note_add     {opId, ts, note: {id, pageId, x, y, width, height, color, text}}
        note_move    {opId, ts, noteId, x?, y?, width?, height?, front?}
        note_edit    {opId, ts, noteId, text?, color?, front?}
        note_front   {opId, ts, noteId}
        note_delete  {opId, ts, noteId}
        note_drag    {noteId, x, y, width?, height?}     (ephemeral, NOT persisted)

    Server -> client (same envelope):
        note_ack      -> sirf bhejne wale ko: {opId, noteId, status, reason?, note?, rejected?}
                         status: applied | partial | stale | deleted | rejected
        note_upsert   -> baaki sab ko: {note, source}
        note_removed  -> baaki sab ko: {noteId, pageId, updatedAt}
        note_drag     -> baaki sab ko: {noteId, x, y, width?, height?, userId}

Conflict handling — per-field-group last-write-wins:
    Har note ke 5 "field groups" hain:  pos (x,y) | size (width,height) |
    color | text | z (z_index). `StudyRoomNote.field_ts[group]` us group ka
    last-write timestamp (epoch ms) hai. Incoming op ka `ts` >= stored ts
    ho tabhi wo group apply hota hai (tie => baad me pahunchne wala jeetta
    hai); warna wo group `rejected` me jaata hai aur sender ko ack me
    authoritative note wapas milta hai (UI rollback ke liye). Groups
    independent hain: A note ko MOVE kare aur B usi waqt TEXT edit kare to
    dono survive karte hain — sirf same group pe takraav ho to ek jeetta hai.

    `ts` client ka clock hai (client `server_time_ms` se offset correct
    karta hai) — isliye server `now + FUTURE_SKEW_MS` se aage ka ts clamp
    kar deta hai, warna ek galat clock wala phone note ko hamesha ke liye
    "future" me freeze kar sakta tha.

Writes har room ke liye Postgres advisory lock se serialize hote hain
(`pg_advisory_xact_lock`) — isse z_index / note-limit strictly consistent
rehte hain, bina `Conversation` row lock kiye (jo har chat message send
ke saath contend karta).
"""
import logging
import math
import time
import uuid
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from .group_rules import check_group_permission, is_group_admin_or_mod
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    StudyRoomNote,
    StudyRoomState,
)

logger = logging.getLogger(__name__)
User = get_user_model()

# ----------------------------------------------------------------------
# Limits / constants
# ----------------------------------------------------------------------
MAX_NOTES_PER_ROOM = 200
MAX_TEXT_LENGTH = 2000
MIN_WIDTH, MAX_WIDTH = 80.0, 800.0
MIN_HEIGHT, MAX_HEIGHT = 60.0, 800.0
COORD_LIMIT = 100_000.0
FUTURE_SKEW_MS = 5_000
DEFAULT_WIDTH = 160.0
DEFAULT_HEIGHT = 160.0
DEFAULT_COLOR = 0xFFFFF59D  # Colors.yellow.shade200 jaisa (Flutter)
MAX_COLOR = 0xFFFFFFFF
MAX_PAGE_ID_LENGTH = 64
Z_RENORMALIZE_THRESHOLD = 100_000

GROUP_POS, GROUP_SIZE, GROUP_COLOR, GROUP_TEXT, GROUP_Z = 'pos', 'size', 'color', 'text', 'z'
ALL_GROUPS = (GROUP_POS, GROUP_SIZE, GROUP_COLOR, GROUP_TEXT, GROUP_Z)

ACTION_ADD = 'note_add'
ACTION_MOVE = 'note_move'
ACTION_EDIT = 'note_edit'
ACTION_FRONT = 'note_front'
ACTION_DELETE = 'note_delete'
ACTION_DRAG = 'note_drag'

# `note_drag` persist nahi hota (sirf live preview relay) — baaki sab
# persisted ops hain (ack milta hai).
PERSISTED_ACTIONS = frozenset({ACTION_ADD, ACTION_MOVE, ACTION_EDIT, ACTION_FRONT, ACTION_DELETE})
NOTE_ACTIONS = PERSISTED_ACTIONS | {ACTION_DRAG}

# Board-level events jinka asar notes pe bhi padta hai (consumers.py in
# events ko relay to karta hi hai, ab notes bhi DB me saaf karta hai).
BOARD_CLEAR_ACTIONS = frozenset({'clear_board', 'clear_board_keep_text', 'remove_page'})


class NoteValidationError(ValueError):
    """Payload invalid — `str(e)` short machine-readable reason hai."""


# ----------------------------------------------------------------------
# Pure helpers (DB-free, unit-testable)
# ----------------------------------------------------------------------
def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _clean_float(v, lo, hi, name):
    if not _is_number(v):
        raise NoteValidationError(f'{name}_invalid')
    return float(min(max(v, lo), hi))


def clean_x(v):
    return _clean_float(v, -COORD_LIMIT, COORD_LIMIT, 'x')


def clean_y(v):
    return _clean_float(v, -COORD_LIMIT, COORD_LIMIT, 'y')


def clean_width(v):
    return _clean_float(v, MIN_WIDTH, MAX_WIDTH, 'width')


def clean_height(v):
    return _clean_float(v, MIN_HEIGHT, MAX_HEIGHT, 'height')


def clean_color(v):
    # bool int ka subclass hai — `True` ko color 1 nahi maanna.
    if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > MAX_COLOR:
        raise NoteValidationError('color_invalid')
    return v


def clean_text(v):
    if not isinstance(v, str):
        raise NoteValidationError('text_invalid')
    # Postgres text column NUL (\x00) store nahi kar sakta — poori query
    # 500 de deti hai. Hata do, reject nahi karte (paste se aa sakta hai).
    v = v.replace('\x00', '')
    if len(v) > MAX_TEXT_LENGTH:
        raise NoteValidationError('text_too_long')
    return v


def clean_page_id(v):
    if v is None or v == '':
        return 'page_1'
    if not isinstance(v, str) or len(v) > MAX_PAGE_ID_LENGTH:
        raise NoteValidationError('page_id_invalid')
    return v


def parse_uuid(v, name='id'):
    try:
        return uuid.UUID(str(v))
    except (ValueError, AttributeError, TypeError):
        raise NoteValidationError(f'{name}_invalid')


def clean_ts(v, now_ms):
    """Client ts (epoch ms) -> int, `now + FUTURE_SKEW_MS` pe clamped. Missing/
    garbage ts => server ka `now` (ye op "abhi ka" maana jaata hai)."""
    if not _is_number(v) or v <= 0:
        return now_ms
    return int(min(v, now_ms + FUTURE_SKEW_MS))


def plan_lww(field_ts, incoming):
    """
    Pure last-write-wins planner.

    `field_ts`  : {group: stored_ts_ms}
    `incoming`  : {group: (values, ts_ms)}
    Returns (applied: {group: (values, ts)}, rejected: [group, ...]).
    Incoming tab apply hota hai jab ts >= stored ts (unseen group => 0).
    """
    applied, rejected = {}, []
    for group, (values, ts) in incoming.items():
        if ts >= int(field_ts.get(group, 0) or 0):
            applied[group] = (values, ts)
        else:
            rejected.append(group)
    return applied, rejected


def build_groups_from_payload(action, payload, ts):
    """
    note_move / note_edit / note_front ka payload -> {group: (values, ts)}.
    Invalid/incomplete payload => NoteValidationError.
    """
    groups = {}
    if action == ACTION_MOVE:
        has_pos = 'x' in payload or 'y' in payload
        has_size = 'width' in payload or 'height' in payload
        if has_pos:
            if 'x' not in payload or 'y' not in payload:
                raise NoteValidationError('pos_incomplete')
            groups[GROUP_POS] = ({'x': clean_x(payload['x']), 'y': clean_y(payload['y'])}, ts)
        if has_size:
            if 'width' not in payload or 'height' not in payload:
                raise NoteValidationError('size_incomplete')
            groups[GROUP_SIZE] = (
                {'width': clean_width(payload['width']), 'height': clean_height(payload['height'])},
                ts,
            )
        if not groups:
            raise NoteValidationError('empty_move')
    elif action == ACTION_EDIT:
        if 'text' in payload:
            groups[GROUP_TEXT] = ({'text': clean_text(payload['text'])}, ts)
        if 'color' in payload:
            groups[GROUP_COLOR] = ({'color': clean_color(payload['color'])}, ts)
        if not groups:
            raise NoteValidationError('empty_edit')
    elif action == ACTION_FRONT:
        groups[GROUP_Z] = ({}, ts)
    else:  # pragma: no cover — caller ne pehle hi action filter kiya hota hai
        raise NoteValidationError('unknown_action')

    # move/edit ke saath `front: true` = "is note ko upar laao" (touch =>
    # front-most) — alag round-trip ki zaroorat nahi.
    if payload.get('front') is True and GROUP_Z not in groups:
        groups[GROUP_Z] = ({}, ts)
    return groups


def clean_drag_payload(payload):
    """`note_drag` (ephemeral live-preview) validate. Invalid => None (silently drop)."""
    try:
        out = {
            'noteId': str(parse_uuid(payload.get('noteId'), 'noteId')),
            'x': clean_x(payload.get('x')),
            'y': clean_y(payload.get('y')),
        }
        if 'width' in payload and 'height' in payload:
            out['width'] = clean_width(payload['width'])
            out['height'] = clean_height(payload['height'])
    except NoteValidationError:
        return None
    return out


def note_to_wire(note):
    return {
        'id': str(note.id),
        'pageId': note.page_id,
        'userId': str(note.created_by_id) if note.created_by_id else '',
        'x': note.x,
        'y': note.y,
        'width': note.width,
        'height': note.height,
        'color': int(note.color),
        'text': note.text,
        'zIndex': note.z_index,
        'updatedAt': note.updated_at.isoformat() if note.updated_at else None,
        'fieldTs': {k: int(v) for k, v in (note.field_ts or {}).items()},
    }


def server_time_ms():
    return int(time.time() * 1000)


# ----------------------------------------------------------------------
# Access control
# ----------------------------------------------------------------------
@dataclass
class RoomAccess:
    conversation: object = None
    allowed: bool = False
    reason: str = ''


def check_room_access(conversation_id, user):
    """
    Study-room notes padhne/likhne ka haq: conversation ka ACTIVE member +
    (group me) `study_room_permission` — bilkul `StudyRoomJoinView` jaisa
    rule, taaki jo join nahi kar sakta wo notes bhi na dekh sake.
    """
    is_member = ConversationParticipant.objects.filter(
        conversation_id=conversation_id, user_id=user.id, left_at__isnull=True
    ).exists()
    if not is_member:
        return RoomAccess(reason='not_member')
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if conversation is None:
        return RoomAccess(reason='not_member')
    if conversation.type == ConversationType.GROUP:
        group = getattr(conversation, 'group_detail', None)
        if group:
            ok, _ = check_group_permission(group, user.id, 'study_room_permission')
            if not ok:
                return RoomAccess(conversation=conversation, reason='forbidden')
    return RoomAccess(conversation=conversation, allowed=True)


def can_delete_note(conversation, user, note):
    """
    Delete policy (ek hi jagah, badalna ho to yahin badlo):
      * note ka creator hamesha,
      * group me admin/moderator kisi bhi note ko (moderation),
      * 1-1 chat me dono participants (sirf 2 log hain).
    Move/edit/color/resize sab participants kar sakte hain (collaborative).
    """
    if note.created_by_id == user.id:
        return True
    if conversation.type == ConversationType.GROUP:
        group = getattr(conversation, 'group_detail', None)
        return bool(group) and is_group_admin_or_mod(group, user.id)
    return True


# ----------------------------------------------------------------------
# DB helpers
# ----------------------------------------------------------------------
def _lock_room(room_id):
    """Per-room transaction-scoped advisory lock (Postgres). `atomic()` ke andar hi call karo.

    Sirf Postgres pe lagta hai. SQLite (local dev) me `hashtext` / advisory locks nahi hote
    (OperationalError: no such function) aur wahan write-lock waise bhi poore DB pe hota hai,
    isliye skip kar dete hain.
    """
    if connection.vendor != 'postgresql':
        return
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"studynotes:{room_id}"])


def _next_z(room_id):
    """Room ke sabse upar wale z + 1. Lock ke andar call karo."""
    top = StudyRoomNote.objects.filter(room_id=room_id).aggregate(m=Max('z_index'))['m']
    top = 0 if top is None else top
    if top >= Z_RENORMALIZE_THRESHOLD:
        _renormalize_z(room_id)
        top = StudyRoomNote.objects.filter(room_id=room_id).aggregate(m=Max('z_index'))['m'] or 0
    return top + 1


def _renormalize_z(room_id):
    """Z values ko 1..N me compact karo (order preserve) — unbounded growth rokne ke liye."""
    for i, note in enumerate(StudyRoomNote.objects.filter(room_id=room_id).order_by('z_index', 'created_at', 'id'), 1):
        if note.z_index != i:
            StudyRoomNote.objects.filter(pk=note.pk).update(z_index=i)


@dataclass
class NoteOpResult:
    ack: dict = field(default_factory=dict)
    # [(action, data)] — baaki participants ko (sender ke alawa) bhejna hai.
    broadcasts: list = field(default_factory=list)


def _ack(op_id, note_id, status, reason=None, note=None, rejected=None):
    ack = {'opId': op_id, 'noteId': note_id, 'status': status}
    if reason:
        ack['reason'] = reason
    if note is not None:
        ack['note'] = note_to_wire(note)
    if rejected:
        ack['rejected'] = list(rejected)
    return NoteOpResult(ack=ack)


# ----------------------------------------------------------------------
# Persisted ops
# ----------------------------------------------------------------------
def apply_note_op(conversation_id, user, action, payload, now_ms=None):
    """
    Ek persisted note op (add/move/edit/front/delete) apply karo.
    Kabhi raise nahi karta — hamesha `NoteOpResult` (ack + broadcasts).
    Sync function hai; consumer isse `database_sync_to_async` me chalata hai.
    """
    payload = payload if isinstance(payload, dict) else {}
    op_id = str(payload.get('opId') or '')[:64]
    note_id_raw = (payload.get('note') or {}).get('id') if action == ACTION_ADD else payload.get('noteId')
    note_id = str(note_id_raw) if note_id_raw else ''
    now_ms = now_ms if now_ms is not None else server_time_ms()

    try:
        access = check_room_access(conversation_id, user)
        if not access.allowed:
            return _ack(op_id, note_id, 'rejected', reason=access.reason)

        ts = clean_ts(payload.get('ts'), now_ms)

        if action == ACTION_ADD:
            return _op_add(access.conversation, user, op_id, payload, ts)
        if action == ACTION_DELETE:
            return _op_delete(access.conversation, user, op_id, payload)
        if action in (ACTION_MOVE, ACTION_EDIT, ACTION_FRONT):
            return _op_update(access.conversation, user, action, op_id, payload, ts)
        return _ack(op_id, note_id, 'rejected', reason='unknown_action')
    except NoteValidationError as e:
        return _ack(op_id, note_id, 'rejected', reason=str(e))
    except Exception:
        logger.exception("apply_note_op failed action=%s user=%s room=%s", action, user.id, conversation_id)
        return _ack(op_id, note_id, 'rejected', reason='server_error')


def _op_add(conversation, user, op_id, payload, ts):
    raw = payload.get('note')
    if not isinstance(raw, dict):
        raise NoteValidationError('note_missing')
    note_uuid = parse_uuid(raw.get('id'), 'id')

    page_id = clean_page_id(raw.get('pageId'))
    values = {
        'x': clean_x(raw.get('x', 100)),
        'y': clean_y(raw.get('y', 100)),
        'width': clean_width(raw.get('width', DEFAULT_WIDTH)),
        'height': clean_height(raw.get('height', DEFAULT_HEIGHT)),
        'color': clean_color(raw.get('color', DEFAULT_COLOR)),
        'text': clean_text(raw.get('text', '')),
    }

    with transaction.atomic():
        _lock_room(conversation.id)
        existing = StudyRoomNote.all_objects.filter(id=note_uuid).first()
        if existing is not None:
            if existing.room_id != conversation.id:
                # Kisi doosre room ka id — existence leak mat karo.
                return _ack(op_id, str(note_uuid), 'rejected', reason='forbidden')
            if existing.is_deleted:
                return _ack(op_id, str(note_uuid), 'deleted')
            # Idempotent retry (reconnect ke baad same add dobara aaya) — koi
            # naya broadcast nahi, bas authoritative state wapas.
            return _ack(op_id, str(note_uuid), 'applied', note=existing)

        if StudyRoomNote.objects.filter(room_id=conversation.id).count() >= MAX_NOTES_PER_ROOM:
            return _ack(op_id, str(note_uuid), 'rejected', reason='limit_reached')

        note = StudyRoomNote.objects.create(
            id=note_uuid,
            room_id=conversation.id,
            created_by=user,
            last_edited_by=user,
            page_id=page_id,
            z_index=_next_z(conversation.id),
            field_ts={g: ts for g in ALL_GROUPS},
            **values,
        )

    result = _ack(op_id, str(note.id), 'applied', note=note)
    result.broadcasts.append(('note_upsert', {'note': note_to_wire(note), 'source': ACTION_ADD}))
    return result


def _op_update(conversation, user, action, op_id, payload, ts):
    note_uuid = parse_uuid(payload.get('noteId'), 'noteId')
    incoming = build_groups_from_payload(action, payload, ts)

    with transaction.atomic():
        _lock_room(conversation.id)
        note = StudyRoomNote.all_objects.filter(id=note_uuid, room_id=conversation.id).first()
        if note is None:
            return _ack(op_id, str(note_uuid), 'rejected', reason='not_found')
        if note.is_deleted:
            return _ack(op_id, str(note_uuid), 'deleted')

        field_ts = dict(note.field_ts or {})
        applied, rejected = plan_lww(field_ts, incoming)

        update_fields = []
        for group, (values, group_ts) in applied.items():
            if group == GROUP_Z:
                # Server z assign karta hai: already sabse upar hai to no-op
                # (bekaar z bump aur broadcast nahi), warna max + 1.
                top = StudyRoomNote.objects.filter(room_id=conversation.id).aggregate(m=Max('z_index'))['m'] or 0
                if note.z_index < top or note.z_index == 0:
                    note.z_index = _next_z(conversation.id)
                    update_fields.append('z_index')
            else:
                for attr, value in values.items():
                    setattr(note, attr, value)
                    update_fields.append(attr)
            field_ts[group] = group_ts

        if applied:
            note.field_ts = field_ts
            note.last_edited_by = user
            note.save(update_fields=sorted(set(update_fields + ['field_ts', 'last_edited_by', 'updated_at'])))

    status = 'applied' if applied and not rejected else ('partial' if applied else 'stale')
    result = _ack(op_id, str(note.id), status, note=note, rejected=rejected)
    if applied:
        result.broadcasts.append(('note_upsert', {'note': note_to_wire(note), 'source': action}))
    return result


def _op_delete(conversation, user, op_id, payload):
    note_uuid = parse_uuid(payload.get('noteId'), 'noteId')
    with transaction.atomic():
        _lock_room(conversation.id)
        note = StudyRoomNote.all_objects.filter(id=note_uuid, room_id=conversation.id).first()
        if note is None or note.is_deleted:
            # Idempotent — already gone (ya kabhi thi hi nahi).
            return _ack(op_id, str(note_uuid), 'deleted')
        if not can_delete_note(conversation, user, note):
            return _ack(op_id, str(note.id), 'rejected', reason='forbidden', note=note)
        note.is_deleted = True
        note.last_edited_by = user
        note.save(update_fields=['is_deleted', 'last_edited_by', 'updated_at'])

    result = _ack(op_id, str(note.id), 'deleted')
    result.broadcasts.append((
        'note_removed',
        {
            'noteId': str(note.id),
            'pageId': note.page_id,
            'updatedAt': note.updated_at.isoformat() if note.updated_at else None,
        },
    ))
    return result


# ----------------------------------------------------------------------
# Board-level side effects (clear board / remove page / end session)
# ----------------------------------------------------------------------
def clear_notes_for_page(conversation_id, page_id, user=None):
    """
    Page ke saare notes soft-delete (tombstone). Kitne hue return karta hai.
    `user` do to pehle study-room access check hota hai (WS se aane wale
    clear-board events ke liye) — bina access wala kuch delete nahi kar sakta.
    """
    if not page_id or not isinstance(page_id, str):
        return 0
    if user is not None and not check_room_access(conversation_id, user).allowed:
        return 0
    with transaction.atomic():
        _lock_room(conversation_id)
        return StudyRoomNote.objects.filter(room_id=conversation_id, page_id=page_id).update(
            is_deleted=True, updated_at=timezone.now()
        )


def purge_room_notes(conversation_id):
    """Session khatam / nayi session shuru — tombstones samet SAB notes hard-delete."""
    with transaction.atomic():
        _lock_room(conversation_id)
        deleted, _ = StudyRoomNote.all_objects.filter(room_id=conversation_id).delete()
    return deleted


# ----------------------------------------------------------------------
# One-time migration: purane `StudyRoomState.state['pages'][*]['stickyNotes']`
# ----------------------------------------------------------------------
_LEGACY_NS = uuid.UUID('6f0d1b52-3c0a-4b64-9d5e-5d1f4d7a9c11')


def import_legacy_notes(conversation):
    """
    Deploy se pehle jo notes whiteboard snapshot JSON me the (`stickyNotes`:
    [{id, userId, text, dx, dy, color}]) unko `StudyRoomNote` rows me le aao,
    aur snapshot se `stickyNotes` hata do taaki ye sirf ek baar ho.
    Sirf tab import karta hai jab room me abhi koi note row nahi hai.
    Kitne import hue return karta hai. Idempotent.
    """
    with transaction.atomic():
        _lock_room(conversation.id)
        state_row = StudyRoomState.objects.select_for_update().filter(conversation=conversation).first()
        if state_row is None:
            return 0
        state = state_row.state if isinstance(state_row.state, dict) else {}
        pages = state.get('pages')
        if not isinstance(pages, list) or not any(isinstance(p, dict) and p.get('stickyNotes') for p in pages):
            return 0

        imported = 0
        already_has_rows = StudyRoomNote.all_objects.filter(room_id=conversation.id).exists()
        if not already_has_rows:
            now_ms = server_time_ms()
            z = 0
            # FK constraints Postgres me DEFERRED hain (commit pe check) — savepoint
            # unhe pakad nahi sakta, isliye creator ids pehle hi verify kar lo.
            candidate_ids = {
                int(n['userId'])
                for pg in pages if isinstance(pg, dict)
                for n in (pg.get('stickyNotes') or [])
                if isinstance(n, dict) and str(n.get('userId')).isdigit()
            }
            known_users = set(User.objects.filter(id__in=candidate_ids).values_list('id', flat=True))
            for page in pages:
                if not isinstance(page, dict):
                    continue
                page_id = str(page.get('id') or 'page_1')[:MAX_PAGE_ID_LENGTH]
                for legacy in page.get('stickyNotes') or []:
                    if imported >= MAX_NOTES_PER_ROOM:
                        break
                    if not isinstance(legacy, dict):
                        continue
                    try:
                        legacy_id = str(legacy.get('id') or uuid.uuid4())
                        try:
                            note_uuid = uuid.UUID(legacy_id)
                        except ValueError:
                            note_uuid = uuid.uuid5(_LEGACY_NS, f"{conversation.id}:{legacy_id}")
                        creator = legacy.get('userId')
                        try:
                            color = clean_color(legacy.get('color', DEFAULT_COLOR))
                        except NoteValidationError:
                            color = DEFAULT_COLOR
                        # Savepoint: ek kharab row (jaise ab-delete ho chuka
                        # creator id => FK error) poori transaction abort na kare.
                        with transaction.atomic():
                            StudyRoomNote.objects.create(
                                id=note_uuid,
                                room_id=conversation.id,
                                created_by_id=int(creator) if str(creator).isdigit() and int(creator) in known_users else None,
                                page_id=page_id,
                                x=clean_x(legacy.get('dx', 100)),
                                y=clean_y(legacy.get('dy', 100)),
                                width=140.0,
                                height=140.0,
                                color=color,
                                text=clean_text(str(legacy.get('text') or '')[:MAX_TEXT_LENGTH]),
                                z_index=z + 1,
                                field_ts={g: now_ms for g in ALL_GROUPS},
                            )
                        z += 1
                        imported += 1
                    except Exception:  # ek kharab legacy note poori import na rok de
                        logger.warning("legacy sticky note skipped room=%s", conversation.id, exc_info=True)

        for page in pages:
            if isinstance(page, dict):
                page.pop('stickyNotes', None)
        state_row.state = state
        state_row.save(update_fields=['state', 'updated_at'])
        return imported
