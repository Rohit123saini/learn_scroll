# message/serializers.py
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from .models import (
    BlockedUser,
    CallSession,
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupJoinRequest,
    GroupMedia,
    GroupMember,
    Message,
    MessageReaction,
    MessageStatus,
    MessageType,
    Presentation,
    UserPresence,
)
from .user_display import get_display_name, get_profile_photo_url

User = get_user_model()


# ======================================================================
# USER (MINI)
# ======================================================================
class UserMiniSerializer(serializers.ModelSerializer):
    """
    Chat/call/study-room ke andar dikhne wala minimal user info — sabki
    profile photo aur naam yahin se aata hai (Conversation list, message
    sender, group members, reactions, call history — sab isi serializer
    ko reuse karte hain, taaki data shape har jagah consistent rahe).

    `first_name` / `last_name` / `profile_photo` seedhe custom User model
    (login.User) ke columns hain — koi alag Profile app/table nahi, isliye
    jahan bhi queryset me `select_related('sender' / 'user' / 'caller')`
    lagi hai, ye fields BINA kisi extra query ke already load ho chuke
    hote hain (naya N+1 nahi banta).
    """
    display_name = serializers.SerializerMethodField()
    profile_photo = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'display_name', 'profile_photo']

    def get_display_name(self, obj):
        return get_display_name(obj)

    def get_profile_photo(self, obj):
        # `request` context se absolute URL banta hai (scheme+host sahi
        # milta hai); nested/manual instantiation me context mis jaaye to
        # bhi `get_profile_photo_url` khud settings-based fallback deta
        # hai — crash ya blank URL kabhi nahi hota.
        request = self.context.get('request')
        return get_profile_photo_url(obj, request=request)


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
        # 🔥 FIX (N+1) — this used to run a fresh DB query every time it was
        # called, and it's called TWICE per row (`get_unread_count` +
        # `get_my_settings`) — for a paginated list of 20 conversations
        # that's up to 40 extra queries just for this, on top of
        # `get_other_participant`'s own per-row query for private chats.
        # `ConversationViewSet.get_queryset()` now prefetches "my"
        # membership per conversation as `my_membership_list` (a `Prefetch`
        # with `to_attr`, filtered to `request.user` — exactly 0 or 1 row
        # per conversation, cheap regardless of group size) — when that's
        # present we use it directly (zero extra queries for the whole
        # page). Falls back to a live query when the prefetch isn't there,
        # so this serializer stays correct even if used somewhere that
        # doesn't set it up (e.g. a single-object `retrieve`).
        if hasattr(obj, 'my_membership_list'):
            return obj.my_membership_list[0] if obj.my_membership_list else None
        request = self.context.get('request')
        if not request:
            return None
        return ConversationParticipant.objects.filter(conversation=obj, user=request.user).first()

    def get_other_participant(self, obj):
        if obj.type != ConversationType.PRIVATE:
            return None
        request = self.context.get('request')
        membership = obj.memberships.exclude(user=request.user).select_related('user').first()
        return UserMiniSerializer(membership.user, context=self.context).data if membership else None

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


# 🔥 NAYA — "Seen by" / message-info detail (`MessageViewSet.read_status`).
# `MessageSerializer.is_read_by_me` (neeche) sirf "maine ye padha ya nahi"
# batata hai (ek boolean, apne liye) — ye alag serializer poore
# `MessageStatus` row ko expose karta hai (kisne, kab dekha/deliver hua),
# jo group chats me "message info" jaisi screen ke liye chahiye hota hai.
class MessageReadStatusSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = MessageStatus
        fields = ['user', 'is_delivered', 'delivered_at', 'is_read', 'read_at']


class MessageSerializer(serializers.ModelSerializer):
    """Full message representation — GET responses ke liye."""
    sender = UserMiniSerializer(read_only=True)
    reply_to_detail = ReplyPreviewSerializer(source='reply_to', read_only=True)
    reactions = MessageReactionSerializer(source='all_reactions', many=True, read_only=True)
    is_read_by_me = serializers.SerializerMethodField()
    # 🔥 NAYA — pin info (kisne pin kiya, dikhane ke liye) + @mentions.
    pinned_by = UserMiniSerializer(read_only=True)
    mentioned_users = UserMiniSerializer(many=True, read_only=True)
    # 🔥 NAYA (advanced feature) — starred (saved) messages, WhatsApp-style
    # personal bookmark. `starred_by` field pehle se model pe tha lekin
    # kahin bhi expose/wire nahi kiya gaya tha — ab dikhta bhi hai
    # (`is_starred`) aur `MessageViewSet.star` se toggle bhi ho sakta hai.
    is_starred = serializers.SerializerMethodField()

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'type', 'text',
            'file_url', 'file_urls', 'thumbnail_url', 'meta',
            'reply_to', 'reply_to_detail', 'is_edited', 'is_forwarded',
            'is_system_message', 'deleted_for_everyone', 'client_id',
            'reactions', 'is_read_by_me', 'is_pinned', 'pinned_at', 'pinned_by',
            'mentioned_users', 'is_starred',
            # 🔥 NAYA (advanced feature) — scheduled ("send later") messages.
            # Ye fields sirf tab dikhte hain jab requester khud sender ho
            # (see ConversationViewSet.messages / search / search_all —
            # wahan is_scheduled=True messages already exclude hote hain
            # sabke liye, isliye ye field normal chat me kabhi kisi aur ko
            # nahi dikhega — sirf schedule-message/scheduled-messages
            # endpoints se, jo sender-only hain).
            'is_scheduled', 'scheduled_for',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'conversation', 'sender', 'is_edited', 'is_forwarded',
            'is_system_message', 'deleted_for_everyone', 'is_pinned',
            'pinned_at', 'pinned_by', 'mentioned_users', 'is_scheduled',
            'scheduled_for', 'created_at', 'updated_at',
        ]

    def get_is_read_by_me(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        return MessageStatus.objects.filter(message=obj, user=request.user, is_read=True).exists()

    def get_is_starred(self, obj):
        request = self.context.get('request')
        if not request:
            return False
        # `starred_by` prefetch nahi kiya gaya har jagah (personal bookmark
        # hai, list view me sabke liye alag hota), isliye chhota per-row
        # EXISTS query — indexed M2M pe fast hai, message list-size (30-100
        # per page) ke liye theek hai.
        return obj.starred_by.filter(id=request.user.id).exists()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        # "Delete for me" — sirf usi user ko blank dikhta hai, baaki sabko normal.
        if request and instance.deleted_for_users.filter(id=request.user.id).exists():
            data['text'] = None
            data['file_url'] = None
            data['deleted_for_me'] = True
        return data


# 🔥 NAYA — global search (`ConversationViewSet.search_all`) ke results
# ke liye. `MessageSerializer` jaisa hi hai, bas ek chhota `conversation_
# preview` add karta hai taaki result list me "ye message KIS chat me
# mila" bhi pata chale — single-conversation search
# (`ConversationViewSet.search`) me ye zaroorat nahi (conversation to
# request se hi pata hai), isliye wahan plain `MessageSerializer` hi use
# hota hai.
class MessageSearchResultSerializer(MessageSerializer):
    conversation_preview = serializers.SerializerMethodField()

    class Meta(MessageSerializer.Meta):
        fields = MessageSerializer.Meta.fields + ['conversation_preview']

    def get_conversation_preview(self, obj):
        conversation = obj.conversation
        if conversation.type == ConversationType.GROUP:
            detail = getattr(conversation, 'group_detail', None)
            name = detail.name if detail else None
            photo = detail.photo_url if detail else None
        else:
            request = self.context.get('request')
            other = conversation.memberships.exclude(user=request.user).select_related('user').first() \
                if request else None
            name = get_display_name(other.user) if other else None
            photo = get_profile_photo_url(other.user, request=request) if other else None
        return {'type': conversation.type, 'name': name, 'photo_url': photo}


class MessageCreateSerializer(serializers.ModelSerializer):
    """Naya message bhejne ke liye (REST fallback — realtime delivery websocket se hoti hai)."""

    class Meta:
        model = Message
        fields = [
            'id', 'type', 'text', 'file_url', 'file_urls', 'thumbnail_url',
            'meta', 'reply_to', 'client_id',
        ]
        read_only_fields = ['id']

    # Message types that carry a file/media and need somewhere to point to
    # it. LOCATION and SYSTEM/STUDY_ROOM messages carry their payload in
    # `meta`/`text` instead, so they're excluded here.
    _MEDIA_TYPES = (
        MessageType.IMAGE, MessageType.VIDEO, MessageType.AUDIO,
        MessageType.FILE, MessageType.PRESENTATION,
    )

    def validate(self, attrs):
        msg_type = attrs.get('type', MessageType.TEXT)
        text = attrs.get('text')
        if msg_type == MessageType.TEXT and not (text and text.strip()):
            raise serializers.ValidationError("Text message ke liye 'text' field required hai.")

        # 🔥 FIX — image/video/audio/file/presentation messages had no
        # requirement to actually carry a `file_url` (or `file_urls` for
        # multi-image). Nothing stopped a client from posting a media
        # message with no file attached, which then rendered as a blank/
        # broken attachment for every recipient with no way to tell what
        # went wrong.
        if msg_type in self._MEDIA_TYPES and not (attrs.get('file_url') or attrs.get('file_urls')):
            raise serializers.ValidationError(
                f"'{msg_type}' message ke liye 'file_url' ya 'file_urls' required hai."
            )
        return attrs


# 🔥 NAYA (advanced feature) — "Send Later" / scheduled messages.
# `MessageCreateSerializer` ke saare validations reuse karta hai (media
# type ke liye file_url required, text ke liye non-empty text, etc.) aur
# bas ek required future `scheduled_for` add karta hai. Sirf
# `ConversationViewSet.schedule_message` isko use karta hai — normal send
# `MessageCreateSerializer` hi use karta rehta hai.
class ScheduleMessageSerializer(MessageCreateSerializer):
    scheduled_for = serializers.DateTimeField()

    class Meta(MessageCreateSerializer.Meta):
        fields = MessageCreateSerializer.Meta.fields + ['scheduled_for']

    def validate_scheduled_for(self, value):
        if value <= timezone.now():
            raise serializers.ValidationError("'scheduled_for' future ka time hona chahiye.")
        return value


# ======================================================================
# GROUP
# ======================================================================
class GroupMemberSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)

    class Meta:
        model = GroupMember
        fields = ['id', 'user', 'role', 'is_muted', 'is_banned', 'created_at']
        read_only_fields = ['id', 'created_at']


# 🔥 NAYA — private group ke "join request" ke liye. Admin/moderator ki
# pending-requests list yahi serializer use karti hai.
class GroupJoinRequestSerializer(serializers.ModelSerializer):
    user = UserMiniSerializer(read_only=True)
    responded_by = UserMiniSerializer(read_only=True)

    class Meta:
        model = GroupJoinRequest
        fields = ['id', 'group', 'user', 'status', 'responded_by', 'responded_at', 'created_at']
        read_only_fields = fields


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
            # 🔥 NAYA — group_profile_screen.dart ke access-control sheet
            # ke liye (message/call/study-room permission + daily limit).
            'message_permission', 'call_permission', 'study_room_permission',
            'daily_message_limit',
        ]
        read_only_fields = [
            'id', 'conversation_id', 'created_by', 'invite_code',
            'members_count', 'messages_count', 'created_at',
        ]

    def get_members(self, obj):
        qs = obj.group_members.filter(is_banned=False).select_related('user')
        return GroupMemberSerializer(qs, many=True, context=self.context).data


class GroupCreateSerializer(serializers.Serializer):
    """
    Naya group banane ke liye input serializer.

    🔥 FIX: `member_ids` pehle `serializers.UUIDField()` list tha, jabki
    tumhara custom `User` model ka primary key UUID nahi hai — integer
    hai (profile app ke `urls.py` me `<int:user_id>` / `<int:follow_id>`
    converters se aur `serializers.py` ke `IntegerField()` based
    my_id/target_user_id/follow_id fields se confirm hota hai). Isi
    mismatch ki wajah se frontend se aane wale valid integer IDs bhi
    "Must be a valid UUID." keh ke reject ho rahe the. Ab `IntegerField()`
    use kiya hai taaki actual User pk type se match ho.
    """
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    photo_url = serializers.URLField(required=False, allow_null=True)
    is_private = serializers.BooleanField(required=False, default=False)
    member_ids = serializers.ListField(
        child=serializers.IntegerField(), allow_empty=True, required=False, default=list
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


# 🔥 NAYA — sirf apni read-receipt privacy setting dekhne/badalne ke liye
# (`ReadReceiptSettingsView`, GET/PATCH, hamesha `request.user` par hi
# operate karta hai). Jaan-boojh kar `UserPresenceSerializer` se ALAG
# rakha hai — wo doosron ka bhi presence dikhata hai (`UserPresenceView`
# by `user_id`), aur `show_read_receipts` kisi aur ka expose karna iska
# maksad nahi hai (khud ka toggle-state doosron ko dikhna, arms-race
# jaisa signal ban sakta hai — WhatsApp bhi ye field kabhi seedha expose
# nahi karta, sirf effect dikhata hai).
class ReadReceiptSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPresence
        fields = ['show_read_receipts']


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