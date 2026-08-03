import uuid
import os
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
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
    Group,
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
    GroupMediaSerializer,
    GroupMemberSerializer,
    GroupSerializer,
    MessageCreateSerializer,
    MessageReactionSerializer,
    MessageSerializer,
    UserPresenceSerializer,
)
from .push_utils import send_push_to_users, send_chat_message_push, send_incoming_call_push
from .livekit_utils import generate_livekit_token
from .user_display import get_display_name, get_profile_photo_url

# LiveKit URL env se lo, nahi to default
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "ws://10.93.221.189:7880")

User = get_user_model()


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

    @action(detail=True, methods=['get', 'post'], url_path='messages')
    def messages(self, request, pk=None):
        conversation = self.get_object()

        if request.method == 'GET':
            qs = conversation.all_messages.select_related(
                'sender', 'reply_to', 'reply_to__sender'
            ).prefetch_related('all_reactions', 'all_reactions__user')
            paginator = MessagePagination()
            page = paginator.paginate_queryset(qs, request, view=self)
            serializer = MessageSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        client_id = serializer.validated_data.get('client_id')
        if client_id:
            existing = conversation.all_messages.filter(sender=request.user, client_id=client_id).first()
            if existing:
                return Response(MessageSerializer(existing, context={'request': request}).data)

        with transaction.atomic():
            message = serializer.save(conversation=conversation, sender=request.user)

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

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation.id}',
            {
                'type': 'chat_message',
                'event': 'message',
                'id': str(message.id),
                'conversation_id': str(conversation.id),
                'sender_id': str(request.user.id),
                'message_type': message.type,
                'text': message.text,
                'file_url': message.file_url,
                'file_urls': message.file_urls,
                'thumbnail_url': message.thumbnail_url,
                'meta': message.meta,
                'reply_to': str(message.reply_to.id) if message.reply_to else None,
                'client_id': client_id,
                'created_at': message.created_at.isoformat(),
            }
        )

        other_recipients = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user=request.user)
            .values_list('user_id', flat=True)
        )
        if other_recipients:
            sender_name = get_display_name(request.user)
            send_chat_message_push(
                recipient_ids=other_recipients,
                sender_name=sender_name,
                message_text=message.text,
                message_type=message.type,
                conversation_id=conversation.id,
                message_id=message.id
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
        if self.action in ('update', 'partial_update', 'add_members'):
            return [IsAuthenticated(), IsGroupAdminOrModerator()]
        return [IsAuthenticated()]

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
                created_by=request.user,
            )

            member_ids = {str(uid) for uid in data.get('member_ids', [])}
            member_ids.discard(str(request.user.id))
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
        user_ids = request.data.get('user_ids', [])
        if not user_ids:
            return Response({'detail': "'user_ids' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        existing_ids = set(str(uid) for uid in group.group_members.values_list('user_id', flat=True))
        new_ids = [uid for uid in user_ids if str(uid) not in existing_ids]
        users = User.objects.filter(id__in=new_ids)

        with transaction.atomic():
            for user in users:
                ConversationParticipant.objects.get_or_create(conversation=group.conversation, user=user)
                GroupMember.objects.get_or_create(group=group, user=user, defaults={'added_by': request.user})
            Group.objects.filter(id=group.id).update(
                members_count=group.group_members.filter(is_banned=False).count()
            )

        return Response(GroupSerializer(group, context={'request': request}).data)

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
        for field in ('role', 'is_muted', 'is_banned'):
            if field in request.data:
                setattr(membership, field, request.data[field])
        membership.save()
        return Response(GroupMemberSerializer(membership, context={'request': request}).data)

    @staticmethod
    def _require_admin(group_id, user):
        allowed = GroupMember.objects.filter(
            group_id=group_id, user=user, role__in=['admin', 'moderator'], is_banned=False,
        ).exists()
        if not allowed:
            raise PermissionDenied('Sirf group admin/moderator ye action kar sakte hain.')

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
    serializer_class = BlockedUserSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return BlockedUser.objects.filter(blocker=self.request.user).select_related('blocked')

    def perform_create(self, serializer):
        blocked_user = serializer.validated_data['blocked']
        if blocked_user.id == self.request.user.id:
            raise ValidationError('Khud ko block nahi kar sakte.')
        serializer.save(blocker=self.request.user)


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

        other_members = list(conversation.memberships.exclude(user=request.user).values_list('user_id', flat=True))
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

    def post(self, request, call_id):
        action = request.data.get('action')
        call = CallSession.objects.filter(id=call_id).first()
        if not call:
            return Response({"detail": "Call not found"}, status=404)

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
            CallParticipant.objects.filter(call=call, user=request.user).update(left_at=timezone.now(), status=CallStatus.ENDED)
            if not CallParticipant.objects.filter(call=call, left_at__isnull=True).exists():
                call.status = CallStatus.ENDED
                call.ended_at = timezone.now()
                if call.connected_at:
                    call.duration_seconds = int((call.ended_at - call.connected_at).total_seconds())
                call.save(update_fields=['status', 'ended_at', 'duration_seconds'])

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

        room_name = f"study_{conversation_id}"
        user_name = get_display_name(request.user)
        token = generate_livekit_token(
            room_name=room_name,
            user_id=request.user.id,
            user_name=user_name,
        )

        return Response({
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": token,
            "room_name": room_name,
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