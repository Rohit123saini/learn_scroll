# campus/attendance_push.py
"""
Push: "your child was absent" -> the student's linked PARENTS (never the student).

Called from the two places an `Attendance` row can become ABSENT:
  * `bulk_ops.bulk_mark_attendance`   (teacher 2-tap flow)
  * `views.AttendanceViewSet`         (single create / PATCH)

Rules (each one is deliberate, not an accident):
  * Only for a mark dated TODAY. A teacher back-filling or correcting an old
    day must not buzz parents about something that happened last week.
  * Only when the row is NEW or CHANGED to absent (callers pass only those),
    so re-saving an unchanged register never re-notifies.
  * At most ONE push per (enrollment, date) — subject/period-wise registers
    would otherwise send a parent six pushes for one missed day. The marker is
    an atomic `cache.add`; it is rolled back if nothing could be sent so a
    failed send is retried on the next save.
  * Two parent kinds are covered: real `User` parents (CampusParentLink, via
    the normal bell + push pipeline, which honours NotificationPreference —
    push toggle, muted types, quiet hours) and loginless Parent Mode devices
    (ParentToken FCM, via `bridge.push_to_parent_devices`).
  * Never raises — a notification problem must never fail the attendance save.

Correction (`notify_parents_of_corrections`): if the parent already got the
absent push today and the teacher then changes that row to present / late /
leave (or clears it), the parent gets a short "attendance updated" push. Only
when (a) the absent push really went out (its marker exists), (b) the child is
no longer absent in ANY register of that day, (c) the date is today. The marker
is removed afterwards so a later absent mark can notify again.

Everything cross-app goes through `campus.bridge` (design doc golden rule).
"""
import logging

from django.core.cache import cache
from django.utils import timezone

from . import bridge
from .bridge import NotifTypes
from .models import Attendance, CampusParentLink, StudentEnrollment

logger = logging.getLogger(__name__)

_ABSENT_PUSH_KEY = "campus:absent-push:{enrollment_id}:{date}"
# 36h: spans the calendar day plus scheduler/timezone jitter, then expires.
_ABSENT_PUSH_TTL_SECONDS = 36 * 60 * 60
_SHADOW_PARENT_PREFIX = "parent_shadow_"  # see bridge._get_or_create_shadow_parent_user


def _display_name(user):
    full = getattr(user, "get_full_name", lambda: "")() or ""
    return full.strip() or user.username


def notify_parents_of_absences(*, enrollment_ids, date, subject=None) -> int:
    """Push the parents of every enrollment in `enrollment_ids` (newly absent
    on `date`). Returns the number of students whose parents were notified."""
    try:
        enrollment_ids = list(enrollment_ids or [])
        if not enrollment_ids or date != timezone.localdate():
            return 0

        enrollments = list(
            StudentEnrollment.objects.filter(
                pk__in=enrollment_ids, status=StudentEnrollment.Status.ACTIVE
            ).select_related("student", "section__school_class")
        )
        if not enrollments:
            return 0

        # One query for every parent link of every absent student.
        campus_ids = {e.section.school_class.campus_id for e in enrollments}
        student_ids = {e.student_id for e in enrollments}
        parents_by_student = {}
        for link in CampusParentLink.objects.filter(
            campus_id__in=campus_ids, student_id__in=student_ids
        ).select_related("parent"):
            parents_by_student.setdefault((link.campus_id, link.student_id), []).append(link.parent)

        notified = 0
        for enrollment in enrollments:
            key = _ABSENT_PUSH_KEY.format(enrollment_id=enrollment.pk, date=date.isoformat())
            if not cache.add(key, 1, timeout=_ABSENT_PUSH_TTL_SECONDS):
                continue  # already pushed for this child today

            student = enrollment.student
            campus_id = enrollment.section.school_class.campus_id
            name = _display_name(student)
            where = f" in {subject.name}" if subject is not None else ""
            title = f"{name} was absent today"
            body = f"{name} was marked absent{where} on {date:%d %b}."
            data = {
                "type": NotifTypes.CAMPUS_STUDENT_ABSENT,
                "student_id": str(student.pk),
                "attendance_date": date.isoformat(),
                "campus_id": str(campus_id),
            }

            real_parents = [
                p for p in parents_by_student.get((campus_id, student.pk), [])
                if not (p.username or "").startswith(_SHADOW_PARENT_PREFIX)
            ]
            try:
                if real_parents:
                    bridge.notify(
                        users=real_parents,
                        notif_type=NotifTypes.CAMPUS_STUDENT_ABSENT,
                        title=title,
                        body=body,
                        data=data,
                        push=True,
                    )
                device_pushes = bridge.push_to_parent_devices(
                    student=student, title=title, body=body, data=data
                )
            except Exception:
                logger.exception("campus absent push failed (enrollment=%s).", enrollment.pk)
                cache.delete(key)
                continue

            if not real_parents and not device_pushes:
                # No parent to tell -> don't burn the marker; a parent who links
                # later today still gets the push if the register is re-saved.
                cache.delete(key)
                continue
            notified += 1
        return notified
    except Exception:
        logger.exception("campus.attendance_push.notify_parents_of_absences failed.")
        return 0


def notify_parents_of_corrections(*, enrollment_ids, date, subject=None) -> int:
    """Tell the parents that an absent alert sent earlier today no longer holds.
    `enrollment_ids` = rows that just changed FROM absent to something else (or
    were cleared). Returns the number of students whose parents were told."""
    try:
        enrollment_ids = list(enrollment_ids or [])
        if not enrollment_ids or date != timezone.localdate():
            return 0

        enrollments = list(
            StudentEnrollment.objects.filter(
                pk__in=enrollment_ids, status=StudentEnrollment.Status.ACTIVE
            ).select_related("student", "section__school_class")
        )
        if not enrollments:
            return 0

        # Still absent in another register (e.g. another subject) -> nothing to correct.
        still_absent = set(
            Attendance.objects.filter(
                enrollment_id__in=[e.pk for e in enrollments], date=date, status=Attendance.Status.ABSENT
            ).values_list("enrollment_id", flat=True)
        )

        campus_ids = {e.section.school_class.campus_id for e in enrollments}
        student_ids = {e.student_id for e in enrollments}
        parents_by_student = {}
        for link in CampusParentLink.objects.filter(
            campus_id__in=campus_ids, student_id__in=student_ids
        ).select_related("parent"):
            parents_by_student.setdefault((link.campus_id, link.student_id), []).append(link.parent)

        notified = 0
        for enrollment in enrollments:
            if enrollment.pk in still_absent:
                continue
            key = _ABSENT_PUSH_KEY.format(enrollment_id=enrollment.pk, date=date.isoformat())
            if cache.get(key) is None:
                continue  # the parents were never told "absent" -> nothing to correct

            student = enrollment.student
            campus_id = enrollment.section.school_class.campus_id
            name = _display_name(student)
            title = f"Attendance updated: {name}"
            body = f"{name} is no longer marked absent on {date:%d %b}."
            data = {
                "type": NotifTypes.CAMPUS_STUDENT_ABSENT,
                "corrected": "1",
                "student_id": str(student.pk),
                "attendance_date": date.isoformat(),
                "campus_id": str(campus_id),
            }
            real_parents = [
                p for p in parents_by_student.get((campus_id, student.pk), [])
                if not (p.username or "").startswith(_SHADOW_PARENT_PREFIX)
            ]
            try:
                if real_parents:
                    bridge.notify(
                        users=real_parents,
                        notif_type=NotifTypes.CAMPUS_STUDENT_ABSENT,
                        title=title,
                        body=body,
                        data=data,
                        push=True,
                    )
                bridge.push_to_parent_devices(student=student, title=title, body=body, data=data)
            except Exception:
                logger.exception("campus correction push failed (enrollment=%s).", enrollment.pk)
                continue  # keep the marker; the next save retries
            cache.delete(key)  # a later absent mark may notify again
            notified += 1
        return notified
    except Exception:
        logger.exception("campus.attendance_push.notify_parents_of_corrections failed.")
        return 0
