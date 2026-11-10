# campus/reports.py
"""
REPORTS / EXPORT — data for the attendance and marks exports. Every function returns
`(header, rows)` (rows may be a generator) — the same shape as `finance.report_*` — so
`reports_export.render()` can write any of them as CSV / Excel / PDF.

Read-only. The caller (`report_views.ReportExportView`) has already decided WHICH sections the
user may see; nothing here checks permissions.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q

from . import grading, report_rules as rules
from .models import Attendance, ResultEntry, StudentEnrollment
from .finance import student_name

_ATT = Attendance.Status


def _num(value):
    """Decimal marks -> int when whole (85.00 -> 85), else float."""
    if value is None:
        return ""
    f = float(value)
    return int(f) if f == int(f) else round(f, 2)


def _enrollments(sections, session):
    return (StudentEnrollment.objects.filter(section__in=sections, session=session)
            .select_related("student", "section__school_class")
            .order_by("section__school_class__name", "section__name", "roll_number", "student__first_name"))


# ------------------------------------------------------------------ attendance
def attendance_summary(sections, session, date_from, date_to, *, subject_id=None):
    """One row per student: marked / present / absent / late / leave / attendance %.
    "Marked" counts every attendance row in the range (daily marks AND subject-wise marks, like
    `services.compute_attendance_summary`); pass `subject_id` to look at one subject only.
    Attended = present + late. A student with nothing marked gets an empty % (not 0 %)."""
    header = ["Student", "Roll No", "Class", "Section", "Marked", "Present", "Absent", "Late", "Leave",
              "Attendance %"]
    in_range = Q(attendance_records__date__gte=date_from, attendance_records__date__lte=date_to)
    if subject_id:
        in_range &= Q(attendance_records__subject_id=subject_id)

    qs = _enrollments(sections, session).annotate(
        n_total=Count("attendance_records", filter=in_range),
        n_present=Count("attendance_records", filter=in_range & Q(attendance_records__status=_ATT.PRESENT)),
        n_absent=Count("attendance_records", filter=in_range & Q(attendance_records__status=_ATT.ABSENT)),
        n_late=Count("attendance_records", filter=in_range & Q(attendance_records__status=_ATT.LATE)),
        n_leave=Count("attendance_records", filter=in_range & Q(attendance_records__status=_ATT.LEAVE)),
    )

    def rows():
        for en in qs.iterator(chunk_size=500):
            # students who left the section and have nothing in this range are noise
            if en.status != StudentEnrollment.Status.ACTIVE and not en.n_total:
                continue
            pct = rules.percent(en.n_present + en.n_late, en.n_total)
            yield [student_name(en.student), en.roll_number, en.section.school_class.name, en.section.name,
                   en.n_total, en.n_present, en.n_absent, en.n_late, en.n_leave,
                   "" if pct is None else pct]
    return header, rows()


def attendance_daily(sections, session, date_from, date_to, *, subject_id=None):
    """The register itself: one row per marked student per day (and subject)."""
    header = ["Date", "Student", "Roll No", "Class", "Section", "Subject", "Status", "Source", "Marked by"]
    qs = (Attendance.objects.filter(enrollment__section__in=sections, enrollment__session=session,
                                    date__gte=date_from, date__lte=date_to)
          .select_related("enrollment__student", "enrollment__section__school_class", "subject", "marked_by")
          .order_by("date", "enrollment__section__school_class__name", "enrollment__section__name",
                    "enrollment__roll_number"))
    if subject_id:
        qs = qs.filter(subject_id=subject_id)

    def rows():
        for a in qs.iterator(chunk_size=500):
            en = a.enrollment
            yield [a.date, student_name(en.student), en.roll_number, en.section.school_class.name,
                   en.section.name, a.subject.name if a.subject_id else "Daily", a.get_status_display(),
                   a.get_source_display(), student_name(a.marked_by)]
    return header, rows()


# ------------------------------------------------------------------ marks
def _results_qs(sections, exam_term, include_drafts):
    qs = (ResultEntry.objects.filter(enrollment__section__in=sections, exam_term=exam_term)
          .select_related("enrollment__student", "enrollment__section__school_class", "subject"))
    if not include_drafts:
        qs = qs.filter(status=ResultEntry.Status.PUBLISHED)
    return qs


def marks_detail(sections, exam_term, campus, *, include_drafts=False, subject_id=None):
    """One row per student per subject, with % / grade / pass-fail from the campus grade scale."""
    header = ["Student", "Roll No", "Class", "Section", "Subject", "Marks", "Max", "Percent", "Grade",
              "Result", "Status", "Remarks"]
    qs = _results_qs(sections, exam_term, include_drafts)
    if subject_id:
        qs = qs.filter(subject_id=subject_id)
    qs = qs.order_by("enrollment__section__school_class__name", "enrollment__section__name",
                     "enrollment__roll_number", "subject__name")
    scale, pass_pct = grading.campus_scale(campus), campus.pass_percent

    def rows():
        for r in qs.iterator(chunk_size=500):
            en = r.enrollment
            pct = grading.percent_of(r.marks_obtained, r.max_marks)
            yield [student_name(en.student), en.roll_number, en.section.school_class.name, en.section.name,
                   r.subject.name, _num(r.marks_obtained), _num(r.max_marks),
                   "" if pct is None else round(float(pct), 2),
                   grading.grade_for_percent(pct, scale) or "",
                   ("Pass" if grading.is_pass(pct, pass_pct) else "Fail") if pct is not None else "",
                   r.get_status_display(), r.remarks]
    return header, rows()


def marks_sheet(sections, exam_term, campus, *, include_drafts=False):
    """Class-sheet layout: one row per student, a column per subject, then Total / Max / % / Grade /
    Result / Rank (rank is inside the student's own section). A subject the student has no marks
    for is left blank and does not count towards their total."""
    results = list(_results_qs(sections, exam_term, include_drafts))
    subjects = sorted({r.subject.name for r in results}, key=str.lower)
    header = ["Student", "Roll No", "Class", "Section", *subjects, "Total", "Max", "Percent", "Grade",
              "Result", "Rank"]

    by_en = defaultdict(dict)
    for r in results:
        by_en[r.enrollment_id][r.subject.name] = r
    scale, pass_pct = grading.campus_scale(campus), campus.pass_percent

    lines, scores = [], defaultdict(dict)       # scores[section_id][enrollment_id] = percent
    for en in _enrollments(sections, exam_term.session):
        got = by_en.get(en.id)
        if not got:
            continue
        total = sum((r.marks_obtained for r in got.values()), Decimal(0))
        top = sum((r.max_marks for r in got.values()), Decimal(0))
        pct = grading.percent_of(total, top)
        scores[en.section_id][en.id] = None if pct is None else round(float(pct), 2)
        lines.append((en, got, total, top, pct))

    ranks = {sid: rules.rank_by_score(s) for sid, s in scores.items()}

    def rows():
        for en, got, total, top, pct in lines:
            yield [student_name(en.student), en.roll_number, en.section.school_class.name, en.section.name,
                   *[_num(got[s].marks_obtained) if s in got else "" for s in subjects],
                   _num(total), _num(top), "" if pct is None else round(float(pct), 2),
                   grading.grade_for_percent(pct, scale) or "",
                   ("Pass" if grading.is_pass(pct, pass_pct) else "Fail") if pct is not None else "",
                   ranks.get(en.section_id, {}).get(en.id, "")]
    return header, rows()
