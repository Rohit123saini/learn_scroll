# campus/invoice_pdf.py
"""
FINANCE — renders a FeeInvoice to an A4 PDF *invoice* (the demand note: fee, concession,
late fine, payable, paid, balance + the payment history). The receipt for ONE payment stays in
receipt_pdf.py; this is the whole-invoice view the office/parent keeps.

`reportlab` is optional and imported lazily — same contract (and same PdfUnavailable error,
turned into HTTP 501 by the view) as receipt_pdf.py. Plain data in, bytes out: no ORM here.
"""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Sequence

from .receipt_pdf import PdfUnavailable


def _rs(value) -> str:
    return f"Rs. {Decimal(str(value)):,.2f}"


def render_invoice_pdf(
    *,
    invoice_no: str,
    campus_name: str,
    campus_type: str,
    student_name: str,
    roll_number: str,
    class_name: str,
    section_name: str,
    session_name: str,
    fee_title: str,
    fee_category: str,
    issued_on: date,
    due_date: date,
    base_amount: Decimal,
    concession_amount: Decimal,
    fine_amount: Decimal,
    amount_due: Decimal,
    amount_paid: Decimal,
    status: str,
    concession_names: Sequence[str] = (),
    fine_note: str = "",
    payments: Sequence[tuple[date, str, str, Decimal]] = (),  # (date, mode, reference, amount)
) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable fee invoice PDFs.") from exc

    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Fee Invoice {invoice_no or ''}".strip())
    ink, muted, dark = colors.HexColor("#2A3FC7"), colors.HexColor("#5A617A"), colors.HexColor("#171B2D")
    rule = colors.HexColor("#D8DBEA")

    pdf.setStrokeColor(ink)
    pdf.setLineWidth(2)
    pdf.rect(28, 28, width - 56, height - 56)

    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(width / 2, height - 78, campus_name or "—")
    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 11)
    pdf.drawCentredString(width / 2, height - 96, (campus_type or "").title())
    pdf.setStrokeColor(rule)
    pdf.setLineWidth(0.8)
    pdf.line(60, height - 112, width - 60, height - 112)

    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawCentredString(width / 2, height - 140, "FEE INVOICE")
    pdf.setFont("Helvetica", 10)
    pdf.setFillColor(muted)
    pdf.drawString(60, height - 166, f"Invoice No: {invoice_no or '—'}")
    pdf.drawRightString(width - 60, height - 166, f"Issued: {issued_on:%d %B %Y}")

    y = height - 196
    meta = [
        ("Student", student_name), ("Class / Section", f"{class_name} / {section_name}"),
        ("Roll No", roll_number or "—"), ("Session", session_name),
        ("Fee", f"{fee_title} ({fee_category.title()})"), ("Due date", f"{due_date:%d %B %Y}"),
        ("Status", status.title()),
    ]
    for label, value in meta:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(60, y, f"{label}:")
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica", 10)
        pdf.drawString(190, y, str(value)[:70])
        y -= 20

    # Amount table
    y -= 8
    pdf.setStrokeColor(rule)
    pdf.line(60, y, width - 60, y)
    y -= 22
    lines = [("Fee amount", _rs(base_amount), dark)]
    if Decimal(str(concession_amount)) > 0:
        label = "Concession" + (f" ({', '.join(concession_names)[:40]})" if concession_names else "")
        lines.append((label, "- " + _rs(concession_amount), colors.HexColor("#1B7F3B")))
    if Decimal(str(fine_amount)) > 0:
        lines.append(("Late fine" + (f" ({fine_note})" if fine_note else ""), "+ " + _rs(fine_amount), colors.HexColor("#B3261E")))
    for label, value, colour in lines:
        pdf.setFillColor(colour)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(60, y, label)
        pdf.drawRightString(width - 60, y, value)
        y -= 20
    pdf.setStrokeColor(rule)
    pdf.line(60, y + 8, width - 60, y + 8)
    y -= 10
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    pdf.drawString(60, y, "Total payable")
    pdf.drawRightString(width - 60, y, _rs(amount_due))
    y -= 20
    pdf.setFont("Helvetica", 11)
    pdf.drawString(60, y, "Paid so far")
    pdf.drawRightString(width - 60, y, _rs(amount_paid))
    y -= 22
    balance = max(Decimal(str(amount_due)) - Decimal(str(amount_paid)), Decimal("0"))
    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(60, y, "Balance due")
    pdf.drawRightString(width - 60, y, _rs(balance))

    # Payment history
    if payments:
        y -= 34
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(60, y, "Payments received")
        y -= 8
        pdf.setStrokeColor(rule)
        pdf.line(60, y, width - 60, y)
        y -= 16
        pdf.setFont("Helvetica", 9)
        for when, mode, ref, amount in list(payments)[:14]:
            pdf.setFillColor(muted)
            pdf.drawString(60, y, f"{when:%d %b %Y}")
            pdf.drawString(150, y, mode.replace("_", " ").title()[:18])
            pdf.drawString(260, y, (ref or "")[:34])
            pdf.setFillColor(dark)
            pdf.drawRightString(width - 60, y, _rs(amount))
            y -= 15
        if len(payments) > 14:
            pdf.setFillColor(muted)
            pdf.drawString(60, y, f"… and {len(payments) - 14} more payment(s)")

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica", 8)
    pdf.drawCentredString(width / 2, 60, "This is a system-generated invoice and does not require a signature.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
