# campus/receipt_pdf.py
"""
Renders a `FeePayment` to a one-page A4 PDF receipt.

Task 5 (Fee Invoice PDF Receipt), subtask 1/4 — PDF generation only. The
download endpoint (subtask 2), auto-email-on-payment (subtask 3), and the
Flutter download button (subtask 4) build on top of this.

`reportlab` is an OPTIONAL dependency, imported lazily here — same contract
as `testseries/certificate_pdf.py`, which this module deliberately mirrors
so both PDF features live or die together on the same `pip install
reportlab`. The view turns `PdfUnavailable` into HTTP 501.

Campus has no logo/address field on the model (see `campus.models.Campus`)
so "campus letterhead" here is a text header (name + type) rather than an
image — nothing to fetch, nothing to fail if unset.
"""
from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal


class PdfUnavailable(RuntimeError):
    """reportlab is not installed."""


def _display_name(user) -> str:
    """Public-facing name for a receipt (never the email) — same shape as
    `testseries.serializers.display_name`, duplicated locally rather than
    imported so `campus` doesn't take a new cross-app dependency on
    `testseries` for one helper."""
    if user is None:
        return "—"
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "—").strip()


def render_pdf(
    *,
    receipt_no: str,
    campus_name: str,
    campus_type: str,
    student_name: str,
    fee_title: str,
    invoice_id: str,
    amount: Decimal,
    amount_due: Decimal,
    amount_paid_total: Decimal,
    payment_mode: str,
    payer_role: str,
    paid_by,
    recorded_by,
    status: str,
    notes: str,
    paid_at: datetime,
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable fee receipt PDFs.") from exc

    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Fee Receipt {receipt_no}")

    ink = colors.HexColor("#2A3FC7")
    muted = colors.HexColor("#5A617A")
    dark = colors.HexColor("#171B2D")

    # Border, same visual language as certificate_pdf.py.
    pdf.setStrokeColor(ink)
    pdf.setLineWidth(2)
    pdf.rect(28, 28, width - 56, height - 56)

    # Letterhead
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(width / 2, height - 80, campus_name or "—")
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(width / 2, height - 98, (campus_type or "").title())

    pdf.setStrokeColor(colors.HexColor("#D8DBEA"))
    pdf.setLineWidth(0.8)
    pdf.line(60, height - 115, width - 60, height - 115)

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2, height - 145, "FEE PAYMENT RECEIPT")

    # Meta row: receipt no / date
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(muted)
    pdf.drawString(60, height - 175, f"Receipt No: {receipt_no}")
    pdf.drawRightString(width - 60, height - 175, f"Date: {paid_at:%d %B %Y, %I:%M %p}")

    # Body — label/value rows
    rows = [
        ("Student", student_name),
        ("Fee", fee_title),
        ("Invoice ID", invoice_id),
        ("Paid By", f"{_display_name(paid_by)} ({payer_role.title()})" if paid_by else payer_role.title()),
        ("Payment Mode", payment_mode.replace("_", " ").title()),
        ("Status", status.title()),
    ]
    if recorded_by is not None:
        rows.append(("Recorded By (Office)", _display_name(recorded_by)))
    if notes:
        rows.append(("Notes", notes))

    y = height - 215
    pdf.setFont("Helvetica-Bold", 10)
    for label, value in rows:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(60, y, f"{label}:")
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(200, y, str(value))
        y -= 24

    # Amount box
    y -= 10
    pdf.setStrokeColor(colors.HexColor("#D8DBEA"))
    pdf.setLineWidth(0.8)
    pdf.line(60, y, width - 60, y)
    y -= 30

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Amount Paid (this receipt)")
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawRightString(width - 60, y, f"Rs. {amount:.2f}")
    y -= 22

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(60, y, f"Total paid on invoice so far: Rs. {amount_paid_total:.2f} / Rs. {amount_due:.2f} due")

    # Footer
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 60, "This is a system-generated receipt and does not require a signature.")

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
