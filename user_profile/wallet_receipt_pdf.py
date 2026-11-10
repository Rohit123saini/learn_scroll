"""
user_profile/wallet_receipt_pdf.py - S9: coin purchase receipt + withdrawal slip as a one-page A4 PDF.

`reportlab` optional hai aur lazily import hota hai (campus/receipt_pdf.py jaisa contract): na ho to `PdfUnavailable`,
view use 501 bana deta hai. Is file me sirf drawing + masking hai - koi DB query nahi, isliye pure-function jaisa test
ho sakta hai.

Privacy: payout details (UPI id / bank account) aur payment method detail yahan hamesha MASKED aate hain.
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal
from typing import Optional


class PdfUnavailable(RuntimeError):
    """reportlab is not installed."""


# --------------------------------------------------------------------------------------------------
# masking + receipt numbers
# --------------------------------------------------------------------------------------------------
def mask_upi(vpa: str) -> str:
    """abc.def@okbank -> ab***@okbank (handle ke pehle 2 akshar, bank ka hissa poora)."""
    vpa = (vpa or "").strip()
    if "@" not in vpa:
        return "***" if vpa else ""
    name, bank = vpa.split("@", 1)
    return f"{name[:2]}***@{bank}"


def mask_account(number: str) -> str:
    digits = "".join(ch for ch in str(number or "") if ch.isalnum())
    return f"XXXX{digits[-4:]}" if len(digits) >= 4 else ("XXXX" if digits else "")


def payout_summary(method: str, details: Optional[dict]) -> str:
    """Withdrawal payout ka masked, ek-line naam: "UPI ab***@okbank" / "Bank XXXX1234"."""
    details = details or {}
    method = (method or "").lower()
    if method == "upi":
        masked = mask_upi(str(details.get("upi_id") or details.get("vpa") or ""))
        return f"UPI {masked}".strip()
    if method == "bank_transfer":
        masked = mask_account(str(details.get("account_number") or details.get("account") or ""))
        return f"Bank {masked}".strip()
    return method.replace("_", " ").title()


def purchase_receipt_no(purchase_id: int, created_at: datetime) -> str:
    return f"LS-R-{created_at:%Y%m}-{int(purchase_id):06d}"


def withdrawal_slip_no(withdrawal_id: int, created_at: datetime) -> str:
    return f"LS-W-{created_at:%Y%m}-{int(withdrawal_id):06d}"


# --------------------------------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------------------------------
def _render(*, title: str, doc_no: str, doc_date: datetime, user_name: str, rows: list, amount_label: str,
            amount_text: str, sub_lines: list, footer: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable wallet receipt PDFs.") from exc

    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"{title} {doc_no}")

    ink = colors.HexColor("#2A3FC7")
    muted = colors.HexColor("#5A617A")
    dark = colors.HexColor("#171B2D")
    rule = colors.HexColor("#D8DBEA")

    pdf.setStrokeColor(ink)
    pdf.setLineWidth(2)
    pdf.rect(28, 28, width - 56, height - 56)

    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(width / 2, height - 80, "LearnScroll")
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(width / 2, height - 98, "Wallet")

    pdf.setStrokeColor(rule)
    pdf.setLineWidth(0.8)
    pdf.line(60, height - 115, width - 60, height - 115)

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2, height - 145, title)

    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(muted)
    pdf.drawString(60, height - 175, f"No: {doc_no}")
    pdf.drawRightString(width - 60, height - 175, f"Date: {doc_date:%d %B %Y, %I:%M %p}")

    y = height - 215
    rows = [("Name", user_name or "-"), *rows]
    for label, value in rows:
        if value in (None, ""):
            continue
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(60, y, f"{label}:")
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(200, y, str(value)[:70])
        y -= 24

    y -= 10
    pdf.setStrokeColor(rule)
    pdf.line(60, y, width - 60, y)
    y -= 30
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, amount_label)
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - 60, y, amount_text)

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 9)
    for line in sub_lines:
        y -= 16
        pdf.drawString(60, y, line)

    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 60, footer)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def render_purchase_receipt(*, purchase_id: int, user_name: str, created_at: datetime, paid_at: Optional[datetime],
                            order_id: str, payment_id: str, gateway: str, method_text: str, coins: int,
                            amount: Decimal, status: str, refund_status: str = "",
                            refund_amount: Optional[Decimal] = None) -> bytes:
    rows = [
        ("Item", f"{coins} LearnScroll coins"),
        ("Order ID", order_id),
        ("Payment ID", payment_id),
        ("Gateway", (gateway or "").title()),
        ("Paid with", method_text),
        ("Status", status.title()),
    ]
    sub = ["Coins are credited to your wallet once the payment is confirmed by the gateway."]
    if refund_status:
        shown = f" (Rs. {Decimal(refund_amount):.2f})" if refund_amount is not None else ""
        rows.append(("Refund", f"{refund_status.title()}{shown}"))
    return _render(
        title="COIN PURCHASE RECEIPT",
        doc_no=purchase_receipt_no(purchase_id, created_at),
        doc_date=paid_at or created_at,
        user_name=user_name,
        rows=rows,
        amount_label="Amount Paid",
        amount_text=f"Rs. {Decimal(amount):.2f}",
        sub_lines=sub,
        footer="This is a system-generated receipt and does not require a signature.",
    )


def render_withdrawal_slip(*, withdrawal_id: int, user_name: str, created_at: datetime, completed_at: Optional[datetime],
                           coins: int, amount_inr: Decimal, payout_text: str, status: str) -> bytes:
    rows = [
        ("Coins withdrawn", f"{coins}"),
        ("Payout to", payout_text),
        ("Status", status.title()),
        ("Requested on", f"{created_at:%d %B %Y}"),
    ]
    return _render(
        title="COIN WITHDRAWAL SLIP",
        doc_no=withdrawal_slip_no(withdrawal_id, created_at),
        doc_date=completed_at or created_at,
        user_name=user_name,
        rows=rows,
        amount_label="Payout Amount",
        amount_text=f"Rs. {Decimal(amount_inr):.2f}",
        sub_lines=["Bank / UPI credit time depends on your bank."],
        footer="This is a system-generated slip and does not require a signature.",
    )


def spend_receipt_no(prefix: str, purchase_id: int, when: datetime) -> str:
    """LS-P (class pass) / LS-T (test series): same shape as the coin receipt number."""
    return f"{prefix}-{when:%Y%m}-{int(purchase_id):06d}"


def render_spend_receipt(*, doc_no: str, title: str, user_name: str, when: datetime, item: str, detail_rows: list,
                         coins: int, status: str) -> bytes:
    """P-G2: receipt for something paid WITH coins (class pass / test series). Coins are a closed in-app currency, so no
    GST line here: the INR invoice is the coin top-up receipt; this one says what the coins were spent on."""
    rows = [("Item", item), *detail_rows, ("Status", (status or "").title())]
    return _render(
        title=title, doc_no=doc_no, doc_date=when, user_name=user_name, rows=rows,
        amount_label="Coins spent", amount_text=f"{int(coins)} coins",
        sub_lines=["Paid from your LearnScroll wallet. The coin top-up receipt shows the rupee amount you paid."],
        footer="This is a system-generated receipt and does not require a signature.",
    )
