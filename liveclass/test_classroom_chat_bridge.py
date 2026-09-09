# core/classroom_chat_bridge.py
"""
Classroom (liveclass app) <-> chat Group (message app) bridge — task 28.

Ye module do already-separate apps ko jodta hai: `liveclass` (classrooms,
sessions, join requests, staff, bans) aur `message` (Groups/chat). Koi bhi
cross-app coupling isi ek file se guzarta hai — `liveclass/signals.py`,
`liveclass/views.py`, aur `notify_session_live` task in 8 functions ko
call karte hain, khud kabhi `message.models`/`message.services` ko seedha
import nahi karte. Isse:
    1. `liveclass` app `message` app ke internal implementation details
       (Group ka exact shape, GroupMember role enum, ...) se decoupled
       rehta hai — sirf yahi ek jagah dono taraf ka contract jaanta hai.
    2. Kal ko chat-backend badle (naya Group model, alag app) to sirf ye
       ek file badalni padegi.

DESIGN — har function best-effort hai (module docstring ka wahi pattern
jo liveclass/signals.py already follow karta hai): agar classroom ke paas
`chat_group_enabled=False` hai (teacher ne kabhi group banaya hi nahi),
har sync function chup-chaap NO-OP ho jaata hai — kabhi exception nahi
raise karta jo caller (koi bhi signal handler) ko todde. Sirf
`create_classroom_group()` (jo khud ek explicit teacher action hai, signal
nahi) real errors raise karta hai — us case me caller (the view) ko pata
hona chahiye ki create fail hui.

⚠️ ASSUMPTIONS (search this word to find every spot) — liveclass/models.py
aur liveclass/views.py upload na hone ki wajah se `Classroom`,
`ClassJoinRequest`, `ClassroomStaff` ke kuch field-names best-guess hain,
tests.py/urls.py/signals.py me jo confirm hua wahi use kiya hai. Agar
tumhare real model me naam alag hain, sirf neeche marked lines badlo —
baaki logic same rahega.
"""

import logging

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _get_group_for_classroom(classroom):
    """Classroom se linked Group instance nikaalta hai, ya None agar koi
    group link hi nahi hai. Kabhi exception nahi raise karta (best-effort
    sync functions isi pe rely karte hain)."""
    if not classroom.chat_group_enabled or not classroom.linked_conversation_id:
        return None
    from message.models import Group  # local import — cross-app, avoid module-load-time coupling

    try:
        return Group.objects.select_related('conversation').get(
            conversation_id=classroom.linked_conversation_id
        )
    except Group.DoesNotExist:
        logger.warning(
            "Classroom %s has chat_group_enabled=True but its linked Group is missing "
            "(conversation_id=%s) — was it deleted directly?",
            classroom.pk, classroom.linked_conversation_id,
        )
        return None


# ---------------------------------------------------------------------------
# 🔧 GAP FIX (Gap 2) — bulk, READ-ONLY counterpart of `_get_group_for_
# classroom()`, for callers that need the linked Group for MANY classrooms
# at once (e.g. the parent dashboard looping over every classroom a
# student is enrolled in) and can't afford one query per classroom the way
# the per-classroom helper above does. Public (no leading underscore) on
# purpose — this is the intended cross-app read entry point, kept in this
# one file per the module's own design principle #1 ("only this file
# knows both sides' contract"), rather than another app reimplementing
# the `chat_group_enabled` + `linked_conversation_id` eligibility check
# itself and risking it drifting out of sync with the per-classroom path.
# ---------------------------------------------------------------------------
def get_groups_for_classrooms(classrooms):
    """
    `classrooms`: iterable of Classroom instances (or anything with
    `.id`, `.chat_group_enabled`, `.linked_conversation_id` already
    loaded — e.g. via `.only(...)`, to keep this cheap).

    Returns: {classroom_id: Group}. A classroom with no linked group
    (chat_group_enabled=False, never created one, or its Group row was
    deleted directly — see the warning below) is simply ABSENT from the
    dict. Callers must use `.get(classroom_id)` and treat a miss as
    "no chat group for this classroom", never assume every classroom
    has an entry.

    Costs a fixed 2 queries total (one for the Groups, none per
    classroom) regardless of how many classrooms are passed in.
    """
    from message.models import Group

    conversation_id_by_classroom_id = {
        classroom.id: classroom.linked_conversation_id
        for classroom in classrooms
        if classroom.chat_group_enabled and classroom.linked_conversation_id
    }
    if not conversation_id_by_classroom_id:
        return {}

    groups_by_conversation_id = {
        group.conversation_id: group
        for group in Group.objects.filter(
            conversation_id__in=conversation_id_by_classroom_id.values(),
        ).select_related('conversation')
    }

    missing = set(conversation_id_by_classroom_id.values()) - set(groups_by_conversation_id)
    if missing:
        logger.warning(
            "%d classroom(s) have chat_group_enabled=True but their linked Group is "
            "missing (conversation_ids=%s) — were they deleted directly?",
            len(missing), missing,
        )

    return {
        classroom_id: groups_by_conversation_id[conversation_id]
        for classroom_id, conversation_id in conversation_id_by_classroom_id.items()
        if conversation_id in groups_by_conversation_id
    }


def _post_system_message(group, sender, text: str):
    """Group ki conversation me ek system message post karta hai
    (`post_welcome_message` aur `post_session_live_announcement` dono
    isi se guzarte hain). Best-effort — fail hone pe sirf log karta hai,
    kabhi caller ko exception nahi deta."""
    from message.models import Message, MessageType

    try:
        Message.objects.create(
            conversation=group.conversation,
            sender=sender,
            type=MessageType.SYSTEM,
            text=text,
            is_system_message=True,
        )
    except Exception:
        logger.exception("Failed posting system message to group %s.", group.pk)


# ---------------------------------------------------------------------------
# 1. create_classroom_group() — teacher's explicit confirm action
# ---------------------------------------------------------------------------
def create_classroom_group(classroom, actor):
    """
    Teacher ke explicit "haan" (POST /liveclass/classrooms/<id>/create_group/)
    pe call hota hai. Idempotent — agar classroom ke paas already group hai,
    wahi wapas kar deta hai (naya nahi banata).

    Initial members: teacher + har already-ACCEPTED join-request wala
    student + har existing co-teacher/moderator (ClassroomStaff row).

    Raises ValueError agar actor teacher na ho (view ismein permission bhi
    khud check kar sakta hai — ye ek extra safety net hai kyunki ye
    function direct bhi call ho sakta hai).
    """
    from message.models import Group
    from message.services import create_group

    if actor.id != classroom.teacher_id:
        raise ValueError("Sirf classroom ka teacher hi chat group bana sakta hai.")

    existing = _get_group_for_classroom(classroom)
    if existing is not None:
        return existing

    # ASSUMPTION: ClassJoinRequest(classroom, student, status) — confirmed
    # shape from liveclass/tests.py (ClassJoinRequestAcceptViewTests etc.)
    from liveclass.models import ClassJoinRequest, ClassroomStaff

    accepted_student_ids = list(
        ClassJoinRequest.objects.filter(
            classroom=classroom, status=ClassJoinRequest.Status.ACCEPTED,
        ).values_list('student_id', flat=True)
    )
    # ASSUMPTION: ClassroomStaff(classroom, user, role) — see
    # models_PATCH_apply_to_Classroom.md for the exact shape assumed.
    staff_user_ids = list(
        ClassroomStaff.objects.filter(classroom=classroom).values_list('user_id', flat=True)
    )
    member_ids = set(accepted_student_ids) | set(staff_user_ids)

    with transaction.atomic():
        group = create_group(
            created_by=classroom.teacher,
            name=classroom.title,
            description=getattr(classroom, 'description', '') or '',
            photo_url=_classroom_cover_image_url(classroom),
            is_private=True,
            member_ids=member_ids,
        )
        # Every co-teacher/moderator should land as GroupMember.MODERATOR,
        # not plain MEMBER — create_group() only ever grants ADMIN (to the
        # teacher) or MEMBER (to everyone else), so promote them now.
        from message.models import GroupMember

        if staff_user_ids:
            GroupMember.objects.filter(
                group=group, user_id__in=staff_user_ids,
            ).update(role=GroupMember.Role.MODERATOR)

        classroom.linked_conversation_id = group.conversation_id
        classroom.chat_group_enabled = True
        classroom.save(update_fields=['linked_conversation_id', 'chat_group_enabled'])

    post_welcome_message(classroom)
    return group


def _classroom_cover_image_url(classroom):
    """`cover_image` ImageField ya URLField dono ho sakta hai (task 26/36
    ka field type confirm nahi ho paya — see models_PATCH note) — dono
    handle karte hain."""
    cover = getattr(classroom, 'cover_image', None)
    if not cover:
        return None
    try:
        return cover.url  # ImageField
    except (AttributeError, ValueError):
        return str(cover)  # already a plain URL string


# ---------------------------------------------------------------------------
# 2. sync_membership_on_join_accept() — ClassJoinRequest -> ACCEPTED, and
#    (task 32) waitlist FCFS promotion. Takes (classroom, student) rather
#    than a specific model instance so BOTH signal call-sites (join-request
#    accept, and waitlist-promotion inside SessionParticipant's post_save
#    signal) can reuse the exact same function — a promoted-off-waitlist
#    student already has real classroom access (an accepted join-request /
#    an active pass), this just makes sure they're in the chat group too,
#    in case the group was created AFTER they were first accepted.
# ---------------------------------------------------------------------------
def sync_membership_on_join_accept(classroom, student):
    """A student just got confirmed access to `classroom` (either their
    join request was accepted, or they were promoted off the session
    waitlist) — add them to the classroom's chat group, if one exists.
    No-op if the classroom has no linked group yet."""
    group = _get_group_for_classroom(classroom)
    if group is None:
        return

    from message.services import add_members_to_group

    try:
        # actor=None -> system call, no permission gate (see services.py
        # docstring for this convention).
        add_members_to_group(group=group, actor=None, user_ids=[student.id])
    except Exception:
        logger.exception(
            "Failed syncing student %s into group for classroom %s.",
            student.pk, classroom.pk,
        )


# ---------------------------------------------------------------------------
# 3. sync_membership_on_removal() — kick / ban / refund
# ---------------------------------------------------------------------------
def sync_membership_on_removal(classroom, student, reason: str = ""):
    """Removes `student` from the classroom's chat group, if one exists.
    Called for kick, ban, and refund — `reason` is only used for logging."""
    group = _get_group_for_classroom(classroom)
    if group is None:
        return

    from message.services import remove_group_member

    try:
        remove_group_member(group=group, actor=None, user_id=student.id)
    except Exception:
        logger.exception(
            "Failed removing student %s from group for classroom %s (reason=%s).",
            student.pk, classroom.pk, reason,
        )


# ---------------------------------------------------------------------------
# 4. promote_to_moderator() — staff / co-teacher add
# ---------------------------------------------------------------------------
def promote_to_moderator(classroom, user):
    """A co-teacher/moderator was just added to the classroom (ClassroomStaff
    row created) — promote them to GroupMember.MODERATOR in the linked
    chat group, adding them first if they aren't already a member."""
    group = _get_group_for_classroom(classroom)
    if group is None:
        return

    from message.models import GroupMember
    from message.services import add_members_to_group, update_group_member_role

    try:
        if not GroupMember.objects.filter(group=group, user_id=user.id).exists():
            add_members_to_group(group=group, actor=None, user_ids=[user.id])
        update_group_member_role(
            group=group, actor=None, user_id=user.id, data={'role': GroupMember.Role.MODERATOR},
        )
    except Exception:
        logger.exception(
            "Failed promoting user %s to moderator in group for classroom %s.",
            user.pk, classroom.pk,
        )


# ---------------------------------------------------------------------------
# 5. sync_group_metadata() — classroom title/cover_image/description update
# ---------------------------------------------------------------------------
def sync_group_metadata(classroom):
    """Keeps the linked group's name/photo_url/description in step with
    the classroom's own — best-effort, no-op if no group is linked."""
    group = _get_group_for_classroom(classroom)
    if group is None:
        return

    try:
        group.name = classroom.title
        group.description = getattr(classroom, 'description', '') or ''
        group.photo_url = _classroom_cover_image_url(classroom)
        group.save(update_fields=['name', 'description', 'photo_url'])
    except Exception:
        logger.exception("Failed syncing group metadata for classroom %s.", classroom.pk)


# ---------------------------------------------------------------------------
# 6. archive_group_on_classroom_close() — classroom close / soft-delete
# ---------------------------------------------------------------------------
def archive_group_on_classroom_close(classroom):
    """
    Classroom close/soft-delete ho gaya — group ko soft-delete karo (same
    pattern jo `GroupViewSet.destroy` already use karta hai: broadcast +
    `soft_delete()`, hard-delete nahi — history admin/support recover kar
    sakte hain grace-period ke andar). Best-effort, no-op if no group.
    """
    group = _get_group_for_classroom(classroom)
    if group is None:
        return

    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer

    try:
        _post_system_message(
            group, classroom.teacher,
            f"{classroom.title} ab close ho chuki hai — ye group archive kar diya gaya hai.",
        )

        conversation = group.conversation
        conversation_id = str(conversation.id)
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'chat_{conversation_id}',
                {
                    'type': 'group_deleted',
                    'group_id': str(group.id),
                    'conversation_id': conversation_id,
                    'deleted_by': str(classroom.teacher_id),
                }
            )
        except Exception:
            logger.exception("Failed broadcasting group_deleted for classroom %s close.", classroom.pk)

        group.soft_delete()
        conversation.soft_delete()
    except Exception:
        logger.exception("Failed archiving group for classroom %s close.", classroom.pk)


# ---------------------------------------------------------------------------
# 7. post_welcome_message() — right after group creation
# ---------------------------------------------------------------------------
def post_welcome_message(classroom):
    group = _get_group_for_classroom(classroom)
    if group is None:
        return
    _post_system_message(
        group, classroom.teacher,
        f"{classroom.title} ka chat group ban gaya hai — yahan classroom-related "
        "updates aur baatcheet ho sakti hai. 🎉",
    )


# ---------------------------------------------------------------------------
# 8. post_session_live_announcement() — from notify_session_live task
# ---------------------------------------------------------------------------
def post_session_live_announcement(session):
    classroom = session.classroom
    group = _get_group_for_classroom(classroom)
    if group is None:
        return
    _post_system_message(
        group, classroom.teacher,
        f"🔴 Live session shuru ho gaya hai — \"{classroom.title}\" me abhi join karo!",
    )