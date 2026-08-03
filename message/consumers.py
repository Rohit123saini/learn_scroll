import asyncio
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
from .user_display import build_user_mini, get_display_name

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
        {"type": "study_room_event", "action": "draw_point", "data": {...}}

    Server -> Client events: same "type" field, plus "error" type on failure.

    NOTE — sender profile info: `handle_new_message` `self.user.profile_photo`
    aur `.first_name`/`.last_name` ko DIRECTLY access karta hai (koi extra
    DB query nahi — `self.user` already `AuthMiddleware` se poora loaded
    object hai). `build_user_mini()` ke andar `.url` property call hoti hai
    jo bhi storage-config se compute hota hai, DB hit NAHI karta — isliye
    async context me bina `database_sync_to_async` ke safe hai.
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
    #
    # 🔥 FIX: pehle yahan koi timeout nahi tha. `channel_layer.group_discard`
    # / `group_send` (Redis se baat karte hain) aur `set_presence` (DB) —
    # in teeno me se koi bhi agar Redis/DB slow ya unreachable ho jaaye to
    # `await` HAMESHA KE LIYE ruk sakta hai, kyunki channels_redis apne aap
    # koi timeout lagaakar nahi rakhta. Daphne kuch second wait karke aisi
    # atki hui disconnect() ko force-kill kar deta hai — yahi wo
    # "took too long to shut down and was killed" warning hai jo tum dekh
    # rahe ho. Ab har step 3 second ke andar khatam na ho to skip ho jaayega
    # (aur log me dikh jaayega) — connection phir bhi turant close hoga.
    async def disconnect(self, close_code):
        try:
            if hasattr(self, 'room_group_name'):
                await asyncio.wait_for(
                    self.channel_layer.group_discard(self.room_group_name, self.channel_name),
                    timeout=3,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "ChatConsumer.disconnect: group_discard timed out (Redis slow/unreachable?) conv=%s",
                getattr(self, 'conversation_id', None),
            )
        except Exception:
            logger.exception("ChatConsumer.disconnect: group_discard failed")

        if getattr(self, 'user', None) and self.user.is_authenticated and hasattr(self, 'room_group_name'):
            try:
                still_online = await asyncio.wait_for(self.set_presence(online=False), timeout=3)
                await asyncio.wait_for(
                    self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'presence_update',
                            'user_id': str(self.user.id),
                            'is_online': still_online,
                        },
                    ),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "ChatConsumer.disconnect: presence update timed out (Redis/DB slow?) user=%s",
                    self.user.id,
                )
            except Exception:
                logger.exception("ChatConsumer.disconnect: presence update failed")

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
            'study_room_event': self.handle_study_room_event,  # 🔥 NAYA
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

        sender = build_user_mini(self.user)
        payload = {
            'type': 'chat_message',
            'event': 'message',
            'id': str(message['id']),
            'conversation_id': str(self.conversation_id),
            'sender_id': str(self.user.id),
            # 🔥 NAYA — 'sender_name' ab full display name hai (pehle sirf
            # username tha). Neeche wale extra fields se Flutter side
            # (ChatScreen ka `UserMini.fromSocketEvent` jaisa parser) turant
            # naam + avatar dikha sakta hai, bina alag REST call ke.
            'sender_name': sender['display_name'],
            'sender_username': sender['username'],
            'sender_first_name': sender['first_name'],
            'sender_last_name': sender['last_name'],
            'sender_profile_photo': sender['profile_photo'],
            'message_type': message_type,
            'text': text,
            'reply_to': reply_to,
            'client_id': client_id,
            'created_at': message['created_at'],
        }
        await self.channel_layer.group_send(self.room_group_name, payload)

        # 🔥 NAYA: doosre participants ko push notification (agar wo app
        # band karke baithe hain to unhe pata chale)
        await self.send_push_for_message(message, text, message_type)

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

    # 🔥 NAYA — Study Room ke saare realtime events (whiteboard strokes,
    # shapes, text, sticky notes, floating windows, timer sync, clear-board)
    # isi ek generic passthrough se aate hain: Flutter side
    # `ChatSocketService.sendStudyRoomEvent(action, data)` se
    # {"type": "study_room_event", "action": "...", "data": {...}} bhejta
    # hai. Hum yahan sirf action+data ko validate karke poore room me
    # relay kar dete hain — DB me kuch save nahi karte (jaise typing/
    # reaction ke pattern jaisa), whiteboard state client-side hi rakha
    # jaata hai.
    async def handle_study_room_event(self, data):
        action = data.get('action')
        payload = data.get('data') or {}
        if not action:
            return await self.send_error("missing_field", "action required hai")

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'study_room_broadcast',
                'sender_channel_name': self.channel_name,
                'action': action,
                'data': payload,
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

    async def call_event(self, event):
        # Channel layer se aaya hua call notification event (e.g. incoming call trigger) frontend tak pass karo
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA — study_room_event ka group broadcast wapas client tak pahunchata
    # hai. sender_channel_name check isliye taaki jisne event bheja usi ko
    # apna khud ka event wapas na mile (client already local setState se
    # apni draw turant dikha chuka hota hai — dobara aane se duplicate/glitch
    # ho sakta tha).
    async def study_room_broadcast(self, event):
        if event.get('sender_channel_name') == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            'type': 'study_room_event',
            'action': event['action'],
            'data': event['data'],
        }))

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

    # ---------------- PUSH NOTIFICATION ----------------
    @database_sync_to_async
    def _other_participant_ids(self, conversation_id, sender_id):
        return list(
            ConversationParticipant.objects.filter(conversation_id=conversation_id)
            .exclude(user_id=sender_id)
            .values_list('user_id', flat=True)
        )

    async def send_push_for_message(self, message, text, message_type):
        # yaha import karte hain taaki firebase_admin ki dependency sirf
        # tab load ho jab actually zaroorat ho (aur circular import se bache)
        #
        # 🔥 FIX (asli duplicate-notification wala bug): text messages
        # WEBSOCKET se jaate hain, aur ye function pehle `send_push_to_users`
        # (generic helper) use karta tha — jo top-level `notification` block
        # ke saath push bhejta hai. Isi wajah se har text message pe 2
        # notification aate the (Android ka apna auto-popup + humara khud
        # ka background handler wala). REST/media path pehle se
        # `send_chat_message_push` (data-only) use karta tha isliye sirf
        # WEBSOCKET text messages hi is bug se affected the — ab dono path
        # same, sahi function use karte hain.
        from .push_utils import send_chat_message_push

        recipient_ids = await self._other_participant_ids(self.conversation_id, self.user.id)
        if not recipient_ids:
            return

        sender_name = get_display_name(self.user)

        await database_sync_to_async(send_chat_message_push)(
            recipient_ids,
            sender_name,
            text,
            message_type,
            self.conversation_id,
            message['id'],
        )

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
        # 🔥 FIX: sender_channel_name bhejte hain taaki 'user_joined' event
        # khud joining wale user ko echo hoke wapas na aaye. Isse client
        # (Flutter) side pe reliably pata chal sakta hai ki "DOOSRA banda
        # room me aa gaya" — jo caller ke liye offer bhejne ka sahi trigger
        # hai (pehle caller connect hote hi turant offer bhej deta tha,
        # jab tak callee abhi tak room me join hi nahi hua hota tha to offer
        # hamesha ke liye drop ho jaata tha — isi wajah se ek side
        # "Ringing..." aur doosri side "Connecting..." pe hamesha atki reh
        # jaati thi).
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'call_signal',
                'sender_channel_name': self.channel_name,
                'data': {'event': 'user_joined', 'user_id': str(self.user.id)},
            },
        )

        # 🔥 FIX (reverse-order safety net): normally caller connect hota
        # hai callee se pehle, isliye upar wala broadcast kaafi hai. Lekin
        # agar kabhi ulta ho jaye (callee kisi wajah se pehle connect ho
        # jaye), to caller ko "peer already there" wala future broadcast
        # kabhi nahi milega — kyunki wo event already ja chuka tha jab
        # caller room me tha hi nahi. Isliye yahan check karte hain ki
        # koi doosra participant already ONGOING hai kya, aur agar hai to
        # use seedha khud ko (self.send, group ko nahi) bata dete hain —
        # taaki client side ka wahi 'user_joined' handler safely trigger
        # ho jaaye chahe join order kuch bhi ho.
        existing_peer_id = await self.get_other_ongoing_participant(self.call_id, self.user.id)
        if existing_peer_id:
            await self.send(text_data=json.dumps({'event': 'user_joined', 'user_id': existing_peer_id}))

    async def disconnect(self, close_code):
        try:
            if hasattr(self, 'room_group_name'):
                await asyncio.wait_for(
                    self.channel_layer.group_discard(self.room_group_name, self.channel_name),
                    timeout=3,
                )
        except asyncio.TimeoutError:
            logger.warning("CallConsumer.disconnect: group_discard timed out call=%s", getattr(self, 'call_id', None))
        except Exception:
            logger.exception("CallConsumer.disconnect: group_discard failed")

        if getattr(self, 'user', None) and self.user.is_authenticated and hasattr(self, 'room_group_name'):
            try:
                call_ended = await asyncio.wait_for(self.mark_participant_left(self.call_id, self.user.id), timeout=3)
                await asyncio.wait_for(
                    self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'call_signal',
                            'data': {
                                'event': 'user_left',
                                'user_id': str(self.user.id),
                                'call_ended': call_ended,
                            },
                        },
                    ),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                logger.warning("CallConsumer.disconnect: mark_participant_left/group_send timed out user=%s",
                               self.user.id)
            except Exception:
                logger.exception("CallConsumer.disconnect: participant-left handling failed")

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_json'}))

        event_type = data.get('type')

        if event_type == 'signal':
            # raw SDP/ICE payload — sirf relay karo, DB me kuch nahi
            # 🔥 FIX: 'sender_channel_name' bhejte hain taaki group_send
            # sender ko uska khud ka offer/answer/ICE wapas na bheje.
            # Pehle ye missing tha, isliye caller apna hi offer/ICE candidate
            # wapas receive karke setRemoteDescription/addCandidate kar raha
            # tha — jisse WebRTC negotiation silently corrupt ho jaata tha
            # (call "connect" toh ho jaati thi, lekin audio/video kabhi
            # flow nahi karta tha).
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'call_signal',
                    'sender_channel_name': self.channel_name,
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
                    'sender_channel_name': self.channel_name,
                    'data': {'event': event_type, 'user_id': str(self.user.id), **data},
                },
            )
        elif event_type == 'leave':
            await self.close(code=1000)
        else:
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'unknown_type'}))

    async def call_signal(self, event):
        # 🔥 FIX: apna hi bheja hua signal khud ko relay mat karo.
        if event.get('sender_channel_name') == self.channel_name:
            return
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
    def get_other_ongoing_participant(self, call_id, user_id):
        other = CallParticipant.objects.filter(
            call_id=call_id, status=CallStatus.ONGOING
        ).exclude(user_id=user_id).exclude(left_at__isnull=False).first()
        return str(other.user_id) if other else None

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