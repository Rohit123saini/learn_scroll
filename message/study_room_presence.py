"""
message/study_room_presence.py

S5 — Study room presence + attendance minutes (LiveKit webhook se).

Pehle `StudyRoomAttendance` me join par din ki sirf ek row banti thi — kitni der room me raha / kab gaya,
kuch nahi. Ab C2 ka LiveKit webhook (`livekit_webhook.handle_message_event`) study rooms (`study_<conv>[_<suffix>]`)
ke events bhi yahin bhejta hai:

  participant_joined                       -> `StudyRoomPresence` row (open interval)
  participant_left / connection_aborted    -> interval band + minutes `StudyRoomAttendance.total_seconds` me
  room_finished                            -> us room ke saare open intervals band

Wahi minutes:
  * streak ko "kam se kam N minute" se calculate karte hain (`qualifies()` — attendance_utils use karta hai),
  * `leaderboard` ke study-time board (`scope_type=study_time`) ko feed karte hain.

Design rules
  * IDEMPOTENT: row ki key (room_name, user, LiveKit `participant.joined_at`) hai — duplicate delivery (dono webhook
    URL configured ho) ya out-of-order (left pehle, joined baad me) me bhi minutes ek hi baar jodte hain.
  * Minutes din ke hisaab se split hote hain (local midnight pe) — raat ko 11:50 se 12:20 tak padhne wale ke dono
    din ke time me sahi hissa jata hai.
  * Ek interval ka credit `study_room_max_duration()` (default 12h) se zyada nahi.
  * Webhook kabhi kho jaye to `sweep_stale()` (Celery beat, `message.sweep_stale_study_presence`) LiveKit se
    poochkar band karta hai; jab pata nahi chalta tab sirf thoda (default 1h) credit deta hai — over-credit nahi.
  * Kabhi raise nahi karta (webhook hamesha 200 de).

Settings (sab optional):
  STUDY_ROOM_PRESENCE_ENABLED              (True)  False => join hi attendance (purana behaviour), minutes streak me nahi.
  STUDY_ROOM_STREAK_MIN_MINUTES            (10)    din tabhi streak me ginta hai jab itne minute room me raha. 0 => join kaafi.
  STUDY_ROOM_PRESENCE_STALE_SECONDS        (7200)  itne purane open interval ko sweeper verify karega.
  STUDY_ROOM_PRESENCE_STALE_CREDIT_SECONDS (3600)  leave webhook kho gaya to maximum itna credit.
  STUDY_ROOM_PRESENCE_SWEEP_BATCH          (200)   ek sweep me max rows.
  STUDY_ROOM_IDLE_FILTER_ENABLED           (True)  S5 leftover: heartbeat bhejne wale clients ka idle time minutes me nahi.
  STUDY_ROOM_ACTIVITY_PING_SECONDS         (60)    client heartbeat interval (pehle ping ka credit).
  STUDY_ROOM_ACTIVITY_GAP_SECONDS          (150)   2 pings ke beech is gap tak active maana jata hai; zyada gap = idle.

Idle time (S5 leftover): client active hone par (touch / mic / camera / screen share) ~har 60s `record_activity()` (view:
`study-room/<id>/activity/`) bulata hai. Har ping `min(ab - pichhla ping, GAP)` seconds `active_seconds` me jodta hai — spam
karne se bhi real elapsed time se zyada credit nahi milta. Interval band hote hi credit = `active_seconds` (+ aakhri ping ke baad
GAP tak ka grace), connected time se zyada nahi. Jis row me kabhi ping nahi aaya (purana client) uska poora time gina jata hai.
"""

import logging
import re
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import ConversationParticipant, StudyRoomAttendance, StudyRoomPresence

logger = logging.getLogger(__name__)

STUDY_ROOM_PREFIX = 'study_'
# `study_<conversation uuid>` ya `study_<conversation uuid>_<suffix>` (build_room_name, study_room_session.py).
_ROOM_RE = re.compile(
    r'^study_(?P<conv>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:_[0-9A-Za-z]+)?$'
)
_BOT_PREFIXES = ('EG_', 'IN_', 'ST_', 'AJ_')   # egress / ingress / agent bots (livekit_webhook ke jaisa)

REASON_LEFT = 'left'
REASON_ROOM_FINISHED = 'room_finished'
REASON_SWEPT = 'swept'
REASON_CAPPED = 'capped'


# ----------------------------------------------------------------------
# settings
# ----------------------------------------------------------------------
def _int_setting(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def presence_enabled() -> bool:
    return bool(getattr(settings, 'STUDY_ROOM_PRESENCE_ENABLED', True))


def streak_min_seconds() -> int:
    return max(_int_setting('STUDY_ROOM_STREAK_MIN_MINUTES', 10), 0) * 60


def streak_min_minutes() -> int:
    return streak_min_seconds() // 60


def idle_filter_enabled() -> bool:
    return bool(getattr(settings, 'STUDY_ROOM_IDLE_FILTER_ENABLED', True))


def _activity_ping_seconds() -> int:
    return max(_int_setting('STUDY_ROOM_ACTIVITY_PING_SECONDS', 60), 1)


def _activity_gap_seconds() -> int:
    return max(_int_setting('STUDY_ROOM_ACTIVITY_GAP_SECONDS', 150), _activity_ping_seconds())


def _max_duration() -> timedelta:
    from .livekit_utils import study_room_max_duration

    return study_room_max_duration()


def _stale_after() -> timedelta:
    return timedelta(seconds=max(_int_setting('STUDY_ROOM_PRESENCE_STALE_SECONDS', 2 * 3600), 60))


def _stale_credit_cap() -> timedelta:
    return timedelta(seconds=max(_int_setting('STUDY_ROOM_PRESENCE_STALE_CREDIT_SECONDS', 3600), 0))


def _sweep_batch() -> int:
    return max(_int_setting('STUDY_ROOM_PRESENCE_SWEEP_BATCH', 200), 1)


# ----------------------------------------------------------------------
# streak qualification (attendance_utils yahin se leta hai — ek hi rule)
# ----------------------------------------------------------------------
def qualifies(total_seconds: int, presence_tracked: bool, live_seconds: int = 0) -> bool:
    """Kya ye din streak/attended count me jayega?

    * threshold 0 ya purani (untracked) row => haan (backward compatible).
    * warna us din ke minutes (+ abhi room me chal rahe interval ke) >= N minute.
    """
    min_seconds = streak_min_seconds()
    if min_seconds <= 0 or not presence_tracked:
        return True
    return int(total_seconds or 0) + int(live_seconds or 0) >= min_seconds


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def parse_room_name(room_name):
    """`study_<uuid>[_suffix]` -> conversation id (str), warna None (call / class rooms ignore)."""
    match = _ROOM_RE.match(room_name or '')
    return match.group('conv').lower() if match else None


def _from_epoch(value):
    """LiveKit seconds (ya ms) epoch -> aware datetime; 0 / None / bakwaas -> None."""
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value > 10_000_000_000:   # ms
        value = value / 1000.0
    try:
        return datetime.fromtimestamp(value, tz=dt_timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _event_time(event):
    return _from_epoch(getattr(event, 'created_at', 0)) or timezone.now()


def _participant_joined_at(participant, fallback):
    for attr in ('joined_at_ms', 'joined_at'):
        dt = _from_epoch(getattr(participant, attr, 0))
        if dt is not None:
            return dt
    return fallback


def _user_id(participant):
    identity = (getattr(participant, 'identity', '') or '').strip()
    if not identity or identity.startswith(_BOT_PREFIXES):
        return None
    return identity


def _active_member(conversation_id, user_id) -> bool:
    try:
        return ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user_id=user_id, left_at__isnull=True,
        ).exists()
    except (ValueError, TypeError, ValidationError):
        return False   # identity user id nahi (bakwaas) — ignore


def _next_local_midnight(dt):
    local = timezone.localtime(dt)
    return timezone.make_aware(datetime.combine(local.date() + timedelta(days=1), time.min))


# ----------------------------------------------------------------------
# idle time (S5 leftover)
# ----------------------------------------------------------------------
def active_ratio(joined_at, tracked, active_seconds, last_active_at, end) -> float:
    """[joined_at, end) me se kitna hissa ACTIVE tha (0..1). Heartbeat kabhi nahi aaya (`tracked=False`) ya filter OFF => 1.0."""
    if not tracked or not idle_filter_enabled():
        return 1.0
    total = (end - joined_at).total_seconds()
    if total <= 0:
        return 1.0
    active = float(active_seconds or 0)
    if last_active_at is not None:
        # aakhri ping ke baad GAP tak ka grace (user ping ke turant baad gaya ho sakta hai).
        active += min(max((end - last_active_at).total_seconds(), 0.0), float(_activity_gap_seconds()))
    return max(0.0, min(1.0, active / total))


def record_activity(conversation_id, user_id, now=None):
    """Client heartbeat: user ke abhi-open interval me active time jodo. Return: dict ya None (open interval nahi mila).

    Credit = `min(ab - pichhla ping, GAP)` (pehle ping par `min(PING, ab - joined_at)`) => ping spam se real time se
    zyada kabhi nahi milta. Kabhi raise nahi karta."""
    if not presence_enabled() or not idle_filter_enabled():
        return None
    now = now or timezone.now()
    try:
        with transaction.atomic():
            row = (
                StudyRoomPresence.objects.select_for_update()
                .filter(conversation_id=conversation_id, user_id=user_id, left_at__isnull=True)
                .order_by('-joined_at').first()
            )
            if row is None:
                return None
            if row.last_active_at is None:
                credit = min(float(_activity_ping_seconds()), max((now - row.joined_at).total_seconds(), 0.0))
            else:
                credit = min(max((now - row.last_active_at).total_seconds(), 0.0), float(_activity_gap_seconds()))
            elapsed = max((now - row.joined_at).total_seconds(), 0.0)
            row.active_seconds = int(min(float(row.active_seconds or 0) + credit, elapsed))
            row.last_active_at = now
            row.activity_tracked = True
            row.save(update_fields=['active_seconds', 'last_active_at', 'activity_tracked', 'updated_at'])
            return {'active_seconds': row.active_seconds}
    except Exception:
        logger.exception("study_room_presence: record_activity failed conv=%s", conversation_id)
        return None


# ----------------------------------------------------------------------
# crediting
# ----------------------------------------------------------------------
def _add_to_attendance(presence, day, seconds, seg_start, seg_end, count_join):
    attendance, _ = StudyRoomAttendance.objects.get_or_create(
        conversation_id=presence.conversation_id,
        user_id=presence.user_id,
        attended_date=day,
        defaults={'session_id': presence.room_name, 'presence_tracked': True},
    )
    attendance = StudyRoomAttendance.objects.select_for_update().get(pk=attendance.pk)
    attendance.total_seconds = int(attendance.total_seconds or 0) + int(seconds)
    if count_join:
        attendance.join_count = min(int(attendance.join_count or 0) + 1, 32000)
    if attendance.first_joined_at is None or seg_start < attendance.first_joined_at:
        attendance.first_joined_at = seg_start
    if attendance.last_left_at is None or seg_end > attendance.last_left_at:
        attendance.last_left_at = seg_end
    attendance.save(update_fields=[
        'total_seconds', 'join_count', 'first_joined_at', 'last_left_at', 'updated_at',
    ])


def _credit_segments(presence, start, end, ratio=1.0):
    """[start, end) ko local din ke hisaab se todkar attendance me jodta hai. Return: total seconds.

    `ratio` (S5 idle time): har segment ke seconds is hissa se scale hote hain (1.0 = poora connected time)."""
    total = 0
    cursor = start
    first = True
    while cursor < end:
        seg_end = min(end, _next_local_midnight(cursor))
        raw = int((seg_end - cursor).total_seconds())
        if raw > 0:
            seconds = int(raw * ratio) if ratio < 1.0 else raw
            _add_to_attendance(
                presence, timezone.localtime(cursor).date(), seconds, cursor, seg_end, count_join=first,
            )
            total += seconds
            first = False
        cursor = seg_end
    return total


def close_and_credit(presence_id, end, reason=REASON_LEFT) -> int:
    """Ek open interval band karo + minutes attendance me jodo. Idempotent: pehle se band row par 0 return.
    Return: jodhe gaye seconds."""
    with transaction.atomic():
        row = StudyRoomPresence.objects.select_for_update().filter(pk=presence_id).first()
        if row is None or row.left_at is not None:
            return 0
        start = row.joined_at
        end = max(end, start)
        credited_end = min(end, start + _max_duration())
        ratio = active_ratio(start, row.activity_tracked, row.active_seconds, row.last_active_at, credited_end)
        seconds = _credit_segments(row, start, credited_end, ratio)
        row.left_at = end
        row.duration_seconds = seconds
        row.end_reason = reason[:24]
        row.save(update_fields=['left_at', 'duration_seconds', 'end_reason', 'updated_at'])
        return seconds


# ----------------------------------------------------------------------
# webhook handlers (livekit_webhook.handle_message_event se)
# ----------------------------------------------------------------------
def _resolve(event):
    """(conversation_id, user_id, participant) ya None — study room + active member + non-bot."""
    room_name = getattr(getattr(event, 'room', None), 'name', '') or ''
    conversation_id = parse_room_name(room_name)
    if conversation_id is None:
        return None
    participant = getattr(event, 'participant', None)
    user_id = _user_id(participant)
    if user_id is None or not _active_member(conversation_id, user_id):
        return None
    return room_name, conversation_id, user_id, participant


def _get_or_open(room_name, conversation_id, user_id, joined_at):
    row, _ = StudyRoomPresence.objects.get_or_create(
        room_name=room_name, user_id=user_id, joined_at=joined_at,
        defaults={'conversation_id': conversation_id},
    )
    return row


def _on_joined(event):
    resolved = _resolve(event)
    if resolved is None:
        return
    room_name, conversation_id, user_id, participant = resolved
    joined_at = _participant_joined_at(participant, _event_time(event))
    _get_or_open(room_name, conversation_id, user_id, joined_at)   # already-closed row ko reopen nahi karta


def _on_left(event):
    resolved = _resolve(event)
    if resolved is None:
        return
    room_name, conversation_id, user_id, participant = resolved
    end = _event_time(event)
    joined_at = _participant_joined_at(participant, None)
    if joined_at is not None:
        row = _get_or_open(room_name, conversation_id, user_id, joined_at)   # joined webhook kho gaya ho tab bhi
    else:
        row = StudyRoomPresence.objects.filter(
            room_name=room_name, user_id=user_id, left_at__isnull=True,
        ).order_by('-joined_at').first()
        if row is None:
            return
    close_and_credit(row.pk, end, REASON_LEFT)


def _on_room_finished(event):
    room_name = getattr(getattr(event, 'room', None), 'name', '') or ''
    if parse_room_name(room_name) is None:
        return
    end = _event_time(event)
    open_ids = list(
        StudyRoomPresence.objects.filter(room_name=room_name, left_at__isnull=True).values_list('pk', flat=True)
    )
    for pk in open_ids:
        close_and_credit(pk, end, REASON_ROOM_FINISHED)


_HANDLERS = {
    'participant_joined': _on_joined,
    'participant_left': _on_left,
    'participant_connection_aborted': _on_left,
    'room_finished': _on_room_finished,
}


def handle_webhook_event(event) -> bool:
    """Public entry (livekit_webhook.handle_message_event se). Kabhi raise nahi karta; True = handler chala."""
    if not presence_enabled():
        return False
    handler = _HANDLERS.get(getattr(event, 'event', ''))
    if handler is None:
        return False
    try:
        handler(event)
        return True
    except Exception:
        logger.exception("study_room_presence: handler failed event=%s", getattr(event, 'event', ''))
        return False


# ----------------------------------------------------------------------
# live ("abhi room me") minutes — streak aaj ka din turant dikhaye, leave ka wait kiye bina
# ----------------------------------------------------------------------
def open_seconds_today(conversation_ids, user_id, now=None):
    """{conversation_id: seconds} — abhi open intervals ka AAJ ka hissa (sirf aaj ka din; max-duration cap ke andar)."""
    conversation_ids = list(conversation_ids)
    if not conversation_ids:
        return {}
    now = now or timezone.now()
    today_start = timezone.make_aware(datetime.combine(timezone.localdate(now), time.min))
    cap = _max_duration()
    out = {}
    rows = StudyRoomPresence.objects.filter(
        conversation_id__in=conversation_ids, user_id=user_id, left_at__isnull=True,
    ).values_list('conversation_id', 'joined_at', 'activity_tracked', 'active_seconds', 'last_active_at')
    for conversation_id, joined_at, tracked, active_seconds, last_active_at in rows:
        start = max(joined_at, today_start)
        end = min(now, joined_at + cap)
        if end > start:
            ratio = active_ratio(joined_at, tracked, active_seconds, last_active_at, end)
            out[conversation_id] = out.get(conversation_id, 0) + int((end - start).total_seconds() * ratio)
    return out


# ----------------------------------------------------------------------
# sweeper (Celery: message.sweep_stale_study_presence)
# ----------------------------------------------------------------------
def sweep_stale(now=None) -> dict:
    """Jin open intervals ka leave webhook nahi aaya unhe band karo.

    * `STALE_SECONDS` se purane open rows LiveKit (`list_room_identities`) se verify hote hain.
    * User sach me room me hai aur cap nahi laga => chhod do.
    * Room me nahi => `STALE_CREDIT_SECONDS` tak ka hi credit (asli leave time pata nahi — over-credit nahi).
    * LiveKit unreachable (None) => agle run me dobara, jab tak `max duration + 1h` na ho jaye (tab bhi sirf stale credit).
    Return: {"checked", "closed", "seconds"}.
    """
    if not presence_enabled():
        return {'checked': 0, 'closed': 0, 'seconds': 0}
    now = now or timezone.now()
    cap = _max_duration()
    stale_credit = _stale_credit_cap()
    hard_after = cap + timedelta(hours=1)

    rows = list(
        StudyRoomPresence.objects.filter(left_at__isnull=True, joined_at__lt=now - _stale_after())
        .order_by('joined_at')[:_sweep_batch()]
    )
    identities_by_room = {}
    closed = 0
    seconds = 0
    for row in rows:
        elapsed = now - row.joined_at
        if row.room_name not in identities_by_room:
            from . import livekit_utils as lk

            try:
                identities_by_room[row.room_name] = lk.list_room_identities(row.room_name)
            except Exception:
                logger.warning("study_room_presence: list identities failed room=%s", row.room_name, exc_info=True)
                identities_by_room[row.room_name] = None
        present = identities_by_room[row.room_name]
        verified_present = present is not None and str(row.user_id) in present

        if verified_present:
            if elapsed < cap:
                continue   # sach me abhi room me hai
            end, reason = row.joined_at + cap, REASON_CAPPED
        else:
            if present is None and elapsed < hard_after:
                continue   # LiveKit se pata nahi chala — baad me
            end, reason = row.joined_at + min(elapsed, stale_credit), REASON_SWEPT
        try:
            seconds += close_and_credit(row.pk, end, reason)
            closed += 1
        except Exception:
            logger.exception("study_room_presence: sweep close failed presence=%s", row.pk)
    return {'checked': len(rows), 'closed': closed, 'seconds': seconds}
