# campus/finance.py
"""
FINANCE — service layer: loads rows, calls the pure rules in finance_rules.py /
finance_recon.py / finance_export.py, writes results back.

  invoice pricing   next_invoice_no · eligible_grants · refresh_fine · refresh_invoice ·
                    create_invoice_for · refresh_structure_invoices
  reporting         summary · report_collection · report_dues · report_concessions ·
                    report_daybook · report_reconciliation
  reconciliation    fetch_razorpay_payments · run_gateway_reconciliation ·
                    run_internal_checks · resolve_item

Imported lazily from models.FeeInvoice (save / recompute_status) — keep module-level imports of
`.models` only; never import `.views` here.
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from urllib import error as urlerror
from urllib import request as urlrequest

from django.conf import settings
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from . import finance_recon as rc
from . import finance_rules as fr
from .models import (
    AcademicSession,
    Campus,
    FeeConcession,
    FeeConcessionGrant,
    FeeInvoice,
    FeeInvoiceCounter,
    FeePayment,
    FeeReconciliationItem,
    FeeReconciliationRun,
    FeeStructure,
    StudentEnrollment,
)

logger = logging.getLogger(__name__)
ZERO = fr.ZERO


class FinanceError(Exception):
    """A rule was broken (bad input / unsafe action). `.detail` is safe to show the user."""

    def __init__(self, detail: str, code: str = "finance_error"):
        super().__init__(detail)
        self.detail = detail
        self.code = code


# ======================================================================================
# invoice numbering
# ======================================================================================
def next_invoice_no(invoice: FeeInvoice) -> str:
    """Next gap-free number for (campus, session). MUST run inside transaction.atomic()
    (FeeInvoice.save does) — the counter row is locked so two parallel generates cannot
    hand out the same number."""
    session = invoice.fee_structure.session
    counter, _ = FeeInvoiceCounter.objects.select_for_update().get_or_create(
        campus_id=invoice.campus_id, session=session
    )
    counter.last_no += 1
    counter.save(update_fields=["last_no"])
    return fr.format_invoice_no(fr.session_label(session.start_date, session.end_date), counter.last_no)


# ======================================================================================
# concession + fine on one invoice
# ======================================================================================
def eligible_grants(invoice: FeeInvoice):
    """Active grants that apply to this invoice (student/session match, scope, category rule)."""
    fs = invoice.fee_structure
    if not fs.allow_concession:
        return []
    qs = (
        FeeConcessionGrant.objects.filter(
            enrollment_id=invoice.enrollment_id, is_active=True, concession__is_active=True
        )
        .filter(Q(fee_structure__isnull=True) | Q(fee_structure_id=fs.id))
        .select_related("concession")
    )
    return [g for g in qs if not g.concession.applies_to_category or g.concession.applies_to_category == fs.category]


def compute_concession(invoice: FeeInvoice) -> Decimal:
    return fr.total_concession(invoice.base_amount, [(g.concession.kind, g.concession.value) for g in eligible_grants(invoice)])


def _local_date(dt) -> date:
    return timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()


def successful_payments(invoice: FeeInvoice) -> list[tuple[date, Decimal]]:
    rows = invoice.payments.filter(status=FeePayment.Status.SUCCESS).values_list("paid_at", "created_at", "amount")
    return [(_local_date(paid_at or created_at), amount) for paid_at, created_at, amount in rows]


def compute_fine(invoice: FeeInvoice, today: date | None = None) -> Decimal:
    fs = invoice.fee_structure
    if invoice.fine_waived or fs.late_fine_type == FeeStructure.FineType.NONE:
        return ZERO
    return fr.late_fine(
        kind=fs.late_fine_type, value=fs.late_fine_value, grace_days=fs.late_fine_grace_days,
        cap=fs.late_fine_cap, principal=fr.net_principal(invoice.base_amount, invoice.concession_amount),
        due_date=fs.due_date, payments=successful_payments(invoice), as_of=today or timezone.localdate(),
    )


def refresh_fine(invoice: FeeInvoice, today: date | None = None) -> bool:
    """Re-derive fine_amount and amount_due (= base - concession + fine). Saves only on change.
    Returns True when something changed. Safe to call any time; called from recompute_status."""
    if invoice.status == FeeInvoice.Status.WAIVED or invoice._state.adding:
        return False
    if not invoice.base_amount:  # legacy row that slipped the backfill
        invoice.base_amount = fr.money(invoice.amount_due) - fr.money(invoice.fine_amount) + fr.money(invoice.concession_amount)
    fine = compute_fine(invoice, today)
    due = fr.amount_due(invoice.base_amount, invoice.concession_amount, fine)
    if fine == fr.money(invoice.fine_amount) and due == fr.money(invoice.amount_due):
        return False
    invoice.fine_amount, invoice.amount_due = fine, due
    invoice.fine_computed_on = today or timezone.localdate()
    invoice.save(update_fields=["base_amount", "fine_amount", "amount_due", "fine_computed_on"])
    return True


def refresh_invoice(invoice: FeeInvoice, today: date | None = None) -> FeeInvoice:
    """Full re-price: concession from current grants + fine + status. Idempotent."""
    with transaction.atomic():
        invoice = FeeInvoice.objects.select_for_update().select_related("fee_structure", "enrollment").get(pk=invoice.pk)
        if invoice.status != FeeInvoice.Status.WAIVED:
            invoice.concession_amount = compute_concession(invoice)
            invoice.save(update_fields=["concession_amount"])
            refresh_fine(invoice, today)
        invoice.recompute_status()
    return invoice


def create_invoice_for(enrollment: StudentEnrollment, fee_structure: FeeStructure) -> tuple[FeeInvoice, bool]:
    """get_or_create an invoice and price it from the student's concession grants."""
    invoice, created = FeeInvoice.objects.get_or_create(
        enrollment=enrollment, fee_structure=fee_structure,
        defaults={"amount_due": fee_structure.amount, "base_amount": fee_structure.amount},
    )
    if created:
        refresh_invoice(invoice)
    return invoice, created


def refresh_structure_invoices(fee_structure: FeeStructure, today: date | None = None) -> int:
    """Re-price every unpaid-or-partial invoice of a structure (after a fine-policy / amount change).
    PAID and WAIVED invoices are history and left alone. Returns how many changed."""
    changed = 0
    qs = FeeInvoice.objects.filter(fee_structure=fee_structure).exclude(
        status__in=[FeeInvoice.Status.PAID, FeeInvoice.Status.WAIVED]
    )
    for inv in qs.iterator(chunk_size=200):
        before = (inv.amount_due, inv.fine_amount, inv.concession_amount, inv.status)
        fresh = refresh_invoice(inv, today)
        changed += int(before != (fresh.amount_due, fresh.fine_amount, fresh.concession_amount, fresh.status))
    return changed


def refresh_open_invoices_for_campus(campus_id=None, today: date | None = None) -> int:
    """Daily job: accrue late fines on overdue invoices whose structure has a fine policy."""
    today = today or timezone.localdate()
    qs = (
        FeeInvoice.objects.filter(
            fee_structure__due_date__lt=today,
            status__in=[FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE],
            fine_waived=False,
        )
        .exclude(fee_structure__late_fine_type=FeeStructure.FineType.NONE)
        .select_related("fee_structure", "enrollment")
    )
    if campus_id:
        qs = qs.filter(campus_id=campus_id)
    n = 0
    for inv in qs.iterator(chunk_size=200):
        try:
            n += int(refresh_fine(inv, today))
            inv.recompute_status()
        except Exception:  # one bad row must not stop the run
            logger.exception("late-fine refresh failed for invoice %s", inv.pk)
    return n


_REPRICEABLE = (FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE)


def refresh_enrollment_invoices(enrollment, fee_structure=None) -> int:
    """Re-price a student's open invoices after a concession was granted / revoked.
    PAID invoices are history — a late concession does not rewrite them (refund by hand)."""
    qs = FeeInvoice.objects.filter(enrollment=enrollment, status__in=_REPRICEABLE)
    if fee_structure is not None:
        qs = qs.filter(fee_structure=fee_structure)
    n = 0
    for inv in qs:
        before = inv.amount_due
        n += int(refresh_invoice(inv).amount_due != before)
    return n


def refresh_for_concession(concession) -> int:
    """A concession's value / kind / on-off changed: re-price every open invoice it touches."""
    n = 0
    seen = set()
    for g in concession.grants.select_related("enrollment", "fee_structure"):
        key = (g.enrollment_id, g.fee_structure_id)
        if key in seen:
            continue
        seen.add(key)
        n += refresh_enrollment_invoices(g.enrollment, g.fee_structure)
    return n


def waive_invoice(invoice: FeeInvoice, reason: str) -> FeeInvoice:
    with transaction.atomic():
        inv = FeeInvoice.objects.select_for_update().get(pk=invoice.pk)
        if inv.status == FeeInvoice.Status.PAID:
            raise FinanceError("A fully paid invoice cannot be waived.", "already_paid")
        inv.status = FeeInvoice.Status.WAIVED
        inv.adjustment_note = reason[:255]
        inv.save(update_fields=["status", "adjustment_note"])
    return inv


def unwaive_invoice(invoice: FeeInvoice) -> FeeInvoice:
    with transaction.atomic():
        inv = FeeInvoice.objects.select_for_update().get(pk=invoice.pk)
        if inv.status != FeeInvoice.Status.WAIVED:
            raise FinanceError("This invoice is not waived.", "not_waived")
        inv.status = FeeInvoice.Status.PENDING
        inv.save(update_fields=["status"])
    return refresh_invoice(inv)


def set_fine_waived(invoice: FeeInvoice, waived: bool, reason: str = "") -> FeeInvoice:
    with transaction.atomic():
        inv = FeeInvoice.objects.select_for_update().get(pk=invoice.pk)
        if inv.status == FeeInvoice.Status.WAIVED:
            raise FinanceError("This invoice is waived entirely.", "invoice_waived")
        inv.fine_waived = waived
        if reason:
            inv.adjustment_note = reason[:255]
        inv.save(update_fields=["fine_waived", "adjustment_note"])
    return refresh_invoice(inv)


def create_installment_plan(*, campus, session, school_class, title, total, parts, first_due, every_months,
                            category, allow_concession, fine: dict, generate: bool):
    """N FeeStructures that add up exactly to `total`. Returns (structures, invoices_created)."""
    amounts = fr.split_amount(total, parts)
    dates = fr.installment_dates(first_due, parts, every_months)
    titles = fr.installment_titles(title, parts)
    structures, invoices = [], 0
    with transaction.atomic():
        for t, a, d in zip(titles, amounts, dates):
            structures.append(FeeStructure.objects.create(
                campus=campus, session=session, school_class=school_class, title=t, amount=a, due_date=d,
                category=category, allow_concession=allow_concession, **fine,
            ))
        if generate:
            enrollments = StudentEnrollment.objects.filter(
                section__school_class__campus=campus, session=session, status=StudentEnrollment.Status.ACTIVE
            )
            if school_class is not None:
                enrollments = enrollments.filter(section__school_class=school_class)
            for fs in structures:
                for en in enrollments.select_related("section__school_class"):
                    invoices += int(create_invoice_for(en, fs)[1])
    return structures, invoices


# ======================================================================================
# reporting
# ======================================================================================
def student_name(user) -> str:
    if user is None:
        return ""
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()


def paid_subquery():
    return Coalesce(
        Subquery(
            FeePayment.objects.filter(invoice=OuterRef("pk"), status=FeePayment.Status.SUCCESS)
            .order_by().values("invoice").annotate(t=Sum("amount")).values("t")[:1],
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
        Value(Decimal("0")), output_field=DecimalField(max_digits=12, decimal_places=2),
    )


def _inv_qs(campus, session=None, class_id=None, section_id=None):
    qs = FeeInvoice.objects.filter(campus=campus).select_related(
        "fee_structure", "enrollment__student", "enrollment__section__school_class"
    )
    if session is not None:
        qs = qs.filter(fee_structure__session=session)
    if class_id:
        qs = qs.filter(enrollment__section__school_class_id=class_id)
    if section_id:
        qs = qs.filter(enrollment__section_id=section_id)
    return qs


def _pay_qs(campus, date_from=None, date_to=None, session=None, mode=None, status=None):
    qs = FeePayment.objects.filter(invoice__campus=campus).select_related(
        "invoice__fee_structure", "invoice__enrollment__student",
        "invoice__enrollment__section__school_class", "recorded_by",
    )
    if session is not None:
        qs = qs.filter(invoice__fee_structure__session=session)
    if mode:
        qs = qs.filter(payment_mode=mode)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(Q(paid_at__date__gte=date_from) | Q(paid_at__isnull=True, created_at__date__gte=date_from))
    if date_to:
        qs = qs.filter(Q(paid_at__date__lte=date_to) | Q(paid_at__isnull=True, created_at__date__lte=date_to))
    return qs


def summary(campus: Campus, session: AcademicSession, date_from: date | None, date_to: date | None) -> dict:
    today = timezone.localdate()
    rows = list(
        _inv_qs(campus, session).annotate(paid=paid_subquery()).values(
            "status", "base_amount", "concession_amount", "fine_amount", "amount_due", "paid",
            "fee_structure__due_date", "fee_structure__category",
            "enrollment__section__school_class_id", "enrollment__section__school_class__name",
        )
    )
    t = {k: ZERO for k in ("billed", "concession", "fine", "payable", "collected", "outstanding", "overdue", "waived")}
    by_class: dict = {}
    by_head: dict = {}
    for r in rows:
        paid = fr.money(r["paid"])
        if r["status"] == FeeInvoice.Status.WAIVED:
            t["waived"] += fr.money(r["base_amount"])
            continue
        due = fr.money(r["amount_due"])
        bal = max(due - paid, ZERO)
        t["billed"] += fr.money(r["base_amount"])
        t["concession"] += fr.money(r["concession_amount"])
        t["fine"] += fr.money(r["fine_amount"])
        t["payable"] += due
        t["collected"] += paid
        t["outstanding"] += bal
        if bal > 0 and r["fee_structure__due_date"] and r["fee_structure__due_date"] < today:
            t["overdue"] += bal
        c = by_class.setdefault(r["enrollment__section__school_class_id"], {
            "class_id": str(r["enrollment__section__school_class_id"]),
            "class_name": r["enrollment__section__school_class__name"],
            "payable": ZERO, "collected": ZERO, "outstanding": ZERO, "invoices": 0})
        c["payable"] += due; c["collected"] += paid; c["outstanding"] += bal; c["invoices"] += 1
        h = by_head.setdefault(r["fee_structure__category"], {"category": r["fee_structure__category"],
                                                              "payable": ZERO, "collected": ZERO})
        h["payable"] += due; h["collected"] += paid

    pays = _pay_qs(campus, date_from, date_to, session)
    ok = pays.filter(status=FeePayment.Status.SUCCESS)
    by_mode = {}
    by_day: dict = {}
    online_total = online_reconciled = ZERO
    online_unreconciled_count = 0
    for p in ok.values("payment_mode", "amount", "paid_at", "created_at", "reconciled_at"):
        amt = fr.money(p["amount"])
        by_mode[p["payment_mode"]] = by_mode.get(p["payment_mode"], ZERO) + amt
        day = _local_date(p["paid_at"] or p["created_at"]).isoformat()
        by_day[day] = by_day.get(day, ZERO) + amt
        if p["payment_mode"] == FeePayment.Mode.ONLINE:
            online_total += amt
            if p["reconciled_at"]:
                online_reconciled += amt
            else:
                online_unreconciled_count += 1
    refunded = pays.filter(status=FeePayment.Status.REFUNDED).aggregate(t=Sum("amount"))["t"] or ZERO
    open_items = FeeReconciliationItem.objects.filter(
        run__campus=campus, resolution=FeeReconciliationItem.Resolution.OPEN, severity__in=("warn", "error")
    ).count()
    s = lambda d: str(fr.money(d))  # noqa: E731
    return {
        "session": {"id": str(session.id), "name": session.name},
        "period": {"from": date_from.isoformat() if date_from else None, "to": date_to.isoformat() if date_to else None},
        "invoices": {k: s(v) for k, v in t.items()},
        "collection": {
            "total": s(sum(by_mode.values(), ZERO)),
            "by_mode": {k: s(v) for k, v in sorted(by_mode.items())},
            "by_day": [{"date": k, "amount": s(v)} for k, v in sorted(by_day.items())],
            "refunded": s(refunded),
        },
        "by_class": [{**{k: (s(v) if isinstance(v, Decimal) else v) for k, v in c.items()}}
                     for c in sorted(by_class.values(), key=lambda c: c["class_name"])],
        "by_category": [{"category": h["category"], "payable": s(h["payable"]), "collected": s(h["collected"])}
                        for h in sorted(by_head.values(), key=lambda h: h["category"])],
        "online": {"total": s(online_total), "reconciled": s(online_reconciled),
                   "unreconciled_payments": online_unreconciled_count, "open_reconciliation_issues": open_items},
    }


def report_collection(campus, *, date_from, date_to, session=None, mode=None, status=None):
    header = ["Payment ID", "Date", "Invoice No", "Student", "Roll No", "Class", "Section", "Fee", "Category",
              "Mode", "Gateway method", "Amount", "Status", "Gateway order", "Gateway payment", "Reconciled",
              "Recorded by", "Notes"]
    qs = _pay_qs(campus, date_from, date_to, session, mode, status).order_by("paid_at", "created_at")

    def rows():
        for p in qs.iterator(chunk_size=500):
            inv, en = p.invoice, p.invoice.enrollment
            yield [str(p.id), _local_date(p.paid_at or p.created_at), inv.invoice_no, student_name(en.student),
                   en.roll_number, en.section.school_class.name, en.section.name, inv.fee_structure.title,
                   inv.fee_structure.category, p.payment_mode, p.gateway_method, p.amount, p.status,
                   p.gateway_reference, p.gateway_payment_id, p.reconciled_at is not None,
                   student_name(p.recorded_by), p.notes]
    return header, rows()


def report_dues(campus, *, session=None, class_id=None, section_id=None, overdue_only=False):
    header = ["Invoice No", "Student", "Roll No", "Class", "Section", "Fee", "Due date", "Base", "Concession",
              "Fine", "Payable", "Paid", "Balance", "Status", "Days overdue"]
    today = timezone.localdate()
    qs = (_inv_qs(campus, session, class_id, section_id)
          .exclude(status__in=[FeeInvoice.Status.PAID, FeeInvoice.Status.WAIVED])
          .annotate(paid=paid_subquery()).order_by("fee_structure__due_date", "enrollment__section__school_class__name"))

    def rows():
        for inv in qs.iterator(chunk_size=500):
            bal = max(fr.money(inv.amount_due) - fr.money(inv.paid), ZERO)
            if bal <= 0:
                continue
            due = inv.fee_structure.due_date
            overdue_days = max((today - due).days, 0) if due else 0
            if overdue_only and overdue_days == 0:
                continue
            en = inv.enrollment
            yield [inv.invoice_no, student_name(en.student), en.roll_number, en.section.school_class.name,
                   en.section.name, inv.fee_structure.title, due, inv.base_amount, inv.concession_amount,
                   inv.fine_amount, inv.amount_due, inv.paid, bal, inv.status, overdue_days]
    return header, rows()


def report_concessions(campus, *, session=None):
    header = ["Student", "Roll No", "Class", "Section", "Concession", "Reason", "Type", "Value", "Applies to",
              "Granted by", "Granted on", "Active", "Note"]
    qs = FeeConcessionGrant.objects.filter(concession__campus=campus).select_related(
        "concession", "fee_structure", "granted_by", "enrollment__student", "enrollment__section__school_class")
    if session is not None:
        qs = qs.filter(enrollment__session=session)

    def rows():
        for g in qs.order_by("enrollment__section__school_class__name", "enrollment__roll_number").iterator(chunk_size=500):
            en, c = g.enrollment, g.concession
            yield [student_name(en.student), en.roll_number, en.section.school_class.name, en.section.name, c.name,
                   c.reason, c.kind, c.value, g.fee_structure.title if g.fee_structure_id else "All eligible fees",
                   student_name(g.granted_by), _local_date(g.created_at), g.is_active, g.note]
    return header, rows()


def report_daybook(campus, *, date_from, date_to, session=None):
    header = ["Date", "Mode", "Payments", "Amount"]
    agg: dict = {}
    for p in _pay_qs(campus, date_from, date_to, session, None, FeePayment.Status.SUCCESS).values(
            "payment_mode", "amount", "paid_at", "created_at"):
        k = (_local_date(p["paid_at"] or p["created_at"]), p["payment_mode"])
        n, a = agg.get(k, (0, ZERO))
        agg[k] = (n + 1, a + fr.money(p["amount"]))
    return header, ([d, m, n, a] for (d, m), (n, a) in sorted(agg.items()))


def report_reconciliation(campus, *, run_id):
    header = ["Category", "Severity", "Suggested action", "Resolution", "Detail", "Our payment", "Our amount",
              "Our status", "Gateway payment", "Gateway order", "Gateway amount", "Gateway status", "Method",
              "Gateway fee", "Gateway tax", "Settlement", "Resolution note"]
    qs = FeeReconciliationItem.objects.filter(run__campus=campus, run_id=run_id).order_by("severity", "category")

    def rows():
        for i in qs.iterator(chunk_size=500):
            yield [i.category, i.severity, i.suggested_action, i.resolution, i.detail, i.payment_id or "",
                   i.our_amount, i.our_status, i.gateway_payment_id, i.gateway_order_id, i.gateway_amount,
                   i.gateway_status, i.gateway_method, i.gateway_fee, i.gateway_tax, i.settlement_id,
                   i.resolution_note]
    return header, rows()


# ======================================================================================
# reconciliation
# ======================================================================================
def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def fetch_razorpay_payments(date_from: date, date_to: date, *, max_rows: int = rc.MAX_ROWS) -> list[rc.GatewayPayment]:
    """Pull payments from Razorpay (GET /payments, paginated). stdlib only, mirrors
    user_profile.payments.create_razorpay_order. Raises FinanceError on any gateway problem."""
    key_id = getattr(settings, "RAZORPAY_KEY_ID", "")
    key_secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    if not key_id or not key_secret:
        raise FinanceError("Razorpay keys are not configured on the server.", "gateway_not_configured")
    base = getattr(settings, "RAZORPAY_API_BASE", "https://api.razorpay.com/v1").rstrip("/")
    tz = timezone.get_current_timezone()
    start = int(datetime.combine(date_from, time.min, tzinfo=tz).timestamp())
    end = int(datetime.combine(date_to, time.max, tzinfo=tz).timestamp())
    auth = "Basic " + base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    items: list[dict] = []
    skip = 0
    while len(items) < max_rows:
        url = f"{base}/payments?from={start}&to={end}&count=100&skip={skip}"
        req = urlrequest.Request(url, headers={"Authorization": auth})
        try:
            with urlrequest.urlopen(req, timeout=20) as resp:  # noqa: S310 (fixed https base)
                data = json.loads(resp.read().decode())
        except (urlerror.URLError, TimeoutError, ValueError) as exc:
            raise FinanceError(f"Could not fetch payments from Razorpay: {exc}", "gateway_error") from exc
        batch = data.get("items") or []
        items.extend(batch)
        if len(batch) < 100:
            break
        skip += 100
    return rc.from_api_items(items)


def _our_payment(p: FeePayment) -> rc.OurPayment:
    created = p.created_at
    if created is not None and timezone.is_aware(created):
        created = timezone.make_naive(created, timezone.utc)
    return rc.OurPayment(
        payment_id=str(p.id), order_id=p.gateway_reference, gateway_payment_id=p.gateway_payment_id,
        amount=fr.money(p.amount), status=p.status, when=_local_date(p.paid_at or p.created_at), created_at=created,
    )


def _store_run(campus, user, source, findings, summary_data, *, period, file_name, payments_by_id) -> FeeReconciliationRun:
    run = FeeReconciliationRun.objects.create(
        campus=campus, source=source, created_by=user, period_from=period[0] if period else None,
        period_to=period[1] if period else None, file_name=file_name[:200], summary=summary_data,
    )
    inv_by_pay = {pid: p.invoice_id for pid, p in payments_by_id.items()}
    items = []
    for f in findings:
        items.append(FeeReconciliationItem(
            run=run, category=f.category, severity=f.severity, suggested_action=f.suggested_action,
            detail=f.detail, payment_id=f.payment_id or None,
            invoice_id=f.invoice_id or inv_by_pay.get(f.payment_id),
            gateway_order_id=f.order_id[:100], gateway_payment_id=f.gateway_payment_id[:100],
            our_amount=f.our_amount, gateway_amount=f.gateway_amount, our_status=f.our_status,
            gateway_status=f.gateway_status, gateway_method=f.method[:20], gateway_fee=f.fee, gateway_tax=f.tax,
            settlement_id=f.settlement_id[:60],
            resolution=(FeeReconciliationItem.Resolution.AUTO if f.category == rc.MATCHED
                        else FeeReconciliationItem.Resolution.OPEN),
        ))
    FeeReconciliationItem.objects.bulk_create(items, batch_size=500)
    return run


def _supersede(campus, run, findings, *, internal: bool):
    """A newer run replaces older OPEN items about the same payment / gateway payment."""
    pay_ids = {f.payment_id for f in findings if f.payment_id}
    gw_ids = {f.gateway_payment_id for f in findings if f.gateway_payment_id}
    base = FeeReconciliationItem.objects.filter(
        run__campus=campus, resolution=FeeReconciliationItem.Resolution.OPEN
    ).exclude(run=run)
    base = base.filter(run__source=FeeReconciliationRun.Source.INTERNAL) if internal else base.exclude(
        run__source=FeeReconciliationRun.Source.INTERNAL)
    for chunk in _chunks(pay_ids):
        base.filter(payment_id__in=chunk).update(resolution=FeeReconciliationItem.Resolution.SUPERSEDED)
    for chunk in _chunks(gw_ids):
        base.filter(gateway_payment_id__in=chunk).update(resolution=FeeReconciliationItem.Resolution.SUPERSEDED)


def run_gateway_reconciliation(campus, user, *, source, gateway_rows, period, file_name="") -> FeeReconciliationRun:
    """Compare Razorpay's rows with our ONLINE FeePayment rows of `campus`. Read-only on money:
    only stamps `reconciled_at` on exact matches. Everything else becomes an item to review."""
    dates = [g.when for g in gateway_rows if g.when]
    if period is None and dates:
        period = (min(dates), max(dates))
    order_ids = {g.order_id for g in gateway_rows if g.order_id}
    pay_ids = {g.payment_id for g in gateway_rows}

    base = FeePayment.objects.filter(invoice__campus=campus, payment_mode=FeePayment.Mode.ONLINE)
    found: dict = {}
    if period:
        lo, hi = period[0] - timedelta(days=2), period[1] + timedelta(days=2)
        for p in base.filter(Q(created_at__date__range=(lo, hi)) | Q(paid_at__date__range=(lo, hi))):
            found[p.id] = p
    for chunk in _chunks(order_ids):
        for p in base.filter(gateway_reference__in=chunk):
            found[p.id] = p
    for chunk in _chunks(pay_ids):
        for p in base.filter(gateway_payment_id__in=chunk):
            found[p.id] = p

    ours = [_our_payment(p) for p in found.values()]
    own_orders = {o.order_id for o in ours if o.order_id}
    ignore = set()
    try:  # coin wallet top-ups share the Razorpay account — not fee money
        from user_profile.models import CoinPurchaseRequest

        for chunk in _chunks(order_ids - own_orders):
            ignore.update(CoinPurchaseRequest.objects.filter(gateway_reference__in=chunk).values_list("gateway_reference", flat=True))
    except Exception:  # pragma: no cover - never let an unrelated app break reconciliation
        logger.exception("could not load coin purchase orders to ignore")

    findings = rc.reconcile(ours, gateway_rows, window=period, ignore_order_ids=ignore)
    with transaction.atomic():
        run = _store_run(campus, user, source, findings, rc.summarize(findings, gateway_rows),
                         period=period, file_name=file_name, payments_by_id={str(k): v for k, v in found.items()})
        matched = [f.payment_id for f in findings if f.category == rc.MATCHED and f.payment_id]
        for chunk in _chunks(matched):
            FeePayment.objects.filter(id__in=chunk, reconciled_at__isnull=True).update(reconciled_at=timezone.now())
        _supersede(campus, run, findings, internal=False)
    return run


def run_internal_checks(campus, user, *, stale_hours: int = 24) -> FeeReconciliationRun:
    """No gateway file needed: finds things that are wrong inside our own data."""
    now = timezone.now()
    findings: list[rc.Finding] = []
    payments: dict = {}

    def add(cat, detail, *, p=None, inv=None, action=None):
        sev, default_action = rc.CATEGORY_META[cat]
        findings.append(rc.Finding(
            category=cat, severity=sev, suggested_action=action or default_action, detail=detail,
            payment_id=str(p.id) if p else "", order_id=p.gateway_reference if p else "",
            invoice_id=str(inv.id) if inv else "",
            gateway_payment_id=p.gateway_payment_id if p else "",
            our_amount=fr.money(p.amount) if p else (fr.money(inv.amount_due) if inv else None),
            our_status=p.status if p else (inv.status if inv else ""),
        ))
        if p:
            payments[str(p.id)] = p

    online = FeePayment.objects.filter(invoice__campus=campus, payment_mode=FeePayment.Mode.ONLINE)
    for p in online.filter(status=FeePayment.Status.PENDING, created_at__lt=now - timedelta(hours=stale_hours)):
        add(rc.STALE_PENDING, "Checkout started but never completed (abandoned).", p=p)
    for p in online.filter(status=FeePayment.Status.SUCCESS, gateway_payment_id=""):
        add(rc.NO_TXN_ID, "Online payment marked paid without a Razorpay transaction id.", p=p)

    today = timezone.localdate()
    for inv in FeeInvoice.objects.filter(campus=campus).exclude(status=FeeInvoice.Status.WAIVED).select_related(
            "fee_structure").annotate(paid=paid_subquery()).iterator(chunk_size=500):
        paid, due = fr.money(inv.paid), fr.money(inv.amount_due)
        if paid > due:
            add(rc.OVERPAID_INVOICE, f"Paid {paid} against {due} due — refund or adjust the {paid - due} extra.", inv=inv)
        if paid >= due:
            expect = FeeInvoice.Status.PAID
        elif paid > 0:
            expect = FeeInvoice.Status.PARTIAL
        else:
            expect = FeeInvoice.Status.OVERDUE if inv.fee_structure.due_date < today else FeeInvoice.Status.PENDING
        if expect != inv.status:
            add(rc.STATUS_DRIFT, f"Stored status '{inv.status}' but payments say '{expect}'.", inv=inv)
    with transaction.atomic():
        run = _store_run(campus, user, FeeReconciliationRun.Source.INTERNAL, findings,
                         {"counts": rc.summarize(findings)["counts"], "open_issues": len(findings)},
                         period=None, file_name="", payments_by_id=payments)
        _supersede(campus, run, findings, internal=True)
    return run


def resolve_item(item: FeeReconciliationItem, *, action: str, note: str, user) -> str:
    """Apply ONE reviewed fix. Returns a short human message; raises FinanceError when unsafe.
    Nothing here runs without a person pressing the button."""
    if item.resolution != FeeReconciliationItem.Resolution.OPEN:
        raise FinanceError("This item is already closed.", "already_closed")
    note = (note or "").strip()
    message = ""
    now = timezone.now()

    if action in ("ignore", "resolve"):
        if len(note) < 3:
            raise FinanceError("Add a short note explaining why.", "note_required")
        message = "Marked as " + ("ignored." if action == "ignore" else "resolved.")
        status = FeeReconciliationItem.Resolution.IGNORED if action == "ignore" else FeeReconciliationItem.Resolution.RESOLVED

    elif action == "settle":
        if item.category != rc.PENDING_BUT_CAPTURED or not item.payment_id or item.gateway_amount is None:
            raise FinanceError("Only 'captured at Razorpay but pending here' items can be settled.", "not_settlable")
        from user_profile.payments import to_paise

        from . import fee_gateway

        payment = item.payment
        outcome, _ = fee_gateway.settle_online_payment(
            order_id=item.gateway_order_id or payment.gateway_reference, payment_id=item.gateway_payment_id,
            method=item.gateway_method, amount_paise=to_paise(item.gateway_amount), currency="INR",
        )
        if outcome == fee_gateway.PAID:
            message = "Payment settled and the invoice updated."
        elif outcome == fee_gateway.ALREADY:
            message = "It was already settled."
        elif outcome == fee_gateway.MISMATCH:
            raise FinanceError("Amount does not match our order — not settled. Review it in Razorpay.", "mismatch")
        else:
            raise FinanceError("This payment can no longer be settled (refunded or failed here).", outcome)
        status = FeeReconciliationItem.Resolution.RESOLVED

    elif action == "mark_refunded":
        if item.category != rc.REFUNDED_AT_GATEWAY or not item.payment_id:
            raise FinanceError("Only 'refunded at Razorpay' items can be marked refunded.", "not_refundable")
        with transaction.atomic():
            payment = FeePayment.objects.select_for_update().get(pk=item.payment_id)
            if payment.status == FeePayment.Status.SUCCESS:
                payment.status = FeePayment.Status.REFUNDED
                payment.save(update_fields=["status"])
                invoice = FeeInvoice.objects.select_for_update().get(pk=payment.invoice_id)
                invoice.recompute_status()
            elif payment.status != FeePayment.Status.REFUNDED:
                raise FinanceError(f"Payment is '{payment.status}', not paid — nothing to refund.", "bad_state")
        message = "Payment marked refunded and the invoice re-opened."
        status = FeeReconciliationItem.Resolution.RESOLVED

    elif action == "recompute":
        invoice_id = item.invoice_id or (item.payment.invoice_id if item.payment_id else None)
        if not invoice_id:
            raise FinanceError("No invoice linked to this item.", "no_invoice")
        refresh_invoice(FeeInvoice.objects.get(pk=invoice_id))
        message = "Invoice recomputed."
        status = FeeReconciliationItem.Resolution.RESOLVED
    else:
        raise FinanceError("Unknown action.", "bad_action")

    item.resolution, item.resolution_action = status, action
    item.resolution_note, item.resolved_by, item.resolved_at = note[:255], user, now
    item.save(update_fields=["resolution", "resolution_action", "resolution_note", "resolved_by", "resolved_at"])
    return message
