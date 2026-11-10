# campus/views/fees.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

import uuid
from ..models import (
    AcademicSession,
    Campus,
    CampusParentLink,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    SchoolClass,
    StudentEnrollment,
)
from ..throttles import CampusFeePaymentThrottle
from django.db.models.functions import Coalesce
from user_profile.models import CoinLedger
from decimal import (
    Decimal,
    InvalidOperation,
)
from django.db.models import (
    DecimalField,
    Q,
    Sum,
    Value,
)
from ..serializers import (
    FeeInvoiceSerializer,
    FeePaymentSerializer,
    FeeStructureSerializer,
)
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsCampusAdminOrPrincipal,
    is_any_active_staff,
    is_campus_admin_or_principal,
    is_linked_parent_of_student,
)
from ..receipt_pdf import (
    PdfUnavailable,
    render_pdf,
)
from rest_framework.exceptions import (
    PermissionDenied,
    ValidationError,
)
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from ..receipt_pdf import _display_name as _receipt_display_name
from rest_framework.decorators import action
from .. import tasks as campus_tasks
from datetime import date
from core.async_utils import dispatch_after_commit
from .. import fee_gateway
from .. import (
    finance,
    finance_rules,
)
from ..finance_views import need as finance_need
from django.shortcuts import get_object_or_404
from ..audit import log_action
from rest_framework import (
    mixins,
    status,
    viewsets,
)
from ..invoice_pdf import render_invoice_pdf
from django.utils import timezone
from django.db import transaction
from ._common import CampusMemberScopedMixin


def _require_fee_module_enabled(campus):
    """FEE-1: single shared gate for every fee-writing entry point, so
    a campus that never opted in (`Campus.fee_module_enabled=False`)
    can't have fee structures, invoices, or payments created against
    it through ANY path — not just `FeeStructureViewSet.perform_create`,
    which was previously the only place this was checked."""
    if not campus.fee_module_enabled:
        raise PermissionDenied("The fee module is not enabled for this campus (design doc §8 — opt-in only).")


class FeeStructureViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = FeeStructureSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        # a student sees campus-wide fees + their own class's fees only
        return Q(school_class__isnull=True) | Q(school_class_id__in=vis.visible_class_ids)
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeeStructure.objects.all(), self.request)

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        _require_fee_module_enabled(campus)
        serializer.save()

    @action(detail=True, methods=["post"], url_path="generate-invoices")
    def generate_invoices(self, request, pk=None):
        """Bulk-creates a `FeeInvoice` for every currently-active
        enrollment this structure applies to (campus-wide if
        `school_class` is null, else just that class) — idempotent via
        `unique_invoice_per_structure`, so calling this twice never
        double-invoices the same student.

        FEE-1: this used to be reachable even for a campus that had
        since turned `fee_module_enabled` off after the FeeStructure
        was created (the module-disabled check only ran once, at
        FeeStructure-creation time, in `perform_create` above) — gated
        the same way here now."""
        fee_structure = self.get_object()
        _require_fee_module_enabled(fee_structure.campus)
        enrollments = StudentEnrollment.objects.filter(
            section__school_class__campus=fee_structure.campus,
            session=fee_structure.session,
            status=StudentEnrollment.Status.ACTIVE,
        )
        if fee_structure.school_class_id:
            enrollments = enrollments.filter(section__school_class_id=fee_structure.school_class_id)
        created = 0
        for enrollment in enrollments.select_related("section__school_class"):
            # FINANCE: numbers the invoice and applies the student's active concession grants.
            _, was_created = finance.create_invoice_for(enrollment, fee_structure)
            created += int(was_created)
        return Response({"invoices_created": created, "already_existed": enrollments.count() - created})

    # ------------------------------------------------------------------ FINANCE
    def perform_update(self, serializer):
        fine_fields = {"late_fine_type", "late_fine_value", "late_fine_grace_days", "late_fine_cap",
                       "due_date", "allow_concession", "category"}
        campus = serializer.instance.campus
        finance_need(self.request.user, campus, "fee.manage")
        touched = bool(fine_fields & set(serializer.validated_data))
        with transaction.atomic():
            fs = serializer.save()
            repriced = finance.refresh_structure_invoices(fs) if touched else 0
        log_action(campus.id, self.request.user, "finance.structure.update", target=fs,
                   summary=f"Fee '{fs.title}' updated; {repriced} open invoice(s) re-priced")

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        """Re-price every open invoice of this fee (concessions + late fine). Idempotent."""
        fs = self.get_object()
        finance_need(request.user, fs.campus, "fee.manage")
        _require_fee_module_enabled(fs.campus)
        return Response({"invoices_changed": finance.refresh_structure_invoices(fs)})

    @action(detail=False, methods=["post"], url_path="create-plan")
    def create_plan(self, request):
        """One fee split into N instalments that add up EXACTLY (paise-safe).
        Body: campus, session, [school_class], title, total_amount, installments (1-12),
        first_due_date, [every_months=1], [category], [allow_concession=true],
        [late_fine_type, late_fine_value, late_fine_grace_days, late_fine_cap],
        [generate_invoices=false], [dry_run=false]"""
        d = request.data
        campus = get_object_or_404(Campus, pk=d.get("campus")) if d.get("campus") else None
        if campus is None:
            raise ValidationError({"campus": "campus is required."})
        finance_need(request.user, campus, "fee.manage")
        _require_fee_module_enabled(campus)
        session = get_object_or_404(AcademicSession, pk=d.get("session"), campus=campus)
        school_class = None
        if d.get("school_class"):
            school_class = get_object_or_404(SchoolClass, pk=d["school_class"], campus=campus, session=session)
        title = (d.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Give the fee a name."})
        try:
            total = finance_rules.parse_money(d.get("total_amount"))
            parts = int(d.get("installments", 1))
            every = int(d.get("every_months", 1))
            first_due = date.fromisoformat(str(d.get("first_due_date")))
        except (ValueError, TypeError) as exc:
            raise ValidationError({"detail": f"Check total_amount / installments / every_months / first_due_date: {exc}"})
        if not 1 <= parts <= 12 or not 1 <= every <= 12:
            raise ValidationError({"installments": "Use 1-12 instalments, 1-12 months apart."})
        category = d.get("category", FeeStructure.Category.OTHER)
        if category not in FeeStructure.Category.values:
            raise ValidationError({"category": "Unknown fee category."})
        fine = {
            "late_fine_type": d.get("late_fine_type", FeeStructure.FineType.NONE),
            "late_fine_value": d.get("late_fine_value", 0) or 0,
            "late_fine_grace_days": int(d.get("late_fine_grace_days", 0) or 0),
            "late_fine_cap": d.get("late_fine_cap") or None,
        }
        check = FeeStructureSerializer(data={
            "campus": campus.id, "session": session.id, "title": title, "amount": str(total),
            "due_date": first_due.isoformat(), "category": category, **{k: v for k, v in fine.items() if v is not None},
        })
        check.is_valid(raise_exception=True)
        preview = [
            {"title": t, "amount": str(a), "due_date": dd.isoformat()}
            for t, a, dd in zip(finance_rules.installment_titles(title, parts),
                                finance_rules.split_amount(total, parts),
                                finance_rules.installment_dates(first_due, parts, every))
        ]
        if d.get("dry_run") in (True, "true", "1", 1):
            return Response({"dry_run": True, "installments": preview})
        structures, invoices = finance.create_installment_plan(
            campus=campus, session=session, school_class=school_class, title=title, total=total, parts=parts,
            first_due=first_due, every_months=every, category=category,
            allow_concession=d.get("allow_concession", True) not in (False, "false", "0", 0), fine=fine,
            generate=d.get("generate_invoices") in (True, "true", "1", 1),
        )
        log_action(campus.id, request.user, "finance.plan.create", target=structures[0],
                   summary=f"Plan '{title}': {parts} instalment(s), total {total}", invoices=invoices)
        return Response({"installments": preview, "structure_ids": [str(x.id) for x in structures],
                         "invoices_created": invoices}, status=status.HTTP_201_CREATED)


class FeeInvoiceViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    FEE-1 (gap found, not in the original task list but the same class
    of bug): this is a full `ModelViewSet`, so before this pass a
    campus admin could `POST` a `FeeInvoice` directly here even for a
    campus with `fee_module_enabled=False` — only
    `FeeStructureViewSet.perform_create`/`.generate_invoices` were
    gated, and this viewset's normal create path was never routed
    through either of those. Gated below the same way, for consistency
    with "agar False chuna, to fee-related sab UI/endpoints hidden/403
    rahein" from the task.
    """
    serializer_class = FeeInvoiceSerializer
    campus_field_path = "enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "enrollment__student"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(FeeInvoice.objects.all(), self.request)
        if getattr(self, "action", None) == "list":
            # [PERF] amount_paid + balance each ran a SUM query per invoice (2N+1 for a page).
            # One grouped annotation per page; FeeInvoice.amount_paid reads it (list only, so
            # payment/recompute flows never see a stale cached total).
            qs = qs.annotate(paid_total_ann=Coalesce(
                Sum("payments__amount", filter=Q(payments__status=FeePayment.Status.SUCCESS)),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ))
        return qs

    def perform_create(self, serializer):
        enrollment = serializer.validated_data["enrollment"]
        _require_fee_module_enabled(enrollment.section.school_class.campus)
        serializer.save()

    # ------------------------------------------------------------------ FINANCE
    def _manage(self, request, invoice):
        campus = invoice.enrollment.section.school_class.campus
        finance_need(request.user, campus, "fee.manage")
        _require_fee_module_enabled(campus)
        return campus

    def _audit(self, request, campus, name, invoice, summary):
        log_action(campus.id, request.user, f"finance.invoice.{name}", target=invoice,
                   summary=summary, invoice_no=invoice.invoice_no)

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        invoice = finance.refresh_invoice(invoice)
        self._audit(request, campus, "recalculate", invoice, "Invoice re-priced")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="waive")
    def waive(self, request, pk=None):
        """Write the whole invoice off. Body: {"reason": "..."} (required)."""
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        reason = (request.data.get("reason") or "").strip()
        if len(reason) < 3:
            raise ValidationError({"reason": "Give a reason (it is kept in the audit log)."})
        try:
            invoice = finance.waive_invoice(invoice, reason)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "waive", invoice, f"Invoice waived: {reason[:120]}")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="unwaive")
    def unwaive(self, request, pk=None):
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        try:
            invoice = finance.unwaive_invoice(invoice)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "unwaive", invoice, "Waiver removed")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="waive-fine")
    def waive_fine(self, request, pk=None):
        """Remove the late fine (and stop it re-accruing). Body: {"reason": "...", "restore": false}"""
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        restore = request.data.get("restore") in (True, "true", "1", 1)
        reason = (request.data.get("reason") or "").strip()
        if not restore and len(reason) < 3:
            raise ValidationError({"reason": "Give a reason (it is kept in the audit log)."})
        try:
            invoice = finance.set_fine_waived(invoice, not restore, reason)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "fine_restore" if restore else "fine_waive", invoice,
                    "Late fine restored" if restore else f"Late fine waived: {reason[:120]}")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="invoice-pdf")
    def invoice_pdf(self, request, pk=None):
        """The whole-invoice PDF (fee, concession, fine, payable, paid, balance, payments).
        Same visibility as the invoice itself (student / linked parent / campus staff)."""
        invoice = self.get_object()
        finance.refresh_fine(invoice)
        en = invoice.enrollment
        campus = en.section.school_class.campus
        fs = invoice.fee_structure
        concessions = [g.concession.name for g in finance.eligible_grants(invoice)]
        fine_note = ""
        if invoice.fine_amount and fs.due_date:
            fine_note = f"{max((timezone.localdate() - fs.due_date).days, 0)} days after due date"
        payments = [
            (finance._local_date(p.paid_at or p.created_at), p.payment_mode,
             p.gateway_payment_id or p.gateway_reference or p.notes, p.amount)
            for p in invoice.payments.filter(status=FeePayment.Status.SUCCESS).order_by("paid_at", "created_at")
        ]
        try:
            blob = render_invoice_pdf(
                invoice_no=invoice.invoice_no, campus_name=campus.name, campus_type=campus.type,
                student_name=_receipt_display_name(en.student), roll_number=en.roll_number,
                class_name=en.section.school_class.name, section_name=en.section.name, session_name=fs.session.name,
                fee_title=fs.title, fee_category=fs.category, issued_on=finance._local_date(invoice.created_at),
                due_date=fs.due_date, base_amount=invoice.base_amount, concession_amount=invoice.concession_amount,
                fine_amount=invoice.fine_amount, amount_due=invoice.amount_due, amount_paid=invoice.amount_paid,
                status=invoice.status, concession_names=concessions, fine_note=fine_note, payments=payments,
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        resp = HttpResponse(blob, content_type="application/pdf")
        name = (invoice.invoice_no or str(invoice.id)).replace("/", "-")
        resp["Content-Disposition"] = f'attachment; filename="invoice-{name}.pdf"'
        resp["Cache-Control"] = "no-store"
        return resp

    @action(detail=False, methods=["get"], url_path="my-summary")
    def my_summary(self, request):
        """"Fees dikhe": one call for the student / parent fee screen.

        `?student=<user id>` -> that child (a parent) or yourself; without it -> you + every child
        linked to you. Optional `?campus=`. Each invoice carries what is paid / still owed, a live
        `display_status` (an unpaid invoice past its due date reads "overdue" even if the cached
        column has not been recomputed), whether it can be paid online, and its payments with the
        receipt URL of the successful ones.
        """
        from django.urls import reverse

        qs = self.get_queryset().filter(
            enrollment__section__school_class__campus__fee_module_enabled=True
        )
        student_id = request.query_params.get("student")
        if student_id:
            qs = qs.filter(enrollment__student_id=student_id)
        else:
            mine = {request.user.id}
            mine.update(CampusParentLink.objects.filter(parent=request.user).values_list("student_id", flat=True))
            qs = qs.filter(enrollment__student_id__in=mine)
        qs = qs.select_related(
            "fee_structure", "enrollment__student", "enrollment__section__school_class__campus"
        ).prefetch_related("payments").order_by("fee_structure__due_date", "-created_at")

        today = timezone.localdate()
        online_ok = fee_gateway.online_enabled()
        invoices = []
        tot_due = tot_paid = tot_overdue = Decimal("0.00")
        for inv in qs:
            if inv.fee_structure.late_fine_type != FeeStructure.FineType.NONE and inv.status in (
                    FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE):
                finance.refresh_fine(inv)  # FINANCE: show today's late fine, not last night's
            paid = sum((p.amount for p in inv.payments.all() if p.status == FeePayment.Status.SUCCESS), Decimal("0.00"))
            due = fee_gateway.money(inv.amount_due)
            paid = fee_gateway.money(paid)
            balance = max(due - paid, Decimal("0.00"))
            if inv.status == FeeInvoice.Status.WAIVED:
                display, balance = "waived", Decimal("0.00")
            elif balance <= 0:
                display = "paid"
            elif inv.fee_structure.due_date and inv.fee_structure.due_date < today:
                display = "overdue"
            else:
                display = "partial" if paid > 0 else "pending"
            if display != "waived":
                tot_due += due
                tot_paid += min(paid, due)
                if display == "overdue":
                    tot_overdue += balance
            payments = []
            for p in sorted(inv.payments.all(), key=lambda x: x.created_at, reverse=True):
                if p.status == FeePayment.Status.PENDING and p.payment_mode == FeePayment.Mode.ONLINE:
                    continue  # an unfinished checkout is not something to show a parent
                payments.append({
                    "id": str(p.id),
                    "amount": str(p.amount),
                    "status": p.status,
                    "payment_mode": p.payment_mode,
                    "method": p.gateway_method,
                    "transaction_id": p.gateway_payment_id,
                    "paid_at": (p.paid_at or p.created_at).isoformat(),
                    "receipt_url": request.build_absolute_uri(
                        reverse("fee-payment-receipt-pdf", args=[p.id])
                    ) if p.status == FeePayment.Status.SUCCESS else None,
                })
            student = inv.enrollment.student
            invoices.append({
                "id": str(inv.id),
                "student": str(student.id),
                "student_name": _receipt_display_name(student),
                "fee_title": inv.fee_structure.title,
                "due_date": inv.fee_structure.due_date.isoformat() if inv.fee_structure.due_date else None,
                "amount_due": str(due),
                "amount_paid": str(paid),
                "balance": str(balance),
                # FINANCE breakdown: amount_due = base - concession + fine
                "invoice_no": inv.invoice_no,
                "base_amount": str(inv.base_amount),
                "concession_amount": str(inv.concession_amount),
                "fine_amount": str(inv.fine_amount),
                "invoice_pdf_url": request.build_absolute_uri(reverse("fee-invoice-invoice-pdf", args=[inv.id])),
                "status": inv.status,
                "display_status": display,
                "can_pay_online": online_ok and balance > 0,
                "payments": payments,
            })
        return Response({
            "currency": "INR",
            "online_enabled": online_ok,
            "online_methods": fee_gateway.allowed_methods() if online_ok else [],
            "totals": {
                "due": str(tot_due), "paid": str(tot_paid),
                "balance": str(max(tot_due - tot_paid, Decimal("0.00"))), "overdue": str(tot_overdue),
            },
            "invoices": invoices,
        })

    def get_campus_id_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        if enrollment_id:
            e = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if e:
                return e.section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)


class FeePaymentViewSet(CampusMemberScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    FEE-2: `pay` used to be `payment_mode=ONLINE`, landing `PENDING`
    until a separate `confirm` action was called (standing in for a
    Razorpay webhook). That online gateway + confirm step is gone —
    `pay` now debits the payer's `user_profile.CoinLedger`-backed
    wallet synchronously and atomically (`CoinLedger.record_transaction`
    holds a DB row lock for the whole operation), so there is no
    PENDING-until-webhook window to confirm and the `confirm` action
    has been removed as dead code, not left in place unreachable.

    Two write paths, both still first-class: `pay` — student/parent
    self-serve, `payment_mode=WALLET`, resolves straight to SUCCESS or
    a clean 402 in the same request; `record` — office staff logging a
    cash/cheque/bank-transfer payment (no wallet touched at all),
    marked `SUCCESS` immediately, same as before. No generic create/
    update/delete — every write goes through one of those two actions.
    """
    serializer_class = FeePaymentSerializer
    campus_field_path = "invoice__enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "invoice__enrollment__student"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeePayment.objects.all(), self.request)

    def _get_invoice_and_check(self, request, allow_self_pay):
        invoice_id = request.data.get("invoice")
        invoice = FeeInvoice.objects.filter(pk=invoice_id).first() if invoice_id else None
        if not invoice:
            return None, Response({"detail": "invoice is required."}, status=status.HTTP_400_BAD_REQUEST)
        campus = invoice.enrollment.section.school_class.campus
        # FEE-1: gate every fee write, not just FeeStructure creation —
        # a disabled module means no new invoices should be payable
        # either, even if the invoice row itself already exists.
        if not campus.fee_module_enabled:
            return None, Response(
                {"detail": "The fee module is not enabled for this campus."}, status=status.HTTP_403_FORBIDDEN
            )
        campus_id = campus.id
        if allow_self_pay:
            is_self = invoice.enrollment.student_id == request.user.id
            is_parent = is_linked_parent_of_student(request.user, invoice.enrollment.student_id, campus_id)
            if not (is_self or is_parent):
                return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        elif not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return invoice, None

    @staticmethod
    def _resolve_whole_coin_amount(raw_amount):
        """FEE-2 unit-mismatch guard (see models.py module docstring):
        FeePayment.amount is a Decimal (paise-capable), CoinLedger.amount/
        User.coin are whole integers. Returns `(amount_decimal, error_response)`
        — `error_response` is set instead of raising so callers can
        `return` it directly, matching this viewset's existing
        `(value, error)` convention (`_get_invoice_and_check` above)."""
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError):
            return None, Response({"detail": "amount must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return None, Response({"detail": "amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
        if amount != amount.to_integral_value():
            return None, Response(
                {"detail": "Wallet payments must be a whole number of coins (no paise)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return amount, None

    # B-4 fix — see CampusFeePaymentThrottle's docstring in throttles.py:
    # same scope reused across pay/record/refund below since all three
    # move money on an invoice, just through different roles/paths.
    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def pay(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=True)
        if error:
            return error
        amount, error = self._resolve_whole_coin_amount(request.data.get("amount", invoice.amount_due))
        if error:
            return error
        payer_role = (
            FeePayment.PayerRole.STUDENT
            if invoice.enrollment.student_id == request.user.id
            else FeePayment.PayerRole.PARENT
        )
        gateway_reference = request.data.get("gateway_reference") or uuid.uuid4().hex

        # get_or_create keyed on gateway_reference is the FeePayment-level
        # idempotency guard: a client retrying the SAME request (same
        # reference) gets back the row from its first attempt instead of
        # a second FeePayment (which unique_nonblank_gateway_reference
        # would reject anyway) and, critically, without a second coin debit.
        payment, created = FeePayment.objects.get_or_create(
            gateway_reference=gateway_reference,
            defaults=dict(
                invoice=invoice,
                amount=amount,
                paid_by=request.user,
                payer_role=payer_role,
                payment_mode=FeePayment.Mode.WALLET,
                status=FeePayment.Status.PENDING,
            ),
        )
        if not created:
            return Response(FeePaymentSerializer(payment).data, status=status.HTTP_200_OK)

        try:
            CoinLedger.objects.record_transaction(
                user=request.user,
                transaction_type=CoinLedger.TransactionType.SPEND,
                amount=-int(amount),
                # Derived from the already-deduplicated payment row's own
                # id, not a fresh random value per call — a second,
                # independent idempotency guarantee at the wallet layer
                # itself (see models.py module docstring), distinct from
                # the gateway_reference guard above.
                reference=f"fee-payment-{payment.id}",
                description=f"Fee paid for {invoice.fee_structure.title}",
                metadata={"invoice_id": str(invoice.id), "campus_id": str(invoice.enrollment.section.school_class.campus_id)},
                # S9: school fee is not "spending coins on the app" - the user's spend limit / minor cap must not block it.
                enforce_spend_limit=False,
            )
        except ValueError:
            # FEE-3: insufficient balance — clean, catchable, no exception
            # bubbling up as a 500. Payment row is kept as FAILED (not
            # deleted) so there's an audit trail of the attempt; the
            # gateway_reference stays reserved so a genuine retry with the
            # same client-side idempotency key doesn't collide with a NEW
            # attempt at a different amount.
            payment.status = FeePayment.Status.FAILED
            payment.save(update_fields=["status"])
            request.user.refresh_from_db(fields=["coin"])
            shortfall = int(amount) - request.user.coin
            return Response(
                {
                    "detail": "Insufficient coin balance to pay this fee.",
                    "current_balance": request.user.coin,
                    "required": int(amount),
                    "coins_needed": shortfall,
                    # FEE-3: no new coin-purchase gateway here — point the
                    # client at the existing tuitionclass top-up flow instead
                    # of rebuilding one. Campus deliberately never imports
                    # tuitionclass (see models.py GOLDEN RULE), so this is a
                    # generic flag for the frontend to route on, not a
                    # hardcoded tuitionclass URL.
                    "action": "top_up_coins",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        payment.mark_success()
        # Task 5 subtask 3 — fire-and-forget receipt email, same dispatch
        # bridge.notify uses for push: enqueued only once this request's
        # transaction actually commits, never blocks the response.
        dispatch_after_commit(campus_tasks.send_fee_receipt_email, payment.id)
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    # ---------------------------------------------- FEE-5: online (Razorpay UPI / card)
    @action(detail=False, methods=["post"], url_path="online-order", throttle_classes=[CampusFeePaymentThrottle])
    def online_order(self, request):
        """Start an online payment: -> Razorpay order for Checkout (see campus/fee_gateway.py).
        Same who-may-pay rule as `pay`: the student or a linked parent."""
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=True)
        if error:
            return error
        payer_role = (
            FeePayment.PayerRole.STUDENT
            if invoice.enrollment.student_id == request.user.id
            else FeePayment.PayerRole.PARENT
        )
        return fee_gateway.start_online_payment(
            invoice=invoice, user=request.user, payer_role=payer_role, raw_amount=request.data.get("amount"),
        )

    @action(detail=False, methods=["post"], url_path="online-verify", throttle_classes=[CampusFeePaymentThrottle])
    def online_verify(self, request):
        """After Checkout succeeds: send razorpay_order_id / razorpay_payment_id / razorpay_signature.
        Settles only on a valid signature; the signed webhook is the backup."""
        return fee_gateway.verify_online_payment(
            user=request.user,
            order_id=str(request.data.get("razorpay_order_id") or ""),
            payment_id=str(request.data.get("razorpay_payment_id") or ""),
            signature=str(request.data.get("razorpay_signature") or ""),
        )

    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def record(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=False)
        if error:
            return error
        finance.refresh_fine(invoice)  # FINANCE: quote today's fine, not yesterday's
        balance = max(finance_rules.money(invoice.amount_due) - finance_rules.money(invoice.amount_paid), finance_rules.ZERO)
        raw = request.data.get("amount")
        if raw in (None, ""):
            raw = balance
        try:  # office money is rupees-and-paise (only WALLET payments need whole coins)
            amount = finance_rules.parse_money(raw)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payment_mode = request.data.get("payment_mode", FeePayment.Mode.CASH)
        if payment_mode == FeePayment.Mode.WALLET:
            # Office staff shouldn't be able to trigger a wallet debit
            # through the "manual counter payment" path — that's what
            # `pay` is for, and it's the payer's own wallet, not
            # something staff can spend on someone else's behalf here.
            return Response(
                {"detail": "Use the pay action for wallet payments; record is for cash/cheque/bank-transfer only."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment = FeePayment.objects.create(
            invoice=invoice,
            amount=amount,
            payer_role=FeePayment.PayerRole.ADMIN,
            payment_mode=payment_mode,
            status=FeePayment.Status.SUCCESS,
            recorded_by=request.user,
            notes=request.data.get("notes", ""),
        )
        payment.invoice.recompute_status()
        # Task 5 subtask 3 — same receipt-email dispatch as the wallet
        # path above; `record` payments are created SUCCESS immediately
        # (no mark_success() call), so this is the equivalent hook here.
        dispatch_after_commit(campus_tasks.send_fee_receipt_email, payment.id)
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def refund(self, request, pk=None):
        """FEE-4: reverses a wallet payment and credits the coins back
        via `FeePayment.refund()`. Office/admin action only — same
        permission shape as `record`, not self-serve like `pay`."""
        payment = self.get_object()
        campus_id = payment.invoice.enrollment.section.school_class.campus_id
        if not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            payment.refund()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FeePaymentSerializer(payment).data)

    # ------------------------------------------------------ receipt PDF (Task 5, subtask 2/4)
    def _check_receipt_view_permission(self, request, payment):
        """Who can see a payment's receipt: the payer themselves, a
        linked parent of the paying student, or campus admin/staff —
        same three roles `_get_invoice_and_check` already recognizes for
        the invoice as a whole (self-pay vs. office-side), just read-only
        here instead of a write gate."""
        invoice = payment.invoice
        campus_id = invoice.enrollment.section.school_class.campus_id
        is_payer = payment.paid_by_id == request.user.id
        is_self = invoice.enrollment.student_id == request.user.id
        is_parent = is_linked_parent_of_student(request.user, invoice.enrollment.student_id, campus_id)
        is_staff = is_campus_admin_or_principal(request.user, campus_id) or is_any_active_staff(request.user, campus_id)
        return is_payer or is_self or is_parent or is_staff

    @action(detail=True, methods=["get"], url_path="receipt-pdf")
    def receipt_pdf(self, request, pk=None):
        payment = self.get_object()
        if not self._check_receipt_view_permission(request, payment):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if payment.status != FeePayment.Status.SUCCESS:
            return Response(
                {"detail": "A receipt is only available for a successful payment."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invoice = payment.invoice
        try:
            pdf = render_pdf(
                receipt_no=f"RCPT-{payment.id}",
                campus_name=invoice.enrollment.section.school_class.campus.name,
                campus_type=invoice.enrollment.section.school_class.campus.type,
                student_name=_receipt_display_name(invoice.enrollment.student),
                fee_title=invoice.fee_structure.title,
                invoice_id=str(invoice.id),
                amount=payment.amount,
                amount_due=invoice.amount_due,
                amount_paid_total=invoice.amount_paid,
                payment_mode=payment.payment_mode,
                payer_role=payment.payer_role,
                paid_by=payment.paid_by,
                recorded_by=payment.recorded_by,
                status=payment.status,
                notes=payment.notes,
                paid_at=payment.paid_at or payment.created_at,
                transaction_id=payment.gateway_payment_id,
                gateway_method=payment.gateway_method,
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="fee-receipt-{payment.id}.pdf"'
        return response
