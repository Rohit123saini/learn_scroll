# message/services.py
#
# 🔥 NAYA (task 27) — `GroupViewSet.create` / `add_members` / `update_member`
# ka core logic yahan plain functions me extract kiya gaya hai, taaki ye
# HTTP (DRF Response/PermissionDenied) se decoupled ho jaaye aur kahin bhi
# reuse ho sake — sabse pehli zaroorat khud isi batch me hai:
# `core/classroom_chat_bridge.py` ko bilkul yehi "group banao" / "member
# add karo" / "role badlo" logic chahiye (Classroom <-> chat-group bridge
# ke liye), bina DRF request/response cycle ke through jaaye.
#
# Design rule: ye functions kabhi DRF exception (`PermissionDenied`,
# `ValidationError`) nahi raise karte — plain `ValueError`/
# `PermissionError` raise karte hain. `GroupViewSet` in dono ko apne try/
# except me DRF ke equivalent exceptions me convert karta hai (neeche
# comment me shape dikhaya gaya hai) — isse ye module DRF import kiye
# bina bhi (core/ jaisi jagah se) use ho sakta hai.
#
# GroupViewSet khud ab sirf ek thin wrapper hai: request parse karo, in
# functions ko call karo, Response banao. Permission/role checks
# (`_require_admin`, `group.is_private`) bhi yahin move ho gaye hain taaki
# ek hi jagah se "kaun kya kar sakta hai" control ho.

import secrets
from typing import Iterable, Optional

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .cache_utils import invalidate_group_role_cache
from .group_rules import is_group_admin_or_mod
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Group,
    GroupMember,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared helper — moved here from views.py (single source; views.py now
# imports this from here instead of defining its own copy).
# ---------------------------------------------------------------------------
def add_or_reactivate_participant(conversation, user):
    """
    `ConversationParticipant` row banao agar exist nahi karti, ya agar
    pehle se hai (user pehle group chhod chuka tha, `left_at` set tha) to
    use wapas active kar do. Har jagah jahan bhi koi user (re-)add hota
    hai — group create, add_members, join, approve_join_request, aur ab
    classroom-chat-bridge — isi function se guzarta hai, taaki "member ban
    gaya par left_at abhi bhi set hai isliye chat me dikh nahi raha" wala
    bug kabhi na ho.
    """
    participant, created = ConversationParticipant.objects.get_or_create(
        conversation=conversation, user=user,
    )
    if not created and participant.left_at is not None:
        participant.left_at = None
        participant.save(update_fields=['left_at'])
    return participant, created


def generate_group_invite_code() -> str:
    while True:
        code = secrets.token_urlsafe(6)[:8]
        if not Group.objects.filter(invite_code=code).exists():
            return code


def require_group_admin_or_mod(group: Group, user) -> None:
    """Raise plain PermissionError (not DRF's) if `user` isn't admin/mod
    on `group`. Caller (view or classroom_chat_bridge) converts this to
    whatever error shape it needs."""
    if not is_group_admin_or_mod(group, user.id):
        raise PermissionError('Sirf group admin/moderator ye action kar sakte hain.')


# ---------------------------------------------------------------------------
# create() — extracted from GroupViewSet.create
# ---------------------------------------------------------------------------
def create_group(
    *,
    created_by,
    name: str,
    description: str = '',
    photo_url: Optional[str] = None,
    is_private: bool = False,
    member_ids: Iterable = (),
) -> Group:
    """
    Naya group + uski underlying Conversation banata hai, `created_by` ko
    ADMIN role ke saath, aur `member_ids` (agar diye hon) ko plain MEMBER
    ke roop me. `created_by` khud `member_ids` me ho to bhi duplicate
    nahi banega (discard kar diya jaata hai).
    """
    with transaction.atomic():
        conversation = Conversation.objects.create(type=ConversationType.GROUP)
        group = Group.objects.create(
            conversation=conversation,
            name=name,
            description=description or '',
            photo_url=photo_url,
            is_private=is_private,
            invite_code=generate_group_invite_code(),
            created_by=created_by,
        )

        ids = set(member_ids)
        ids.discard(created_by.id)
        valid_users = list(User.objects.filter(id__in=ids))

        memberships = [ConversationParticipant(conversation=conversation, user=created_by)]
        group_members = [GroupMember(group=group, user=created_by, role=GroupMember.Role.ADMIN)]
        for user in valid_users:
            memberships.append(ConversationParticipant(conversation=conversation, user=user))
            group_members.append(GroupMember(group=group, user=user, added_by=created_by))

        ConversationParticipant.objects.bulk_create(memberships)
        GroupMember.objects.bulk_create(group_members)
        Group.objects.filter(id=group.id).update(members_count=len(group_members))
        group.refresh_from_db()

    return group


# ---------------------------------------------------------------------------
# add_members() — extracted from GroupViewSet.add_members
# ---------------------------------------------------------------------------
def add_members_to_group(*, group: Group, actor, user_ids: Iterable) -> list:
    """
    `actor` ke through `user_ids` ko `group` me add karta hai. Public
    group: koi bhi member add kar sakta hai. Private group: sirf
    admin/moderator — caller (view) ye role-check khud `actor` ki group
    membership confirm hone ke baad hi karega; classroom_chat_bridge jaisa
    internal/system caller `actor=None` pass kar sakta hai (kyunki wo call
    khud teacher ke confirm-action ke response me system code se ho raha
    hai, koi doosra HTTP request nahi) — is case me role-check skip hota
    hai.

    Returns: newly-added `User` instances ki list (already-member users
    silently skip ho jaate hain, error nahi).
    Raises: PermissionError agar private group + non-admin actor.
    ValueError agar `user_ids` empty ho.
    """
    if group.is_private and actor is not None:
        require_group_admin_or_mod(group, actor)

    user_ids = list(user_ids)
    if not user_ids:
        raise ValueError("'user_ids' required hai.")

    existing_ids = set(str(uid) for uid in group.group_members.values_list('user_id', flat=True))
    new_ids = [uid for uid in user_ids if str(uid) not in existing_ids]
    users = list(User.objects.filter(id__in=new_ids))

    with transaction.atomic():
        for user in users:
            add_or_reactivate_participant(group.conversation, user)
            GroupMember.objects.get_or_create(
                group=group, user=user,
                defaults={'added_by': actor if actor is not None else group.created_by},
            )
        Group.objects.filter(id=group.id).update(
            members_count=group.group_members.filter(is_banned=False).count()
        )

    for user in users:
        invalidate_group_role_cache(group.id, user.id)

    return users


# ---------------------------------------------------------------------------
# update_member() — extracted from GroupViewSet.update_member
# ---------------------------------------------------------------------------
def remove_group_member(*, group: Group, actor, user_id) -> None:
    """DELETE branch: `actor` khud ko remove kar sakta hai bina kisi
    role-check ke; kisi aur ko remove karne ke liye admin/mod hona
    zaroori hai."""
    from django.shortcuts import get_object_or_404

    membership = get_object_or_404(GroupMember, group=group, user_id=user_id)
    is_self = actor is not None and str(actor.id) == str(user_id)
    if not is_self and actor is not None:
        require_group_admin_or_mod(group, actor)

    membership.delete()
    ConversationParticipant.objects.filter(
        conversation=group.conversation, user_id=user_id
    ).update(left_at=timezone.now())
    Group.objects.filter(id=group.id).update(
        members_count=group.group_members.filter(is_banned=False).count()
    )
    invalidate_group_role_cache(group.id, user_id)


def update_group_member_role(*, group: Group, actor, user_id, data: dict) -> GroupMember:
    """PATCH branch: role / is_muted / is_banned change — admin/mod only.
    `actor=None` (internal/system caller, e.g. classroom_chat_bridge's
    promote_to_moderator) skips the permission check, same convention as
    `add_members_to_group` above."""
    from django.shortcuts import get_object_or_404

    membership = get_object_or_404(GroupMember, group=group, user_id=user_id)
    if actor is not None:
        require_group_admin_or_mod(group, actor)

    was_banned = membership.is_banned
    for field in ('role', 'is_muted', 'is_banned'):
        if field in data:
            setattr(membership, field, data[field])
    membership.save()

    invalidate_group_role_cache(group.id, user_id)

    if membership.is_banned and not was_banned:
        ConversationParticipant.objects.filter(
            conversation=group.conversation, user_id=user_id
        ).update(left_at=timezone.now())
    elif was_banned and not membership.is_banned:
        ConversationParticipant.objects.filter(
            conversation=group.conversation, user_id=user_id
        ).update(left_at=None)

    return membership

# ---------------------------------------------------------------------------
# answer_doubt_question() — Task 16. Shared answer-path for BOTH classroom
# doubts (`doubt.group` set) and context-pointer doubts (`doubt.
# context_type`/`context_id` set, e.g. testseries — see `testseries/
# bridge.py::answer_query_on_series()`), since `DoubtQuestion` itself now
# supports both shapes (see models.py's Task 16 comment on the field).
# ---------------------------------------------------------------------------
def answer_doubt_question(*, doubt, actor, answer_text: str, answered_by=None):
    """
    `actor`: used ONLY for the classroom-doubt permission check
    (admin/mod on `doubt.group`) — same `actor=None` convention as
    `add_members_to_group`/`update_group_member_role` above: pass `None`
    when the caller has already authorized this itself (e.g. `testseries/
    bridge.py::answer_query_on_series()`, which confirms
    `teacher == series.creator` before ever calling here) or when
    `doubt.group` is None (a context-pointer doubt has no group to check
    admin/mod against in the first place).

    `answered_by`: the user to record as having answered. Defaults to
    `actor` (the normal classroom-teacher-answers-directly case). Kept as
    a SEPARATE param from `actor` so a trusted caller passing `actor=None`
    (to skip the group-permission check) can still correctly record who
    answered — `testseries/bridge.py::answer_query_on_series()` passes its
    already-verified `teacher` here even though it passes `actor=None`.

    Raises `ValueError` if `doubt` is already answered, or if neither
    `actor` nor `answered_by` is given (nothing to record as the answerer).
    """
    if doubt.is_answered:
        raise ValueError('Ye doubt pehle se hi answer ho chuka hai.')

    if doubt.group_id is not None and actor is not None:
        require_group_admin_or_mod(doubt.group, actor)

    resolved_answered_by = answered_by if answered_by is not None else actor
    if resolved_answered_by is None:
        raise ValueError("'answered_by' (ya 'actor') required hai.")

    doubt.is_answered = True
    doubt.answer_text = answer_text
    doubt.answered_by = resolved_answered_by
    doubt.answered_at = timezone.now()
    doubt.save(update_fields=['is_answered', 'answer_text', 'answered_by', 'answered_at', 'updated_at'])

    # Task 16 acceptance checklist: student gets TESTSERIES_QUERY_ANSWERED
    # when their testseries query is answered. Classroom (group) doubts
    # don't get a notify here — deliberately: no acceptance requirement
    # for that path in this task, and it'd need its own NotifType member
    # (same "flag the gap, don't guess" convention `testseries/models.py`
    # already uses for its own unconfirmed NotifType members).
    if doubt.context_type == 'testseries_attempt':
        # ⚠️ GAP, same shape as testseries/models.py's own flagged gaps:
        # `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`
        # is NOT confirmed to exist yet. Until `core` adds it, this raises
        # `AttributeError` at this point — i.e. the answer itself (fields
        # above) is saved and real either way, only this notify step needs
        # that enum member added first.
        from core.models import Notification
        from core.services import create_notification

        create_notification(
            recipient=doubt.author,
            notif_type=Notification.NotifType.TESTSERIES_QUERY_ANSWERED,
            title='Your query has been answered',
            message=answer_text[:200],
            data={'doubt_id': str(doubt.id), 'context_id': str(doubt.context_id)},
        )

    return doubt


# ---------------------------------------------------------------------------
# 🔧 REMOVED (was dead code) — `create_bell_rows_for_push` used to live
# here, written back when `push_utils.py`'s source wasn't available to
# check against. Now that it is: `push_utils.py` already creates its own
# bell rows, inline, per event (`send_chat_message_push` /
# `send_mention_push` / `send_incoming_call_push` each call `core.
# services.create_notification()` directly — see that file). This helper
# had zero callers anywhere in the codebase and duplicated a job that was
# already done a different way, so it's gone rather than left to drift
# from the real implementation.
# ---------------------------------------------------------------------------