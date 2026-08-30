import uuid
import os
import secrets
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from .models import (
    BlockedUser,
    CallSession,
    CallParticipant,
    CallStatus,
    CallType,
    Conversation,
    ConversationParticipant,
    ConversationType,
    DeviceToken,
    DisappearingDuration,
    Group,
    GroupJoinRequest,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    StudyRoomState,
)
from .permissions import IsConversationParticipant, IsGroupAdminOrModerator, IsMessageSender
from .serializers import (
    BlockedUserSerializer,
    CallSessionSerializer,
    ConversationListSerializer,
    ConversationSettingsSerializer,
    GroupCreateSerializer,
    GroupJoinRequestSerializer,
    GroupMediaSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    MessageCreateSerializer,
    MessageReactionSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    UserMiniSerializer,
    UserPresenceSerializer,
)
from .push_utils import (
    send_push_to_users, send_chat_message_push, send_incoming_call_push,
    send_call_cancelled_push, send_mention_push,
)
from .livekit_utils import generate_livekit_token
from .user_display import build_user_mini, get_display_name, get_profile_photo_url
from .group_rules import check_group_permission, check_daily_message_limit, is_group_admin_or_mod
from .mentions import extract_mentioned_user_ids
from .media_utils import create_group_media_for_message
from .constants import MAX_PINNED_PER_CONVERSATION

# LiveKit URL env se lo, nahi to default
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "ws://10.93.221.189:7880")

User = get_user_model()


# 🔥 NAYA — block-enforcement helper. Pehle `BlockedUser` sirf `start_private`
# (nayi conversation banate waqt) check hota tha — EXISTING conversation me
# message bhejte/call karte waqt kahin bhi check nahi tha, isliye block karne
# ke baad bhi dusra user normally message/call kar sakta tha. Ab is helper ko
# `ConversationViewSet.messages` aur `CallInitiateView.post` dono jagah use
# karte hain. Sirf 1-1 (private) conversations ke liye relevant hai — group
# me user-level block ka concept hi nahi hai, isliye caller khud `conversation
# .type != group` check karke hi ye function bulaye.
def is_blocked_pair(user_a_id, user_b_id):
    return BlockedUser.objects.filter(
        Q(blocker_id=user_a_id, blocked_id=user_b_id) |
        Q(blocker_id=user_b_id, blocked_id=user_a_id)
    ).exists()


# 🔥 FIX — `ConversationParticipant.objects.get_or_create(conversation=..,
# user=..)` alone is NOT enough to re-add someone who previously left / was
# removed from a conversation. `unique_together = ('conversation', 'user')`
# means get_or_create() finds their OLD row (with `left_at` still set to a
# past timestamp) and returns it AS-IS — it never resets `left_at` back to
# None. Every membership check in this app (`ConversationViewSet.get_queryset`,
# `ChatConsumer.is_conversation_member`, message/call permission checks, the
# chat-list query, ...) filters on `left_at__isnull=True`, so that user would
# be "added" (a `GroupMember` row exists, `add_members`/`join`/
# `approve_join_request`/`add_participant_to_conversation` all return success)
# but stay silently locked out — no error is ever shown, the conversation
# just never appears for them and every membership check keeps failing. This
# helper is used everywhere a user is (re-)added to a conversation so access
# is actually restored.
def add_or_reactivate_participant(conversation, user):
    participant, created = ConversationParticipant.objects.get_or_create(
        conversation=conversation, user=user,
    )
    if not created and participant.left_at is not None:
        participant.left_at = None
        participant.save(update_fields=['left_at'])
    return participant, created


class MessagePagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


# ======================================================================
# CONVERSATIONS
# ======================================================================
class ConversationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConversationListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        return Conversation.objects.filter(
            memberships__user=self.request.user, memberships__left_at__isnull=True,
        ).distinct().select_related('group_detail', 'last_message_sender').order_by(
            '-last_message_at', '-created_at'
        )

    def get_serializer_context(self):
        return {'request': self.request}

    @action(detail=False, methods=['post'], url_path='start_private')
    def start_private(self, request):
        other_user_id = request.data.get('user_id')
        if not other_user_id:
            return Response({'detail': "'user_id' required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if str(other_user_id) == str(request.user.id):
            return Response({'detail': 'Khud se chat nahi bana sakte.'}, status=status.HTTP_400_BAD_REQUEST)

        other_user = get_object_or_404(User, id=other_user_id)

        blocked = BlockedUser.objects.filter(
            Q(blocker=request.user, blocked=other_user) | Q(blocker=other_user, blocked=request.user)
        ).exists()
        if blocked:
            return Response({'detail': 'Ye user block hai, chat start nahi ho sakti.'}, status=status.HTTP_403_FORBIDDEN)

        conversation, created = Conversation.get_or_create_private(request.user, other_user)
        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['patch'], url_path='settings')
    def update_settings(self, request, pk=None):
        conversation = self.get_object()
        membership = get_object_or_404(ConversationParticipant, conversation=conversation, user=request.user)
        serializer = ConversationSettingsSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # 🔥 NAYA — chat list se ek ya kai chats ek saath delete karne ke liye.
    # Ye sirf REQUESTING USER ke liye chat hide karta hai (WhatsApp jaisa
    # "delete chat" — dusre participant/group members ko koi farak nahi
    # padta, unki chat waisi hi rehti hai). Membership row delete nahi
    # karte (taaki messages/unread history intact rahe agar chat wapas
    # khule to), sirf `left_at` set karte hain — jo `get_queryset()` me
    # already filter ho raha hai (`memberships__left_at__isnull=True`),
    # isliye delete hote hi ye conversation list se turant gayab ho jaati hai.
    # 🔥 NAYA — Temporary chat (disappearing messages) on/off ya duration
    # change karne ke liye. Poori conversation ke liye ek hi setting hai
    # (dono/sabhi participants ko wahi duration dikhta/lagta hai — WhatsApp
    # jaisa), isliye per-user settings (`update_settings` action, jo
    # `ConversationParticipant` pe hai) se ye alag rakha hai.
    # Group chat me sirf admin/moderator change kar sakte hain; private chat
    # me dono me se koi bhi (WhatsApp me bhi private chat ka disappearing
    # toggle dono side use kar sakte hain).
    @action(detail=True, methods=['patch'], url_path='disappearing_messages')
    def disappearing_messages(self, request, pk=None):
        conversation = self.get_object()
        duration = request.data.get('duration')
        valid_values = [choice[0] for choice in DisappearingDuration.choices]
        if duration not in valid_values:
            return Response(
                {'detail': f"'duration' invalid hai. Valid values: {valid_values}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if conversation.type == ConversationType.GROUP:
            is_admin = GroupMember.objects.filter(
                group=conversation.group_detail, user=request.user,
                role__in=['admin', 'moderator'], is_banned=False,
            ).exists()
            if not is_admin:
                raise PermissionDenied('Sirf group admin/moderator ye setting change kar sakte hain.')

        conversation.disappearing_messages_duration = duration
        conversation.save(update_fields=['disappearing_messages_duration'])

        # 🔥 Sabhi connected participants ko live inform karo taaki chat
        # screen me turant (naya) banner/label dikh jaaye, refresh ki
        # zaroorat na pade.
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'disappearing_messages_updated',
                'conversation_id': str(conversation.id),
                'duration': duration,
                'updated_by': str(request.user.id),
            }
        )

        return Response({'detail': 'Disappearing messages setting update ho gayi.', 'duration': duration})

    # 🔥 NAYA — Chat ko apna custom naam/nickname dene ke liye (sirf
    # tumhare account ke liye — dusre participant/group members ko nahi
    # dikhega). Empty string bhejo to label clear ho jaayega aur wapas
    # default naam (participant/group ka naam) dikhne lagega.
    @action(detail=True, methods=['patch'], url_path='label')
    def update_label(self, request, pk=None):
        conversation = self.get_object()
        membership = get_object_or_404(ConversationParticipant, conversation=conversation, user=request.user)

        label = request.data.get('label', '')
        if not isinstance(label, str):
            return Response({'detail': "'label' string honi chahiye."}, status=status.HTTP_400_BAD_REQUEST)
        label = label.strip()
        if len(label) > 100:
            return Response(
                {'detail': 'Label 100 characters se zyada nahi ho sakta.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        membership.label = label or None
        membership.save(update_fields=['label'])
        return Response({'detail': 'Label update ho gaya.', 'label': membership.label})

    @action(detail=False, methods=['post'], url_path='bulk_delete')
    def bulk_delete(self, request):
        conversation_ids = request.data.get('conversation_ids', [])
        if not isinstance(conversation_ids, list) or not conversation_ids:
            return Response(
                {'detail': "'conversation_ids' (list) required hai."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        updated = ConversationParticipant.objects.filter(
            conversation_id__in=conversation_ids,
            user=request.user,
            left_at__isnull=True,
        ).update(left_at=timezone.now())

        return Response({
            'detail': f'{updated} chat(s) delete ho gayi.',
            'deleted_count': updated,
            'conversation_ids': conversation_ids,
        })

    # 🔥 NAYA — existing conversation me naya member add karne ke liye.
    # Sirf GROUP conversations ke liye valid hai (private 1-1 chat me
    # teesra banda add nahi ho sakta — usके liye group hi banao). Group
    # ke apne `/groups/<id>/members/` action jaisa hi behavior hai, bas
    # yahan conversation_id se entry point diya gaya hai jaisa frontend
    # ka `Participants` API contract expect karta hai.
    @action(detail=True, methods=['post'], url_path='participants')
    def add_participant_to_conversation(self, request, pk=None):
        conversation = self.get_object()
        if conversation.type != ConversationType.GROUP:
            return Response(
                {'detail': 'Sirf group conversation me participant add ho sakta hai.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'detail': "'user_id' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        group = getattr(conversation, 'group_detail', None)
        if group and group.is_private:
            is_admin = GroupMember.objects.filter(
                group=group, user=request.user, role__in=['admin', 'moderator'], is_banned=False,
            ).exists()
            if not is_admin:
                raise PermissionDenied('Sirf group admin/moderator member add kar sakte hain.')

        target_user = get_object_or_404(User, id=user_id)

        with transaction.atomic():
            add_or_reactivate_participant(conversation, target_user)
            if group:
                GroupMember.objects.get_or_create(group=group, user=target_user, defaults={'added_by': request.user})
                Group.objects.filter(id=group.id).update(
                    members_count=group.group_members.filter(is_banned=False).count()
                )

        serializer = self.get_serializer(conversation)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        conversation = self.get_object()

        if request.method == 'GET':
            # 🔥 NAYA — disappearing messages jinki expiry nikal chuki hai
            # unhe list se hide karo (hard delete cleanup ke liye alag se
            # `cleanup_expired_messages` command chalta hai, yahan sirf
            # defensive filter hai taaki cleanup thoda late chale to bhi
            # user ko expired message na dikhe).
            qs = conversation.all_messages.filter(
                Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
            ).select_related(
                'sender', 'reply_to', 'reply_to__sender'
            ).prefetch_related('all_reactions', 'all_reactions__user')
            paginator = MessagePagination()
            page = paginator.paginate_queryset(qs, request, view=self)
            serializer = MessageSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        # 🔥 NAYA — 1-1 chat me agar dono me se koi ek doosre ko block kiye
        # hue hai to REST se message send nahi hone dena (pehle sirf naya
        # `start_private` conversation banate waqt block check hota tha,
        # existing conversation ke is endpoint pe kabhi nahi — is wajah se
        # block karne ke baad bhi dusra bandaa normally message bhej sakta
        # tha). Group conversations me ye check skip karte hain, block wahan
        # applicable hi nahi hai.
        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se message nahi bheja ja sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # 🔥 NAYA — group ka message_permission + daily_message_limit
            # enforce karo (pehle in settings ka backend pe koi asar hi
            # nahi padta tha).
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)
                allowed, reason = check_daily_message_limit(group, request.user, conversation)
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client_id = serializer.validated_data.get('client_id')
        if client_id:
            existing = conversation.all_messages.filter(sender=request.user, client_id=client_id).first()
            if existing:
                return Response(MessageSerializer(existing, context={'request': request}).data)

        # 🔥 NAYA — conversation ki current disappearing-messages duration se
        # is message ka expiry snapshot nikaal lo (settings baad me badle to
        # is message pe asar nahi padega, sirf naye messages pe padega).
        disappearing_delta = conversation.get_disappearing_timedelta()
        expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

        with transaction.atomic():
            message = serializer.save(conversation=conversation, sender=request.user, expires_at=expires_at)

            conversation.last_message_text = (message.text or '')[:500]
            conversation.last_message_at = message.created_at
            conversation.last_message_sender = request.user
            conversation.last_message_type = message.type
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])

            ConversationParticipant.objects.filter(conversation=conversation).exclude(
                user=request.user
            ).update(unread_count=F('unread_count') + 1)

            # 🔥 NAYA — @mentions resolve karke message pe attach karo.
            # Sirf conversation ke ACTIVE members hi mention ho sakte hain.
            mentioned_ids = extract_mentioned_user_ids(message.text, conversation)
            mentioned_ids = [uid for uid in mentioned_ids if uid != request.user.id]
            if mentioned_ids:
                message.mentioned_users.set(mentioned_ids)

            # 🔥 BUG FIX — group gallery ke liye GroupMedia row (pehle
            # kahin bhi nahi banti thi, see media_utils.py).
            create_group_media_for_message(message)

        # NOTE: this payload must carry the same sender_* fields as
        # ChatConsumer.handle_new_message's websocket payload. This REST
        # endpoint is the path used for every media/location message and
        # for any text message sent while the socket briefly reconnects,
        # so leaving sender_name/sender_profile_photo out here made every
        # group member see "Unknown" as the sender for those messages.
        sender = build_user_mini(request.user)
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(message.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(request.user.id),
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
                'reply_to': str(message.reply_to.id) if message.reply_to else None,
                'client_id': client_id,
                'mentioned_user_ids': [str(uid) for uid in mentioned_ids],
                'created_at': message.created_at.isoformat(),
            }
        )

        other_recipients = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user=request.user)
            .values_list('user_id', flat=True)
        )
        sender_name = sender['display_name']

        # 🔥 NAYA — REST se gaya message (media message ya socket-down
        # fallback) bhi ConversationsScreen ko turant update karwaye.
        # `chat_{conversation.id}` group sirf unhi ko milta hai jo abhi
        # isi chat ke andar hain (ChatConsumer se joined); yahan har
        # recipient ke apne global `user_<id>` inbox group ko bhi ek
        # halka event bhejte hain (InboxConsumer se connect hota hai).
        for uid in other_recipients:
            async_to_sync(channel_layer.group_send)(
                f'user_{uid}',
                {
                    'type': 'inbox_update',
                    'conversation_id': str(conversation.id),
                    'message_id': str(message.id),
                    'sender_id': str(request.user.id),
                    'sender_name': sender_name,
                    'last_message_text': message.text,
                    'last_message_type': message.type,
                    'created_at': message.created_at.isoformat(),
                }
            )

        # 🔥 FIX — jinhone ye conversation mute kar rakha hai unhe push
        # notification NAHI jaani chahiye (WhatsApp jaisa: chat list me
        # unread badge/message update to dikhta rahega — wo upar already
        # ho chuka hai — sirf phone pe notification popup/sound nahi
        # aayega). Pehle sirf `is_muted` DB me save ho raha tha, lekin
        # yahan check hi nahi ho raha tha isliye muted logon ko bhi
        # notification chali jaati thi.
        muted_user_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_recipients, is_muted=True,
            ).values_list('user_id', flat=True)
        )
        # 🔥 NAYA — mention hone waale members ko ALAG "mention" push
        # milta hai (mute state ko override karta hai — WhatsApp isi
        # tarah karta hai), isliye unhe generic chat-message push ki
        # list se nikaal dete hain taaki double notification na jaaye.
        mentioned_set = set(mentioned_ids)
        push_recipients = [
            uid for uid in other_recipients if uid not in muted_user_ids and uid not in mentioned_set
        ]

        if push_recipients:
            send_chat_message_push(
                recipient_ids=push_recipients,
                sender_name=sender_name,
                message_text=message.text,
                message_type=message.type,
                conversation_id=conversation.id,
                message_id=message.id
            )

        if mentioned_ids:
            send_mention_push(
                recipient_ids=mentioned_ids,
                sender_name=sender_name,
                message_text=message.text,
                conversation_id=conversation.id,
                message_id=message.id,
            )

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='read_all')
    def read_all(self, request, pk=None):
        """
        FIXED: pehle for loop me update_or_create se database locked ho rha tha.
        Ab bulk_update + bulk_create + ignore_conflicts use kiya hai.
        """
        conversation = self.get_object()
        now = timezone.now()

        try:
            # Sirf 500 tak limit rakho taaki ek sath lock na lage
            unread_ids = list(
                conversation.all_messages.exclude(sender=request.user)
                .values_list('id', flat=True)[:500]
            )

            if unread_ids:
                existing_ids = set(
                    MessageStatus.objects.filter(
                        message_id__in=unread_ids, user=request.user
                    ).values_list('message_id', flat=True)
                )

                if existing_ids:
                    MessageStatus.objects.filter(
                        message_id__in=existing_ids, user=request.user
                    ).update(is_read=True, read_at=now, is_delivered=True, delivered_at=now)

                new_ids = set(unread_ids) - existing_ids
                if new_ids:
                    MessageStatus.objects.bulk_create([
                        MessageStatus(
                            message_id=mid,
                            user=request.user,
                            is_read=True,
                            read_at=now,
                            is_delivered=True,
                            delivered_at=now
                        ) for mid in new_ids
                    ], ignore_conflicts=True)

            ConversationParticipant.objects.filter(
                conversation=conversation,
                user=request.user
            ).update(
                unread_count=0,
                last_read_at=now
            )
        except Exception:
            # Agar bhi lock ho jaye to kam se kam unread 0 kar do, 500 error mat do
            ConversationParticipant.objects.filter(
                conversation=conversation,
                user=request.user
            ).update(unread_count=0, last_read_at=now)

        return Response({'detail': 'Saare messages read mark ho gaye.'})

    # ==================================================================
    # SEARCH — ek conversation ke andar text search
    # ==================================================================
    # GET /message/conversations/<id>/search/?q=...
    #
    # Simple `icontains` search hai (poore chat-app ka scale abhi itna
    # bada nahi ki Postgres full-text-search / Elasticsearch chahiye ho —
    # `conversation` FK pe already index hai to query chhoti si conversation
    # ke andar hi rehti hai). Deleted/expired messages exclude karte hain,
    # jaisa normal message-list me hota hai.
    @action(detail=True, methods=['get'], url_path='search')
    def search(self, request, pk=None):
        conversation = self.get_object()
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < 2:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = conversation.all_messages.filter(
            text__icontains=query,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).select_related('sender', 'reply_to').order_by('-created_at')

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # SEARCH — sabhi conversations me ek saath (global chat search)
    # ==================================================================
    # GET /message/conversations/search_all/?q=...
    #
    # Ye `detail=False` hai isliye alag URL `search_all/` par baithta hai
    # (router `search/` already `search` action ke through detail route
    # pe bana chuka hai). Sirf un conversations ke messages aate hain jinka
    # user abhi ACTIVE member hai (chat delete/leave kar chuka ho to us
    # conversation ke results nahi aayenge).
    @action(detail=False, methods=['get'], url_path='search_all')
    def search_all(self, request):
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < 2:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = Message.objects.filter(
            conversation__memberships__user=request.user,
            conversation__memberships__left_at__isnull=True,
            text__icontains=query,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).select_related(
            'sender', 'conversation', 'conversation__group_detail',
        ).distinct().order_by('-created_at')

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSearchResultSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # PINNED MESSAGES — is conversation ke saare currently-pinned messages
    # ==================================================================
    # GET /message/conversations/<id>/pinned/
    @action(detail=True, methods=['get'], url_path='pinned')
    def pinned(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.all_messages.filter(is_pinned=True).select_related(
            'sender', 'pinned_by',
        ).order_by('-pinned_at')
        serializer = MessageSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)


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
        if self.action in ('update', 'partial_update', 'destroy'):
            return [IsAuthenticated(), IsConversationParticipant(), IsMessageSender()]
        # 'forward' is a list-level action (no single pk/object) — it
        # checks conversation membership itself for every source message
        # and every target conversation, so it only needs authentication.
        if self.action == 'forward':
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsConversationParticipant()]

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
        return Response(MessageSerializer(message, context={'request': request}).data)

    def destroy(self, request, *args, **kwargs):
        message = self.get_object()
        for_everyone = str(request.query_params.get('for_everyone', 'false')).lower() == 'true'

        if for_everyone:
            if message.sender_id != request.user.id:
                return Response({'detail': 'Sirf sender hi sabke liye delete kar sakta hai.'}, status=status.HTTP_403_FORBIDDEN)
            message.deleted_for_everyone = True
            message.text = ''
            message.file_url = None
            message.save(update_fields=['deleted_for_everyone', 'text', 'file_url'])
        else:
            message.deleted_for_users.add(request.user)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post', 'delete'], url_path='react')
    def react(self, request, pk=None):
        message = self.get_object()

        if request.method == 'DELETE':
            MessageReaction.objects.filter(message=message, user=request.user).delete()
            return Response(status=status.HTTP_204_NO_CONTENT)

        emoji = request.data.get('emoji')
        if not emoji:
            return Response({'detail': "'emoji' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        reaction, _ = MessageReaction.objects.update_or_create(
            message=message, user=request.user, defaults={'emoji': emoji},
        )
        return Response(MessageReactionSerializer(reaction).data)

    @action(detail=True, methods=['post'], url_path='read')
    def mark_read(self, request, pk=None):
        message = self.get_object()
        now = timezone.now()

        MessageStatus.objects.update_or_create(
            message=message, user=request.user,
            defaults={'is_read': True, 'read_at': now, 'is_delivered': True},
        )
        if message.sender_id != request.user.id:
            ConversationParticipant.objects.filter(
                conversation=message.conversation, user=request.user, unread_count__gt=0,
            ).update(unread_count=F('unread_count') - 1)

        return Response({'detail': 'Read mark ho gaya.'})

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

        # Only messages from conversations the user is actually a member
        # of can be forwarded — silently drops any id that doesn't exist,
        # was deleted, or belongs to a chat the user isn't in.
        # 🔥 FIX — also excludes messages whose disappearing-messages
        # `expires_at` has already passed. They were still forwardable
        # before this, which defeated the point of "disappearing" — a
        # message that vanished from the chat could be resurrected in a
        # brand new chat with a fresh (non-expiring) copy.
        source_messages = list(
            Message.objects.filter(id__in=message_ids, conversation__memberships__user=request.user)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .exclude(deleted_for_everyone=True)
            .exclude(deleted_for_users=request.user)
            .select_related('conversation')
        )
        if not source_messages:
            return Response({'detail': 'Koi valid message nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        # preserve the order the client selected them in, not DB order
        by_id = {str(m.id): m for m in source_messages}
        ordered_messages = [by_id[str(mid)] for mid in message_ids if str(mid) in by_id]

        # Only target conversations the user is currently a member of.
        target_conversations = list(
            Conversation.objects.filter(
                id__in=conversation_ids,
                memberships__user=request.user,
                memberships__left_at__isnull=True,
            ).distinct()
        )
        if not target_conversations:
            return Response({'detail': 'Koi valid target conversation nahi mila.'}, status=status.HTTP_404_NOT_FOUND)

        sender = build_user_mini(request.user)
        sender_name = sender['display_name']
        created_by_conversation = {}

        with transaction.atomic():
            for conversation in target_conversations:
                created_messages = [
                    Message.objects.create(
                        conversation=conversation,
                        sender=request.user,
                        type=src.type,
                        text=src.text,
                        file_url=src.file_url,
                        file_urls=src.file_urls,
                        thumbnail_url=src.thumbnail_url,
                        meta=src.meta,
                        is_forwarded=True,
                    )
                    for src in ordered_messages
                ]

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
                async_to_sync(channel_layer.group_send)(
                    f'chat_{conversation.id}',
                    {
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
                )

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


# ======================================================================
# GROUPS
# ======================================================================
class GroupViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'create':
            return GroupCreateSerializer
        return GroupSerializer

    def get_queryset(self):
        return Group.objects.filter(
            group_members__user=self.request.user, group_members__is_banned=False,
        ).distinct().select_related('conversation', 'created_by')

    def get_permissions(self):
        # 🔥 NAYA — 'add_members' yahan se hata diya: public group me KOI
        # BHI member add kar sakta hai, private group me sirf admin/mod —
        # ye do-tarah ka rule ek single permission class se express nahi ho
        # sakta (usko group object dekhna padega), isliye check ab
        # `add_members()` method ke andar khud manually hota hai.
        if self.action in ('update', 'partial_update'):
            return [IsAuthenticated(), IsGroupAdminOrModerator()]
        return [IsAuthenticated()]

    # 🔥 NAYA — Delete group (ADMIN ONLY — moderator bhi nahi, sirf role
    # exactly 'admin' wale). Pehle ye action `ModelViewSet` ke default
    # `DestroyModelMixin` se bina kisi restriction ke chal raha tha —
    # matlab group ka KOI BHI member (chahe simple 'member' role ho) poora
    # group delete kar sakta tha. Ab `get_object()` khud hi queryset se
    # aata hai (jo already sirf group-members tak limited hai), uske baad
    # yahan explicit admin-role check lagaya hai.
    def destroy(self, request, *args, **kwargs):
        group = self.get_object()
        is_admin = GroupMember.objects.filter(
            group=group, user=request.user,
            role=GroupMember.Role.ADMIN, is_banned=False,
        ).exists()
        if not is_admin:
            raise PermissionDenied('Sirf group admin hi group delete kar sakta hai.')

        conversation = group.conversation
        group_id = str(group.id)
        conversation_id = str(conversation.id)

        # 🔥 Delete se PEHLE broadcast karo — taaki sabhi connected members
        # (jo abhi is chat me hain) ko turant pata chal jaaye group delete
        # ho gaya, aur unki app apne-aap chat screen se conversations list
        # pe wapas nikaal de. Delete ke BAAD `chat_{id}` group hi exist
        # nahi karega broadcast karne ke liye, isliye order zaroori hai.
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation_id}',
            {
                'type': 'group_deleted',
                'group_id': group_id,
                'conversation_id': conversation_id,
                'deleted_by': str(request.user.id),
            }
        )

        # 🔥 `Group.conversation` FK `on_delete=CASCADE` hai, isliye
        # `Conversation` delete karte hi Group, GroupMember,
        # ConversationParticipant, Message (aur unki reactions/status/
        # media/presentation/gallery) sab apne-aap CASCADE se delete ho
        # jaate hain — alag se har table clean karne ki zaroorat nahi.
        conversation.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        with transaction.atomic():
            conversation = Conversation.objects.create(type=ConversationType.GROUP)
            group = Group.objects.create(
                conversation=conversation,
                name=data['name'],
                description=data.get('description', ''),
                photo_url=data.get('photo_url'),
                is_private=data.get('is_private', False),
                invite_code=self._generate_invite_code(),
                created_by=request.user,
            )

            # 🔥 FIX: `member_ids` ab `GroupCreateSerializer` me
            # `IntegerField()` list hai (User pk integer hai, UUID nahi),
            # isliye yahan bhi seedha integers use karo — pehle `str(uid)`
            # bana ke `User.objects.filter(id__in=member_ids)` chalaya ja
            # raha tha, jo integer pk ke against string set match hi nahi
            # karta (Django ORM `id__in` me type mismatch pe silently 0
            # results deta hai) — matlab members select hote hue bhi group
            # me kabhi add hi nahi hote the.
            member_ids = set(data.get('member_ids', []))
            member_ids.discard(request.user.id)
            valid_users = list(User.objects.filter(id__in=member_ids))

            memberships = [ConversationParticipant(conversation=conversation, user=request.user)]
            group_members = [GroupMember(group=group, user=request.user, role=GroupMember.Role.ADMIN)]
            for user in valid_users:
                memberships.append(ConversationParticipant(conversation=conversation, user=user))
                group_members.append(GroupMember(group=group, user=user, added_by=request.user))

            ConversationParticipant.objects.bulk_create(memberships)
            GroupMember.objects.bulk_create(group_members)
            Group.objects.filter(id=group.id).update(members_count=len(group_members))
            group.refresh_from_db()

        return Response(GroupSerializer(group, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='members')
    def add_members(self, request, pk=None):
        group = self.get_object()
        # 🔥 NAYA — Public group: group ka koi bhi member (role kuch bhi ho)
        # doosre users ko seedha add kar sakta hai. Private group: sirf
        # admin/moderator (jaisa pehle tha). `get_permissions()` ab is
        # action ko sirf `IsAuthenticated` tak khula chhodta hai — `get_
        # object()` khud hi queryset se aata hai (jo already sirf group-
        # members tak limited hai), isliye caller ka member hona to already
        # confirm hai, bas role-check yahan manually lagana hai.
        if group.is_private:
            self._require_admin(group.id, request.user)

        user_ids = request.data.get('user_ids', [])
        if not user_ids:
            return Response({'detail': "'user_ids' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        existing_ids = set(str(uid) for uid in group.group_members.values_list('user_id', flat=True))
        new_ids = [uid for uid in user_ids if str(uid) not in existing_ids]
        users = User.objects.filter(id__in=new_ids)

        with transaction.atomic():
            for user in users:
                add_or_reactivate_participant(group.conversation, user)
                GroupMember.objects.get_or_create(group=group, user=user, defaults={'added_by': request.user})
            Group.objects.filter(id=group.id).update(
                members_count=group.group_members.filter(is_banned=False).count()
            )

        return Response(GroupSerializer(group, context={'request': request}).data)

    # 🔥 NAYA — invite-code se group join karna. Public group me turant
    # member ban jaate ho; private group me `GroupJoinRequest` (PENDING)
    # ban jaata hai, admin/moderator approve/reject karega. Non-member bhi
    # ise call kar sakta hai (`detail=False` isliye `get_object()`/
    # `get_queryset()` — jo sirf existing members tak limited hai — beech
    # me nahi aata, group seedha `invite_code` se dhoondte hain).
    @action(detail=False, methods=['post'], url_path='join')
    def join(self, request):
        invite_code = request.data.get('invite_code')
        if not invite_code:
            return Response({'detail': "'invite_code' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        group = Group.objects.filter(invite_code=invite_code).select_related('conversation').first()
        if not group:
            return Response({'detail': 'Invalid invite code.'}, status=status.HTTP_404_NOT_FOUND)

        existing_membership = GroupMember.objects.filter(group=group, user=request.user).first()
        if existing_membership:
            if existing_membership.is_banned:
                return Response({'detail': 'Aapko is group se ban kiya gaya hai.'}, status=status.HTTP_403_FORBIDDEN)
            return Response({'detail': 'Aap already is group ke member hain.'}, status=status.HTTP_400_BAD_REQUEST)

        if not group.is_private:
            # Public group — koi approval nahi chahiye, turant member.
            with transaction.atomic():
                add_or_reactivate_participant(group.conversation, request.user)
                GroupMember.objects.get_or_create(group=group, user=request.user)
                Group.objects.filter(id=group.id).update(
                    members_count=group.group_members.filter(is_banned=False).count()
                )
            return Response(
                {'status': 'joined', 'group': GroupSerializer(group, context={'request': request}).data},
                status=status.HTTP_201_CREATED,
            )

        # Private group — pending request. Pehle se koi row ho (reject wali
        # bhi) to naya banane ke bajaye usi ko wapas PENDING pe reset karo
        # (history bhi preserve rehti hai ki pehle kya hua tha).
        join_request, created = GroupJoinRequest.objects.get_or_create(
            group=group, user=request.user,
            defaults={'status': GroupJoinRequest.Status.PENDING},
        )
        if not created:
            if join_request.status == GroupJoinRequest.Status.PENDING:
                return Response({'status': 'pending', 'detail': 'Request pehle se pending hai.'})
            join_request.status = GroupJoinRequest.Status.PENDING
            join_request.responded_by = None
            join_request.responded_at = None
            join_request.save(update_fields=['status', 'responded_by', 'responded_at'])

        return Response(
            {'status': 'pending', 'detail': 'Request bhej di gayi, group admin approve karega.'},
            status=status.HTTP_202_ACCEPTED,
        )

    # 🔥 NAYA — private group ki pending join-requests (admin/moderator only).
    @action(detail=True, methods=['get'], url_path='join-requests')
    def join_requests(self, request, pk=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        qs = group.join_requests.filter(
            status=GroupJoinRequest.Status.PENDING
        ).select_related('user').order_by('-created_at')
        return Response(GroupJoinRequestSerializer(qs, many=True, context={'request': request}).data)

    # 🔥 NAYA — ek pending request approve karna (admin/moderator only) —
    # approve hote hi requester `GroupMember` + `ConversationParticipant`
    # ban jaata hai, `add_members` jaisa hi effect.
    @action(detail=True, methods=['post'], url_path=r'join-requests/(?P<request_id>[^/.]+)/approve')
    def approve_join_request(self, request, pk=None, request_id=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        join_request = get_object_or_404(
            GroupJoinRequest, id=request_id, group=group, status=GroupJoinRequest.Status.PENDING,
        )

        with transaction.atomic():
            add_or_reactivate_participant(group.conversation, join_request.user)
            GroupMember.objects.get_or_create(group=group, user=join_request.user, defaults={'added_by': request.user})
            join_request.status = GroupJoinRequest.Status.APPROVED
            join_request.responded_by = request.user
            join_request.responded_at = timezone.now()
            join_request.save(update_fields=['status', 'responded_by', 'responded_at'])
            Group.objects.filter(id=group.id).update(
                members_count=group.group_members.filter(is_banned=False).count()
            )

        member = GroupMember.objects.select_related('user').get(group=group, user=join_request.user)
        return Response(GroupMemberSerializer(member, context={'request': request}).data)

    # 🔥 NAYA — ek pending request reject karna (admin/moderator only).
    @action(detail=True, methods=['post'], url_path=r'join-requests/(?P<request_id>[^/.]+)/reject')
    def reject_join_request(self, request, pk=None, request_id=None):
        group = self.get_object()
        self._require_admin(group.id, request.user)
        join_request = get_object_or_404(
            GroupJoinRequest, id=request_id, group=group, status=GroupJoinRequest.Status.PENDING,
        )
        join_request.status = GroupJoinRequest.Status.REJECTED
        join_request.responded_by = request.user
        join_request.responded_at = timezone.now()
        join_request.save(update_fields=['status', 'responded_by', 'responded_at'])
        return Response({'detail': 'Request reject ho gayi.'}, status=status.HTTP_200_OK)

    @staticmethod
    def _generate_invite_code():
        # `secrets.token_urlsafe` URL-safe base64 deta hai (letters/digits/
        # -/_), 8 chars kaafi hai collision-avoid karne ke liye; phir bhi
        # loop laga rakha hai taaki DB-level uniqueness kabhi na tooté.
        while True:
            code = secrets.token_urlsafe(6)[:8]
            if not Group.objects.filter(invite_code=code).exists():
                return code

    @staticmethod
    def _require_admin(group_id, user):
        allowed = GroupMember.objects.filter(
            group_id=group_id, user=user, role__in=['admin', 'moderator'], is_banned=False,
        ).exists()
        if not allowed:
            raise PermissionDenied('Sirf group admin/moderator ye action kar sakte hain.')

    @action(detail=True, methods=['patch', 'delete'], url_path=r'members/(?P<user_id>[^/.]+)')
    def update_member(self, request, pk=None, user_id=None):
        group = self.get_object()
        membership = get_object_or_404(GroupMember, group=group, user_id=user_id)
        is_self = str(request.user.id) == str(user_id)

        if request.method == 'DELETE':
            if not is_self:
                self._require_admin(group.id, request.user)
            membership.delete()
            ConversationParticipant.objects.filter(
                conversation=group.conversation, user_id=user_id
            ).update(left_at=timezone.now())
            Group.objects.filter(id=group.id).update(
                members_count=group.group_members.filter(is_banned=False).count()
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        self._require_admin(group.id, request.user)
        was_banned = membership.is_banned
        for field in ('role', 'is_muted', 'is_banned'):
            if field in request.data:
                setattr(membership, field, request.data[field])
        membership.save()

        # 🔥 FIX — pehle sirf `GroupMember.is_banned` set hota tha.
        # Har permission check (`IsConversationParticipant`, chat list
        # queryset, message-send) `ConversationParticipant.left_at` pe
        # depend karta hai, `is_banned` pe nahi — isliye "banned" user
        # ban hone ke baad bhi normally chat kar/dekh pa raha tha. Ab
        # ban hote hi (jaisa DELETE/remove me already hota hai) left_at
        # set karo taaki access turant revoke ho; unban pe wapas active
        # karo.
        if membership.is_banned and not was_banned:
            ConversationParticipant.objects.filter(
                conversation=group.conversation, user_id=user_id
            ).update(left_at=timezone.now())
        elif was_banned and not membership.is_banned:
            ConversationParticipant.objects.filter(
                conversation=group.conversation, user_id=user_id
            ).update(left_at=None)

        return Response(GroupMemberSerializer(membership, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='media')
    def media(self, request, pk=None):
        group = self.get_object()
        qs = group.gallery.select_related('sender').order_by('-created_at')
        file_type = request.query_params.get('type')
        if file_type:
            qs = qs.filter(file_type=file_type)

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = GroupMediaSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


# ======================================================================
# BLOCKED USERS
# ======================================================================
class BlockedUserViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Block / unblock users — WhatsApp/Insta jaisa.

    POST   /blocked-users/           {"blocked": "<user_id>"}  -> block
    GET    /blocked-users/           -> meri poori block list
    DELETE /blocked-users/<lookup>/  -> unblock

    🔥 FIX: pehle `perform_create()` seedha `serializer.save()` karta tha
    — agar wahi user dobara block kiya jaata (ya frontend se double-tap
    ho jaata) to `unique_together = ('blocker', 'blocked')` ki wajah se
    500 (IntegrityError) aata tha. Ab `get_or_create` use kiya hai, isliye
    duplicate block pe error nahi, existing record hi wapas mil jaata hai.

    🔥 NAYA — unblock ab do tarike se ho sakta hai:
      1. `BlockedUser` record ki apni id se (jaisa pehle tha)
      2. seedha TARGET USER ki id se (frontend ke liye zyada natural —
         usko block-record ka internal id track karne ki zaroorat nahi,
         bas jis user ko unblock karna hai uski id bhejni hai)
    `get_object()` override karke dono lookups try kiye jaate hain.
    """
    serializer_class = BlockedUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return BlockedUser.objects.filter(blocker=self.request.user).select_related('blocked')

    def get_serializer_context(self):
        return {'request': self.request}

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        blocked_user = serializer.validated_data['blocked']

        if blocked_user.id == request.user.id:
            raise ValidationError('Khud ko block nahi kar sakte.')

        obj, created = BlockedUser.objects.get_or_create(
            blocker=request.user, blocked=blocked_user,
        )
        return Response(
            BlockedUserSerializer(obj, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def get_object(self):
        lookup_value = self.kwargs.get(self.lookup_url_kwarg or self.lookup_field)
        try:
            obj = self.get_queryset().filter(
                Q(id=lookup_value) | Q(blocked_id=lookup_value)
            ).first()
        except (ValueError, TypeError):
            obj = None
        if obj is None:
            raise Http404('Ye block record nahi mila.')
        self.check_object_permissions(self.request, obj)
        return obj

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response({'detail': 'Unblock ho gaya.'}, status=status.HTTP_204_NO_CONTENT)


# ======================================================================
# PRESENCE
# ======================================================================
class UserPresenceView(generics.RetrieveAPIView):
    serializer_class = UserPresenceSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        from .models import UserPresence
        presence, _ = UserPresence.objects.select_related('user').get_or_create(user_id=self.kwargs['user_id'])
        return presence


# ======================================================================
# CALLS
# ======================================================================
class CallInitiateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        conversation_id = request.data.get('conversation_id')
        call_type = request.data.get('type', 'audio')
        if not conversation_id:
            return Response({"detail": "conversation_id required"}, status=400)

        conversation = Conversation.objects.filter(id=conversation_id, memberships__user=request.user).first()
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        # 🔥 NAYA — messages POST action jaisa hi block check yahan bhi —
        # pehle koi bhi (blocked ho ya na ho) existing 1-1 conversation me
        # call initiate kar sakta tha, sirf naya chat start karte waqt block
        # check hota tha. Group calls me skip (block group me apply nahi).
        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {"detail": "Block hone ki wajah se call nahi ho sakti."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # 🔥 NAYA — group ka call_permission enforce karo.
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'call_permission')
                if not allowed:
                    return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        CallSession.objects.filter(
            conversation_id=conversation_id,
            caller=request.user,
            status__in=[CallStatus.INITIATED, CallStatus.RINGING]
        ).update(status=CallStatus.ENDED, ended_at=timezone.now())

        call = CallSession.objects.create(
            type=call_type,
            status=CallStatus.RINGING,
            conversation=conversation,
            group=getattr(conversation, 'group_detail', None),
            is_group_call=conversation.type == 'group',
            caller=request.user,
            channel_name=f"call_{uuid.uuid4().hex}",
            started_at=timezone.now()
        )
        CallParticipant.objects.create(call=call, user=request.user, status=CallStatus.ONGOING)

        # 🔥 FIX — was missing `left_at__isnull=True`. Without it, a user who
        # left the group/conversation (or was removed) still had a stale
        # `ConversationParticipant` row and kept getting rung for every new
        # call on that conversation — an incoming-call push + a
        # `CallParticipant` row — long after they had no business being
        # invited. Every other membership lookup in this view already
        # filters on `left_at__isnull=True`; this one didn't.
        other_members = list(
            conversation.memberships.filter(left_at__isnull=True)
            .exclude(user=request.user).values_list('user_id', flat=True)
        )
        for uid in other_members:
            CallParticipant.objects.create(call=call, user_id=uid, status=CallStatus.RINGING)

        caller_name = get_display_name(request.user)
        caller_photo = get_profile_photo_url(request.user, request=request)

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation_id}',
            {
                'type': 'call_event',
                'event': 'incoming_call',
                'call_id': str(call.id),
                'call_type': call_type,
                'caller_id': str(request.user.id),
                'caller_name': caller_name,
                'caller_photo': caller_photo,
                'conversation_id': str(conversation_id),
                'channel_name': call.channel_name,
            }
        )

        if other_members:
            send_incoming_call_push(
                recipient_ids=other_members,
                caller_name=caller_name,
                call_type=call_type,
                call_id=call.id,
                conversation_id=conversation_id,
                channel_name=call.channel_name
            )

        caller_token = generate_livekit_token(
            room_name=call.channel_name,
            user_id=request.user.id,
            user_name=caller_name,
        )

        return Response({
            "call_id": str(call.id),
            "channel_name": call.channel_name,
            "type": call_type,
            "status": call.status,
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": caller_token,
        })


class CallActionView(APIView):
    permission_classes = [IsAuthenticated]

    VALID_ACTIONS = ('accept', 'reject', 'end')

    def post(self, request, call_id):
        action = request.data.get('action')
        # 🔥 FIX — `action` was never validated against a known set. A
        # missing/typo'd value used to fall through every `if/elif` silently
        # (no token generated, no error either) and still broadcast a
        # `call_<action>` signal — e.g. `call_None` — to both sockets,
        # which the frontend has no handler for. Reject anything unexpected
        # up front with a clear 400 instead.
        if action not in self.VALID_ACTIONS:
            return Response(
                {"detail": f"'action' must be one of {self.VALID_ACTIONS}."},
                status=400,
            )

        call = CallSession.objects.filter(id=call_id).first()
        if not call:
            return Response({"detail": "Call not found"}, status=404)

        # 🔥 FIX — pehle yahan koi check nahi tha ki request bhejne wala
        # is call ka invited participant hai ya nahi. `CallParticipant`
        # filter 0 rows match karke bhi silently aage badh jaata tha aur
        # neeche ek VALID LiveKit token bana ke de deta tha — matlab koi
        # bhi authenticated user kisi bhi call_id pe 'accept' bhej ke us
        # call ke room me ghus sakta tha. Ab explicit membership check.
        is_participant = CallParticipant.objects.filter(call=call, user=request.user).exists()
        if not is_participant:
            return Response({"detail": "Aap is call ka hissa nahi hain."}, status=403)

        # 🔥 FIX — an 'accept' racing against a call the caller already
        # cancelled/ended would still mint a valid LiveKit token for a room
        # nobody else is in. The push/WS 'call_cancelled' event usually beats
        # this, but it's not guaranteed, so guard explicitly.
        if action == 'accept' and call.status == CallStatus.ENDED:
            return Response({"detail": "Ye call already end ho chuki hai."}, status=400)

        livekit_token = None

        if action == 'accept':
            CallParticipant.objects.filter(call=call, user=request.user).update(status=CallStatus.ONGOING)
            call.status = CallStatus.ONGOING
            call.connected_at = call.connected_at or timezone.now()
            call.save(update_fields=['status', 'connected_at'])

            user_name = get_display_name(request.user)
            livekit_token = generate_livekit_token(
                room_name=call.channel_name,
                user_id=request.user.id,
                user_name=user_name,
            )
        elif action == 'reject':
            CallParticipant.objects.filter(call=call, user=request.user).update(status=CallStatus.REJECTED, left_at=timezone.now())
            if not call.is_group_call:
                call.status = CallStatus.REJECTED
                call.ended_at = timezone.now()
                call.save(update_fields=['status', 'ended_at'])
        elif action == 'end':
            now = timezone.now()
            CallParticipant.objects.filter(call=call, user=request.user).update(left_at=now, status=CallStatus.ENDED)

            # 🔥 FIX (asli root-cause) — jo participants abhi bhi RINGING
            # hain (kabhi answer hi nahi kiya) unhe MISSED maro. Pehle
            # neeche wala check saare participants ka `left_at` set hone
            # ka wait karta tha — agar dusra banda kabhi answer/reject/end
            # hi nahi karta (jo caller-cancels-before-answer ka bilkul
            # normal case hai), to `left_at` kabhi set hi nahi hota aur
            # call.status HAMESHA 'ringing' pada rehta tha DB me — is
            # wajah se missed-call history/detection kabhi kaam hi nahi
            # karti thi.
            never_answered = CallParticipant.objects.filter(call=call, status=CallStatus.RINGING)
            missed_user_ids = list(
                never_answered.exclude(user=request.user).values_list('user_id', flat=True)
            )
            never_answered.update(status=CallStatus.MISSED, left_at=now)

            # Call sirf tab khatam maano jab koi bhi participant abhi
            # ONGOING (active) na ho — RINGING (jo kabhi jawab hi nahi
            # denge) ka wait nahi karna, warna group call bhi kabhi
            # 'ended' state me nahi jaati agar kuch invited log ignore
            # kar dein.
            still_active = CallParticipant.objects.filter(
                call=call, status=CallStatus.ONGOING, left_at__isnull=True
            ).exists()
            if not still_active:
                call.status = CallStatus.ENDED if call.connected_at else CallStatus.MISSED
                call.ended_at = now
                if call.connected_at:
                    call.duration_seconds = int((now - call.connected_at).total_seconds())
                call.save(update_fields=['status', 'ended_at', 'duration_seconds'])

            # 🔥 NAYA — jinhone kabhi answer hi nahi kiya unhe explicit
            # 'call_cancelled' push, taaki unka native incoming-call popup
            # (background/killed state me bhi) turant dismiss ho jaaye,
            # ringtone hamesha ke liye bajti na rahe.
            if missed_user_ids:
                send_call_cancelled_push(
                    recipient_ids=missed_user_ids,
                    call_id=call.id,
                    conversation_id=call.conversation_id,
                )

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'call_{call_id}',
            {
                'type': 'call_signal',
                'data': {'event': f'call_{action}', 'user_id': str(request.user.id), 'call_id': str(call_id)}
            }
        )
        async_to_sync(channel_layer.group_send)(
            f'chat_{call.conversation_id}',
            {
                'type': 'call_event',
                'event': f'call_{action}',
                'call_id': str(call_id),
                'user_id': str(request.user.id),
            }
        )
        return Response({
            "detail": f"call {action} done",
            "livekit_url": LIVEKIT_WS_URL if livekit_token else None,
            "livekit_token": livekit_token,
        })


class CallHistoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CallSessionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return CallSession.objects.filter(
            Q(caller=user) | Q(call_participants__user=user) | Q(group__group_members__user=user)
        ).distinct().select_related('caller').order_by('-created_at')

    # 🔥 NAYA — `MissedCallWatcher` (offline→online transition) ye
    # endpoint call karta hai. "Missed" ka matlab: user ko call me invite
    # kiya gaya tha (`CallParticipant` row exists), usne kabhi accept
    # nahi kiya (status abhi bhi RINGING), aur call khatam ho chuka hai.
    # Frontend errors ko silently swallow karta hai (returns []), isliye
    # pehle ye feature chup-chaap kabhi kaam hi nahi karta tha.
    @action(detail=False, methods=['get'], url_path='missed')
    def missed(self, request):
        since = request.query_params.get('since')
        # 🔥 FIX — pehle `status=RINGING` check karta tha, jo `CallActionView`
        # ke purane bug ki wajah se theek kaam nahi karta tha (never-answered
        # participants hamesha RINGING pade rehte the, call kabhi ENDED hoti
        # hi nahi thi). Ab `CallActionView.end` explicitly `MISSED` status
        # set karta hai jab koi jawab nahi deta, isliye seedha wahi check karo.
        qs = CallParticipant.objects.filter(
            user=request.user,
            status=CallStatus.MISSED,
        ).exclude(call__caller=request.user).select_related('call', 'call__caller')

        if since:
            parsed = parse_datetime(since)
            if parsed is None:
                return Response({'detail': "'since' valid ISO datetime honi chahiye."}, status=400)
            qs = qs.filter(call__created_at__gte=parsed)

        calls = [cp.call for cp in qs.order_by('-call__created_at')]
        serializer = CallSessionSerializer(calls, many=True, context={'request': request})
        return Response(serializer.data)

    # 🔥 NAYA — `_AddParticipantSheet` conversation ke members me se jo
    # abhi call me nahi hain unhe list karta hai (khud ko aur already-
    # invited/participating logon ko exclude karke).
    @action(detail=True, methods=['get'], url_path='addable-participants')
    def addable_participants(self, request, pk=None):
        call = get_object_or_404(CallSession, id=pk)

        if call.is_group_call and call.group_id:
            member_ids = set(
                call.group.group_members.filter(is_banned=False).values_list('user_id', flat=True)
            )
        elif call.conversation_id:
            member_ids = set(
                call.conversation.memberships.filter(left_at__isnull=True).values_list('user_id', flat=True)
            )
        else:
            member_ids = set()

        already_in_call = set(
            CallParticipant.objects.filter(call=call).values_list('user_id', flat=True)
        )
        addable_ids = member_ids - already_in_call - {request.user.id}

        users = User.objects.filter(id__in=addable_ids)
        serializer = UserMiniSerializer(users, many=True, context={'request': request})
        return Response(serializer.data)

    # 🔥 NAYA — ongoing (group) call me naya banda add karta hai: uska
    # `CallParticipant` (RINGING) banata hai, normal `incoming_call` push
    # + WS event bhejta hai SAME `call_id` ke saath, taaki accept karne
    # par wo isi LiveKit room me join ho jaaye.
    @action(detail=True, methods=['post'], url_path='add-participant')
    def add_participant(self, request, pk=None):
        call = get_object_or_404(CallSession, id=pk)
        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'detail': "'user_id' required hai."}, status=400)

        if call.status not in [CallStatus.RINGING, CallStatus.ONGOING]:
            return Response({'detail': 'Ye call ab active nahi hai.'}, status=400)

        target_user = get_object_or_404(User, id=user_id)
        participant, created = CallParticipant.objects.get_or_create(
            call=call, user=target_user, defaults={'status': CallStatus.RINGING},
        )
        if not created and participant.status not in [CallStatus.RINGING]:
            return Response({'detail': 'User already is/was in this call.'}, status=400)

        if not call.is_group_call:
            call.is_group_call = True
            call.save(update_fields=['is_group_call'])

        caller_name = get_display_name(request.user)

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{call.conversation_id}' if call.conversation_id else f'call_{call.id}',
            {
                'type': 'call_event',
                'event': 'incoming_call',
                'call_id': str(call.id),
                'call_type': call.type,
                'caller_id': str(request.user.id),
                'caller_name': caller_name,
                'caller_photo': get_profile_photo_url(request.user, request=request),
                'conversation_id': str(call.conversation_id) if call.conversation_id else None,
                'channel_name': call.channel_name,
            }
        )

        send_incoming_call_push(
            recipient_ids=[target_user.id],
            caller_name=caller_name,
            call_type=call.type,
            call_id=call.id,
            conversation_id=call.conversation_id,
            channel_name=call.channel_name,
        )

        return Response(CallSessionSerializer(call, context={'request': request}).data, status=201)


# ======================================================================
# STUDY ROOM
# ------------------------------------------------------------
# 🔥 NAYA — Google Meet-style persistent room. `CallInitiateView`/
# `CallActionView` se ALAG hai: koi `CallSession` nahi banta, koi
# ringing/accept-reject nahi, koi push notification nahi. Room-name
# seedha `conversation_id` se derive hota hai (`study_<conversation_id>`)
# taaki jo bhi study room khole wo sabke saath ek hi persistent LiveKit
# room me mile — jaise Meet link kholte hi ho jaata hai.
# ======================================================================
class StudyRoomJoinView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        conversation = Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user
        ).first()
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        # 🔥 NAYA — group ka study_room_permission enforce karo.
        if conversation.type == ConversationType.GROUP:
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'study_room_permission')
                if not allowed:
                    return Response({"detail": reason}, status=403)

        room_name = f"study_{conversation_id}"
        user_name = get_display_name(request.user)
        # 🔥 FIX — study rooms are long-running Meet-style sessions, unlike
        # calls; give them a much longer-lived token (8h) so an active
        # session doesn't get silently disconnected when the default 2h
        # call-token TTL runs out. Frontend should still re-join/refresh on
        # reconnect for sessions that outlive even this.
        token = generate_livekit_token(
            room_name=room_name,
            user_id=request.user.id,
            user_name=user_name,
            ttl=timedelta(hours=8),
        )

        # 🔥 NAYA — conversation ke saare members (naam + profile photo)
        # yahin se bhej dete hain, taaki join hote hi turant sabke naam/
        # photo dikh jayein — socket 'user_joined' handshake ka wait nahi
        # karna padta (wo sirf backup/late-joiner sync ke liye rehta hai).
        participants = []
        for membership in conversation.memberships.select_related('user').all():
            u = membership.user
            participants.append({
                "user_id": str(u.id),
                "display_name": get_display_name(u),
                "avatar_url": get_profile_photo_url(u, request=request),
            })

        return Response({
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": token,
            "room_name": room_name,
            "participants": participants,
        })


class StudyRoomStateView(APIView):
    """
    GET  -> poora saved whiteboard (`{"pages": [...]}`) wapas deta hai —
            state kabhi save hi nahi hui to khali `{"pages": []}` (404 nahi,
            taaki frontend har naye room ke liye error-log spam na kare).
    PUT  -> poora whiteboard state overwrite karta hai (frontend periodic
            auto-save `{"pages": [...]}` yahi bhejta hai).
    """
    permission_classes = [IsAuthenticated]

    def _get_conversation(self, request, conversation_id):
        return Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user
        ).first()

    def get(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        room_state = StudyRoomState.objects.filter(conversation=conversation).first()
        if not room_state:
            return Response({"pages": []})
        return Response(room_state.state or {"pages": []})

    def put(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        StudyRoomState.objects.update_or_create(
            conversation=conversation,
            defaults={"state": request.data, "updated_by": request.user},
        )
        return Response({"detail": "saved"})

    # 🔥 NAYA — `endStudyRoomState()` (frontend) is par depend karta hai
    # taaki room close hote hi poora whiteboard clear ho jaaye. Pehle ye
    # method hi missing tha, isliye DELETE call 405 deta tha.
    def delete(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        StudyRoomState.objects.filter(conversation=conversation).delete()
        return Response({"detail": "cleared"})


# ======================================================================
# DEVICE TOKENS
# ======================================================================
class DeviceTokenView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = request.data.get('token')
        platform = request.data.get('platform', 'android')
        if not token:
            return Response({'detail': "'token' required hai"}, status=400)

        DeviceToken.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'platform': platform},
        )
        return Response({'detail': 'Device registered'})

    def delete(self, request):
        token = request.data.get('token')
        if token:
            DeviceToken.objects.filter(token=token, user=request.user).delete()
        return Response({'detail': 'Device unregistered'})