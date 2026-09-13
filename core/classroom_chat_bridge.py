# core/classroom_chat_bridge.py
"""
Classroom (liveclass app) <-> chat Group (message app) bridge — task 28.
🔧 GAP FIX (this pass) — also now the campus (campus app) <-> chat
Group / video-room bridge, see functions 10/11 below.

Ye module do already-separate apps ko jodta hai: `liveclass` (classrooms,
sessions, join requests, staff, bans) aur `message` (Groups/chat). Koi bhi
cross-app coupling isi ek file se guzarta hai — `liveclass/signals.py`,
`liveclass/views.py`, aur `notify_session_live` task in 9 functions ko
call karte hain (pehle 8 the — Task 5 ne `resolve_parent_from_token()`
add ki, neeche dekho).

🔧 Docstring fix: is module ke total **12** public entry points hain —
functions 1-9 `liveclass` khud call karta hai, 10wa (`get_groups_for_
classrooms()`) `message/views_parent.py` seedha call karta hai, aur naye
11wa/12wa (`create_section_group()`/`provision_video_room()`, is pass me
add kiye — campus/bridge.py's own STATUS note dekho) `campus/bridge.py`
call karta hai. `resolve_parent_from_token()` khud renumbered ho gaya hai
(ab #12, pehle #9 tha) taaki campus-facing functions liveclass-facing
group ke saath (10/11) rahein, na ki unke beech me insert ho jaayein.

Module khud kabhi `message.models`/`message.services` ko seedha import
nahi karta (sirf local imports, function ke andar). Isse:
    1. `liveclass`/`campus` app `message` app ke internal implementation
       details (Group ka exact shape, GroupMember role enum, ...) se
       decoupled rehte hain — sirf yahi ek jagah dono taraf ka contract
       jaanta hai.
    2. Kal ko chat-backend badle (naya Group model, alag app) to sirf ye
       ek file badalni padegi.

DESIGN — har function best-effort hai (module docstring ka wahi pattern
jo liveclass/signals.py already follow karta hai): agar classroom ke paas
`chat_group_enabled=False` hai (teacher ne kabhi group banaya hi nahi),
har sync function chup-chaap NO-OP ho jaata hai — kabhi exception nahi
raise karta jo caller (koi bhi signal handler) ko todde. Sirf
`create_classroom_group()`/`create_section_group()` (jo khud explicit
teacher/class-teacher confirm actions hain, signal nahi) real errors
raise karte hain — us case me caller (the view) ko pata hona chahiye ki
create fail hui.

✅ VERIFIED (Task 2 gap-fix pass) against the real `liveclass/models.py`:
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

    # CONFIRMED against liveclass/models.py: ClassJoinRequest(classroom,
    # student, status) with Status.ACCEPTED — exact match, no change needed.
    from liveclass.models import ClassJoinRequest, ClassroomStaff

    accepted_student_ids = list(
        ClassJoinRequest.objects.filter(
            classroom=classroom, status=ClassJoinRequest.Status.ACCEPTED,
        ).values_list('student_id', flat=True)
    )
    # CONFIRMED against liveclass/models.py: ClassroomStaff(classroom,
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


# ---------------------------------------------------------------------------
# 10/11. Campus (campus app) <-> chat Group / video-room bridge functions.
#
# ADDED this pass to close the gap `campus/bridge.py`'s own module
# docstring flagged: `create_section_group`/`provision_video_room` were
# referenced from campus but did not exist here yet. Both follow the
# exact same "campus never imports message/liveclass models directly,
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
# 12. resolve_parent_from_token() — Task 5, parent-portal auth
# ---------------------------------------------------------------------------
class ParentTokenResolution:
    """Lightweight result object — `liveclass/permissions.py`'s
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