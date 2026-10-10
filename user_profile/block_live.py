"""
user_profile/block_live.py

Real-time side effects of block / unblock, so the change shows up on both
phones immediately instead of "next time the screen reloads":

  * block  -> every ACTIVE 1-1 call between the two people is ended
              (group calls are left alone — a block is not a group kick)
  * block / unblock -> a `block_changed` event goes to
        - both users' inbox sockets   (group `user_<id>`)
        - the 1-1 chat's socket       (group `chat_<conversation_id>`)
    so the chat screen can flip its composer / banner, and chat lists refresh.

PRIVACY: the event is identical for both sides — it only carries the OTHER
person's id and the new state, never who did the blocking. (Messages were
already refused by the consumers; this just makes the UI follow.)

Everything runs after the DB transaction commits, and never raises — a
missing channel layer / Redis must not make block or unblock fail.
"""
import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

ACTIVE_CALL_STATUSES = ("initiated", "ringing", "ongoing")


def _end_active_calls_between(user_a_id, user_b_id):
    """End active 1-1 calls shared by the pair. Returns [(call_id, conversation_id), ...]."""
    from django.db.models import Q
    from message.models import CallParticipant, CallSession, CallStatus

    now = timezone.now()
    calls = list(
        CallSession.objects.filter(is_group_call=False, status__in=ACTIVE_CALL_STATUSES)
        .filter(
            Q(caller_id=user_a_id, call_participants__user_id=user_b_id)
            | Q(caller_id=user_b_id, call_participants__user_id=user_a_id)
        )
        .distinct()
    )
    ended = []
    for call in calls:
        was_connected = bool(call.connected_at)
        call.status = CallStatus.ENDED if was_connected else CallStatus.MISSED
        call.ended_at = now
        fields = ["status", "ended_at"]
        if was_connected:
            call.duration_seconds = int((now - call.connected_at).total_seconds())
            fields.append("duration_seconds")
        call.save(update_fields=fields)
        CallParticipant.objects.filter(call=call, left_at__isnull=True).update(left_at=now, status=CallStatus.ENDED)
        ended.append((call.id, call.conversation_id))
        try:
            from message.livekit_utils import delete_room

            delete_room(call.channel_name)  # disconnects both phones from the media room
        except Exception:
            logger.exception("closing LiveKit room failed (call=%s)", call.id)
    return ended


def _private_conversation_id(user_a_id, user_b_id):
    from message.models import Conversation, ConversationParticipant, ConversationType

    a_convs = ConversationParticipant.objects.filter(user_id=user_a_id).values("conversation_id")
    return (
        ConversationParticipant.objects.filter(user_id=user_b_id, conversation_id__in=a_convs)
        .exclude(conversation__type=ConversationType.GROUP)
        .values_list("conversation_id", flat=True)
        .first()
    )


def _broadcast(blocker_id, blocked_id, is_blocked, ended_calls):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        layer = get_channel_layer()
        if layer is None:
            return
        send = async_to_sync(layer.group_send)

        # Same payload for both people, no direction.
        for me, other in ((blocker_id, blocked_id), (blocked_id, blocker_id)):
            send(f"user_{me}", {"type": "block_changed", "other_user_id": str(other), "is_blocked": is_blocked})

        conversation_id = _private_conversation_id(blocker_id, blocked_id)
        if conversation_id:
            send(
                f"chat_{conversation_id}",
                {"type": "block_changed", "is_blocked": is_blocked},
            )

        for call_id, call_conversation_id in ended_calls:
            send(
                f"call_{call_id}",
                {"type": "call_signal", "data": {"event": "call_ended", "call_id": str(call_id), "reason": "blocked"}},
            )
            if call_conversation_id:
                send(
                    f"chat_{call_conversation_id}",
                    {"type": "call_event", "event": "call_ended", "call_id": str(call_id), "user_id": ""},
                )
    except Exception:
        logger.exception("block live broadcast failed (%s <-> %s)", blocker_id, blocked_id)


def on_block_changed(blocker_id, blocked_id, is_blocked):
    """Entry point used by the BlockUser signals. Safe to call inside a transaction."""

    def _run():
        # T1 item 6/8: a block must show up in the Home feed at once - drop both people's cached
        # feed candidates (post/feed_cache.py). Pages are ALSO re-filtered through the "safe to show"
        # queryset on every request, so even a frozen snapshot can't serve the blocked account.
        try:
            from post import feed_cache

            for uid in (blocker_id, blocked_id):
                feed_cache.invalidate(uid)
        except Exception:
            logger.exception("feed cache invalidation on block failed (%s <-> %s)", blocker_id, blocked_id)
        try:
            ended = _end_active_calls_between(blocker_id, blocked_id) if is_blocked else []
        except Exception:
            logger.exception("ending calls on block failed (%s <-> %s)", blocker_id, blocked_id)
            ended = []
        _broadcast(blocker_id, blocked_id, is_blocked, ended)

    try:
        transaction.on_commit(_run)
    except Exception:
        logger.exception("block live scheduling failed")
