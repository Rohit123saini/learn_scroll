# campus/services.py
"""
Read-side computations that are deliberately NOT stored columns (design
doc §5/§7 — `compute_attendance_summary` and `generate_report_card_data`
are the "service function" both `Attendance` and `ResultEntry`'s model
docstrings point to instead of a cached %-age/aggregate field). Kept as
plain functions, not model methods, since both take an optional extra
filter argument (`subject`, `exam_term`) that doesn't belong pinned to
one model instance.

Also home to the attendance-%-threshold check the `campus-daily-
attendance-check` Celery task (see `tasks.py`) calls per enrollment —
kept here, not duplicated in `tasks.py`, so the task and the on-demand
`AttendanceViewSet.summary` action can never drift out of sync on what
"percent" means.

F-3 (this pass) — `compute_attendance_streak()` / `compute_assignment_
ontime_streak()` added below. Neither existed in this file before this
pass; `tasks.check_attendance_streak_rewards()` /
`check_assignment_ontime_streak_rewards()` were already calling
`from .services import compute_attendance_streak` /
`compute_assignment_ontime_streak` — i.e. this module could not have
been imported successfully by either task before this fix, meaning
BOTH streak Celery tasks raised `ImportError` on every single run.
That's the "abhi crash karte the" the Task 14 checklist refers to.
"""
from django.db.models import Sum

from .models import Attendance, ResultEntry

# Statuses that count as "attended" for the %-age — LATE still counts
# (the student was there), ABSENT/LEAVE do not. This is the one place
# that definition lives; nothing else in the app re-derives it.
ATTENDED_STATUSES = (Attendance.Status.PRESENT, Attendance.Status.LATE)


def compute_attendance_summary(enrollment, subject=None):
    """
    `subject=None` (the default, and what a plain
    `?enrollment=<id>` request without `&subject=` resolves to) means
    "every attendance record for this enrollment regardless of
    subject" — daily marks AND subject-wise marks together — NOT "only
    the daily (subject-less) marks". Pass an actual `Subject` instance
    to scope the summary to just that subject's marks.

    Returns a plain dict (never persisted) with per-status counts and
    the overall %-age, rounded to 2 decimal places.
    """
    qs = Attendance.objects.filter(enrollment=enrollment)
    if subject is not None:
        qs = qs.filter(subject=subject)

    total = qs.count()
    counts = {choice_value: 0 for choice_value, _ in Attendance.Status.choices}
    for status_value in qs.values_list("status", flat=True):
        counts[status_value] = counts.get(status_value, 0) + 1

    attended = sum(counts.get(s, 0) for s in ATTENDED_STATUSES)
    percent = round((attended / total) * 100, 2) if total else 0.0

    return {
        "enrollment": enrollment.id,
        "subject": subject.id if subject is not None else None,
        "total": total,
        "present": counts.get(Attendance.Status.PRESENT, 0),
        "absent": counts.get(Attendance.Status.ABSENT, 0),
        "late": counts.get(Attendance.Status.LATE, 0),
        "leave": counts.get(Attendance.Status.LEAVE, 0),
        "percent": percent,
    }


def generate_report_card_data(enrollment, exam_term):
    """
    On-demand aggregation of every `ResultEntry` for this enrollment
    within one `ExamTerm` — `ResultEntry` stays the single source of
    truth (design doc §7 — no separate `ReportCard` model/table).
    """
    entries = list(
        ResultEntry.objects.filter(enrollment=enrollment, exam_term=exam_term).select_related("subject")
    )
    totals = ResultEntry.objects.filter(enrollment=enrollment, exam_term=exam_term).aggregate(
        total_obtained=Sum("marks_obtained"), total_max=Sum("max_marks")
    )
    total_obtained = totals["total_obtained"] or 0
    total_max = totals["total_max"] or 0
    percentage = round(float(total_obtained) / float(total_max) * 100, 2) if total_max else 0.0

    return {
        "enrollment": enrollment.id,
        "exam_term": exam_term.id,
        "subjects": [
            {
                "subject": entry.subject_id,
                "subject_name": entry.subject.name,
                "marks_obtained": float(entry.marks_obtained),
                "max_marks": float(entry.max_marks),
                "remarks": entry.remarks,
            }
            for entry in entries
        ],
        "total_obtained": float(total_obtained),
        "total_max": float(total_max),
        "percentage": percentage,
    }


def compute_attendance_streak(enrollment):
    """
    F-3 — current unbroken streak of daily (subject-less, i.e.
    `subject=None`) PRESENT/LATE attendance marks for `enrollment`,
    walking backward from the most recently-marked day. Reuses the same
    `ATTENDED_STATUSES` definition `compute_attendance_summary()` above
    already establishes as the one place "attended" is defined — not
    re-derived here.

    Only the daily (subject=None) mark counts towards this streak, not
    subject-wise marks — matches `Attendance`'s own `unique_attendance_
    per_day_subject` constraint shape (one subject=None row per day is
    the "was the student in school today" record; subject-wise rows are
    a separate, per-period concept `compute_attendance_summary()` also
    reports on but that has no obvious single daily "streak" reading).

    A single ABSENT/LEAVE record ends the streak. Calendar days with NO
    record at all (weekends, holidays — nobody marked anything) are not
    treated as gaps, since this only walks actual marked records in
    date order — the streak is a count of consecutive ATTENDED *records*,
    not consecutive calendar dates.

    Returns `(streak_length, last_date)`. `last_date` is the date of the
    most recent record in the streak — the day the streak most recently
    extended to `streak_length` — which is exactly the value
    `tasks.check_attendance_streak_rewards()` needs to build an
    idempotency reference that can only ever repeat for a genuinely new
    milestone (a given streak length can only be reached on one real
    calendar date, ever). Returns `(0, None)` if there is no current
    streak (the most recent record, if any, isn't an attended status)
    or there are no attendance records for this enrollment at all.
    """
    streak = 0
    last_date = None
    records = Attendance.objects.filter(enrollment=enrollment, subject__isnull=True).order_by("-date")
    for record in records:
        if record.status not in ATTENDED_STATUSES:
            break
        streak += 1
        if last_date is None:
            last_date = record.date
    return streak, last_date


def compute_assignment_ontime_streak(student, section):
    """
    F-3 — current unbroken streak of on-time assignment submissions by
    `student` within `section`, walking backward from the most recently
    due campus assignment in that section.

    [Task 11 QUERY REDIRECT] Reads through `campus.bridge.
    get_assignment_submissions(section)` — the unified `assignment`
    app's data — never the deprecated `campus.AssignmentSubmission`
    model (which stopped receiving new rows once Task 11 landed; see
    that model's own `save()` guard in models.py). This is the same
    redirect `tasks.send_assignment_due_reminders()` already applied
    for its own query; before this pass, `compute_assignment_ontime_
    streak()` did not exist in this file at all, so this is a new
    function, not a fix to a pre-existing wrong-model read.

    ⚠️ FLAGGED, NOT GUESSED AT — two things below are assumed rather
    than confirmed, because `assignment/models.py` / `assignment/
    bridge.py` were not part of this pass's upload (only `campus/
    services.py`, `tasks.py`, `bridge.py`, `settings.py` were):

    1. "On-time" is read as `submission.status == "submitted"` — a bare
       string, not `AssignmentSubmission.SubmissionStatus.SUBMITTED` —
       specifically so this file does NOT import `assignment.models`
       directly, which `campus/bridge.py`'s own docstring states is
       the golden rule for `AssignmentSubmission` ("never a direct
       import ... outside this bridge module"). The value `"submitted"`
       itself is not independently confirmed against the real enum —
       it matches every other `TextChoices` in this codebase's own
       convention (member name lower-cased == value, e.g. `Attendance.
       Status.PRESENT = "present"`) and matches the deprecated
       `campus.AssignmentSubmission.Status.SUBMITTED = "submitted"`
       this model replaces, but has not been checked character-for-
       character against `assignment.models.AssignmentSubmission.
       SubmissionStatus` the way `bridge.py`'s own `NotifTypes` class
       states it checked its values. Please confirm against the real
       `assignment/models.py` — if the value differs, only the string
       literal below needs to change.
    2. `get_assignment_submissions()`'s return type isn't confirmed
       (queryset vs. plain list) — handled defensively below by
       filtering/sorting in plain Python instead of chaining
       `.filter()`/`.order_by()` onto it, so this works either way
       rather than risking a fresh `AttributeError` from guessing wrong
       about chainability (exactly the class of bug this task exists to
       eliminate).

    Returns `(streak_length, last_due_date)` — `last_due_date` is the
    `Assignment.due_date` of the most recent submission in the streak,
    used by `tasks.check_assignment_ontime_streak_rewards()` the same
    idempotency-reference way `compute_attendance_streak()`'s
    `last_date` is used. Returns `(0, None)` if there's no current
    streak (the most recent submission, if any, isn't on-time) or there
    are no submissions for this student in this section at all.
    """
    from .bridge import get_assignment_submissions

    submissions = [s for s in get_assignment_submissions(section) if s.student_id == student.id]
    submissions.sort(key=lambda s: s.assignment.due_date, reverse=True)

    streak = 0
    last_due_date = None
    for submission in submissions:
        if submission.status != "submitted":
            break
        streak += 1
        if last_due_date is None:
            last_due_date = submission.assignment.due_date
    return streak, last_due_date