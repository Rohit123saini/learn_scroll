# message/scheduled_messages.py
"""
🔥 NAYA (ADVANCED FEATURE) — "Send Later" / Scheduled Messages, delivery half.

`ConversationViewSet.schedule_message` (views.py) sirf message row banata hai
`is_scheduled=True` ke saath — koi broadcast, koi unread-count, koi push,
kuch nahi. Ye module wo dusra half hai: jab time aa jaaye to message ko
"actually send" karna — bilkul waisा jaisa `ConversationViewSet.messages`
(POST) normal message ke liye karta hai.

Sirf `send_scheduled_messages` management command isko call karta hai
(cron / Celery-beat se periodically chalao, e.g. har 1 minute).
"""
import logging

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .mentions import extract_mentioned_user_ids
from .media_utils import create_group_media_for_message
from .models import ConversationParticipant, MessageStatus
from .push_utils import send_chat_message_push, send_mention_push
from .user_display import build_user_mini

logger = logging.getLogger(__name__)


def finalize_scheduled_message(message):
    """
    Ek `is_scheduled=True` Message ko normal, delivered message bana deta
    hai:

    1. `is_scheduled=False` set karta hai, aur disappearing-messages
       `expires_at` ko conversation ki *ABHI* ki (schedule-time ki nahi)
       duration setting se recompute karta hai — normal send jaisa hi.
    2. `created_at` ko "abhi" pe update karta hai (auto_now_add sirf
       INSERT pe lagta hai, is UPDATE pe nahi lagega) taaki chat list me
       ye sahi jagah (abhi ke top pe) dikhe, schedule-karne ke waqt ki
       jagah nahi.
    3. Conversation ke denormalized last-message fields, unread counts,
       MessageStatus rows, @mentions, GroupMedia gallery — sab wahi
       update karta hai jo REST/WS ka normal send-flow karta hai.
    4. WS `chat_message` + `inbox_update` broadcast karta hai, aur push
       notifications (mute/mention-aware, normal send jaisa hi) bhejta hai.

    Poora function ek try/except ke bina hi chalta hai — calling
    management command har message ko apne try/except me wrap karta hai,
    taaki ek bad message poori batch ko na roke.
    """
    conversation = message.conversation

    with transaction.atomic():
        disappearing_delta = conversation.get_disappearing_timedelta()
        now = timezone.now()

        message.is_scheduled = False
        message.expires_at = (now + disappearing_delta) if disappearing_delta else None
        message.created_at = now  # ab "bheja" ja raha hai — timestamp bhi wahi ho
        message.save(update_fields=['is_scheduled', 'expires_at', 'created_at', 'updated_at'])

        conversation.last_message_text = (message.text or '')[:500]
        conversation.last_message_at = message.created_at
        conversation.last_message_sender_id = message.sender_id
        conversation.last_message_type = message.type
        conversation.save(update_fields=[
            'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
        ])

        ConversationParticipant.objects.filter(conversation=conversation).exclude(
            user_id=message.sender_id
        ).update(unread_count=F('unread_count') + 1)

        other_participant_ids = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user_id=message.sender_id)
            .values_list('user_id', flat=True)
        )
        MessageStatus.objects.bulk_create(
            [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
            ignore_conflicts=True,
        )

        mentioned_ids = extract_mentioned_user_ids(message.text, conversation)
        mentioned_ids = [uid for uid in mentioned_ids if uid != message.sender_id]
        if mentioned_ids:
            message.mentioned_users.set(mentioned_ids)

        create_group_media_for_message(message)

    sender = build_user_mini(message.sender)
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f'chat_{conversation.id}',
        {
            'type': 'chat_message',
            'event': 'message',
            'id': str(message.id),
            'conversation_id': str(conversation.id),
            'sender_id': str(message.sender_id),
            'sender_name': sender['display_name'],
            'sender_username': sender['username'],
            'sender_first_name': sender['first_name'],
            'sender_last_name': sender['last_name'],
            'sender_profile_photo': sender['profile_photo'],
            'message_type': message.type,
            'text': message.text,
            'file_url': message.file_url,
            'file_urls': message.file_urls,
            'thumbnail_url': message.thumbnail_url,
            'meta': message.meta,
            'reply_to': str(message.reply_to_id) if message.reply_to_id else None,
            'client_id': message.client_id,
            'mentioned_user_ids': [str(uid) for uid in mentioned_ids],
            'created_at': message.created_at.isoformat(),
        }
    )

    for uid in other_participant_ids:
        async_to_sync(channel_layer.group_send)(
            f'user_{uid}',
            {
                'type': 'inbox_update',
                'conversation_id': str(conversation.id),
                'message_id': str(message.id),
                'sender_id': str(message.sender_id),
                'sender_name': sender['display_name'],
                'last_message_text': message.text,
                'last_message_type': message.type,
                'created_at': message.created_at.isoformat(),
            }
        )

    muted_user_ids = set(
        ConversationParticipant.objects.filter(
            conversation=conversation, user_id__in=other_participant_ids, is_muted=True,
        ).values_list('user_id', flat=True)
    )
    mentioned_set = set(mentioned_ids)
    push_recipients = [
        uid for uid in other_participant_ids if uid not in muted_user_ids and uid not in mentioned_set
    ]

    if push_recipients:
        send_chat_message_push(
            recipient_ids=push_recipients,
            sender_name=sender['display_name'],
            message_text=message.text,
            message_type=message.type,
            conversation_id=conversation.id,
            message_id=message.id,
        )

    if mentioned_ids:
        send_mention_push(
            recipient_ids=mentioned_ids,
            sender_name=sender['display_name'],
            message_text=message.text,
            conversation_id=conversation.id,
            message_id=message.id,
        )