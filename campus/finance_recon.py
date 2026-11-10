# campus/finance_recon.py
"""
FINANCE — payment-gateway RECONCILIATION engine (pure Python, no Django imports).

Question it answers: "does what Razorpay says it collected match what our FeePayment rows
say?" Two inputs:
  * OurPayment      — our ONLINE FeePayment rows (loaded by campus/finance.py)
  * GatewayPayment  — Razorpay's side: parsed from the dashboard CSV report, or from the
                      Razorpay payments API (paise)

Nothing here writes anything. It returns `Finding`s; a human (admin / principal) then
applies the safe fixes one by one (campus/finance.py) — money is never auto-changed.

Categories (see CATEGORY_META for severity + suggested action)
  matched               same order, same amount, captured on both sides
  pending_but_captured  Razorpay captured it, our row is still PENDING   -> settle
  gateway_only          Razorpay captured an order we have no row for    -> review
  missing_at_gateway    our row is SUCCESS, Razorpay has no captured pay -> review
  amount_mismatch       both captured, amounts differ                    -> review
  refunded_at_gateway   Razorpay fully refunded, our row still SUCCESS   -> mark_refunded
  partial_refund        Razorpay refunded part of it                     -> review
  refund_not_at_gateway our row REFUNDED but Razorpay never refunded     -> review (money not returned!)
  failed_but_success    our row SUCCESS but Razorpay says failed         -> review
  duplicate_capture     two captured payments on one order (double pay)  -> review (refund the extra)
  stale_pending         online PENDING for long, no payment at gateway   -> info (abandoned checkout)
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

MATCHED = "matched"
PENDING_BUT_CAPTURED = "pending_but_captured"
GATEWAY_ONLY = "gateway_only"
MISSING_AT_GATEWAY = "missing_at_gateway"
AMOUNT_MISMATCH = "amount_mismatch"
REFUNDED_AT_GATEWAY = "refunded_at_gateway"
PARTIAL_REFUND = "partial_refund"
REFUND_NOT_AT_GATEWAY = "refund_not_at_gateway"
FAILED_BUT_SUCCESS = "failed_but_success"
DUPLICATE_CAPTURE = "duplicate_capture"
STALE_PENDING = "stale_pending"

# internal (no gateway file needed) — produced by campus/finance.py
OVERPAID_INVOICE = "overpaid_invoice"
STATUS_DRIFT = "status_drift"
NO_TXN_ID = "no_transaction_id"

# severity, default suggested action
CATEGORY_META = {
    MATCHED: ("ok", "none"),
    PENDING_BUT_CAPTURED: ("error", "settle"),
    GATEWAY_ONLY: ("error", "review"),
    MISSING_AT_GATEWAY: ("error", "review"),
    AMOUNT_MISMATCH: ("error", "review"),
    REFUNDED_AT_GATEWAY: ("warn", "mark_refunded"),
    PARTIAL_REFUND: ("warn", "review"),
    REFUND_NOT_AT_GATEWAY: ("error", "review"),
    FAILED_BUT_SUCCESS: ("error", "review"),
    DUPLICATE_CAPTURE: ("error", "review"),
    STALE_PENDING: ("info", "none"),
    OVERPAID_INVOICE: ("warn", "review"),
    STATUS_DRIFT: ("warn", "recompute"),
    NO_TXN_ID: ("warn", "review"),
}
ALL_CATEGORIES = tuple(CATEGORY_META)

CAPTURED = "captured"
REFUNDED = "refunded"
FAILED = "failed"
AUTHORIZED = "authorized"
CREATED = "created"


def money(v) -> Decimal:
    if v is None or str(v).strip() == "":
        return ZERO
    try:
        return Decimal(str(v).strip().replace(",", "")).quantize(CENT)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Not a number: {v!r}")


# ======================================================================================
# data shapes
# ======================================================================================
@dataclass(frozen=True)
class OurPayment:
    payment_id: str
    order_id: str                 # FeePayment.gateway_reference
    gateway_payment_id: str       # FeePayment.gateway_payment_id ('' while PENDING)
    amount: Decimal
    status: str                   # pending | success | failed | refunded
    when: Optional[date]          # paid_at / created_at date
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class GatewayPayment:
    payment_id: str
    order_id: str
    amount: Decimal               # rupees
    status: str                   # captured | authorized | failed | refunded | created
    amount_refunded: Decimal = ZERO
    currency: str = "INR"
    method: str = ""
    fee: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    when: Optional[date] = None
    settlement_id: str = ""
    kind: str = ""                # notes.kind from the API ('campus_fee'), '' when unknown (CSV)
    row: int = 0                  # CSV line number, for messages


@dataclass
class Finding:
    category: str
    severity: str
    suggested_action: str
    detail: str = ""
    payment_id: str = ""
    order_id: str = ""
    gateway_payment_id: str = ""
    our_amount: Optional[Decimal] = None
    gateway_amount: Optional[Decimal] = None
    our_status: str = ""
    gateway_status: str = ""
    fee: Optional[Decimal] = None
    tax: Optional[Decimal] = None
    settlement_id: str = ""
    method: str = ""
    invoice_id: str = ""          # internal checks only


def _finding(category: str, *, detail: str = "", ours: Optional[OurPayment] = None,
             gw: Optional[GatewayPayment] = None, action: Optional[str] = None) -> Finding:
    severity, default_action = CATEGORY_META[category]
    return Finding(
        category=category, severity=severity, suggested_action=action or default_action, detail=detail[:300],
        payment_id=ours.payment_id if ours else "",
        order_id=(ours.order_id if ours else "") or (gw.order_id if gw else ""),
        gateway_payment_id=(gw.payment_id if gw else "") or (ours.gateway_payment_id if ours else ""),
        our_amount=ours.amount if ours else None,
        gateway_amount=gw.amount if gw else None,
        our_status=ours.status if ours else "",
        gateway_status=gw.status if gw else "",
        fee=gw.fee if gw else None, tax=gw.tax if gw else None,
        settlement_id=gw.settlement_id if gw else "", method=gw.method if gw else "",
    )


# ======================================================================================
# the matcher
# ======================================================================================
def reconcile(
    ours: Iterable[OurPayment],
    gateway: Iterable[GatewayPayment],
    *,
    window: Optional[tuple[date, date]] = None,
    ignore_order_ids: Iterable[str] = (),
    stale_pending_hours: int = 24,
    now: Optional[datetime] = None,
) -> list[Finding]:
    """Compare both sides. `window` = (first_day, last_day) the gateway report covers — our
    SUCCESS rows outside it are NOT expected in the report, so they are never flagged
    `missing_at_gateway`. `ignore_order_ids` = orders that are not fee payments (e.g. coin
    wallet top-ups on the same Razorpay account)."""
    ours = list(ours)
    gateway = list(gateway)
    ignore = {i for i in ignore_order_ids if i}
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    by_order: dict[str, OurPayment] = {o.order_id: o for o in ours if o.order_id}
    by_pay: dict[str, OurPayment] = {o.gateway_payment_id: o for o in ours if o.gateway_payment_id}

    findings: list[Finding] = []
    seen_ours: set[str] = set()

    # captured payments per order — to catch a double payment on one order
    captured_per_order: dict[str, list[GatewayPayment]] = {}
    for g in gateway:
        if g.status in (CAPTURED, REFUNDED) and g.order_id:
            captured_per_order.setdefault(g.order_id, []).append(g)

    flagged_dupes: set[str] = set()

    for g in gateway:
        if g.order_id and g.order_id in ignore:
            continue
        o = by_pay.get(g.payment_id) or (by_order.get(g.order_id) if g.order_id else None)

        if o is None:
            if g.status in (CAPTURED, REFUNDED):
                known_fee = g.kind == "campus_fee"
                findings.append(_finding(
                    GATEWAY_ONLY, gw=g,
                    detail=("Fee payment captured at Razorpay but we have no record of it."
                            if known_fee else
                            "Captured at Razorpay with no matching fee payment here (could be a non-fee order)."),
                ))
            continue
        seen_ours.add(o.payment_id)

        # --- duplicate capture on the same order -------------------------------------
        group = captured_per_order.get(g.order_id, []) if g.order_id else []
        if len(group) > 1 and g.order_id not in flagged_dupes:
            flagged_dupes.add(g.order_id)
            findings.append(_finding(
                DUPLICATE_CAPTURE, ours=o, gw=g,
                detail=f"{len(group)} captured payments on one order — refund the extra from the Razorpay dashboard.",
            ))

        if g.status == FAILED:
            if o.status == "success":
                findings.append(_finding(FAILED_BUT_SUCCESS, ours=o, gw=g,
                                         detail="We marked it paid but Razorpay shows the payment FAILED."))
            continue
        if g.status in (AUTHORIZED, CREATED):
            if o.status == "success":
                findings.append(_finding(MISSING_AT_GATEWAY, ours=o, gw=g,
                                         detail=f"We marked it paid but Razorpay shows it only '{g.status}' (not captured)."))
            continue

        # g.status is captured or refunded from here
        fully_refunded = g.status == REFUNDED or (g.amount_refunded > ZERO and g.amount_refunded >= g.amount)
        partly_refunded = ZERO < g.amount_refunded < g.amount

        if o.status == "pending":
            if g.amount != o.amount:
                findings.append(_finding(AMOUNT_MISMATCH, ours=o, gw=g,
                                         detail=f"Razorpay captured {g.amount} but our pending order is {o.amount}; not safe to settle."))
            elif fully_refunded:
                findings.append(_finding(REFUNDED_AT_GATEWAY, ours=o, gw=g, action="review",
                                         detail="Captured then fully refunded at Razorpay; our row never settled. Nothing to collect."))
            else:
                findings.append(_finding(PENDING_BUT_CAPTURED, ours=o, gw=g,
                                         detail="Razorpay captured this payment but our row is still pending — settle it."))
            continue

        if o.status == "success":
            if fully_refunded:
                findings.append(_finding(REFUNDED_AT_GATEWAY, ours=o, gw=g,
                                         detail="Fully refunded at Razorpay but still counted as paid here."))
            elif g.amount != o.amount:
                findings.append(_finding(AMOUNT_MISMATCH, ours=o, gw=g,
                                         detail=f"Amount differs: ours {o.amount}, Razorpay {g.amount}."))
            elif partly_refunded:
                findings.append(_finding(PARTIAL_REFUND, ours=o, gw=g,
                                         detail=f"Razorpay refunded {g.amount_refunded} of {g.amount}; adjust the invoice by hand."))
            else:
                findings.append(_finding(MATCHED, ours=o, gw=g))
            continue

        if o.status == "refunded":
            if not fully_refunded:
                findings.append(_finding(REFUND_NOT_AT_GATEWAY, ours=o, gw=g,
                                         detail="Marked refunded here but Razorpay still holds the money — the parent has not been refunded."))
            else:
                findings.append(_finding(MATCHED, ours=o, gw=g, detail="Refunded on both sides."))
            continue
        # our status 'failed' but Razorpay captured -> money taken, nothing credited
        findings.append(_finding(GATEWAY_ONLY, ours=o, gw=g,
                                 detail=f"Our row is '{o.status}' but Razorpay captured {g.amount} — money taken, fee not credited."))

    # --- our online rows Razorpay never mentioned ------------------------------------
    for o in ours:
        if o.payment_id in seen_ours:
            continue
        if o.status == "success":
            in_window = window is None or o.when is None or (window[0] <= o.when <= window[1])
            if in_window:
                findings.append(_finding(MISSING_AT_GATEWAY, ours=o,
                                         detail="Marked paid here but there is no such payment in the Razorpay report."))
        elif o.status == "refunded":
            findings.append(_finding(REFUND_NOT_AT_GATEWAY, ours=o,
                                     detail="Marked refunded here but Razorpay has no matching payment in the report."))
        elif o.status == "pending" and o.created_at is not None:
            if now - o.created_at.replace(tzinfo=None) > timedelta(hours=stale_pending_hours):
                findings.append(_finding(STALE_PENDING, ours=o,
                                         detail="Checkout started but never paid (abandoned)."))
    return findings


def _q(d: Decimal) -> Decimal:
    return Decimal(d).quantize(CENT)


def summarize(findings: Iterable[Finding], gateway: Iterable[GatewayPayment] = ()) -> dict:
    """Counts per category + the money picture of the gateway file."""
    counts: dict[str, int] = {}
    for f in findings:
        counts[f.category] = counts.get(f.category, 0) + 1
    gw = [g for g in gateway if g.status in (CAPTURED, REFUNDED)]
    gross = sum((g.amount for g in gw), ZERO)
    refunded = sum((g.amount if g.status == REFUNDED else g.amount_refunded for g in gw), ZERO)
    fees = sum((g.fee or ZERO) for g in gw)
    taxes = sum((g.tax or ZERO) for g in gw)
    open_issues = sum(n for c, n in counts.items() if CATEGORY_META[c][0] in ("warn", "error"))
    return {
        "counts": counts,
        "open_issues": open_issues,
        "matched": counts.get(MATCHED, 0),
        "gateway_rows": len(gw),
        "gross_captured": str(_q(gross)),
        "refunded": str(_q(refunded)),
        "gateway_fee": str(_q(fees)),
        "gateway_tax": str(_q(taxes)),
        "net_expected_settlement": str(_q(gross - refunded - fees - taxes)),
    }


# ======================================================================================
# parsers: Razorpay CSV report / API payload -> GatewayPayment
# ======================================================================================
MAX_ROWS = 20_000
MAX_BYTES = 8_000_000

_ALIASES = {
    "payment_id": {"id", "paymentid", "payment id", "razorpay payment id", "razorpay_payment_id"},
    "order_id": {"orderid", "order id", "razorpay order id", "razorpay_order_id", "order_id"},
    "amount": {"amount", "payment amount", "paymentamount", "amount (inr)"},
    "currency": {"currency"},
    "status": {"status", "payment status", "paymentstatus"},
    "refunded": {"amountrefunded", "amount refunded", "amount_refunded", "refund amount"},
    "refund_status": {"refundstatus", "refund status", "refund_status"},
    "method": {"method", "payment method", "paymentmethod"},
    "fee": {"fee", "fees", "razorpay fee", "razorpay_fee"},
    "tax": {"tax", "gst", "service tax"},
    "created": {"createdat", "created at", "created_at", "date", "payment date", "captured at"},
    "settlement": {"settlementid", "settlement id", "settlement_id", "settled by", "utr"},
    "captured": {"captured"},
    "notes_kind": {"notes[kind]", "notes.kind", "kind"},
}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", (h or "").strip().lstrip("\ufeff")).lower()


def _map_headers(headers: list[str]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, h in enumerate(headers):
        n = _norm_header(h)
        for key, names in _ALIASES.items():
            if n in names and key not in idx:
                idx[key] = i
    return idx


_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    "%d/%m/%Y", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%d %b %Y %H:%M:%S", "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%d %b %Y, %I:%M %p",
)


def parse_date(text) -> Optional[date]:
    s = (str(text) if text is not None else "").strip()
    if not s:
        return None
    s = s.replace("Z", "").split("+")[0].strip()
    if re.fullmatch(r"\d{9,13}", s):  # unix seconds / millis
        n = int(s)
        n = n / 1000 if n > 10**11 else n
        return datetime.fromtimestamp(n, timezone.utc).date()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _status(raw: str, captured: str, refund_status: str, refunded: Decimal, amount: Decimal) -> str:
    s = (raw or "").strip().lower()
    if s in {"captured", "success", "successful", "paid", "settled"}:
        s = CAPTURED
    elif s in {"refunded", "fully refunded"}:
        s = REFUNDED
    elif s in {"failed", "failure", "cancelled", "canceled"}:
        s = FAILED
    elif s in {"authorized", "authorised"}:
        s = AUTHORIZED
    elif s in {"created", "pending"}:
        s = CREATED
    elif s == "":
        s = CAPTURED if str(captured).strip().lower() in {"true", "1", "yes"} else CREATED
    rs = (refund_status or "").strip().lower()
    if s == CAPTURED and (rs in {"full", "refunded"} or (refunded > ZERO and refunded >= amount)):
        s = REFUNDED
    return s


def parse_razorpay_csv(text: str, *, amount_unit: str = "rupees") -> tuple[list[GatewayPayment], list[tuple[int, str]]]:
    """Razorpay dashboard payments report -> (rows, errors). Header names are matched loosely
    (see _ALIASES). `amount_unit` = 'rupees' (dashboard export) or 'paise' (API dump).
    Errors carry the CSV line number; bad rows are skipped, never guessed."""
    if amount_unit not in ("rupees", "paise"):
        raise ValueError("amount_unit must be 'rupees' or 'paise'.")
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        return [], [(0, "File is too large.")]
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        headers = next(reader)
    except StopIteration:
        return [], [(0, "The file is empty.")]
    idx = _map_headers(headers)
    missing = [k for k in ("payment_id", "amount", "status") if k not in idx]
    if missing:
        return [], [(1, "Missing column(s): " + ", ".join(missing) +
                     ". Use the Razorpay 'Payments' report (needs id, amount, status).")]

    def col(row, key):
        i = idx.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    divisor = Decimal(100) if amount_unit == "paise" else Decimal(1)
    rows: list[GatewayPayment] = []
    errors: list[tuple[int, str]] = []
    seen: set[str] = set()
    for n, row in enumerate(reader, start=2):
        if not any(c.strip() for c in row):
            continue
        if len(rows) + len(errors) >= MAX_ROWS:
            errors.append((n, f"Stopped after {MAX_ROWS} rows."))
            break
        try:
            pid = col(row, "payment_id")
            if not pid:
                raise ValueError("Payment id is empty.")
            if pid in seen:
                raise ValueError(f"Duplicate payment id {pid} in the file.")
            amount = (money(col(row, "amount")) / divisor).quantize(CENT)
            refunded = (money(col(row, "refunded")) / divisor).quantize(CENT)
            fee = col(row, "fee")
            tax = col(row, "tax")
            rows.append(GatewayPayment(
                payment_id=pid, order_id=col(row, "order_id"), amount=amount,
                status=_status(col(row, "status"), col(row, "captured"), col(row, "refund_status"), refunded, amount),
                amount_refunded=refunded, currency=(col(row, "currency") or "INR").upper(),
                method=col(row, "method").lower(),
                fee=(money(fee) / divisor).quantize(CENT) if fee else None,
                tax=(money(tax) / divisor).quantize(CENT) if tax else None,
                when=parse_date(col(row, "created")), settlement_id=col(row, "settlement"),
                kind=col(row, "notes_kind"), row=n,
            ))
            seen.add(pid)
        except ValueError as exc:
            errors.append((n, str(exc)))
    return rows, errors


def from_api_items(items: Iterable[dict]) -> list[GatewayPayment]:
    """Razorpay `GET /v1/payments` items (amounts in PAISE) -> GatewayPayment (rupees)."""
    out = []
    for n, d in enumerate(items, start=1):
        pid = d.get("id")
        if not pid:
            continue
        amount = (Decimal(int(d.get("amount") or 0)) / 100).quantize(CENT)
        refunded = (Decimal(int(d.get("amount_refunded") or 0)) / 100).quantize(CENT)
        status = _status(str(d.get("status") or ""), str(d.get("captured") or ""),
                         str(d.get("refund_status") or ""), refunded, amount)
        notes = d.get("notes") if isinstance(d.get("notes"), dict) else {}
        created = d.get("created_at")
        out.append(GatewayPayment(
            payment_id=pid, order_id=d.get("order_id") or "", amount=amount, status=status,
            amount_refunded=refunded, currency=(d.get("currency") or "INR").upper(),
            method=(d.get("method") or "").lower(),
            fee=(Decimal(int(d["fee"])) / 100).quantize(CENT) if d.get("fee") is not None else None,
            tax=(Decimal(int(d["tax"])) / 100).quantize(CENT) if d.get("tax") is not None else None,
            when=datetime.fromtimestamp(int(created), timezone.utc).date() if created else None,
            kind=str(notes.get("kind") or ""), row=n,
        ))
    return out
