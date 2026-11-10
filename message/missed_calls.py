# message/missed_calls.py
#
# C12 — missed-call push + chat bubble (C1 / C6 / C7 ke "missed-call push / chat bubble C12 me" wale TODO).
#
# Ye module sirf logic hai (DB + push + chat message); Celery wrapper `tasks.notify_missed_call_task`
# aur `tasks.enqueue_missed_call` me hai. Hook points (sab commit ke BAAD):
#   * `tasks._finish_stale_call`          — sweeper ring-timeout / LiveKit webhook (call band)
#   * `tasks.sweep_stale_calls` step 4    — chalti call me add-participant se bulaye, jawab nahi diya
#   * `views.calls._dispatch_result`      — caller ne uthne se pehle cut kiya / group me last banda gaya
#   * `views.calls.CallInitiateView`      — 1:1 callee BUSY (dusri call me tha) => callee ko "missed call"
#   * `consumers.CallConsumer.mark_participant_left` — socket se call band (bache RINGING => MISSED)
#
# Kya milta hai:
#   1. CHAT BUBBLE (ek call = ek bubble, sab ke liye):
#        sirf tab jab call kabhi CONNECT nahi hui (`connected_at IS NULL`) aur status MISSED / CANCELLED / BUSY ho.
#        `Message(type=system, is_system_message=True, meta={system_event:'call_log', call_id, call_status,
#        call_type, is_group_call, caller_id})` — sender = caller. Client viewer ke hisaab se text banata hai
#        (callee: "Missed voice call", caller: "No answer" / "Cancelled").
#        Naya MessageType / model field NAHI => koi migration nahi.
#   2. BELL ROW (har missed user ko): C5/C11 me ring ke time jo INCOMING_CALL row bani thi use "Missed ... call"
#        me badal dete hain (data.missed=True, unread). Row nahi thi (busy / overflow / late invite) => nayi banti hai.
#        Naya NotifType NAHI => koi migration nahi.
#   3. PUSH (data-only, type='missed_call'): `push_utils.send_missed_call_push`. Group call + muted chat => push nahi
#        (bell row + bubble phir bhi). User ne INCOMING_CALL type mute/push off kiya ho => push nahi.
#
# Idempotent: bell row `data.missed` se, bubble `meta.call_id` + cache key se. Retry/duplicate task safe.
# Kabhi raise nahi karta (har step best-effort) — call flow kabhi isse fail nahi hona chahiye.
import logging

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

CALL_LOG_EVENT = 'call_log'
_BUBBLE_STATUSES = ('missed', 'cancelled', 'busy')


def _flag(name, default=True):
    return bool(getattr(settings, name, default))


def push_enabled():
    return _flag('CALL_MISSED_PUSH_ENABLED', True)


def bubble_enabled():
    return _flag('CALL_MISSED_BUBBLE_ENABLED', True)


def missed_push_ttl_seconds():
    try:
        return max(60, int(getattr(settings, 'CALL_MISSED_PUSH_TTL_SECONDS', 24 * 3600)))
    except (TypeError, ValueError):
        return 24 * 3600


def _kind(call):
    return 'video' if str(getattr(call, 'type', '')) == 'video' else 'voice'


def missed_text(call):
    """Server-side English text (chat preview / bell row / iOS alert). Android client apni language me banata hai."""
    kind = _kind(call)
    if call.is_group_call:
        return f'Missed group {kind} call'
    return f'Missed {kind} call'


# ----------------------------------------------------------------------
# 1) chat bubble
# ----------------------------------------------------------------------
def should_post_bubble(call):
    """Bubble sirf un calls ka jo kabhi connect nahi hui."""
    if not bubble_enabled() or call.conversation_id is None:
        return False
    if call.connected_at is not None:
        return False
    return str(call.status) in _BUBBLE_STATUSES


def post_call_log_bubble(call):
    """Conversation me ek 'call_log' system message (call.id par dedupe). Returns Message ya None."""
    if not should_post_bubble(call):
        return None
    try:
        from .models import Message, MessageType
        from .services import create_message_and_broadcast

        key = f'call_log_bubble:{call.id}'
        if not cache.add(key, 1, timeout=24 * 3600):
            return None  # kisi aur worker ne abhi-abhi banaya
        if Message.objects.filter(
            conversation_id=call.conversation_id,
            meta__system_event=CALL_LOG_EVENT,
            meta__call_id=str(call.id),
        ).exists():
            return None

        meta = {
            'system_event': CALL_LOG_EVENT,
            'call_id': str(call.id),
            'call_status': str(call.status),
            'call_type': str(call.type),
            'is_group_call': bool(call.is_group_call),
            'caller_id': str(call.caller_id),
            'duration_seconds': 0,
            'started_at': call.started_at.isoformat() if call.started_at else None,
        }
        try:
            return create_message_and_broadcast(
                conversation=call.conversation,
                sender=call.caller,
                message_type=MessageType.SYSTEM,
                text=missed_text(call),
                meta=meta,
                extra_fields={'is_system_message': True},
                send_push=False,   # push `send_missed_call_push` se alag jaata hai (generic "naya message" nahi)
            )
        except Exception:
            cache.delete(key)      # create fail hua => Celery retry / agla hook dobara bana sake
            raise
    except Exception:
        logger.warning('missed_calls: bubble failed call=%s', getattr(call, 'id', None), exc_info=True)
        return None


# ----------------------------------------------------------------------
# 2) bell row
# ----------------------------------------------------------------------
def _missed_payload(call, now):
    return {
        'call_id': str(call.id),
        'conversation_id': str(call.conversation_id) if call.conversation_id else '',
        'channel_name': call.channel_name,
        'kind': 'missed_call',
        'missed': True,
        'call_type': str(call.type),
        'call_status': str(call.status),
        'is_group_call': bool(call.is_group_call),
        'missed_at': now.isoformat(),
    }


def upsert_missed_bell_row(call, user_id, caller_name, now):
    """
    Returns (changed, created). changed=False => is user ko is call ka missed row pehle hi mil chuka
    (ya actor-block/restrict/mute se row hi nahi bani) — push mat bhejo.
    """
    from core.models import Notification
    from core.services import create_notification, publish_unread_badge

    NT = Notification.NotifType
    payload = _missed_payload(call, now)
    title = caller_name or 'Missed call'
    message = missed_text(call)

    row = None
    try:
        row = Notification.objects.filter(
            recipient_id=user_id, notif_type=NT.INCOMING_CALL, data__call_id=str(call.id),
        ).order_by('-created_at').first()
    except Exception:
        logger.warning('missed_calls: bell lookup failed call=%s user=%s', call.id, user_id, exc_info=True)

    if row is not None:
        data = dict(row.data or {})
        if data.get('missed'):
            return False, False
        data.update(payload)
        row.title = title
        row.message = message
        row.data = data
        row.is_read = False
        row.read_at = None
        row.save(update_fields=['title', 'message', 'data', 'is_read', 'read_at'])
        publish_unread_badge(user_id)
        return True, False

    created = create_notification(
        recipient=user_id, notif_type=NT.INCOMING_CALL, title=title, message=message,
        data=payload, actor=call.caller,
    )
    if created is None:
        return False, False
    publish_unread_badge(user_id)
    return True, True


# ----------------------------------------------------------------------
# 3) push eligibility
# ----------------------------------------------------------------------
def _push_allowed(call, user_id):
    """User ne INCOMING_CALL push band/mute kiya ho, ya group call ki chat mute ho => False."""
    try:
        from core.models import Notification
        from core.services import channels_for

        if 'push' not in channels_for(user_id, Notification.NotifType.INCOMING_CALL):
            return False
    except Exception:
        logger.warning('missed_calls: channel pref check failed user=%s (push allowed)', user_id, exc_info=True)

    if call.is_group_call and call.conversation_id:
        try:
            from .models import ConversationParticipant
            if ConversationParticipant.objects.filter(
                conversation_id=call.conversation_id, user_id=user_id, is_muted=True,
            ).exists():
                return False
        except Exception:
            logger.exception("missed-call mute check failed; notifying anyway")
    return True


# ----------------------------------------------------------------------
# entry point
# ----------------------------------------------------------------------
def process_missed_call(call_id, user_ids):
    """
    `user_ids` — jinki participant row abhi MISSED hui (caller ko kabhi nahi). Sirf wahi users jinki row sach me
    MISSED hai process hote hain (cancel ke baad reject/accept ho gaya ho to skip).
    Returns dict: {'bubble': bool, 'notified': n, 'pushed': n}.
    """
    out = {'bubble': False, 'notified': 0, 'pushed': 0}
    try:
        from .models import CallParticipant, CallSession, CallStatus
        from .push_utils import send_missed_call_push
        from .user_display import get_display_name, get_profile_photo_url

        call = CallSession.objects.select_related('caller', 'conversation').filter(id=call_id).first()
        if call is None:
            return out

        # Bubble pehle (call-level, ek baar) — push/bell se independent.
        if post_call_log_bubble(call) is not None:
            out['bubble'] = True

        ids = [u for u in (user_ids or []) if u is not None]
        if not ids:
            return out
        missed_ids = list(
            CallParticipant.objects.filter(call=call, user_id__in=ids, status=CallStatus.MISSED)
            .exclude(user_id=call.caller_id).values_list('user_id', flat=True)
        )
        if not missed_ids:
            return out

        now = timezone.now()
        caller_name = get_display_name(call.caller)
        push_ids = []
        for uid in missed_ids:
            try:
                changed, _created = upsert_missed_bell_row(call, uid, caller_name, now)
            except Exception:
                logger.warning('missed_calls: bell row failed call=%s user=%s', call.id, uid, exc_info=True)
                continue
            if not changed:
                continue
            out['notified'] += 1
            if push_enabled() and _push_allowed(call, uid):
                push_ids.append(uid)

        if push_ids:
            try:
                avatar = get_profile_photo_url(call.caller)
            except Exception:
                avatar = None
            send_missed_call_push(
                recipient_ids=push_ids, caller_name=caller_name, call_type=str(call.type), call_id=call.id,
                conversation_id=call.conversation_id, is_group_call=bool(call.is_group_call),
                call_status=str(call.status), caller_avatar=avatar, body=missed_text(call),
                missed_at=now, ttl_seconds=missed_push_ttl_seconds(),
            )
            out['pushed'] = len(push_ids)
    except Exception:
        logger.exception('process_missed_call failed call=%s', call_id)
    return out
