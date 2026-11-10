# campus/substitute_push.py
"""
Notifications for the substitute-teacher flow.

Three messages, three audiences:

  assigned   -> the SUBSTITUTE teacher ("you're covering Maths, 10-A, Mon
                20 Oct 10:00-10:45, for Mrs Sharma")                [push]
             -> the SECTION'S students + their linked parents ("Maths on Mon
                20 Oct will be taken by Mr Verma")        [bell; push if soon]
  cancelled  -> the (former) SUBSTITUTE only ("that cover is off") [push]

Rules worth knowing:
  * The time-visibility rule (T4 §G) is honoured: students/parents are told
    the TIME only for ONLINE periods; an OFFLINE period is announced by date
    + subject only. Staff (the substitute) always get the time.
  * The absence `reason` is never included anywhere — students read this.
  * Push to students/parents only when the class is today or tomorrow (a
    cover arranged three weeks ahead is a bell row, not a buzz; it would be
    stale by the day). The substitute is always pushed.
  * Preferences / quiet hours / DND are honoured by the push layer
    (`bridge.notify(push=True)`), same as every other campus push.

Runs inside a Celery worker (`tasks.send_substitute_notifications`), never in
the request path: a 60-student section with parents is 100+ rows.
Everything cross-app goes through `campus.bridge` (design doc golden rule).
"""
import logging
from datetime import timedelta

from django.utils import timezone

from . import bridge
from .bridge import NotifTypes
from .models import ClassMode, CampusParentLink, StudentEnrollment
from .substitutes import display_name

logger = logging.getLogger(__name__)

_SHADOW_PARENT_PREFIX = "parent_shadow_"  # see bridge._get_or_create_shadow_parent_user


def _day(d) -> str:
    return f"{d:%a %d %b}"


def _time(t) -> str:
    return f"{t:%I:%M %p}".lstrip("0")


def _section_label(entry) -> str:
    section = entry.section
    return f"{section.school_class.name} - {section.name}"


def _payload(assignment, notif_type):
    entry = assignment.entry
    return {
        "type": notif_type,
        "assignment_id": str(assignment.pk),
        "entry_id": str(entry.pk),
        "section_id": str(entry.section_id),
        "subject_id": str(entry.subject_id),
        "campus_id": str(assignment.campus_id),
        "date": assignment.date.isoformat(),
    }


def section_audience(assignment):
    """`(students, real_parent_users)` of the substituted period's section.

    `students` are `User` rows (needed for loginless-parent device pushes);
    parents exclude the non-loginable "shadow" users made for Parent Mode
    codes (reached through their devices instead)."""
    entry = assignment.entry
    students, seen = [], set()
    for enrollment in (
        StudentEnrollment.objects.filter(
            section_id=entry.section_id,
            session_id=entry.session_id,
            status=StudentEnrollment.Status.ACTIVE,
        )
        .select_related("student")
        .order_by("id")
    ):
        if enrollment.student_id not in seen:
            seen.add(enrollment.student_id)
            students.append(enrollment.student)

    parents, seen_parent = [], set()
    if students:
        for link in CampusParentLink.objects.filter(
            campus_id=assignment.campus_id, student_id__in=seen
        ).select_related("parent"):
            parent = link.parent
            if parent.pk in seen_parent or (parent.username or "").startswith(_SHADOW_PARENT_PREFIX):
                continue
            seen_parent.add(parent.pk)
            parents.append(parent)
    return students, parents


def notify_substitute_assigned(assignment) -> int:
    """Tell the substitute and the section. Returns recipients attempted."""
    entry = assignment.entry
    subject = entry.subject.name
    slot = entry.time_slot
    sent = 0

    # --- the substitute teacher (always sees the time) ------------------------
    original = display_name(assignment.original_staff.user)
    bridge.notify(
        users=[assignment.substitute.user],
        notif_type=NotifTypes.CAMPUS_SUBSTITUTE_ASSIGNED,
        title=f"Substitute duty: {subject}",
        body=(
            f"{_section_label(entry)} · {_day(assignment.date)}, "
            f"{_time(slot.start_time)}-{_time(slot.end_time)} · covering for {original}"
        ),
        data=_payload(assignment, NotifTypes.CAMPUS_SUBSTITUTE_ASSIGNED),
        push=True,
    )
    sent += 1

    # --- the section's students + parents --------------------------------------
    students, parents = section_audience(assignment)
    recipients = {u.pk: u for u in students}
    recipients.update({p.pk: p for p in parents})
    # staff are never "students" of the section, but be safe about the two
    # teachers involved if a data oddity put one in the roster.
    recipients.pop(assignment.substitute.user_id, None)
    recipients.pop(assignment.original_staff.user_id, None)

    sub_name = display_name(assignment.substitute.user)
    body = f"{_day(assignment.date)} · {sub_name} will take this class."
    if entry.mode == ClassMode.ONLINE:  # offline periods never reveal a time to students
        body = f"{_day(assignment.date)}, {_time(slot.start_time)} · {sub_name} will take this class."
    data = _payload(assignment, NotifTypes.CAMPUS_CLASS_SUBSTITUTED)
    title = f"{subject}: substitute teacher"
    push_now = (assignment.date - timezone.localdate()) <= timedelta(days=1)

    if recipients:
        bridge.notify(
            users=list(recipients.values()),
            notif_type=NotifTypes.CAMPUS_CLASS_SUBSTITUTED,
            title=title,
            body=body,
            data=data,
            push=push_now,
        )
        sent += len(recipients)

    # Loginless Parent Mode devices — one lookup per student; each call is
    # best-effort and never raises (see bridge.push_to_parent_devices).
    if push_now:
        for student in students:
            try:
                sent += bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
            except Exception:  # pragma: no cover - the helper already swallows
                logger.exception("campus substitute push to parent devices failed (assignment=%s).", assignment.pk)
    return sent


def notify_substitute_cancelled(assignment) -> int:
    """Tell the former substitute the cover is off."""
    entry = assignment.entry
    bridge.notify(
        users=[assignment.substitute.user],
        notif_type=NotifTypes.CAMPUS_SUBSTITUTE_CANCELLED,
        title="Substitute duty cancelled",
        body=f"{entry.subject.name} · {_section_label(entry)} · {_day(assignment.date)} — you no longer need to cover this.",
        data=_payload(assignment, NotifTypes.CAMPUS_SUBSTITUTE_CANCELLED),
        push=True,
    )
    return 1
