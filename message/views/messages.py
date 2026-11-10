"""
message/views/messages.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .. import message_requests
from ..admin_only import is_admins_only
from ..constants import MAX_PINNED_PER_CONVERSATION
from ..group_rules import is_group_admin_or_mod
from ..models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    GroupMedia,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    Poll,
    PollOption,
    PollVote,
)
from ..permissions import IsConversationParticipant, IsMessageSender
from ..push_utils import send_chat_message_push
from ..serializers import (
    MessageReactionSerializer,
    MessageReadStatusSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    PollSerializer,
    PollVoteSerializer,
)
from ..throttles import ReactionThrottle, TranslateThrottle
from ..translation_service import (
    SUPPORTED_LANGUAGES,
    TranslationError,
    TranslationServiceUnavailable,
    UnsupportedLanguageError,
    translate_text,
)
from ..user_display import build_user_mini

from ._common import MessagePagination, with_message_list_prefetch

logger = logging.getLogger(__name__)


# ======================================================================
# MESSAGES
# ======================================================================
class MessageViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    queryset = Message.objects.select_related('conversation', 'sender')
    serializer_class = MessageSerializer

    def get_serializer_context(self):
        return {'request': self.request}

    def get_permissions(self):
        # `manage_schedule` sender-only hai (cancel/reschedule apne hi
        # "send later" message ka), isliye `update`/`destroy` jaisa hi
        # `IsMessageSender` bhi laga dete hain.
        if self.action in ('update', 'partial_update', 'destroy', 'manage_schedule'):
            return [IsAuthenticated(), IsConversationParticipant(), IsMessageSender()]
        # 'forward' and 'starred' are list-level actions (no single
        # pk/object) — 'forward' checks conversation membership itself
        # for every source message and every target conversation;
        # 'starred' filters by the requester's own active conversations
        # inside the query itself. Both only need authentication here.
        if self.action in ('forward', 'starred'):
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsConversationParticipant()]

    # 🔥 FIX — reaction-spam guard (`ReactionThrottle`) existed in
    # throttles.py but was never wired in; rapid emoji toggling on the
    # `react` action was unbounded on the REST side.
    def get_throttles(self):
        if self.action == 'react':
            return [ReactionThrottle()]
        if self.action == 'translate':
            return [TranslateThrottle()]
        return super().get_throttles()

    def get_object(self):
        # 🔧 GAP FIX (part of Scheduled Messages) — a scheduled-but-not-
        # yet-sent message must never be reachable through the *normal*
        # single-message actions (react/read/pin/edit/delete/etc.) by
        # anyone, including the sender — it hasn't been "sent" yet, so
        # none of those actions make sense on it. Only `manage_schedule`
        # (cancel/reschedule) may touch it, and only the sender.
        obj = super().get_object()
        if obj.is_scheduled and self.action != 'manage_schedule':
            if obj.sender_id != self.request.user.id:
                raise Http404
            raise PermissionDenied("Ye message abhi schedule hai, bheja nahi gaya — pehle schedule cancel/send karo.")
        return obj

    def partial_update(self, request, *args, **kwargs):
        message = self.get_object()
        if message.type != MessageType.TEXT:
            return Response({'detail': 'Sirf text message edit ho sakta hai.'}, status=status.HTTP_400_BAD_REQUEST)
        if message.deleted_for_everyone:
            return Response({'detail': 'Delete kiya hua message edit nahi ho sakta.'}, status=status.HTTP_400_BAD_REQUEST)

        text = (request.data.get('text') or '').strip()
        if not text:
            return Response({'detail': "'text' khali nahi ho sakta."}, status=status.HTTP_400_BAD_REQUEST)

        message.text = text
        message.is_edited = True
        message.save(update_fields=['text', 'is_edited', 'updated_at'])

        # 🔥 NAYA — REST edit ke liye WS broadcast, taaki ye action live
        # dikhe (see `consumers.py`'s `edit_event` handler docstring — is
        # se pehle koi WS event hi is app me exist nahi karta tha edit ke
        # liye, chahe REST se ho ya WS se).
        async_to_sync(get_channel_layer().group_send)(
            f'chat_{message.conversation_id}',
            {
                'type': 'edit_event',
                'message_id': str(message.id),
                'text': message.text,
                'is_edited': True,
                'edited_by': str(request.user.id),
                'updated_at': message.updated_at.isoformat(),
            },
        )

        return Response(MessageSerializer(message, context={'request': request}).data)

    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        for_everyone = str(request.query_params.get('for_everyone', 'false')).lower() == 'true'

        if for_everyone:
            if message.sender_id != request.user.id:
                return Response({'detail': 'Sirf sender hi sabke liye delete kar sakta hai.'}, status=status.HTTP_403_FORBIDDEN)
            message.deleted_for_everyone = True
            message.text = ''
            # 🔥 BUG FIX — sirf `file_url` clear ho raha tha. Multi-image
            # messages ka asli data `file_urls` (JSON list) me hota hai
            # (`file_url` unke liye khaali/None hi rehta hai — models.py ka
            # comment dekho), aur video/pdf ka `thumbnail_url` bhi alag
            # field hai. In dono ko clear na karne se "delete for everyone"
            # ke baad bhi wo files serializer response me poori tarah
            # accessible reh jaati thin — delete ka poora point hi defeat
            # ho jaata (khaaskar multi-image posts ke liye, jahan file_url
            # hamesha khaali hota hai to sirf ise clear karna kuch bhi
            # nahi chhupata).
            message.file_url = None
            message.file_urls = []
            message.thumbnail_url = None
            message.save(update_fields=[
                'deleted_for_everyone', 'text', 'file_url', 'file_urls', 'thumbnail_url',
            ])
            # 🔥 BUG FIX — `GroupMedia` (group gallery, `/groups/<id>/media/`)
            # stores its OWN copy of `file_url` at creation time
            # (`media_utils.create_group_media_for_message`), separate from
            # the `Message` row — it's a `OneToOne` with `on_delete=CASCADE`,
            # which only cascades if the `Message` itself is hard-deleted.
            # A "delete for everyone" never deletes the `Message` row (it
            # just blanks the fields above), so nothing here ever cleaned up
            # the gallery copy — a photo/video deleted "for everyone" stayed
            # permanently visible and downloadable in the group's shared
            # media gallery, defeating the delete entirely for anyone
            # browsing that tab instead of the chat itself.
            GroupMedia.objects.filter(message=message).delete()

            # 🔥 NAYA — WS broadcast, sirf `for_everyone=True` ke liye
            # (`consumers.py`'s `handle_delete_message` bug-fix comment
            # dekho — "delete for me" KABHI room-wide broadcast nahi hona
            # chahiye, sirf deleter ke liye private hai). REST path pehle
            # yahan koi bhi broadcast nahi karta tha — matlab REST se
            # "delete for everyone" karne par doosron ki khuli chat screen
            # se message live gayab nahi hota tha, refresh tak wahi rehta.
            async_to_sync(get_channel_layer().group_send)(
                f'chat_{message.conversation_id}',
                {
                    'type': 'delete_event',
                    'message_id': str(message.id),
                    'for_everyone': True,
                    'deleted_by': str(request.user.id),
                },
            )
        else:
            message.deleted_for_users.add(request.user)
            # "Delete for me" jaan-boojh kar broadcast NAHI hota — sirf
            # deleter ke liye private hai, dusre participants ko kabhi
            # pata nahi chalna chahiye.

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post', 'delete'], url_path='react')
    def react(self, request, pk=None):
        message = self.get_object()

        if request.method == 'DELETE':
            MessageReaction.objects.filter(message=message, user=request.user).delete()
            # 🔥 NAYA — reaction-removed bhi broadcast karo (WS-native path
            # sirf add karta hai, REST me add aur remove dono ka event
            # missing tha). `emoji: None` client ko batata hai ki ye ek
            # removal event hai, add nahi.
            async_to_sync(get_channel_layer().group_send)(
                f'chat_{message.conversation_id}',
                {
                    'type': 'reaction_event',
                    'message_id': str(message.id),
                    'user_id': str(request.user.id),
                    'emoji': None,
                },
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        emoji = request.data.get('emoji')
        if not emoji:
            return Response({'detail': "'emoji' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        reaction, _ = MessageReaction.objects.update_or_create(
            message=message, user=request.user, defaults={'emoji': emoji},
        )

        # 🔥 NAYA — REST se react karne par pehle koi WS broadcast nahi
        # hota tha (sirf WS-native `handle_reaction` broadcast karta tha)
        # — matlab REST se bheja gaya reaction doosron ki khuli chat
        # screen pe live nahi dikhta tha, refresh tak nahi.
        async_to_sync(get_channel_layer().group_send)(
            f'chat_{message.conversation_id}',
            {
                'type': 'reaction_event',
                'message_id': str(message.id),
                'user_id': str(request.user.id),
                'emoji': emoji,
            },
        )

        return Response(MessageReactionSerializer(reaction).data)

    # ==================================================================
    # 🔥 NAYA — Feature 9: real-time message translate
    # ------------------------------------------------------------
    # Deliberately per-message, on-demand (not auto-translate-everything —
    # that would burn API quota on messages nobody ever asks to read in
    # another language). Same `IsConversationParticipant` gate as every
    # other single-message action (default branch of `get_permissions`
    # above already covers `translate` since it isn't in any of the
    # explicit lists) — you can only translate a message you could
    # already read.
    #
    # Cache key includes `message.updated_at` so an edited message
    # (`partial_update` above) automatically gets a fresh cache key
    # instead of serving a stale translation of the old text — no
    # separate invalidation step needed.
    # ==================================================================
    @action(detail=True, methods=['post'], url_path='translate')
    def translate(self, request, pk=None):
        message = self.get_object()

        if message.type != MessageType.TEXT or not (message.text or '').strip():
            return Response(
                {'detail': 'Sirf text message translate ho sakta hai.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if message.deleted_for_everyone or message.deleted_for_users.filter(id=request.user.id).exists():
            raise Http404

        target_lang = (request.data.get('target_lang') or '').strip().lower()
        if not target_lang:
            return Response(
                {'detail': "'target_lang' required hai (e.g. 'hi', 'en', 'ta')."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # TASK 29 — reject an unsupported code here, before even hitting
        # the cache, so a bad language never produces a cached "success"
        # entry. `translate_text` re-checks this too (it's the shared,
        # reusable enforcement point for every caller of that function),
        # but checking it here as well lets us return 400 with the full
        # supported-language list without having to parse that back out
        # of the exception message.
        if target_lang not in SUPPORTED_LANGUAGES:
            return Response(
                {
                    'detail': f"'{target_lang}' abhi supported nahi hai.",
                    'supported_languages': sorted(SUPPORTED_LANGUAGES),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache_key = f"translate:{message.id}:{int(message.updated_at.timestamp())}:{target_lang}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({
                'message_id': str(message.id),
                'target_lang': target_lang,
                'source_text': message.text,
                'translated_text': cached,
            })

        try:
            translated = translate_text(message.text, target_lang)
        except UnsupportedLanguageError as e:
            # Defensive — the explicit check above already covers this
            # for the normal `target_lang` path, but `translate_text`
            # also validates `source_lang` (not accepted from the client
            # today, only used if a future caller passes one), so this
            # keeps that path from ever surfacing as a raw 502.
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except TranslationServiceUnavailable as e:
            return Response({'detail': str(e)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except TranslationError as e:
            return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        # Message text is effectively immutable at a given `updated_at`
        # snapshot, so a long TTL is safe — a week is generous headroom
        # without keeping every translated message cached forever.
        cache.set(cache_key, translated, timeout=60 * 60 * 24 * 7)

        return Response({
            'message_id': str(message.id),
            'target_lang': target_lang,
            'source_text': message.text,
            'translated_text': translated,
        })

    # ==================================================================
    # 🔥 NAYA (ADVANCED FEATURE) — STARRED MESSAGES (personal save/bookmark)
    # ==================================================================
    # `Message.starred_by` M2M field already model pe tha lekin kahin bhi
    # expose/wire nahi kiya gaya tha. Pin se ALAG hai: pin group-wide
    # "important message" hai (§ MessageViewSet.pin), star sirf PERSONAL
    # bookmark hai — kisi aur participant ko pata bhi nahi chalta ki
    # tumne kya star kiya hai (WhatsApp ke "Starred Messages" jaisa),
    # isliye koi broadcast/notification yahan zaroori nahi.
    @action(detail=True, methods=['post', 'delete'], url_path='star')
    def star(self, request, pk=None):
        message = self.get_object()
        if request.method == 'DELETE':
            message.starred_by.remove(request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        message.starred_by.add(request.user)
        return Response(MessageSerializer(message, context={'request': request}).data)

    # GET /message/messages/starred/ — sabhi conversations me se apne
    # saare starred messages, jahan abhi bhi active member ho (chat
    # leave/delete kar chuka ho to us conversation ke star results nahi
    # aayenge — global-search wala hi pattern). `conversation_preview`
    # taaki UI dikha sake "ye kis chat me star kiya tha".
    @action(detail=False, methods=['get'], url_path='starred')
    def starred(self, request):
        qs = Message.objects.filter(
            starred_by=request.user,
            conversation__memberships__user=request.user,
            conversation__memberships__left_at__isnull=True,
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).select_related(
            'sender', 'conversation', 'conversation__group_detail',
        ).distinct().order_by('-created_at')
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSearchResultSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # 🔥 NAYA — SCHEDULED MESSAGE MANAGEMENT (cancel / reschedule / edit)
    # ==================================================================
    # DELETE /message/messages/<id>/schedule/ -> cancel (hard-delete theek
    #        hai kyunki ye kabhi kisi ko dikha hi nahi — send hua hi nahi)
    # PATCH  /message/messages/<id>/schedule/ -> text aur/ya scheduled_for
    #        badlo, jab tak `send_scheduled_messages` isko pick na kare
    @action(detail=True, methods=['patch', 'delete'], url_path='schedule')
    def manage_schedule(self, request, pk=None):
        message = self.get_object()
        if not message.is_scheduled:
            return Response({'detail': 'Ye message scheduled nahi hai (ya already bhej diya gaya).'},
                             status=status.HTTP_400_BAD_REQUEST)

        if request.method == 'DELETE':
            message.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        update_fields = []
        if 'text' in request.data:
            text = (request.data.get('text') or '').strip()
            if not text:
                return Response({'detail': "'text' khali nahi ho sakta."}, status=status.HTTP_400_BAD_REQUEST)
            message.text = text
            update_fields.append('text')

        if 'scheduled_for' in request.data:
            parsed = parse_datetime(request.data.get('scheduled_for') or '')
            if parsed is None or parsed <= timezone.now():
                return Response(
                    {'detail': "'scheduled_for' future ka valid ISO datetime hona chahiye."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            message.scheduled_for = parsed
            update_fields.append('scheduled_for')

        if not update_fields:
            return Response({'detail': "Kam se kam 'text' ya 'scheduled_for' me se ek dena zaroori hai."},
                             status=status.HTTP_400_BAD_REQUEST)

        message.save(update_fields=update_fields + ['updated_at'])
        return Response(MessageSerializer(message, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        message = self.get_object()
        # 🔥 NAYA (M1-BE) — see `read_all`: pending/declined me read-receipt no-op.
        if not message_requests.is_request_accepted(message.conversation_id, request.user.id):
            return Response({'detail': 'Read mark ho gaya.'})
        now = timezone.now()

        MessageStatus.objects.update_or_create(
            message=message, user=request.user,
            defaults={'is_read': True, 'read_at': now, 'is_delivered': True},
        )
        if message.sender_id != request.user.id:
            ConversationParticipant.objects.filter(
                conversation=message.conversation, user=request.user, unread_count__gt=0,
            ).update(unread_count=F('unread_count') - 1)

        # Task 10 — same core.Notification sync as ConversationViewSet
        # .read_all() above, scoped to just THIS message (data__message_id)
        # rather than the whole conversation, since a single-message read
        # here doesn't imply every other message in the conversation was
        # also read.
        try:
            from core.models import Notification

            Notification.objects.for_user(request.user).unread().filter(
                notif_type=Notification.NotifType.CHAT_MESSAGE,
                data__message_id=str(message.id),
            ).update(is_read=True, read_at=now)
        except Exception:
            logger.exception('Task 10: failed to sync core.Notification read-state for message %s', message.id)

        return Response({'detail': 'Read mark ho gaya.'})

    # 🔥 NAYA — "Message info" / seen-by detail.
    #   GET /message/messages/<id>/read-status/
    # Response: {"delivered_to": [...], "read_by": [...]}
    # Har entry: {"user": {...}, "is_delivered", "delivered_at", "is_read", "read_at"}
    #
    # Privacy (see `UserPresence.show_read_receipts` docstring — mutual,
    # WhatsApp-style):
    #   - Jis user ne khud apna `show_read_receipts=False` kar rakha hai,
    #     uska `read_at`/`is_read` kisi ko bhi (group members samet) nahi
    #     dikhta — us row me `is_read` False aur `read_at` None kar diya
    #     jaata hai chahe DB me actually True/set ho (internal bookkeeping
    #     hamesha sahi rehti hai, sirf ye endpoint gate karta hai).
    #   - Agar DEKHNE waala khud bhi `show_read_receipts=False` par hai, to
    #     use bhi kisi ka bhi read-status nahi dikhta — poori `read_by`
    #     list khaali aa jaati hai (sirf `delivered_to` dikhta hai).
    # `is_delivered`/`delivered_at` is toggle se kabhi affect nahi hota.
    @action(detail=True, methods=['get'], url_path='read-status')
    def read_status(self, request, pk=None):
        from ..models import UserPresence

        message = self.get_object()

        viewer_receipts_off = UserPresence.objects.filter(
            user=request.user, show_read_receipts=False,
        ).exists()

        statuses = list(
            MessageStatus.objects.filter(message=message)
            .exclude(user_id=message.sender_id)
            .select_related('user')
        )

        # Sender ke saamne bhi apna khud ka receipt-off toggle apply hota
        # hai — sender khud bhi ek "viewer" hai jab wo apne message ka
        # status dekh raha hai.
        hidden_user_ids = set(
            UserPresence.objects.filter(
                user_id__in=[s.user_id for s in statuses], show_read_receipts=False,
            ).values_list('user_id', flat=True)
        )

        # Issue #2 (RestrictUser): anyone who has restricted the viewer
        # never shows the viewer a read receipt — same as their own
        # receipts toggle being off, but only towards this viewer.
        from user_profile.services import restrictor_ids_of
        hidden_user_ids |= restrictor_ids_of(
            request.user.id, among=[s.user_id for s in statuses],
        )

        delivered_to, read_by = [], []
        for s in statuses:
            if s.is_delivered:
                delivered_to.append(s)
            show_read = s.is_read and not viewer_receipts_off and s.user_id not in hidden_user_ids
            if show_read:
                read_by.append(s)
            elif s.is_delivered:
                # read_at/is_read is response se hide karo, delivered info bacha rehne do
                s.is_read = False
                s.read_at = None

        return Response({
            'delivered_to': MessageReadStatusSerializer(delivered_to, many=True, context={'request': request}).data,
            'read_by': MessageReadStatusSerializer(read_by, many=True, context={'request': request}).data,
        })

    # ==================================================================
    # PIN / UNPIN — WhatsApp/Telegram-style important-message pin
    # ==================================================================
    # POST   /message/messages/<id>/pin/    -> pin
    # DELETE /message/messages/<id>/pin/    -> unpin
    #
    # Permission: group me sirf admin/moderator pin/unpin kar sakte hain
    # (`group_rules.is_group_admin_or_mod` — jaisa baaki group-wide
    # actions me hai). Private 1-1 chat me koi bhi hierarchy nahi hoti,
    # isliye dono participants me se koi bhi pin/unpin kar sakta hai
    # (WhatsApp isi tarah karta hai).
    #
    # Max-3-pinned limit (WhatsApp jaisa) enforce karte hain taaki chat
    # "pinned messages" list bemaani zyada lambi na ho jaaye — limit
    # cross ho to naya pin karne se pehle purana unpin karne ko kaha
    # jaata hai (auto-replace nahi karte, taaki accidental unpin na ho).
    # 🔥 FIX — `MAX_PINNED_PER_CONVERSATION` ab `constants.py` se shared
    # (module-level import, upar dekho) — pehle yahan aur consumers.py
    # dono me alag-alag class attribute ke roop me hardcoded tha, dono ko
    # manually sync rakhna padta tha (see constants.py comment).

    @action(detail=True, methods=['post', 'delete'], url_path='pin')
    def pin(self, request, pk=None):
        message = self.get_object()
        conversation = message.conversation

        if conversation.type == ConversationType.GROUP:
            group = getattr(conversation, 'group_detail', None)
            if group and not is_group_admin_or_mod(group, request.user.id):
                raise PermissionDenied('Sirf group admin/moderator message pin/unpin kar sakte hain.')

        if request.method == 'DELETE':
            message.is_pinned = False
            message.pinned_at = None
            message.pinned_by = None
            message.save(update_fields=['is_pinned', 'pinned_at', 'pinned_by'])
            event = 'unpinned'
        else:
            if message.deleted_for_everyone:
                return Response({'detail': 'Delete kiya hua message pin nahi ho sakta.'}, status=status.HTTP_400_BAD_REQUEST)
            if not message.is_pinned:
                pinned_count = conversation.all_messages.filter(is_pinned=True).count()
                if pinned_count >= MAX_PINNED_PER_CONVERSATION:
                    return Response(
                        {'detail': f'Ek chat me max {MAX_PINNED_PER_CONVERSATION} messages hi pin ho sakte hain. Pehle koi purana unpin karo.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                message.is_pinned = True
                message.pinned_at = timezone.now()
                message.pinned_by = request.user
                message.save(update_fields=['is_pinned', 'pinned_at', 'pinned_by'])
            event = 'pinned'

        # 🔥 Live update — sabhi connected participants ko turant pin/unpin
        # dikhe, chat re-open kiye bina (jaisa disappearing_messages
        # update ka pattern hai).
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'pin_event',
                'event': event,
                'message_id': str(message.id),
                'conversation_id': str(conversation.id),
                'actor_id': str(request.user.id),
            }
        )

        return Response(MessageSerializer(message, context={'request': request}).data)

    # ==================================================================
    # 🔥 NAYA — POLL VOTE / CLOSE
    # ==================================================================
    # POST /message/messages/<message_id>/poll/vote/
    #   body: {"option_ids": ["<uuid>", ...]}
    #
    # Single-choice poll (`allow_multiple_answers=False`): sirf 1 option_id
    # bhejo — naya vote is user ke is poll ke andar ke saare purane votes
    # replace kar deta hai (WhatsApp jaisa — vote badalna allowed hai, jab
    # tak poll close na ho). Multi-choice: kai option_ids ek saath bhejo —
    # ye call "meri poori vote list ye hai" jaisa idempotent hai (purane
    # saare votes clear karke naye set se replace karta hai), isliye ek
    # option untick karne ke liye bhi client bas naya (chhota) list bhejta
    # hai, alag "unvote" endpoint ki zaroorat nahi.
    #
    # TASK 29 — "clear my vote entirely": `option_ids: []` above already
    # reads as "my vote list is now empty" for the multi-choice case, but
    # for a single-choice poll `PollVoteSerializer` requires at least one
    # option (a bare empty POST body doesn't map to "no opinion" cleanly
    # for that shape), and there was no way at all to go from "voted" back
    # to "no vote" without picking some other option first. `DELETE` on
    # this same route is the explicit, unambiguous version of that: it
    # removes every vote this user has on this poll and nothing else,
    # regardless of single/multi-choice.
    @action(detail=True, methods=['post', 'delete'], url_path='poll/vote')
    def poll_vote(self, request, pk=None):
        message = self.get_object()
        if message.type != MessageType.POLL:
            return Response({'detail': 'Ye poll message nahi hai.'}, status=status.HTTP_400_BAD_REQUEST)

        poll = getattr(message, 'poll', None)
        if not poll:
            return Response({'detail': 'Poll data nahi mila.'}, status=status.HTTP_404_NOT_FOUND)
        if poll.is_closed:
            return Response({'detail': 'Ye poll band ho chuka hai, ab vote nahi ho sakta.'}, status=status.HTTP_400_BAD_REQUEST)

        if request.method == 'DELETE':
            PollVote.objects.filter(option__poll=poll, user=request.user).delete()
            poll_data = PollSerializer(poll, context={'request': request}).data
            async_to_sync(get_channel_layer().group_send)(
                f'chat_{message.conversation_id}',
                {
                    'type': 'poll_update',
                    'message_id': str(message.id),
                    'poll': poll_data,
                    # 🔥 NAYA — `voted_by` (POST) ke parallel `cleared_by`,
                    # taaki client ye differentiate kar sake ki ye event
                    # "kisi ne vote diya" hai ya "kisi ne apna vote hataya".
                    'cleared_by': str(request.user.id),
                },
            )
            # Updated poll (fresh tallies) return karte hain, 204 nahi —
            # `POST` yahi karta hai, aur client ko refresh ke liye alag
            # GET nahi karna padta clear ke turant baad.
            return Response(poll_data)

        serializer = PollVoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        option_ids = serializer.validated_data['option_ids']

        # Sirf isi poll ke apne options hi valid hain — kisi doosre
        # message/poll ka option_id chupke se bhej ke cross-poll vote
        # inject karna is filter se hi block ho jaata hai.
        options = list(poll.options.filter(id__in=option_ids))
        if not options:
            return Response({'detail': 'Koi valid option nahi mila.'}, status=status.HTTP_404_NOT_FOUND)
        if not poll.allow_multiple_answers and len(options) > 1:
            return Response({'detail': 'Ye poll sirf ek option allow karta hai.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            PollVote.objects.filter(option__poll=poll, user=request.user).delete()
            PollVote.objects.bulk_create([
                PollVote(option=opt, user=request.user) for opt in options
            ])

        poll_data = PollSerializer(poll, context={'request': request}).data
        async_to_sync(get_channel_layer().group_send)(
            f'chat_{message.conversation_id}',
            {
                'type': 'poll_update',
                'message_id': str(message.id),
                'poll': poll_data,
                'voted_by': str(request.user.id),
            },
        )
        return Response(poll_data)

    # POST /message/messages/<message_id>/poll/close/
    # Poll banane wala, ya group me admin/moderator, poll ko close kar
    # sakta hai (results wahin freeze ho jaate hain — vote/re-vote ke liye
    # ab 400 aayega, existing votes/counts hamesha ke liye visible rehte hain).
    @action(detail=True, methods=['post'], url_path='poll/close')
    def poll_close(self, request, pk=None):
        message = self.get_object()
        if message.type != MessageType.POLL:
            return Response({'detail': 'Ye poll message nahi hai.'}, status=status.HTTP_400_BAD_REQUEST)

        poll = getattr(message, 'poll', None)
        if not poll:
            return Response({'detail': 'Poll data nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        conversation = message.conversation
        if conversation.type == ConversationType.GROUP:
            group = getattr(conversation, 'group_detail', None)
            is_admin_or_mod = bool(group and is_group_admin_or_mod(group, request.user.id))
            if message.sender_id != request.user.id and not is_admin_or_mod:
                raise PermissionDenied('Sirf poll banane wala ya group admin/moderator poll close kar sakta hai.')
        elif message.sender_id != request.user.id:
            raise PermissionDenied('Sirf poll banane wala hi ise close kar sakta hai.')

        if not poll.is_closed:
            poll.is_closed = True
            poll.closed_at = timezone.now()
            poll.closed_by = request.user
            poll.save(update_fields=['is_closed', 'closed_at', 'closed_by', 'updated_at'])

        poll_data = PollSerializer(poll, context={'request': request}).data
        async_to_sync(get_channel_layer().group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'poll_update',
                'message_id': str(message.id),
                'poll': poll_data,
                'closed_by': str(request.user.id),
            },
        )
        return Response(poll_data)

    # ==================================================================
    # FORWARD — one or many messages, to one or many target conversations
    # ==================================================================
    #
    # POST /message/messages/forward/
    # body: {"message_ids": ["<id>", ...], "conversation_ids": ["<id>", ...]}
    #
    # WhatsApp-style forward: works for a single message to a single chat,
    # a single message to many chats, or many messages to many chats — all
    # in one request. Each forwarded copy is a NEW Message row (sender =
    # the forwarding user, is_forwarded=True), not a pointer to the
    # original, so later edits/deletes of the source message never affect
    # forwarded copies.
    #
    # This is a detail=False action (no single object to check
    # IsConversationParticipant against), so membership is verified
    # manually below for every source message and every target
    # conversation instead of relying on the object-level permission.
    @action(detail=False, methods=['post'], url_path='forward')
    def forward(self, request):
        message_ids = request.data.get('message_ids') or []
        conversation_ids = request.data.get('conversation_ids') or []

        if not isinstance(message_ids, list) or not message_ids:
            return Response({'detail': "'message_ids' required hai (non-empty list)."}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(conversation_ids, list) or not conversation_ids:
            return Response({'detail': "'conversation_ids' required hai (non-empty list)."}, status=status.HTTP_400_BAD_REQUEST)

        # 🔥 NAYA — optional caption jo forward ke saath jodi ja sakti hai
        # (WhatsApp jaisa: forward karte waqt compose box me kuch aur bhi
        # likh ke bhejna). Sirf un forwarded copies pe apply hoti hai jinke
        # paas khud koi text nahi tha (media/location messages) — agar
        # source message pehle se hi text-type hai to uska original text
        # kabhi silently overwrite/lose nahi karte, caption us case me
        # ignore ho jaati hai.
        caption = (request.data.get('caption') or '').strip() or None

        # Only messages from conversations the user is actually a member
        # of can be forwarded — silently drops any id that doesn't exist,
        # was deleted, or belongs to a chat the user isn't in.
        # 🔥 FIX — also excludes messages whose disappearing-messages
        # `expires_at` has already passed. They were still forwardable
        # before this, which defeated the point of "disappearing" — a
        # message that vanished from the chat could be resurrected in a
        # brand new chat with a fresh (non-expiring) copy.
        # TASK 29 — poll forwarding is now supported. Plain field-copy
        # (text/file_url/meta) is still not enough for a POLL message —
        # the actual poll data lives in separate `Poll`/`PollOption`
        # rows — so a poll message additionally gets its `Poll` +
        # `PollOption` rows explicitly cloned below (see the loop
        # further down). Deliberately NOT cloned:
        #   - `PollVote` rows: a forwarded poll is a fresh poll in a
        #     (possibly totally different) chat — carrying over votes
        #     from the original audience would misrepresent who voted
        #     in the new conversation, and could leak "who voted what"
        #     to people who were never in the source chat to begin with.
        #   - `is_closed` / `closed_at` / `closed_by`: the forwarded copy
        #     always starts OPEN, regardless of whether the source poll
        #     was closed — it's a new poll, so people in the target chat
        #     should be able to vote on it.
        # A poll message with no `Poll` row at all (shouldn't normally
        # happen, but defensive) is silently dropped from the forward
        # rather than creating a client-crashing "POLL type with no poll
        # data" message — see the `ordered_messages` filter below.
        source_messages = list(
            Message.objects.filter(id__in=message_ids, conversation__memberships__user=request.user)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .exclude(deleted_for_everyone=True)
            .exclude(deleted_for_users=request.user)
            .select_related('conversation')
            .prefetch_related('poll', 'poll__options')
        )
        if not source_messages:
            return Response({'detail': 'Koi valid message nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        # preserve the order the client selected them in, not DB order
        by_id = {str(m.id): m for m in source_messages}
        ordered_messages = [
            m for m in (by_id[str(mid)] for mid in message_ids if str(mid) in by_id)
            if m.type != MessageType.POLL or getattr(m, 'poll', None) is not None
        ]
        if not ordered_messages:
            return Response({'detail': 'Koi valid message nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        # Only target conversations the user is currently a member of.
        target_conversations = list(
            Conversation.objects.filter(
                id__in=conversation_ids,
                memberships__user=request.user,
                memberships__left_at__isnull=True,
            ).distinct()
        )
        # M9a — forward bhi send hai: admin-only group me normal member forward
        # nahi kar sakta (pehle ye path permission bypass karta tha).
        _blocked_titles = []
        _allowed_targets = []
        for _c in target_conversations:
            _g = getattr(_c, 'group_detail', None) if _c.type == ConversationType.GROUP else None
            if _g is not None and is_admins_only(_g) and not is_group_admin_or_mod(_g, request.user.id):
                _blocked_titles.append(str(_c.id))
                continue
            _allowed_targets.append(_c)
        target_conversations = _allowed_targets
        if not target_conversations and _blocked_titles:
            return Response(
                {'detail': 'Only admins can send messages', 'code': 'admins_only', 'blocked_conversation_ids': _blocked_titles},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not target_conversations:
            return Response({'detail': 'Koi valid target conversation nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        sender = build_user_mini(request.user)
        sender_name = sender['display_name']
        created_by_conversation = {}

        with transaction.atomic():
            for conversation in target_conversations:
                created_messages = []
                for src in ordered_messages:
                    msg = Message.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        type=src.type,
                        # Caption sirf tab lagti hai jab source ke paas
                        # khud koi non-empty text nahi tha (media/location
                        # message) — text message ka apna text hamesha
                        # priority pe rehta hai, caption tab silently
                        # ignore ho jaati hai. Poll messages ka `text`
                        # hamesha poll question hota hai (non-empty), to
                        # ye caption bhi unke liye automatically ignore
                        # ho jaati hai — koi alag POLL-specific check
                        # nahi chahiye.
                        text=(caption if (caption and not (src.text or '').strip()) else src.text),
                        file_url=src.file_url,
                        file_urls=src.file_urls,
                        thumbnail_url=src.thumbnail_url,
                        meta=src.meta,
                        is_forwarded=True,
                    )

                    # TASK 29 — poll forward: clone `Poll` + `PollOption`
                    # rows onto the new message. Votes and closed-state
                    # are intentionally NOT carried over — see the big
                    # comment above `source_messages` for why.
                    if src.type == MessageType.POLL:
                        src_poll = src.poll
                        new_poll = Poll.objects.create(
                            message=msg,
                            question=src_poll.question,
                            allow_multiple_answers=src_poll.allow_multiple_answers,
                        )
                        PollOption.objects.bulk_create([
                            PollOption(poll=new_poll, text=opt.text, order=opt.order)
                            for opt in src_poll.options.all().order_by('order')
                        ])
                        # Cache the reverse `msg.poll` lookup so the
                        # broadcast loop below (after this transaction
                        # commits) doesn't need an extra query to fetch
                        # what we just created.
                        msg.poll = new_poll

                    created_messages.append(msg)

                last = created_messages[-1]
                conversation.last_message_text = (last.text or '')[:500]
                conversation.last_message_at = last.created_at
                conversation.last_message_sender = request.user
                conversation.last_message_type = last.type
                conversation.save(update_fields=[
                    'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
                ])

                ConversationParticipant.objects.filter(conversation=conversation).exclude(
                    user=request.user
                ).update(unread_count=F('unread_count') + len(created_messages))

                created_by_conversation[str(conversation.id)] = created_messages

        # Broadcast + push AFTER the transaction commits, per target conversation.
        channel_layer = get_channel_layer()
        for conversation in target_conversations:
            created_messages = created_by_conversation[str(conversation.id)]
            for msg in created_messages:
                payload = {
                    'type': 'chat_message',
                    'event': 'message',
                    'id': str(msg.id),
                    'conversation_id': str(conversation.id),
                    'sender_id': str(request.user.id),
                    'sender_name': sender_name,
                    'sender_username': sender['username'],
                    'sender_first_name': sender['first_name'],
                    'sender_last_name': sender['last_name'],
                    'sender_profile_photo': sender['profile_photo'],
                    'message_type': msg.type,
                    'text': msg.text,
                    'file_url': msg.file_url,
                    'file_urls': msg.file_urls,
                    'thumbnail_url': msg.thumbnail_url,
                    'meta': msg.meta,
                    'reply_to': None,
                    'is_forwarded': True,
                    'client_id': None,
                    'created_at': msg.created_at.isoformat(),
                }
                # TASK 29 — forwarded poll needs its `poll` data in the
                # live event too, same as `create_poll` sends for a
                # brand-new poll — otherwise a client that's already open
                # on the target chat would render a POLL-type bubble with
                # no options until the next full refetch.
                if msg.type == MessageType.POLL:
                    poll_obj = getattr(msg, 'poll', None)
                    if poll_obj:
                        payload['poll'] = PollSerializer(poll_obj, context={'request': request}).data
                async_to_sync(channel_layer.group_send)(f'chat_{conversation.id}', payload)

            other_recipients = list(
                ConversationParticipant.objects.filter(conversation=conversation)
                .exclude(user=request.user)
                .values_list('user_id', flat=True)
            )
            last = created_messages[-1]
            for uid in other_recipients:
                async_to_sync(channel_layer.group_send)(
                    f'user_{uid}',
                    {
                        'type': 'inbox_update',
                        'conversation_id': str(conversation.id),
                        'message_id': str(last.id),
                        'sender_id': str(request.user.id),
                        'sender_name': sender_name,
                        'last_message_text': last.text,
                        'last_message_type': last.type,
                        'created_at': last.created_at.isoformat(),
                    }
                )

            muted_user_ids = set(
                ConversationParticipant.objects.filter(
                    conversation=conversation, user_id__in=other_recipients, is_muted=True,
                ).values_list('user_id', flat=True)
            )
            push_recipients = [uid for uid in other_recipients if uid not in muted_user_ids]
            if push_recipients:
                preview_text = last.text if len(created_messages) == 1 else f"{len(created_messages)} forwarded messages"
                send_chat_message_push(
                    recipient_ids=push_recipients,
                    sender_name=sender_name,
                    message_text=preview_text,
                    message_type=last.type,
                    conversation_id=conversation.id,
                    message_id=last.id,
                )

        return Response({
            'detail': f"{len(ordered_messages)} message(s) forwarded to {len(target_conversations)} chat(s).",
            'forwarded': {
                conv_id: MessageSerializer(msgs, many=True, context={'request': request}).data
                for conv_id, msgs in created_by_conversation.items()
            },
        }, status=status.HTTP_201_CREATED)
