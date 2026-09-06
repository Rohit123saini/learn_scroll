# chat/models.py
import hashlib
import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
# 🔧 GAP FIX — `SearchVectorField`/`GinIndex` neeche `Message` model me
# already use ho rahe the (full-text search field + its GIN index) lekin
# yahan import hi missing tha — jis wajah se poori `message` app import
# time pe `NameError: name 'SearchVectorField' is not defined` deke crash
# ho jaati (Django app boot hi nahi hota). Ye bug pehle se hi mila,
# is session ke full-text-search kaam se independent — isko fix karna
# zaroori tha warna neeche koi bhi naya migration/query bhi nahi chalta.
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.utils import timezone

User = get_user_model()


# ================= BASE MODEL =================
# 🔧 GAP FIX — `is_deleted` field pehle bhi model pe tha, lekin koi bhi
# queryset/manager isko filter nahi karta tha, matlab isse "delete" karne
# ka koi real effect hi nahi tha — dead field. Ab do managers hain:
#   - `objects` (default): `is_deleted=True` rows KABHI normal query me
#     nahi aayenge — Conversation/Message/Group sab isi manager se query
#     hote hain (reverse-FK/M2M relations jaise `.memberships`,
#     `.all_messages` bhi isi manager class ko inherit karte hain, isliye
#     ye automatically har jagah lagu ho jaata hai, alag se har jagah
#     `.exclude(is_deleted=True)` likhne ki zaroorat nahi).
#   - `all_objects`: poora data (soft-deleted rows samet) — sirf admin
#     panel / cleanup management-commands ke liye.
# Abhi kahin bhi `is_deleted=True` set nahi hota, isliye ye change
# zero-risk hai (behaviour bilkul same rehta hai) — bas ab is field ka
# istemaal karke groundwork ready hai (e.g. group/message moderation
# "soft delete" future me `obj.soft_delete()` call karke turant kaam
# karega, migration ki zaroorat nahi padegi).
class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


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

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        ordering = ['-created_at']

    def soft_delete(self):
        """Row ko hide karo bina hard-delete kiye (moderation/cleanup ke liye)."""
        self.is_deleted = True
        self.save(update_fields=['is_deleted', 'updated_at'])

    def restore(self):
        """Soft-deleted row wapas normal manager (`objects`) me dikhne lage."""
        self.is_deleted = False
        self.save(update_fields=['is_deleted', 'updated_at'])


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
    POLL = 'poll', 'Poll'  # 🔥 NAYA — group poll messages, see Poll model neeche


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

    # 🔥 NAYA — Server-side draft auto-save. `label`/`is_muted`/`is_pinned`
    # jaisa hi per-user field hai (ye bhi is ChatParticipant row pe hai, na
    # ki Conversation pe), taaki ek hi half-typed message multi-device pe
    # carry ho sake (WhatsApp Web jaisa — phone pe kuch type karo, laptop
    # khol ke wahi text compose-box me mil jaaye). `draft_updated_at` sirf
    # last-write-wins ke liye — 2 devices se near-simultaneous save ho to
    # client ise dikha ke merge-conflict decide kar sakta hai; server khud
    # koi merge nahi karta, seedha overwrite karta hai.
    draft_text = models.TextField(blank=True, null=True)
    draft_updated_at = models.DateTimeField(null=True, blank=True)

    # 🔧 GAP FIX (this session) — corrected: `message_api_service.dart`
    # (getWallpaper/setWallpaper/clearWallpaper) and `PROJECT_ARCHITECTURE.md`
    # (`GET/PATCH /message/conversations/<id>/wallpaper/`) already describe
    # this as a per-user setting, exactly like `is_muted`/`is_pinned`/
    # `label` above. Turns out this field was NEVER actually missing from
    # the database — migrations `0007_remove_message_background_url_and_more`
    # (AddField) and `0008_alter_conversationparticipant_wallpaper_url`
    # (AlterField → max_length=1000) already added and finalized it. It had
    # only dropped out of this models.py source snapshot (drift between the
    # file and the applied migration history) — the field itself, and its
    # `max_length=1000`, must exactly match those two migrations so
    # `makemigrations` sees no difference here. The REST endpoint
    # (`ConversationViewSet.wallpaper` in views.py) was the actual missing
    # piece, not this field.
    wallpaper_url = models.URLField(max_length=1000, blank=True, null=True)

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

    # 🔥 NAYA — "Doubt Queue" ke anonymous-asking toggle. Teacher (admin/
    # moderator) is group ke settings se on/off karta hai
    # (`GroupSerializer` me already-wired 'message_permission' jaisa hi
    # writable field — GroupViewSet ka existing PATCH endpoint reuse
    # hota hai, koi naya endpoint nahi chahiye). Default True — students
    # ko out-of-the-box shy-friendly experience milta hai; teacher chahe
    # to band kar sakta hai.
    allow_anonymous_doubts = models.BooleanField(default=True)

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

    # 🔥 NAYA (ADVANCED FEATURE) — full-text search. Postgres `to_tsvector`
    # trigger se auto-populate hota hai (migration: `0900_message_search_
    # vector.py`), yahan sirf column define hai. `editable=False` — kabhi
    # bhi form/serializer se direct set nahi hona chahiye. NULL rehna theek
    # hai on non-Postgres DBs (`search_utils.py` khud `icontains` pe
    # fallback karta hai agar ye column absent/None ho).
    search_vector = SearchVectorField(null=True, blank=True, editable=False)

    # ⚠️ NOTE (this review) — E2EE fields. `is_encrypted`/`encryption_iv`
    # already existed on this model, but `Conversation.is_e2ee_enabled`
    # (referenced by the comment that used to be here) does NOT exist
    # anywhere in `Conversation` — nor does `e2ee_utils.py` exist in the
    # file map (§1 of the doc). So these two columns are currently dead:
    # nothing sets them, nothing reads them, and no client has anywhere to
    # turn E2EE on. Full E2EE (per-device keys, Double Ratchet/libsignal,
    # redesigning search/link-preview/AI-transcribe to skip ciphertext
    # messages) is a large, separate undertaking — see the response for
    # scoping. Left as-is here (not removed) since dropping columns is a
    # destructive migration; just documenting the actual state instead of
    # the aspirational one.
    is_encrypted = models.BooleanField(default=False, db_index=True)
    encryption_iv = models.CharField(max_length=64, blank=True, null=True)

    # 🔧 FIX (Feature 11 — Announcements, backend/frontend sync) — is field
    # ki zaroorat `views.py`'s `ConversationViewSet.messages` POST action ko
    # pehle se thi (`serializer.save(..., is_announcement=is_announcement)`),
    # lekin model pe column define hi nahi tha — isliye HAR REST message
    # send (group ya private, dono) `TypeError: 'is_announcement' is an
    # invalid keyword argument` de raha tha. True tab set hota hai jab
    # sender us waqt group ka admin/mod ho (private chat me hamesha False).
    is_announcement = models.BooleanField(default=False, db_index=True)

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
            models.Index(fields=['type']),
            models.Index(fields=['reply_to']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['conversation', 'is_pinned']),
            models.Index(fields=['is_scheduled', 'scheduled_for']),
            GinIndex(fields=['search_vector'], name='message_search_vector_gin'),
            # 🔥 NAYA (this session) — trigram GIN index on `text` itself,
            # for typo-tolerant matching. `search_vector` (tsvector) does
            # stemming/ranking but NOT fuzzy/typo matching — "helo" won't
            # match "hello" via to_tsquery. `pg_trgm` similarity search
            # (see `search_utils.py`) is what covers that case, and it
            # needs its own trigram-ops GIN index to stay fast at scale.
            GinIndex(fields=['text'], name='message_text_trgm_gin', opclasses=['gin_trgm_ops']),
        ]
        constraints = [
            # same sender ek hi client_id do baar submit kare to DB level pe hi block
            models.UniqueConstraint(
                fields=['conversation', 'sender', 'client_id'],
                name='unique_message_client_id',
                condition=models.Q(client_id__isnull=False),
            )
        ]


# ================= 3b. POLL (group/private poll messages) =================
# 🔥 NAYA — WhatsApp-style poll. `Message` khud (`type=MessageType.POLL`)
# sirf ek normal message row hai (list/search/pin/star/reply — sab wahi
# machinery Message ke liye already kaam karti hai, poll ko koi special
# case nahi banana pada). Poll ka apna structured data (question/options/
# votes) alag models me hai kyunki votes RELATIONAL hone chahiye (kisne
# vote kiya, kis option ko — `meta` JSONField me isko theek se query/
# unique-constrain nahi kar sakte the).
class Poll(BaseModel):
    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name='poll')
    question = models.CharField(max_length=500)
    # False = single-choice (ek user sirf 1 option choose kar sakta hai —
    # naya vote purane ko replace karta hai). True = multi-choice.
    allow_multiple_answers = models.BooleanField(default=False)

    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta(BaseModel.Meta):
        indexes = [models.Index(fields=['message'])]

    def __str__(self):
        return self.question


class PollOption(BaseModel):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=200)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta(BaseModel.Meta):
        ordering = ['order', 'created_at']
        indexes = [models.Index(fields=['poll'])]


class PollVote(BaseModel):
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_votes')

    class Meta(BaseModel.Meta):
        # Ek user ek hi option ko do baar vote nahi kar sakta. Single-choice
        # polls me "sirf 1 option" ka rule DB constraint se nahi, view-level
        # se enforce hota hai (`MessageViewSet.poll_vote` — naya vote se
        # pehle us poll ke andar user ke saare purane votes clear karta hai),
        # taaki allow_multiple_answers switch flexible rahe bina migration ke.
        unique_together = ('option', 'user')
        indexes = [
            models.Index(fields=['option']),
            models.Index(fields=['user']),
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

    # LiveKit — room name doubles as the join key for both calls (2h token
    # TTL) and study rooms (8h token TTL), see livekit_utils.py
    channel_name = models.CharField(max_length=150, unique=True, db_index=True)

    # 🔧 CLEANUP (this session) — removed the legacy `token` (Agora,
    # unused since the LiveKit migration — `livekit_utils.generate_
    # livekit_token` generates join tokens on demand instead of storing
    # one) and `is_recording`/`recording_url` (no REST/WS code anywhere
    # ever set or read them — LiveKit server-side recording/egress isn't
    # wired into this stack, see `models.py`'s ClassTranscriptSegment
    # design note) fields that used to live here. They were pure dead
    # weight that could mislead a client into showing a false "recording"
    # indicator. See migration 0903_remove_callsession_legacy_agora_fields
    # for the corresponding column drop.

    started_at = models.DateTimeField(default=timezone.now)
    connected_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(default=0)

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

    # 🔥 NAYA — Read-receipt privacy toggle (WhatsApp-style mutual switch).
    # `MessageStatus.is_read`/`read_at` (upar) hamesha internally record hote
    # rehte hain (message ke apne "unread" badge/`is_read_by_me` logic ko
    # kabhi affect nahi karna chahiye — wo sirf "maine padha ya nahi" hai).
    # Ye field sirf VISIBILITY control karta hai jab koi DOOSRE ka read
    # status dekhna chahe (naya `MessageViewSet.read_status` action):
    #   - False rakhne wale ka apna `read_at` kisi ko bhi (group members
    #     samet) nahi dikhta.
    #   - Isi tarah, agar DEKHNE waale ne khud ye False kar rakha hai, to
    #     use bhi doosron ka `read_at` nahi dikhta (mutual — jaisa WhatsApp
    #     karta hai: turn off both sending and seeing).
    # `is_delivered`/`delivered_at` is toggle se kabhi affect nahi hota
    # (WhatsApp me bhi delivery/double-tick hamesha visible rehta hai, sirf
    # "read"/blue-tick hi is switch se control hota hai).
    show_read_receipts = models.BooleanField(default=True)

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


# ======================================================================
# 🔥 NAYA — CLASS TRANSCRIPT (Feature 3: timestamped searchable recap)
# ------------------------------------------------------------
# Design note: LiveKit server-side room recording/egress abhi is stack
# me wired nahi hai (`CallSession.is_recording`/`recording_url` upar
# already dead fields hain — koi trigger/record code kahin nahi milta).
# Isliye "poori class ki ek continuous recording" is version me nahi
# banti. Iske bajaye har participant apna khud ka mic locally
# chunk-record karta hai (frontend: StudyRoomCallManager, ~45s chunks,
# `record` package — chat voice-note recording jaisa hi) aur har chunk
# yahan ek row banata hai. Har row = ek participant ke ek chunk ka
# transcript, session-relative offset ke saath. Combined (session_id +
# start_offset_seconds se sorted) sab participants ke segments milke ek
# time-ordered, searchable class transcript ban jaate hain.
#
# `session_id`: study room ka `channel_name` (CallSession upar, "channel_
# name doubles as join key for both calls and study rooms") reuse karna
# best hai agar `StudyRoomJoinView` LiveKit room ke liye wahi naming use
# kar raha hai — isse alag se ek naya session-id-generation scheme nahi
# banana padega, aur `new_session: true` naya room = naturally naya
# transcript bhi ho jaata hai. Yahan plain CharField isliye rakha hai
# (na ki FK) taaki StudyRoomJoinView chahe CallSession row banaye ya na
# banaye, ye model kisi bhi tarah se decoupled rahe.
# ======================================================================
class ClassTranscriptSegment(BaseModel):
    STATUS_PENDING = 'pending'
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_DONE, 'Done'),
        (STATUS_FAILED, 'Failed'),
    ]

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='transcript_segments',
    )
    session_id = models.CharField(max_length=150, db_index=True)

    speaker = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='transcript_segments',
    )

    # Session start (jab pehla participant join hua) se offset seconds me —
    # NOT wall-clock time, taaki "jump to timestamp" ek hi consistent
    # timeline pe kaam kare chahe kisi ka bhi phone clock skewed ho.
    start_offset_seconds = models.FloatField()
    end_offset_seconds = models.FloatField()

    # audio chunk ka file_url (`upload_view.py` se, chat voice-note jaisa
    # hi) — "is segment ko dobara sunna hai" ke liye rakha hai, poori class
    # ki continuous recording ki jagah per-segment playback.
    audio_file_url = models.URLField(max_length=500)

    text = models.TextField(blank=True, default='')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)

    class Meta(BaseModel.Meta):
        ordering = ['session_id', 'start_offset_seconds']
        indexes = [
            models.Index(fields=['conversation', 'session_id', 'start_offset_seconds']),
            models.Index(fields=['conversation', 'status']),
        ]

    def __str__(self):
        return f"{self.conversation_id}/{self.session_id} @{self.start_offset_seconds}s"

    # 🔥 NOTE (scale upgrade path) — abhi search `text__icontains` se hota
    # hai (views_ai.ClassTranscriptSearchView), chhoti class-transcript ke
    # liye (ek session = usually kuch sau segments) kaafi hai. Agar scale
    # badhe, `Message` model upar jaisa hi pattern lagao: naya
    # `search_vector = SearchVectorField(...)` field + migration trigger +
    # `GinIndex(fields=['search_vector'], ...)` — same approach, alag se
    # kuch naya design nahi karna padega.


# ======================================================================
# 🔥 NAYA — REVISION DECK (Feature 5: auto flashcards/quiz for revision)
# ------------------------------------------------------------
# `ai_service.generate_revision_deck()` ka output yahan persist hota hai
# (summary/quiz jaisa throwaway response NAHI hai — student ko exam se
# pehle bina regenerate kiye baar-baar khud revise karna hota hai, isliye
# ek row save karke rakhte hain). Har naya "Generate Revision Deck" tap
# ek naya row banata hai (history rakhte hain — purana deck bhi kaam aa
# sakta hai agar naya content thoda kam ho); frontend latest wala
# (`-created_at` default ordering, `BaseModel.Meta`) fetch karta hai.
# ======================================================================
class RevisionDeck(BaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='revision_decks',
    )
    # Optional — kis study-room session ke content se ye deck bana, taaki
    # future me "is specific class ka revision deck do" bhi possible ho.
    # Blank chhod sakte ho jab deck poore-conversation-level content
    # (multiple sessions/chat combined) se bana ho.
    session_id = models.CharField(max_length=150, blank=True, default='', db_index=True)

    flashcards = models.JSONField(default=list, blank=True)
    quiz = models.JSONField(default=list, blank=True)

    generated_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+',
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
        ]

    def __str__(self):
        return f"RevisionDeck({self.conversation_id}, {len(self.flashcards)} cards)"


# ======================================================================
# 🔥 NAYA — STUDY ROOM ATTENDANCE (Feature 6: consistency streak)
# ------------------------------------------------------------
# `StudyRoomJoinView` (views.py) pehle sirf ek in-memory/cache-based
# "current session" pointer rakhta tha — koi permanent per-user join
# history kabhi persist nahi hoti thi (confirmed: `CallParticipant` sirf
# 1:1/group CALLS ke liye hai, study-room join usko touch hi nahi karta).
# Isliye "SessionParticipant se free mein nikal sakte ho" wali assumption
# sahi nahi thi — ye table hi streak ka data-source banega, ab se har
# study-room join yahan ek row banayega (`StudyRoomJoinView.post()` me).
#
# `attended_date` deliberately ek alag DateField hai (`created_at` ka
# `.date()` nahi) — agar kabhi timezone-aware "attendance day" ko server
# timezone ke bajaye kisi aur logic se decide karna pade (e.g. class ka
# apna timezone), ye field independently set/backfill ho sakta hai bina
# `created_at` (jo BaseModel khud manage karta hai) ko chhede.
# ======================================================================
class StudyRoomAttendance(BaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name='study_room_attendances',
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='study_room_attendances',
    )
    session_id = models.CharField(max_length=150, blank=True, default='')
    attended_date = models.DateField(default=timezone.localdate, db_index=True)

    class Meta(BaseModel.Meta):
        indexes = [
            # Streak calculation ka main query: "is user ne is conversation
            # me kin-kin dates pe attend kiya" — descending date order me.
            models.Index(fields=['conversation', 'user', '-attended_date']),
        ]
        # Same din multiple baar join karna (disconnect-reconnect, leave-
        # rejoin) ek hi din ki attendance count honi chahiye, do din ki
        # nahi — isliye (conversation, user, attended_date) par unique.
        constraints = [
            models.UniqueConstraint(
                fields=['conversation', 'user', 'attended_date'],
                name='unique_study_room_attendance_per_day',
            )
        ]

    def __str__(self):
        return f"{self.user_id} attended {self.conversation_id} on {self.attended_date}"


# ============================================================================
# DOUBT QUEUE — persistent, upvotable per-classroom(group) question board
# ============================================================================
class DoubtQuestion(BaseModel):
    """
    Ek student ka doubt/question, ek group (classroom) ke andar. Chat ke
    normal message-stream se ALAG hai — apna khud ka "Doubts" tab/endpoint
    hai, taaki same doubt baar-baar alag messages me na bikhre aur
    upvote-count se sabse common doubt upar aa jaaye.

    `group` + `conversation` dono rakhe hain: `group` queries/permission ke
    liye (`group_rules.is_group_admin_or_mod(doubt.group, ...)`), `conversation`
    isliye taaki WS broadcast `chat_{conversation_id}` room reuse ho sake
    (ChatConsumer already isi room se connected hai — koi naya WS group
    banane ki zaroorat nahi).

    `author` HAMESHA asli poochne wala user hi hota hai, chahe `is_anonymous`
    True ho ya False — anonymity sirf DISPLAY-level hai
    (`DoubtQuestionSerializer.get_author`), DB me identity kabhi khoti nahi
    (moderation/abuse ke liye zaroori hai, aur teacher `reveal` action se
    dobara dekh sakta hai).
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='doubts')
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='doubts')
    author = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='doubts_asked',
    )
    text = models.TextField(max_length=2000)

    # Display-level anonymity (see docstring above) — `is_anonymous=True`
    # par bhi `author` column me asli user hi save hota hai.
    is_anonymous = models.BooleanField(default=False)
    # 🔥 Teacher ne is anonymous doubt ke liye `reveal` action call ki hai —
    # ab admin/moderator ko is doubt ke `author` ka naam dikhega (student ko
    # khud ko aur baaki students ko abhi bhi nahi dikhega). One-way flag,
    # kabhi wapas False nahi hota (jo dikh chuka wo dikh chuka).
    is_revealed = models.BooleanField(default=False)

    # Denormalized counter — `DoubtUpvote` ki `.count()` baar baar na chalani
    # pade (list view sabse zyada-hit query hai, "sabse upvoted upar"
    # ordering ke saath), `upvote`/`un-upvote` action isko `F()` se atomically
    # update karta hai.
    upvotes_count = models.PositiveIntegerField(default=0)

    is_answered = models.BooleanField(default=False)
    answer_text = models.TextField(blank=True, default='')
    answered_by = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='doubts_answered',
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        # Sabse-upvoted pehle, phir naya-pehle — "Doubts" tab default view
        # exactly isi order me list karta hai taaki teacher ko common doubt
        # sabse pehle dikhe.
        ordering = ['-upvotes_count', '-created_at']
        indexes = [
            models.Index(fields=['group', 'is_answered', '-upvotes_count']),
        ]


class DoubtUpvote(BaseModel):
    """
    "मुझे भी yahi doubt hai" — ek user ek doubt ko sirf ek baar upvote kar
    sakta hai (`unique_together`), toggle off karne ke liye row delete hoti
    hai. Apna khud ka doubt bhi upvote kar sakta hai (server explicitly
    isko block nahi karta — WhatsApp poll me bhi khud ka option select karna
    allowed hota hai, yahan bhi wahi spirit hai; agar chaho to
    `DoubtQuestionViewSet.upvote` me ek line se `author == user` block kar
    sakte ho).
    """
    doubt = models.ForeignKey(DoubtQuestion, on_delete=models.CASCADE, related_name='upvotes')
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='doubt_upvotes',
    )

    class Meta(BaseModel.Meta):
        unique_together = ('doubt', 'user')


# ======================================================================
# 🔥 NAYA — PARENT / GUARDIAN MODE (Feature 8)
# ------------------------------------------------------------
# Scope, deliberately narrow: read-only, AGGREGATE-only view for a
# parent/guardian. NEVER chat content, NEVER message text, NEVER contact
# details beyond the student's display name. Only:
#   - attendance (reuses `StudyRoomAttendance`, per classroom)
#   - assignment status (new `Assignment`/`AssignmentSubmission` below —
#     this app had NO assignment concept anywhere before this; it's new
#     groundwork, added specifically so "assignment pending" has real
#     data instead of being a placeholder number)
#
# Parents do NOT get a normal `User` row / login here. Flow:
#   1. Student generates a "Parent Code" from their own app
#      (`ParentAccessCodeView`, needs the student's own login).
#   2. Student shares that code with the parent (WhatsApp/SMS/in person).
#   3. Parent opens "Parent Mode" in the app (no student login needed),
#      types the code in -> `ParentVerifyCodeView` checks it and returns
#      a `parent_token` that can ONLY ever hit the `/parent/...`
#      read-only routes (`permissions.HasValidParentToken`).
#
# One code = one (student, relationship-label) pair — a student can hand
# separate codes to mom and dad and revoke either independently, without
# touching their own main account/login at all.
# ======================================================================
class ParentAccessCode(BaseModel):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='parent_access_codes',
    )
    # Cosmetic — lets the STUDENT tell their own codes apart in their
    # "Manage parent access" screen ("Mom", "Papa", ...). Never shown to
    # the parent, no effect on access/permissions.
    label = models.CharField(max_length=50, blank=True, default='')

    # Short, easy to read-aloud/type code. NOT the long-term secret by
    # itself — see `throttles.ParentCodeVerifyThrottle` for brute-force
    # protection on the verify endpoint; the real bearer credential is
    # the `ParentToken` issued after a successful verify.
    code = models.CharField(max_length=12, unique=True, db_index=True)

    is_active = models.BooleanField(default=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    # 🔧 GAP FIX — TTL / auto-expiry. Pehle code + tokens dono hamesha
    # valid rehte the jab tak student khud revoke na kare — parent ka
    # phone kho jaaye to unauthorized access indefinitely chalta rehta
    # tha. Ab har code ki ek absolute expiry hai; expire hone ke baad
    # (a) naya `ParentToken` verify NAHI ho sakta (`ParentVerifyCodeView`
    # 410 deta hai), aur (b) is code se pehle se verify hue saare
    # tokens bhi turant invalid ho jaate hain (`HasValidParentToken`
    # check karta hai) — matlab ek expired code effectively `is_active
    # =False` jaisa hi behave karta hai, bas student ke explicit revoke
    # ke bina. Student "Renew" tap karke isi code ko fresh TTL de sakta
    # hai bina parent ko naya code dobara share kiye (`renew()` neeche).
    DEFAULT_TTL_DAYS = 180  # ~6 mahine — school-term jaisa reasonable default

    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # 🔧 GAP FIX — reveal-once exposure surface. Pehle GET har baar
    # plaintext `code` return karta tha — "Manage parent access" screen
    # khulte hi saare active codes plain text me dikh jaate the. Ab list
    # sirf `masked_code` dikhata hai; poora plaintext sirf 2 jagah milta
    # hai: (a) generation ke turant baad (POST response — natural
    # "reveal once at creation" moment), (b) student explicitly
    # `POST /parent/codes/<id>/reveal/` tap kare — throttled + audited
    # (`last_revealed_at`), taaki compromised session/device se bulk-
    # scrape na ho sake, aur student ko khud pata rahe last baar kab
    # dobara dekha gaya tha.
    last_revealed_at = models.DateTimeField(null=True, blank=True)

    @property
    def masked_code(self):
        # e.g. "7F3K9QRT" -> "••••9QRT" — aakhri 4 chars kaafi hain
        # student ke liye "haan yahi wala code hai" confirm karne ko,
        # bina poora plaintext expose kiye.
        if len(self.code) <= 4:
            return '•' * len(self.code)
        return ('•' * (len(self.code) - 4)) + self.code[-4:]

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['student', 'is_active']),
            models.Index(fields=['is_active', 'expires_at']),
        ]

    @staticmethod
    def _generate_code():
        # No 0/O/1/I — avoids a parent misreading/mistyping a shared code.
        alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
        return ''.join(secrets.choice(alphabet) for _ in range(8))

    @classmethod
    def generate_for(cls, student, label='', ttl_days=None):
        ttl_days = ttl_days if ttl_days is not None else cls.DEFAULT_TTL_DAYS
        for _ in range(5):
            code = cls._generate_code()
            if not cls.objects.filter(code=code).exists():
                return cls.objects.create(
                    student=student,
                    label=label,
                    code=code,
                    expires_at=timezone.now() + timedelta(days=ttl_days),
                )
        raise RuntimeError("Parent code generate nahi ho paaya, dobara try karo.")

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    def renew(self, ttl_days=None):
        """Same code/devices ko fresh TTL do — parent ko dobara verify
        karne ki zaroorat nahi padti, sirf student side se ek tap."""
        ttl_days = ttl_days if ttl_days is not None else self.DEFAULT_TTL_DAYS
        self.expires_at = timezone.now() + timedelta(days=ttl_days)
        self.is_active = True
        self.save(update_fields=['expires_at', 'is_active', 'updated_at'])
        return self

    def __str__(self):
        return f"ParentAccessCode({self.student_id}, {self.label or 'unnamed'})"


class ParentToken(BaseModel):
    """
    Verify hone ke baad ka asli bearer credential jo parent ki app store
    karti hai (student ke `access_token` jaisa hi idea, bas scope bahut
    chhota — sirf `/parent/...` read-only routes, kabhi bhi normal
    chat/message/group endpoint hit nahi kar sakta — enforced in
    `permissions.HasValidParentToken`, jo `request.user` ko bilkul chhoo
    ta nahi, taaki ye kahin bhi "student khud request kar raha hai" jaisa
    accidentally treat na ho jaaye).

    Code se decouple isliye kiya hai: (a) parent apna phone badle to
    student ko naya code nahi dena padta — parent dobara same code
    verify kare, naya token mil jaata hai; (b) student jab chahe saara
    access ek saath `ParentAccessCode.is_active=False` karke revoke kar
    sakta hai, sabhi is code ke tokens automatically invalid ho jaate hain.
    """
    parent_access_code = models.ForeignKey(
        ParentAccessCode, on_delete=models.CASCADE, related_name='tokens',
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # 🔧 GAP FIX — rolling inactivity expiry, independent of the parent
    # code's own `expires_at`. Ek device 30 din tak dashboard hit nahi
    # karta (phone kho gaya/app uninstall/parent ne bas dekhna band kar
    # diya) to *sirf wo device* apne aap invalid ho jaata hai — student
    # ko manually revoke karne ki zaroorat nahi, aur baaki devices
    # (agar koi active hain) untouched rehte hain. Note: is check ke
    # kaam karne ke liye `last_seen_at` ko har successful dashboard
    # fetch pe update hona zaroori hai — see `HasValidParentToken`.
    INACTIVITY_TTL_DAYS = 30

    @staticmethod
    def generate_token():
        return secrets.token_urlsafe(32)

    @property
    def is_expired(self):
        reference = self.last_seen_at or self.created_at
        if reference is None:
            return False
        return timezone.now() - reference > timedelta(days=self.INACTIVITY_TTL_DAYS)

    def touch(self):
        """Mark this device as seen right now — call on every authenticated
        parent-side request so inactivity expiry is measured correctly."""
        self.last_seen_at = timezone.now()
        self.save(update_fields=['last_seen_at', 'updated_at'])


# ----------------------------------------------------------------------
# ASSIGNMENTS — minimal tracking, added so "assignment pending" in the
# parent dashboard is real data. Nothing else in the app touches this
# yet (no teacher-facing "create assignment" UI included here) — this is
# intentionally the smallest schema that supports the parent-view number;
# a teacher-facing create/submit flow can build on top without changing
# this shape.
#
# 🔧 FIX (this session) — `related_name` on both FKs below renamed
# (`assignments` -> `message_assignments`, `assignment_submissions` ->
# `message_assignment_submissions`) because a separate `liveclass` app
# already defines its own `Assignment`/`AssignmentSubmission` models with
# the SAME related_names pointing at the SAME `User`/`Group` models.
# Django can't register two identical reverse accessors on `User`, so
# `makemigrations` failed with fields.E304/E305 until these were made
# unique. NOTE: if `liveclass.Assignment` is meant to be the SAME concept
# as this one (not a different feature that happens to share a name), the
# better long-term fix is to delete this duplicate pair entirely and have
# the parent-dashboard code query `liveclass.Assignment` instead — see
# chat discussion. Kept here for now since that's a bigger structural
# decision than a naming clash fix.
# ----------------------------------------------------------------------
class Assignment(BaseModel):
    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name='message_assignments',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    due_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta(BaseModel.Meta):
        indexes = [
            models.Index(fields=['group', 'due_at']),
        ]

    def __str__(self):
        return f"{self.title} ({self.group_id})"


class AssignmentSubmission(BaseModel):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='submissions')
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='message_assignment_submissions',
    )
    is_submitted = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta(BaseModel.Meta):
        unique_together = ('assignment', 'student')
        indexes = [
            models.Index(fields=['student', 'is_submitted']),
        ]


# ----------------------------------------------------------------------
# 🔧 FIX (merged from models_focus.py) — Feature 12: Smart DND / Focus
# Mode. Was documented as a separate merge-in file that was never
# actually pasted into models.py, so `from .models import FocusSession`
# in views_focus.py raised ImportError the moment that file got
# imported. `BaseModel`, `models`, and `settings` are already imported
# at the top of this file — no new imports needed.
# ----------------------------------------------------------------------
class FocusSession(BaseModel):
    """
    Ek active "focus window" — student ne khud set kiya hai ki agle N
    minutes/hours sirf teacher/staff ke pings aayenge, baaki sab mute.

    Ek time pe user ka sirf EK active session hota hai — naya start
    purane ko replace kar deta hai (extend/overwrite), stack nahi hote.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="focus_sessions",
    )
    starts_at = models.DateTimeField(auto_now_add=True)
    ends_at = models.DateTimeField(db_index=True)

    # Exception rule — kaun ke messages phir bhi through aayenge.
    # 'teachers_only' (default) = sirf un groups ke admin/moderator jinme
    # user member hai. 'nobody' = poora silence, koi bhi exception nahi
    # (hard focus mode, exam jaisa).
    class ExceptionRule(models.TextChoices):
        TEACHERS_ONLY = "teachers_only", "Only teachers/staff"
        NOBODY = "nobody", "Nobody — full silence"

    exception_rule = models.CharField(
        max_length=20,
        choices=ExceptionRule.choices,
        default=ExceptionRule.TEACHERS_ONLY,
    )

    # user ne khud jab band kiya (auto-expire se pehle) — analytics/UI ke
    # liye useful ("cancelled early" vs "ran full duration").
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-starts_at"]
        indexes = [
            models.Index(fields=["user", "ends_at"]),
        ]

    def __str__(self):
        return f"FocusSession(user={self.user_id}, ends_at={self.ends_at}, rule={self.exception_rule})"

    @property
    def is_active(self):
        return self.cancelled_at is None and self.ends_at > timezone.now()

    @classmethod
    def get_active_for_user(cls, user_id):
        """Returns the active FocusSession for a user, or None."""
        return (
            cls.objects.filter(
                user_id=user_id,
                ends_at__gt=timezone.now(),
                cancelled_at__isnull=True,
            )
            .order_by("-starts_at")
            .first()
        )

    @classmethod
    def start_for_user(cls, user, duration_minutes, exception_rule=ExceptionRule.TEACHERS_ONLY):
        """
        Race-safe-ish start: purana active session (agar hai) cancel kar
        ke naya bana do — ek user ka ek hi session zinda rehta hai.
        """
        cls.objects.filter(
            user=user, ends_at__gt=timezone.now(), cancelled_at__isnull=True
        ).update(cancelled_at=timezone.now())
        return cls.objects.create(
            user=user,
            ends_at=timezone.now() + timedelta(minutes=duration_minutes),
            exception_rule=exception_rule,
        )