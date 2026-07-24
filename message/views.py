from django.shortcuts import render

# Create your views here.
# message/views.py
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

from .models import (
    BlockedUser,
    CallSession,
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
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

User = get_user_model()


class MessagePagination(PageNumberPagination):
    """Chat history ke liye — 30 messages per page (newest-first, model Meta ordering se)."""
    page_size = 30
    page_size_query_param = 'page_size'
    max_page_size = 100


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50


# ======================================================================
# CONVERSATIONS  (chat list + messages sub-resource)
# ======================================================================
class ConversationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    GET   /api/conversations/                    -> mera chat list
    GET   /api/conversations/{id}/                -> ek conversation detail
    POST  /api/conversations/start_private/       -> {"user_id": "..."} 1-1 chat start/open
    PATCH /api/conversations/{id}/settings/       -> {"is_muted"/"is_archived"/"is_pinned": true}
    GET   /api/conversations/{id}/messages/       -> is chat ke messages (paginated)
    POST  /api/conversations/{id}/messages/       -> naya message bhejo (REST fallback)
    POST  /api/conversations/{id}/read_all/       -> saare unread messages read mark karo
    """
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
        conversation = self.get_object()  # get_queryset() already scopes to my conversations

        if request.method == 'GET':
            qs = conversation.all_messages.select_related(
                'sender', 'reply_to', 'reply_to__sender'
            ).prefetch_related('all_reactions', 'all_reactions__user')
            paginator = MessagePagination()
            page = paginator.paginate_queryset(qs, request, view=self)
            serializer = MessageSerializer(page, many=True, context={'request': request})
            return paginator.get_paginated_response(serializer.data)

        # POST -> naya message (real-time ke liye websocket /ws/chat/ preferred hai,
        # ye REST endpoint fallback / bots / server-to-server ke liye hai)
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

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], url_path='read_all')
    def read_all(self, request, pk=None):
        conversation = self.get_object()
        now = timezone.now()

        for message in conversation.all_messages.exclude(sender=request.user):
            MessageStatus.objects.update_or_create(
                message=message, user=request.user,
                defaults={'is_read': True, 'read_at': now, 'is_delivered': True},
            )

        ConversationParticipant.objects.filter(conversation=conversation, user=request.user).update(
            unread_count=0, last_read_at=now,
        )
        return Response({'detail': 'Saare messages read mark ho gaye.'})


# ======================================================================
# MESSAGES  (edit / delete / react / read on a single message)
# ======================================================================
class MessageViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    GET    /api/messages/{id}/                    -> ek message
    PATCH  /api/messages/{id}/                     -> {"text": "..."} edit (sirf sender)
    DELETE /api/messages/{id}/?for_everyone=true   -> delete for everyone / for me
    POST   /api/messages/{id}/react/               -> {"emoji": "🔥"} reaction add/update
    DELETE /api/messages/{id}/react/               -> apni reaction hatao
    POST   /api/messages/{id}/read/                -> read receipt mark karo
    """
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
    """
    POST   /api/groups/                          -> naya group banao
    GET    /api/groups/{id}/                      -> group detail
    PATCH  /api/groups/{id}/                      -> group info update (admin/mod)
    POST   /api/groups/{id}/members/              -> {"user_ids": [...]} members add karo
    PATCH  /api/groups/{id}/members/{user_id}/    -> {"role"/"is_muted"/"is_banned"} (admin/mod)
    DELETE /api/groups/{id}/members/{user_id}/    -> remove member (admin/mod) ya khud leave
    GET    /api/groups/{id}/media/                -> group gallery (photos/videos/files)
    """
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

        return Response(GroupSerializer(group).data, status=status.HTTP_201_CREATED)

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

        return Response(GroupSerializer(group).data)

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

        # PATCH -> role / mute / ban update, sirf admin/moderator
        self._require_admin(group.id, request.user)
        for field in ('role', 'is_muted', 'is_banned'):
            if field in request.data:
                setattr(membership, field, request.data[field])
        membership.save()
        return Response(GroupMemberSerializer(membership).data)

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
        serializer = GroupMediaSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)


# ======================================================================
# BLOCKED USERS
# ======================================================================
class BlockedUserViewSet(mixins.ListModelMixin, mixins.CreateModelMixin,
                          mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    GET    /api/blocked-users/       -> maine kise block kiya hai
    POST   /api/blocked-users/       -> {"blocked": "<user_id>"}
    DELETE /api/blocked-users/{id}/  -> unblock
    """
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
    """GET /api/users/<uuid:user_id>/presence/ -> online status / last seen"""
    serializer_class = UserPresenceSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        from .models import UserPresence
        presence, _ = UserPresence.objects.select_related('user').get_or_create(user_id=self.kwargs['user_id'])
        return presence


# ======================================================================
# CALL HISTORY
# ======================================================================
class CallHistoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """GET /api/calls/  ->  GET /api/calls/{id}/  -> meri call history (1-1 + group)"""
    serializer_class = CallSessionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return CallSession.objects.filter(
            Q(caller=user) | Q(call_participants__user=user) | Q(group__group_members__user=user)
        ).distinct().select_related('caller').order_by('-created_at')
