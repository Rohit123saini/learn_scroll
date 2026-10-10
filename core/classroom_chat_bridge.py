# core/classroom_chat_bridge.py
"""
Classroom (tuitionclass app) <-> chat Group (message app) bridge — task 28.
🔧 GAP FIX (this pass) — also now the campus (campus app) <-> chat
Group / video-room bridge, see functions 10/11 below.

Ye module do already-separate apps ko jodta hai: `tuitionclass` (classrooms,
sessions, join requests, staff, bans) aur `message` (Groups/chat). Koi bhi
cross-app coupling isi ek file se guzarta hai — `tuitionclass/signals.py`,
`tuitionclass/views.py`, aur `notify_session_live` task in 9 functions ko
call karte hain (pehle 8 the — Task 5 ne `resolve_parent_from_token()`
add ki, neeche dekho).

🔧 Docstring fix: is module ke total **14** public entry points hain —
functions 1-9 `tuitionclass` khud call karta hai, 10wa (`get_groups_for_
classrooms()`) `message/views_parent.py` seedha call karta hai, aur
11wa/12wa (`create_section_group()`/`provision_video_room()`, pehli pass
me add kiye — campus/bridge.py's own STATUS note dekho) `campus/bridge.py`
call karta hai. `resolve_parent_from_token()` ab **#14** hai (pehle #9,
phir #12 tha).

🔧 GAP FIX (TASK 14) — 13wa (`generate_campus_session_token()`) is pass
me add hua: `provision_video_room()` sirf room ka naam deta tha, actual
join-time token mint karne wala function missing tha (us function ka
apna docstring yahi flag karta tha). `campus/views.py`'s naya `join`
action ise seedha call karta hai — `campus/bridge.py` ke through nahi
(us file ke current contents is pass me available nahi the, aur ye
TASK 14 ke file list me bhi nahi thi).

Module khud kabhi `message.models`/`message.services` ko seedha import
nahi karta (sirf local imports, function ke andar). Isse:
    1. `tuitionclass`/`campus` app `message` app ke internal implementation
       details (Group ka exact shape, GroupMember role enum, ...) se
       decoupled rehte hain — sirf yahi ek jagah dono taraf ka contract
       jaanta hai.
    2. Kal ko chat-backend badle (naya Group model, alag app) to sirf ye
       ek file badalni padegi.

DESIGN — har function best-effort hai (module docstring ka wahi pattern
jo tuitionclass/signals.py already follow karta hai): agar classroom ke paas
`chat_group_enabled=False` hai (teacher ne kabhi group banaya hi nahi),
har sync function chup-chaap NO-OP ho jaata hai — kabhi exception nahi
raise karta jo caller (koi bhi signal handler) ko todde. Sirf
`create_classroom_group()`/`create_section_group()` (jo khud explicit
teacher/class-teacher confirm actions hain, signal nahi) real errors
raise karte hain — us case me caller (the view) ko pata hona chahiye ki
create fail hui.

✅ VERIFIED (Task 2 gap-fix pass) against the real `tuitionclass/models.py`:
`Classroom.chat_group_enabled` / `linked_conversation_id`,
`ClassJoinRequest(classroom, student, status)`,
`ClassroomStaff(classroom, user, role)`, and `Classroom.cover_image`
(confirmed to always be an `ImageField`, never a `URLField`) all match
exactly what this module originally assumed — no field-name changes were
needed, the ASSUMPTION markers below have been resolved and removed.

✅ VERIFIED (this pass) against the real `campus/models.py`:
`Section.chat_group_enabled`/`linked_conversation_id` (newly added this
same pass, mirroring `Classroom`'s pair exactly — see that model's own
comment), `ClassTeacherassigments(section, staff)`,
`SubjectTeacherassigments(section, subject, staff, status)` with
`Status.APPROVED`, `StudentEnrollment(student, section, status)` with
`Status.ACTIVE`, and `CampusLiveSession.room_id` (plain `CharField`) all
match what functions 10/11 below assume.

9. `resolve_parent_from_token(token)` was here — see #12 below, same
   function, renumbered only (module docstring reorg, no behavior change).
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

# Rolling inactivity window for a parent's session token — matches the
# `message` app's own HasValidParentToken behaviour (§7.16 of
# CHAT_APP_DOCUMENTATION.md): a token that hasn't been used in this many
# days is treated as expired, independent of the access code's own
# absolute expires_at.
INACTIVITY_TTL_DAYS = 30


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
# GAP FIX — get_groups_for_classrooms() — bulk twin of _get_group_for_
# classroom(), added for the parent dashboard
# (message/views_parent.py: StudentReportCardList._chat_group_by_classroom,
# see that function's own docstring — "get_groups_for_classrooms() — 2
# queries total"). That view needs "does this classroom have a linked chat
# Group" for a whole page of classrooms at once; looping
# `_get_group_for_classroom()` per classroom would be an N+1 (one SELECT
# per classroom). This does it in a single bulk query regardless of how
# many classrooms are passed in. Public (no leading underscore) —
# `message/views_parent.py` imports it directly:
# `from core.classroom_chat_bridge import get_groups_for_classrooms`.
# ---------------------------------------------------------------------------
def get_groups_for_classrooms(classrooms):
    """
    `classrooms` — any iterable of Classroom instances (or anything with
    `.id` / `.chat_group_enabled` / `.linked_conversation_id` populated,
    e.g. via `.only('id', 'title', 'chat_group_enabled',
    'linked_conversation_id')` as the caller already does).

    Returns `{classroom_id: Group}` — only for classrooms that actually
    have `chat_group_enabled=True`, a non-null `linked_conversation_id`,
    AND a Group row that still exists for that conversation (mirrors
    `_get_group_for_classroom`'s own DoesNotExist -> skip-and-log
    behaviour, just batched). A classroom with no eligible group is
    simply absent from the result dict — same "absent, not None"
    contract every caller of `_get_group_for_classroom` already relies
    on.
    """
    conversation_id_to_classroom_id = {
        classroom.linked_conversation_id: classroom.id
        for classroom in classrooms
        if classroom.chat_group_enabled and classroom.linked_conversation_id
    }
    if not conversation_id_to_classroom_id:
        return {}

    from message.models import Group  # local import — same cross-app avoidance as _get_group_for_classroom

    groups = Group.objects.select_related('conversation').filter(
        conversation_id__in=conversation_id_to_classroom_id.keys(),
    )

    result = {}
    found_conversation_ids = set()
    for group in groups:
        classroom_id = conversation_id_to_classroom_id.get(group.conversation_id)
        if classroom_id is not None:
            result[classroom_id] = group
            found_conversation_ids.add(group.conversation_id)

    missing = set(conversation_id_to_classroom_id) - found_conversation_ids
    if missing:
        logger.warning(
            "get_groups_for_classrooms: %d classroom(s) have chat_group_enabled=True "
            "but their linked Group is missing (conversation_ids=%s) — deleted directly?",
            len(missing), missing,
        )

    return result


# ---------------------------------------------------------------------------
# 1. create_classroom_group() — teacher's explicit confirm action
# ---------------------------------------------------------------------------
def create_classroom_group(classroom, actor):
    """
    Teacher ke explicit "haan" (POST /tuitionclass/classrooms/<id>/create_group/)
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

    # CONFIRMED against tuitionclass/models.py: ClassJoinRequest(classroom,
    # student, status) with Status.ACCEPTED — exact match, no change needed.
    from tuitionclass.models import ClassJoinRequest, ClassroomStaff

    accepted_student_ids = list(
        ClassJoinRequest.objects.filter(
            classroom=classroom, status=ClassJoinRequest.Status.ACCEPTED,
        ).values_list('student_id', flat=True)
    )
    # CONFIRMED against tuitionclass/models.py: ClassroomStaff(classroom,
    # user, role) — exact match, no change needed.
    staff_user_ids = list(
        ClassroomStaff.objects.filter(classroom=classroom).values_list('user_id', flat=True)
    )
    member_ids = set(accepted_student_ids) | set(staff_user_ids)

    with transaction.atomic():
        group = create_group(
            created_by=classroom.teacher,
            name=classroom.title,
            # CONFIRMED: Classroom.description is TextField(blank=True) —
            # always a string (never None), no getattr fallback needed.
            description=classroom.description,
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
    """CONFIRMED: `Classroom.cover_image` is always an `ImageField`
    (validators=[MaxFileSizeValidator(5)], upload_to='classroom_covers/')
    — never a plain URLField. `cover` is falsy when no file is attached
    (null=True, blank=True), so this returns None in that case rather
    than raising."""
    cover = classroom.cover_image
    if not cover:
        return None
    return cover.url


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
        group.description = classroom.description
        group.photo_url = _classroom_cover_image_url(classroom)
        group.save(update_fields=['name', 'description', 'photo_url'])
    except Exception:
        logger.exception("Failed syncing group metadata for classroom %s.", classroom.pk)


# ---------------------------------------------------------------------------
# 6. archive_group_on_classroom_close() — classroom close / soft-delete
# ---------------------------------------------------------------------------
def archive_group_on_classroom_close(classroom, message=None):
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
            message or f"{classroom.title} ab close ho chuki hai — ye group archive kar diya gaya hai.",
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


# ---------------------------------------------------------------------------
# 10/11. Campus (campus app) <-> chat Group / video-room bridge functions.
#
# ADDED this pass to close the gap `campus/bridge.py`'s own module
# docstring flagged: `create_section_group`/`provision_video_room` were
# referenced from campus but did not exist here yet. Both follow the
# exact same "campus never imports message/tuitionclass models directly,
# core.classroom_chat_bridge is the only door" pattern as functions 1-8
# above — the only difference is the section/campus vocabulary
# (StudentEnrollment/ClassTeacherassigments/SubjectTeacherassigments
# instead of ClassJoinRequest/ClassroomStaff) and, for the video room,
# no persistent-model integration at all (see that function's own
# docstring for why).
# ---------------------------------------------------------------------------
def _get_group_for_section(section):
    """`_get_group_for_classroom()`'s exact counterpart for
    `campus.Section` — same contract: never raises, returns `None` if
    no group is linked or if the linked Group has gone missing."""
    if not getattr(section, "chat_group_enabled", False) or not section.linked_conversation_id:
        return None
    from message.models import Group  # local import — cross-app, avoid module-load-time coupling

    try:
        return Group.objects.select_related("conversation").get(
            conversation_id=section.linked_conversation_id
        )
    except Group.DoesNotExist:
        logger.warning(
            "Section %s has chat_group_enabled=True but its linked Group is missing "
            "(conversation_id=%s) — was it deleted directly?",
            section.pk, section.linked_conversation_id,
        )
        return None


def create_section_group(section, actor):
    """
    `create_classroom_group()`'s counterpart for `campus.Section` —
    called from campus's own class-teacher "create chat group" confirm
    action (campus/bridge.py::create_section_group, which is now a
    direct top-level-import wrapper around this function — see that
    file's STATUS note).

    Idempotent — returns the existing group unchanged if `section`
    already has one linked (`_get_group_for_section()` above).

    Initial members: every ACTIVE `StudentEnrollment` for this section,
    plus every APPROVED `SubjectTeacherassigments` for it (promoted to
    MODERATOR after creation, same as classroom co-teachers/moderators
    above) — campus has no `ClassJoinRequest`/`ClassroomStaff` concept,
    these are its equivalents. The section's own class-teacher
    (`ClassTeacherassigments`) is the group creator/ADMIN.

    Raises `ValueError` if `actor` isn't this section's assigned
    class-teacher (same "extra safety net, view should also check its
    own permission" reasoning `create_classroom_group()` documents).
    """
    # Local imports — cross-app (campus), same "no hard import-time
    # coupling" reasoning every other local import in this module gives.
    from campus.models import ClassTeacherassigments, StudentEnrollment, SubjectTeacherassigments
    from message.models import GroupMember
    from message.services import create_group

    class_teacher_assigments = (
        ClassTeacherassigments.objects.filter(section=section)
        .select_related("staff__user")
        .first()
    )
    if class_teacher_assigments is None or actor.id != class_teacher_assigments.staff.user_id:
        raise ValueError("Sirf section ka class-teacher hi chat group bana sakta hai.")

    existing = _get_group_for_section(section)
    if existing is not None:
        return existing

    student_ids = list(
        StudentEnrollment.objects.filter(
            section=section, status=StudentEnrollment.Status.ACTIVE,
        ).values_list("student_id", flat=True)
    )
    subject_teacher_user_ids = list(
        SubjectTeacherassigments.objects.filter(
            section=section, status=SubjectTeacherassigments.Status.APPROVED,
        ).values_list("staff__user_id", flat=True)
    )
    member_ids = set(student_ids) | set(subject_teacher_user_ids)

    with transaction.atomic():
        group = create_group(
            created_by=class_teacher_assigments.staff.user,
            name=str(section),
            description="",
            # Section has no cover-image-equivalent field (confirmed
            # against campus/models.py) — unlike Classroom, so no
            # `_section_cover_image_url()` helper exists to mirror
            # `_classroom_cover_image_url()` above; `photo_url=None` is
            # the honest value here, not a gap.
            photo_url=None,
            is_private=True,
            member_ids=member_ids,
        )
        if subject_teacher_user_ids:
            GroupMember.objects.filter(
                group=group, user_id__in=subject_teacher_user_ids,
            ).update(role=GroupMember.Role.MODERATOR)

        section.linked_conversation_id = group.conversation_id
        section.chat_group_enabled = True
        section.save(update_fields=["linked_conversation_id", "chat_group_enabled"])

    return group


def provision_video_room(live_session, actor):
    """
    Generates the LiveKit room identifier for a `campus.CampusLiveSession`
    and returns it as a plain string — this is what campus/bridge.py's
    wrapper hands back to `CampusLiveSessionViewSet.perform_create` to
    store directly in `CampusLiveSession.room_id` (a `CharField`, per
    campus/models.py).

    Deliberately does NOT mint a LiveKit JWT here. [CONFIRMED, from
    message/views.py's actual call sites — message/livekit_utils.py's own
    source wasn't in this pass — `generate_livekit_token(room_name, user_id,
    user_name)` is the real signature `CallInitiateView`/`StudyRoomJoinView`
    call]: every one of those call sites mints a token PER PARTICIPANT, AT
    JOIN TIME, using a room_name that already exists — never once up front
    for a room that has no participants yet. A LiveKit token is
    short-lived and viewer-specific; storing one now (for `actor`, the
    person scheduling/starting the session) would go stale long before a
    student actually joins, and would be the wrong identity for every
    OTHER participant anyway. So this function's job stops at handing
    back a stable, deterministic room name — whatever campus view later
    handles "join this live session" is the right place to call
    `message.livekit_utils.generate_livekit_token(room_name=<this
    CampusLiveSession.room_id>, user_id=<joining user>.id,
    user_name=<joining user>'s display name)` fresh, per participant —
    that endpoint wasn't part of this pass (`campus/views.py` not
    provided), so it isn't wired here; flagging rather than guessing at
    its shape.

    `actor` is accepted (kept in the signature campus/bridge.py already
    calls this with) but unused below — no permission check is performed
    here because `CampusLiveSessionViewSet.perform_create` is assumed to
    already gate session-scheduling to the right staff (teacher/co-
    teacher) before ever reaching this call; this function only names
    the room.
    """
    return f"campus_live_session_{live_session.id}"


# ---------------------------------------------------------------------------
# 13. generate_campus_session_token() — TASK 14
#
# Closes the exact gap `provision_video_room()`'s own docstring above
# flagged: that function only *names* a room and deliberately never
# mints a token (wrong identity, would go stale before anyone actually
# joins — see its docstring for the full reasoning). This is the
# "join this live session" counterpart, called fresh, per participant,
# at actual join time, by campus/views.py's new `join` action.
#
# Kept here (not pushed onto `campus/bridge.py`) for the same "campus
# never imports message/tuitionclass directly, this module is the only
# door" reasoning every function above documents — `campus/views.py`
# imports this function directly, same as `message/views_parent.py`
# already imports `get_groups_for_classrooms()` directly rather than
# through a per-caller wrapper (see that function's own comment above).
# `campus/bridge.py` wasn't touched in this pass (not in TASK 14's file
# list, and its current contents weren't available to check) — if a
# later pass adds a thin `campus/bridge.py::generate_session_token()`
# wrapper for consistency with `provision_video_room()`/
# `create_section_group()`'s call style, this is what it should forward
# to; nothing here needs to change for that.
# ---------------------------------------------------------------------------
def _display_name_for_token(user) -> str:
    """Best-effort human-readable LiveKit participant name. This module
    never assumes the real User model's exact shape (custom user models
    vary — `get_full_name()` may not exist, `username` may not exist on
    an email-as-username-field setup) — degrades through the friendliest
    available option rather than raising or guessing a field name."""
    full_name = user.get_full_name() if hasattr(user, "get_full_name") else ""
    if full_name:
        return full_name
    return getattr(user, "username", None) or getattr(user, "email", None) or str(user.id)


def generate_campus_session_token(room_name: str, user) -> str:
    """
    Mints a single-participant LiveKit join token for a campus live
    session's room.

    `room_name` — the `CampusLiveSession.room_id` that
    `provision_video_room()` already generated and the caller already
    has saved on the session; this function does no model I/O itself
    and doesn't look it up — same "just names/tokens things, caller
    owns persistence" posture `provision_video_room()` documents.

    `user` — the participant actually joining (NOT necessarily the
    session's teacher/scheduler) — the caller is responsible for
    confirming `user` is allowed into this room (enrolled student,
    assigned subject-teacher, class-teacher, or campus admin) *before*
    calling this; minting a token is not itself an authorization check.

    Raises `RuntimeError` if `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`
    aren't configured on this deployment — the same lazy check
    `message.livekit_utils.generate_livekit_token()` itself performs.
    Deliberately not caught here, so the caller (campus/views.py's
    `join` action) can turn it into a clean 503 rather than a raw 500 —
    same "let RuntimeError surface as-is" convention
    `create_classroom_group()`/`create_section_group()` above already
    follow for their own explicit-action (non-signal) callers.
    """
    from message.livekit_utils import generate_livekit_token  # local import — cross-app, same avoidance as every other import in this module

    return generate_livekit_token(
        room_name=room_name,
        user_id=user.id,
        user_name=_display_name_for_token(user),
    )


# ---------------------------------------------------------------------------
# 14. resolve_parent_from_token() — Task 5, parent-portal auth
# ---------------------------------------------------------------------------
class ParentTokenResolution:
    """Lightweight result object — `tuitionclass/permissions.py`'s
    `HasValidParentSessionToken` copies these straight onto
    `request.parent_student` / `request.parent_access_code`."""

    __slots__ = ("student", "parent_access_code")

    def __init__(self, student, parent_access_code):
        self.student = student
        self.parent_access_code = parent_access_code


def resolve_parent_from_token(token: str):
    """
    `X-Parent-Token` header value ko resolve karta hai ek
    (student, parent_access_code) pair me — ya `None` agar token invalid,
    expired, revoked, ya kuch bhi unexpected ho jaaye.

    FAIL-CLOSED, hamesha — ye dusre 8 sync functions ke best-effort
    "chup-chaap no-op" pattern se deliberately alag hai. Wo functions
    signal handlers hain jinhe fail nahi hone dena; ye function ek live
    audio/video room (aur report-card/query-thread data) ka access-gate
    hai, isliye "fail open" yahan galat hoga. Har unexpected condition —
    unknown token, dono tarah ki expiry, revoked code, ya koi bhi
    unexpected DB/lookup error — sab `None` (deny) return karte hain,
    kabhi exception ugalta nahi.

    Checks (dono independent, dono pass karne zaroori hain):
      1. Token khud expired na ho — rolling `INACTIVITY_TTL_DAYS`-day
         window, `last_seen_at` se.
      2. Access code khud expired na ho — `ParentAccessCode.expires_at`,
         ek absolute expiry jo code pe khud hai, token ki activity se
         independent.
    Plus: `ParentAccessCode.is_active=False` (revoke) → is code pe issued
    saare tokens ek saath invalid — single-code-revokes-all-devices.

    Success pe `ParentToken.last_seen_at` bump hota hai (rolling window
    ko accurate rakhne ke liye — same jaisa message app ka
    `HasValidParentToken` already karta hai) aur ek `ParentTokenResolution`
    return hoti hai.
    """
    if not token:
        return None

    from message.models import ParentToken

    try:
        parent_token = (
            ParentToken.objects
            .select_related("parent_access_code", "parent_access_code__student")
            .get(token=token)
        )
    except ParentToken.DoesNotExist:
        return None
    except Exception:
        # Koi bhi unexpected DB/lookup error — deny, kabhi propagate nahi.
        logger.exception("Unexpected error resolving parent token — denying access.")
        return None

    now = timezone.now()

    # Check 1 — token's own rolling inactivity window.
    if parent_token.last_seen_at is None or (now - parent_token.last_seen_at) > timedelta(days=INACTIVITY_TTL_DAYS):
        return None

    access_code = parent_token.parent_access_code

    # Revocation — a single is_active=False invalidates every token
    # issued against this code, all at once.
    if not access_code.is_active:
        return None

    # Check 2 — the access code's own absolute expiry, independent of
    # how recently the token itself was used.
    if access_code.expires_at is not None and access_code.expires_at <= now:
        return None

    try:
        parent_token.last_seen_at = now
        parent_token.save(update_fields=["last_seen_at"])
    except Exception:
        logger.exception("Failed touching last_seen_at for parent token — denying access.")
        return None

    return ParentTokenResolution(student=access_code.student, parent_access_code=access_code)


# ===========================================================================
# T3 — "jitne participants, utna group member": classroom group = participants
# ka exact mirror.
#
# Source of truth for "who is a participant of this classroom" (ek hi jagah):
#     teacher                                      -> GroupMember.ADMIN
#     ClassroomStaff (co-teacher / moderator / TA) -> GroupMember.MODERATOR
#     active PassPurchase holders (not banned)     -> GroupMember.MEMBER
# "active PassPurchase" = status SUCCESS + is_active + expires_at > now — the
# exact same filter `tuitionclass/bridge.py` already uses for its roster.
# Free-trial viewers (TrialAccess) are deliberately NOT participants.
#
# Every function below follows the module's golden rule: no module-level
# import of `message.*` / `tuitionclass.*`.
# ===========================================================================
def active_student_ids(classroom) -> set:
    """Students who currently hold a live pass for `classroom` and are not
    banned from it."""
    from tuitionclass.models import ClassroomBan, PassPurchase

    ids = set(
        PassPurchase.objects.filter(
            class_pass__classroom=classroom,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
        ).values_list('student_id', flat=True)
    )
    banned = set(ClassroomBan.objects.filter(classroom=classroom).values_list('student_id', flat=True))
    return ids - banned


def student_has_active_access(classroom, user_id) -> bool:
    return user_id in active_student_ids(classroom)


def expected_members(classroom) -> dict:
    """`{'teacher': id, 'staff': {ids}, 'students': {ids}}` — what the group
    SHOULD contain right now. A person is listed once, in their highest role."""
    from tuitionclass.models import ClassroomStaff

    teacher_id = classroom.teacher_id
    staff = set(ClassroomStaff.objects.filter(classroom=classroom).values_list('user_id', flat=True))
    staff.discard(teacher_id)
    students = active_student_ids(classroom) - staff - {teacher_id}
    return {'teacher': teacher_id, 'staff': staff, 'students': students}


def _find_group_any_state(classroom):
    """Linked Group even when it is soft-deleted (archived)."""
    if not classroom.linked_conversation_id:
        return None
    from message.models import Group

    return Group.all_objects.select_related('conversation').filter(
        conversation_id=classroom.linked_conversation_id
    ).first()


def _restore_archived_group(classroom):
    """Teacher re-enabled the group: bring the archived Group + Conversation
    back instead of creating a second one (history is preserved)."""
    from message.models import Conversation, Group

    group = _find_group_any_state(classroom)
    if group is None:
        return None
    if group.is_deleted:
        group.restore()
    conversation = Conversation.all_objects.filter(pk=group.conversation_id).first()
    if conversation is not None and conversation.is_deleted:
        conversation.restore()
    return Group.objects.select_related('conversation').filter(pk=group.pk).first()


def ensure_classroom_group(classroom):
    """Idempotent: returns the classroom's live group, creating it (or
    restoring an archived one) when `chat_group_enabled` is True. Returns None
    when the teacher disabled the group. The group is reconciled right after
    creation so it is an exact mirror from its first second. Raises on real
    errors (callers — Celery task / retry endpoint — handle retry)."""
    if not classroom.chat_group_enabled or classroom.is_deleted or not classroom.is_active:
        return None
    group = _get_group_for_classroom(classroom)
    if group is not None:
        return group

    group = _restore_archived_group(classroom)
    if group is None:
        group = create_classroom_group(classroom, classroom.teacher)
    reconcile_classroom_group(classroom)
    return group


def reconcile_classroom_group(classroom, *, dry_run: bool = False) -> dict:
    """Make the group's members equal `expected_members(classroom)`: add the
    missing, remove the extras, fix roles. Returns a plain-dict report (ids as
    strings) — printed by the management command, summed by the Celery task.
    With `dry_run=True` nothing is written."""
    report = {
        'classroom_id': classroom.pk, 'skipped': '', 'group_missing': False, 'created': False,
        'added': [], 'removed': [], 'role_fixed': [], 'expected_count': 0, 'member_count': 0,
        'capacity_mismatch': False,
    }
    if classroom.is_deleted or not classroom.is_active:
        report['skipped'] = 'inactive'
        return report
    if not classroom.chat_group_enabled:
        report['skipped'] = 'disabled'
        return report

    expected = expected_members(classroom)
    expected_all = {expected['teacher']} | expected['staff'] | expected['students']
    report['expected_count'] = len(expected_all)

    # max_participants is the per-session seat limit; students beyond it can
    # never all be live together. Not an error — just surfaced.
    if len(expected['students']) > classroom.max_participants:
        report['capacity_mismatch'] = True
        logger.warning(
            "Classroom %s has %s active students but max_participants=%s.",
            classroom.pk, len(expected['students']), classroom.max_participants,
        )

    group = _get_group_for_classroom(classroom)
    if group is None:
        report['group_missing'] = True
        if dry_run:
            report['added'] = sorted(str(i) for i in expected_all)
            return report
        group = ensure_classroom_group(classroom)  # re-enters reconcile once, then returns
        if group is None:
            report['skipped'] = 'disabled'
            return report
        report['created'] = True

    from message.models import GroupMember
    from message.services import add_members_to_group, remove_group_member, update_group_member_role

    members = {m.user_id: m for m in GroupMember.objects.filter(group=group)}
    missing = expected_all - set(members)
    extra = {uid for uid in members if uid not in expected_all and uid != group.created_by_id}
    report['added'] = sorted(str(i) for i in missing)
    report['removed'] = sorted(str(i) for i in extra)

    def _wanted_role(uid):
        if uid == expected['teacher']:
            return GroupMember.Role.ADMIN
        return GroupMember.Role.MODERATOR if uid in expected['staff'] else GroupMember.Role.MEMBER

    role_fix_ids = [uid for uid in expected_all & set(members) if members[uid].role != _wanted_role(uid)]
    report['role_fixed'] = sorted(str(i) for i in role_fix_ids)

    if not dry_run:
        if missing:
            add_members_to_group(group=group, actor=None, user_ids=list(missing))
        for uid in extra:
            try:
                remove_group_member(group=group, actor=None, user_id=uid)
            except Exception:
                logger.exception("reconcile: failed removing user %s from group of classroom %s.", uid, classroom.pk)
        for uid in list(missing) + role_fix_ids:
            want = _wanted_role(uid)
            current = GroupMember.objects.filter(group=group, user_id=uid).values_list('role', flat=True).first()
            if current is not None and current != want:
                update_group_member_role(group=group, actor=None, user_id=uid, data={'role': want})

    # dry-run: what the count WOULD be after the fix; real run: the actual count.
    report['member_count'] = (
        GroupMember.objects.filter(group=group, is_banned=False).count() if not dry_run
        else len(set(members) - extra) + len(missing)
    )
    if not dry_run and (missing or extra):
        logger.info(
            "reconcile classroom %s: +%d -%d roles:%d", classroom.pk, len(missing), len(extra), len(report['role_fixed']),
        )
    return report


def demote_from_moderator(classroom, user):
    """A co-teacher/moderator row was deleted: demote to plain member if they
    still hold a live pass, otherwise remove them from the group. Never
    touches the teacher."""
    group = _get_group_for_classroom(classroom)
    if group is None or user.id == classroom.teacher_id:
        return
    from message.models import GroupMember
    from message.services import remove_group_member, update_group_member_role

    try:
        if student_has_active_access(classroom, user.id):
            update_group_member_role(group=group, actor=None, user_id=user.id, data={'role': GroupMember.Role.MEMBER})
        else:
            remove_group_member(group=group, actor=None, user_id=user.id)
    except Exception:
        logger.exception("Failed demoting user %s in group for classroom %s.", user.pk, classroom.pk)


def set_group_enabled(classroom, enabled: bool):
    """Teacher toggle. Disable = ARCHIVE the group (soft delete, history kept,
    `linked_conversation_id` kept so enabling again restores it). Enable =
    restore/create + reconcile. Returns the live group or None."""
    enabled = bool(enabled)
    if not enabled:
        if classroom.chat_group_enabled:
            archive_group_on_classroom_close(
                classroom, message=f"{classroom.title} ka chat group teacher ne band (archive) kar diya hai.",
            )
            classroom.chat_group_enabled = False
            classroom.save(update_fields=['chat_group_enabled'])
        return None
    if not classroom.chat_group_enabled:
        classroom.chat_group_enabled = True
        classroom.save(update_fields=['chat_group_enabled'])
    return ensure_classroom_group(classroom)


def group_status(classroom) -> dict:
    """Small JSON-safe status block for the API / Flutter."""
    group = _get_group_for_classroom(classroom)
    expected = expected_members(classroom)
    expected_count = 1 + len(expected['staff']) + len(expected['students'])
    member_count = 0
    if group is not None:
        from message.models import GroupMember

        member_count = GroupMember.objects.filter(group=group, is_banned=False).count()
    return {
        'chat_group_enabled': classroom.chat_group_enabled,
        'group_ready': group is not None,
        'linked_conversation_id': str(group.conversation_id) if group is not None else None,
        'member_count': member_count,
        'expected_count': expected_count,
        'in_sync': group is not None and member_count == expected_count,
        'max_participants': classroom.max_participants,
    }


# ===========================================================================
# [T4 §D] Campus SECTION chat group == section roster (T3's reconcile pattern).
#
# Source of truth for "who belongs in this section's group":
#     class teacher (ClassTeacherassigments)                -> ADMIN
#     approved SubjectTeacherassigments staff (+moderators) -> MODERATOR
#     ACTIVE StudentEnrollment in the section               -> MEMBER
# Same golden rule as above: no module-level import of campus/message models.
# ===========================================================================
def expected_section_members(section) -> dict:
    """`{'teacher': id|None, 'staff': {ids}, 'students': {ids}}`."""
    from campus.models import ClassTeacherassigments, StudentEnrollment, SubjectTeacherassigments

    ct = ClassTeacherassigments.objects.filter(section=section).select_related("staff").first()
    teacher_id = ct.staff.user_id if ct else None
    staff = set(
        SubjectTeacherassigments.objects.filter(
            section=section, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True,
        ).values_list("staff__user_id", flat=True)
    )
    staff.discard(teacher_id)
    students = set(
        StudentEnrollment.objects.filter(
            section=section, session=section.school_class.session, status=StudentEnrollment.Status.ACTIVE,
        ).values_list("student_id", flat=True)
    )
    students -= staff
    students.discard(teacher_id)
    return {"teacher": teacher_id, "staff": staff, "students": students}


def reconcile_section_group(section, *, dry_run: bool = False) -> dict:
    """Make the section group's members equal `expected_section_members()`.
    Report shape mirrors `reconcile_classroom_group` (ids as strings)."""
    report = {
        "section_id": str(section.pk), "skipped": "", "group_missing": False, "created": False,
        "added": [], "removed": [], "role_fixed": [], "expected_count": 0, "member_count": 0,
        "capacity_mismatch": False,
    }
    if not section.chat_group_enabled:
        report["skipped"] = "disabled"
        return report
    expected = expected_section_members(section)
    if expected["teacher"] is None:
        report["skipped"] = "no_class_teacher"  # a group needs its class-teacher (creator/ADMIN)
        return report
    expected_all = {expected["teacher"]} | expected["staff"] | expected["students"]
    report["expected_count"] = len(expected_all)
    if section.capacity is not None and len(expected["students"]) > section.capacity:
        report["capacity_mismatch"] = True
        logger.warning(
            "Section %s has %s active students but capacity=%s.", section.pk, len(expected["students"]), section.capacity,
        )

    group = _get_group_for_section(section)
    if group is None:
        report["group_missing"] = True
        if dry_run:
            report["added"] = sorted(str(i) for i in expected_all)
            return report
        from campus.models import ClassTeacherassigments

        ct = ClassTeacherassigments.objects.select_related("staff__user").get(section=section)
        group = create_section_group(section, ct.staff.user)
        report["created"] = True

    from message.models import GroupMember
    from message.services import add_members_to_group, remove_group_member, update_group_member_role

    members = {m.user_id: m for m in GroupMember.objects.filter(group=group)}
    missing = expected_all - set(members)
    extra = {uid for uid in members if uid not in expected_all}
    report["added"] = sorted(str(i) for i in missing)
    report["removed"] = sorted(str(i) for i in extra)

    def _wanted_role(uid):
        if uid == expected["teacher"]:
            return GroupMember.Role.ADMIN
        return GroupMember.Role.MODERATOR if uid in expected["staff"] else GroupMember.Role.MEMBER

    role_fix_ids = [uid for uid in expected_all & set(members) if members[uid].role != _wanted_role(uid)]
    report["role_fixed"] = sorted(str(i) for i in role_fix_ids)

    if not dry_run:
        if missing:
            add_members_to_group(group=group, actor=None, user_ids=list(missing))
        for uid in extra:
            try:
                remove_group_member(group=group, actor=None, user_id=uid)
            except Exception:
                logger.exception("reconcile: failed removing user %s from group of section %s.", uid, section.pk)
        for uid in list(missing) + role_fix_ids:
            want = _wanted_role(uid)
            current = GroupMember.objects.filter(group=group, user_id=uid).values_list("role", flat=True).first()
            if current is not None and current != want:
                update_group_member_role(group=group, actor=None, user_id=uid, data={"role": want})
    report["member_count"] = (
        GroupMember.objects.filter(group=group, is_banned=False).count() if not dry_run
        else len(set(members) - extra) + len(missing)
    )
    return report
