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