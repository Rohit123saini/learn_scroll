"""
tuitionclass/views/access.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

from django.db.models import Q
from ..livekit_utils import ParticipantRole
from ..models import (
    ClassJoinRequest,
    Classroom,
    ClassroomStaff,
    ClassroomTrialAccess,
    PassPurchase,
    SessionParticipant,
)


# ---------------------------------------------------------------------------
# 3. SESSION
# ---------------------------------------------------------------------------
def _org_staff_role(classroom, user):
    """Staff row for this user on this classroom, or None. Separate lookup
    (instead of inlining everywhere) since both role-resolution and the
    org-staff host-access rule below need it."""
    return (
        ClassroomStaff.objects.filter(classroom=classroom, user=user)
        .values_list("role", flat=True)
        .first()
    )


def _has_room_access_no_pass(classroom, user) -> bool:
    """Who gets into the room WITHOUT needing a pass, beyond the teacher
    (Classroom.has_access already covers teacher + valid PassPurchase).

    CO_TEACHER/MODERATOR are host-tier staff on ANY classroom type — this
    must match _can_manage_classroom()'s ADMIN grant (which _access_level
    already hands out on any classroom type), otherwise my-pass's
    can_enter_class lies to the frontend for a co-teacher/moderator on an
    individual classroom: it shows "Enter Class" but the actual join()/
    token() gate below used to reject them, since this function used to
    bail out to False for anything not ORGANISATION before ever looking at
    the staff role.

    Plain TA stays the tighter, org-only rule: they're the organisation's
    own staff (not a paying student) on an ORGANISATION classroom, but on
    an INDIVIDUAL (solo teacher) classroom a plain TA still needs a pass
    like any student.
    """
    staff_role = _org_staff_role(classroom, user)
    if staff_role is None:
        return False
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return True
    return classroom.classroom_type == Classroom.ClassroomType.ORGANISATION


def _has_full_room_access(classroom, user) -> bool:
    """The ORIGINAL gate, unchanged: valid pass (or teacher) OR org-staff
    bypass above. Used by the regular join()/token()/start-or-join path so
    a trial never unlocks a full-length token."""
    return (
        classroom.has_access(user)
        or _has_room_access_no_pass(classroom, user)
        or _has_standalone_open_access(classroom, user)
    )


def _has_standalone_open_access(classroom, user) -> bool:
    """X12 L-C: a standalone live (hidden personal classroom) that is open to followers / everybody needs no pass.
    Cheap exit for every ordinary classroom (`is_personal` is False). Bans / kicks / capacity stay in _perform_join."""
    if not getattr(classroom, "is_personal", False):
        return False
    from ..standalone_live import open_access_for

    return open_access_for(classroom, user)


def _active_trial_for(classroom, user):
    """The user's currently-running ClassroomTrialAccess for this classroom,
    or None. Banned users never count."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    trial = ClassroomTrialAccess.objects.filter(classroom=classroom, user=user).first()
    if trial is None or not trial.is_active():
        return None
    if classroom.bans.filter(student_id=user.id).exists():
        return None
    return trial


def _has_active_trial(classroom, user) -> bool:
    return _active_trial_for(classroom, user) is not None


def _has_room_access(classroom, user) -> bool:
    """Gate for everything INSIDE a room (chat, polls, reactions, hand,
    whiteboard, session websocket): full access OR a still-running trial.
    The trial lapses server-side the instant expires_at passes (see
    ClassroomTrialAccess.is_active), even if the kick task is late.
    join()/token() deliberately use _has_full_room_access instead."""
    return _has_full_room_access(classroom, user) or _has_active_trial(classroom, user)


def _resolve_session_roles(session, user):
    """Map a user to (livekit_role, participant_role) for a given session.

    - Classroom.teacher                                    -> HOST
    - ClassroomStaff (co_teacher / moderator), any classroom -> CO_HOST on
      LiveKit (moderation power), recorded as SessionParticipant.Role.HOST
      since that model only distinguishes HOST/STUDENT.
    - ClassroomStaff (ta), ORGANISATION classroom only     -> CO_HOST too —
      org staff are staff, not students, even in the TA seat.
    - ClassroomStaff (ta) on an INDIVIDUAL classroom, or anyone else -> STUDENT
    """
    classroom = session.classroom
    if user.id == classroom.teacher_id:
        return ParticipantRole.HOST, SessionParticipant.Role.HOST

    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return ParticipantRole.CO_HOST, SessionParticipant.Role.HOST
    if staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION:
        return ParticipantRole.CO_HOST, SessionParticipant.Role.HOST

    return ParticipantRole.STUDENT, SessionParticipant.Role.STUDENT


def _can_moderate_session(session, user) -> bool:
    """Teacher, co-teacher, or moderator can end the session / remove
    participants — and, on an organisation classroom, any staff row (TA
    included), same host-tier bypass as _resolve_session_roles above."""
    classroom = session.classroom
    if user.id == classroom.teacher_id:
        return True
    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return True
    return staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION


def _is_guest_cohost(session, user) -> bool:
    """True for a guest who is ON STAGE right now and was made co-host by a moderator."""
    if not getattr(user, "is_authenticated", False):
        return False
    return SessionParticipant.objects.filter(
        session=session, user=user, left_at__isnull=True, guest_state="on_stage", guest_cohost=True,
    ).exists()


def _can_cohost_moderate(session, user) -> bool:
    """Real moderators PLUS an on-stage guest co-host. Used ONLY for the light live-room actions (chat delete /
    pin / unpin, mute, lower hand, slow-mode exemption). End / kick / record / invite / breakouts / polls keep
    `_can_moderate_session`."""
    return _can_moderate_session(session, user) or _is_guest_cohost(session, user)


def _can_manage_classroom(classroom, user) -> bool:
    """Teacher, co-teacher, or moderator can post notices / mark off-days.
    (Plain TAs on an individual classroom can't — same split as above; org
    TAs can, via the same org-staff bypass.)"""
    if user.id == classroom.teacher_id:
        return True
    staff_role = _org_staff_role(classroom, user)
    if staff_role in (ClassroomStaff.Role.CO_TEACHER, ClassroomStaff.Role.MODERATOR):
        return True
    return staff_role is not None and classroom.classroom_type == Classroom.ClassroomType.ORGANISATION


def _can_view_classroom_internals(classroom, user) -> bool:
    """Gate for everything that is NOT part of the public Explore/listing
    card: schedule timings, generated sessions, off-days, the notice board,
    materials, doubts, assigmentss, etc. Before a join request is ever
    accepted (see ClassJoinRequestViewSet), a non-participant only ever gets
    the classroom's public description and its reviews — nothing else.

    True for the teacher/any staff row (_can_manage_classroom), OR anyone
    who has EVER held a successful pass for this classroom — active or
    expired (classroom.is_enrolled). Deliberately broader than has_access():
    a lapsed pass should still unlock the classroom's general content, it
    should only lose the ability to actually enter a live session (that
    tighter boundary is _has_room_access, used by join()/token()/chat/polls
    below)."""
    return _can_manage_classroom(classroom, user) or classroom.is_enrolled(user)


# The 4 user tiers this app recognises for a given classroom, from most to
# least privileged. Used by ClassroomViewSet.my_pass (the "class details"
# access-status endpoint the frontend drives its UI off of) and anywhere
# else that needs a single, authoritative answer to "who is this user, for
# this classroom".
class AccessLevel:
    ADMIN = "admin"      # teacher, or co-teacher/moderator/org-staff — full manage rights
    ACTIVE = "active"    # currently valid (unexpired) pass — full access, CAN enter a live session
    EXPIRED = "expired"  # held a pass before, it lapsed — full access EXCEPT entering a live session
    # PENDING (Flutter Phase 1 — join-request pending state): caller has a
    # ClassJoinRequest against this classroom still sitting at
    # Status.PENDING, and has never separately held access some other way.
    # Distinct from NONE so the frontend can show "Request Sent — Waiting
    # for Approval" instead of "Request to Join" and disable the CTA/stop
    # a duplicate request being raised (perform_create() already blocks a
    # second pending request server-side; this just lets the UI reflect
    # that state instead of the student discovering it via a 400).
    PENDING = "pending"
    NONE = "none"        # never held a pass / no accepted join request yet — public listing + reviews only


def _access_level(classroom, user) -> str:
    """Single source of truth for the access-tier model:
        admin   -> _can_manage_classroom (teacher / co-teacher / moderator /
                    org staff of any role)
        active  -> classroom.has_access (currently valid pass)
        expired -> classroom.is_enrolled but NOT has_access (pass lapsed)
        pending -> not enrolled at all, but has a ClassJoinRequest against
                    this classroom still sitting at PENDING
        none    -> everything else (no pass ever purchased, no pending
                    request; or their only past join request was
                    rejected/cancelled)
    """
    if _can_manage_classroom(classroom, user):
        return AccessLevel.ADMIN
    if classroom.has_access(user):
        return AccessLevel.ACTIVE
    if classroom.is_enrolled(user):
        return AccessLevel.EXPIRED
    if classroom.join_requests.filter(student=user, status=ClassJoinRequest.Status.PENDING).exists():
        return AccessLevel.PENDING
    return AccessLevel.NONE


def _accessible_classroom_ids(user):
    """Classroom ids `user` is allowed to see internals for: classrooms they
    teach or staff, plus every classroom where they have EVER held a
    successful pass (active or expired — see is_enrolled). Used to scope
    list endpoints that are called WITHOUT a ?classroom= filter, so e.g.
    GET /schedules/ never leaks every classroom's schedule on the whole
    platform to an unrelated signed-in user, while still showing a lapsed
    student the classrooms they were once enrolled in."""
    managed = Classroom.objects.filter(
        Q(teacher=user) | Q(staff__user=user)
    ).values_list("id", flat=True)
    enrolled = PassPurchase.objects.filter(
        student=user,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    ).values_list("class_pass__classroom_id", flat=True)
    return set(managed) | set(enrolled)
