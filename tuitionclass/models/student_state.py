# tuitionclass/models/student_state.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models
from django.utils import timezone
from .schedules import ClassSession
from .classroom import Classroom
from .live import SessionParticipant


# ---------------------------------------------------------------------------
# FEATURE (trial/demo access) — one row per (classroom, student): the
# student's single trial. Created by ClassroomViewSet.trial_join once the
# teacher's trial code matched AND a live session was actually joined.
#
#   unique (classroom, user)  -> one trial per student per classroom, ever.
#                                 Calling trial-join again while it's still
#                                 running just re-issues a token (reconnect)
#                                 WITHOUT extending expires_at; after it
#                                 lapsed it's a hard "trial already used".
#   expires_at                -> started_at + classroom.trial_duration_minutes
#                                 (snapshotted, so a teacher editing the
#                                 duration later doesn't stretch/shrink a
#                                 trial already in progress).
#   ended_at                  -> stamped once the server actually removed the
#                                 student (tasks.expire_trial_access).
#
# `is_active()` is what views._has_room_access() consults for the
# in-room features (chat/polls/reactions/socket). The regular join()/
# token() gate deliberately does NOT — trial is a gateway, not a pass.
# ---------------------------------------------------------------------------
class ClassroomTrialAccess(models.Model):
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="trial_accesses")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_trials")
    session = models.ForeignKey(
        ClassSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="trial_accesses"
    )
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=5)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("classroom", "user")
        indexes = [models.Index(fields=["ended_at", "expires_at"], name="trial_ended_expires_idx")]

    def __str__(self):
        return f"Trial: {self.user} @ {self.classroom.title} (until {self.expires_at:%H:%M})"

    def is_active(self) -> bool:
        return self.ended_at is None and self.expires_at > timezone.now()

    def remaining_seconds(self) -> int:
        return max(int((self.expires_at - timezone.now()).total_seconds()), 0)


# ---------------------------------------------------------------------------
# FEATURE (optional Parent) — per (classroom, student) parent state.
#
#   no row          -> "none"    teacher hasn't decided yet
#   skipped         -> teacher pressed "Skip" (no more prompts; can add later)
#   invited         -> an invite link/code was generated / sent
#   "linked"        -> NEVER stored: computed live from message.ParentToken
#                      (an APPROVED, unexpired device on an active code), so
#                      it can't go stale when a student revokes access.
#
# The state only drives UI prompts + which students get the bulk "send link"
# and which parents get notifications. It is never consulted by join / pass
# purchase / session / attendance / payment code.
# ---------------------------------------------------------------------------
class ClassroomParentState(models.Model):
    class Status(models.TextChoices):
        SKIPPED = "skipped", "Skipped"
        INVITED = "invited", "Invited"

    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE, related_name="parent_states")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="classroom_parent_states")
    status = models.CharField(max_length=10, choices=Status.choices)
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("classroom", "student")
        indexes = [models.Index(fields=["classroom", "status"], name="parentstate_cls_status_idx")]

    def __str__(self):
        return f"{self.student} / {self.classroom_id}: {self.status}"


# ---------------------------------------------------------------------------
# FEATURE (student attendance) — one row per (session, student).
#
# Built automatically when a session reaches COMPLETED (see
# tuitionclass/attendance.py + signals.cleanup_on_session_end) from
# SessionParticipant join/leave times; a teacher/staff member can then edit
# any row by hand (source flips to MANUAL and is never overwritten by a
# later auto-rebuild). Attendance is a RECORD (report card, parent portal,
# my-progress) — it never affects the daily escrow release
# (PassPurchase.charge_for_session), which is per class-day regardless of
# present/absent.
# ---------------------------------------------------------------------------
class SessionAttendance(models.Model):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        LATE = "late", "Late"
        ABSENT = "absent", "Absent"
        EXCUSED = "excused", "Excused"

    class Source(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"

    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="attendance")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="session_attendance")
    status = models.CharField(max_length=10, choices=Status.choices)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.AUTO)
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    minutes_present = models.PositiveIntegerField(default=0)
    marked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="session_attendance_marked"
    )
    marked_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        unique_together = ("session", "student")
        indexes = [models.Index(fields=["student", "status"], name="attendance_student_status_idx")]

    def __str__(self):
        return f"{self.student_id} @ session {self.session_id}: {self.status} ({self.source})"


# ---------------------------------------------------------------------------
# 🔧 GAP FIX (Gap 3) — `StudentReportCard.attendance_percent` (and the
# parent dashboard's per-classroom `attendance_percent`) must be computed
# from THIS app's own session-attendance record — `ClassSession` +
# `SessionParticipant` above, the exact same data `Classroom.has_access()`
# and `enrolled_count` are already built on — and nothing else.
#
# The message app separately has its own `StudyRoomAttendance`, which
# drives a self-check-in "study room" streak widget
# (`current_streak`/`longest_streak`, via `attendance_utils.
# compute_attendance_stats_bulk`). That is a different, unrelated feature
# — group-study self-check-in, not real classroom attendance — and per
# product decision it has NOTHING to do with tuitionclass at all (message
# app is chat/group-study only; it has no teacher/student/classroom
# concept). It is left completely untouched, is never imported here, and
# must never feed a report card or a tuitionclass-side attendance number —
# a student could self-check-in to a study room daily and never once
# attend an actual class, or the reverse, and a report card silently
# built off the wrong one would disagree with what the teacher sees in
# their own classroom's attendance log.
# ---------------------------------------------------------------------------
def compute_attendance_percent_bulk(classroom_ids, user):
    """
    `classroom_ids`: iterable of Classroom ids.
    `user`: the student.

    Returns: {classroom_id: float} — percent (0-100, rounded to 2
    decimal places) of this classroom's COMPLETED sessions the student
    attended (had at least one `SessionParticipant` row with
    role=STUDENT for that session). A classroom with zero COMPLETED
    sessions yet gets 0, never a divide-by-zero. Every requested
    classroom_id is guaranteed a key, same "no missing-key handling for
    the caller" contract `compute_attendance_stats_bulk` (message app)
    already follows.

    Only COMPLETED sessions count in the denominator — a SCHEDULED
    session hasn't happened yet and a CANCELLED one was never held, so
    neither should be held against (or credited to) attendance. This
    mirrors `PassPurchase.charge_for_session`'s own rule that a day only
    "counts" once its session actually reached COMPLETED.

    Bulk — a fixed 2 queries total no matter how many classroom_ids are
    passed in, not one query per classroom.
    """
    classroom_ids = list(classroom_ids)
    if not classroom_ids:
        return {}
    counts = compute_attendance_counts_bulk(classroom_ids, user)
    return {
        cid: (round((a / t) * 100, 2) if t else 0) for cid, (a, t) in counts.items()
    }


def compute_attendance_counts_bulk(classroom_ids, user):
    """{classroom_id: (attended, total)} — the raw counts behind
    compute_attendance_percent_bulk (which is just attended/total*100).
    Every requested classroom_id gets a key."""
    classroom_ids = list(classroom_ids)
    if not classroom_ids:
        return {}

    # Source of truth = SessionAttendance (auto-built when a session COMPLETES,
    # hand-editable by the teacher). A COMPLETED session that has no
    # SessionAttendance row for this student yet (built before the table
    # existed / build hasn't run) falls back to the raw SessionParticipant
    # record so the number is never silently lower than reality.
    #   present + late      -> attended
    #   excused             -> removed from the denominator (not held against)
    #   absent              -> counts against
    sessions = list(
        ClassSession.objects.filter(
            classroom_id__in=classroom_ids, status=ClassSession.Status.COMPLETED,
        ).values_list("id", "classroom_id")
    )
    session_classroom = dict(sessions)

    att_status = dict(
        SessionAttendance.objects.filter(
            session_id__in=session_classroom.keys(), student=user,
        ).values_list("session_id", "status")
    )
    # `distinct` session ids — a rejoin creates a new SessionParticipant row
    # per unique_together = (session, user, joined_at).
    participated = set(
        SessionParticipant.objects.filter(
            session_id__in=session_classroom.keys(),
            user=user,
            role=SessionParticipant.Role.STUDENT,
            is_trial=False,
        ).values_list("session_id", flat=True)
    )

    total = {cid: 0 for cid in classroom_ids}
    attended = {cid: 0 for cid in classroom_ids}
    for session_id, cid in session_classroom.items():
        status = att_status.get(session_id)
        if status is None:
            total[cid] += 1
            if session_id in participated:
                attended[cid] += 1
        elif status == SessionAttendance.Status.EXCUSED:
            continue
        else:
            total[cid] += 1
            if status in (SessionAttendance.Status.PRESENT, SessionAttendance.Status.LATE):
                attended[cid] += 1

    return {cid: (attended.get(cid, 0), total.get(cid, 0)) for cid in classroom_ids}
