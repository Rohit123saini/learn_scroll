# common/question_csv.py
"""
Bulk question import **with the answer key already filled in** — the
"MCQ me answers pehle se daal do, final check apne aap ho jaye" requirement.

Shared by `testseries` (Question) and `assigments` (assigmentsQuestion), which
is why it lives in `common/` (neither app may import the other). A creator
prepares a spreadsheet, exports CSV, uploads it once; every row becomes a
question whose `correct_answer` is already set, so submitting auto-grades
every objective question (no manual review step for those).

Pure Python (no Django imports) so it is unit-testable in isolation
(`testseries/tests_pure.py`). It returns plain dicts shaped exactly like
`Question`'s writable fields; the view hands them to the same
`Question.full_clean()` path every other creation route uses, so shape
validation is never duplicated here.

CSV columns (header row required, case-insensitive, any order):

    type         mcq | msq | order | text            (default: mcq)
    question     the question text                   (required)
    option_a .. option_h   answer options            (mcq/msq/order)
    correct      mcq   -> one letter            e.g.  B
                 msq   -> letters, comma/space  e.g.  A,C
                 order -> letters in the right order   C,A,B,D
                 text  -> leave empty (teacher grades manually)
    marks        positive integer                    (default: 1)
    negative     marks deducted for a WRONG answer   (default: 0)
    topic        optional label (feeds topic analytics)
    difficulty   easy | medium | hard  (optional)
    explanation  shown to the student with the solution (optional)

`match` (match-the-following) questions have a two-column shape that
doesn't fit a flat CSV row; create those through the JSON bulk endpoint.
"""
from __future__ import annotations

import csv
import io
from typing import Any

MAX_ROWS = 500
MAX_OPTIONS = 8
_LETTERS = "abcdefgh"
_TYPE_ALIASES = {
    "mcq": "mcq", "single": "mcq", "single_choice": "mcq",
    "msq": "msq", "multi": "msq", "multiple": "msq", "multi_select": "msq",
    "order": "order", "ordering": "order", "sequence": "order",
    "text": "text", "subjective": "text", "written": "text",
}
_DIFFICULTIES = {"easy", "medium", "hard"}


class CsvImportResult:
    """`questions` are ready to create; `errors` is `[(row_number, message)]`
    (row 1 = header, so the first data row is 2 — matches what the creator
    sees in their spreadsheet)."""

    def __init__(self):
        self.questions: list[dict[str, Any]] = []
        self.errors: list[tuple[int, str]] = []

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.questions)


def _letters(raw: str) -> list[str]:
    cleaned = raw.replace(",", " ").replace(";", " ").replace("|", " ").replace("->", " ")
    parts = cleaned.split()
    if len(parts) == 1 and len(parts[0]) > 1 and parts[0].isalpha():
        parts = list(parts[0])  # "ACD" -> A C D
    return [p.strip().lower() for p in parts if p.strip()]


def parse_csv(content: bytes | str, *, start_order: int = 1) -> CsvImportResult:
    """Parse CSV text/bytes into question dicts. Never raises on bad
    *content* — every problem is reported per row so the creator can fix the
    sheet in one pass instead of one error at a time."""
    result = CsvImportResult()
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8-sig")  # Excel's "CSV UTF-8" adds a BOM
        except UnicodeDecodeError:
            result.errors.append((1, "File must be UTF-8 encoded (in Excel: Save As -> CSV UTF-8)."))
            return result
    else:
        text = content

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        result.errors.append((1, "The file is empty."))
        return result
    reader.fieldnames = [(h or "").strip().lower() for h in reader.fieldnames]
    if "question" not in reader.fieldnames:
        result.errors.append((1, "Missing required column: question."))
        return result

    order = start_order
    for offset, row in enumerate(reader, start=2):
        if offset - 1 > MAX_ROWS:
            result.errors.append((offset, f"Too many rows (max {MAX_ROWS} per file)."))
            break
        row = {k: (v or "").strip() for k, v in row.items() if k}
        if not any(row.values()):
            continue  # blank spreadsheet line

        text_q = row.get("question", "")
        if not text_q:
            result.errors.append((offset, "question is empty."))
            continue

        qtype = _TYPE_ALIASES.get((row.get("type") or "mcq").lower())
        if qtype is None:
            result.errors.append((offset, f"Unknown type '{row.get('type')}'. Use mcq, msq, order or text."))
            continue

        try:
            marks = int(row.get("marks") or 1)
            negative = int(row.get("negative") or 0)
        except ValueError:
            result.errors.append((offset, "marks / negative must be whole numbers."))
            continue
        if marks < 1:
            result.errors.append((offset, "marks must be at least 1."))
            continue
        if negative < 0:
            result.errors.append((offset, "negative cannot be below 0."))
            continue

        difficulty = (row.get("difficulty") or "").lower()
        if difficulty and difficulty not in _DIFFICULTIES:
            result.errors.append((offset, "difficulty must be easy, medium or hard."))
            continue

        q: dict[str, Any] = {
            "order": order,
            "text": text_q,
            "marks": marks,
            "negative_marks": negative,
            "topic": row.get("topic", "")[:80],
            "difficulty": difficulty,
            "explanation": row.get("explanation", ""),
        }

        if qtype == "text":
            q.update(question_type="text", options=[], correct_answer={})
            result.questions.append(q)
            order += 1
            continue

        options = []
        for i, letter in enumerate(_LETTERS[:MAX_OPTIONS]):
            value = row.get(f"option_{letter}", "")
            if value:
                options.append({"id": letter, "text": value})
        ids = {o["id"] for o in options}
        if len(options) < 2:
            result.errors.append((offset, "At least two options (option_a, option_b, ...) are required."))
            continue
        # Fill gaps check: option_a, option_c without option_b is almost always a typo.
        expected = list(_LETTERS[: len(options)])
        if [o["id"] for o in options] != expected:
            result.errors.append((offset, "Options must be continuous from option_a (no skipped columns)."))
            continue

        picked = _letters(row.get("correct", ""))
        if not picked:
            result.errors.append((offset, "correct is empty — fill the answer key (e.g. B, or A,C)."))
            continue
        unknown = [p for p in picked if p not in ids]
        if unknown:
            result.errors.append((offset, f"correct refers to option(s) that don't exist: {', '.join(unknown).upper()}."))
            continue

        if qtype == "mcq":
            if len(picked) != 1:
                result.errors.append((offset, "An mcq has exactly one correct option (use type=msq for several)."))
                continue
            q.update(question_type="mcq", options=options, correct_answer={"option_id": picked[0]})
        elif qtype == "msq":
            if len(set(picked)) != len(picked):
                result.errors.append((offset, "correct lists the same option twice."))
                continue
            q.update(question_type="msq", options=options, correct_answer={"option_ids": picked})
        else:  # order
            if sorted(picked) != sorted(ids):
                result.errors.append((offset, "For type=order, correct must list every option exactly once, in order."))
                continue
            q.update(
                question_type="list",
                options=options,
                correct_answer={"list_mode": "order", "sequence": picked},
            )

        result.questions.append(q)
        order += 1

    if not result.questions and not result.errors:
        result.errors.append((1, "No questions found in the file."))
    return result
