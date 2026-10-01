# message/admin_only.py
# M9a — Admin-only messaging (group).
#
# Source of truth abhi bhi `Group.message_permission` hai (naya
# `Conversation.only_admins_can_send` NAHI banaya — dono rakhne se sync bugs
# aate). Ye module REST + WebSocket + group-PATCH teeno ke liye ek hi jagah
# rule rakhta hai taaki teeno paths same behaviour dein.
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

ADMIN_ONLY_CODE = 'admins_only'
ADMIN_ONLY_TEXT = "Only admins can send messages"
EVERYONE_TEXT = "All members can send messages now"

# FE abhi 'admins_mods' bhejta/compare karta hai, model me 'admins_only' hai.
# Dono ko same maano jab tak FE (Part 2/3) align na ho.
_ADMINS_ONLY_VALUES = {'admins_only', 'admins_mods'}


def normalize_permission(value):
    """FE alias ('admins_mods') -> model value ('admins_only')."""
    return 'admins_only' if value in _ADMINS_ONLY_VALUES else value


def is_admins_only(group):
    return (getattr(group, 'message_permission', None) in _ADMINS_ONLY_VALUES)


def check_admin_only_send(group, user_id):
    """(allowed, reason, code). Admin/mod hamesha allowed."""
    from .group_rules import is_group_admin_or_mod

    if not is_admins_only(group):
        return True, '', ''
    if is_group_admin_or_mod(group, user_id):
        return True, '', ''
    return False, ADMIN_ONLY_TEXT, ADMIN_ONLY_CODE


def admin_only_block_response(group, user_id):
    """REST helper: blocked ho to 403 Response (code ke saath), warna None."""
    from rest_framework import status
    from rest_framework.response import Response

    allowed, reason, code = check_admin_only_send(group, user_id)
    if allowed:
        return None
    return Response({'detail': reason, 'code': code}, status=status.HTTP_403_FORBIDDEN)


def post_permission_system_message(group, actor, admins_only_now):
    """Setting badalne par group me system message + WS broadcast.

    Fail-open: koi error aaye to settings PATCH fail nahi hona chahiye.
    """
    try:
        from .models import ConversationParticipant, Message, MessageType

        conversation = group.conversation
        text = ADMIN_ONLY_TEXT if admins_only_now else EVERYONE_TEXT
        with transaction.atomic():
            msg = Message.objects.create(
                conversation=conversation,
                sender=actor,
                type=MessageType.SYSTEM,
                text=text,
                is_system_message=True,
                meta={'system_event': 'message_permission_changed',
                      'message_permission': 'admins_only' if admins_only_now else 'everyone'},
            )
            conversation.last_message_text = text[:500]
            conversation.last_message_at = msg.created_at
            conversation.last_message_sender = actor
            conversation.last_message_type = MessageType.SYSTEM
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])
            ConversationParticipant.objects.filter(
                conversation=conversation, left_at__isnull=True,
            ).exclude(user=actor).update(unread_count=F('unread_count') + 1)

        async_to_sync(get_channel_layer().group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(msg.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(actor.id),
                'message_type': MessageType.SYSTEM,
                'is_system_message': True,
                'text': text,
                'meta': msg.meta,
                'message_permission': msg.meta['message_permission'],
                'created_at': msg.created_at.isoformat(),
            },
        )
    except Exception:
        logger.exception("admin_only: system message failed group=%s (fail-open)", getattr(group, 'pk', None))
