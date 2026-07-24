# message/serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import (
    BlockedUser,
    CallSession,
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMedia,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    Presentation,
    UserPresence,
)

User = get_user_model()


# ======================================================================
# USER (MINI)
# ======================================================================
class UserMiniSerializer(serializers.ModelSerializer):
    """
    Chat ke andar dikhne wala minimal user info.
    NOTE: apne actual AUTH_USER_MODEL (login.User) ke fields ke hisaab se
    `get_display_name` me field names adjust kar lena (e.g. 'full_name',
    'avatar_url' waghera).
    """
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'display_name']

    def get_display_name(self, obj):
        for field in ('full_name', 'name', 'username', 'email'):
            value = getattr(obj, field, None)
            if value:
                return value
        return str(obj)


# ======================================================================
# CONVERSATION
# ======================================================================
class ConversationSettingsSerializer(serializers.ModelSerializer):
    """Per-user chat settings: mute/archive/pin (ConversationParticipant)."""

    class Meta:
        model = ConversationParticipant
        fields = ['is_archived', 'is_muted', 'is_pinned']


class GroupMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ['id', 'name', 'photo_url', 'members_count']


class ConversationListSerializer(serializers.ModelSerializer):
    other_participant = serializers.SerializerMethodField()
    group = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    my_settings = serializers.SerializerMethodField()
    last_message_sender = UserMiniSerializer(read_only=True)

    class Meta:
        model = Conversation
        fields = [
            'id', 'type', 'other_participant', 'group',
            'last_message_text', 'last_message_at', 'last_message_sender',
            'last_message_type', 'unread_count', 'my_settings', 'created_at',
        ]

    def _membership(self, obj):
        request = self.context.get('request')
        if not request:
            return None
        return ConversationParticipant.objects.filter(conversation=obj, user=request.user).first()

    def get_other_participant(self, obj):
        if obj.type != ConversationType.PRIVATE:
            return None
        request = self.context.get('request')
        membership = obj.memberships.exclude(user=request.user).select_related('user').first()
        return UserMiniSerializer(membership.user).data if membership else None

    def get_group(self, obj):
        if obj.type != ConversationType.GROUP:
            return None
        detail = getattr(obj, 'group_detail', None)
        return GroupMiniSerializer(detail).data if detail else None

    def get_unread_count(self, obj):
        membership = self._membership(obj)
        return membership.unread_count if membership else 0

    def get_my_settings(self, obj):
        membership = self._membership(obj)
        return ConversationSettingsSerializer(membership).data if membership else None


# ======================================================================
# MESSAGE
# ======================================================================
class ReplyPreviewSerializer(serializers.ModelSerializer):
    sender = UserMiniSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'type', 'text', 'sender']


class MessageReactionSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = MessageReaction
        fields = ['id', 'user', 'emoji', 'created_at']
        read_only_fields = ['id', 'created_at']


class MessageSerializer(serializers.ModelSerializer):
    """Full message representation — GET responses ke liye."""
    sender = UserMiniSerializer(read_only=True)
    reply_to_detail = ReplyPreviewSerializer(source='reply_to', read_only=True)
    reactions = MessageReactionSerializer(source='all_reactions', many=True, read_only=True)
    is_read_by_me = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'type', 'text',
            'file_url', 'file_urls', 'thumbnail_url', 'meta',
            'reply_to', 'reply_to_detail', 'is_edited', 'is_forwarded',
            'is_system_message', 'deleted_for_everyone', 'client_id',
            'reactions', 'is_read_by_me', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'conversation', 'sender', 'is_edited', 'is_forwarded',
            'is_system_message', 'deleted_for_everyone', 'created_at', 'updated_at',
        ]

    def get_is_read_by_me(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return MessageStatus.objects.filter(message=obj, user=request.user, is_read=True).exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        # "Delete for me" — sirf usi user ko blank dikhta hai, baaki sabko normal.
        if request and instance.deleted_for_users.filter(id=request.user.id).exists():
            data['text'] = None
            data['file_url'] = None
            data['deleted_for_me'] = True
        return data


class MessageCreateSerializer(serializers.ModelSerializer):
    """Naya message bhejne ke liye (REST fallback — realtime delivery websocket se hoti hai)."""

    class Meta:
        model = Message
        fields = [
            'id', 'type', 'text', 'file_url', 'file_urls', 'thumbnail_url',
            'meta', 'reply_to', 'client_id',
        ]
        read_only_fields = ['id']

    def validate(self, attrs):
        msg_type = attrs.get('type', MessageType.TEXT)
        text = attrs.get('text')
        if msg_type == MessageType.TEXT and not (text and text.strip()):
            raise serializers.ValidationError("Text message ke liye 'text' field required hai.")
        return attrs


# ======================================================================
# GROUP
# ======================================================================
class GroupMemberSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = GroupMember
        fields = ['id', 'user', 'role', 'is_muted', 'is_banned', 'created_at']
        read_only_fields = ['id', 'created_at']


class GroupSerializer(serializers.ModelSerializer):
    members = serializers.SerializerMethodField()
    conversation_id = serializers.UUIDField(source='conversation.id', read_only=True)
    created_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = Group
        fields = [
            'id', 'conversation_id', 'name', 'description', 'photo_url',
            'created_by', 'invite_code', 'is_private', 'members_count',
            'messages_count', 'members', 'created_at',
        ]
        read_only_fields = [
            'id', 'conversation_id', 'created_by', 'invite_code',
            'members_count', 'messages_count', 'created_at',
        ]

    def get_members(self, obj):
        qs = obj.group_members.filter(is_banned=False).select_related('user')
        return GroupMemberSerializer(qs, many=True).data


class GroupCreateSerializer(serializers.Serializer):
    """Naya group banane ke liye input serializer."""
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    photo_url = serializers.URLField(required=False, allow_null=True)
    is_private = serializers.BooleanField(required=False, default=False)
    member_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=True, required=False, default=list
    )


# ======================================================================
# MEDIA / PRESENTATION
# ======================================================================
class PresentationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Presentation
        fields = [
            'id', 'message', 'group', 'file_url', 'file_name', 'file_size',
            'total_pages', 'file_type', 'cover_thumbnail', 'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class GroupMediaSerializer(serializers.ModelSerializer):
    sender = UserMiniSerializer(read_only=True)

    class Meta:
        model = GroupMedia
        fields = [
            'id', 'group', 'conversation', 'message', 'sender',
            'file_url', 'file_type', 'file_size', 'thumbnail_url', 'created_at',
        ]
        read_only_fields = fields


# ======================================================================
# PRESENCE / BLOCK / CALLS
# ======================================================================
class UserPresenceSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = UserPresence
        fields = ['user', 'is_online', 'last_seen_at']


class BlockedUserSerializer(serializers.ModelSerializer):
    blocked_detail = UserMiniSerializer(source='blocked', read_only=True)

    class Meta:
        model = BlockedUser
        fields = ['id', 'blocked', 'blocked_detail', 'created_at']
        read_only_fields = ['id', 'created_at']


class CallSessionSerializer(serializers.ModelSerializer):
    caller = UserMiniSerializer(read_only=True)

    class Meta:
        model = CallSession
        fields = [
            'id', 'type', 'status', 'is_group_call', 'conversation', 'group',
            'caller', 'channel_name', 'started_at', 'connected_at', 'ended_at',
            'duration_seconds', 'is_recording', 'recording_url', 'created_at',
        ]
        read_only_fields = fields