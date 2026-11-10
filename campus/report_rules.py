# campus/report_rules.py
"""
REPORTS / EXPORT — the pure rules (no Django, no ORM): the catalog of reports, who may export
what, date-range limits, percentage and ranking maths and the download file name.

Run the tests without Django:   python -m unittest campus.tests_report_rules   (from backend/backend)
"""
from __future__ import annotations

from datetime import date, timedelta

# ---- the catalog ----------------------------------------------------------------------------------
ATTENDANCE_SUMMARY = "attendance_summary"   # one row per student: present / absent / late / leave / %
ATTENDANCE_DAILY = "attendance_daily"       # one row per marked day (the register itself)
MARKS_SHEET = "marks_sheet"                 # one row per student, a column per subject, total / % / grade / rank
MARKS_DETAIL = "marks_detail"               # one row per student per subject
FEES_COLLECTION = "fees_collection"
FEES_DUES = "fees_dues"
FEES_CONCESSIONS = "fees_concessions"
FEES_DAYBOOK = "fees_daybook"

ATTENDANCE_REPORTS = (ATTENDANCE_SUMMARY, ATTENDANCE_DAILY)
MARKS_REPORTS = (MARKS_SHEET, MARKS_DETAIL)
FEES_REPORTS = (FEES_COLLECTION, FEES_DUES, FEES_CONCESSIONS, FEES_DAYBOOK)
REPORTS = ATTENDANCE_REPORTS + MARKS_REPORTS + FEES_REPORTS

# `finance.report_*` name for each fees report
FEES_FINANCE_NAME = {
    FEES_COLLECTION: "collection",
    FEES_DUES: "dues",
    FEES_CONCESSIONS: "concessions",
    FEES_DAYBOOK: "daybook",
}

MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30

TITLES = {
    ATTENDANCE_SUMMARY: "Attendance summary",
    ATTENDANCE_DAILY: "Attendance register",
    MARKS_SHEET: "Marks sheet",
    MARKS_DETAIL: "Marks (subject-wise)",
    FEES_COLLECTION: "Fee collection",
    FEES_DUES: "Fee dues",
    FEES_CONCESSIONS: "Fee concessions",
    FEES_DAYBOOK: "Fee day book",
}


class ReportRuleError(Exception):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def group_of(report: str) -> str:
    if report in ATTENDANCE_REPORTS:
        return "attendance"
    if report in MARKS_REPORTS:
        return "marks"
    if report in FEES_REPORTS:
        return "fees"
    raise ReportRuleError("report", f"Choose one of: {', '.join(REPORTS)}.")


def title_of(report: str) -> str:
    return TITLES.get(report, report.replace("_", " ").title())


# ---- who may export what ------------------------------------------------------------------------------
def can_export_group(role: str, group: str) -> bool:
    """`role` = the StaffProfile role value.
    attendance / marks: admin, principal-HOD, class teacher (own sections only — the view narrows it).
    fees: admin, principal-HOD, non-teaching accounts clerk (== `fee.view`)."""
    if group in ("attendance", "marks"):
        return role in ("admin", "principal_hod", "class_teacher")
    if group == "fees":
        return role in ("admin", "principal_hod", "non_teaching")
    return False


def sees_whole_campus(role: str) -> bool:
    return role in ("admin", "principal_hod")


# ---- dates --------------------------------------------------------------------------------------------
def resolve_range(date_from: date | None, date_to: date | None, today: date) -> tuple[date, date]:
    """Default = the last 30 days. Raises ReportRuleError for a reversed or > 366 day range."""
    end = date_to or today
    start = date_from or (end - timedelta(days=DEFAULT_RANGE_DAYS))
    if start > end:
        raise ReportRuleError("from", "Start date is after the end date.")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ReportRuleError("to", f"Choose a range of at most {MAX_RANGE_DAYS} days.")
    return start, end


# ---- maths --------------------------------------------------------------------------------------------
def percent(part, whole) -> float | None:
    """`None` when there is nothing to divide by (an empty report cell, not a fake 0 %)."""
    try:
        part, whole = float(part), float(whole)
    except (TypeError, ValueError):
        return None
    if whole <= 0:
        return None
    return round(part * 100.0 / whole, 2)


def rank_by_score(scores: dict) -> dict:
    """{key: score} -> {key: rank}. Competition ranking: equal scores share a rank and the next
    rank skips (90, 90, 80 -> 1, 1, 3). `None` scores get no rank."""
    ranked = sorted((k for k, v in scores.items() if v is not None), key=lambda k: scores[k], reverse=True)
    out, last_score, last_rank = {}, None, 0
    for pos, key in enumerate(ranked, start=1):
        if scores[key] != last_score:
            last_rank, last_score = pos, scores[key]
        out[key] = last_rank
    return out


# ---- file name ----------------------------------------------------------------------------------------
def file_name(report: str, fmt: str, today: date) -> str:
    return f"{report}_{today:%Y%m%d}.{fmt}"
