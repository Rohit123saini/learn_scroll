# campus/report_views.py
"""
REPORTS / EXPORT — HTTP layer. One endpoint for every export:

  GET /campus/reports/catalog/?campus=
        -> what THIS user may export + which formats the server can write
  GET /campus/reports/export/?campus=&report=&format=csv|xlsx|pdf [&session=&class=&section=&subject=
        &from=&to=&exam_term=&include_drafts=1 &mode=&status=&overdue_only=1]

  report = attendance_summary | attendance_daily | marks_sheet | marks_detail
         | fees_collection | fees_dues | fees_concessions | fees_daybook

Access (rules in `report_rules.py`)
  attendance / marks : admin, principal-HOD (a department-limited HOD: that department only),
                       class teacher (their own section(s) only)
  fees               : `fee.view` (admin, principal, accounts clerk) + the fee module switched on
Everything is audit-logged; downloads are `Cache-Control: no-store`.
"""
from __future__ import annotations

import importlib.util
import logging

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import finance, report_rules as rules, reports, reports_export as rx
from .audit import log_action
from .bulk_ops import _is_uuid
from .finance_views import finance_campus_ids, get_campus, get_session, parse_day
from .models import ClassTeacherassigments, ExamTerm, Section, StaffProfile, Subject
from .permissions import get_staff_profile
from .throttles import CampusReportExportThrottle

logger = logging.getLogger(__name__)


def _uuid(q, name):
    raw = q.get(name)
    if not raw:
        return None
    if not _is_uuid(raw):
        raise ValidationError({name: "Invalid id."})
    return raw


def _formats_available() -> dict:
    return {
        "csv": True,
        "xlsx": importlib.util.find_spec("openpyxl") is not None,
        "pdf": importlib.util.find_spec("reportlab") is not None,
    }


def _staff_role(user, campus):
    profile = get_staff_profile(user, campus.id)
    if profile is None:
        raise PermissionDenied("Only campus staff can export reports.")
    return profile


def _can_group(user, campus, profile, group) -> bool:
    if not rules.can_export_group(profile.role, group):
        return False
    if group == "fees":
        # same gate as the finance screens: a department-limited HOD never sees campus-wide money
        return campus.id in finance_campus_ids(user, "fee.view") and bool(campus.fee_module_enabled)
    return True


class ReportCatalogView(APIView):
    """What this user can export (the app builds its picker from this)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        campus = get_campus(request)
        profile = _staff_role(request.user, campus)
        groups = [g for g in ("attendance", "marks", "fees") if _can_group(request.user, campus, profile, g)]
        return Response({
            "campus": str(campus.id),
            "role": profile.role,
            "formats": _formats_available(),
            "reports": [{"key": k, "group": rules.group_of(k), "title": rules.title_of(k)}
                        for k in rules.REPORTS if rules.group_of(k) in groups],
            "whole_campus": rules.sees_whole_campus(profile.role) and profile.department_id is None,
        })


def _scoped_sections(campus, profile, session, q):
    """The sections this user may export, narrowed by ?class= / ?section=."""
    qs = Section.objects.filter(school_class__campus=campus, school_class__session=session).select_related("school_class")
    if profile.role == StaffProfile.Role.PRINCIPAL_HOD and profile.department_id:
        qs = qs.filter(school_class__department_id=profile.department_id)
    elif profile.role == StaffProfile.Role.CLASS_TEACHER:
        mine = ClassTeacherassigments.objects.filter(staff=profile).values_list("section_id", flat=True)
        qs = qs.filter(id__in=list(mine))
        if not qs.exists():
            raise PermissionDenied("You are not the class teacher of any section in this session.")
    section_id, class_id = _uuid(q, "section"), _uuid(q, "class")
    if class_id:
        qs = qs.filter(school_class_id=class_id)
    if section_id:
        found = qs.filter(id=section_id)
        if not found.exists():
            if Section.objects.filter(id=section_id, school_class__campus=campus).exists():
                raise PermissionDenied("You can only export your own sections.")
            raise ValidationError({"section": "Not found in this campus."})
        qs = found
    return qs.order_by("school_class__name", "name")


def _scope_label(sections) -> str:
    names = [f"{s.school_class.name}-{s.name}" for s in sections[:6]]
    more = max(sections.count() - 6, 0)
    return ", ".join(names) + (f" +{more} more" if more else "") if names else "—"


class ReportExportView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [CampusReportExportThrottle]

    def get(self, request):
        q = request.query_params
        campus = get_campus(request)
        report = q.get("report", "")
        try:
            group = rules.group_of(report)
        except rules.ReportRuleError as exc:
            raise ValidationError({exc.field: exc.message})
        fmt = (q.get("format") or "csv").lower()
        if fmt not in rx.FORMATS:
            raise ValidationError({"format": "csv, xlsx or pdf."})

        profile = _staff_role(request.user, campus)
        if not rules.can_export_group(profile.role, group):
            raise PermissionDenied("You don't have permission to export this report.")
        if group == "fees":
            if campus.id not in finance_campus_ids(request.user, "fee.view"):
                raise PermissionDenied("You don't have permission for this finance action in this campus.")
            if not campus.fee_module_enabled:
                raise PermissionDenied("The fee module is not enabled for this campus.")

        today = timezone.localdate()
        subtitle: list[str] = []
        try:
            header, rows, subtitle = self._build(request, campus, profile, report, group, today)
        except rules.ReportRuleError as exc:
            raise ValidationError({exc.field: exc.message})

        try:
            blob, ctype, ext = rx.render(
                fmt, sheet=rules.title_of(report), title=rules.title_of(report), header=header, rows=rows,
                campus_name=campus.name, subtitle=subtitle, generated_at=timezone.localtime(),
                font_path=getattr(settings, "CAMPUS_PDF_FONT", None),
            )
        except (rx.ExportUnavailable, rx.PdfUnavailable) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except (rx.TooManyRows, rx.PdfTooLarge) as exc:
            raise ValidationError({"detail": str(exc)})

        log_action(campus.id, request.user, "report.export", target_type="Report", target_id=report,
                   summary=f"{report} exported as {fmt}", report=report, fmt=fmt, scope="; ".join(subtitle)[:200])
        resp = HttpResponse(blob, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="{rules.file_name(report, ext, today)}"'
        resp["Cache-Control"] = "no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp

    # ------------------------------------------------------------------ builders
    def _build(self, request, campus, profile, report, group, today):
        q = request.query_params
        if group == "fees":
            return self._fees(request, campus, report, today)

        session = get_session(campus, q.get("session"))
        sections = _scoped_sections(campus, profile, session, q)
        subject_id = _uuid(q, "subject")
        if subject_id and not Subject.objects.filter(pk=subject_id, campus=campus).exists():
            raise ValidationError({"subject": "Not found in this campus."})
        subtitle = [f"Session: {session.name}", f"Sections: {_scope_label(sections)}"]

        if group == "attendance":
            date_from, date_to = rules.resolve_range(parse_day(q.get("from"), "from"), parse_day(q.get("to"), "to"), today)
            subtitle.append(f"Period: {date_from:%d %b %Y} to {date_to:%d %b %Y}")
            build = reports.attendance_summary if report == rules.ATTENDANCE_SUMMARY else reports.attendance_daily
            header, rows = build(sections, session, date_from, date_to, subject_id=subject_id)
            return header, rows, subtitle

        term_id = _uuid(q, "exam_term")
        if not term_id:
            raise ValidationError({"exam_term": "exam_term is required for marks reports."})
        term = get_object_or_404(ExamTerm, pk=term_id, session=session)
        drafts = q.get("include_drafts") in ("1", "true")
        subtitle.append(f"Exam: {term.name}" + (" (incl. drafts)" if drafts else ""))
        if report == rules.MARKS_SHEET:
            header, rows = reports.marks_sheet(sections, term, campus, include_drafts=drafts)
        else:
            header, rows = reports.marks_detail(sections, term, campus, include_drafts=drafts, subject_id=subject_id)
        return header, rows, subtitle

    def _fees(self, request, campus, report, today):
        """Same data, same filters and same rules as `finance/export/` — just under the one report
        menu (and with PDF)."""
        from datetime import timedelta
        q = request.query_params
        name = rules.FEES_FINANCE_NAME[report]
        date_from, date_to = parse_day(q.get("from"), "from"), parse_day(q.get("to"), "to")
        if name in ("collection", "daybook") and not (date_from and date_to):
            date_from, date_to = date_from or today - timedelta(days=30), date_to or today
        if date_from and date_to and date_from > date_to:
            raise rules.ReportRuleError("from", "Start date is after the end date.")
        session = get_session(campus, q.get("session")) if (q.get("session") or name in ("dues", "concessions")) else None
        if name == "collection":
            header, rows = finance.report_collection(campus, date_from=date_from, date_to=date_to, session=session,
                                                     mode=q.get("mode") or None, status=q.get("status") or None)
        elif name == "daybook":
            header, rows = finance.report_daybook(campus, date_from=date_from, date_to=date_to, session=session)
        elif name == "dues":
            header, rows = finance.report_dues(campus, session=session, class_id=_uuid(q, "class"),
                                               section_id=_uuid(q, "section"),
                                               overdue_only=q.get("overdue_only") in ("1", "true"))
        else:
            header, rows = finance.report_concessions(campus, session=session)
        subtitle = []
        if session is not None:
            subtitle.append(f"Session: {session.name}")
        if date_from and date_to:
            subtitle.append(f"Period: {date_from:%d %b %Y} to {date_to:%d %b %Y}")
        return header, rows, subtitle
