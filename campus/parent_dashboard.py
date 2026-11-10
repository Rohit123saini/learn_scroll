# campus/parent_dashboard.py
"""
Parent home dashboard — ONE call that answers "how are all my children doing
today?" so a parent does not have to open each child separately.

GET /campus/parent-dashboard/            (all my linked children, all campuses)
GET /campus/parent-dashboard/?campus=ID  (only that campus)

Per child: today's attendance (worst status across today's registers, or
"not_marked"), this month's attendance %, fees outstanding (+ overdue count),
the next booked PTM, and the number of unread parent-chat messages.

Strictly READ-ONLY and strictly the caller's own links: only
``CampusParentLink`` rows whose ``parent`` is the caller are used — a user can
never see a child they are not linked to. Loginless Parent Mode "shadow"
parents are not users who can call this API, so nothing special is needed.
Bulk queries only (no per-child query loop for attendance / fees / PTM).
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    Attendance,
    CampusParentLink,
    FeeInvoice,
    FeePayment,
    PTMBooking,
    StudentEnrollment,
)

# Severity order for "today's status": any absent wins, then late, leave, present.
_SEVERITY = {
    Attendance.Status.ABSENT: 4,
    Attendance.Status.LATE: 3,
    Attendance.Status.LEAVE: 2,
    Attendance.Status.PRESENT: 1,
}


def _name(user) -> str:
    full = getattr(user, "get_full_name", lambda: "")() or ""
    return full.strip() or user.username


def _money(value) -> str:
    return str(Decimal(value or 0).quantize(Decimal("0.01")))


def build_dashboard(user, campus_id=None) -> dict:
    today = timezone.localdate()
    month_start = today.replace(day=1)

    links = CampusParentLink.objects.filter(parent=user).select_related("student", "campus")
    if campus_id:
        links = links.filter(campus_id=campus_id)
    links = list(links.order_by("student__first_name", "student__username", "campus_id"))
    if not links:
        return {"date": today.isoformat(), "children": [], "totals": _totals([])}

    student_ids = {l.student_id for l in links}
    campus_ids = {l.campus_id for l in links}

    # Active enrollment per (campus, student) — the newest one wins.
    enrollments = {}
    for e in (
        StudentEnrollment.objects.filter(
            student_id__in=student_ids,
            section__school_class__campus_id__in=campus_ids,
            status=StudentEnrollment.Status.ACTIVE,
        )
        .select_related("section__school_class")
        .order_by("-session__start_date")  # newest session first -> setdefault keeps it
    ):
        enrollments.setdefault((e.section.school_class.campus_id, e.student_id), e)
    enrollment_ids = [e.pk for e in enrollments.values()]

    # ---- attendance: today's rows + month-to-date counts (2 queries)
    today_status: dict[int, str] = {}
    for enr_id, status in Attendance.objects.filter(
        enrollment_id__in=enrollment_ids, date=today
    ).values_list("enrollment_id", "status"):
        cur = today_status.get(enr_id)
        if cur is None or _SEVERITY.get(status, 0) > _SEVERITY.get(cur, 0):
            today_status[enr_id] = status

    month_counts: dict[int, dict[str, int]] = defaultdict(dict)
    for row in (
        Attendance.objects.filter(
            enrollment_id__in=enrollment_ids, date__gte=month_start, date__lte=today
        )
        .values("enrollment_id", "status")
        .annotate(n=Count("id"))
    ):
        month_counts[row["enrollment_id"]][row["status"]] = row["n"]

    # ---- fees: unpaid-ish invoices with their paid sum (1 query)
    fee_rows: dict[int, dict] = defaultdict(lambda: {"due": Decimal("0"), "overdue": 0, "open": 0})
    open_statuses = [
        FeeInvoice.Status.PENDING,
        FeeInvoice.Status.PARTIAL,
        FeeInvoice.Status.OVERDUE,
    ]
    invoices = (
        FeeInvoice.objects.filter(enrollment_id__in=enrollment_ids, status__in=open_statuses)
        .annotate(
            paid=Coalesce(
                Sum("payments__amount", filter=Q(payments__status=FeePayment.Status.SUCCESS)),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            )
        )
        .values("enrollment_id", "amount_due", "status", "paid")
    )
    for inv in invoices:
        balance = Decimal(inv["amount_due"]) - Decimal(inv["paid"])
        if balance <= 0:
            continue
        row = fee_rows[inv["enrollment_id"]]
        row["due"] += balance
        row["open"] += 1
        if inv["status"] == FeeInvoice.Status.OVERDUE:
            row["overdue"] += 1

    # ---- next booked PTM per child (1 query)
    next_ptm: dict[tuple, PTMBooking] = {}
    for b in (
        PTMBooking.objects.filter(
            parent=user,
            student_id__in=student_ids,
            status=PTMBooking.Status.BOOKED,
            event__date__gte=today,
        )
        .select_related("event", "slot", "staff__user")
        .order_by("event__date", "slot__start_time")
    ):
        next_ptm.setdefault((b.campus_id, b.student_id), b)

    # ---- unread chat messages per child (1 query, same rules as the chat badge)
    unread_by_child: dict[tuple, int] = defaultdict(int)
    try:
        from .models import ParentChatThread
        from .parent_chat import annotate_unread

        open_threads = ParentChatThread.objects.filter(
            parent=user, student_id__in=student_ids, status=ParentChatThread.Status.OPEN
        )
        for t in annotate_unread(open_threads, "parent"):
            unread_by_child[(t.campus_id, t.student_id)] += t.unread
    except Exception:  # unread is a nicety; never fail the dashboard for it
        unread_by_child = defaultdict(int)

    children = []
    for link in links:
        enr = enrollments.get((link.campus_id, link.student_id))
        enr_id = enr.pk if enr else None
        counts = month_counts.get(enr_id, {}) if enr_id else {}
        present = counts.get("present", 0) + counts.get("late", 0)
        absent = counts.get("absent", 0)
        marked = present + absent
        pct = round(present * 100 / marked) if marked else None

        fee = fee_rows.get(enr_id) if enr_id else None
        booking = next_ptm.get((link.campus_id, link.student_id))

        children.append({
            "link_id": str(link.pk),
            "campus_id": str(link.campus_id),
            "campus_name": link.campus.name,
            "student_id": str(link.student_id),
            "student_name": _name(link.student),
            "section": (f"{enr.section.school_class.name} {enr.section.name}".strip() if enr else ""),
            "attendance_today": today_status.get(enr_id, "not_marked") if enr_id else "not_marked",
            "attendance_month_percent": pct,
            "attendance_month_days": marked,
            "fees_due": _money(fee["due"]) if fee else "0.00",
            "fees_open_invoices": fee["open"] if fee else 0,
            "fees_overdue_invoices": fee["overdue"] if fee else 0,
            "next_ptm": None if booking is None else {
                "booking_id": str(booking.pk),
                "event_id": str(booking.event_id),
                "title": booking.event.title,
                "date": booking.event.date.isoformat(),
                "start_time": booking.slot.start_time.strftime("%H:%M"),
                "teacher_name": _name(booking.staff.user) if getattr(booking.staff, "user", None) else "",
            },
            "unread_chat": unread_by_child.get((link.campus_id, link.student_id), 0),
        })

    return {"date": today.isoformat(), "children": children, "totals": _totals(children)}


def _totals(children: list[dict]) -> dict:
    return {
        "children": len(children),
        "absent_today": sum(1 for c in children if c["attendance_today"] == "absent"),
        "not_marked_today": sum(1 for c in children if c["attendance_today"] == "not_marked"),
        "fees_due": _money(sum((Decimal(c["fees_due"]) for c in children), Decimal("0"))),
        "overdue_invoices": sum(c["fees_overdue_invoices"] for c in children),
    }


class ParentDashboardView(APIView):
    """GET /campus/parent-dashboard/ — see module docstring."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        campus_id = request.query_params.get("campus") or None
        if campus_id is not None:
            try:
                campus_id = uuid.UUID(str(campus_id))
            except ValueError:
                return Response({"detail": "invalid_campus"}, status=400)
        return Response(build_dashboard(request.user, campus_id))
