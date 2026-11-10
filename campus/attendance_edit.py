# campus/attendance_edit.py
"""
Attendance galti-sudhaar (correction) rules — edit window + reason + audit log.

Rules
-----
* FIRST marking of a (student, date, subject) is free — nothing to correct yet.
* CHANGING a row's status, or DELETING it ("clear"), is an *edit*:
    1. Edit window — a teacher can edit only within
       `Campus.attendance_edit_window_hours` (default 24) of the row's FIRST
       marking (`Attendance.marked_at`; editing never re-opens the window).
       After that the request fails with 403 `edit_window_closed`.
       A campus admin / principal-HOD may still edit (override), see below.
    2. Reason — every edit needs a short reason (3-200 chars), otherwise
       400 `reason_required`. (The app's Undo button sends "Undo".)
    3. Audit log — every changed row writes one `AttendanceEditLog` row
       (who / when / old -> new / reason / was_override) plus ONE
       `CampusAuditLog` summary entry per request (`attendance.edit` /
       `attendance.clear`, visible in the existing campus audit-log API).
* Admin / principal override after the window: allowed, reason mandatory,
  logged with `was_override=True`.
* Re-saving a register with NO real change is a no-op: no reason needed, no
  window check, nothing logged.

Pure helpers + two DB writers (`record_edits`). Permission to touch the
section at all is checked by the views (`IsSectionSubjectStaffOrReadOnly`).
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import APIException

from .audit import log_action
from .models import AttendanceEditLog
from .permissions import is_campus_admin_or_principal

DEFAULT_WINDOW_HOURS = 24
REASON_MIN_LEN = 3
REASON_MAX_LEN = 200
MAX_AUDIT_CHANGES = 100  # per CampusAuditLog.metadata (keeps the JSON small)


class AttendanceEditError(APIException):
    """Business-rule failure with a stable machine `code`. The project's
    exception handler (tuitionclass.exceptions) puts `machine_code` in the
    response envelope's `code`, same as `RosterAPIError`."""

    status_code = 400

    def __init__(self, code, message, status_code=400):
        super().__init__(detail=message)
        self.machine_code = code
        self.status_code = status_code


def window_hours(campus):
    hours = getattr(campus, "attendance_edit_window_hours", None) or DEFAULT_WINDOW_HOURS
    return int(hours)


def edit_deadline(marked_at, campus):
    return marked_at + timedelta(hours=window_hours(campus))


def is_row_locked(row, campus, now=None):
    """True if the teacher edit window for this already-marked row has closed."""
    now = now or timezone.now()
    return now > edit_deadline(row.marked_at, campus)


def can_override(user, campus):
    """Admin / principal-HOD of THIS campus may edit after the window."""
    return is_campus_admin_or_principal(user, campus.id)


def clean_reason(raw):
    reason = " ".join(str(raw or "").split())  # trim + collapse whitespace/newlines
    if len(reason) > REASON_MAX_LEN:
        raise AttendanceEditError(
            "reason_too_long", f"Reason must be at most {REASON_MAX_LEN} characters."
        )
    return reason


def check_edit(*, user, campus, rows, reason, now=None):
    """
    Gate an edit of ALREADY-MARKED `rows` (the ones whose status really
    changes, or that are being deleted). Returns `(clean_reason, override_used)`
    or raises `AttendanceEditError`.

    No rows -> `("", False)` (nothing is being edited).
    """
    rows = list(rows)
    if not rows:
        return "", False
    now = now or timezone.now()
    locked = [r for r in rows if is_row_locked(r, campus, now)]
    override = False
    if locked:
        if not can_override(user, campus):
            hours = window_hours(campus)
            raise AttendanceEditError(
                "edit_window_closed",
                f"The {hours}-hour edit window for this attendance has closed. "
                "Only a campus admin or principal can change it now.",
                status_code=403,
            )
        override = True
    reason = clean_reason(reason)
    if len(reason) < REASON_MIN_LEN:
        raise AttendanceEditError(
            "reason_required", "A reason is required to change attendance that is already marked."
        )
    return reason, override


def _student_label(enrollment):
    student = getattr(enrollment, "student", None)
    if student is None:
        return ""
    full = f"{student.first_name} {student.last_name}".strip()
    return (full or student.username)[:150]


def record_edits(*, user, campus, section, date, subject, changes, reason, override, now=None):
    """
    Write the audit trail for one edit request. `changes` is a list of
    `(attendance_row, old_status, new_status_or_None)` — `None` means the row
    was deleted ("clear"). Call it INSIDE the same transaction as the write so a
    rolled-back edit leaves no log behind.
    """
    if not changes:
        return
    now = now or timezone.now()
    logs = []
    for row, old_status, new_status in changes:
        logs.append(
            AttendanceEditLog(
                campus=campus,
                section=section,
                enrollment_id=row.enrollment_id,
                subject=subject,
                student_label=_student_label(getattr(row, "enrollment", None)),
                date=date,
                action=AttendanceEditLog.Action.CLEAR if new_status is None else AttendanceEditLog.Action.EDIT,
                old_status=old_status,
                new_status=new_status or "",
                reason=reason[:300],
                edited_by=user,
                edited_at=now,
                original_marked_at=row.marked_at,
                was_override=override,
            )
        )
    AttendanceEditLog.objects.bulk_create(logs)

    cleared = all(new is None for _, _, new in changes)
    action = "attendance.clear" if cleared else "attendance.edit"
    log_action(
        campus.id,
        user,
        action,
        target_type="Section",
        target_id=getattr(section, "pk", "") or "",
        summary=(
            f"{'Cleared' if cleared else 'Edited'} attendance of {len(changes)} student(s) "
            f"for {date.isoformat()}: {reason}"
        )[:300],
        date=date.isoformat(),
        subject=str(subject.pk) if subject else None,
        reason=reason,
        override=override,
        count=len(changes),
        changes=[
            {"enrollment": str(row.enrollment_id), "old": old, "new": new}
            for row, old, new in changes[:MAX_AUDIT_CHANGES]
        ],
    )


def edit_info(rows, *, user, campus, now=None):
    """
    Edit-state summary the app shows for one day's register. `rows` are the
    EXISTING `Attendance` rows of that (section, date, subject).

        {window_hours, editable, editable_until, locked, can_override,
         reason_required, marked_at, last_edited_at, edit_count}

    `editable` is what THIS user can do right now: the window is open for every
    row, or they may override. `editable_until` is the earliest row deadline
    (None when nothing is marked yet).
    """
    rows = list(rows)
    hours = window_hours(campus)
    override_ok = can_override(user, campus)
    if not rows:
        return {
            "window_hours": hours,
            "editable": True,
            "editable_until": None,
            "locked": False,
            "can_override": override_ok,
            "reason_required": False,
            "marked_at": None,
            "last_edited_at": None,
            "edit_count": 0,
        }
    now = now or timezone.now()
    first = min(r.marked_at for r in rows)
    deadline = edit_deadline(first, campus)
    locked = now > deadline
    edited = [r.last_edited_at for r in rows if r.last_edited_at]
    return {
        "window_hours": hours,
        "editable": (not locked) or override_ok,
        "editable_until": deadline,
        "locked": locked,
        "can_override": override_ok,
        "reason_required": True,
        "marked_at": first,
        "last_edited_at": max(edited) if edited else None,
        "edit_count": sum(r.edit_count for r in rows),
    }
