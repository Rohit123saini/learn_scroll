# campus/homework_diary_push.py
"""
Push for the Homework Diary: "new homework posted" and "homework due tomorrow".

Audience (one rule, both pushes): every ACTIVE student of the entry's section,
plus their linked parents — real `User` parents (CampusParentLink, normal
bell + push pipeline, honours NotificationPreference: push toggle, muted
types, quiet hours) AND loginless Parent Mode devices (ParentToken FCM, via
`bridge.push_to_parent_devices`), exactly like `attendance_push`.

The poster is never notified about their own post.

Runs inside a Celery worker (`tasks.send_homework_diary_posted` /
`send_homework_diary_due_reminders`), never in the request path: a 60-student
section with a few parents each is 100+ notification rows and as many FCM
calls, which must not delay the teacher's 201.

Everything cross-app goes through `campus.bridge` (design doc golden rule).
Never raises to the caller's caller on a single bad recipient — see callers.
"""
import logging

from . import bridge
from .bridge import NotifTypes
from .models import CampusParentLink, StudentEnrollment

logger = logging.getLogger(__name__)

_SHADOW_PARENT_PREFIX = "parent_shadow_"  # see bridge._get_or_create_shadow_parent_user


def _fmt_date(d):
    return f"{d:%d %b}"


def homework_audience(entry):
    """`(students, real_parent_users)` for a diary entry's section.

    `students` are `User` rows (needed for loginless-parent device pushes);
    `real_parent_users` excludes the non-loginable "shadow" parents created for
    Parent Mode codes (those are reached through their devices instead)."""
    students = []
    seen = set()
    for enrollment in (
        StudentEnrollment.objects.filter(section_id=entry.section_id, status=StudentEnrollment.Status.ACTIVE)
        .select_related("student")
        .order_by("id")
    ):
        if enrollment.student_id not in seen:
            seen.add(enrollment.student_id)
            students.append(enrollment.student)

    parents = []
    seen_parent = set()
    if students:
        for link in CampusParentLink.objects.filter(
            campus_id=entry.campus_id, student_id__in=seen
        ).select_related("parent"):
            parent = link.parent
            if parent.pk in seen_parent or (parent.username or "").startswith(_SHADOW_PARENT_PREFIX):
                continue
            seen_parent.add(parent.pk)
            parents.append(parent)
    return students, parents


def _payload(entry, notif_type):
    return {
        "type": notif_type,
        "entry_id": str(entry.pk),
        "section_id": str(entry.section_id),
        "subject_id": str(entry.subject_id),
        "campus_id": str(entry.campus_id),
        "diary_date": entry.diary_date.isoformat(),
        "due_date": entry.due_date.isoformat(),
    }


def _send(entry, *, notif_type, title, body):
    students, parents = homework_audience(entry)
    data = _payload(entry, notif_type)

    recipients = {u.pk: u for u in students}
    recipients.update({p.pk: p for p in parents})
    if entry.posted_by_id:
        recipients.pop(entry.posted_by_id, None)

    sent = 0
    if recipients:
        bridge.notify(
            users=list(recipients.values()),
            notif_type=notif_type,
            title=title,
            body=body,
            data=data,
            push=True,
        )
        sent += len(recipients)

    # Loginless Parent Mode devices — one lookup per student; each call is
    # best-effort and never raises (see bridge.push_to_parent_devices).
    for student in students:
        try:
            sent += bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
        except Exception:  # pragma: no cover - the helper already swallows
            logger.exception("campus homework push to parent devices failed (entry=%s).", entry.pk)
    return sent


def notify_homework_posted(entry) -> int:
    """New diary line -> section students + parents. Returns pushes attempted."""
    subject_name = entry.subject.name
    return _send(
        entry,
        notif_type=NotifTypes.HOMEWORK_DIARY_POSTED,
        title=f"Homework: {subject_name}",
        body=f"{entry.title[:120]} · Due {_fmt_date(entry.due_date)}",
    )


def notify_homework_due(entry) -> int:
    """Due-soon reminder -> section students + parents. Returns pushes attempted."""
    subject_name = entry.subject.name
    return _send(
        entry,
        notif_type=NotifTypes.HOMEWORK_DIARY_DUE,
        title=f"Homework due: {subject_name}",
        body=f"{entry.title[:120]} · Due {_fmt_date(entry.due_date)}",
    )
