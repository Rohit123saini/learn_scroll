# campus/finance_export.py
"""
FINANCE — export writers (CSV always, XLSX when `openpyxl` is installed). No Django imports.

Safety
* Spreadsheet formula injection: a text cell starting with = + - @ (or TAB / CR) is
  prefixed with a single quote, so a student/parent/remark typed as `=HYPERLINK(...)`
  can never run inside the accountant's Excel. Numbers / Decimals are NOT touched
  (a refund shown as -500.00 stays a number).
* CSV is UTF-8 with BOM so Excel opens Hindi names correctly.
* Hard row cap (MAX_ROWS) — an export is a report, not a database dump.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Sequence

MAX_ROWS = 100_000
_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


class ExportUnavailable(RuntimeError):
    """openpyxl is not installed (XLSX only)."""


class TooManyRows(ValueError):
    pass


def safe_text(value: str) -> str:
    s = str(value)
    return "'" + s if s and s[0] in _DANGEROUS else s


def cell(value):
    """Python value -> something safe to write to a CSV / XLSX cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return value
    return safe_text(value)


def _guard(rows: Iterable[Sequence]) -> list[list]:
    out = []
    for i, row in enumerate(rows):
        if i >= MAX_ROWS:
            raise TooManyRows(f"More than {MAX_ROWS} rows — narrow the date range or filters.")
        out.append([cell(v) for v in row])
    return out


def csv_bytes(header: Sequence[str], rows: Iterable[Sequence]) -> bytes:
    body = _guard(rows)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow([safe_text(h) for h in header])
    writer.writerows(body)
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def xlsx_bytes(sheet_name: str, header: Sequence[str], rows: Iterable[Sequence]) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ExportUnavailable("Install `openpyxl` to enable Excel export.") from exc
    body = _guard(rows)
    wb = Workbook()
    ws = wb.active
    ws.title = (sheet_name or "Report")[:31]
    ws.append([safe_text(h) for h in header])
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in body:
        ws.append(row)
    ws.freeze_panes = "A2"
    for idx, h in enumerate(header, start=1):
        width = max([len(str(h))] + [len(str(r[idx - 1])) for r in body[:200]]) + 2
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = min(width, 48)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
