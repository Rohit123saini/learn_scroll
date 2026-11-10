"""
message/study_room_safety.py

S8 — Discovery -> study room safety.

Problem: `/groups/discover/` me koi bhi PUBLIC group join kar leta tha aur seedha study room me aa jata tha — na
approval, na rules, room me block ka koi tareeqa nahi, aur minors ka naam/photo bhi adult strangers ko dikhta tha.

Is module me (pure helpers, views inhe call karte hain):

  1. JOIN APPROVAL      `join_decision(group, user)` — instant join ya `GroupJoinRequest` (pending):
                          * private group                       -> approval (purana flow)
                          * `Group.join_approval_required`      -> approval (admin ka option)
                          * minor + `MINOR_PUBLIC_GROUP_JOIN_APPROVAL` -> approval (post.minor_privacy)
                        Group admin/mod ko approval nahi lagta jab wo khud add karte hain (`add_members`).
  2. ROOM RULES         default rules (EN + HI) + group ke apne rules (`Group.room_rules`, version);
                        `rules_gate_denial` public group ke study room me aane se pehle accept maangta hai.
  3. MINOR PRIVACY      `masked_user_ids` — public group me minor ka naam (first name) / photo (None) adult
                        strangers se chhupta hai (group admin/mod aur doosre minors ko real). Rule `post/minor_privacy.py`
                        me hai (`MINOR_PUBLIC_ROOM_MASK_IDENTITY`).
  4. BLOCK              `block_pair_ids` / `blocked_by_me_ids` — room me block karne ke baad dono taraf ke events hide hote
                        hain (consumer filter + client hide).

Settings (sab optional, `LearnScroll/settings.py`): STUDY_ROOM_RULES_REQUIRED_PUBLIC (1),
STUDY_ROOM_REPORT_AUTO_REMOVE_THRESHOLD (0 = off), STUDY_ROOM_REPORT_AUTO_REMOVE_WINDOW_MINUTES (60),
MINOR_PUBLIC_ROOM_MASK_IDENTITY (1), MINOR_PUBLIC_GROUP_JOIN_APPROVAL (1), MINOR_PUBLIC_ROOM_CAMERA_OFF (1).
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from post import minor_privacy

from .group_rules import is_group_admin_or_mod
from .models import (
    BlockedUser,
    Conversation,
    ConversationType,
    GroupJoinRequest,
    GroupRulesAcceptance,
)
from .user_display import get_display_name, get_profile_photo_url

logger = logging.getLogger(__name__)

MAX_ROOM_RULES_CHARS = 2000


# ----------------------------------------------------------------------
# Default rules (admin ne apne rules nahi likhe ho to ye dikhte hain)
# ----------------------------------------------------------------------
DEFAULT_ROOM_RULES = [
    {
        'id': 'respect',
        'en': "Be respectful. No abuse, bullying or mocking anyone.",
        'hi': "सबके साथ सम्मान से बात करें। गाली-गलौज, बुलिंग या मज़ाक उड़ाना मना है।",
    },
    {
        'id': 'focus',
        'en': "The room is for studying. No spam, ads, promotions or off-topic links.",
        'hi': "रूम पढ़ाई के लिए है। स्पैम, विज्ञापन, प्रमोशन या विषय से हटकर लिंक नहीं।",
    },
    {
        'id': 'privacy',
        'en': "Never share personal info (phone, address, school, OTP, social handles) — yours or anyone else's.",
        'hi': "अपनी या किसी और की निजी जानकारी (फ़ोन, पता, स्कूल, OTP, सोशल हैंडल) शेयर न करें।",
    },
    {
        'id': 'no_recording',
        'en': "Do not record, screenshot or share anyone's video / photo without their permission.",
        'hi': "किसी की भी फ़ोटो, वीडियो, स्क्रीनशॉट या रिकॉर्डिंग बिना अनुमति शेयर न करें।",
    },
    {
        'id': 'safe_content',
        'en': "No adult, violent or inappropriate content on camera, screen share or whiteboard.",
        'hi': "कैमरा, स्क्रीन शेयर या व्हाइटबोर्ड पर कोई अश्लील, हिंसक या अनुचित कंटेंट नहीं।",
    },
    {
        'id': 'minors',
        'en': "Do not push students under 18 into private chats or meet-ups. If anyone makes you uncomfortable, Report or Block them.",
        'hi': "18 साल से कम उम्र के स्टूडेंट्स को प्राइवेट चैट या मिलने के लिए न बुलाएँ। कोई असहज करे तो रिपोर्ट या ब्लॉक करें।",
    },
    {
        'id': 'host',
        'en': "Follow the host / admin. Breaking rules can lead to mute, removal or a ban.",
        'hi': "होस्ट / एडमिन का फ़ैसला मानें। नियम तोड़ने पर म्यूट, रिमूव या बैन हो सकता है।",
    },
]


def default_rules_payload():
    return [dict(r) for r in DEFAULT_ROOM_RULES]


# ----------------------------------------------------------------------
# Group helpers
# ----------------------------------------------------------------------
def public_group_for(conversation):
    """Conversation public GROUP ka ho to uska `Group`, warna None (DM / private group)."""
    if conversation is None or conversation.type != ConversationType.GROUP:
        return None
    group = getattr(conversation, 'group_detail', None)
    if group is None or group.is_private:
        return None
    return group


def group_for(conversation):
    if conversation is None or conversation.type != ConversationType.GROUP:
        return None
    return getattr(conversation, 'group_detail', None)


def is_staff(group, user_id) -> bool:
    return bool(group) and is_group_admin_or_mod(group, user_id)


# ----------------------------------------------------------------------
# 1. Join approval
# ----------------------------------------------------------------------
class JoinDecision:
    """`needs_approval` + `reason` (private | group_setting | minor | '')."""
    __slots__ = ('needs_approval', 'reason')

    def __init__(self, needs_approval, reason=''):
        self.needs_approval = needs_approval
        self.reason = reason


def join_decision(group, user) -> JoinDecision:
    """Discover / invite-link se `user` ke join karne par: seedha member ya admin approval?"""
    if group.is_private:
        return JoinDecision(True, 'private')
    if group.join_approval_required:
        return JoinDecision(True, 'group_setting')
    if minor_privacy.public_group_join_needs_approval(user):
        return JoinDecision(True, 'minor')
    return JoinDecision(False, '')


def join_request_message(reason) -> str:
    if reason == 'minor':
        return "Is public group me join karne ke liye admin ki approval chahiye. Request bhej di gayi hai."
    return "Request bhej di gayi, group admin approve karega."


def request_status_map(user, group_ids) -> dict:
    """{group_id: 'pending' | 'rejected'} — discover list me "Requested" state ke liye (ONE query)."""
    ids = list(group_ids)
    if not ids:
        return {}
    rows = GroupJoinRequest.objects.filter(user=user, group_id__in=ids).values_list('group_id', 'status')
    return {gid: st for gid, st in rows if st in (GroupJoinRequest.Status.PENDING, GroupJoinRequest.Status.REJECTED)}


def create_or_reset_join_request(group, user):
    """
    `GroupJoinRequest` ko PENDING banao (naya ya purani reject wali row reset). Returns (row, state) —
    state: 'created' | 'pending' (pehle se pending, kuch nahi badla) | 'reset' (reject ke baad dobara).
    """
    row, created = GroupJoinRequest.objects.get_or_create(
        group=group, user=user, defaults={'status': GroupJoinRequest.Status.PENDING},
    )
    if created:
        return row, 'created'
    if row.status == GroupJoinRequest.Status.PENDING:
        return row, 'pending'
    row.status = GroupJoinRequest.Status.PENDING
    row.responded_by = None
    row.responded_at = None
    row.save(update_fields=['status', 'responded_by', 'responded_at', 'updated_at'])
    return row, 'reset'


def can_add_directly(group, actor, target_user) -> bool:
    """`add_members` (koi bhi member doosre ko add kare) — approval wale group / minor ke liye sirf admin/mod."""
    if actor is None:
        return True   # system caller (classroom bridge)
    if is_staff(group, actor.id):
        return True
    if group.is_private:
        return False
    if group.join_approval_required:
        return False
    return not minor_privacy.public_group_join_needs_approval(target_user)


# ----------------------------------------------------------------------
# 2. Room rules
# ----------------------------------------------------------------------
def rules_required_setting() -> bool:
    return bool(getattr(settings, 'STUDY_ROOM_RULES_REQUIRED_PUBLIC', True))


def rules_required_for(group, user) -> bool:
    """Public group ke study room me aane se pehle rules accept chahiye? (Group admin/mod exempt.)"""
    if group is None or group.is_private or not rules_required_setting():
        return False
    return not is_staff(group, user.id)


def has_accepted(group, user) -> bool:
    row = GroupRulesAcceptance.objects.filter(group=group, user=user).values_list('version', flat=True).first()
    return row is not None and row >= group.room_rules_version


def accept_rules(group, user, version=None):
    """Accept record (idempotent). `version` None => current. Returns (row, current_version_matched)."""
    current = group.room_rules_version
    if version is not None and int(version) != current:
        return None, False
    row, created = GroupRulesAcceptance.objects.get_or_create(
        group=group, user=user, defaults={'version': current, 'accepted_at': timezone.now()},
    )
    if not created and row.version != current:
        row.version = current
        row.accepted_at = timezone.now()
        row.save(update_fields=['version', 'accepted_at', 'updated_at'])
    return row, True


def clean_rules_text(value) -> str:
    text = str(value or '').replace('\r\n', '\n').strip()
    return text[:MAX_ROOM_RULES_CHARS]


def rules_payload(group, user) -> dict:
    """`GET study-room/<id>/rules/` / discover pre-join sheet ka body."""
    staff = is_staff(group, user.id) if group is not None else False
    return {
        'group_id': str(group.id),
        'group_name': group.name,
        'is_public': not group.is_private,
        'version': group.room_rules_version,
        'default_rules': default_rules_payload(),
        'custom_rules': group.room_rules or '',
        'accepted': True if staff else has_accepted(group, user),
        'required': rules_required_for(group, user),
        'can_edit': staff,
        'join_approval_required': bool(group.join_approval_required),
        'privacy': minor_privacy.room_privacy_for(user),
    }


def rules_gate_denial(group, user):
    """Join / refresh gate: None (aa sakta hai) ya (detail, code, http_status)."""
    if not rules_required_for(group, user):
        return None
    if has_accepted(group, user):
        return None
    return ("Study room me aane se pehle group ke rules accept karo.", 'rules_not_accepted', 403)


# ----------------------------------------------------------------------
# 3. Minor privacy in public rooms
# ----------------------------------------------------------------------
def masked_user_ids(viewer, conversation, user_ids) -> set:
    """
    Ids (from `user_ids`) jinka naam / photo `viewer` se chhupana hai. Sirf PUBLIC group me; viewer khud kabhi nahi.
    Group admin/mod viewer ya minor viewer => kuch mask nahi (moderation / same-age). ONE query (`minors_among`).
    """
    group = public_group_for(conversation)
    if group is None or not minor_privacy.public_room_mask_enabled():
        return set()
    ids = {u for u in user_ids if u is not None and str(u) != str(viewer.id)}
    if not ids:
        return set()
    if is_staff(group, viewer.id) or minor_privacy.treated_as_minor(viewer):
        return set()
    return minor_privacy.minors_among(ids)


def person_fields(user, masked, request=None) -> dict:
    """`display_name` + `avatar_url` + `masked` — masked => first name only, photo None."""
    if masked:
        return {'display_name': minor_privacy.masked_display_name(user), 'avatar_url': None, 'masked': True}
    return {
        'display_name': get_display_name(user),
        'avatar_url': get_profile_photo_url(user, request=request),
        'masked': False,
    }


def token_display_name(conversation, user) -> str:
    """LiveKit token me jaane wala naam (SAB ko dikhta hai) — public group me minor ka naam masked."""
    if public_group_for(conversation) is not None and minor_privacy.public_room_mask_enabled() \
            and minor_privacy.treated_as_minor(user):
        return minor_privacy.masked_display_name(user)
    return get_display_name(user)


def room_privacy_hints(conversation, user) -> dict:
    """Join response `privacy`: client hints (camera default off, masked) — sirf public group me active."""
    hints = minor_privacy.room_privacy_for(user)
    if public_group_for(conversation) is None:
        return {'is_minor': hints['is_minor'], 'mask_identity': False, 'camera_default_off': False,
                'join_needs_approval': False}
    return hints


# ----------------------------------------------------------------------
# 4. Block (room)
# ----------------------------------------------------------------------
def blocked_by_me_ids(user_id) -> set:
    """Wo user ids jinhe `user_id` ne block kiya."""
    return set(BlockedUser.objects.filter(blocker_id=user_id).values_list('blocked_id', flat=True))


def block_pair_ids(user_id) -> set:
    """Dono direction: jinhe `user_id` ne block kiya + jinhone `user_id` ko block kiya. (Room events dono taraf hide.)"""
    rows = BlockedUser.objects.filter(Q(blocker_id=user_id) | Q(blocked_id=user_id)).values_list('blocker_id', 'blocked_id')
    out = set()
    for a, b in rows:
        out.add(b if a == user_id else a)
    return out


def block_user(blocker, blocked_user):
    """
    Idempotent block (existing `BlockedUser` table; `user_profile` ka `BlockUser` signal se mirror hota hai).
    Soft-deleted purani row ho to wahi revive hoti hai (unique_together ke wajah se naya row ban hi nahi sakta).
    Returns (row, created).
    """
    row = BlockedUser.all_objects.filter(blocker=blocker, blocked=blocked_user).first()
    if row is None:
        return BlockedUser.objects.create(blocker=blocker, blocked=blocked_user), True
    if row.is_deleted:
        row.is_deleted = False
        row.save(update_fields=['is_deleted', 'updated_at'])
        # revive par post_save `created=False` hota hai => signal mirror nahi chalta; profile table yahin sync.
        try:
            from user_profile.models import BlockUser
            BlockUser.objects.get_or_create(blocker=blocker, blocked=blocked_user)
        except Exception:
            logger.exception("block mirror to profile failed blocker=%s blocked=%s", blocker.id, blocked_user.id)
        return row, True
    return row, False


def unblock_user(blocker, blocked_user_id) -> bool:
    deleted, _ = BlockedUser.objects.filter(blocker=blocker, blocked_id=blocked_user_id).delete()
    return bool(deleted)


# ----------------------------------------------------------------------
# 5. Report -> auto remove  (room me dher saari alag-alag reports => session se hata do)
# ----------------------------------------------------------------------
def auto_remove_threshold() -> int:
    try:
        return max(0, int(getattr(settings, 'STUDY_ROOM_REPORT_AUTO_REMOVE_THRESHOLD', 0) or 0))
    except (TypeError, ValueError):
        return 0


def auto_remove_window() -> timedelta:
    try:
        minutes = max(1, int(getattr(settings, 'STUDY_ROOM_REPORT_AUTO_REMOVE_WINDOW_MINUTES', 60) or 60))
    except (TypeError, ValueError):
        minutes = 60
    return timedelta(minutes=minutes)


def maybe_auto_remove(conversation, reported_user_id) -> bool:
    """
    Window (default 60 min) me `threshold` ya usse zyada ALAG reporters ne is user ko report kiya ho to usse
    chalte session se hata do (status `removed` — wapas aane ke liye host `admit` kare). Default OFF (threshold 0).
    Host / co-host / group admin-mod kabhi auto-remove nahi hote (report-bombing se lockout na ho). Best-effort:
    kuch fail ho to report phir bhi save rehti hai. Returns True agar abhi remove kiya.
    """
    threshold = auto_remove_threshold()
    if threshold <= 0:
        return False
    try:
        from . import livekit_utils as lk
        from . import study_room_events as sr_events
        from . import study_room_roles as sr_roles
        from .models import StudyRoomParticipant, StudyRoomReport
        from .study_room_session import get_active_session

        session = get_active_session(conversation.id)
        if session is None:
            return False
        since = timezone.now() - auto_remove_window()
        distinct_reporters = (
            StudyRoomReport.objects.filter(conversation=conversation, reported_user_id=reported_user_id,
                                           created_at__gte=since)
            .exclude(status=StudyRoomReport.Status.DISMISSED)
            .values('reporter_id').distinct().count()
        )
        if distinct_reporters < threshold:
            return False
        row, _ = StudyRoomParticipant.objects.get_or_create(
            session=session, user_id=reported_user_id, defaults={'status': StudyRoomParticipant.Status.ADMITTED},
        )
        role = sr_roles.effective_role(conversation, session, reported_user_id, row)
        if sr_roles.is_manager(role) or row.status == StudyRoomParticipant.Status.REMOVED:
            return False
        row.status = StudyRoomParticipant.Status.REMOVED
        row.hand_raised = False
        row.hand_raised_at = None
        row.save(update_fields=['status', 'hand_raised', 'hand_raised_at', 'updated_at'])
        lk.remove_participant(session.room_name, str(reported_user_id))
        sr_events.broadcast(
            conversation.id, sr_events.EVT_REMOVED,
            {"userId": str(reported_user_id), "auto": True}, target_user_ids=[reported_user_id],
        )
        logger.warning("study room auto-remove conv=%s user=%s reporters=%s", conversation.id, reported_user_id,
                       distinct_reporters)
        return True
    except Exception:
        logger.exception("study room auto-remove failed conv=%s user=%s", getattr(conversation, 'id', None),
                         reported_user_id)
        return False


# ----------------------------------------------------------------------
# 6. Helpers for REST payloads / WS consumer
# ----------------------------------------------------------------------
def masked_for_managers(conversation, user) -> bool:
    """Viewer-agnostic mask (host ko "X andar aana chahta hai" push) — public group ka minor hamesha masked."""
    return (public_group_for(conversation) is not None and minor_privacy.public_room_mask_enabled()
            and minor_privacy.treated_as_minor(user))


def masked_identity_for(viewer, conversation_id, sender_id):
    """
    WS consumer: `sender_id` ka naam / photo `viewer` se chhupana hai? Haan => {'display_name', 'avatar_url': None},
    nahi => None. Client apna naam khud bhejta hai (`user_joined`, `quick_chat`) isliye masking server me bhi lagti hai.
    """
    conversation = (Conversation.objects.select_related('group_detail').filter(id=conversation_id).first())
    if conversation is None:
        return None
    if str(sender_id) not in {str(i) for i in masked_user_ids(viewer, conversation, [sender_id])}:
        return None
    from django.contrib.auth import get_user_model
    target = get_user_model().objects.filter(pk=sender_id).first()
    if target is None:
        return None
    return {'display_name': minor_privacy.masked_display_name(target), 'avatar_url': None}
