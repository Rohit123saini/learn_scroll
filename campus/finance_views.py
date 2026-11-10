# campus/finance_views.py
"""
FINANCE — HTTP layer for concessions, concession grants, finance summary, exports and
payment-gateway reconciliation. (Fee-structure plans, waivers, fine waivers and the invoice PDF are
actions on the existing FeeStructureViewSet / FeeInvoiceViewSet in views.py.)

Access
  fee.view   admin, principal, non-teaching (accounts clerk)   — read, summary, exports
  fee.manage admin, principal                                  — everything that changes money
A department-limited HOD is refused (finance is campus-wide, same rule as year-end).
Every money-changing call writes a CampusAuditLog row.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import finance, finance_export, finance_recon
from . import panel as campus_panel
from .audit import log_action
from .models import (
    AcademicSession,
    Campus,
    FeeConcession,
    FeeConcessionGrant,
    FeeInvoice,
    FeePayment,
    FeeReconciliationItem,
    FeeReconciliationRun,
    StaffProfile,
)
from .permissions import ROLE_ACTIONS
from .serializers import (
    FeeConcessionGrantSerializer,
    FeeConcessionSerializer,
    FeeReconciliationItemSerializer,
    FeeReconciliationRunSerializer,
)
from .throttles import CampusFinanceExportThrottle, CampusFinanceReconcileThrottle

logger = logging.getLogger(__name__)


class FinanceAPIError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "finance_error"


def fail(exc: finance.FinanceError):
    raise FinanceAPIError(detail=exc.detail, code=exc.code)


# --------------------------------------------------------------------------- access
def finance_campus_ids(user, action_name: str) -> set:
    """Campuses where `user` may do `action_name` ('fee.view' / 'fee.manage'). Department-limited
    HODs are excluded — they never see campus-wide money."""
    if not user or not user.is_authenticated:
        return set()
    roles = ROLE_ACTIONS.get(action_name, ())
    qs = StaffProfile.objects.filter(user=user, is_active=True, role__in=roles)
    qs = qs.exclude(role=StaffProfile.Role.PRINCIPAL_HOD, department__isnull=False)
    return set(qs.values_list("campus_id", flat=True))


def need(user, campus, action_name: str):
    if campus.id not in finance_campus_ids(user, action_name):
        raise PermissionDenied("You don't have permission for this finance action in this campus.")


def need_fee_module(campus):
    if not campus.fee_module_enabled:
        raise PermissionDenied("The fee module is not enabled for this campus.")


def get_campus(request, data=None) -> Campus:
    raw = (data or {}).get("campus") if data is not None else None
    raw = raw or request.query_params.get("campus")
    if not raw:
        raise ValidationError({"campus": "campus is required."})
    try:
        return get_object_or_404(Campus, pk=raw)
    except (ValueError, DjangoValidationError):
        raise ValidationError({"campus": "Invalid campus id."})


def parse_day(raw, name):
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationError({name: "Use YYYY-MM-DD."})


def get_session(campus, raw):
    if raw:
        try:
            return get_object_or_404(AcademicSession, pk=raw, campus=campus)
        except (ValueError, DjangoValidationError):
            raise ValidationError({"session": "Invalid session id."})
    session = campus_panel.current_session(campus)
    if session is None:
        raise ValidationError({"session": "No current academic session — pass ?session=."})
    return session


# --------------------------------------------------------------------------- concessions
class FeeConcessionViewSet(viewsets.ModelViewSet):
    """GET list/detail (fee.view) · POST/PATCH/DELETE (fee.manage). A concession that already has
    grants cannot be deleted — switch it off (`is_active=false`) instead; invoices re-price."""

    serializer_class = FeeConcessionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = FeeConcession.objects.filter(campus_id__in=finance_campus_ids(self.request.user, "fee.view")).annotate(
            active_grants_ann=Count("grants", filter=Q(grants__is_active=True))  # [PERF] was 1 COUNT per row
        )
        campus = self.request.query_params.get("campus")
        if campus:
            qs = qs.filter(campus_id=campus)
        if self.request.query_params.get("active") in ("1", "true"):
            qs = qs.filter(is_active=True)
        return qs

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        need(self.request.user, campus, "fee.manage")
        need_fee_module(campus)
        obj = serializer.save()
        log_action(campus.id, self.request.user, "finance.concession.create", target=obj,
                   summary=f"Concession '{obj.name}' created", kind=obj.kind, value=obj.value)

    def perform_update(self, serializer):
        need(self.request.user, serializer.instance.campus, "fee.manage")
        if serializer.validated_data.get("campus") not in (None, serializer.instance.campus):
            raise ValidationError({"campus": "A concession cannot move to another campus."})
        with transaction.atomic():
            obj = serializer.save()
            repriced = finance.refresh_for_concession(obj)
        log_action(obj.campus_id, self.request.user, "finance.concession.update", target=obj,
                   summary=f"Concession '{obj.name}' updated; {repriced} invoice(s) re-priced")

    def perform_destroy(self, instance):
        need(self.request.user, instance.campus, "fee.manage")
        if instance.grants.exists():
            raise ValidationError({"detail": "This concession has been given to students. Switch it off instead of deleting."})
        log_action(instance.campus_id, self.request.user, "finance.concession.delete", target=instance,
                   summary=f"Concession '{instance.name}' deleted")
        instance.delete()


class FeeConcessionGrantViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.CreateModelMixin,
                                mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Give / take back a concession for one student (enrollment). DELETE = revoke (soft, keeps
    history). Giving or revoking re-prices the student's open invoices at once."""

    serializer_class = FeeConcessionGrantSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = FeeConcessionGrant.objects.filter(
            concession__campus_id__in=finance_campus_ids(self.request.user, "fee.view")
        ).select_related("concession", "fee_structure", "enrollment__student")
        q = self.request.query_params
        for param, field in (("campus", "concession__campus_id"), ("enrollment", "enrollment_id"),
                             ("concession", "concession_id"), ("session", "enrollment__session_id")):
            if q.get(param):
                qs = qs.filter(**{field: q[param]})
        if q.get("active") in ("1", "true"):
            qs = qs.filter(is_active=True)
        return qs

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        concession, enrollment = ser.validated_data["concession"], ser.validated_data["enrollment"]
        fs = ser.validated_data.get("fee_structure")
        campus = concession.campus
        need(request.user, campus, "fee.manage")
        need_fee_module(campus)
        with transaction.atomic():
            existing = FeeConcessionGrant.objects.select_for_update().filter(
                concession=concession, enrollment=enrollment, fee_structure=fs).first()
            if existing and existing.is_active:
                raise ValidationError({"detail": "This student already has this concession."})
            if existing:  # re-grant after a revoke
                existing.is_active, existing.revoked_at = True, None
                existing.granted_by, existing.note = request.user, ser.validated_data.get("note", "")
                existing.save(update_fields=["is_active", "revoked_at", "granted_by", "note"])
                grant = existing
            else:
                try:
                    grant = ser.save(granted_by=request.user)
                except IntegrityError:
                    raise ValidationError({"detail": "This student already has this concession."})
            repriced = finance.refresh_enrollment_invoices(enrollment, fs)
        log_action(campus.id, request.user, "finance.concession.grant", target=grant,
                   summary=f"'{concession.name}' given; {repriced} invoice(s) re-priced")
        return Response(self.get_serializer(grant).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance):
        campus = instance.concession.campus
        need(self.request.user, campus, "fee.manage")
        with transaction.atomic():
            instance.is_active, instance.revoked_at = False, timezone.now()
            instance.save(update_fields=["is_active", "revoked_at"])
            repriced = finance.refresh_enrollment_invoices(instance.enrollment, instance.fee_structure)
        log_action(campus.id, self.request.user, "finance.concession.revoke", target=instance,
                   summary=f"'{instance.concession.name}' revoked; {repriced} invoice(s) re-priced")


# --------------------------------------------------------------------------- summary
class FinanceSummaryView(APIView):
    """GET /campus/finance/summary/?campus=&session=&from=&to="""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        campus = get_campus(request)
        need(request.user, campus, "fee.view")
        need_fee_module(campus)
        session = get_session(campus, request.query_params.get("session"))
        date_from, date_to = parse_day(request.query_params.get("from"), "from"), parse_day(request.query_params.get("to"), "to")
        if date_from and date_to and date_from > date_to:
            raise ValidationError({"from": "Start date is after the end date."})
        return Response(finance.summary(campus, session, date_from, date_to))


# --------------------------------------------------------------------------- export
REPORTS = {"collection", "dues", "concessions", "daybook", "reconciliation"}


class FinanceExportView(APIView):
    """GET /campus/finance/export/?campus=&report=collection|dues|concessions|daybook|reconciliation
    &format=csv|xlsx [&from=&to=&session=&class=&section=&mode=&status=&overdue_only=1&run=]"""

    permission_classes = [IsAuthenticated]
    throttle_classes = [CampusFinanceExportThrottle]

    def get(self, request):
        q = request.query_params
        campus = get_campus(request)
        need(request.user, campus, "fee.view")
        need_fee_module(campus)
        report = q.get("report", "")
        if report not in REPORTS:
            raise ValidationError({"report": f"Choose one of: {', '.join(sorted(REPORTS))}."})
        fmt = q.get("format", "csv").lower()
        if fmt not in ("csv", "xlsx", "pdf"):
            raise ValidationError({"format": "csv, xlsx or pdf."})
        date_from, date_to = parse_day(q.get("from"), "from"), parse_day(q.get("to"), "to")
        if report in ("collection", "daybook") and not (date_from and date_to):
            today = timezone.localdate()
            date_from, date_to = date_from or today - timedelta(days=30), date_to or today
        if date_from and date_to and date_from > date_to:
            raise ValidationError({"from": "Start date is after the end date."})
        session = get_session(campus, q.get("session")) if (q.get("session") or report in ("dues", "concessions")) else None
        mode, pay_status = q.get("mode") or None, q.get("status") or None
        if mode and mode not in FeePayment.Mode.values:
            raise ValidationError({"mode": "Unknown payment mode."})
        if pay_status and pay_status not in FeePayment.Status.values:
            raise ValidationError({"status": "Unknown payment status."})

        if report == "collection":
            header, rows = finance.report_collection(campus, date_from=date_from, date_to=date_to, session=session,
                                                     mode=mode, status=pay_status)
        elif report == "daybook":
            header, rows = finance.report_daybook(campus, date_from=date_from, date_to=date_to, session=session)
        elif report == "dues":
            header, rows = finance.report_dues(campus, session=session, class_id=q.get("class") or None,
                                               section_id=q.get("section") or None,
                                               overdue_only=q.get("overdue_only") in ("1", "true"))
        elif report == "concessions":
            header, rows = finance.report_concessions(campus, session=session)
        else:
            if not q.get("run"):
                raise ValidationError({"run": "run is required for the reconciliation report."})
            header, rows = finance.report_reconciliation(campus, run_id=q["run"])

        try:
            # REPORTS / EXPORT: one writer for csv / xlsx / pdf (reports_export.render)
            from . import reports_export
            blob, ctype, ext = reports_export.render(
                fmt, sheet=report.title(), title=f"Fees - {report.title()}", header=header, rows=rows,
                campus_name=campus.name, generated_at=timezone.localtime(),
                subtitle=[f"Period: {date_from:%d %b %Y} to {date_to:%d %b %Y}"] if (date_from and date_to) else [],
            )
        except (finance_export.ExportUnavailable, reports_export.PdfUnavailable) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        except (finance_export.TooManyRows, reports_export.PdfTooLarge) as exc:
            raise ValidationError({"detail": str(exc)})

        log_action(campus.id, request.user, "finance.export", target_type="FinanceExport", target_id=report,
                   summary=f"{report} exported as {fmt}", report=report, fmt=fmt,
                   date_from=str(date_from or ""), date_to=str(date_to or ""))
        resp = HttpResponse(blob, content_type=ctype)
        resp["Content-Disposition"] = f'attachment; filename="fees_{report}_{timezone.localdate():%Y%m%d}.{ext}"'
        resp["Cache-Control"] = "no-store"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp


# --------------------------------------------------------------------------- reconciliation
class FeeReconciliationRunViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """GET list/detail (fee.view) · POST create (fee.manage):
         multipart  campus, source=razorpay_csv, file, [amount_unit=rupees|paise, from, to]
         json/form  campus, source=razorpay_api, from, to            (max 31 days)
         json/form  campus, source=internal                          (no gateway needed)
       GET {id}/items/?category=&severity=&resolution="""

    serializer_class = FeeReconciliationRunSerializer
    permission_classes = [IsAuthenticated]
    MAX_API_DAYS = 31

    def get_queryset(self):
        qs = FeeReconciliationRun.objects.filter(campus_id__in=finance_campus_ids(self.request.user, "fee.view"))
        if self.request.query_params.get("campus"):
            qs = qs.filter(campus_id=self.request.query_params["campus"])
        # [PERF] open_items was one COUNT per run; select_related keeps created_by_name free.
        return qs.select_related("created_by").annotate(open_items_ann=Count(
            "items", filter=Q(items__resolution="open", items__severity__in=("warn", "error"))
        ))

    def get_throttles(self):
        return [CampusFinanceReconcileThrottle()] if self.action == "create" else super().get_throttles()

    def create(self, request, *args, **kwargs):
        campus = get_campus(request, request.data)
        need(request.user, campus, "fee.manage")
        need_fee_module(campus)
        source = request.data.get("source", "")
        S = FeeReconciliationRun.Source
        date_from, date_to = parse_day(request.data.get("from"), "from"), parse_day(request.data.get("to"), "to")
        if (date_from is None) != (date_to is None):
            raise ValidationError({"from": "Give both from and to, or neither."})
        if date_from and date_from > date_to:
            raise ValidationError({"from": "Start date is after the end date."})
        period = (date_from, date_to) if date_from else None

        try:
            if source == S.INTERNAL:
                run = finance.run_internal_checks(campus, request.user)
            elif source == S.RAZORPAY_API:
                if not period:
                    raise ValidationError({"from": "from and to are required."})
                if (date_to - date_from).days >= self.MAX_API_DAYS:
                    raise ValidationError({"to": f"Pull at most {self.MAX_API_DAYS} days at a time."})
                if date_to > timezone.localdate():
                    raise ValidationError({"to": "The end date cannot be in the future."})
                rows = finance.fetch_razorpay_payments(date_from, date_to)
                run = finance.run_gateway_reconciliation(campus, request.user, source=source, gateway_rows=rows,
                                                         period=period, file_name="razorpay API")
            elif source == S.RAZORPAY_CSV:
                upload = request.FILES.get("file")
                if upload is None:
                    raise ValidationError({"file": "Attach the Razorpay payments report (.csv)."})
                if upload.size > finance_recon.MAX_BYTES:
                    raise ValidationError({"file": "File is too large (max 8 MB)."})
                try:
                    text = upload.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    raise ValidationError({"file": "Save the report as UTF-8 CSV."})
                unit = request.data.get("amount_unit", "rupees")
                if unit not in ("rupees", "paise"):
                    raise ValidationError({"amount_unit": "rupees or paise."})
                rows, errors = finance_recon.parse_razorpay_csv(text, amount_unit=unit)
                if errors:
                    return Response({"detail": "The file has problems — fix them and upload again.",
                                     "errors": [{"line": n, "message": m} for n, m in errors[:25]],
                                     "error_count": len(errors)}, status=status.HTTP_400_BAD_REQUEST)
                if not rows:
                    raise ValidationError({"file": "No payments found in the file."})
                run = finance.run_gateway_reconciliation(campus, request.user, source=source, gateway_rows=rows,
                                                         period=period, file_name=upload.name)
            else:
                raise ValidationError({"source": "razorpay_csv, razorpay_api or internal."})
        except finance.FinanceError as exc:
            fail(exc)

        log_action(campus.id, request.user, "finance.reconcile.run", target=run,
                   summary=f"Reconciliation ({source}): {run.summary.get('open_issues', 0)} open issue(s)",
                   source=source)
        return Response(FeeReconciliationRunSerializer(run).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="items")
    def items(self, request, pk=None):
        run = self.get_object()
        qs = run.items.select_related("invoice__enrollment__student", "payment__invoice__enrollment__student")
        q = request.query_params
        if q.get("category"):
            qs = qs.filter(category=q["category"])
        if q.get("severity"):
            qs = qs.filter(severity=q["severity"])
        if q.get("resolution"):
            qs = qs.filter(resolution=q["resolution"])
        elif q.get("only_issues") in ("1", "true"):
            qs = qs.filter(severity__in=("warn", "error"))
        qs = qs.order_by("severity", "category", "id")
        page = self.paginate_queryset(qs)
        ser = FeeReconciliationItemSerializer(page if page is not None else qs, many=True)
        return self.get_paginated_response(ser.data) if page is not None else Response(ser.data)


class FeeReconciliationItemViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """POST {id}/resolve/  {"action": "settle|mark_refunded|recompute|ignore|resolve", "note": "..."}"""

    serializer_class = FeeReconciliationItemSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return FeeReconciliationItem.objects.filter(
            run__campus_id__in=finance_campus_ids(self.request.user, "fee.view")
        ).select_related("run", "payment__invoice__enrollment__student", "invoice__enrollment__student")

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve(self, request, pk=None):
        item = self.get_object()
        campus = item.run.campus
        need(request.user, campus, "fee.manage")
        act = request.data.get("action", "")
        try:
            message = finance.resolve_item(item, action=act, note=request.data.get("note", ""), user=request.user)
        except finance.FinanceError as exc:
            fail(exc)
        item.refresh_from_db()
        log_action(campus.id, request.user, f"finance.reconcile.{act}", target=item,
                   summary=f"{item.category}: {message}", payment=str(item.payment_id or ""))
        return Response({"detail": message, "item": FeeReconciliationItemSerializer(item).data})
