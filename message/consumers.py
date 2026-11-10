# message/consumers.py
import asyncio
import json
import logging
import time

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    BlockedUser,
    CallParticipant,
    CallSession,
    CallStatus,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageStatus,
    RequestStatus,
    UserPresence,
)
from .user_display import build_user_mini, get_display_name
from .mentions import extract_mentioned_user_ids
from .media_utils import create_group_media_for_message
from .voice_meta import sanitize_voice_meta  # 🔥 NAYA (M4a) — voice waveform
from .constants import MAX_PINNED_PER_CONVERSATION
from .throttles import WSMessageRateLimiter, WSStudyRoomNoteRateLimiter
from . import sticky_notes  # NAYA — collaborative study-room sticky notes
from . import message_requests  # 🔥 NAYA (M1-BE) — message requests rules
from .cache_utils import (
    persist_last_seen_throttled,
    presence_connect,
    presence_disconnect,
    presence_touch,
    invalidate_presence_cache,
)
# 🔥 NAYE — advanced features (link preview / auto voice-transcription)
from .tasks import generate_link_preview_task, transcribe_voice_message_task

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
        # 🔥 FIX — media message ab WS se bhi bheja ja sakta hai (pehle
        # sirf REST se possible tha, see media_utils.py / save_message):
        {"type": "message", "client_id": "...", "message_type": "image",
         "file_url": "https://.../photo.jpg", "file_urls": [], "thumbnail_url": null,
         "meta": {"size": 204800}, "reply_to": null}
        {"type": "typing", "is_typing": true}
        {"type": "read", "message_id": "..."}
        {"type": "delete", "message_id": "...", "for_everyone": false}
        {"type": "reaction", "message_id": "...", "emoji": "🔥"}
        {"type": "pin", "message_id": "...", "pin": true}
        {"type": "study_room_event", "action": "draw_point", "data": {...}}
        # NAYA — sticky notes (persisted + ack; see sticky_notes.py docstring):
        {"type": "study_room_event", "action": "note_add|note_move|note_edit|note_front|note_delete|note_drag", "data": {...}}

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

        # 🔥 NAYA (M3b) — "chat khuli hai" counter (vanish mode ke "chat band
        # hone par delete" ke liye). Fail ho jaaye to chat normal chalti rahe.
        try:
            from .tasks import chat_open_incr
            await asyncio.wait_for(
                database_sync_to_async(chat_open_incr)(str(self.conversation_id)), timeout=3
            )
            self._chat_open_counted = True
        except Exception:
            logger.exception("ChatConsumer.connect: chat_open counter incr failed")

        # presence: online mark karo (multi-device safe counter).
        # T0.1 — ab sirf cache/Redis counter (DB write nahi), phir bhi best-effort:
        # presence fail hone se chat connect kabhi crash nahi hona chahiye.
        try:
            await asyncio.wait_for(self.set_presence(online=True), timeout=3)
        except Exception:
            logger.exception("ChatConsumer.connect: set_presence failed user=%s", self.user.id)
        self._presence_touched_at = time.monotonic()
        await self.channel_layer.group_send(
            self.room_group_name,
            {'type': 'presence_update', 'user_id': str(self.user.id), 'is_online': True},
        )

        # 🔥 NAYA — is conversation ke room-group ke alawa, is user ke
        # baaki saare direct (1-1) chat-partners ko bhi turant batao ki
        # ye online ho gaya — taaki unki contact-list/chat-list bhi (jo
        # kabhi is conversation ka room join hi nahi karti) turant update
        # ho sake, `UserPresenceView`'s cached REST poll ka wait kiye
        # bina. See `broadcast_presence_to_partners`'s docstring aur
        # `cache_utils.py`'s `PRESENCE_TTL` note — asli "turant
        # offline->online hone par slow feel" wala fix push hai, sirf
        # TTL tune karna nahi.
        try:
            await asyncio.wait_for(self.broadcast_presence_to_partners(True), timeout=3)
        except asyncio.TimeoutError:
            logger.warning(
                "ChatConsumer.connect: presence partner-broadcast timed out user=%s", self.user.id
            )
        except Exception:
            logger.exception("ChatConsumer.connect: presence partner-broadcast failed")

        # jo messages abhi tak deliver nahi hue the unhe deliver mark karo
        # 🔥 NAYA (M1-BE) — pending/declined request me ye skip: delivered
        # tick bhi sender ko signal hai ki receiver ne chat khola.
        if await self._signals_allowed(force=True):
            # M1 — ye ek DB write hai; SQLite "database is locked" (ya koi bhi DB
            # hiccup) pe poora WS connect crash nahi hona chahiye. Delivered-tick
            # best-effort hai, agle connect/read pe phir ho jaata hai.
            try:
                await self.mark_undelivered_as_delivered(self.conversation_id, self.user.id)
            except Exception:
                logger.warning(
                    "ChatConsumer.connect: mark_undelivered failed user=%s", self.user.id, exc_info=True
                )

        # 🔥 NAYA — missed-event replay ("sync"). Pehle offline/background
        # rehte hue jo bhi WS events (naye messages) miss ho jaate the,
        # unke liye sirf REST full-refetch hi option tha. Ab yahan seedha
        # isi (naye) socket ko — poore room ko NAHI, `self.send` se sirf
        # is connection ko — is user ke `last_read_message` ke baad ke
        # saare naye messages ek 'sync' payload me bhej dete hain, taaki
        # chhote gap (reconnect) ke baad client turant local-merge kar
        # sake, bina full list reload ke.
        await self.send_missed_events_sync(self.conversation_id, self.user.id)

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

        # 🔥 NAYA (M3b) — chat-open counter -1; agar ye aakhri connection tha
        # to "seen" vanish messages ka sweep schedule karo. 5s countdown:
        # flaky reconnect pe counter wapas >0 ho jaata hai aur task skip karta hai.
        if getattr(self, '_chat_open_counted', False):
            try:
                from .tasks import chat_open_decr, sweep_vanish_for_conversation
                remaining = await asyncio.wait_for(
                    database_sync_to_async(chat_open_decr)(str(self.conversation_id)), timeout=3
                )
                if remaining == 0:
                    sweep_vanish_for_conversation.apply_async(
                        args=[str(self.conversation_id)], countdown=5
                    )
            except Exception:
                logger.exception("ChatConsumer.disconnect: vanish sweep schedule failed")

        if getattr(self, 'user', None) and self.user.is_authenticated and hasattr(self, 'room_group_name'):
            still_online, last_seen_at = None, None
            try:
                still_online, last_seen_at = await asyncio.wait_for(self.set_presence(online=False), timeout=3)
                await asyncio.wait_for(
                    self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'presence_update',
                            'user_id': str(self.user.id),
                            'is_online': still_online,
                            'last_seen_at': last_seen_at.isoformat() if last_seen_at else None,
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

            # 🔥 NAYA — connect()'s partner-broadcast ka mirror. Sirf tab
            # bhejte hain jab upar wala `set_presence`/room-broadcast
            # (kam-se-kam DB write) successfully ho chuka ho — `still_online`
            # None hona matlab upar hi fail ho gaya tha, is user ki asli
            # state hume pata hi nahi, to galat/stale value partners ko
            # push karne se better hai kuch na bhejna (agla connect/
            # disconnect ya `PRESENCE_TTL` expiry khud correct kar dega).
            if still_online is not None:
                try:
                    await asyncio.wait_for(
                        self.broadcast_presence_to_partners(still_online, last_seen_at), timeout=3
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "ChatConsumer.disconnect: presence partner-broadcast timed out user=%s", self.user.id
                    )
                except Exception:
                    logger.exception("ChatConsumer.disconnect: presence partner-broadcast failed")

            # T0.1 — last connection band hua to hi DB me `last_seen` likho, aur wo bhi
            # per-user 60s me max ek baar (cache me value turant fresh hai). Best-effort.
            if still_online is False and last_seen_at is not None:
                try:
                    await asyncio.wait_for(self.persist_last_seen(last_seen_at), timeout=3)
                except Exception:
                    logger.warning(
                        "ChatConsumer.disconnect: last_seen DB persist skipped user=%s", self.user.id
                    )

    # ---------------- RECEIVE (client -> server) ----------------
    async def receive(self, text_data=None, bytes_data=None):
        # T0.1 — online-counter TTL refresh (lamba idle chat bhi "online" rahe). 5 min throttle.
        if time.monotonic() - getattr(self, '_presence_touched_at', 0) > 300:
            self._presence_touched_at = time.monotonic()
            try:
                await self.touch_presence()
            except Exception:
                logger.debug("ChatConsumer.receive: presence touch failed", exc_info=True)

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
            'pin': self.handle_pin_message,  # 🔥 NAYA
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
    # 🔥 FIX — WS `message` event pehle sirf text carry karta tha
    # (file_url/file_urls/thumbnail_url/meta socket payload me the hi
    # nahi, aur `save_message` unhe Message row pe set hi nahi karta
    # tha). Isse do problems thi:
    #   1. Media message WS se bheja jaaye to DB me file_url blank save
    #      hota — effectively media message REST ke bina bhejna hi
    #      possible nahi tha (jabki upload_view.py ka doc comment kehta
    #      hai "REST ya WS dono se bheja ja sakta hai").
    #   2. `GroupMedia` gallery wire karne ke liye (ye session ka fix)
    #      file data chahiye hi thi — wo bina iske available nahi thi.
    # Ab REST `MessageCreateSerializer` jaisa hi basic validation yahan
    # bhi hai: media type ho to file_url ya file_urls me se koi ek hona
    # zaroori hai.
    _MEDIA_TYPES = {'image', 'video', 'audio', 'file', 'presentation'}

    async def handle_new_message(self, data):
        # 🔥 NAYA — REST side `MessageSendThrottle` (DRF) se protected thi,
        # WS path (jo docs ke mutabik zyada-use-hone-wala primary path hai)
        # bilkul unprotected tha — koi bhi ek tight client-side loop se
        # is socket pe unlimited messages bhej sakta tha. DRF throttles WS
        # consumers pe apply nahi hote, isliye ye alag, cache-backed
        # sliding-window limiter hai (`throttles.py`).
        allowed, retry_after = await database_sync_to_async(WSMessageRateLimiter.check)(self.user.id)
        if not allowed:
            return await self.send_error(
                "rate_limited", f"Bahut fast bhej rahe ho, {retry_after}s ruko."
            )

        text = (data.get('text') or '').strip()
        message_type = data.get('message_type', 'text')
        client_id = data.get('client_id')  # offline-retry idempotency
        reply_to = data.get('reply_to')
        file_url = data.get('file_url')
        file_urls = data.get('file_urls') or []
        thumbnail_url = data.get('thumbnail_url')
        meta = sanitize_voice_meta(data.get('meta') or {}, message_type)  # 🔥 M4a: waveform clean

        if not text and message_type == 'text':
            return await self.send_error("empty_message", "Empty message bhej nahi sakte")

        if message_type in self._MEDIA_TYPES and not file_url and not file_urls:
            return await self.send_error(
                "missing_file", "Media message ke liye file_url ya file_urls chahiye"
            )

        # 🔥 NAYA — REST `messages` action jaisa hi block-check yahan bhi
        # zaroori tha: `BlockedUser` import to top pe already tha lekin
        # kahin use hi nahi ho raha tha, isliye block karne ke baad bhi
        # dusra user is (primary, REST se zyada use hone waala) socket path
        # se normally message bhej pa raha tha. Group conversations me block
        # ka concept hi nahi hai, isliye skip.
        if await self.is_blocked_in_conversation(self.conversation_id, self.user.id):
            return await self.send_error(
                "blocked", "Block hone ki wajah se message nahi bheja ja sakta"
            )

        allowed, reason, code = await self.check_group_message_rules(self.conversation_id, self.user)
        if not allowed:
            # M9a — 'admins_only' alag code taaki FE composer lock/dialog dikha sake
            return await self.send_error(code or "group_rule_blocked", reason)

        message = await self.save_message(
            conversation_id=self.conversation_id,
            sender_id=self.user.id,
            text=text,
            message_type=message_type,
            client_id=client_id,
            reply_to_id=reply_to,
            file_url=file_url,
            file_urls=file_urls,
            thumbnail_url=thumbnail_url,
            meta=meta,
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
            # 🔥 FIX — media fields ab broadcast me bhi jaate hain (pehle
            # sirf text jaata tha, isliye WS se bheja hua media message
            # doosre connected members ko bina file ke dikhta — sirf
            # sender ke apne local optimistic-UI se hi file dikhta tha).
            'file_url': file_url,
            'file_urls': file_urls,
            'thumbnail_url': thumbnail_url,
            'meta': meta,
            'reply_to': reply_to,
            'client_id': client_id,
            # 🔥 NAYA — @mentions (REST path ke `mentioned_user_ids` jaisa hi field).
            'mentioned_user_ids': [str(uid) for uid in message['mentioned_ids']],
            'created_at': message['created_at'],
        }
        await self.channel_layer.group_send(self.room_group_name, payload)

        # 🔥 NAYE (ADVANCED FEATURES) — `views.py`'s REST path jaisa hi,
        # background/async (Celery `.delay()`), WS `receive()` ko bilkul
        # block nahi karte. `save_message` already commit kar chuka hota
        # hai is point tak, isliye row guaranteed available hoga worker ko.
        if message_type == 'text' and text:
            await database_sync_to_async(generate_link_preview_task.delay)(str(message['id']))
        elif message_type == 'audio' and file_url:
            await database_sync_to_async(transcribe_voice_message_task.delay)(str(message['id']))

        # 🔥 NAYA — ConversationsScreen (list) turant update ho, chahe
        # user is specific chat ke andar na ho. `chat_{conversation_id}`
        # group sirf unhi ko milta hai jo abhi ISI chat ke andar hain;
        # yahan har participant ke apne global `user_<id>` group ko ek
        # halka event bhejte hain — InboxConsumer se connected socket
        # (jo ConversationsScreen khulte hi connect hota hai) isko sunke
        # list ka relevant item locally update kar deta hai, bina full
        # API refetch ke.
        await self.broadcast_inbox_update(message, sender, text, message_type)

        # 🔥 NAYA: doosre participants ko push notification (agar wo app
        # band karke baithe hain to unhe pata chale) — @mention wale
        # members ko normal push se exclude karke unhe alag "mention"
        # push milta hai (mute state ko override karta hai).
        await self.send_push_for_message(
            message, text, message_type, exclude_ids=message['mentioned_ids'],
            is_announcement=message['is_announcement'],
        )
        if message['mentioned_ids']:
            await self.send_mention_push_notification(message, text, is_announcement=message['is_announcement'])

    async def handle_typing(self, data):
        # 🔥 NAYA — har keystroke pe client `typing` event bhej sakta hai;
        # bina throttle ke ye channel layer (Redis) ko high-frequency
        # broadcast se flood kar sakta hai. Same WSMessageRateLimiter reuse
        # kiya hai par alag, generous limit ke saath (typing kam "costly"
        # hai message-send se, isliye tight rakhne ki zaroorat nahi — sirf
        # abuse-level spam rokna hai).
        allowed, _ = await database_sync_to_async(WSMessageRateLimiter.check)(
            f"typing:{self.user.id}"
        )
        if not allowed:
            return  # silently drop — typing indicator ke liye error dikhana UX-wrong hoga

        # 🔥 NAYA (M1-BE) — pending/declined request me receiver ka typing
        # sender tak nahi jaata (silent drop, koi error nahi).
        if not await self._signals_allowed():
            return

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

        # 🔥 NAYA (M1-BE) — pending/declined request me read-receipt na DB me
        # mark hoti hai, na sender ko broadcast. Accept ke baad normal.
        if not await self._signals_allowed():
            return

        # DB bookkeeping (MessageStatus / unread_count / core.Notification)
        # HAMESHA hoti hai — privacy toggle sirf blue-tick VISIBILITY gate
        # karta hai, "maine padha ya nahi" nahi.
        await self.mark_message_read(message_id, self.user.id)

        # Mutual WhatsApp-style rule (REST `read-status` jaisa): reader ne
        # `show_read_receipts=False` kar rakha hai to read_event kisi ko
        # broadcast nahi hota. Viewer-side check `read_event` me hai.
        if await self._receipts_off(self.user.id):
            return

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

        # 🔧 BUG FIX — pehle ye broadcast `for_everyone` ki value ke bina
        # bhi UNCONDITIONALLY poore room group ko jaata tha. "Delete for
        # me" (`for_everyone=False`) sirf DELETER ke liye private hona
        # chahiye — koi bhi doosra participant ko is baat ka pata bhi nahi
        # chalna chahiye ki kisi ne kuch delete kiya (WhatsApp isi tarah
        # kaam karta hai). Agar koi client is event ko blindly "message
        # hata do" jaisa handle karta (bina `for_everyone` check kiye), to
        # ek PRIVATE delete-for-me sabke screen se message hata sakta tha
        # — real correctness bug, sirf missing-feature nahi. Ab sirf
        # `for_everyone=True` hone par hi poore room ko broadcast hota hai.
        if for_everyone:
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

    # 🔥 NAYA — Message pin/unpin (REST `MessageViewSet.pin` jaisa hi
    # permission rule: group me sirf admin/mod, private chat me dono me
    # se koi bhi). WhatsApp jaisa max-3-pinned limit REST wale
    # `MAX_PINNED_PER_CONVERSATION` constant jaisa hi yahan bhi hai.
    # 🔥 FIX — ab `constants.py` se shared module-level import (upar
    # dekho), pehle yahan alag class attribute tha jo views.py se
    # manually sync rakhna padta (see constants.py comment).

    async def handle_pin_message(self, data):
        message_id = data.get('message_id')
        pin = bool(data.get('pin', True))
        if not message_id:
            return await self.send_error("missing_field", "message_id required hai")

        ok, reason = await self.pin_or_unpin_message(message_id, self.user.id, pin)
        if not ok:
            return await self.send_error("not_allowed", reason)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'pin_event',
                'event': 'pinned' if pin else 'unpinned',
                'message_id': message_id,
                'conversation_id': str(self.conversation_id),
                'actor_id': str(self.user.id),
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

        # 🔥 S4 — role gate: removed / waiting => koi event nahi; viewer sirf dekhe; share_policy=host_only me
        # participant ka whiteboard / notes / presentation band; end_session / clear_board sirf host / co-host.
        allowed, deny_code = await self._study_room_gate(action)
        if not allowed:
            return await self._send_study_room_event('denied', {'action': action, 'code': deny_code})

        # 🔥 NAYA — sticky notes ab generic passthrough nahi: har op DB me
        # persist hota hai (per-field last-write-wins), sender ko `note_ack`
        # milta hai aur baaki sab ko authoritative `note_upsert`/`note_removed`.
        if action in sticky_notes.NOTE_ACTIONS:
            return await self.handle_note_event(action, payload)

        await self._broadcast_study_room(action, payload)

        # Board-level events (clear / page remove) ka asar notes pe bhi
        # padta hai — relay ke saath DB se bhi saaf karo, warna rejoin pe
        # "clear" kiye hue notes wapas aa jaate.
        if action in sticky_notes.BOARD_CLEAR_ACTIONS and isinstance(payload, dict):
            try:
                await database_sync_to_async(sticky_notes.clear_notes_for_page)(
                    self.conversation_id, payload.get('pageId'), self.user
                )
            except Exception:
                logger.exception("clear_notes_for_page failed conv=%s", self.conversation_id)

    _SR_ROLE_CACHE_SECONDS = 3.0

    async def _study_room_gate(self, action):
        """(allowed, code). Role + share_policy 3s socket pe cache (draw_point bahut frequent hai);
        role / settings / removal event aate hi cache clear (`study_room_broadcast`)."""
        from . import study_room_roles
        cached = getattr(self, '_sr_role_cache', None)
        now = time.monotonic()
        if cached is None or now - cached[0] > self._SR_ROLE_CACHE_SECONDS:
            try:
                role, policy = await database_sync_to_async(study_room_roles.socket_role_info)(
                    self.conversation_id, self.user.id)
            except Exception:
                logger.exception("study room role lookup failed conv=%s", self.conversation_id)
                return True, ''   # fail-open: DB hiccup se poora room freeze nahi
            cached = (now, role, policy)
            self._sr_role_cache = cached
        _, role, policy = cached
        return study_room_roles.ws_action_allowed(
            action, role, policy, is_note_action=action in sticky_notes.NOTE_ACTIONS)

    async def _broadcast_study_room(self, action, payload):
        """Room ke baaki sockets ko relay (sender ka apna channel exclude — see study_room_broadcast)."""
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'study_room_broadcast',
                'sender_channel_name': self.channel_name,
                # 🔥 S8 — receiver side block filter + minor name masking ke liye (client ke bheje userId pe bharosa nahi).
                'sender_user_id': str(self.user.id),
                'action': action,
                'data': payload,
            },
        )

    async def _send_study_room_event(self, action, data):
        await self.send(text_data=json.dumps({'type': 'study_room_event', 'action': action, 'data': data}))

    async def handle_note_event(self, action, payload):
        payload = payload if isinstance(payload, dict) else {}

        # Ephemeral live-drag preview: DB nahi, ack nahi, sirf validate + relay.
        if action == sticky_notes.ACTION_DRAG:
            allowed, _ = await database_sync_to_async(WSStudyRoomNoteRateLimiter.check)(self.user.id, 'drag')
            if not allowed:
                return  # silently drop — preview ke liye error dikhana UX-wrong hai
            clean = sticky_notes.clean_drag_payload(payload)
            if clean is None:
                return
            clean['userId'] = str(self.user.id)
            return await self._broadcast_study_room(sticky_notes.ACTION_DRAG, clean)

        op_id = str(payload.get('opId') or '')[:64]
        allowed, _ = await database_sync_to_async(WSStudyRoomNoteRateLimiter.check)(self.user.id, 'ops')
        if not allowed:
            return await self._send_study_room_event('note_ack', {
                'opId': op_id, 'noteId': str(payload.get('noteId') or ''),
                'status': 'rejected', 'reason': 'rate_limited',
            })

        result = await database_sync_to_async(sticky_notes.apply_note_op)(
            self.conversation_id, self.user, action, payload
        )
        # Pehle sender ko ack (optimistic UI confirm/rollback), phir baaki sab ko broadcast.
        await self._send_study_room_event('note_ack', result.ack)
        for b_action, b_data in result.broadcasts:
            await self._broadcast_study_room(b_action, b_data)

    # ---------------- GROUP EVENT HANDLERS (server -> socket) ----------------
    # 🔥 NAYA (M1-BE) — kya is socket ke user ke typing/read/delivered signals
    # doosre ko dikhne chahiye? False jab ye chat uske liye pending/declined
    # message-request hai. Typing har keystroke pe aata hai, isliye jawab
    # `_SIGNALS_CACHE_SECONDS` tak instance pe cache hota hai (accept ke baad
    # worst-case itni der signals late shuru honge). Row na mile/lookup fail
    # ho to True (fail-open).
    _SIGNALS_CACHE_SECONDS = 5

    async def _signals_allowed(self, force=False):
        now = time.monotonic()
        cached = getattr(self, '_signals_cache', None)
        if cached is not None and not force and now - cached[0] < self._SIGNALS_CACHE_SECONDS:
            return cached[1]
        try:
            allowed = await database_sync_to_async(message_requests.is_request_accepted)(
                self.conversation_id, self.user.id,
            )
        except Exception:
            logger.exception("ChatConsumer._signals_allowed failed user=%s", self.user.id)
            allowed = True
        self._signals_cache = (now, allowed)
        return allowed

    async def chat_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def typing_event(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'typing'}))

    # Issue #2 (RestrictUser) — `A restricted B` means A's read receipts and
    # online/last-seen must never reach B. These group events are broadcast
    # to the whole room, so the filter is applied per receiving socket: if
    # the event's actor has restricted THIS socket's user, drop it silently
    # (no error, no placeholder — B must not be able to tell).
    @database_sync_to_async
    def _actor_restricted_me(self, actor_user_id):
        from user_profile.services import has_restricted
        try:
            actor_id = int(actor_user_id)
        except (TypeError, ValueError):
            return False
        if actor_id == self.user.id:
            return False
        return has_restricted(actor_id, self.user.id)

    # `UserPresence.show_read_receipts=False` wale user ke liye: row na mile
    # ya lookup fail ho to False (fail-open — default setting ON hai).
    @database_sync_to_async
    def _receipts_off(self, user_id):
        try:
            return UserPresence.objects.filter(
                user_id=user_id, show_read_receipts=False,
            ).exists()
        except Exception:
            logger.exception("ChatConsumer._receipts_off failed user=%s", user_id)
            return False

    async def _viewer_receipts_off(self):
        # read_event har reader ke liye har socket pe aata hai (group chats),
        # isliye viewer ka toggle instance pe `_SIGNALS_CACHE_SECONDS` tak cache.
        now = time.monotonic()
        cached = getattr(self, '_viewer_receipts_cache', None)
        if cached is not None and now - cached[0] < self._SIGNALS_CACHE_SECONDS:
            return cached[1]
        off = await self._receipts_off(self.user.id)
        self._viewer_receipts_cache = (now, off)
        return off

    async def read_event(self, event):
        if await self._actor_restricted_me(event.get('user_id')):
            return
        # Mutual rule ka dusra half: dekhne waale (is socket ke user) ne khud
        # receipts off kar rakhe hain to use bhi dusron ke blue-tick events
        # nahi jaate. Apna khud ka read event (multi-device echo) allowed.
        if str(event.get('user_id')) != str(self.user.id) and await self._viewer_receipts_off():
            return
        await self.send(text_data=json.dumps({**event, 'type': 'read'}))

    async def delete_event(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'delete'}))

    # 🔥 NAYA — REST `MessageViewSet.partial_update` (text edit) ke liye
    # koi WS broadcast pehle nahi tha — is poore app me `edit_event` type
    # kabhi define hi nahi hua tha (WS se edit karne ka koi raasta bhi
    # nahi tha, sirf REST se). Matlab agar user A ek message edit karta
    # (REST se — jo hai hi is app ka intended edit path), to user B ki
    # khuli chat screen (jo WS se live updates sunti hai) ko kabhi pata hi
    # nahi chalta — refresh karne tak purana text hi dikhta rehta, poore
    # "real-time chat" ka point defeat ho jaata sirf isi ek action ke liye.
    # Ab `views.py`'s `partial_update` yahi event group ko bhejta hai.
    async def edit_event(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'edit'}))

    # 🔥 NAYA (ADVANCED FEATURES) — link-preview aur voice-transcript dono
    # `tasks.py` ke background Celery tasks se hi likhe jaate hain (`Message.
    # meta` update), request-response cycle se bahar. Plain passthrough,
    # `disappearing_messages_updated` jaisa hi pattern — event me already
    # `type: 'meta_update'` set hai (`_broadcast_meta_update` helper se).
    async def meta_update(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA — poll vote/close dono `views.py` (`MessageViewSet.poll_vote`/
    # `poll_close`) se hi hote hain (REST-only, `schedule_message` jaisa
    # hi pattern — poll create/vote WS se nahi hota). Plain passthrough,
    # `meta_update`/`disappearing_messages_updated` jaisa — event me
    # already poora updated `poll` object (options + vote counts) hota
    # hai, taaki khuli hui chat screen live results dekhe bina refresh
    # kiye.
    async def poll_update(self, event):
        await self.send(text_data=json.dumps(event))

    async def reaction_event(self, event):
        await self.send(text_data=json.dumps({**event, 'type': 'reaction'}))

    async def pin_event(self, event):
        await self.send(text_data=json.dumps({'type': 'pin', **event}))

    # 🔥 BUG FIX — `ConversationViewSet.disappearing_messages` (views.py)
    # broadcasts `type: 'disappearing_messages_updated'` to this exact
    # room group, but there was NO handler method for it here. Channels
    # requires a consumer method named after the event `type` for every
    # group_send — without it, every connected member of the chat gets a
    # "No handler for message type" error the moment someone changes this
    # setting. Plain passthrough, same pattern as `presence_update`.
    async def disappearing_messages_updated(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 BUG FIX — same issue as above: `GroupViewSet.destroy` (views.py)
    # broadcasts `type: 'group_deleted'` right before deleting the group,
    # so every member still connected to the chat can be told to leave
    # the screen — but again, no handler existed for it.
    async def group_deleted(self, event):
        await self.send(text_data=json.dumps(event))

    async def presence_update(self, event):
        if await self._actor_restricted_me(event.get('user_id')):
            return
        # S4 -- the person chose who may see their online status / last seen.
        if await _presence_hidden_by_privacy(event.get('user_id'), self.user.id):
            return
        await self.send(text_data=json.dumps({**event, 'type': 'presence'}))

    # Block / unblock happened between the two people of this 1-1 chat
    # (user_profile/block_live.py). Same payload for both sides — it never
    # says who blocked whom. Sending is already refused server-side.
    async def block_changed(self, event):
        await self.send(text_data=json.dumps(event))

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
        action = event.get('action')
        # S4 — REST host-controls ke targeted / manager-only events.
        if action in ('role_changed', 'room_settings_changed', 'removed_from_room'):
            self._sr_role_cache = None
        # S8 — block / unblock ke baad is socket ka block cache turant refresh (REST `block/` ye event dono ko bhejta hai).
        if action == 'room_block_changed':
            self._sr_block_cache = None
        targets = event.get('target_user_ids')
        if targets and str(self.user.id) not in targets:
            # role_changed / settings sab ko chahiye (UI badge); targeted sirf target ko.
            if action not in ('role_changed', 'room_settings_changed', 'hand_raised'):
                return
        if event.get('manager_only'):
            from . import study_room_roles
            try:
                role, _p = await database_sync_to_async(study_room_roles.socket_role_info)(
                    self.conversation_id, self.user.id)
            except Exception:
                return
            if not study_room_roles.is_manager(role):
                return

        data = event.get('data')
        # 🔥 S8 — (1) blocked pair ke events hide, (2) public group me minor ka naam / photo adult strangers se masked.
        # Dono best-effort (fail-open) — DB hiccup se poora room freeze nahi hona chahiye.
        data = await self._study_room_apply_safety(event, action, data)
        if data is None:
            return
        await self.send(text_data=json.dumps({
            'type': 'study_room_event',
            'action': action,
            'data': data,
        }))

    # ---------------- S8 — room safety filters (receiver side) ----------------
    _SR_BLOCK_CACHE_SECONDS = 10.0
    _SR_MASK_CACHE_SECONDS = 30.0
    # Moderation / system events blocked user se aaye tab bhi pahunchne chahiye (room end, board clear, host actions).
    _SR_BLOCK_EXEMPT_ACTIONS = frozenset({
        'end_session', 'clear_board', 'room_settings_changed', 'role_changed', 'removed_from_room',
        'muted_by_host', 'waiting_room_request', 'waiting_room_decision', 'denied', 'room_rules_changed',
        'room_block_changed',
    })
    # Naam / photo wale user-generated events: {action: (id key, name key, avatar key)}
    _SR_IDENTITY_KEYS = {
        'user_joined': ('userId', 'displayName', 'avatarUrl'),
        'update_window': ('userId', 'displayName', 'avatarUrl'),
        'quick_chat': ('senderId', 'senderName', None),
        'sticker': ('senderId', 'senderName', None),
        'hand_raised': ('userId', 'displayName', 'avatarUrl'),
    }

    async def _sr_blocked_ids(self):
        """Dono direction ke blocked user ids (str set) — socket pe 10s cache, `room_block_changed` pe clear."""
        from . import study_room_safety
        cached = getattr(self, '_sr_block_cache', None)
        now = time.monotonic()
        if cached is not None and now - cached[0] <= self._SR_BLOCK_CACHE_SECONDS:
            return cached[1]
        try:
            ids = await database_sync_to_async(study_room_safety.block_pair_ids)(self.user.id)
        except Exception:
            logger.exception("study room block lookup failed conv=%s", self.conversation_id)
            return cached[1] if cached else frozenset()
        ids = frozenset(str(i) for i in ids)
        self._sr_block_cache = (now, ids)
        return ids

    async def _sr_masked_identity(self, target_id):
        """`target_id` ka naam / photo is viewer se chhupana hai? {'display_name', 'avatar_url'} ya None (30s cache)."""
        from . import study_room_safety
        cache = getattr(self, '_sr_mask_cache', None)
        if cache is None:
            cache = self._sr_mask_cache = {}
        now = time.monotonic()
        key = str(target_id)
        hit = cache.get(key)
        if hit is not None and now - hit[0] <= self._SR_MASK_CACHE_SECONDS:
            return hit[1]
        try:
            identity = await database_sync_to_async(study_room_safety.masked_identity_for)(
                self.user, self.conversation_id, target_id)
        except Exception:
            logger.exception("study room mask lookup failed conv=%s", self.conversation_id)
            return None
        if len(cache) > 200:
            cache.clear()
        cache[key] = (now, identity)
        return identity

    async def _study_room_apply_safety(self, event, action, data):
        """Returns payload (shayad naam masked) ya None => ye event is socket ko mat bhejo."""
        sender_id = event.get('sender_user_id')
        sender_id = str(sender_id) if sender_id else None
        id_key, name_key, avatar_key = self._SR_IDENTITY_KEYS.get(action, (None, None, None))
        payload = data if isinstance(data, dict) else None

        # Kis user ka event hai: relay me server-stamped sender; REST `hand_raised` me payload ka userId.
        actor_id = sender_id
        if actor_id is None and action == 'hand_raised' and payload is not None:
            actor_id = str(payload.get('userId') or '') or None
        # `update_window` kisi aur ki window bhi drag kar sakta hai => us window ka owner bhi dekho.
        subject_id = None
        if action == 'update_window' and payload is not None and payload.get('userId'):
            subject_id = str(payload.get('userId'))

        if action not in self._SR_BLOCK_EXEMPT_ACTIONS and (actor_id or subject_id):
            blocked = await self._sr_blocked_ids()
            if blocked and ((actor_id and actor_id in blocked) or (subject_id and subject_id in blocked)):
                return None

        if id_key is None or payload is None:
            return data
        owner_id = subject_id if action == 'update_window' else (sender_id or str(payload.get(id_key) or '') or None)
        if not owner_id or owner_id == str(self.user.id):
            return data
        identity = await self._sr_masked_identity(owner_id)
        if identity is None:
            return data
        payload = dict(payload)   # in-memory channel layer me dict shared hota hai — original mutate mat karo
        if sender_id and action != 'update_window':
            payload[id_key] = sender_id   # client ke bheje id pe bharosa nahi (spoof se mask / block bypass)
        payload[name_key] = identity['display_name']
        if avatar_key:
            payload[avatar_key] = None
        return payload

    # 🔥 NAYA — Doubt Queue realtime updates. `DoubtQuestionViewSet` (views.py,
    # `_broadcast` helper) REST se hi create/upvote/answer/reveal karke seedha
    # `chat_{conversation_id}` room me `type: 'doubt_broadcast'` group_send
    # karta hai (`pin_event`/`study_room_broadcast` jaisa hi pattern — client
    # WS se doubt bhejta nahi, sirf REST call ke result ka realtime echo sunta
    # hai, taaki group ke baaki sab connected members ka "Doubts" tab bina
    # refresh kiye turant update ho jaaye).
    async def doubt_broadcast(self, event):
        await self.send(text_data=json.dumps({
            'type': 'doubt_event',
            'event': event.get('event'),  # 'doubt_created' | 'doubt_upvoted' | 'doubt_answered' | 'doubt_revealed'
            'doubt': event.get('doubt'),
            'group_id': event.get('group_id'),
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

    # 🔥 NAYA — is 1-1 conversation ke doosre participant ke saath sender
    # (`user_id`) ka koi bhi direction ka block hai ya nahi, ye check karta
    # hai. Group conversation ho to hamesha `False` (block sirf 1-1 me
    # applicable hai).
    @database_sync_to_async
    def is_blocked_in_conversation(self, conversation_id, user_id):
        from .models import Conversation

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if conversation is None or conversation.type == ConversationType.GROUP:
            return False

        other_id = ConversationParticipant.objects.filter(
            conversation_id=conversation_id, left_at__isnull=True
        ).exclude(user_id=user_id).values_list('user_id', flat=True).first()
        if other_id is None:
            return False

        return BlockedUser.objects.filter(
            Q(blocker_id=user_id, blocked_id=other_id) |
            Q(blocker_id=other_id, blocked_id=user_id)
        ).exists()

    # 🔥 NAYA — REST `messages` action jaisa hi group access-control
    # (message_permission + daily_message_limit) yahan bhi enforce karo.
    # Pehle WS path se ye settings bilkul bypass ho jaati thi kyunki
    # backend me poori feature hi missing thi.
    @database_sync_to_async
    def check_group_message_rules(self, conversation_id, user):
        from .models import Conversation
        from .group_rules import check_group_permission, check_daily_message_limit

        conversation = Conversation.objects.filter(id=conversation_id).first()
        if conversation is None or conversation.type != ConversationType.GROUP:
            return True, "", ""

        group = getattr(conversation, 'group_detail', None)
        if not group:
            return True, "", ""

        # M9a — admin-only check sabse pehle (fresh DB read, har message pe)
        from .admin_only import check_admin_only_send
        allowed, reason, code = check_admin_only_send(group, user.id)
        if not allowed:
            return False, reason, code

        allowed, reason = check_group_permission(group, user.id, 'message_permission')
        if not allowed:
            return False, reason, ""

        allowed, reason = check_daily_message_limit(group, user, conversation)
        return allowed, reason, ""

    @database_sync_to_async
    def save_message(self, conversation_id, sender_id, text, message_type, client_id, reply_to_id,
                      file_url=None, file_urls=None, thumbnail_url=None, meta=None):
        from django.db import IntegrityError
        from .models import Conversation

        # 🔥 BUG FIX — disappearing-messages parity with REST. `Conversation
        # Viewset.messages` (views.py) snapshots `expires_at` from the
        # conversation's current `disappearing_messages_duration` at
        # send-time; this WS path created the `Message` row with no
        # `expires_at` at all (field stayed NULL — "never expires") no
        # matter what the conversation's setting was. A message sent over
        # WebSocket (the normal text-message path) in a disappearing-
        # messages-enabled chat silently never disappeared, while the same
        # message sent via REST would. Fetching the conversation up front
        # (was previously only fetched later, for @mentions) so the same
        # snapshot logic can run before create().
        conversation = Conversation.objects.get(id=conversation_id)
        disappearing_delta = conversation.get_disappearing_timedelta()
        expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

        # 🔥 FIX (Feature 11 — Announcements, same gap as REST
        # `ConversationViewSet.messages`): WS ye value kabhi compute hi nahi
        # karta tha, isliye WS se bheja gaya group-admin/mod ka message bhi
        # hamesha `is_announcement=False` save hota tha — push-side
        # (`push_utils.py`) already sahi handle karta hai, bas ye path use
        # kabhi bhejta hi nahi tha.
        is_announcement = False
        if conversation.type == ConversationType.GROUP:
            group = getattr(conversation, 'group_detail', None)
            if group:
                from .group_rules import is_group_admin_or_mod
                is_announcement = is_group_admin_or_mod(group, sender_id)

        try:
            message = Message.objects.create(
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                type=message_type,
                client_id=client_id,
                reply_to_id=reply_to_id,
                # 🔥 FIX — pehle ye 4 fields yahan set hi nahi hote the,
                # isliye WS se bheja gaya media message DB me file ke
                # bina save hota tha (see handle_new_message comment).
                file_url=file_url or None,
                file_urls=file_urls or [],
                thumbnail_url=thumbnail_url or None,
                meta=meta or {},
                expires_at=expires_at,
                is_announcement=is_announcement,
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

        # 🔥 NAYA — @mentions resolve karke message pe attach karo (REST
        # `ConversationViewSet.messages` POST jaisa hi logic — dono jagah
        # `mentions.extract_mentioned_user_ids` hi use karte hain).
        mentioned_ids = extract_mentioned_user_ids(text, conversation, sender_id=sender_id)
        mentioned_ids = [uid for uid in mentioned_ids if str(uid) != str(sender_id)]
        if mentioned_ids:
            message.mentioned_users.set(mentioned_ids)

        # 🔥 BUG FIX — pehle sirf REST path (`ConversationViewSet.messages`)
        # `create_group_media_for_message` call karta tha; WS se bheja gaya
        # media message kabhi group gallery (`GroupMedia`) me nahi jaata
        # tha, matlab jo bhi media WS se bheja jaaye wo `/groups/<id>/media/`
        # me kabhi dikhta hi nahi. Ab dono paths consistent hain.
        create_group_media_for_message(message)

        return {
            'id': message.id, 'created_at': message.created_at.isoformat(),
            'mentioned_ids': mentioned_ids, 'is_announcement': is_announcement,
        }

    @database_sync_to_async
    def mark_undelivered_as_delivered(self, conversation_id, user_id):
        from common.db_retry import retry_on_db_lock  # T0.1 — transient lock pe 1 retry

        retry_on_db_lock(
            lambda: MessageStatus.objects.filter(
                message__conversation_id=conversation_id, user_id=user_id, is_delivered=False
            ).update(is_delivered=True, delivered_at=timezone.now())
        )

    # ---------------- MISSED-EVENT SYNC (reconnect) ----------------
    async def send_missed_events_sync(self, conversation_id, user_id):
        missed = await self.get_missed_messages(conversation_id, user_id)
        if not missed:
            return
        await self.send(text_data=json.dumps({
            'type': 'sync',
            'event': 'sync',
            'conversation_id': str(conversation_id),
            'count': len(missed),
            'messages': missed,
        }))

    @database_sync_to_async
    def get_missed_messages(self, conversation_id, user_id):
        """
        `last_read_message` ke baad (ya, agar user ne is chat me kabhi
        kuch read hi nahi kiya, `joined_at` ke baad) ke saare naye
        messages — jo is connect se pehle offline/disconnected rehte hue
        miss ho gaye the. Bounded (200) taaki bahut lamba gap (din/hafton
        baad wapas aana) ek hi bhaari query na ban jaaye — us case me
        client apna normal paginated REST history-fetch use kare; ye sirf
        chhote reconnect-gap ki UX-smoothing ke liye hai. Apne hi bheje
        hue messages exclude kiye hain (client ko wo already, apne
        optimistic-send se, pata hote hain).
        """
        try:
            participant = ConversationParticipant.objects.select_related('last_read_message').get(
                conversation_id=conversation_id, user_id=user_id, left_at__isnull=True,
            )
        except ConversationParticipant.DoesNotExist:
            return []

        baseline = (
            participant.last_read_message.created_at
            if participant.last_read_message_id else participant.joined_at
        )

        qs = Message.objects.filter(
            conversation_id=conversation_id, created_at__gt=baseline,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=user_id,
        ).exclude(
            is_scheduled=True,
        ).exclude(
            sender_id=user_id,
        ).select_related('sender').order_by('created_at')[:200]

        results = []
        for message in qs:
            sender = build_user_mini(message.sender)
            results.append({
                'id': str(message.id),
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
                'is_edited': message.is_edited,
                'is_forwarded': message.is_forwarded,
                'created_at': message.created_at.isoformat(),
            })
        return results

    # ---------------- INBOX BROADCAST (global list update) ----------------
    async def broadcast_inbox_update(self, message, sender, text, message_type):
        participant_ids = await self._other_participant_ids(self.conversation_id, self.user.id)
        payload = {
            'type': 'inbox_update',
            'conversation_id': str(self.conversation_id),
            'message_id': str(message['id']),
            'sender_id': str(self.user.id),
            'sender_name': sender['display_name'],
            'last_message_text': text,
            'last_message_type': message_type,
            'created_at': message['created_at'],
        }
        # 🔥 NAYA (M1-BE) — accepted -> `inbox_update`, pending ->
        # `message_request`, declined -> kuch nahi.
        events = await database_sync_to_async(message_requests.inbox_events_for)(
            self.conversation_id, participant_ids, payload,
        )
        for uid, event in events:
            await self.channel_layer.group_send(f'user_{uid}', event)

    # ---------------- PUSH NOTIFICATION ----------------
    @database_sync_to_async
    def _other_participant_ids(self, conversation_id, sender_id):
        return list(
            ConversationParticipant.objects.filter(conversation_id=conversation_id)
            .exclude(user_id=sender_id)
            .values_list('user_id', flat=True)
        )

    async def send_push_for_message(self, message, text, message_type, exclude_ids=None, is_announcement=False):
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
        exclude_set = {str(uid) for uid in (exclude_ids or [])}
        recipient_ids = [uid for uid in recipient_ids if str(uid) not in exclude_set]
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
            is_announcement=is_announcement,
        )

    async def send_mention_push_notification(self, message, text, is_announcement=False):
        from .push_utils import send_mention_push

        sender_name = get_display_name(self.user)
        await database_sync_to_async(send_mention_push)(
            message['mentioned_ids'],
            sender_name,
            text,
            self.conversation_id,
            message['id'],
            is_announcement=is_announcement,
        )

    @database_sync_to_async
    def mark_message_read(self, message_id, user_id):
        # 🐛 BUG FIX — pehle yahan `unread_count=0` seedha set ho jaata tha
        # sirf EK message read-mark hone par bhi. REST wala equivalent
        # (`MessageViewSet.mark_read`, views.py) sahi tarike se sirf 1 se
        # decrement karta hai — matlab agar chat me 5 unread messages the
        # aur user ne sirf 1 khola (WS `read` event isi ek message ke liye
        # trigger hota hai), badge turant "0" dikhata tha jabki asal me 4
        # aur unread the. Ab dono paths consistent hain: sirf tab hi
        # decrement karo jab wo message pehli baar is user ke liye
        # read-mark ho raha ho (already-read message dobara mark karne se
        # count dobara na ghate).
        updated = MessageStatus.objects.filter(
            message_id=message_id, user_id=user_id, is_read=False,
        ).update(is_read=True, is_delivered=True, read_at=timezone.now())

        ConversationParticipant.objects.filter(
            conversation_id=self.conversation_id, user_id=user_id,
        ).update(last_read_at=timezone.now())

        if updated:
            ConversationParticipant.objects.filter(
                conversation_id=self.conversation_id, user_id=user_id, unread_count__gt=0,
            ).update(unread_count=models_f_decrement())

        # Chats badge / bell `core.Notification` (source=message) se aata hai —
        # REST `read_all` / `mark_read` ye already sync karte the, WS path
        # nahi karta tha, isliye WS se read hone par bhi badge unread dikhta
        # tha. Is message ke CHAT_MESSAGE/MENTION rows (data.message_id) read
        # mark karo, phir fresh badge count inbox socket pe publish karo.
        # Best-effort: yahan ki koi bhi galti read-receipt ko na roke.
        try:
            from core.models import Notification
            from core.services import publish_unread_badge

            Notification.objects.filter(
                recipient_id=user_id,
                is_read=False,
                notif_type__in=[
                    Notification.NotifType.CHAT_MESSAGE,
                    Notification.NotifType.MENTION,
                ],
                data__message_id=str(message_id),
            ).update(is_read=True, read_at=timezone.now())
            publish_unread_badge(user_id)
        except Exception:
            logger.exception('WS read: failed to sync core.Notification for message %s', message_id)

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
            # 🔥 BUG FIX — parity with `MessageViewSet.destroy` (views.py).
            # This WS path only ever cleared `text`, leaving `file_url`,
            # `file_urls` (multi-image), and `thumbnail_url` fully intact
            # in the DB — a message "deleted for everyone" over WebSocket
            # (the default in-chat delete path) still exposed the original
            # file to every participant via those fields. Now clears the
            # same set REST does.
            message.file_url = None
            message.file_urls = []
            message.thumbnail_url = None
            message.save(update_fields=[
                'deleted_for_everyone', 'text', 'file_url', 'file_urls', 'thumbnail_url',
            ])
            # 🔥 BUG FIX — same gallery-cleanup gap as REST `destroy()`:
            # `GroupMedia` keeps its own independent `file_url` copy from
            # send-time, so blanking the `Message` row above never removed
            # it from the group's shared media gallery. Without this, the
            # WS delete path (the default in-chat delete) left every
            # deleted photo/video permanently visible in `/groups/<id>/media/`.
            from .models import GroupMedia
            GroupMedia.objects.filter(message=message).delete()
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
    def pin_or_unpin_message(self, message_id, user_id, pin):
        from .group_rules import is_group_admin_or_mod
        from django.contrib.auth import get_user_model

        try:
            message = Message.objects.select_related('conversation', 'conversation__group_detail').get(
                id=message_id, conversation_id=self.conversation_id,
            )
        except Message.DoesNotExist:
            return False, "Message nahi mila"

        conversation = message.conversation
        if conversation.type == ConversationType.GROUP:
            group = getattr(conversation, 'group_detail', None)
            if group and not is_group_admin_or_mod(group, user_id):
                return False, "Sirf group admin/moderator message pin/unpin kar sakte hain"

        if not pin:
            message.is_pinned = False
            message.pinned_at = None
            message.pinned_by = None
            message.save(update_fields=['is_pinned', 'pinned_at', 'pinned_by'])
            return True, ""

        if message.deleted_for_everyone:
            return False, "Delete kiya hua message pin nahi ho sakta"

        if not message.is_pinned:
            pinned_count = conversation.all_messages.filter(is_pinned=True).count()
            if pinned_count >= MAX_PINNED_PER_CONVERSATION:
                return False, f"Ek chat me max {MAX_PINNED_PER_CONVERSATION} messages hi pin ho sakte hain"

            User = get_user_model()
            message.is_pinned = True
            message.pinned_at = timezone.now()
            message.pinned_by = User.objects.get(id=user_id)
            message.save(update_fields=['is_pinned', 'pinned_at', 'pinned_by'])
        return True, ""

    @database_sync_to_async
    def set_presence(self, online):
        """
        T0.1 — presence ab DB-write-free hai (cache/Redis counter).

        Pehle: har connect/disconnect `UserPresence` row pe `select_for_update` +
        `save()`. Daphne ke kai threads ek saath (presence + heartbeat + events +
        device-token) likhte the to SQLite ek-writer limit se "database is locked"
        aata tha — connect() crash, 500s, 8.5s POSTs.

        Ab: `cache_utils.presence_connect/presence_disconnect` multi-device
        connection counter ko atomic `cache.incr/decr` se chalate hain (online <=>
        counter > 0). DB me `last_seen_at` sirf last disconnect par, aur wo bhi
        `persist_last_seen` (60s throttle) se — alag step, taaki DB slow ho to
        presence/broadcast na ruke.

        Returns: (is_online, last_seen_at) — pehle jaisa hi contract.
        """
        if online:
            is_online, last_seen_at = presence_connect(self.user.id)
        else:
            is_online, last_seen_at = presence_disconnect(self.user.id)
        # `UserPresenceView` ka 15s snapshot purana ho gaya — hata do (reader ab
        # counter + last_seen cache se seedha padhta hai).
        invalidate_presence_cache(self.user.id)
        return is_online, last_seen_at

    @database_sync_to_async
    def persist_last_seen(self, last_seen_at):
        """Throttled (60s/user) DB `last_seen_at` write; kabhi raise nahi karta."""
        return persist_last_seen_throttled(self.user.id, last_seen_at)

    @database_sync_to_async
    def touch_presence(self):
        presence_touch(self.user.id)

    @database_sync_to_async
    def get_restricted_ids(self, user_id):
        from user_profile.services import restricted_ids_by
        return restricted_ids_by(user_id)

    @database_sync_to_async
    def get_direct_partner_ids(self, user_id):
        """
        🔥 NAYA — is user ke jitne bhi ACTIVE 1-1 (direct) conversations
        hain, un sabke doosre participant ka id (distinct). Group
        conversations jaan-bujhke exclude kiye hain — ek group me
        potentially sainkdon members ho sakte hain, har member ke
        online/offline hone par sabko push karna bahut noisy/costly ho
        jaata; real chat apps bhi generally sirf 1-1 contacts ka hi live
        presence dikhati hain, group members ka nahi.
        """
        # NOTE: "direct" yahan `!= ConversationType.GROUP` se check kiya
        # hai, `== ConversationType.DIRECT` se nahi — models.py is upload
        # ka hissa nahi tha, isliye us doosre enum member ka exact naam
        # yahan se confirm nahi kar sakta. Baaki poori codebase (views.py/
        # consumers.py) me bhi har jagah isi "not GROUP" pattern se hi
        # direct-vs-group check hota hai (`is_blocked_in_conversation`,
        # `check_group_message_rules`, waghera) — wahi safe, already-
        # verified pattern yahan bhi use kiya hai.
        direct_conversation_ids = ConversationParticipant.objects.filter(
            user_id=user_id,
            left_at__isnull=True,
            # 🔥 NAYA (M1-BE) — pending/declined request ke sender ko meri
            # online/last-seen presence push nahi hoti.
            request_status=RequestStatus.ACCEPTED,
        ).exclude(
            conversation__type=ConversationType.GROUP,
        ).values_list('conversation_id', flat=True)

        return list(
            ConversationParticipant.objects.filter(
                conversation_id__in=direct_conversation_ids, left_at__isnull=True,
            )
            .exclude(user_id=user_id)
            .values_list('user_id', flat=True)
            .distinct()
        )

    async def broadcast_presence_to_partners(self, is_online, last_seen_at=None):
        """
        🔥 NAYA — ab tak sirf isi conversation ke room-group ko
        `presence_update` milta tha, matlab sirf jo log ABHI wahi chat
        khole baithe the unhe hi turant pata chalta tha. Contact-list /
        chat-list jaisi screens jo koi specific conversation room join hi
        nahi karti, unke liye ab tak sirf `UserPresenceView` (REST,
        `cache_utils.PRESENCE_TTL` = 15s) hi option tha — agar wahan koi
        periodic poll bhi lagi ho, to "abhi-abhi online hua" dikhne me
        poore TTL jitni der lag sakti thi.

        Ye har direct chat-partner ke apne personal `user_<id>` inbox
        group (`InboxConsumer` — already notifications ke liye use ho
        raha hai) pe seedha push kar deta hai, taaki wo screens bhi turant
        update ho sakein, REST poll/cache-TTL ka wait kiye bina.
        """
        partner_ids = await self.get_direct_partner_ids(self.user.id)
        # Issue #2 (RestrictUser): don't push my presence to people I restricted.
        restricted_by_me = await self.get_restricted_ids(self.user.id)
        if restricted_by_me:
            partner_ids = [p for p in partner_ids if p not in restricted_by_me]
        if not partner_ids:
            return
        payload = {
            'type': 'presence_update',
            'user_id': str(self.user.id),
            'is_online': is_online,
            'last_seen_at': last_seen_at.isoformat() if last_seen_at else None,
        }
        for partner_id in partner_ids:
            await self.channel_layer.group_send(f'user_{partner_id}', payload)


@database_sync_to_async
def _presence_hidden_by_privacy(actor_user_id, viewer_id):
    """S4: True when `actor` limited "who can see my online status" and `viewer` is outside that circle. The event's
    owner is always allowed to get their own multi-device echo. Settings are cached per user (user_profile/privacy.py).
    A lookup that fails shows the presence (the old behaviour) rather than dropping chat events."""
    try:
        from user_profile import privacy as privacy_rules

        actor_id = privacy_rules.as_user_id(actor_user_id)
        if actor_id is None or actor_id == viewer_id:
            return False
        return not privacy_rules.can_see_online_status(actor_id, viewer_id)
    except Exception:
        logger.exception('presence privacy check failed actor=%s viewer=%s', actor_user_id, viewer_id)
        return False


def models_f_increment():
    from django.db.models import F
    return F('unread_count') + 1


def models_f_decrement():
    from django.db.models import F
    return F('unread_count') - 1


# ======================================================================
# INBOX CONSUMER — global, app-wide (🔥 NAYA)
# ------------------------------------------------------------
# /ws/inbox/?token=<jwt>
#
# `ChatConsumer` se ALAG hai: koi `conversation_id` URL me nahi chahiye,
# koi membership check nahi — bas user authenticated hona chahiye. App
# login hote hi (ya app start hote hi) EK BAAR connect ho jaata hai aur
# poori session me connected rehta hai, chahe user kisi bhi screen pe ho.
#
# Ye sirf apne `user_<user_id>` group me join karta hai. Jab bhi koi
# message kisi conversation me aata hai (chahe REST se ho ya
# `ChatConsumer` ke websocket se), har participant ke isi group ko ek
# halka `inbox_update` event bheja jaata hai — Flutter side
# `ConversationsScreen` isko sunke list turant update kar deti hai, bina
# kisi chat ke andar gaye.
#
# Client -> Server: kuch nahi (read-only channel, koi `receive` handling
# nahi chahiye).
# Server -> Client: {"type": "inbox_update", "conversation_id": ...,
#                     "message_id": ..., "sender_id": ..., "sender_name": ...,
#                     "last_message_text": ..., "last_message_type": ...,
#                     "created_at": ...}
# ======================================================================
class InboxConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope.get('user')
        if self.user is None or not self.user.is_authenticated:
            await self.close(code=4001)  # unauthorized
            return

        self.group_name = f'user_{self.user.id}'
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        # Shared, payload-free "live stories changed" signal group (post.story_live).
        self.live_group_name = 'live_stories'
        await self.channel_layer.group_add(self.live_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if not hasattr(self, 'group_name'):
            return
        for _group in (self.group_name, getattr(self, 'live_group_name', None)):
            if not _group:
                continue
            try:
                await asyncio.wait_for(
                    self.channel_layer.group_discard(_group, self.channel_name),
                    timeout=3,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "InboxConsumer.disconnect: group_discard timed out user=%s group=%s",
                    getattr(self, 'user', None) and self.user.id, _group,
                )
            except Exception:
                logger.exception("InboxConsumer.disconnect: group_discard failed")

    async def receive(self, text_data=None, bytes_data=None):
        # Read-only channel hai — client se koi event process nahi karna.
        pass

    # ---------------- GROUP EVENT HANDLER (server -> socket) ----------------
    async def inbox_update(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA — mutual-follow se auto-create hua empty conversation
    # (`message.services.get_or_create_conversation`, `user_profile/
    # signals.py` se call hota hai). Koi real `Message` nahi hai isliye
    # `inbox_update` (jo ek message ke around bana hai — sender/text/etc)
    # reuse nahi kiya; ye khud ka halka event hai, client bas apni
    # conversations list ko refresh/prepend kar le.
    # Server -> Client: {"type": "conversation_created",
    #                     "conversation_id": ..., "conversation_type": ...,
    #                     "created_at": ...}
    async def conversation_created(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA (M1-BE) — anjaan ka pehla DM: `inbox_update` ki jagah ye event
    # aata hai (chat list me NAHI, Requests tab/badge me dikhana hai).
    # Server -> Client: {"type": "message_request", "conversation_id": ...,
    #   "message_id": ..., "sender_id": ..., "sender_name": ...,
    #   "last_message_text": ..., "last_message_type": ..., "created_at": ...}
    async def message_request(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA (M1-BE) — accept/decline hone par SIRF receiver ke apne devices
    # ko (kabhi sender ko nahi) taaki doosre device ka Requests list/badge
    # sync ho. Accept pe saath me purana `conversation_created` bhi aata hai.
    # Server -> Client: {"type": "message_request_resolved",
    #   "conversation_id": ..., "status": "accepted" | "declined"}
    async def message_request_resolved(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA — story reaction (post.views.StoryReactAPIView group_sends
    # this straight to the story owner's `user_<id>` inbox group — same
    # group this consumer already listens on, no new group/consumer
    # needed). Plain passthrough, same shape as `inbox_update`/
    # `conversation_created` above.
    # Server -> Client: {"type": "story_reaction", "story_id": ...,
    #   "user_id": ..., "username": ..., "emoji": ..., "reacted": bool}
    async def story_reaction(self, event):
        await self.send(text_data=json.dumps(event))

    # Live story ring signal (post.story_live.publish_live_stories_changed): a class went LIVE /
    # ended. NO payload on purpose - the client refetches GET /post/stories/live/, which applies
    # per-viewer visibility. Server -> Client: {"type": "live_stories_changed"}
    async def live_stories_changed(self, event):
        await self.send(text_data=json.dumps({"type": "live_stories_changed"}))

    # N10-BE — real-time bell badge. Published by
    # core.services.publish_unread_badge() (via transaction.on_commit)
    # whenever a Notification row is created, read or deleted for this
    # user; goes to the same `user_<id>` group this consumer already joins,
    # so no new socket/group is needed. `unread_count` is the user's total
    # unread (see core.services.unread_badge_count). Plain passthrough.
    # Server -> Client: {"type": "notification_badge", "unread_count": N}
    async def notification_badge(self, event):
        await self.send(text_data=json.dumps(event))

    # Block / unblock with someone (user_profile/block_live.py): chat lists
    # and open profiles refresh. {"type": "block_changed", "other_user_id", "is_blocked"}
    async def block_changed(self, event):
        await self.send(text_data=json.dumps(event))

    # 🔥 NAYA — `ChatConsumer.broadcast_presence_to_partners` ab is user
    # ke saare direct (1-1) chat-partners ke isi inbox group pe bhi
    # presence_update bhejta hai, taaki contact-list/chat-list jaisi
    # screens turant update ho sakein, `UserPresenceView`'s cached REST
    # poll ka wait kiye bina. `ChatConsumer.presence_update`'s exact same
    # shape use kiya hai consistency ke liye — NOTE (pre-existing, is
    # NOTE: `event` dict me khud bhi 'type': 'presence_update' hota hai (Channels
    # dispatch ke liye). Isliye final JSON me asal 'type' set karne ke liye
    # `{**event, 'type': 'presence'}` likha hai (baad wali key jeetti hai) — client
    # `case 'presence'` handle karta hai. (`pin_event` ka 'pin_event' hi jaata hai,
    # aur client bhi wahi match karta hai.)
    @database_sync_to_async
    def _actor_restricted_me(self, actor_user_id):
        # Issue #2 (RestrictUser): drop presence of anyone who restricted me.
        from user_profile.services import has_restricted
        try:
            actor_id = int(actor_user_id)
        except (TypeError, ValueError):
            return False
        if actor_id == self.user.id:
            return False
        return has_restricted(actor_id, self.user.id)

    async def presence_update(self, event):
        if await self._actor_restricted_me(event.get('user_id')):
            return
        # S4 -- the person chose who may see their online status / last seen.
        if await _presence_hidden_by_privacy(event.get('user_id'), self.user.id):
            return
        await self.send(text_data=json.dumps({**event, 'type': 'presence'}))


# ======================================================================
# CALL CONSUMER (1-1 + Group signaling, e.g. WebRTC/Agora/LiveKit)
# ======================================================================
class CallConsumer(AsyncWebsocketConsumer):
    """
    /ws/call/<call_id>/?token=<jwt>

    🔥 C14 — Ye socket ab SIRF call EVENTS ke liye hai (user_joined / user_left /
    mute / video_off + server-side ring/cancel/ended events jo REST / webhook /
    sweeper `call_{id}` group me bhejte hain). SDP/ICE relay HATA diya gaya:
    media negotiation 100% LiveKit (token via REST) se hoti hai, isliye socket pe
    offer/answer/candidate relay dead path + attack surface tha.

    Client -> Server:
        {"type": "mute", "is_muted": true}
        {"type": "video_off", "is_video_off": true}
        {"type": "leave"}
        (purana {"type": "signal"} => error `signal_not_supported`, relay nahi hota)

    Connect rule (C14): sirf wo user jiski is call me `CallParticipant` row hai
    (caller / invited callee / REST `join` se bana participant). Group ka
    koi bhi member bina invite/join ke socket connect nahi kar sakta.
    """

    # Ek connection par events ki limit (mute/video_off spam / junk flood roko).
    RATE_LIMIT_MAX = 20          # itne messages ...
    RATE_LIMIT_WINDOW = 10.0     # ... itne seconds me
    MAX_FRAME_BYTES = 2048       # control events chhote hote hain; bada frame = junk

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
        self._rate_window_start = 0.0
        self._rate_count = 0
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        is_in_call = await self.mark_participant_joined(self.call_id, self.user.id)
        if not is_in_call:
            # C14 — RINGING (abhi accept nahi kiya) / left participant sirf events
            # SUN sakta hai (ring/cancel/ended). Wo "user_joined" broadcast nahi
            # karta aur peer-joined synthetic event bhi nahi pata.
            return
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

    def _rate_limited(self):
        now = time.monotonic()
        if now - self._rate_window_start > self.RATE_LIMIT_WINDOW:
            self._rate_window_start = now
            self._rate_count = 0
        self._rate_count += 1
        return self._rate_count > self.RATE_LIMIT_MAX

    async def receive(self, text_data=None, bytes_data=None):
        # C14 — binary frame / bada frame / flood: relay ya parse ki zaroorat hi nahi.
        if bytes_data is not None or not text_data or len(text_data) > self.MAX_FRAME_BYTES:
            return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_frame'}))
        if self._rate_limited():
            return await self.close(code=4008)

        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_json'}))
        if not isinstance(data, dict):
            return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_json'}))

        event_type = data.get('type')

        if event_type == 'signal':
            # 🔥 C14 — SDP/ICE relay hata diya (media = LiveKit). Kuch relay / store nahi hota.
            await self.send(text_data=json.dumps({'type': 'error', 'code': 'signal_not_supported'}))
        elif event_type in ('mute', 'video_off'):
            field = 'is_muted' if event_type == 'mute' else 'is_video_off'
            value = data.get(field, True)
            if not isinstance(value, bool):
                return await self.send(text_data=json.dumps({'type': 'error', 'code': 'invalid_value'}))
            # Sirf call me maujood (ONGOING) participant ka state badalta / broadcast hota hai.
            updated = await self.update_participant_flag(self.call_id, self.user.id, event_type, value)
            if not updated:
                return await self.send(text_data=json.dumps({'type': 'error', 'code': 'not_in_call'}))
            # Explicit payload: pehle `**data` spread hota tha => client `event` / `user_id`
            # override (spoof) kar sakta tha aur koi bhi extra key sabko jaati thi.
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'call_signal',
                    'sender_channel_name': self.channel_name,
                    'data': {'event': event_type, 'user_id': str(self.user.id), field: value},
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
        # 🔥 C1 — sweeper se MISSED/REJECTED hui calls pe bhi socket connect na ho
        return CallSession.objects.filter(id=call_id).exclude(
            status__in=[CallStatus.ENDED, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY,
                        CallStatus.CANCELLED]
        ).first()

    @database_sync_to_async
    def is_call_participant(self, call_id, user_id, call):
        # 🔥 C14 — pehle: group call me har non-banned GroupMember connect kar sakta tha
        # (aur C6 se pehle `mark_participant_joined` unhe participant bhi bana deta tha).
        # Ab sirf wo jiski is call me `CallParticipant` row hai (caller / invited / REST join).
        # REJECTED / MISSED / BUSY rows ko socket nahi (unhe events ki zaroorat nahi);
        # ENDED allowed hai taaki network blip ke baad reconnect ho sake.
        return CallParticipant.objects.filter(
            call_id=call_id, user_id=user_id,
            status__in=[CallStatus.RINGING, CallStatus.ONGOING, CallStatus.ENDED],
        ).exists()

    @database_sync_to_async
    def get_other_ongoing_participant(self, call_id, user_id):
        other = CallParticipant.objects.filter(
            call_id=call_id, status=CallStatus.ONGOING
        ).exclude(user_id=user_id).exclude(left_at__isnull=False).first()
        return str(other.user_id) if other else None

    @database_sync_to_async
    def mark_participant_joined(self, call_id, user_id):
        # 🔥 C6 — socket connect se participant ban/ONGOING NAHI hota. Pehle:
        #  * get_or_create => koi bhi group member bina invite/accept ke participant ban jaata tha
        #  * RINGING/REJECTED/MISSED participant ka socket khulte hi ONGOING ho jaata tha
        # Ab sirf pehle se ONGOING (ya blip se ENDED hue) participant ka reconnect
        # re-affirm hota hai; join sirf REST `accept` se (state machine).
        # C16 — recording chal rahi ho to minor ka socket reconnect use ENDED -> ONGOING nahi karega
        # (recording me minor nahi; REST accept/join bhi wahi 409 deta hai). Socket khula rehta hai, bas "in call" nahi.
        if CallSession.objects.filter(id=call_id, is_recording=True).exists():
            from django.contrib.auth import get_user_model
            from . import call_recording_consent as _consent
            _u = get_user_model().objects.filter(pk=user_id).only('id', 'date_of_birth').first()
            if _u is not None and _consent.is_minor_user(_u):
                return False
        # 🔥 C14 leftover — ENDED participant ka socket reconnect pehle KABHI bhi use wapas ONGOING kar deta tha (REST
        # "leave" ke ghanton baad bhi). Ab sirf taaza blip: ENDED + `left_at` pichhle CALL_WS_REJOIN_WINDOW_SECONDS
        # (default 60s) me. Purana ENDED participant wapas judna chahe to REST accept/join se (token ke saath) hi.
        from datetime import timedelta as _td
        from django.conf import settings as _settings
        _window = max(int(getattr(_settings, 'CALL_WS_REJOIN_WINDOW_SECONDS', 60)), 0)
        _cutoff = timezone.now() - _td(seconds=_window)
        rejoined = CallParticipant.objects.filter(
            call_id=call_id, user_id=user_id,
            call__status__in=[CallStatus.INITIATED, CallStatus.RINGING, CallStatus.ONGOING],
        ).filter(
            Q(status=CallStatus.ONGOING) | Q(status=CallStatus.ENDED, left_at__gte=_cutoff)
        ).update(status=CallStatus.ONGOING, left_at=None)
        CallSession.objects.filter(id=call_id, status=CallStatus.INITIATED).update(
            status=CallStatus.ONGOING, connected_at=timezone.now()
        )
        # C14 — True sirf jab ye user ab call me (ONGOING) hai; RINGING => False.
        return bool(rejoined)

    @database_sync_to_async
    def mark_participant_left(self, call_id, user_id):
        # 🔥 C1 FIX — pehle `still_active` me `left_at IS NULL` wale SAB rows
        # gine jaate the, jisme RINGING (kabhi na uthane wale) bhi aate the,
        # isliye socket se call kabhi `ended` nahi hoti thi. Ab sirf ONGOING
        # (asli me call me maujood) participants gine jaate hain.
        now = timezone.now()
        # C6 — sirf ONGOING leg ENDED hoti hai; RINGING participant ka socket band
        # hone se uska MISSED/missed-call history nahi bigadna chahiye, REJECTED/MISSED
        # row overwrite nahi honi chahiye.
        CallParticipant.objects.filter(
            call_id=call_id, user_id=user_id, status=CallStatus.ONGOING, left_at__isnull=True,
        ).update(left_at=now, status=CallStatus.ENDED)
        call = CallSession.objects.filter(id=call_id).first()
        if call is None or call.status in (
            CallStatus.ENDED, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY,
            CallStatus.CANCELLED,
        ):
            return call is not None

        # Abhi tak kisi ne uthayi hi nahi (ringing) — caller ka socket blip
        # (network switch) call khatam NAHI karna chahiye. Ye call
        # `sweep_stale_calls` (ring timeout) ya explicit `end` action se
        # band hogi.
        if not call.connected_at:
            return False

        still_active = CallParticipant.objects.filter(
            call_id=call_id, status=CallStatus.ONGOING, left_at__isnull=True
        ).exists()
        if still_active:
            return False

        ringing_ids = list(
            CallParticipant.objects.filter(call_id=call_id, status=CallStatus.RINGING)
            .values_list('user_id', flat=True)
        )
        CallParticipant.objects.filter(call_id=call_id, status=CallStatus.RINGING).update(
            status=CallStatus.MISSED, left_at=now
        )
        duration = int((now - call.connected_at).total_seconds())
        CallSession.objects.filter(id=call_id).update(
            status=CallStatus.ENDED, ended_at=now, duration_seconds=max(duration, 0),
            is_recording=False,
        )
        if ringing_ids:
            try:
                from .push_utils import send_call_cancelled_push
                send_call_cancelled_push(
                    recipient_ids=ringing_ids, call_id=call_id,
                    conversation_id=call.conversation_id,
                )
            except Exception:
                logger.warning("mark_participant_left: cancel push failed call=%s", call_id, exc_info=True)
            # C12 — bache RINGING (ab MISSED) ko "Missed call" push + bell row.
            try:
                from .tasks import enqueue_missed_call
                enqueue_missed_call(call_id, ringing_ids)
            except Exception:
                logger.warning("mark_participant_left: missed-call enqueue failed call=%s", call_id, exc_info=True)
        return True

    @database_sync_to_async
    def update_participant_flag(self, call_id, user_id, event_type, value):
        # C14 — sirf ONGOING participant (RINGING / left user ka flag nahi badalta).
        field = 'is_muted' if event_type == 'mute' else 'is_video_off'
        return CallParticipant.objects.filter(
            call_id=call_id, user_id=user_id, status=CallStatus.ONGOING, left_at__isnull=True,
        ).update(**{field: bool(value)}) > 0
