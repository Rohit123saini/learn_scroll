# campus/dashboard.py
"""
Campus admin DASHBOARD — "aaj ka haal" in one call:

  * attendance % for today
  * fees collected vs pending
  * pending approvals (subject-teacher requests + staff leave applications)
  * active teachers / students
  * live classes running right now

Plain functions (no request/response objects) so they are unit-testable;
`campus/views.py` (`CampusViewSet.dashboard`) only checks the permission and
returns JSON. Read-only: nothing here writes.

Definitions (kept deliberately simple so the numbers are explainable):

* Scope = the campus's CURRENT academic session. A Principal/HOD that is
  limited to one department (`hod_department_id`) only sees that department's
  classes / staff — admin sees the whole campus.
* Attendance today: a student counts as *marked* if he/she has ANY attendance
  row dated today (daily or subject-wise, manual or auto from a live class) and
  as *attended* if any of today's rows is present / late (same "late counts as
  attended" rule as `services.ATTENDED_STATUSES`). percent = attended / marked.
  Students nobody has marked yet are reported separately (`not_marked`) and do
  NOT drag the percentage down. `percent` is None until someone is marked.
* Fees: invoices of the current session's active fee structures, waived
  invoices excluded. collected = successful payments; pending = what is still
  owed (per invoice, never negative, so one over-payment can't hide another
  student's dues); overdue = the part of pending whose due date has passed.
  Money is returned as strings ("5000.00") — same convention as the fee APIs.
* Active teachers = active staff whose role teaches (class / subject teacher,
  moderator). Active students = distinct students with an ACTIVE enrollment in
  the current session.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from .models import (
    Attendance,
    ClassMode,
    CampusLiveSession,
    FeeInvoice,
    FeePayment,
    Section,
    StaffLeave,
    StaffProfile,
    StudentEnrollment,
    SubjectTeacherassigments,
    TeacherAbsence,
)
from .panel import TEACHING_ROLES, current_session

LIVE_ITEMS_LIMIT = 20
ATTENDED = (Attendance.Status.PRESENT, Attendance.Status.LATE)
_MONEY = DecimalField(max_digits=14, decimal_places=2)
_ZERO = Value(Decimal("0"), output_field=_MONEY)


def _money(v):
    return f"{Decimal(v or 0).quantize(Decimal('0.01'))}"


def _day_bounds(today):
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, time.min), tz)
    return start, start + timedelta(days=1)


def _person_name(user):
    return (user.get_full_name() or "").strip() or user.username


def _scoped_sections(campus, session, department_id):
    if not session:
        return Section.objects.none()
    qs = Section.objects.filter(school_class__campus=campus, school_class__session=session)
    if department_id:
        qs = qs.filter(school_class__department_id=department_id)
    return qs


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------
def attendance_today(campus, session, sections, today):
    enrollments = StudentEnrollment.objects.filter(
        section__in=sections, session=session, status=StudentEnrollment.Status.ACTIVE
    ) if session else StudentEnrollment.objects.none()
    total_students = enrollments.count()
    agg = Attendance.objects.filter(enrollment__in=enrollments, date=today).aggregate(
        marked=Count("enrollment", distinct=True),
        attended=Count("enrollment", filter=Q(status__in=ATTENDED), distinct=True),
        sections_marked=Count("enrollment__section", distinct=True),
    )
    marked, attended = agg["marked"] or 0, agg["attended"] or 0
    return {
        "date": today.isoformat(),
        "percent": round(attended / marked * 100, 1) if marked else None,
        "total_students": total_students,
        "marked": marked,
        "attended": attended,
        "absent": marked - attended,
        "not_marked": max(total_students - marked, 0),
        "sections_total": sections.count(),
        "sections_marked": agg["sections_marked"] or 0,
        "alert_threshold_percent": campus.attendance_alert_threshold_percent,
    }


def fees_summary(campus, session, department_id, today):
    empty = {
        "total_due": "0.00", "collected": "0.00", "pending": "0.00", "overdue": "0.00",
        "collected_today": "0.00", "collected_percent": None,
        "invoices_total": 0, "invoices_unpaid": 0,
    }
    if not session:
        return empty
    invoices = FeeInvoice.objects.filter(
        fee_structure__campus=campus, fee_structure__session=session, fee_structure__is_active=True,
    ).exclude(status=FeeInvoice.Status.WAIVED)
    if department_id:
        invoices = invoices.filter(enrollment__section__school_class__department_id=department_id)

    paid_sq = (
        FeePayment.objects.filter(invoice=OuterRef("pk"), status=FeePayment.Status.SUCCESS)
        .order_by().values("invoice").annotate(t=Sum("amount")).values("t")
    )
    rows = invoices.annotate(paid=Coalesce(Subquery(paid_sq, output_field=_MONEY), _ZERO)).annotate(
        owed=Greatest(ExpressionWrapper(F("amount_due") - F("paid"), output_field=_MONEY), _ZERO)
    )
    agg = rows.aggregate(
        total_due=Coalesce(Sum("amount_due"), _ZERO),
        collected=Coalesce(Sum("paid"), _ZERO),
        pending=Coalesce(Sum("owed"), _ZERO),
        overdue=Coalesce(Sum("owed", filter=Q(fee_structure__due_date__lt=today)), _ZERO),
        invoices_total=Count("id"),
        invoices_unpaid=Count("id", filter=Q(owed__gt=0)),
    )
    start, end = _day_bounds(today)
    # `paid_at` is the settle time (cash/wallet rows stamp it in mark_success);
    # fall back to created_at for older rows that never got one.
    collected_today = FeePayment.objects.filter(
        invoice__in=invoices, status=FeePayment.Status.SUCCESS,
    ).filter(
        Q(paid_at__gte=start, paid_at__lt=end) | Q(paid_at__isnull=True, created_at__gte=start, created_at__lt=end)
    ).aggregate(t=Coalesce(Sum("amount"), _ZERO))["t"]

    due = agg["total_due"]
    return {
        "total_due": _money(due),
        "collected": _money(agg["collected"]),
        "pending": _money(agg["pending"]),
        "overdue": _money(agg["overdue"]),
        "collected_today": _money(collected_today),
        "collected_percent": round(float(agg["collected"]) / float(due) * 100, 1) if due else None,
        "invoices_total": agg["invoices_total"],
        "invoices_unpaid": agg["invoices_unpaid"],
    }


def pending_approvals(campus, sections, department_id):
    subject_requests = SubjectTeacherassigments.objects.filter(
        section__in=sections, status=SubjectTeacherassigments.Status.PENDING
    ).count()
    leaves = StaffLeave.objects.filter(campus=campus, status=StaffLeave.Status.PENDING)
    if department_id:
        leaves = leaves.filter(staff__department_id=department_id)
    staff_leaves = leaves.count()
    return {
        "total": subject_requests + staff_leaves,
        "subject_requests": subject_requests,
        "staff_leaves": staff_leaves,
    }


def people(campus, session, sections, department_id, today):
    staff = StaffProfile.objects.filter(campus=campus, is_active=True, role__in=TEACHING_ROLES)
    if department_id:
        staff = staff.filter(department_id=department_id)
    teachers = staff.count()
    on_leave = TeacherAbsence.objects.filter(
        staff__in=staff, is_active=True, date_from__lte=today, date_to__gte=today
    ).values("staff").distinct().count()
    students = (
        StudentEnrollment.objects.filter(
            section__in=sections, session=session, status=StudentEnrollment.Status.ACTIVE
        ).values("student").distinct().count()
        if session else 0
    )
    return {
        "active_teachers": teachers,
        "teachers_on_leave_today": on_leave,
        "active_students": students,
    }


def live_classes(sections, now):
    base = CampusLiveSession.objects.filter(section__in=sections, mode=ClassMode.ONLINE)
    live = (
        base.filter(status=CampusLiveSession.Status.LIVE)
        .select_related("subject", "section__school_class", "teacher__user")
        .annotate(
            in_room=Count(
                "participants", filter=Q(participants__left_at__isnull=True) & ~Q(participants__user=F("teacher__user")),
                distinct=True,
            )
        )
        .order_by("started_at", "scheduled_at")
    )
    live_count = live.count()
    items = [
        {
            "id": str(s.id),
            "subject": s.subject.name,
            "section": f"{s.section.school_class.name} {s.section.name}".strip(),
            "teacher": _person_name(s.teacher.user),
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "students_in_room": s.in_room,
        }
        for s in live[:LIVE_ITEMS_LIMIT]
    ]
    _, day_end = _day_bounds(timezone.localdate(now))
    later = base.filter(
        status=CampusLiveSession.Status.SCHEDULED, scheduled_at__gte=now, scheduled_at__lt=day_end
    ).count()
    return {"live_now": live_count, "scheduled_later_today": later, "items": items}


# ---------------------------------------------------------------------------
def dashboard(campus, *, department_id=None, now=None):
    now = now or timezone.now()
    today = timezone.localdate(now)
    session = current_session(campus)
    sections = _scoped_sections(campus, session, department_id)
    return {
        "campus": str(campus.id),
        "session": {"id": str(session.id), "name": session.name} if session else None,
        "scope_department": str(department_id) if department_id else None,
        "generated_at": now.isoformat(),
        "attendance": attendance_today(campus, session, sections, today),
        "fees": fees_summary(campus, session, department_id, today),
        "approvals": pending_approvals(campus, sections, department_id),
        "people": people(campus, session, sections, department_id, today),
        "live": live_classes(sections, now),
    }
