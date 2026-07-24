# chat/consumers.py - PRODUCTION LEVEL
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils import timezone

from .models import (
    BlockedUser,
    CallParticipant,
    CallSession,
    CallStatus,
    ConversationParticipant,
    Message,
    MessageStatus,
    UserPresence,
)

logger = logging.getLogger(__name__)


# ======================================================================
# CHAT CONSUMER
# ======================================================================
class ChatConsumer(AsyncWebsocketConsumer):
    """
    /ws/chat/<conversation_id>/?token=<jwt>

    Client -> Server event types (JSON body me "type" field):
        {"type": "message", "client_id": "...", "message_type": "text",
         "text": "hi", "reply_to": null}
        {"type": "typing", "is_typing": true}
        {"type": "read", "message_id": "..."}
        {"type": "delete", "message_id": "...", "for_everyone": false}
        {"type": "reaction", "message_id": "...", "emoji": "🔥"}

    Server -> Client events: same "type" field, plus "error" type on failure.
    """

    # ---------------- CONNECT ----------------
    async def connect(self):
        self.user = self.scope.get('user')

        # 🔥 SECURITY: unauthenticated user ko connect hi mat karne do
        if self.user is None or not self.user.is_authenticated:
            await self.close(code=4001)  # custom code: unauthorized
            return

        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']

        # 🔥 SECURITY: sirf conversation ka member hi is room me aa sake,
        # warna koi bhi kisi ki private chat me ghus sakta hai sirf ID guess
        # karke.
        is_member = await self.is_conversation_member(self.conversation_id, self.user.id)
        if not is_member:
            await self.close(code=4003)  # forbidden
            return

        self.room_group_name = f'chat_{self.conversation_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # presence: online mark karo (multi-device safe counter)
        await self.set_presence(online=True)
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'presence_update', 'user_id': str(self.user.id), 'is_online': True},
        )

        # jo messages abhi tak deliver nahi hue the unhe deliver mark karo
        await self.mark_undelivered_as_delivered(self.conversation_id, self.user.id)

    # ---------------- DISCONNECT ----------------
    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        if getattr(self, 'user', None) and self.user.is_authenticated:
            still_online = await self.set_presence(online=False)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'presence_update',
                    'user_id': str(self.user.id),
                    'is_online': still_online,
                },
            )

    # ---------------- RECEIVE (client -> server) ----------------
    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return await self.send_error("invalid_json", "Payload valid JSON nahi hai")

        event_type = data.get('type')
        handler_map = {
            'message': self.handle_new_message,
            'typing': self.handle_typing,
            'read': self.handle_read_receipt,
            'delete': self.handle_delete_message,
            'reaction': self.handle_reaction,
        }
        handler = handler_map.get(event_type)
        if handler is None:
            return await self.send_error("unknown_type", f"'{event_type}' type supported nahi hai")

        try:
            await handler(data)
        except Exception:
            logger.exception("ChatConsumer error handling event=%s user=%s", event_type, self.user.id)
            await self.send_error("server_error", "Kuch galat ho gaya, dobara try karo")

    # ---------------- EVENT HANDLERS ----------------
    async def handle_new_message(self, data):
        text = (data.get('text') or '').strip()
        message_type = data.get('message_type', 'text')
        client_id = data.get('client_id')  # offline-retry idempotency
        reply_to = data.get('reply_to')

        if not text and message_type == 'text':
            return await self.send_error("empty_message", "Empty message bhej nahi sakte")

        message = await self.save_message(
            conversation_id=self.conversation_id,
            sender_id=self.user.id,
            text=text,
            message_type=message_type,
            client_id=client_id,
            reply_to_id=reply_to,
        )
        if message is None:
            # duplicate client_id -> already saved, silently ignore (idempotent retry)
            return

        payload = {
            'type': 'chat_message',
            'event': 'message',
            'id': str(message['id']),
            'conversation_id': str(self.conversation_id),
            'sender_id': str(self.user.id),
            'sender_name': self.user.get_username() if hasattr(self.user, 'get_username') else str(self.user),
            'message_type': message_type,
            'text': text,
            'reply_to': reply_to,
            'client_id': client_id,
            'created_at': message['created_at'],
        }
        await self.channel_layer.group_send(self.room_group_name, payload)

    async def handle_typing(self, data):
        # DB me kuch save nahi karte, sirf broadcast — high frequency event
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_event',
                'user_id': str(self.user.id),
                'is_typing': bool(data.get('is_typing')),
            },
        )

    async def handle_read_receipt(self, data):
        message_id = data.get('message_id')
        if not message_id:
            return await self.send_error("missing_field", "message_id required hai")

        await self.mark_message_read(message_id, self.user.id)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'read_event',
                'user_id': str(self.user.id),
                'message_id': message_id,
            },
        )

    async def handle_delete_message(self, data):
        message_id = data.get('message_id')
        for_everyone = bool(data.get('for_everyone'))
        ok = await self.delete_message(message_id, self.user.id, for_everyone)
        if not ok:
            return await self.send_error("not_allowed", "Ye message delete nahi kar sakte")

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'delete_event',
                'message_id': message_id,
                'for_everyone': for_everyone,
                'deleted_by': str(self.user.id),
            },
        )

    async def handle_reaction(self, data):
        message_id = data.get('message_id')
        emoji = data.get('emoji')
        if not (message_id and emoji):
            return await self.send_error("missing_field", "message_id aur emoji required hai")

        await self.save_reaction(message_id, self.user.id, emoji)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'reaction_event',
                'message_id': message_id,
                'user_id': str(self.user.id),
                'emoji': emoji,
            },
        )

    # ---------------- GROUP EVENT HANDLERS (server -> socket) ----------------
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def typing_event(self, event):
        await self.send(text_data=json.dumps({'type': 'typing', **event}))

    async def read_event(self, event):
        await self.send(text_data=json.dumps({'type': 'read', **event}))

    async def delete_event(self, event):
        await self.send(text_data=json.dumps({'type': 'delete', **event}))

    async def reaction_event(self, event):
        await self.send(text_data=json.dumps({'type': 'reaction', **event}))

    async def presence_update(self, event):
        await self.send(text_data=json.dumps({'type': 'presence', **event}))

    # ---------------- HELPERS ----------------
    async def send_error(self, code, message):
        await self.send(text_data=json.dumps({'type': 'error', 'code': code, 'message': message}))

    # ---------------- DB ACCESS (sync -> async wrapped) ----------------
    @database_sync_to_async
    def is_conversation_member(self, conversation_id, user_id):
        return ConversationParticipant.objects.filter(
            conversation_id=conversation_id, user_id=user_id, left_at__isnull=True
        ).exists()

    @database_sync_to_async
    def save_message(self, conversation_id, sender_id, text, message_type, client_id, reply_to_id):
        from django.db import IntegrityError
        from .models import Conversation

        try:
            message = Message.objects.create(
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                type=message_type,
                client_id=client_id,
                reply_to_id=reply_to_id,
            )
        except IntegrityError:
            # duplicate client_id -> retry of an already-saved message
            return None

        # denormalized "last message" fields update (chat-list ke liye)
        Conversation.objects.filter(id=conversation_id).update(
            last_message_text=text[:500],
            last_message_at=message.created_at,
            last_message_sender_id=sender_id,
            last_message_type=message_type,
        )

        # baaki sab members ke liye delivery-status row + unread_count++
        other_members = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, left_at__isnull=True
        ).exclude(user_id=sender_id)

        MessageStatus.objects.bulk_create([
            MessageStatus(message=message, user_id=m.user_id) for m in other_members
        ])
        other_members.update(unread_count=models_f_increment())

        return {'id': message.id, 'created_at': message.created_at.isoformat()}

    @database_sync_to_async
    def mark_undelivered_as_delivered(self, conversation_id, user_id):
        MessageStatus.objects.filter(
            message__conversation_id=conversation_id, user_id=user_id, is_delivered=False
        ).update(is_delivered=True, delivered_at=timezone.now())

    @database_sync_to_async
    def mark_message_read(self, message_id, user_id):
        MessageStatus.objects.filter(message_id=message_id, user_id=user_id).update(
            is_read=True, is_delivered=True, read_at=timezone.now()
        )
        ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id, user_id=user_id
        ).update(unread_count=0, last_read_at=timezone.now())

    @database_sync_to_async
    def delete_message(self, message_id, user_id, for_everyone):
        try:
            message = Message.objects.get(id=message_id, conversation_id=self.conversation_id)
        except Message.DoesNotExist:
            return False

        if for_everyone:
            if str(message.sender_id) != str(user_id):
                return False  # sirf sender hi "delete for everyone" kar sakta hai
            message.deleted_for_everyone = True
            message.text = ''
            message.save(update_fields=['deleted_for_everyone', 'text'])
        else:
            message.deleted_for_users.add(user_id)
        return True

    @database_sync_to_async
    def save_reaction(self, message_id, user_id, emoji):
        from .models import MessageReaction
        MessageReaction.objects.update_or_create(
            message_id=message_id, user_id=user_id, defaults={'emoji': emoji}
        )

    @database_sync_to_async
    def set_presence(self, online):
        """
        Multi-device support: active_connections counter use karte hain.
        Returns: final is_online state (true agar abhi bhi koi connection open hai).
        """
        presence, _ = UserPresence.objects.get_or_create(user_id=self.user.id)
        if online:
            presence.active_connections += 1
            presence.is_online = True
        else:
            presence.active_connections = max(0, presence.active_connections - 1)
            presence.is_online = presence.active_connections > 0
            if not presence.is_online:
                presence.last_seen_at = timezone.now()
        presence.save(update_fields=['active_connections', 'is_online', 'last_seen_at'])
        return presence.is_online


def models_f_increment():
    from django.db.models import F
    return F('unread_count') + 1


# ======================================================================
# CALL CONSUMER (1-1 + Group signaling, e.g. WebRTC/Agora/LiveKit)
# ======================================================================
class CallConsumer(AsyncWebsocketConsumer):
    """
    /ws/call/<call_id>/?token=<jwt>

    Ye pure signaling relay hai (SDP offer/answer, ICE candidates) — actual
    audio/video Agora/LiveKit jaisi SFU service khud handle karti hai, isliye
    yaha heavy media payload nahi bhejte, sirf signaling JSON.

    Client -> Server:
        {"type": "signal", "payload": {...sdp/ice...}}
        {"type": "mute", "is_muted": true}
        {"type": "video_off", "is_video_off": true}
        {"type": "leave"}
    """

    async def connect(self):
        self.user = self.scope.get('user')
        if self.user is None or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        self.call_id = self.scope['url_route']['kwargs']['call_id']

        call = await self.get_active_call(self.call_id)
        if call is None:
            await self.close(code=4004)  # call not found / already ended
            return

        allowed = await self.is_call_participant(self.call_id, self.user.id, call)
        if not allowed:
            await self.close(code=4003)
            return

        self.room_group_name = f'call_{self.call_id}'
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.mark_participant_joined(self.call_id, self.user.id)
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'call_signal', 'data': {'event': 'user_joined', 'user_id': str(self.user.id)}},
        )

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        if getattr(self, 'user', None) and self.user.is_authenticated:
            call_ended = await self.mark_participant_left(self.call_id, self.user.id)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'call_signal',
                    'data': {
                        'event': 'user_left',
                        'user_id': str(self.user.id),
                        'call_ended': call_ended,
                    },
                },
            )

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_json'}))

        event_type = data.get('type')

        if event_type == 'signal':
            # raw SDP/ICE payload — sirf relay karo, DB me kuch nahi
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'call_signal',
                    'data': {
                        'event': 'signal',
                        'from_user': str(self.user.id),
                        'payload': data.get('payload'),
                    },
                },
            )
        elif event_type in ('mute', 'video_off'):
            await self.update_participant_flag(self.call_id, self.user.id, event_type, data)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'call_signal',
                    'data': {'event': event_type, 'user_id': str(self.user.id), **data},
                },
            )
        elif event_type == 'leave':
            await self.close(code=1000)
        else:
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'unknown_type'}))

    async def call_signal(self, event):
        await self.send(text_data=json.dumps(event['data']))

    # ---------------- DB ACCESS ----------------
    @database_sync_to_async
    def get_active_call(self, call_id):
        return CallSession.objects.filter(id=call_id).exclude(status=CallStatus.ENDED).first()

    @database_sync_to_async
    def is_call_participant(self, call_id, user_id, call):
        if str(call.caller_id) == str(user_id):
            return True
        if call.is_group_call and call.group_id:
            from .models import GroupMember
            return GroupMember.objects.filter(group_id=call.group_id, user_id=user_id, is_banned=False).exists()
        return CallParticipant.objects.filter(call_id=call_id, user_id=user_id).exists()

    @database_sync_to_async
    def mark_participant_joined(self, call_id, user_id):
        participant, _ = CallParticipant.objects.get_or_create(
            call_id=call_id, user_id=user_id,
            defaults={'status': CallStatus.ONGOING},
        )
        participant.status = CallStatus.ONGOING
        participant.left_at = None
        participant.save(update_fields=['status', 'left_at'])
        CallSession.objects.filter(id=call_id, status=CallStatus.INITIATED).update(
            status=CallStatus.ONGOING, connected_at=timezone.now()
        )

    @database_sync_to_async
    def mark_participant_left(self, call_id, user_id):
        CallParticipant.objects.filter(call_id=call_id, user_id=user_id).update(
            left_at=timezone.now(), status=CallStatus.ENDED
        )
        still_active = CallParticipant.objects.filter(call_id=call_id, left_at__isnull=True).exists()
        if not still_active:
            call = CallSession.objects.filter(id=call_id).first()
            if call and call.status != CallStatus.ENDED:
                ended_at = timezone.now()
                duration = int((ended_at - (call.connected_at or call.started_at)).total_seconds())
                CallSession.objects.filter(id=call_id).update(
                    status=CallStatus.ENDED, ended_at=ended_at, duration_seconds=max(duration, 0)
                )
            return True
        return False

    @database_sync_to_async
    def update_participant_flag(self, call_id, user_id, event_type, data):
        field = 'is_muted' if event_type == 'mute' else 'is_video_off'
        value = bool(data.get(field, True))
        CallParticipant.objects.filter(call_id=call_id, user_id=user_id).update(**{field: value})