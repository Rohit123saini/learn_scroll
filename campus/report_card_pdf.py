# campus/report_card_pdf.py
"""
Renders a student's report card (one exam term) to an A4 PDF.

Pure rendering: the view hands in plain values (already limited to what the
viewer may see — students / parents only ever get PUBLISHED marks), so this
module never touches the ORM. Same optional-dependency contract as
`receipt_pdf.py` / `testseries/certificate_pdf.py`: `reportlab` is imported
lazily and the view turns `PdfUnavailable` into HTTP 501.

Limits (deliberate): the built-in PDF fonts only cover Latin text, so a subject
or campus name written in Devanagari would not render — names are passed through
`_safe()` which replaces unsupported characters with "?" instead of crashing.
More subjects than fit on a page flow onto continuation pages.
"""
from __future__ import annotations

import io
from datetime import datetime

from .receipt_pdf import PdfUnavailable  # one exception type for every campus PDF

__all__ = ["PdfUnavailable", "render_report_card_pdf"]


def _safe(text) -> str:
    """Latin-1 only (the standard Helvetica fonts)."""
    return str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1")


def _marks(value) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return "-"
    return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")


def render_report_card_pdf(
    *,
    campus_name: str,
    campus_type: str,
    student_name: str,
    roll_number: str,
    class_label: str,
    session_name: str,
    term_name: str,
    subjects: list,
    total_obtained,
    total_max,
    percentage,
    grade,
    passed,
    attendance_percent,
    attendance_total: int,
    generated_at: datetime,
) -> bytes:
    """`subjects`: dicts with subject_name, marks_obtained, max_marks, grade, passed, remarks."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable report card PDFs.") from exc

    buffer = io.BytesIO()
    width, height = A4
    pdf = canvas.Canvas(buffer, pagesize=(width, height))
    pdf.setTitle(f"Report Card - {_safe(student_name)} - {_safe(term_name)}")

    ink = colors.HexColor("#2A3FC7")
    muted = colors.HexColor("#5A617A")
    dark = colors.HexColor("#171B2D")
    green = colors.HexColor("#1E8E3E")
    red = colors.HexColor("#C5221F")
    band = colors.HexColor("#EEF0FB")
    left, right = 48, width - 48

    def frame():
        pdf.setStrokeColor(ink)
        pdf.setLineWidth(2)
        pdf.rect(28, 28, width - 56, height - 56)

    def header():
        pdf.setFillColor(ink)
        pdf.setFont("Helvetica-Bold", 20)
        pdf.drawCentredString(width / 2, height - 78, _safe(campus_name))
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawCentredString(width / 2, height - 95, _safe(str(campus_type).title()))
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawCentredString(width / 2, height - 125, "REPORT CARD")
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawCentredString(width / 2, height - 142, _safe(f"{term_name}  |  {session_name}"))
        pdf.setStrokeColor(ink)
        pdf.setLineWidth(0.8)
        pdf.line(left, height - 154, right, height - 154)

    def student_block(y):
        rows = [("Student", student_name), ("Class", class_label)]
        if roll_number:
            rows.append(("Roll no.", roll_number))
        for label, value in rows:
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica", 10)
            pdf.drawString(left, y, label)
            pdf.setFillColor(dark)
            pdf.setFont("Helvetica-Bold", 11)
            pdf.drawString(left + 80, y, _safe(value))
            y -= 17
        return y

    cols = {"subject": left + 8, "marks": left + 250, "max": left + 320, "grade": left + 385, "result": right - 8}

    def table_head(y):
        pdf.setFillColor(band)
        pdf.rect(left, y - 6, right - left, 22, stroke=0, fill=1)
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(cols["subject"], y, "Subject")
        pdf.drawRightString(cols["marks"], y, "Marks")
        pdf.drawRightString(cols["max"], y, "Out of")
        pdf.drawString(cols["grade"], y, "Grade")
        pdf.drawRightString(cols["result"], y, "Result")
        return y - 24

    frame()
    header()
    y = student_block(height - 182)
    y = table_head(y - 14)

    bottom = 150  # room for the totals block on the last page
    for row in subjects:
        if y < bottom:
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica-Oblique", 9)
            pdf.drawCentredString(width / 2, 44, "continued on next page")
            pdf.showPage()
            frame()
            header()
            y = table_head(height - 182)
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawString(cols["subject"], y, _safe(row.get("subject_name"))[:42])
        pdf.drawRightString(cols["marks"], y, _marks(row.get("marks_obtained")))
        pdf.drawRightString(cols["max"], y, _marks(row.get("max_marks")))
        pdf.drawString(cols["grade"], y, _safe(row.get("grade") or "-"))
        ok = row.get("passed") is not False
        pdf.setFillColor(green if ok else red)
        pdf.setFont("Helvetica-Bold", 9.5)
        pdf.drawRightString(cols["result"], y, "PASS" if ok else "FAIL")
        remarks = _safe(row.get("remarks"))
        y -= 15
        if remarks:
            pdf.setFillColor(muted)
            pdf.setFont("Helvetica-Oblique", 8.5)
            pdf.drawString(cols["subject"], y, remarks[:90])
            y -= 13
        pdf.setStrokeColor(colors.HexColor("#E3E6F2"))
        pdf.setLineWidth(0.5)
        pdf.line(left, y + 6, right, y + 6)
        y -= 4

    # totals
    y -= 8
    pdf.setStrokeColor(ink)
    pdf.setLineWidth(1)
    pdf.line(left, y + 10, right, y + 10)
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 11)
    pdf.drawString(cols["subject"], y - 6, "Total")
    pdf.drawRightString(cols["marks"], y - 6, _marks(total_obtained))
    pdf.drawRightString(cols["max"], y - 6, _marks(total_max))
    pdf.drawString(cols["grade"], y - 6, _safe(grade or "-"))
    y -= 34

    pdf.setFont("Helvetica", 10.5)
    pdf.setFillColor(muted)
    pdf.drawString(left, y, "Percentage")
    pdf.setFillColor(dark)
    pdf.setFont("Helvetica-Bold", 12)
    try:
        pct_text = f"{float(percentage):.2f}%"
    except (TypeError, ValueError):
        pct_text = "-"
    pdf.drawString(left + 80, y, pct_text)

    if passed is not None:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawString(left + 190, y, "Result")
        pdf.setFillColor(green if passed else red)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(left + 232, y, "PASS" if passed else "FAIL")

    if attendance_percent is not None and attendance_total:
        pdf.setFillColor(muted)
        pdf.setFont("Helvetica", 10.5)
        pdf.drawString(left + 310, y, "Attendance")
        pdf.setFillColor(dark)
        pdf.setFont("Helvetica-Bold", 12)
        pdf.drawString(left + 375, y, f"{float(attendance_percent):.1f}%")

    pdf.setFillColor(muted)
    pdf.setFont("Helvetica-Oblique", 8.5)
    pdf.drawString(left, 46, f"Generated on {generated_at:%d %b %Y} by LearnScroll. Computer generated - published marks only.")
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()
