import uuid
import os
import secrets
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
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
    GroupMedia,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    Poll,
    PollOption,
    PollVote,
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
    MessageReadStatusSerializer,
    MessageSearchResultSerializer,
    MessageSerializer,
    PollCreateSerializer,
    PollSerializer,
    PollVoteSerializer,
    ScheduleMessageSerializer,
    UserMiniSerializer,
    UserPresenceSerializer,
    ReadReceiptSettingsSerializer,
)
from .push_utils import (
    send_push_to_users, send_chat_message_push, send_incoming_call_push,
    send_call_cancelled_push, send_mention_push,
)
from .livekit_utils import generate_livekit_token
from .user_display import build_user_mini, get_display_name, get_profile_photo_url
from .group_rules import check_group_permission, check_daily_message_limit, is_group_admin_or_mod
from .cache_utils import invalidate_group_role_cache, get_presence_cached, set_presence_cache
from .mentions import extract_mentioned_user_ids
from .media_utils import create_group_media_for_message
from .constants import MAX_PINNED_PER_CONVERSATION
# 🔧 GAP FIX — `search()` / `search_all()` below already called
# `search_utils.MIN_QUERY_LENGTH` / `.apply_structured_filters(...)` /
# `.search_messages(...)`, but this module was never imported here (and,
# until this session, never existed at all) — every call to either search
# endpoint was a guaranteed `NameError`. See `search_utils.py`.
from . import search_utils
# 🔥 NAYE — advanced features (link preview / auto voice-transcription),
# dono `tasks.py` ke Celery tasks hain, seedha yahan se `.delay()` hota hai.
from .tasks import generate_link_preview_task, transcribe_voice_message_task
# 🔥 FIX — throttles.py already existed with full "how to wire this up"
# instructions in its own docstring (MessageSendThrottle /
# CallInitiateThrottle / GroupCreateThrottle / ReactionThrottle), but none
# of it was ever actually imported/used in views.py — every REST write
# endpoint it was meant to protect stayed unthrottled. Only the WS path
# (`WSMessageRateLimiter`, in consumers.py) was ever wired up. Doing the
# REST half now, exactly per that file's own setup comment.
from .throttles import (
    MessageSendThrottle, CallInitiateThrottle, GroupCreateThrottle, ReactionThrottle,
    MessageSendIPThrottle, CallInitiateIPThrottle,
)

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


# 🔥 NAYA — PERF FIX. `MessageSerializer.get_is_starred`/`get_is_read_by_me` pehle
# HAR message row ke liye alag DB query karte the (`obj.starred_by.filter(...)
# .exists()` / `MessageStatus.objects.filter(...).exists()`) — 30-message page pe
# ye 2 extra query-per-row = 60 extra queries ek single list-load ke liye.
#
# Fix: is user ke liye already-narrowed `Prefetch` — poori list ke liye sirf 2
# EXTRA queries total (starred_by ke liye 1, delivery_status ke liye 1), row-count
# se independent. Serializer `obj.my_star.exists()`/`obj.my_read_status` (list,
# already Python-side filtered by prefetch) check karta hai instead of naya query
# maarne ke — `to_attr` isi liye diya hai taaki `.all()` cache use ho, `.filter()`
# nahi (jo prefetch cache ko bypass kar deta hai).
#
# Har jagah use karo jahan `MessageSerializer(qs_or_page, many=True, ...)` call
# hoti hai (list ke liye) — single-object responses (`MessageSerializer(message,
# ...)`) ko iski zaroorat nahi, wahan 1 query already theek hai.
def with_message_list_prefetch(queryset, user):
    return queryset.prefetch_related(
        Prefetch('starred_by', queryset=User.objects.filter(id=user.id), to_attr='my_star'),
        Prefetch(
            'delivery_status',
            queryset=MessageStatus.objects.filter(user=user, is_read=True),
            to_attr='my_read_status',
        ),
    )


# ======================================================================
# CONVERSATIONS
# ======================================================================
class ConversationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConversationListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = Conversation.objects.filter(
            memberships__user=self.request.user, memberships__left_at__isnull=True,
        ).distinct().select_related('group_detail', 'last_message_sender').prefetch_related(
            # 🔥 FIX (N+1) — see `ConversationListSerializer._membership`'s
            # own comment: this used to be re-queried (twice!) per row by
            # the serializer. One prefetch here = one extra query for the
            # WHOLE page instead of up to 2*N.
            Prefetch(
                'memberships',
                queryset=ConversationParticipant.objects.filter(user=self.request.user),
                to_attr='my_membership_list',
            )
        ).order_by('-last_message_at', '-created_at')

        # ⚠️ IMPORTANT: filters below apply ONLY to `list` — every detail
        # action (`messages`, `settings`, `disappearing_messages`, ...)
        # also resolves through `get_object()` -> this same `get_queryset()`.
        # If the archived-exclude default applied there too, opening an
        # archived chat's messages (or any detail action on it) would 404
        # for its own owner — a real regression, not just an unused filter.
        if self.action != 'list':
            return qs

        # 🔥 NAYA — chat-list ab filterable hai. Pehle `is_archived`/
        # `is_pinned`/`is_muted` sirf per-user settings the (PATCH
        # `/settings/` se set hote the) par list endpoint unhe kabhi filter
        # nahi karta tha — matlab "Archived Chats" jaisi standard chat-app
        # screen backend me possible hi nahi thi (frontend ko client-side
        # poore list se chhaanna padta, jo scale pe galat/inefficient hai).
        # Default behavior WhatsApp jaisa: archived chats normal list se
        # bahar (jab tak explicitly `?archived=true` na maanga jaaye).
        #
        #   GET /conversations/                -> archived chhod ke sab
        #   GET /conversations/?archived=true   -> sirf archived
        #   GET /conversations/?pinned=true     -> sirf pinned
        #   GET /conversations/?muted=true      -> sirf muted
        #   GET /conversations/?unread=true     -> sirf jinme unread > 0
        # NOTE: `unique_together = ('conversation', 'user')` on
        # ConversationParticipant means there's at most one membership row
        # per (conversation, me) pair — so scoping every extra condition
        # below with `memberships__user=self.request.user` in its own
        # `.filter()` call still lands on that same single row even though
        # each call is its own join; kept as separate calls for readable
        # optional filters instead of one big conditional dict.
        params = self.request.query_params
        me = self.request.user
        archived_param = params.get('archived')
        if archived_param is not None:
            qs = qs.filter(memberships__user=me, memberships__is_archived=(archived_param.lower() == 'true'))
        else:
            qs = qs.exclude(memberships__user=me, memberships__is_archived=True)

        if (pinned := params.get('pinned')) is not None:
            qs = qs.filter(memberships__user=me, memberships__is_pinned=(pinned.lower() == 'true'))

        if (muted := params.get('muted')) is not None:
            qs = qs.filter(memberships__user=me, memberships__is_muted=(muted.lower() == 'true'))

        if params.get('unread') == 'true':
            qs = qs.filter(memberships__user=me, memberships__unread_count__gt=0)

        return qs

    def get_serializer_context(self):
        return {'request': self.request}

    # 🔥 FIX — see throttles.py's own setup comment: message-send spam/abuse
    # guard was written but never actually applied here.
    def get_throttles(self):
        if self.action == 'messages' and self.request.method == 'POST':
            # 🔥 NAYA — per-user (`MessageSendThrottle`) ke SAATH per-IP
            # safety net bhi (`MessageSendIPThrottle`). DRF dono list me hon
            # to dono check karta hai — jo bhi pehle trip ho, request block.
            return [MessageSendThrottle(), MessageSendIPThrottle()]
        return super().get_throttles()

    # 🔥 NAYA — GET /conversations/unread-count/
    # App-icon/bottom-nav badge ke liye ek hi lightweight number chahiye
    # hota hai — pehle iske liye frontend ko poori paginated chat-list
    # fetch karke client-side sum karna padta (galat approach: page size
    # se bada total ho to ye ganit hi galat aa jaata, aur ek extra bhaari
    # request hai sirf ek number ke liye). Ye single aggregate query hai —
    # koi row-level data wapas nahi jaati.
    #   GET /conversations/unread-count/               -> muted included
    #   GET /conversations/unread-count/?exclude_muted=true -> muted skip
    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        # `left_at__isnull=True` is the app's actual "still in this chat"
        # signal (see `bulk_delete`/leave-group above — a deleted/left chat
        # sets `left_at`, `Conversation` itself is never soft-deleted).
        qs = ConversationParticipant.objects.filter(user=request.user, left_at__isnull=True)
        if str(request.query_params.get('exclude_muted', '')).lower() == 'true':
            qs = qs.exclude(is_muted=True)
        total = qs.aggregate(total=Sum('unread_count'))['total'] or 0
        return Response({'unread_count': total})

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
            # 🔥 FIX — pehle yahan is check ka apna alag raw
            # `GroupMember.objects.filter(...)` query tha, jabki
            # `group_rules.is_group_admin_or_mod` (ab cached) exact
            # same rule already implement karta hai. Teen jagah (yahan,
            # `add_participant_to_conversation`, `GroupViewSet.
            # _require_admin`) same "admin/mod, not banned" logic
            # independently duplicate thi — single source pe unify kiya.
            if not is_group_admin_or_mod(conversation.group_detail, request.user.id):
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
            # 🔥 FIX — same unification as `disappearing_messages` above.
            if not is_group_admin_or_mod(group, request.user.id):
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
            ).exclude(
                # 🔧 GAP FIX — `is_scheduled`/`scheduled_for` fields already
                # existed on the model, but nothing filtered them out of the
                # normal timeline — a "send later" message would've shown up
                # to every participant immediately, defeating the whole
                # point. Scheduled-but-not-yet-sent messages are visible
                # only via `schedule_message`/`scheduled_messages_list`
                # (sender-only) until `send_scheduled_messages` delivers them.
                is_scheduled=True,
            ).select_related(
                'sender', 'reply_to', 'reply_to__sender'
            ).prefetch_related(
                'all_reactions', 'all_reactions__user',
                # 🔥 NAYA — poll messages ke liye. Ek chat me poll rare hote
                # hain (star/is_read jaisa hi chhota per-row query rehta agar
                # prefetch na hota), lekin jab hote hain to option/vote count
                # ke liye har poll message pe 1+N option query lagti, isliye
                # yahi prefetch kar dena behtar hai (bade groups me poll
                # zyada options ke saath ho to farak padta hai).
                'poll', 'poll__options', 'poll__options__votes',
            )
            qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — see helper above
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

            # 🔥 FIX — REST se message bhejte waqt `MessageStatus` rows
            # (delivery/read tracking) pehle kabhi nahi banti thi — sirf WS
            # `ChatConsumer.save_message` ye karta tha. Har media/location
            # message (jo hamesha REST se jaate hain, WS text-first flow
            # ke against) is wajah se kabhi bhi "delivered" (double-tick)
            # state nahi dikhata tha jab tak recipient use actively read na
            # kar de — kyunki `mark_undelivered_as_delivered` (WS connect())
            # sirf EXISTING rows ko update karta hai, naya row nahi banata.
            # Ab dono paths consistent hain.
            other_participant_ids = list(
                ConversationParticipant.objects.filter(conversation=conversation)
                .exclude(user=request.user)
                .values_list('user_id', flat=True)
            )
            MessageStatus.objects.bulk_create(
                [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
                ignore_conflicts=True,
            )

            # 🔥 NAYA — @mentions resolve karke message pe attach karo.
            # Sirf conversation ke ACTIVE members hi mention ho sakte hain.
            mentioned_ids = extract_mentioned_user_ids(message.text, conversation)
            mentioned_ids = [uid for uid in mentioned_ids if uid != request.user.id]
            if mentioned_ids:
                message.mentioned_users.set(mentioned_ids)

            # 🔥 BUG FIX — group gallery ke liye GroupMedia row (pehle
            # kahin bhi nahi banti thi, see media_utils.py).
            create_group_media_for_message(message)

        # 🔥 NAYE (ADVANCED FEATURES) — dono background/async chalte hain
        # (Celery `.delay()`), request ko bilkul block nahi karte. Transaction
        # commit ho chuke hone ke BAAD enqueue kiye hain (with-block ke bahar)
        # taaki worker ko guaranteed committed row mile, race condition na ho.
        if message.type == MessageType.TEXT and message.text:
            generate_link_preview_task.delay(str(message.id))
        elif message.type == MessageType.AUDIO and message.file_url:
            transcribe_voice_message_task.delay(str(message.id))

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

    # ======================================================================
    # 🔥 NAYA — POLL MESSAGES (WhatsApp-style group poll)
    # ======================================================================
    # POST /message/conversations/<id>/poll/
    #   body: {"question": "...", "options": ["A", "B", ...], "allow_multiple_answers": false}
    #
    # Ek poll = ek naya `Message` (type=POLL, text=question) + ek `Poll`
    # row + N `PollOption` rows. Same permission/block/throttle checks
    # jaise normal `messages()` POST (group message_permission +
    # daily_message_limit, private-chat block-check) — koi bhi jo normal
    # text message nahi bhej sakta, poll bhi nahi bhej sakega.
    @action(detail=True, methods=['post'], url_path='poll')
    def create_poll(self, request, pk=None):
        conversation = self.get_object()

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se poll nahi bheja ja sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)
                allowed, reason = check_daily_message_limit(group, request.user, conversation)
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        serializer = PollCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        disappearing_delta = conversation.get_disappearing_timedelta()
        expires_at = (timezone.now() + disappearing_delta) if disappearing_delta else None

        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation, sender=request.user,
                type=MessageType.POLL, text=data['question'], expires_at=expires_at,
            )
            poll = Poll.objects.create(
                message=message, question=data['question'],
                allow_multiple_answers=data['allow_multiple_answers'],
            )
            PollOption.objects.bulk_create([
                PollOption(poll=poll, text=opt_text, order=i)
                for i, opt_text in enumerate(data['options'])
            ])

            preview_text = f"📊 {data['question']}"[:500]
            conversation.last_message_text = preview_text
            conversation.last_message_at = message.created_at
            conversation.last_message_sender = request.user
            conversation.last_message_type = message.type
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])

            other_participant_ids = list(
                ConversationParticipant.objects.filter(conversation=conversation)
                .exclude(user=request.user)
                .values_list('user_id', flat=True)
            )
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_participant_ids,
            ).update(unread_count=F('unread_count') + 1)

            MessageStatus.objects.bulk_create(
                [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
                ignore_conflicts=True,
            )

        sender = build_user_mini(request.user)
        poll_data = PollSerializer(poll, context={'request': request}).data
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
                'poll': poll_data,
                'reply_to': None,
                'client_id': None,
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
                    'sender_id': str(request.user.id),
                    'sender_name': sender['display_name'],
                    'last_message_text': preview_text,
                    'last_message_type': message.type,
                    'created_at': message.created_at.isoformat(),
                }
            )

        muted_user_ids = set(
            ConversationParticipant.objects.filter(
                conversation=conversation, user_id__in=other_participant_ids, is_muted=True,
            ).values_list('user_id', flat=True)
        )
        push_recipients = [uid for uid in other_participant_ids if uid not in muted_user_ids]
        if push_recipients:
            send_chat_message_push(
                recipient_ids=push_recipients,
                sender_name=sender['display_name'],
                message_text=preview_text,
                message_type=message.type,
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
    # GET /message/conversations/<id>/search/?q=...&sender=<id>&date_from=
    #     ...&date_to=...&has_media=true|false
    #
    # 🔥 UPGRADED (this session) — pehle plain `icontains` tha. Ab Postgres
    # full-text search (ranked, stemmed) + trigram similarity (typo-
    # tolerant) `search_utils.search_messages()` se, plus sender/date-
    # range/has_media filters. `conversation` FK pe already index hai to
    # ye query ek conversation tak hi scoped rehti hai; global search
    # `search_all` neeche hai. Deleted/expired/scheduled messages exclude
    # karte hain, jaisa normal message-list me hota hai.
    @action(detail=True, methods=['get'], url_path='search')
    def search(self, request, pk=None):
        conversation = self.get_object()
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < search_utils.MIN_QUERY_LENGTH:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = conversation.all_messages.filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).exclude(
            is_scheduled=True,  # 🔧 GAP FIX — see note in `messages` GET above
        ).select_related('sender', 'reply_to')

        qs, filter_error = search_utils.apply_structured_filters(qs, request.query_params)
        if filter_error:
            return Response({'detail': filter_error}, status=status.HTTP_400_BAD_REQUEST)

        qs = search_utils.search_messages(qs, query)
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def

        paginator = MessagePagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = MessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)

    # ==================================================================
    # SEARCH — sabhi conversations me ek saath (global chat search)
    # ==================================================================
    # GET /message/conversations/search_all/?q=...&sender=<id>&date_from=
    #     ...&date_to=...&has_media=true|false
    #
    # Ye `detail=False` hai isliye alag URL `search_all/` par baithta hai
    # (router `search/` already `search` action ke through detail route
    # pe bana chuka hai). Sirf un conversations ke messages aate hain jinka
    # user abhi ACTIVE member hai (chat delete/leave kar chuka ho to us
    # conversation ke results nahi aayenge). 🔥 UPGRADED (this session) —
    # same ranked/typo-tolerant search + filters as the in-conversation
    # search above. `sender` filters by sender across ALL conversations —
    # combine with a specific conversation's own `/search/` endpoint if
    # you want "messages from X in THIS chat only".
    @action(detail=False, methods=['get'], url_path='search_all')
    def search_all(self, request):
        query = (request.query_params.get('q') or '').strip()
        if not query:
            return Response({'detail': "'q' query param required hai."}, status=status.HTTP_400_BAD_REQUEST)
        if len(query) < search_utils.MIN_QUERY_LENGTH:
            return Response({'detail': 'Search kam se kam 2 characters ka hona chahiye.'}, status=status.HTTP_400_BAD_REQUEST)

        qs = Message.objects.filter(
            conversation__memberships__user=request.user,
            conversation__memberships__left_at__isnull=True,
        ).filter(
            Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        ).exclude(
            deleted_for_everyone=True,
        ).exclude(
            deleted_for_users=request.user,
        ).exclude(
            is_scheduled=True,  # 🔧 GAP FIX — see note in `messages` GET above
        ).select_related(
            'sender', 'conversation', 'conversation__group_detail',
        ).distinct()

        # 🔧 NOTE — `conversation_id` scope param, so the client can reuse
        # this one global endpoint instead of needing two code paths, e.g.
        # "search everywhere" vs "search in this chat" with the same UI.
        conversation_id = request.query_params.get('conversation_id')
        if conversation_id:
            qs = qs.filter(conversation_id=conversation_id)

        qs, filter_error = search_utils.apply_structured_filters(qs, request.query_params)
        if filter_error:
            return Response({'detail': filter_error}, status=status.HTTP_400_BAD_REQUEST)

        qs = search_utils.search_messages(qs, query)
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def

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
        qs = with_message_list_prefetch(qs, request.user)  # 🔥 NAYA — perf, see helper def
        serializer = MessageSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    # ==================================================================
    # 🔥 NAYA (ADVANCED FEATURE) — SCHEDULED MESSAGES / "SEND LATER"
    # ==================================================================
    # Model pe `Message.is_scheduled` / `scheduled_for` fields already the
    # (kisi purani session me add hue the) lekin end-to-end kahin bhi wire
    # nahi the — na koi endpoint inhe set karta tha, na koi job inhe
    # actually deliver karta tha, na normal message-list inhe hide karta
    # tha. Ye poora feature ab yahan complete hai:
    #
    #   POST /message/conversations/<id>/schedule-message/
    #        body = MessageCreateSerializer ke saare fields + "scheduled_for"
    #        (future ISO datetime). Message DB me ban jaata hai lekin
    #        `is_scheduled=True` ke saath — is wajah se `messages` GET /
    #        `search` / `search_all` me kisi ko bhi (sender samet, dusre
    #        screen pe) nahi dikhta jab tak bhej na diya jaaye. Koi
    #        broadcast/push/unread-count yahan NAHI hota — wo sab
    #        `scheduled_messages.finalize_scheduled_message()` karta hai,
    #        jab `send_scheduled_messages` management command (cron/
    #        Celery-beat se har minute chalao) isko pick karta hai.
    #   GET  /message/conversations/<id>/scheduled-messages/
    #        sirf apne bheje hue, abhi tak pending scheduled messages is
    #        chat ke — taaki UI me "Scheduled" tab dikha sako.
    #   DELETE/PATCH /message/messages/<id>/schedule/ (MessageViewSet)
    #        cancel karo ya reschedule/text-edit karo, jab tak bhej na
    #        diya ho.
    #
    # Permission checks (block / group message_permission) yahan bhi
    # utni hi lagti hain jitni normal send me — lekin `daily_message_limit`
    # ka count actual-send-time pe stale ho sakta hai (schedule karte
    # waqt limit ke andar tha, ab tak limit cross ho chuki ho) — ye ek
    # chhota known edge-case hai, WhatsApp/Gmail bhi isi tarah handle
    # karte hain (schedule-time pe hi check, deliver-time pe nahi).
    @action(detail=True, methods=['post'], url_path='schedule-message')
    def schedule_message(self, request, pk=None):
        conversation = self.get_object()

        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {'detail': 'Block hone ki wajah se message schedule nahi ho sakta.'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'message_permission')
                if not allowed:
                    return Response({'detail': reason}, status=status.HTTP_403_FORBIDDEN)

        serializer = ScheduleMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = serializer.save(
            conversation=conversation,
            sender=request.user,
            is_scheduled=True,
            # `expires_at` (disappearing messages) jaan-boojh kar yahan
            # NAHI compute karte — conversation ki duration setting badal
            # sakti hai schedule aur actual-send ke beech; asli send-time
            # (finalize_scheduled_message) pe hi snapshot lena sahi hai,
            # jaisa normal messages ke liye already hota hai.
        )
        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='scheduled-messages')
    def scheduled_messages_list(self, request, pk=None):
        conversation = self.get_object()
        qs = conversation.all_messages.filter(
            is_scheduled=True, sender=request.user,
        ).order_by('scheduled_for')
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
        from .models import UserPresence

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
    @action(detail=True, methods=['post'], url_path='poll/vote')
    def poll_vote(self, request, pk=None):
        message = self.get_object()
        if message.type != MessageType.POLL:
            return Response({'detail': 'Ye poll message nahi hai.'}, status=status.HTTP_400_BAD_REQUEST)

        poll = getattr(message, 'poll', None)
        if not poll:
            return Response({'detail': 'Poll data nahi mila.'}, status=status.HTTP_404_NOT_FOUND)
        if poll.is_closed:
            return Response({'detail': 'Ye poll band ho chuka hai, ab vote nahi ho sakta.'}, status=status.HTTP_400_BAD_REQUEST)

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
        # 🔥 NAYA — poll messages bhi exclude kiye hain: forward yahan
        # sirf plain field-copy karta hai (text/file_url/meta), poll ka
        # asal data alag `Poll`/`PollOption` rows me hota hai jo copy nahi
        # hote — forward karne se ek "POLL type ka message bina Poll data
        # ke" ban jaata, jo client pe crash/blank card dikhata. Poll
        # forward abhi supported nahi hai (future: naya Poll+options
        # explicitly clone karna padega, sirf Message field-copy se nahi).
        source_messages = list(
            Message.objects.filter(id__in=message_ids, conversation__memberships__user=request.user)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .exclude(deleted_for_everyone=True)
            .exclude(deleted_for_users=request.user)
            .exclude(type=MessageType.POLL)
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
                        # Caption sirf tab lagti hai jab source ke paas
                        # khud koi non-empty text nahi tha (media/location
                        # message) — text message ka apna text hamesha
                        # priority pe rehta hai, caption tab silently
                        # ignore ho jaati hai.
                        text=(caption if (caption and not (src.text or '').strip()) else src.text),
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

    # 🔥 FIX — mass-group-creation spam guard (`GroupCreateThrottle`)
    # existed in throttles.py but was never wired in.
    def get_throttles(self):
        if self.action == 'create':
            return [GroupCreateThrottle()]
        return super().get_throttles()

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

        # 🔥 FIX — cache_utils.py's own setup docstring explicitly names
        # `add_members` as a required invalidation call-site (alongside
        # `update_member`/`approve_join_request`/member-remove), but it was
        # never added here. Without it, a user who was checked (and cached
        # as "not a member") just before being added here — e.g. by trying
        # an admin-only action — could keep reading as a non-member for up
        # to the 60s TTL.
        for user in users:
            invalidate_group_role_cache(group.id, user.id)

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
            # 🔥 FIX — same class of gap as `add_members`/`approve_join_request`.
            invalidate_group_role_cache(group.id, request.user.id)
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

        # 🔥 FIX — same gap as `add_members` above; cache_utils.py's setup
        # docstring names `approve_join_request` explicitly too.
        invalidate_group_role_cache(group.id, join_request.user_id)

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
        # 🔥 FIX — pehle yahan is check ka apna alag raw query tha
        # (`GroupMember.objects.filter(...)`), jabki `group_rules.
        # is_group_admin_or_mod` bhi EXACT same rule (admin/mod, not
        # banned) implement karta hai. Do independent implementations ek
        # hi rule ki — bilkul wahi class ka risk jo `MAX_PINNED_PER_
        # CONVERSATION` pehle duplicate hone se hua tha (dono kabhi
        # silently drift kar sakte the). Ab dono ek hi (cached) function
        # use karte hain — single source of truth.
        from .models import Group
        group = Group.objects.get(id=group_id)
        if not is_group_admin_or_mod(group, user.id):
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
            # 🔥 FIX — member remove hone ke baad (khaaskar agar wo khud
            # admin/mod tha) is_group_admin_or_mod cache ab stale ho gayi
            # hai (member row hi delete ho gaya, par cache abhi bhi purana
            # role/is_banned dikha sakti thi TTL khatam hone tak). Turant
            # invalidate karo taaki access-check turant sahi reflect kare.
            invalidate_group_role_cache(group.id, user_id)
            return Response(status=status.HTTP_204_NO_CONTENT)

        self._require_admin(group.id, request.user)
        was_banned = membership.is_banned
        for field in ('role', 'is_muted', 'is_banned'):
            if field in request.data:
                setattr(membership, field, request.data[field])
        membership.save()

        # 🔥 CRITICAL FIX — role ya is_banned change hone ke baad group-role
        # cache (`cache_utils.get_group_role_cached`, `group_rules.
        # is_group_admin_or_mod` ke peeche) turant invalidate karna ZAROORI
        # hai. Bina is call ke, ek demote/ban kiye gaye admin ke paas agle
        # 60s (cache TTL) tak bhi pin/message/call/study-room jaisi
        # admin-only actions ka access reh sakta tha — cache add karne se
        # pehle ye gap exist hi nahi karta tha (har check seedha DB se
        # hota tha), isliye ye is session ki caching change ka direct
        # side-effect hai aur usi ke saath fix hona chahiye tha.
        invalidate_group_role_cache(group.id, user_id)

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

    # 🔥 FIX — cache_utils.py's own docstring says this view is meant to be
    # one of the two read sites for `get_presence_cached` (the other being
    # `ChatConsumer`'s presence update), but it was never wired in — every
    # single presence check (chat header "online"/"last seen", opening a
    # user's profile, etc.) was hitting the DB directly, defeating the
    # point of a 15s-TTL cache for a value that changes this often.
    def get_object(self):
        from .models import UserPresence
        user_id = self.kwargs['user_id']

        cached = get_presence_cached(user_id)
        if cached is not None:
            # Build a lightweight, unsaved UserPresence so the existing
            # ModelSerializer (which needs `.user` for the nested
            # UserMiniSerializer) can render it without writing to the DB
            # just to serve a read.
            user = get_object_or_404(User, id=user_id)
            return UserPresence(
                user=user,
                is_online=cached['is_online'],
                last_seen_at=parse_datetime(cached['last_seen_at']) if cached['last_seen_at'] else None,
            )

        presence, _ = UserPresence.objects.select_related('user').get_or_create(user_id=user_id)
        set_presence_cache(user_id, presence.is_online, presence.last_seen_at)
        return presence


# 🔥 NAYA — apni read-receipt privacy setting dekhna/badalna.
#   GET   /message/presence/read-receipts/  -> {"show_read_receipts": true|false}
#   PATCH /message/presence/read-receipts/  body: {"show_read_receipts": false}
# Hamesha `request.user` par operate karta hai — koi `user_id` param nahi,
# isliye koi bhi doosre ka toggle change nahi kar sakta. Effect turant
# `MessageViewSet.read_status` pe lagu ho jaata hai (koi cache invalidate
# karne ki zaroorat nahi — wo action hamesha live query karta hai).
class ReadReceiptSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .models import UserPresence
        presence, _ = UserPresence.objects.get_or_create(user=request.user)
        return Response(ReadReceiptSettingsSerializer(presence).data)

    def patch(self, request):
        from .models import UserPresence
        presence, _ = UserPresence.objects.get_or_create(user=request.user)
        serializer = ReadReceiptSettingsSerializer(presence, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


# ======================================================================
# CALLS
# ======================================================================
class CallInitiateView(APIView):
    permission_classes = [IsAuthenticated]
    # 🔥 FIX — `CallInitiateThrottle` existed in throttles.py (calls are
    # more "costly" than a normal message: each one creates an FCM push +
    # a LiveKit room) but was never applied here.
    throttle_classes = [CallInitiateThrottle, CallInitiateIPThrottle]

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