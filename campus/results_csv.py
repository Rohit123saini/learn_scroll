"""
Results CSV parser. Pure Python, no Django imports (standalone testable).

Do layouts:

1. **Subject-wise columns** — ek row per student, har subject ka ek column:
       roll_number,Maths (50),Science (50)
       12,41,AB
   Header ka ``(50)`` / ``-50`` / ``[50]`` suffix us column ka max marks hai
   (optional). Pehle POORA header subject naam se match hota hai (taaki
   "Maths-2" jaisa subject naam ``-2`` max na ban jaye), phir suffix hata kar.
2. **Single subject** — ek ``marks`` column (+ optional ``max_marks``,
   ``remarks``); subject request se aata hai.

Student ki pehchaan: roll number, enrollment number ya username — NAAM se
kabhi nahi (do bacchon ka naam same ho sakta hai). Comma / semicolon / tab
delimiter auto-detect. ``AB`` / ``ABS`` / ``ABSENT`` / ``-`` / ``NA`` marks =
skip (abhi marks nahi). Har error line number ke saath.
"""
import csv
import io
import re
from decimal import Decimal, InvalidOperation

MAX_ROWS = 500
MAX_BYTES = 1_000_000

ID_COLUMNS = {
    "roll_number": "roll_number", "roll_no": "roll_number", "roll": "roll_number",
    "rollno": "roll_number", "roll number": "roll_number",
    "enrollment_no": "enrollment_no", "enrollment_number": "enrollment_no",
    "enrollment no": "enrollment_no", "enrollment": "enrollment_no",
    "admission_no": "enrollment_no", "admission no": "enrollment_no",
    "username": "username", "user": "username", "user_name": "username",
}
IGNORED_COLUMNS = {"name", "student", "student_name", "student name", "section", "class", "sr", "sr_no", "s.no", "#"}
SINGLE_MARKS = {"marks", "marks_obtained", "obtained", "score"}
SINGLE_MAX = {"max_marks", "max", "out_of", "total", "maximum"}
SINGLE_REMARKS = {"remarks", "remark", "comment", "comments"}
ABSENT_TOKENS = {"ab", "abs", "absent", "-", "--", "na", "n/a", ""}

_SUFFIX_RE = re.compile(r"^(?P<name>.*?)\s*(?:\((?P<a>[\d.]+)\)|\[(?P<b>[\d.]+)\]|-\s*(?P<c>[\d.]+))\s*$")

TEMPLATE_SINGLE_HEADER = ["roll_number", "marks", "max_marks", "remarks"]


def _norm(h):
    return re.sub(r"\s+", " ", (h or "").strip().lstrip("﻿")).lower()


def _dec(text):
    """Returns Decimal or raises ValueError('...')."""
    try:
        d = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Not a number.")
    if not d.is_finite() or d < 0:
        raise ValueError("Must be 0 or more.")
    if d >= Decimal("10000"):
        raise ValueError("Too large.")
    return d.quantize(Decimal("0.01"))


def _sniff(text):
    sample = text[:4096]
    first = sample.splitlines()[0] if sample.splitlines() else ""
    counts = {d: first.count(d) for d in (",", ";", "\t")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def _match_subject_header(header, subject_names):
    """-> (subject_name, max_marks Decimal|None) or None."""
    full = _norm(header)
    lookup = {_norm(n): n for n in subject_names}
    if full in lookup:
        return lookup[full], None
    m = _SUFFIX_RE.match(header.strip().lstrip("﻿"))
    if m:
        base = _norm(m.group("name"))
        raw = m.group("a") or m.group("b") or m.group("c")
        if base in lookup:
            try:
                mx = _dec(raw)
            except ValueError:
                mx = None
            return lookup[base], mx
    return None


def parse_results_csv(text, *, subject_names=(), single_subject=None):
    """
    ``subject_names``: is campus ke subject naam (layout 1 ke liye).
    ``single_subject``: agar request ek hi subject ke liye hai to uska naam;
    tab ``marks`` column wala layout bhi chalta hai.

    Returns::
        {"layout": "subjects"|"single"|None,
         "cells": [{"line", "key", "key_type", "subject", "marks", "max_marks", "remarks"}],
         "errors": [{"line", "message"}], "warnings": [{"line", "message"}]}
    ``marks`` None = skip (AB / khaali). ``subject`` = naam (single layout me
    ``single_subject``).
    """
    errors, cells, warnings = [], [], []

    def fail(line, msg):
        errors.append({"line": line, "message": msg})

    if text is None:
        return {"layout": None, "cells": [], "errors": [{"line": 0, "message": "Empty file."}], "warnings": []}
    if isinstance(text, bytes):
        if len(text) > MAX_BYTES:
            return {"layout": None, "cells": [], "errors": [{"line": 0, "message": "File too large."}], "warnings": []}
        try:
            text = text.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = text.decode("latin-1")
    text = text.lstrip("﻿")
    if not text.strip():
        return {"layout": None, "cells": [], "errors": [{"line": 0, "message": "Empty file."}], "warnings": []}

    reader = csv.reader(io.StringIO(text), delimiter=_sniff(text))
    rows = [(i + 1, r) for i, r in enumerate(reader)]
    rows = [(n, r) for n, r in rows if any((c or "").strip() for c in r)]
    if not rows:
        return {"layout": None, "cells": [], "errors": [{"line": 0, "message": "Empty file."}], "warnings": []}

    header_line, header = rows[0]
    key_idx, key_type, subj_cols = None, None, {}
    marks_idx = max_idx = remarks_idx = None
    for idx, h in enumerate(header):
        n = _norm(h)
        if n in ID_COLUMNS:
            if key_idx is None:
                key_idx, key_type = idx, ID_COLUMNS[n]
            continue
        if n in IGNORED_COLUMNS:
            continue
        if n in SINGLE_MARKS and single_subject:
            marks_idx = idx
        elif n in SINGLE_MAX:
            max_idx = idx
        elif n in SINGLE_REMARKS:
            remarks_idx = idx
        else:
            hit = _match_subject_header(h, subject_names)
            if hit:
                if hit[0] in {v[0] for v in subj_cols.values()}:
                    fail(header_line, f"Subject '{hit[0]}' appears in more than one column.")
                subj_cols[idx] = hit
            elif n:
                warnings.append({"line": header_line, "message": f"Unknown column '{h.strip()}' ignored."})

    if key_idx is None:
        fail(header_line, "Need a roll_number, enrollment_no or username column.")
        return {"layout": None, "cells": [], "errors": errors, "warnings": warnings}

    if marks_idx is not None and subj_cols:
        fail(header_line, "Use either a 'marks' column or subject columns, not both.")
        return {"layout": None, "cells": [], "errors": errors, "warnings": warnings}
    if marks_idx is None and not subj_cols:
        fail(header_line, "No marks column found. Use 'marks' or subject columns like 'Maths (50)'.")
        return {"layout": None, "cells": [], "errors": errors, "warnings": warnings}

    layout = "single" if marks_idx is not None else "subjects"
    body = rows[1:]
    if len(body) > MAX_ROWS:
        fail(0, f"At most {MAX_ROWS} rows per file.")
        return {"layout": layout, "cells": [], "errors": errors, "warnings": warnings}
    if not body:
        fail(header_line, "No data rows.")

    def cell(r, i):
        return (r[i] if i is not None and i < len(r) else "") or ""

    seen = {}
    for line, r in body:
        key = cell(r, key_idx).strip()
        if not key:
            fail(line, "Student identifier is empty.")
            continue
        sig_key = key.lower()
        targets = (
            [(single_subject, marks_idx, max_idx)] if layout == "single"
            else [(name, i, None) for i, (name, _) in subj_cols.items()]
        )
        for subject, midx, xidx in targets:
            dup = (sig_key, subject)
            if dup in seen:
                fail(line, f"Duplicate row for '{key}' (first seen on line {seen[dup]}).")
                break
            seen[dup] = line
            raw = cell(r, midx).strip()
            try:
                marks = None if raw.lower() in ABSENT_TOKENS else _dec(raw)
            except ValueError as e:
                fail(line, f"{subject}: '{raw}' — {e}")
                continue
            max_marks = None
            if layout == "subjects":
                max_marks = subj_cols[midx][1]
            elif xidx is not None and cell(r, xidx).strip():
                try:
                    max_marks = _dec(cell(r, xidx))
                except ValueError as e:
                    fail(line, f"max_marks '{cell(r, xidx).strip()}' — {e}")
                    continue
            if max_marks is not None and max_marks <= 0:
                fail(line, "max_marks must be greater than 0.")
                continue
            if marks is not None and max_marks is not None and marks > max_marks:
                fail(line, f"{subject}: marks {marks} exceed max {max_marks}.")
                continue
            cells.append({
                "line": line, "key": key, "key_type": key_type, "subject": subject,
                "marks": marks, "max_marks": max_marks,
                "remarks": cell(r, remarks_idx).strip()[:255] if layout == "single" else "",
            })
    return {"layout": layout, "cells": cells, "errors": errors, "warnings": warnings}


def build_template_csv(rows):
    """Blank single-subject template. ``rows``: iterable of (roll_number, name)
    — name sirf sahuliyat ke liye (import me ignore hota hai)."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["roll_number", "name", "marks", "max_marks", "remarks"])
    for roll, name in rows:
        w.writerow([roll, name, "", "", ""])
    return out.getvalue()
