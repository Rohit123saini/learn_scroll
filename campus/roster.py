# campus/roster.py
"""
[T4 §D] Section roster operations — the ONLY place that creates / moves /
removes a student's section enrollment, so capacity, roll numbers, the audit
log and the section chat-group sync can never drift between the REST API,
bulk import and (future) scripts.

  enroll_student()    capacity-checked, roll auto-assigned, idempotent
  transfer_student()  old row -> TRANSFERRED, new row in target section
  withdraw_student()  soft remove (WITHDRAWN) — never a hard delete
  section_dashboard() class-teacher dashboard payload
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import bridge
from .audit import log_action
from .models import (
    Attendance,
    Section,
    StudentEnrollment,
    SubjectTeacherassigments,
)


class RosterError(Exception):
    """Business-rule failure with a stable `code` the API turns into a 400."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def active_count(section, session=None):
    session = session or section.school_class.session
    return StudentEnrollment.objects.filter(
        section=section, session=session, status=StudentEnrollment.Status.ACTIVE
    ).count()


def capacity_info(section, session=None):
    n = active_count(section, session)
    cap = section.capacity
    return {
        "enrolled": n,
        "capacity": cap,
        "is_full": cap is not None and n >= cap,
        "seats_left": None if cap is None else max(cap - n, 0),
    }


def next_roll_number(section, session):
    """Highest numeric roll number ever used in this section+session, +1.
    Non-numeric rolls (e.g. "A-12") are ignored; withdrawn/transferred rows
    still count so a number is never reused."""
    best = 0
    for value in StudentEnrollment.objects.filter(section=section, session=session).values_list("roll_number", flat=True):
        if value and str(value).isdigit():
            best = max(best, int(value))
    return str(best + 1)


def _check_capacity(section, session, override):
    cap = section.capacity
    if cap is None or override:
        return
    n = active_count(section, session)
    if n >= cap:
        raise RosterError("section_full", f"Section {section.name} is full ({n}/{cap}).")


def _roll_taken(section, session, roll, exclude_pk=None):
    qs = StudentEnrollment.objects.filter(
        section=section, session=session, roll_number=roll, status=StudentEnrollment.Status.ACTIVE
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


@transaction.atomic
def enroll_student(*, section, student, session=None, roll_number="", enrollment_no="", actor=None,
                   override_capacity=False):
    """Returns `(enrollment, created)`. Idempotent: an already-ACTIVE row for
    (student, section, session) is returned unchanged; a WITHDRAWN/TRANSFERRED
    row is re-activated (the unique constraint allows one row per triple)."""
    # row lock serialises concurrent enrolments into the same section so the
    # capacity check can't be raced past
    sec = Section.objects.select_for_update().select_related("school_class__campus", "school_class__session").get(
        pk=section.pk
    )
    session = session or sec.school_class.session
    if sec.school_class.session_id != session.id:
        raise RosterError("wrong_session", "This section doesn't belong to the given session.")

    row = StudentEnrollment.objects.filter(student=student, section=sec, session=session).first()
    if row is not None and row.status == StudentEnrollment.Status.ACTIVE:
        return row, False

    _check_capacity(sec, session, override_capacity)
    roll = (roll_number or "").strip()
    if roll and _roll_taken(sec, session, roll, exclude_pk=row.pk if row else None):
        raise RosterError("roll_taken", f"Roll number {roll} is already used in this section.")
    roll = roll or next_roll_number(sec, session)

    created = row is None
    if created:
        row = StudentEnrollment.objects.create(
            student=student, section=sec, session=session, roll_number=roll, enrollment_no=enrollment_no or "",
        )
    else:
        row.status = StudentEnrollment.Status.ACTIVE
        row.left_at = None
        row.roll_number = roll
        if enrollment_no:
            row.enrollment_no = enrollment_no
        row.save(update_fields=["status", "left_at", "roll_number", "enrollment_no"])
    log_action(
        sec.school_class.campus_id, actor, "enrollment.add", target=row,
        summary=f"enrolled {student.username} in {sec} (roll {roll})",
        section=str(sec.pk), student=student.pk, override=bool(override_capacity),
    )
    bridge.sync_section_group(sec)
    return row, created


@transaction.atomic
def withdraw_student(enrollment, *, actor=None, reason=""):
    en = StudentEnrollment.objects.select_for_update().select_related("section__school_class", "student").get(
        pk=enrollment.pk
    )
    if en.status != StudentEnrollment.Status.ACTIVE:
        return en
    en.status = StudentEnrollment.Status.WITHDRAWN
    en.left_at = timezone.now()
    en.save(update_fields=["status", "left_at"])
    log_action(
        en.section.school_class.campus_id, actor, "enrollment.remove", target=en,
        summary=f"withdrew {en.student.username} from {en.section}", reason=reason, section=str(en.section_id),
    )
    bridge.sync_section_group(en.section)
    return en


@transaction.atomic
def transfer_student(enrollment, target_section, *, actor=None, override_capacity=False):
    """Move an ACTIVE student to another section of the SAME session/campus."""
    en = StudentEnrollment.objects.select_for_update().select_related(
        "section__school_class", "student", "session"
    ).get(pk=enrollment.pk)
    if en.status != StudentEnrollment.Status.ACTIVE:
        raise RosterError("not_active", "Only an active enrollment can be transferred.")
    target = Section.objects.select_for_update().select_related("school_class").get(pk=target_section.pk)
    if target.pk == en.section_id:
        raise RosterError("same_section", "Student is already in this section.")
    if target.school_class.campus_id != en.section.school_class.campus_id:
        raise RosterError("other_campus", "Target section belongs to a different campus.")
    if target.school_class.session_id != en.session_id:
        raise RosterError("wrong_session", "Target section is in a different academic session.")

    _check_capacity(target, en.session, override_capacity)
    old_section = en.section
    en.status = StudentEnrollment.Status.TRANSFERRED
    en.left_at = timezone.now()
    en.save(update_fields=["status", "left_at"])

    existing = StudentEnrollment.objects.filter(student=en.student, section=target, session=en.session).first()
    roll = next_roll_number(target, en.session)
    if existing is None:
        new = StudentEnrollment.objects.create(
            student=en.student, section=target, session=en.session, roll_number=roll,
            enrollment_no=en.enrollment_no,
        )
    else:
        existing.status = StudentEnrollment.Status.ACTIVE
        existing.left_at = None
        existing.roll_number = roll
        existing.save(update_fields=["status", "left_at", "roll_number"])
        new = existing
    log_action(
        old_section.school_class.campus_id, actor, "enrollment.transfer", target=new,
        summary=f"moved {en.student.username}: {old_section} -> {target}",
        from_section=str(old_section.pk), to_section=str(target.pk), override=bool(override_capacity),
    )
    bridge.sync_section_group(old_section)
    bridge.sync_section_group(target)
    return new


def section_dashboard(section, *, student_limit=200):
    """Class-teacher dashboard: roster + count/capacity + attendance snapshot +
    pending subject-teacher requests + doubts overview."""
    session = section.school_class.session
    active = (
        StudentEnrollment.objects.filter(section=section, session=session, status=StudentEnrollment.Status.ACTIVE)
        .select_related("student")
        .order_by("roll_number", "student__username")
    )
    students = [
        {
            "enrollment": str(e.id), "student": e.student_id, "username": e.student.username,
            "first_name": e.student.first_name, "last_name": e.student.last_name,
            "roll_number": e.roll_number, "enrollment_no": e.enrollment_no,
        }
        for e in active[:student_limit]
    ]
    info = capacity_info(section, session)

    today = timezone.localdate()
    since = today - timedelta(days=30)
    att = Attendance.objects.filter(enrollment__section=section, enrollment__session=session)
    day = att.filter(date=today).aggregate(
        present=Count("id", filter=Q(status=Attendance.Status.PRESENT)),
        late=Count("id", filter=Q(status=Attendance.Status.LATE)),
        absent=Count("id", filter=Q(status=Attendance.Status.ABSENT)),
        leave=Count("id", filter=Q(status=Attendance.Status.LEAVE)),
        total=Count("id"),
    )
    month = att.filter(date__gte=since).aggregate(
        attended=Count("id", filter=Q(status__in=(Attendance.Status.PRESENT, Attendance.Status.LATE))),
        total=Count("id"),
    )
    pending = (
        SubjectTeacherassigments.objects.filter(section=section, status=SubjectTeacherassigments.Status.PENDING)
        .select_related("staff__user", "subject")
    )
    return {
        "campus": str(section.school_class.campus_id),
        "section": {"id": str(section.id), "name": section.name,
                    "school_class": {"id": str(section.school_class_id), "name": section.school_class.name}},
        "count": info,
        "students": students,
        "students_truncated": info["enrolled"] > student_limit,
        "attendance": {
            "today": day,
            "last_30_days_percent": round(month["attended"] * 100 / month["total"], 2) if month["total"] else None,
        },
        "pending_subject_requests": [
            {"id": str(p.id), "subject": p.subject.name, "staff": p.staff.user.username, "staff_id": str(p.staff_id)}
            for p in pending
        ],
        # §F (doubts) is a later milestone — kept as an explicit placeholder so
        # the Flutter card can render "coming soon" without a shape change.
        "doubts": {"available": False, "open": 0},
    }
