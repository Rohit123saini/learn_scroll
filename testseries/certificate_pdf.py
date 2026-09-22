# testseries/certificate_pdf.py
"""
Renders a `TestCertificate` to a one-page A4-landscape PDF.

`reportlab` is an OPTIONAL dependency: it is imported lazily inside
`render_pdf()`, so the rest of the app (and the JSON certificate endpoints)
work without it. The view turns `PdfUnavailable` into HTTP 501 with a clear
message. Install with `pip install reportlab`.
"""
from __future__ import annotations

import io
from datetime import datetime


class PdfUnavailable(RuntimeError):
    """reportlab is not installed."""


def render_pdf(*, student_name: str, title: str, series_title: str, score: int, total_marks: int,
               percentage: float, code: str, issued_at: datetime, verify_url: str) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable certificate PDFs.") from exc

    buffer = io.BytesIO()
    width, height = landscape(A4)
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Certificate {code}")

    ink = colors.HexColor("#2A3FC7")
    pdf.setStrokeColor(ink)
    pdf.setLineWidth(3)
    pdf.rect(28, 28, width - 56, height - 56)
    pdf.setLineWidth(0.8)
    pdf.rect(38, 38, width - 76, height - 76)

    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 34)
    pdf.drawCentredString(width / 2, height - 120, "CERTIFICATE OF ACHIEVEMENT")

    pdf.setFillColor(colors.HexColor("#5A617A"))
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(width / 2, height - 170, "This is to certify that")

    pdf.setFillColor(colors.HexColor("#171B2D"))
    pdf.setFont("Helvetica-Bold", 30)
    pdf.drawCentredString(width / 2, height - 220, student_name or "—")

    pdf.setFillColor(colors.HexColor("#5A617A"))
    pdf.setFont("Helvetica", 15)
    pdf.drawCentredString(width / 2, height - 262, "has successfully passed")

    pdf.setFillColor(ink)
    pdf.setFont("Helvetica-Bold", 22)
    pdf.drawCentredString(width / 2, height - 302, title or series_title)

    pdf.setFillColor(colors.HexColor("#171B2D"))
    pdf.setFont("Helvetica", 14)
    pdf.drawCentredString(width / 2, height - 340, f"Score: {score} / {total_marks}   ({percentage:.1f}%)")

    pdf.setFont("Helvetica", 11)
    pdf.setFillColor(colors.HexColor("#5A617A"))
    pdf.drawString(70, 92, f"Issued: {issued_at:%d %B %Y}")
    pdf.drawString(70, 74, f"Certificate ID: {code}")
    pdf.drawRightString(width - 70, 92, "Verify this certificate at:")
    pdf.drawRightString(width - 70, 74, verify_url)

    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
