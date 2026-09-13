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

F-3 (this pass) — `compute_attendance_streak()` / `compute_assigments_
ontime_streak()` added below. Neither existed in this file before this
pass; `tasks.check_attendance_streak_rewards()` /
`check_assigments_ontime_streak_rewards()` were already calling
`from .services import compute_attendance_streak` /
`compute_assigments_ontime_streak` — i.e. this module could not have
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


def compute_assigments_ontime_streak(student, section):
    """
    F-3 — current unbroken streak of on-time assigments submissions by
    `student` within `section`, walking backward from the most recently
    due campus assigments in that section.

    [Task 11 QUERY REDIRECT] Reads through `campus.bridge.
    get_assigments_submissions(section)` — the unified `assigments`
    app's data — never the deprecated `campus.assigmentsSubmission`
    model (which stopped receiving new rows once Task 11 landed; see
    that model's own `save()` guard in models.py). This is the same
    redirect `tasks.send_assigments_due_reminders()` already applied
    for its own query; before this pass, `compute_assigments_ontime_
    streak()` did not exist in this file at all, so this is a new
    function, not a fix to a pre-existing wrong-model read.

    ✅ CONFIRMED against the real `assigments/models.py` (this pass) —
    this REPLACES a wrong first draft that checked
    `submission.status == "submitted"`. That check is wrong for two
    confirmed reasons, not just an unconfirmed guess:
      1. `status` does not stay `"submitted"` — once a free-form
         submission is graded, `grade_freeform()` moves it to `CHECKED`
         regardless of whether it was on-time or late, so an on-time
         but already-graded submission would wrongly break the streak.
      2. The structured path (`submit_structured()`) NEVER sets status
         to `SUBMITTED`/`LATE` at all — it goes straight to
         `PARTIALLY_CHECKED` or `CHECKED` (see `_recompute_structured_
         status()`). Every structured submission would ALWAYS wrongly
         break the streak under the old check.
    The model's own `is_late()` method is the actual source of truth —
    it recomputes from `submitted_at` vs. `assigments.due_date` fresh
    every time, independent of workflow status, so it works identically
    for both the free-form and structured paths. "On-time" here is
    therefore `submitted_at is not None and not is_late()` — the
    `submitted_at` check is needed because `is_late()` also returns
    `False` for a never-submitted (`MISSING`) row, which is not on-time.

    ✅ CONFIRMED (this pass, `assigments/bridge.py` now provided):
    `get_submissions_for_context()` — and therefore `campus.bridge.
    get_assigments_submissions()`, which calls straight through to it —
    returns a real Django queryset (`assigmentsSubmission.objects.
    filter(...).select_related("assigments", "student")`), not a plain
    list. This REPLACES a more defensive first draft that filtered/
    sorted in plain Python specifically to avoid depending on that being
    true. Switching to DB-level `.filter()`/`.order_by()` isn't just
    tidier now that it's confirmed — it also sidesteps a real crash the
    Python-side version had: `assigments.due_date` is nullable
    (`null=True, blank=True`, confirmed in `assigments/models.py`), and
    `sorted(..., key=lambda s: s.assigments.due_date)` raises `TypeError`
    the moment it has to compare a real `date` against `None`. Ordering
    at the DB level instead never hits that — SQL handles NULL in
    `ORDER BY` without raising (Postgres sorts them as the "largest"
    value in a `DESC` order, i.e. first), so a null-due-date submission
    can appear in the streak walk without crashing this function, even
    though whether it *should* count is arguably itself a design
    question the callers of `create_context_assigments()` — not this
    function — would need to actually resolve (should a campus
    assigments ever have no due_date at all?).

    Returns `(streak_length, last_due_date, last_submission_id)` —
    `last_due_date` is the `assigments.due_date` of the most recent
    submission in the streak, used by `tasks.
    check_assigments_ontime_streak_rewards()` the same idempotency-
    reference way `compute_attendance_streak()`'s `last_date` is used.
    `last_submission_id` is that same submission's own `id` — see the
    `due_date=None` note below for why the caller needs it too. Returns
    `(0, None, None)` if there's no current streak (the most recent
    submission, if any, isn't on-time) or there are no submissions for
    this student in this section at all.

    ✅ RESOLVED (this pass) — `due_date=None` no longer crashes the
    caller. If the most-recent submission counted into the streak
    belongs to an assigments with `due_date=None` (allowed by the
    model), `last_due_date` comes back `None` alongside a real
    `streak > 0`, exactly as before — but now `last_submission_id` is
    always populated whenever `streak > 0`, `due_date` or not, because
    it's read straight off the submission row itself rather than the
    (possibly-null) assigments field. `tasks.
    check_assigments_ontime_streak_rewards()` builds its idempotency
    reference from `last_due_date` when present and falls back to
    `last_submission_id` when it isn't, instead of calling
    `.isoformat()` on `None`. A submission row's `id` is permanently
    fixed to that one real submission, so the same "can only happen
    once, ever" idempotency argument `last_due_date` relies on holds
    for `last_submission_id` too: the streak can only re-reach the same
    length off a *different* submission later (the old one can't
    un-submit and re-submit into the same streak slot), so the
    `(enrollment id, streak length, submission id)` triple can't repeat
    for two genuinely different milestones.

    This does NOT resolve the separate product-rule question flagged
    before — should a due-date-less campus assigments count towards an
    "on-time" streak at all — that's still a real call for whoever owns
    that rule, not something fixed here. This pass only makes the
    *crash* on that case go away regardless of which way that question
    is eventually answered; if the answer turns out to be "no, exclude
    them", that's a filter added to the `submissions` query below, not
    a change to this return shape.
    """
    from .bridge import get_assigments_submissions

    submissions = get_assigments_submissions(section).filter(student_id=student.id).order_by("-assigments__due_date")

    streak = 0
    last_due_date = None
    last_submission_id = None
    for submission in submissions:
        on_time = submission.submitted_at is not None and not submission.is_late()
        if not on_time:
            break
        streak += 1
        if last_submission_id is None:
            last_due_date = submission.assigments.due_date
            last_submission_id = submission.id
    return streak, last_due_date, last_submission_id