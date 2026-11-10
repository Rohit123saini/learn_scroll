"""
message/study_room_roles.py

S4 — study room ke roles, permissions aur join-gate (lock / waiting room / max participants / removed).

Roles (`StudyRoomParticipant.Role`):
  host         session shuru karne wala (`StudyRoomSession.started_by`)
  co_host      group admin / moderator (default) ya host ne promote kiya
  participant  normal member (mic + camera, share_policy ke hisaab se screen share / whiteboard)
  viewer       sirf dekh / sun sakta hai; raise-hand kar sakta hai

Group `study_room_permission` ab role se juda hai:
  EVERYONE     -> baaki members participant
  ADMINS_ONLY  -> admin/mod co-host; baaki members viewer (pehle 403 se room me aa hi nahi paate the)
Host / co-host `role` override kar sakta hai (`StudyRoomParticipant.role_override`), jo default se upar hai.

Settings (optional): STUDY_ROOM_DEFAULT_MAX_PARTICIPANTS (100), STUDY_ROOM_MAX_PARTICIPANTS_LIMIT (500),
STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM (True — public group ke naye session me waiting room default on).
"""

import logging

from django.conf import settings
from django.utils import timezone

from . import livekit_utils as lk
from .group_rules import is_group_admin_or_mod
from .models import (
    Conversation,
    ConversationType,
    StudyRoomParticipant,
    StudyRoomSession,
)
from .study_room_session import get_active_session

logger = logging.getLogger(__name__)

Role = StudyRoomParticipant.Role
Status = StudyRoomParticipant.Status
SharePolicy = StudyRoomSession.SharePolicy

MANAGER_ROLES = frozenset({Role.HOST, Role.CO_HOST})


def default_max_participants() -> int:
    return int(getattr(settings, 'STUDY_ROOM_DEFAULT_MAX_PARTICIPANTS', 100))


def max_participants_limit() -> int:
    return int(getattr(settings, 'STUDY_ROOM_MAX_PARTICIPANTS_LIMIT', 500))


def public_group_waiting_room() -> bool:
    return bool(getattr(settings, 'STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM', True))


def effective_max_participants(session) -> int:
    return int(session.max_participants or default_max_participants())


# ----------------------------------------------------------------------
# Roles
# ----------------------------------------------------------------------
def default_role(conversation, session, user_id) -> str:
    if session is not None and session.started_by_id == user_id:
        return Role.HOST
    if conversation.type == ConversationType.GROUP:
        group = getattr(conversation, 'group_detail', None)
        if group:
            if is_group_admin_or_mod(group, user_id):
                return Role.CO_HOST
            if group.study_room_permission != group.PermissionLevel.EVERYONE:
                return Role.VIEWER
    return Role.PARTICIPANT


def effective_role(conversation, session, user_id, row=None) -> str:
    if row is not None and row.role_override:
        return row.role_override
    return default_role(conversation, session, user_id)


def is_manager(role) -> bool:
    return role in MANAGER_ROLES


def can_act_on(actor_role, target_role) -> bool:
    """Host kisi pe (host ko chhod kar); co-host sirf participant / viewer pe."""
    if actor_role == Role.HOST:
        return target_role != Role.HOST
    if actor_role == Role.CO_HOST:
        return target_role in (Role.PARTICIPANT, Role.VIEWER)
    return False


def can_edit_board(role, share_policy) -> bool:
    """Whiteboard / sticky notes likhne ka haq."""
    if role in MANAGER_ROLES:
        return True
    if role == Role.VIEWER:
        return False
    return share_policy != SharePolicy.HOST_ONLY


# ----------------------------------------------------------------------
# WebSocket (`study_room_event`) gate
# ----------------------------------------------------------------------
# Hamesha sirf host / co-host: poora session khatam karna, poora board clear karna.
WS_MANAGER_ONLY = frozenset({'end_session', 'clear_board', 'clear_board_keep_text'})
# share_policy == host_only me sirf host / co-host: presentation / file / page + whiteboard editing.
WS_SHARE_ACTIONS = frozenset({
    'presentation_started', 'presentation_stopped', 'load_page_file', 'add_page', 'remove_page',
    'update_window', 'draw_point', 'add_text', 'add_shape', 'sticker', 'ruled_lines',
    'undo_user_stroke', 'undo_user_shape', 'undo_user_text',
})
# Viewer sirf in infra events ko relay kar sakta hai (baaki sab write maane jaate hain).
WS_VIEWER_ALLOWED = frozenset({'user_joined'})


def ws_action_allowed(action, role, share_policy, is_note_action=False):
    """(allowed: bool, code: str). `role=None` => room se removed / non-participant."""
    if role is None:
        return False, 'removed'
    if action in WS_MANAGER_ONLY and role not in MANAGER_ROLES:
        return False, 'host_required'
    if role == Role.VIEWER and action not in WS_VIEWER_ALLOWED:
        return False, 'view_only'
    if role == Role.PARTICIPANT and share_policy == SharePolicy.HOST_ONLY \
            and (action in WS_SHARE_ACTIONS or is_note_action):
        return False, 'host_only_share'
    return True, ''


def socket_role_info(conversation_id, user_id):
    """
    WebSocket gate ke liye (sync; consumer `database_sync_to_async` se bulata hai).
    Returns (role | None, share_policy). `None` = removed / denied (room ke events nahi bhej sakta).
    """
    conversation = Conversation.objects.filter(id=conversation_id).first()
    if conversation is None:
        return None, SharePolicy.EVERYONE
    session = get_active_session(conversation_id)
    row = None
    if session is not None:
        row = StudyRoomParticipant.objects.filter(session=session, user_id=user_id).first()
        if row is not None and row.status in (Status.REMOVED, Status.DENIED):
            return None, session.share_policy
        if row is not None and row.status == Status.WAITING:
            return None, session.share_policy
    return effective_role(conversation, session, user_id, row), (
        session.share_policy if session is not None else SharePolicy.EVERYONE
    )


# ----------------------------------------------------------------------
# Join gate
# ----------------------------------------------------------------------
class JoinDecision:
    """ok / waiting / denied. `denied` me (detail, code, http_status)."""

    def __init__(self, kind, row=None, role=None, denial=None):
        self.kind = kind
        self.row = row
        self.role = role
        self.denial = denial


def new_session_defaults(conversation):
    """Naye session ke liye waiting-room default: public group ka session waiting room ke saath."""
    if conversation.type == ConversationType.GROUP and public_group_waiting_room():
        group = getattr(conversation, 'group_detail', None)
        if group and not group.is_private:
            return {'waiting_room_enabled': True}
    return {}


def _present_count(session, exclude_user_id):
    """Room me abhi kitne log (cap check). LiveKit live truth; na mile to DB (admitted + joined) fallback."""
    identities = lk.list_room_identities(session.room_name)
    if identities is not None:
        return len(identities - {str(exclude_user_id)}), identities
    qs = StudyRoomParticipant.objects.filter(
        session=session, status=Status.ADMITTED, joined_at__isnull=False,
    ).exclude(user_id=exclude_user_id)
    return qs.count(), None


def evaluate_join(conversation, session, user) -> JoinDecision:
    """
    Token dene se pehle ka gate. Row create karta hai (pehli baar). Managers lock / waiting room / cap se exempt.
    """
    row, created = StudyRoomParticipant.objects.get_or_create(
        session=session, user=user, defaults={'status': Status.ADMITTED},
    )
    if row.status == Status.REMOVED:
        return JoinDecision('denied', row, denial=("Aapko is study room se hata diya gaya hai.", 'removed_from_room', 403))
    if row.status == Status.DENIED:
        return JoinDecision('denied', row, denial=("Host ne aapki entry decline ki hai.", 'denied_by_host', 403))

    role = effective_role(conversation, session, user.id, row)
    if is_manager(role):
        if row.status != Status.ADMITTED:
            row.status = Status.ADMITTED
            row.save(update_fields=['status', 'updated_at'])
        return JoinDecision('ok', row, role)

    already_joined = row.joined_at is not None and row.status == Status.ADMITTED

    if session.is_locked and not already_joined:
        return JoinDecision('denied', row, role, ("Room lock hai. Host se kaho ki unlock kare.", 'room_locked', 423))

    # Nayi row default `admitted` banti hai — isliye "admitted" tab maana jaata hai jab user pehle room me aa
    # chuka ho (`joined_at`) ya host ne khud admit kiya ho (`decided_by`). Warna waiting room me.
    admitted_explicitly = row.status == Status.ADMITTED and (
        row.joined_at is not None or row.decided_by_id is not None
    )
    if session.waiting_room_enabled and not admitted_explicitly:
        if row.status != Status.WAITING:
            row.status = Status.WAITING
            row.save(update_fields=['status', 'updated_at'])
        return JoinDecision('waiting', row, role)

    cap = effective_max_participants(session)
    present, identities = _present_count(session, user.id)
    if present >= cap and not (identities and str(user.id) in identities):
        return JoinDecision('denied', row, role, ("Room full hai.", 'room_full', 409))

    return JoinDecision('ok', row, role)


def mark_joined(row):
    row.joined_at = timezone.now()
    row.save(update_fields=['joined_at', 'updated_at'])


def token_context(conversation, session, user):
    """
    Refresh-token ke liye: (role, share_policy, denial|None). Removed / denied / waiting row ko token nahi.
    Session ya row na ho (legacy room) to default role.
    """
    if session is None:
        return effective_role(conversation, None, user.id), SharePolicy.EVERYONE, None
    row = StudyRoomParticipant.objects.filter(session=session, user=user).first()
    if row is not None:
        if row.status == Status.REMOVED:
            return None, session.share_policy, ("Aapko is study room se hata diya gaya hai.", 'removed_from_room', 403)
        if row.status == Status.DENIED:
            return None, session.share_policy, ("Host ne aapki entry decline ki hai.", 'denied_by_host', 403)
        if row.status == Status.WAITING:
            return None, session.share_policy, ("Aap waiting room me hain.", 'waiting', 403)
        if row.joined_at is None:
            # Row hai par join kabhi token tak pahuncha hi nahi (lock / cap / waiting me ruka tha) —
            # refresh-token se gate bypass nahi hona chahiye.
            return None, session.share_policy, ("Pehle room join karo.", 'not_joined', 403)
    elif session.is_locked or session.waiting_room_enabled:
        # Row nahi + room lock / waiting room: sirf join se aana hai (purane, deploy se pehle ke
        # session me jahan ye settings off hain, bina row wale chalte log drop nahi hote).
        return None, session.share_policy, ("Pehle room join karo.", 'not_joined', 403)
    return effective_role(conversation, session, user.id, row), session.share_policy, None


def room_settings_payload(session) -> dict:
    return {
        'is_locked': session.is_locked,
        'waiting_room_enabled': session.waiting_room_enabled,
        'max_participants': effective_max_participants(session),
        'share_policy': session.share_policy,
        'host_id': str(session.started_by_id) if session.started_by_id else None,
    }


# ----------------------------------------------------------------------
# Moderation helpers (REST controls: mute / remove / role / admit)
# ----------------------------------------------------------------------
def is_group_staff(conversation, user_id) -> bool:
    """Group admin / moderator (DM / non-group me False). Ye log room me hamesha manager rehte hain."""
    if conversation.type != ConversationType.GROUP:
        return False
    group = getattr(conversation, 'group_detail', None)
    return bool(group) and is_group_admin_or_mod(group, user_id)


def can_moderate(conversation, session, actor, actor_role, target_user_id, target_role):
    """
    (ok: bool, code: str). `can_act_on` (role order) + do extra rule:
      * khud pe nahi (remove / mute-single ke liye alag message),
      * group admin / moderator pe sirf group admin / moderator hi action le sakta hai — session start karne
        wala normal member (host) group ke admin ko room se nahi nikaal / demote kar sakta.
    """
    if str(actor.id) == str(target_user_id):
        return False, 'cannot_target_self'
    if not can_act_on(actor_role, target_role):
        return False, 'insufficient_role'
    if is_group_staff(conversation, target_user_id) and not is_group_staff(conversation, actor.id):
        return False, 'group_staff_protected'
    return True, ''


def role_and_policy(conversation, user_id):
    """(role | None, share_policy) — REST views ke liye (`socket_role_info` ka conversation-object version)."""
    return socket_role_info(conversation.id, user_id)


def grants_for(role, share_policy) -> dict:
    """Client ko dikhane ke liye permission flags (UI buttons enable/disable)."""
    g = lk.study_room_grants(role, share_policy)
    return {
        'can_publish': g['can_publish'],
        'can_screen_share': 'screen_share' in g['sources'],
        'can_publish_data': g['can_publish_data'],
        'can_edit_board': can_edit_board(role, share_policy),
        'can_manage': is_manager(role),
        'can_raise_hand': True,
    }


def present_identities(session):
    """LiveKit me abhi connected identities (set) ya None."""
    return lk.list_room_identities(session.room_name)
