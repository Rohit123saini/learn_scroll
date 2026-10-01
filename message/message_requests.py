# message/message_requests.py
"""
M1-BE — Message requests (Instagram-style "who can DM me").

Ek jagah par saari rules, taaki views.py / consumers.py / push_utils.py
sirf is module ko call karein (same pattern as `group_rules.py`).

STATE (per user, `ConversationParticipant.request_status`):
    accepted  — normal inbox me dikhta hai (DEFAULT; purani saari rows aur
                saare group chats hamesha yahi rehte hain).
    pending   — receiver ne abhi accept/decline nahi kiya. Inbox me NAHI,
                `GET /message/requests/` me dikhta hai.
    declined  — receiver ne decline kiya. Kahin nahi dikhta. Sender ko
                kuch pata nahi chalta (koi event / push / system message
                nahi), messages normally store hote rehte hain.

RULES (`evaluate_incoming_message`, Message post_save signal se chalta hai —
isliye REST, WebSocket, offline-queue, scheduled, story-reply, post-share
SAB creators automatically cover hote hain):
    1. Sirf PRIVATE conversation. Group kabhi pending nahi hota.
    2. Sender ki apni row pending/declined ho to wo `accepted` ban jaati hai
       (reply dena = accept karna, Instagram jaisa).
    3. Receiver ki row `accepted` hai, ye conversation ka PEHLA message hai
       aur dono ke beech koi follow relation (kisi bhi direction me) nahi
       hai -> receiver `pending`.
    4. Receiver `pending` hai aur ab follow relation ban chuka hai -> `accepted`.
    5. `declined` kabhi apne aap nahi badalta (sirf receiver accept kare to).
"""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ConversationParticipant,
    ConversationType,
    Message,
    MessageType,
    RequestStatus,
)

logger = logging.getLogger(__name__)


# ======================================================================
# FOLLOW LOOKUP (single adapter — user_profile ka follow model yahin se
# touch hota hai, baaki module is detail ko nahi jaanta)
# ======================================================================
# `user_profile.Follow(follower, following, status)` — status choices
# `PENDING` / `ACCEPTED` (default ACCEPTED). Sirf ACCEPTED follow count hota
# hai: private profile pe abhi pending follow-request = follow nahi.
# Model/field naam alag ho to settings.py me override karo:
#   MESSAGE_REQUESTS_FOLLOW_MODEL = "user_profile.Follow"   (default)
#   MESSAGE_REQUESTS_FOLLOWER_FIELD = "follower"            (default)
#   MESSAGE_REQUESTS_FOLLOWING_FIELD = "following"          (default)
_follow_lookup_error_logged = False


def is_following(follower_id, target_id):
    """True agar `follower_id` ne `target_id` ko (approved) follow kar rakha hai.

    FAIL-OPEN: lookup toot jaaye (model/field naam galat) to True return
    hota hai — matlab koi DM request me nahi jaata. Bura case "spam inbox me
    aaya" hai, "asli message chhup gaya" nahi. Error ek baar loud log hota hai.
    """
    global _follow_lookup_error_logged
    try:
        model = apps.get_model(getattr(settings, 'MESSAGE_REQUESTS_FOLLOW_MODEL', 'user_profile.Follow'))
        follower_field = getattr(settings, 'MESSAGE_REQUESTS_FOLLOWER_FIELD', 'follower')
        following_field = getattr(settings, 'MESSAGE_REQUESTS_FOLLOWING_FIELD', 'following')
        qs = model.objects.filter(**{
            f'{follower_field}_id': follower_id,
            f'{following_field}_id': target_id,
        })
        if any(f.name == 'status' for f in model._meta.get_fields()):
            qs = qs.filter(status__iexact='accepted')
        return qs.exists()
    except Exception:
        if not _follow_lookup_error_logged:
            _follow_lookup_error_logged = True
            logger.exception(
                "message_requests.is_following: follow lookup fail — sab DM inbox me jaayenge "
                "(fail-open). settings.MESSAGE_REQUESTS_FOLLOW_MODEL / *_FIELD check karo."
            )
        return True


def has_follow_relation(user_a_id, user_b_id):
    """Kisi bhi direction me follow — dono me se ek bhi follow kare to True."""
    return is_following(user_a_id, user_b_id) or is_following(user_b_id, user_a_id)


# ======================================================================
# EVALUATION (Message post_save)
# ======================================================================
def evaluate_incoming_message(message):
    """Naye message ke baad participants ki `request_status` update karo."""
    if message.is_system_message or message.type == MessageType.SYSTEM:
        return
    if message.conversation.type != ConversationType.PRIVATE:
        return

    rows = list(
        ConversationParticipant.objects.filter(
            conversation_id=message.conversation_id, left_at__isnull=True,
        )
    )
    sender_row = next((r for r in rows if r.user_id == message.sender_id), None)
    receiver_rows = [r for r in rows if r.user_id != message.sender_id]

    # Rule 2 — reply = accept.
    if sender_row is not None and sender_row.request_status != RequestStatus.ACCEPTED:
        _set_status(sender_row, RequestStatus.ACCEPTED)

    is_first_message = None  # lazily computed, at most one query
    for row in receiver_rows:
        if row.request_status == RequestStatus.PENDING:
            # Rule 4
            if has_follow_relation(message.sender_id, row.user_id):
                _set_status(row, RequestStatus.ACCEPTED)
        elif row.request_status == RequestStatus.ACCEPTED:
            # Rule 3
            if is_first_message is None:
                is_first_message = not Message.objects.filter(
                    conversation_id=message.conversation_id,
                ).exclude(pk=message.pk).exclude(
                    Q(is_system_message=True) | Q(type=MessageType.SYSTEM)
                ).exists()
            if is_first_message and not has_follow_relation(message.sender_id, row.user_id):
                # Raw update — koi event nahi: is message ka inbox routing
                # (`inbox_events_for`) hi receiver ko "message_request" bhejta hai.
                ConversationParticipant.objects.filter(pk=row.pk).update(
                    request_status=RequestStatus.PENDING
                )


def _set_status(row, new_status):
    """Row update + us user ke saare devices ko inbox refresh event (commit ke baad)."""
    ConversationParticipant.objects.filter(pk=row.pk).update(request_status=new_status)
    conversation_id = row.conversation_id
    user_id = row.user_id
    transaction.on_commit(lambda: _emit_resolved(user_id, conversation_id, new_status))


# ======================================================================
# QUERIES USED BY views / consumers / push_utils
# ======================================================================
def get_request_status(conversation_id, user_id):
    return (
        ConversationParticipant.objects.filter(conversation_id=conversation_id, user_id=user_id)
        .values_list('request_status', flat=True)
        .first()
    )


def is_request_accepted(conversation_id, user_id):
    """True agar is user ke liye ye normal (accepted) chat hai. Row na mile to True (fail-open)."""
    status = get_request_status(conversation_id, user_id)
    return status is None or status == RequestStatus.ACCEPTED


def partition_recipients(conversation_id, recipient_ids):
    """(accepted_ids, pending_ids). `declined` dono me nahi aate (silently dropped).

    ids ka type (int/str/UUID) caller ka hi wapas milta hai.
    """
    ids = list(recipient_ids)
    if not ids:
        return [], []
    non_accepted = {
        str(uid): status
        for uid, status in ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user_id__in=ids,
        ).exclude(request_status=RequestStatus.ACCEPTED).values_list('user_id', 'request_status')
    }
    if not non_accepted:
        return ids, []
    accepted = [u for u in ids if str(u) not in non_accepted]
    pending = [u for u in ids if non_accepted.get(str(u)) == RequestStatus.PENDING]
    return accepted, pending


def inbox_events_for(conversation_id, recipient_ids, inbox_payload):
    """`[(user_id, event_dict)]` — har recipient ko uske state ke hisaab se sahi inbox event.

    accepted -> `inbox_payload` (type `inbox_update`) jaisa pehle tha
    pending  -> `message_request` event (requests tab/badge ke liye)
    declined -> kuch nahi
    """
    accepted, pending = partition_recipients(conversation_id, recipient_ids)
    events = [(uid, inbox_payload) for uid in accepted]
    if pending:
        request_payload = {**inbox_payload, 'type': 'message_request'}
        events.extend((uid, request_payload) for uid in pending)
    return events


# ======================================================================
# ACCEPT / DECLINE
# ======================================================================
class RequestStateError(Exception):
    """Is state me ye action allowed nahi (e.g. already-accepted chat ko decline)."""


def set_request_status(user, conversation_id, new_status):
    """Receiver ka accept/decline. Returns (membership, changed).

    LookupError         — user is private conversation ka active member nahi.
    RequestStateError   — accepted chat ko decline karne ki koshish.
    Idempotent: same status dobara set karna error nahi, `changed=False`.
    Sender ko KUCH nahi bheja jaata — sirf receiver ke apne devices ko event.
    """
    with transaction.atomic():
        membership = (
            ConversationParticipant.objects.select_for_update(of=('self',))
            .filter(
                conversation_id=conversation_id, user=user, left_at__isnull=True,
                conversation__type=ConversationType.PRIVATE,
            )
            .first()
        )
        if membership is None:
            raise LookupError('message request not found')

        current = membership.request_status
        if current == new_status:
            return membership, False
        if new_status == RequestStatus.DECLINED and current != RequestStatus.PENDING:
            raise RequestStateError('only a pending request can be declined')

        membership.request_status = new_status
        membership.save(update_fields=['request_status', 'updated_at'])
        transaction.on_commit(lambda: _emit_resolved(user.id, conversation_id, new_status))
    return membership, True


# ======================================================================
# REALTIME (receiver ke apne `user_<id>` inbox group tak hi — kabhi sender tak nahi)
# ======================================================================
def _emit_resolved(user_id, conversation_id, status):
    layer = get_channel_layer()
    if layer is None:
        return
    group = f'user_{user_id}'
    try:
        if status == RequestStatus.ACCEPTED:
            # Purane clients bhi ye event already samajhte hain (list refresh/prepend).
            async_to_sync(layer.group_send)(group, {
                'type': 'conversation_created',
                'conversation_id': str(conversation_id),
                'conversation_type': ConversationType.PRIVATE,
                'created_at': timezone.now().isoformat(),
            })
        async_to_sync(layer.group_send)(group, {
            'type': 'message_request_resolved',
            'conversation_id': str(conversation_id),
            'status': status,
        })
    except Exception:
        logger.exception("message_requests: inbox event send failed user=%s", user_id)
