# chat/models.py - PRODUCTION LEVEL (Insta/FB scale)
import hashlib
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

User = get_user_model()


# ================= BASE MODEL =================
class BaseModel(models.Model):
    """
    Har table ka baap. UUID = scaling ke liye best hai (multi-region / sharding me
    auto-increment ID collide karte hain, UUID nahi karta).
    is_deleted = soft delete, Insta bhi hard delete nahi karta.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        abstract = True
        ordering = ['-created_at']


# ================= ENUMS / CHOICES =================
class MessageType(models.TextChoices):
    TEXT = 'text', 'Text'
    IMAGE = 'image', 'Image'
    VIDEO = 'video', 'Video'
    AUDIO = 'audio', 'Audio'
    FILE = 'file', 'File'
    PRESENTATION = 'presentation', 'Presentation'
    LOCATION = 'location', 'Location'
    SYSTEM = 'system', 'System Message'
    STUDY_ROOM = 'study_room', 'Study Room Invite'  # 🔥 NAYA — frontend §7.14
    # ki tappable invite CARD (`type: 'study_room'`) is choice ke bina
    # serializer validation pe 400 deta tha, kyunki ye pehle choices me
    # hi nahi tha.


class ConversationType(models.TextChoices):
    PRIVATE = 'private', 'Private Chat'
    GROUP = 'group', 'Group Chat'


# 🔥 NAYA — Temporary / Disappearing messages (WhatsApp jaisa). Ye poori
# conversation ki setting hai (dono/sabhi participants ke liye same), isliye
# `Conversation` model pe hai, per-user `ConversationParticipant` pe nahi.
class DisappearingDuration(models.TextChoices):
    NONE = 'none', 'Off'
    ONE_MONTH = '1_month', '1 Month'
    SIX_MONTHS = '6_months', '6 Months'
    ONE_YEAR = '1_year', '1 Year'


# duration string -> timedelta. `NONE` -> None (matlab disappearing off hai).
DISAPPEARING_DURATION_TIMEDELTA = {
    DisappearingDuration.NONE: None,
    DisappearingDuration.ONE_MONTH: timedelta(days=30),
    DisappearingDuration.SIX_MONTHS: timedelta(days=182),
    DisappearingDuration.ONE_YEAR: timedelta(days=365),
}


class CallType(models.TextChoices):
    AUDIO = 'audio', 'Audio Call'
    VIDEO = 'video', 'Video Call'


class CallStatus(models.TextChoices):
    INITIATED = 'initiated', 'Initiated'
    RINGING = 'ringing', 'Ringing'
    ONGOING = 'ongoing', 'Ongoing'
    ENDED = 'ended', 'Ended'
    MISSED = 'missed', 'Missed'
    REJECTED = 'rejected', 'Rejected'
    BUSY = 'busy', 'Busy'


# ================= 1. CHATS - Conversation List =================
class Conversation(BaseModel):
    """
    Ye teri WhatsApp/Insta ki chat list hai.
    Private ho ya Group, sabka entry yahi banega.
    Sharding Key: id

    🔥 FIX: participants ab plain ManyToMany nahi hai, through model
    (ConversationParticipant) use kar rahe hain, kyunki mute/archive/pin
    "per-user" hota hai — global nahi. (Pehle wale code me agar A ne chat
    mute ki to B ko bhi muted dikhti — yahi bug production me sabse zyada
    bug-report laata hai.)
    """
    type = models.CharField(max_length=10, choices=ConversationType.choices, db_index=True)
    participants = models.ManyToManyField(
        User, through='ConversationParticipant', related_name='all_conversations', blank=True
    )

    # 🔥 FIX: private chat ke liye 2 users ke beech duplicate conversation na bane
    # is liye ek deterministic key banate hain (sorted user-id hash) aur unique
    # rakhte hain. Group ke liye ye hamesha NULL rahega.
    private_key = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)

    # --- DENORMALIZATION FOR SPEED ---
    # Insta bhi last message alag save karta hai taki list fast khule
    last_message_text = models.CharField(max_length=500, blank=True, null=True)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_sender = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    last_message_type = models.CharField(max_length=20, choices=MessageType.choices, blank=True, null=True)

    # 🔥 NAYA — Temporary chat / disappearing messages setting. Poori
    # conversation ke liye ek hi value hoti hai (WhatsApp jaisa — dono/sabhi
    # participants ko same duration dikhta hai). Default 6 months rakha hai.
    disappearing_messages_duration = models.CharField(
        max_length=10,
        choices=DisappearingDuration.choices,
        default=DisappearingDuration.SIX_MONTHS,
        db_index=True,
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['-last_message_at']),
            models.Index(fields=['type']),
        ]

    def get_disappearing_timedelta(self):
        """Current duration setting ka `timedelta` — `None` matlab off hai."""
        return DISAPPEARING_DURATION_TIMEDELTA.get(self.disappearing_messages_duration)

    def __str__(self):
        return f"{self.type} - {self.id}"

    @staticmethod
    def make_private_key(user_id_1, user_id_2):
        """1-1 chat ke liye deterministic unique key (order matter nahi karta)."""
        ids = sorted([str(user_id_1), str(user_id_2)])
        return hashlib.sha256(f"{ids[0]}:{ids[1]}".encode()).hexdigest()

    @classmethod
    def get_or_create_private(cls, user_1, user_2):
        """
        Race-condition safe: 2 requests same time pe aayein to bhi duplicate
        conversation nahi banega (private_key unique constraint isko rokega).
        """
        key = cls.make_private_key(user_1.id, user_2.id)
        convo, created = cls.objects.get_or_create(
            private_key=key,
            defaults={'type': ConversationType.PRIVATE},
        )
        if created:
            ConversationParticipant.objects.bulk_create([
                ConversationParticipant(conversation=convo, user=user_1),
                ConversationParticipant(conversation=convo, user=user_2),
            ])
        return convo, created


# 🔥 NEW: per-user chat settings (mute/archive/pin/unread) — ye Insta/WhatsApp
# dono me isi tarah alag table me hota hai kyunki har user ki apni preference
# hoti hai, aur unread_count yahin denormalize karke rakhte hain taki chat-list
# query me har baar COUNT(*) na chalana pade.
class ConversationParticipant(BaseModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversation_memberships')

    is_archived = models.BooleanField(default=False)
    is_muted = models.BooleanField(default=False)
    is_pinned = models.BooleanField(default=False)

    # 🔥 NAYA — Is chat ko apna custom naam/nickname dene ke liye (sirf
    # is user ko dikhega, dusre participant/group members ko nahi — isliye
    # `Conversation` pe nahi, per-user `ConversationParticipant` pe hai,
    # jaisa mute/pin/archive hai). NULL/blank matlab koi custom label nahi,
    # default naam (participant ka naam ya group ka naam) hi dikhega.
    label = models.CharField(max_length=100, blank=True, null=True)

    unread_count = models.PositiveIntegerField(default=0)
    last_read_message = models.ForeignKey(
        'Message', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    last_read_at = models.DateTimeField(null=True, blank=True)

    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)  # group leave ke liye

    class Meta(BaseModel.Meta):
        unique_together = ('conversation', 'user')
        indexes = [
            models.Index(fields=['user', 'is_archived']),
            models.Index(fields=['conversation', 'user']),
        ]


# ================= 2. GROUP =================
class Group(BaseModel):
    """
    Group Chat ka data
    """
    # 🔥 NAYA — Access-control settings (`group_profile_screen.dart`'s
    # access-control sheet in par depend karti hai; pehle ye poori
    # feature backend me bilkul missing thi — `group_rules.py` isi ke
    # liye banayi gayi thi par khaali reh gayi thi). ADMINS_ONLY ka
    # matlab: sirf admin/moderator role wale members allowed hain.
    class PermissionLevel(models.TextChoices):
        EVERYONE = 'everyone', 'Everyone'
        ADMINS_ONLY = 'admins_only', 'Admins/Moderators Only'

    conversation = models.OneToOneField(Conversation, on_delete=models.CASCADE, related_name='group_detail')
    name = models.CharField(max_length=100, db_index=True)
    description = models.TextField(blank=True)
    photo_url = models.URLField(blank=True, null=True)  # S3 URL

    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_groups')

    invite_code = models.CharField(max_length=20, unique=True, blank=True, null=True, db_index=True)
    is_private = models.BooleanField(default=False)

    message_permission = models.CharField(
        max_length=20, choices=PermissionLevel.choices, default=PermissionLevel.EVERYONE
    )
    call_permission = models.CharField(
        max_length=20, choices=PermissionLevel.choices, default=PermissionLevel.EVERYONE
    )
    study_room_permission = models.CharField(
        max_length=20, choices=PermissionLevel.choices, default=PermissionLevel.EVERYONE
    )
    # null/blank = koi limit nahi. Admin/moderator is limit se hamesha exempt.
    daily_message_limit = models.PositiveIntegerField(null=True, blank=True)

    # Fast count ke liye (signal se update karo, query se mat gino)
    members_count = models.PositiveIntegerField(default=0)
    messages_count = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name


class GroupMember(BaseModel):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MODERATOR = 'moderator', 'Moderator'
        MEMBER = 'member', 'Member'

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='group_members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='my_groups')
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER, db_index=True)

    added_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    is_muted = models.BooleanField(default=False)
    is_banned = models.BooleanField(default=False)

    class Meta:
        unique_together = ('group', 'user')
        indexes = [
            models.Index(fields=['group', 'user']),
            models.Index(fields=['user', 'role']),
        ]


# 🔥 NAYA — Private group me "seedha add" nahi, "request bhejo -> admin/
# moderator approve/reject kare" wala flow. Public group me ye table use
# hi nahi hota (public join instant hai, GroupMember seedha ban jaata hai).
# `unique_together` isliye taaki ek user same group ke liye ek hi row
# rakhe — reject hone ke baad dobara request kare to naya row banane ke
# bajaye wahi row `PENDING` pe reset ho jaati hai (history bhi preserve
# rehti hai ki pehle reject hua tha).
class GroupJoinRequest(BaseModel):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='join_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='group_join_requests')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    responded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        unique_together = ('group', 'user')
        indexes = [
            models.Index(fields=['group', 'status']),
            models.Index(fields=['user', 'status']),
        ]


# ================= 3. MESSAGE - Sabse Important =================
class Message(BaseModel):
    """
    Saare messages - private ho ya group ka
    Index: conversation + created_at -> ye chat ko 10x fast karega
    """
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='all_messages', db_index=True)
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages', db_index=True)

    type = models.CharField(max_length=20, choices=MessageType.choices, default=MessageType.TEXT, db_index=True)
    text = models.TextField(blank=True, null=True)

    # --- MEDIA ---
    file_url = models.URLField(blank=True, null=True)  # single file
    file_urls = models.JSONField(default=list, blank=True)  # multiple images [url1, url2]
    thumbnail_url = models.URLField(blank=True, null=True)  # video/pdf ka preview

    # file ka extra data: {"size": 2045, "duration": 120, "width": 1080, "height": 1920, "file_name": "abc.pdf", "pages": 20}
    meta = models.JSONField(default=dict, blank=True)

    # --- REPLY SYSTEM ---
    reply_to = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='all_replies')

    # --- FLAGS ---
    is_edited = models.BooleanField(default=False)
    is_forwarded = models.BooleanField(default=False)
    is_system_message = models.BooleanField(default=False)

    # 🔥 NEW: "delete for me" vs "delete for everyone" (WhatsApp/Insta pattern)
    deleted_for_everyone = models.BooleanField(default=False)
    deleted_for_users = models.ManyToManyField(User, blank=True, related_name='deleted_messages')

    # 🔥 NEW: client-generated id — offline pe user message bhejta hai, connection
    # aane pe retry hota hai to same message duplicate na bane (idempotency)
    client_id = models.CharField(max_length=64, blank=True, null=True, db_index=True)

    # 🔥 NAYA — Temporary chat / disappearing messages. Message create hote
    # waqt hi conversation ki current `disappearing_messages_duration` se
    # calculate karke yahan fix kar dete hain (send-time snapshot — WhatsApp
    # jaisa: baad me setting badle to purane messages ki expiry nahi badalti,
    # sirf naye messages nayi duration follow karte hain). `null` = kabhi
    # expire nahi hoga (duration "none" thi jab bheja gaya tha).
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # 🔥 NAYA — Message pin. Ek conversation me kai messages pin ho sakte
    # hain (WhatsApp jaisa max-3 limit `MessageViewSet.pin` action me
    # enforce hota hai, model pe koi hard limit nahi taaki future me limit
    # badalna sirf ek jagah, view me, badalna pade). `pinned_by` = kisne
    # pin kiya — group me sirf admin/mod, private chat me dono me se koi.
    is_pinned = models.BooleanField(default=False, db_index=True)
    pinned_at = models.DateTimeField(null=True, blank=True)
    pinned_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    # 🔥 NAYA — @mentions. Message text me "@username" likhne par us
    # conversation ke member(s) resolve karke yahan store karte hain
    # (`message/mentions.py` ka `extract_mentioned_user_ids` isko fill
    # karta hai — REST aur WebSocket dono message-send path se). Isse
    # (a) mention ko highlight karna client-side aasan hota hai (ID pata
    # hai, sirf regex-guess nahi), aur (b) mentioned user ko alag "you
    # were mentioned" push bheja ja sakta hai.
    mentioned_users = models.ManyToManyField(
        User, blank=True, related_name='mentioned_in_messages'
    )

    # 🔥 NAYA — Starred/saved messages. Pin ki tarah nahi hai — pin SABKO
    # dikhta hai (group-wide "important message"), star sirf PERSONAL
    # bookmark hai (WhatsApp ka "Starred Messages" jaisa — har user ki
    # apni list, kisi aur ko pata bhi nahi chalta). Isliye simple M2M —
    # koi extra "starred_at"/"starred_by" single-FK ki zaroorat nahi.
    starred_by = models.ManyToManyField(User, blank=True, related_name='starred_messages')

    # 🔥 NAYA — Scheduled messages ("Send later"). `scheduled_for` = kab
    # bhejna hai; `is_scheduled=True` jab tak `send_scheduled_messages`
    # management command (cron/Celery-beat se periodically chalega) ise
    # process karke False na kar de. Jab tak scheduled hai, ye message
    # SIRF sender ko dikhta hai (`ConversationViewSet.messages` GET me
    # filter hai) — baaki participants ko tab tak pata hi nahi chalta,
    # jaisa Gmail/WhatsApp scheduled-send karta hai.
    is_scheduled = models.BooleanField(default=False, db_index=True)
    scheduled_for = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
            models.Index(fields=['type']),
            models.Index(fields=['reply_to']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['conversation', 'is_pinned']),
            models.Index(fields=['is_scheduled', 'scheduled_for']),
        ]
        constraints = [
            # same sender ek hi client_id do baar submit kare to DB level pe hi block
            models.UniqueConstraint(
                fields=['conversation', 'sender', 'client_id'],
                name='unique_message_client_id',
                condition=models.Q(client_id__isnull=False),
            )
        ]


# ================= 4. PRESENTATION - Alag se =================
class Presentation(BaseModel):
    """
    PPT / PDF / DOC jo chat me bheja jayega
    """
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='presentation_data')
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE, related_name='presentations')

    file_url = models.URLField()
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField()  # bytes me
    total_pages = models.PositiveIntegerField(default=0)
    file_type = models.CharField(max_length=10)  # pdf, pptx, docx

    # preview ke liye
    cover_thumbnail = models.URLField(blank=True, null=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=['group', '-created_at'])]


# ================= 5. GROUP MEDIA - Gallery =================
class GroupMedia(BaseModel):
    """
    Group ke saare photos/videos/files ek jagah dikhane ke liye
    Ye alag table isliye taki gallery query fast ho
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='gallery')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='gallery_media')
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='media_info')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)

    file_url = models.URLField()
    file_type = models.CharField(max_length=20, choices=MessageType.choices, db_index=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    thumbnail_url = models.URLField(blank=True, null=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['group', 'file_type', '-created_at']),
            models.Index(fields=['conversation', '-created_at']),
        ]


# ================= 6. MESSAGE STATUS & REACTION =================
class MessageStatus(BaseModel):
    """
    Kisne dekha, kisne deliver hua - isko alag rakha hai
    warna Message table bahut bhaari ho jayega
    """
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='delivery_status')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_status')

    is_delivered = models.BooleanField(default=False, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('message', 'user')
        indexes = [models.Index(fields=['user', 'is_read'])]


class MessageReaction(BaseModel):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='all_reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    emoji = models.CharField(max_length=20)  # 👍 ❤️ 😂 🔥

    class Meta:
        unique_together = ('message', 'user')
        indexes = [models.Index(fields=['message'])]


# ================= 7. AUDIO / VIDEO CALL - 1-1 CALL =================
class CallSession(BaseModel):
    """
    Ye 1-1 Audio/Video Call + Group Audio/Video Call dono ke liye ek hi model
    is_group_call = False -> 1-1 Call
    is_group_call = True -> Group Call
    """
    type = models.CharField(max_length=10, choices=CallType.choices, db_index=True)
    status = models.CharField(max_length=20, choices=CallStatus.choices, default=CallStatus.INITIATED, db_index=True)

    is_group_call = models.BooleanField(default=False, db_index=True)

    # Relation
    conversation = models.ForeignKey(Conversation, null=True, blank=True, on_delete=models.CASCADE,
                                     related_name='call_history')
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.CASCADE, related_name='group_calls')

    caller = models.ForeignKey(User, on_delete=models.CASCADE, related_name='calls_initiated')

    # Agora / LiveKit
    channel_name = models.CharField(max_length=150, unique=True, db_index=True)
    token = models.TextField(blank=True, null=True)  # Agora token

    started_at = models.DateTimeField(default=timezone.now)
    connected_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

    is_recording = models.BooleanField(default=False)
    recording_url = models.URLField(blank=True, null=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['caller', '-created_at']),
            models.Index(fields=['group', '-created_at']),
            models.Index(fields=['is_group_call', 'status']),
        ]


class CallParticipant(BaseModel):
    call = models.ForeignKey(CallSession, on_delete=models.CASCADE, related_name='call_participants')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='call_participations')

    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    is_muted = models.BooleanField(default=False)
    is_video_off = models.BooleanField(default=False)
    is_screen_sharing = models.BooleanField(default=False)
    is_deafened = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=CallStatus.choices, default=CallStatus.RINGING)

    class Meta:
        unique_together = ('call', 'user')


# ================= 8. PRESENCE - Online/Offline (NEW) =================
class UserPresence(BaseModel):
    """
    "Online" / "Last seen" dikhane ke liye. Multi-device support: user 2 phone
    se login ho sakta hai isliye active_connections count rakhte hain — jab tak
    count > 0 hai tab tak online, 0 hote hi offline + last_seen update.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='presence')
    is_online = models.BooleanField(default=False, db_index=True)
    active_connections = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=['is_online'])]


# ================= 9. BLOCKED USERS (NEW) =================
class BlockedUser(BaseModel):
    """
    Insta/WhatsApp jaisa block system — websocket layer pe isko check karke
    blocked user ka message deliver hone se rokte hain.
    """
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_users')
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by')

    class Meta(BaseModel.Meta):
        unique_together = ('blocker', 'blocked')
        indexes = [models.Index(fields=['blocker', 'blocked'])]



# ======================================================================
# ADD THIS TO models.py (kahi bhi, e.g. UserPresence ke aas paas)
# ======================================================================
class DeviceToken(BaseModel):
    """
    Har device ka FCM token yaha store hota hai. Ek user ke multiple
    devices ho sakte hain (phone + tablet), isliye ForeignKey — OneToOne
    nahi.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(
        max_length=10,
        choices=[('android', 'Android'), ('ios', 'iOS'), ('web', 'Web')],
        default='android',
    )

    class Meta:
        indexes = [models.Index(fields=['user'])]


# ======================================================================
# STUDY ROOM — WHITEBOARD STATE
# ------------------------------------------------------------
# 🔥 NAYA — poora whiteboard (saari pages: strokes/shapes/text/sticky
# notes, aur ab shared PDF/image ka `fileUrl`) yahan ek hi JSONField me
# store hota hai. Frontend periodically (`saveStudyRoomState`) poora
# `{"pages": [...]}` yahan PUT karta hai, aur room khulte hi
# (`getStudyRoomState`) wahi wapas mil jaata hai — isse room reopen
# karne wale ya baad me join karne wale ko bhi wahi board + shared file
# dikhta hai jahan chhoda gaya tha.
# ======================================================================
class StudyRoomState(BaseModel):
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name='study_room_state'
    )
    state = models.JSONField(default=dict, blank=True)
    updated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        indexes = [models.Index(fields=['conversation'])]