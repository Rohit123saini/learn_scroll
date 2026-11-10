# campus/reports_export.py
"""
REPORTS / EXPORT — one writer for every campus report: CSV, Excel (XLSX) and PDF.

No Django, no ORM in here. A report is `(header, rows)` — exactly what `finance.report_*` and
`reports.py` already return — and `render()` turns it into bytes + content type + file extension.

* CSV / XLSX come from `finance_export` (formula-injection guard, UTF-8 BOM, row cap) — so every
  format shares the same safety rules.
* PDF is an A4 table (landscape when there are many columns) with a repeating header row,
  page numbers and a "generated on" footer. `reportlab` is imported lazily; without it
  `render(..., "pdf")` raises `PdfUnavailable` (the view turns that into HTTP 501), same contract
  as `receipt_pdf.py` / `report_card_pdf.py`.

Fonts: the built-in PDF fonts are Latin-1 only. When a Unicode TTF is installed (Noto Sans
Devanagari / Noto Sans / DejaVu Sans, or the path in `settings.CAMPUS_PDF_FONT`) it is used, so
Hindi names print; otherwise unsupported characters become "?" instead of crashing. reportlab does
not shape Devanagari conjuncts, so very complex words can look slightly off — CSV / Excel are the
exact copy.
"""
from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Iterable, Sequence

from . import finance_export as fx
from .receipt_pdf import PdfUnavailable

__all__ = ["FORMATS", "PdfUnavailable", "ExportUnavailable", "TooManyRows", "render", "pdf_bytes"]

ExportUnavailable = fx.ExportUnavailable
TooManyRows = fx.TooManyRows

FORMATS = ("csv", "xlsx", "pdf")
_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
PDF_MAX_ROWS = 5_000          # a PDF is for reading / printing; bigger sets belong in Excel
LANDSCAPE_FROM_COLUMNS = 7

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
)


class PdfTooLarge(ValueError):
    pass


def _latin(text) -> str:
    return str(text if text is not None else "").encode("latin-1", "replace").decode("latin-1")


def _needs_unicode(strings: Iterable[str]) -> bool:
    for s in strings:
        for ch in str(s):
            if ord(ch) > 255:
                return True
    return False


def _unicode_font(extra_path: str | None):
    """-> (regular_name, bold_name) registered with reportlab, or None."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError:  # pragma: no cover
        return None
    for path in ([extra_path] if extra_path else []) + list(_FONT_CANDIDATES):
        if not path or not os.path.isfile(path):
            continue
        try:
            name = "CampusReportFont"
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            bold_path = path.replace("-Regular", "-Bold").replace("Sans.ttf", "Sans-Bold.ttf")
            bold = name
            if bold_path != path and os.path.isfile(bold_path):
                bold = "CampusReportFont-Bold"
                if bold not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(bold, bold_path))
            return name, bold
        except Exception:  # a broken font file must never break the export
            continue
    return None


def pdf_bytes(
    title: str,
    header: Sequence[str],
    rows: Iterable[Sequence],
    *,
    campus_name: str = "",
    subtitle: Sequence[str] = (),
    generated_at: datetime | None = None,
    font_path: str | None = None,
) -> bytes:
    """A4 table PDF. `rows` are written through `finance_export.cell` (same text rules as CSV)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover — depends on the environment
        raise PdfUnavailable("Install `reportlab` to enable PDF reports.") from exc

    body = []
    for i, row in enumerate(rows):
        if i >= PDF_MAX_ROWS:
            raise PdfTooLarge(
                f"A PDF can hold at most {PDF_MAX_ROWS} rows — narrow the filters or use Excel / CSV.")
        body.append([_plain(v) for v in row])
    head = [str(h) for h in header]

    everything = [title, campus_name, *subtitle, *head] + [c for r in body for c in r]
    uni = _unicode_font(font_path) if _needs_unicode(everything) else None
    regular, bold = uni if uni else ("Helvetica", "Helvetica-Bold")
    fix = (lambda s: str(s)) if uni else _latin

    ncols = max(len(head), 1)
    page = landscape(A4) if ncols >= LANDSCAPE_FROM_COLUMNS else A4
    margin = 28
    usable = page[0] - 2 * margin
    font_size = 8.5 if ncols <= 9 else 7.5 if ncols <= 13 else 6.5

    cell_style = ParagraphStyle("cell", fontName=regular, fontSize=font_size, leading=font_size + 2)
    head_style = ParagraphStyle("head", fontName=bold, fontSize=font_size, leading=font_size + 2,
                                textColor=colors.white)
    title_style = ParagraphStyle("title", fontName=bold, fontSize=15, leading=18, textColor=colors.HexColor("#171B2D"))
    sub_style = ParagraphStyle("sub", fontName=regular, fontSize=9, leading=12, textColor=colors.HexColor("#5A617A"))

    def para(text, style):
        safe = fix(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return Paragraph(safe, style)

    # column widths ~ longest text (capped), then scaled to the page width
    weights = []
    for idx in range(ncols):
        longest = max([len(head[idx]) if idx < len(head) else 4] + [len(r[idx]) for r in body[:300] if idx < len(r)])
        weights.append(min(max(longest, 4), 28))
    total = float(sum(weights)) or 1.0
    widths = [usable * w / total for w in weights]

    data = [[para(h, head_style) for h in head]] + [[para(c, cell_style) for c in r] for r in body]
    if not body:
        data.append([para("No data for these filters.", cell_style)] + [""] * (ncols - 1))
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2A3FC7")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F4FA")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8DBEA")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))

    generated = (generated_at or datetime.now()).strftime("%d %b %Y, %I:%M %p")
    story = []
    if campus_name:
        story.append(para(campus_name, sub_style))
    story.append(para(title, title_style))
    for line in subtitle:
        story.append(para(line, sub_style))
    story += [Spacer(1, 8), table]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(colors.HexColor("#5A617A"))
        canvas.drawString(margin, 16, fix(f"Generated {generated}"))
        canvas.drawRightString(page[0] - margin, 16, f"Page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=margin, rightMargin=margin,
                            topMargin=margin, bottomMargin=34, title=fix(title))
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def _plain(value) -> str:
    """Same value rules as the CSV cell, but without the formula-injection quote (a PDF cannot run
    a formula) — and as text."""
    v = fx.cell(value)
    if isinstance(v, str) and v.startswith("'") and value is not None and str(value)[:1] in fx._DANGEROUS:
        v = v[1:]
    return str(v)


def render(
    fmt: str,
    *,
    sheet: str,
    title: str,
    header: Sequence[str],
    rows: Iterable[Sequence],
    campus_name: str = "",
    subtitle: Sequence[str] = (),
    generated_at: datetime | None = None,
    font_path: str | None = None,
):
    """-> (bytes, content_type, extension). Raises ExportUnavailable (xlsx), PdfUnavailable (pdf),
    TooManyRows / PdfTooLarge (too big), ValueError (unknown format)."""
    fmt = (fmt or "csv").lower()
    if fmt not in FORMATS:
        raise ValueError("format must be csv, xlsx or pdf")
    if fmt == "csv":
        blob = fx.csv_bytes(header, rows)
    elif fmt == "xlsx":
        blob = fx.xlsx_bytes(sheet, header, rows)
    else:
        blob = pdf_bytes(title, header, rows, campus_name=campus_name, subtitle=subtitle,
                         generated_at=generated_at, font_path=font_path)
    return blob, _CONTENT_TYPES[fmt], fmt
