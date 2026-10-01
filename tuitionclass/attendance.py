"""
tuitionclass/attendance.py

Two small features that share one module because they share one audience
(the classroom's teacher, the student, and — optionally — the parent):

  A. SESSION ATTENDANCE
       build_session_attendance(session)   auto build when a session COMPLETES
       apply_manual_edits(...)             teacher/staff hand edits
       attendance_summary(...)             per-student totals for a classroom

  B. OPTIONAL PARENT
       parent_status_map(classroom, ids)   none | skipped | invited | linked
       notify_linked_parents(...)          push ONLY to linked parents

Hard rules (task file, sections 4 + 5):
  * Attendance is a RECORD. It never feeds PassPurchase.charge_for_session —
    the daily escrow release happens per class-day, present or absent.
  * A parent is always optional. Nothing here is ever a gate for join /
    pass purchase / session / attendance / payment.
  * Auto build is idempotent (safe to double-fire) and never overwrites a
    row a teacher edited by hand (source=MANUAL).
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import (
    ClassSession,
    ClassroomParentState,
    PassPurchase,
    SessionAttendance,
    SessionParticipant,
)

logger = logging.getLogger(__name__)

# A student who first appears more than this many minutes after the session
# started is marked LATE (still counts as attended).
LATE_GRACE_MINUTES = 10
# Parent "low attendance" alert: fires once, on the session that takes the
# student from >= threshold to < threshold, and only after a few sessions so
# one missed first class doesn't alarm anyone.
LOW_ATTENDANCE_PERCENT = 75
LOW_ATTENDANCE_MIN_SESSIONS = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _session_window(session):
    start = session.actual_start or session.scheduled_start
    end = session.actual_end or session.scheduled_end
    if end <= start:
        end = start + timedelta(minutes=1)
    return start, end


def enrolled_student_ids(classroom, session=None):
    """Distinct students holding a successful pass for `classroom` (active OR
    lapsed — same breadth as Classroom.is_enrolled()). With `session`, only
    passes that overlap that session's window count, so a student who bought
    a pass AFTER the session isn't marked absent for it, and one whose pass
    expired before it isn't either."""
    qs = PassPurchase.objects.filter(
        class_pass__classroom=classroom,
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
    )
    if session is not None:
        start, end = _session_window(session)
        qs = qs.filter(purchased_at__lte=end, expires_at__gte=start)
    return set(qs.values_list("student_id", flat=True))


def _merged_seconds(intervals):
    """Total seconds covered by a list of (start, end) datetimes, overlaps
    counted once (a rejoin can leave overlapping participant rows)."""
    total = 0.0
    cur_start = cur_end = None
    for s, e in sorted(intervals):
        if cur_end is None or s > cur_end:
            if cur_end is not None:
                total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = s, e
        else:
            cur_end = max(cur_end, e)
    if cur_end is not None:
        total += (cur_end - cur_start).total_seconds()
    return total


# ---------------------------------------------------------------------------
# A. attendance — auto build
# ---------------------------------------------------------------------------
def build_session_attendance(session_or_id):
    """Create SessionAttendance rows for a COMPLETED session. Idempotent.

    Returns {"created": n, "updated": n, "skipped_manual": n, "ran": bool}.

    present : in the room for >= classroom.attendance_min_percent of the session
    late    : same, but first joined more than LATE_GRACE_MINUTES after the start
    absent  : enrolled but never joined, or joined for less than the minimum
    (excused is only ever set by hand.)
    """
    session = (
        ClassSession.objects.select_related("classroom").filter(pk=getattr(session_or_id, "pk", session_or_id)).first()
    )
    result = {"created": 0, "updated": 0, "skipped_manual": 0, "ran": False}
    if session is None or session.status != ClassSession.Status.COMPLETED:
        return result

    classroom = session.classroom
    participants = list(
        SessionParticipant.objects.filter(
            session=session, role=SessionParticipant.Role.STUDENT, is_trial=False,
        )
    )
    # A session that never went live and nobody joined didn't happen — don't
    # mark the whole roster absent for a class that never ran.
    if session.actual_start is None and not participants:
        return result

    start, end = _session_window(session)
    duration = max((end - start).total_seconds(), 60.0)
    grace_cutoff = start + timedelta(minutes=LATE_GRACE_MINUTES)

    by_student = {}
    for p in participants:
        by_student.setdefault(p.user_id, []).append(p)

    roster = enrolled_student_ids(classroom, session)
    # Someone who joined on a valid pass is a student of this class even if
    # their pass record is odd — never drop a real attendee.
    roster |= set(by_student)

    min_ratio = classroom.attendance_min_percent / 100.0
    now = timezone.now()

    with transaction.atomic():
        existing = {
            a.student_id: a
            for a in SessionAttendance.objects.select_for_update().filter(session=session)
        }
        newly_absent = []
        to_create, to_update = [], []
        for sid in roster:
            rows = by_student.get(sid, [])
            intervals = []
            first_join = last_left = None
            for p in rows:
                s = max(p.joined_at, start)
                e = min(p.left_at or end, end)
                if e > s:
                    intervals.append((s, e))
                first_join = p.joined_at if first_join is None else min(first_join, p.joined_at)
                if p.left_at:
                    last_left = p.left_at if last_left is None else max(last_left, p.left_at)
            seconds = _merged_seconds(intervals)
            ratio = seconds / duration

            if not rows or ratio < min_ratio:
                status = SessionAttendance.Status.ABSENT
            elif first_join and first_join > grace_cutoff:
                status = SessionAttendance.Status.LATE
            else:
                status = SessionAttendance.Status.PRESENT

            fields = dict(
                status=status,
                source=SessionAttendance.Source.AUTO,
                joined_at=first_join,
                left_at=last_left,
                minutes_present=int(seconds // 60),
                marked_by=None,
                marked_at=now,
            )
            row = existing.get(sid)
            if row is None:
                to_create.append(SessionAttendance(session=session, student_id=sid, **fields))
                if status == SessionAttendance.Status.ABSENT:
                    newly_absent.append(sid)
            elif row.source == SessionAttendance.Source.MANUAL:
                result["skipped_manual"] += 1
            else:
                previous = row.status
                changed = any(
                    getattr(row, k) != fields[k]
                    for k in ("status", "joined_at", "left_at", "minutes_present")
                )
                if changed:
                    for k, v in fields.items():
                        setattr(row, k, v)
                    to_update.append(row)
                    if status == SessionAttendance.Status.ABSENT and previous != status:
                        newly_absent.append(sid)

        if to_create:
            SessionAttendance.objects.bulk_create(to_create, ignore_conflicts=True)
        if to_update:
            SessionAttendance.objects.bulk_update(
                to_update,
                ["status", "source", "joined_at", "left_at", "minutes_present", "marked_by", "marked_at"],
            )
        result.update(created=len(to_create), updated=len(to_update), ran=True)

    # Notifications only for rows created/changed to absent in THIS run, so a
    # double-fire never pings a parent twice. Best-effort, outside the txn.
    for sid in newly_absent:
        try:
            notify_absent(session, sid)
        except Exception:
            logger.exception("Absent notification failed (session %s, student %s).", session.pk, sid)
    return result


# ---------------------------------------------------------------------------
# A. attendance — manual edit
# ---------------------------------------------------------------------------
def attendance_edit_deadline(session):
    end = session.actual_end or session.scheduled_end
    return end + timedelta(days=session.classroom.attendance_edit_days)


def attendance_edit_open(session) -> bool:
    return session.status == ClassSession.Status.COMPLETED and timezone.now() <= attendance_edit_deadline(session)


def apply_manual_edits(session, records, marked_by):
    """records: [{"student": id, "status": "present|late|absent|excused", "note": "..."}].
    Caller has already validated permission, the edit window and that every
    student is enrolled. Returns the list of SessionAttendance rows written."""
    now = timezone.now()
    written, became_absent = [], []
    with transaction.atomic():
        for rec in records:
            sid, status = rec["student"], rec["status"]
            note = (rec.get("note") or "")[:200]
            row = SessionAttendance.objects.select_for_update().filter(session=session, student_id=sid).first()
            previous = row.status if row else None
            if row is None:
                row = SessionAttendance(session=session, student_id=sid)
            row.status = status
            row.source = SessionAttendance.Source.MANUAL
            row.marked_by = marked_by
            row.marked_at = now
            row.note = note
            row.save()
            written.append(row)
            if status == SessionAttendance.Status.ABSENT and previous != status:
                became_absent.append(sid)
    for sid in became_absent:
        try:
            notify_absent(session, sid)
        except Exception:
            logger.exception("Absent notification failed (session %s, student %s).", session.pk, sid)
    return written


# ---------------------------------------------------------------------------
# A. attendance — summary
# ---------------------------------------------------------------------------
def attendance_summary(classroom, student_ids=None):
    """Per-student totals over this classroom's COMPLETED sessions. Excused
    sessions are removed from the denominator. Returns
    (rows, sessions_total) — rows sorted by student id."""
    completed = list(
        ClassSession.objects.filter(classroom=classroom, status=ClassSession.Status.COMPLETED)
        .values_list("id", flat=True)
    )
    ids = set(student_ids) if student_ids is not None else enrolled_student_ids(classroom)
    counts = {sid: {"present": 0, "late": 0, "absent": 0, "excused": 0} for sid in ids}
    for sid, status in SessionAttendance.objects.filter(
        session_id__in=completed, student_id__in=ids
    ).values_list("student_id", "status"):
        counts[sid][status] += 1
    rows = []
    for sid in sorted(ids):
        c = counts[sid]
        counted = c["present"] + c["late"] + c["absent"]
        attended = c["present"] + c["late"]
        rows.append({
            "student": sid,
            **c,
            "sessions_counted": counted,
            "attendance_percent": round(attended / counted * 100, 2) if counted else 0,
        })
    return rows, len(completed)


# ---------------------------------------------------------------------------
# B. optional parent — status + notifications
# ---------------------------------------------------------------------------
def linked_parent_tokens(student_ids):
    """{student_id: [ParentToken, ...]} for students with at least one
    APPROVED, unexpired parent device on an active, unexpired code."""
    from message.models import ParentToken

    now = timezone.now()
    tokens = (
        ParentToken.objects.filter(
            status=ParentToken.Status.APPROVED,
            parent_access_code__student_id__in=list(student_ids),
            parent_access_code__is_active=True,
        )
        .select_related("parent_access_code")
    )
    out = {}
    for t in tokens:
        code = t.parent_access_code
        if code.expires_at and code.expires_at <= now:
            continue
        if t.is_expired:
            continue
        out.setdefault(code.student_id, []).append(t)
    return out


def parent_status_map(classroom, student_ids):
    """{student_id: "none"|"skipped"|"invited"|"linked"}. `linked` is computed
    live (never stored) so a revoked device can't leave a stale chip."""
    student_ids = list(student_ids)
    linked = set(linked_parent_tokens(student_ids))
    stored = dict(
        ClassroomParentState.objects.filter(classroom=classroom, student_id__in=student_ids)
        .values_list("student_id", "status")
    )
    return {
        sid: ("linked" if sid in linked else stored.get(sid, "none"))
        for sid in student_ids
    }


def set_parent_state(classroom, student_id, status, by):
    obj, _ = ClassroomParentState.objects.update_or_create(
        classroom=classroom, student_id=student_id,
        defaults={"status": status, "updated_by": by},
    )
    return obj


def notify_linked_parents(classroom, student_id, *, title, body, data=None) -> int:
    """Push to the student's linked parent devices — and ONLY those. Does
    nothing when the classroom has parents switched off, or the student has
    no linked parent (none / skipped / invited). Returns pushes attempted."""
    if not classroom.parents_enabled:
        return 0
    tokens = linked_parent_tokens([student_id]).get(student_id, [])
    sent = 0
    from message.push_utils import send_parent_push

    for t in tokens:
        if not t.fcm_token:
            continue
        try:
            send_parent_push(
                fcm_token=t.fcm_token, title=title, body=body,
                data={**(data or {}), "classroom_id": str(classroom.pk)},
            )
            sent += 1
        except Exception:
            logger.exception("Parent push failed (classroom %s, student %s).", classroom.pk, student_id)
    return sent


def _student_label(student_id):
    from django.contrib.auth import get_user_model

    u = get_user_model().objects.filter(pk=student_id).first()
    return (u.get_full_name() or u.username) if u else "Your child"


def _counts_excluding(classroom, student_id, exclude_session_id=None):
    qs = SessionAttendance.objects.filter(
        session__classroom=classroom, session__status=ClassSession.Status.COMPLETED,
        student_id=student_id,
    ).exclude(status=SessionAttendance.Status.EXCUSED)
    if exclude_session_id is not None:
        qs = qs.exclude(session_id=exclude_session_id)
    rows = list(qs.values_list("status", flat=True))
    attended = sum(1 for s in rows if s in ("present", "late"))
    return attended, len(rows)


def notify_absent(session, student_id):
    """'Student absent' push, plus a one-time 'low attendance' push on the
    session that crosses the threshold. Linked parents only."""
    classroom = session.classroom
    if not classroom.parents_enabled:
        return
    name = _student_label(student_id)
    notify_linked_parents(
        classroom, student_id,
        title=f"{name} was absent — {classroom.title}",
        body=f"{name} did not attend the session on {session.scheduled_start:%d %b}.",
        data={"type": "student_absent", "session_id": str(session.pk)},
    )
    a_before, t_before = _counts_excluding(classroom, student_id, exclude_session_id=session.pk)
    a_now, t_now = _counts_excluding(classroom, student_id)
    if t_now >= LOW_ATTENDANCE_MIN_SESSIONS and t_before:
        before_pct = a_before / t_before * 100
        now_pct = a_now / t_now * 100
        if before_pct >= LOW_ATTENDANCE_PERCENT > now_pct:
            notify_linked_parents(
                classroom, student_id,
                title=f"Low attendance — {classroom.title}",
                body=f"{name}'s attendance has dropped to {now_pct:.0f}%.",
                data={"type": "low_attendance"},
            )
