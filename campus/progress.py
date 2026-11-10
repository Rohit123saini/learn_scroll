# campus/progress.py
"""
Read models for the student's "My progress" screen — computed on demand from
`ResultEntry` / `Attendance` (single source of truth, nothing stored), exactly
like `services.generate_report_card_data` / `compute_attendance_summary`.

  marks_progress(enrollment, published_only)   exam-term-by-exam-term trend
  attendance_breakdown(enrollment)             overall + per subject + per month
"""
from __future__ import annotations

from collections import OrderedDict

from . import grading
from .models import Attendance, ResultEntry
from .services import ATTENDED_STATUSES, compute_attendance_summary

MONTHS_SHOWN = 6


def marks_progress(enrollment, *, published_only: bool) -> dict:
    campus = enrollment.section.school_class.campus
    scale, pass_percent = grading.campus_scale(campus), campus.pass_percent
    qs = ResultEntry.objects.filter(enrollment=enrollment, exam_term__session=enrollment.session).select_related(
        "subject", "exam_term"
    )
    if published_only:
        qs = qs.filter(status=ResultEntry.Status.PUBLISHED)

    by_term: "OrderedDict[str, dict]" = OrderedDict()
    subjects: dict = {}
    for entry in qs.order_by("exam_term__start_date", "exam_term__name", "subject__name"):
        term = entry.exam_term
        bucket = by_term.setdefault(
            str(term.id),
            {"exam_term": term.id, "name": term.name, "start_date": term.start_date.isoformat(), "rows": []},
        )
        bucket["rows"].append(entry)
        subjects[str(entry.subject_id)] = entry.subject.name

    terms = []
    for bucket in by_term.values():
        rows = bucket.pop("rows")
        obtained = sum((float(r.marks_obtained) for r in rows), 0.0)
        maximum = sum((float(r.max_marks) for r in rows), 0.0)
        pct = round(obtained / maximum * 100, 2) if maximum else 0.0
        sub_rows = []
        for r in rows:
            p = grading.percent_of(r.marks_obtained, r.max_marks)
            sub_rows.append({
                "subject": r.subject_id,
                "subject_name": r.subject.name,
                "percentage": round(float(p), 2) if p is not None else 0.0,
                "grade": grading.grade_for_percent(p, scale),
            })
        terms.append({
            **bucket,
            "gpa": grading.gpa_of(
                [grading.percent_of(r.marks_obtained, r.max_marks) for r in rows], scale
            ) if grading.uses_points(campus) else None,
            "percentage": pct,
            "grade": grading.grade_for_percent(pct, scale) if maximum else None,
            "passed": all(grading.is_pass(grading.percent_of(r.marks_obtained, r.max_marks), pass_percent) for r in rows),
            "total_obtained": obtained,
            "total_max": maximum,
            "subjects": sub_rows,
        })
    return {
        "enrollment": enrollment.id,
        "pass_percent": float(pass_percent),
        "terms": terms,
        "subjects": [{"id": k, "name": v} for k, v in sorted(subjects.items(), key=lambda kv: kv[1])],
    }


def _pct(attended: int, total: int) -> float:
    return round(attended / total * 100, 2) if total else 0.0


def attendance_breakdown(enrollment) -> dict:
    campus = enrollment.section.school_class.campus
    overall = compute_attendance_summary(enrollment)

    per_subject: dict = {}
    per_month: dict = {}
    rows = Attendance.objects.filter(enrollment=enrollment).select_related("subject").values_list(
        "date", "status", "subject_id", "subject__name"
    )
    for day, st, subject_id, subject_name in rows:
        attended = st in ATTENDED_STATUSES
        month = per_month.setdefault(f"{day.year:04d}-{day.month:02d}", [0, 0])
        month[1] += 1
        month[0] += int(attended)
        if subject_id is not None:
            sub = per_subject.setdefault(str(subject_id), {"subject": subject_id, "name": subject_name, "attended": 0, "total": 0})
            sub["total"] += 1
            sub["attended"] += int(attended)

    months = [
        {"month": m, "attended": a, "total": t, "percent": _pct(a, t)}
        for m, (a, t) in sorted(per_month.items())
    ][-MONTHS_SHOWN:]
    subjects = sorted(
        (
            {"subject": v["subject"], "name": v["name"], "attended": v["attended"], "total": v["total"],
             "percent": _pct(v["attended"], v["total"])}
            for v in per_subject.values()
        ),
        key=lambda v: (v["percent"], v["name"]),
    )
    return {
        **overall,
        "threshold": campus.attendance_alert_threshold_percent,
        "subjects": subjects,
        "months": months,
    }
