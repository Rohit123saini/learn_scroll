# common/question_grading.py
"""
Shared auto-grading logic — originally `Question.auto_grade()` in
`testseries/models.py`. Moved here as a plain function (no Django/model
imports) so `assigments` (Task 7) can reuse the exact same grading rules
instead of duplicating them.

Deliberately takes `question_type`/`options`/`correct_answer` as plain
values (strings/list/dict) rather than a `Question` instance — that's
what keeps this module import-free of `testseries` and any Django app,
so it stays a genuine shared utility instead of secretly depending on
one app's models. `question_type` is compared against the raw string
values ("text"/"mcq"/"msq"/"list") that `Question.QuestionType` stores
in the DB, not the enum itself.

NOTE for whoever wires this into `assigments` (Task 7): the original
`Question.auto_grade(self, answer_data)` used `self.marks` for the
"full marks on correct" number. That's now the `marks` parameter here
— pass the question's own marks value at the call site. `testseries`
does this in its `Question.auto_grade()` wrapper below (see
`testseries/models.py`); `assigments` should do the same when it wires
this in for Task 7.

Task 8.1 — extra types (testseries only; `assigments` keeps its 4 types):
    true_false  correct_answer {"value": true|false}      answer {"value": bool}
    fill_blank  correct_answer {"answers": ["Delhi", ...], "case_sensitive": false}
                                                          answer {"text": "..."}
    numeric     correct_answer {"value": 3.14, "tolerance": 0.01}   (absolute)
                                                          answer {"value": 3.14 | "3.14"}
All three are exact/all-or-nothing like the others, and never raise on a
malformed answer — a junk payload is simply "wrong".
"""
import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation


def _norm_text(value, *, case_sensitive: bool) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value if case_sensitive else value.casefold()


def parse_number(raw):
    """Number from a bool-free int/float/str ("1,250.5" ok) -> Decimal, else None."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            return None
        return Decimal(str(raw))
    if isinstance(raw, str):
        cleaned = raw.strip().replace(",", "").replace(" ", "")
        if not cleaned:
            return None
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            return None
        return number if number.is_finite() else None
    return None


def parse_bool(raw):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in ("true", "t", "yes", "1"):
            return True
        if low in ("false", "f", "no", "0"):
            return False
    return None


def _grade_extra(question_type: str, correct_answer: dict, answer_data: dict) -> bool:
    if question_type == "true_false":
        given = parse_bool(answer_data.get("value"))
        expected = parse_bool(correct_answer.get("value"))
        return given is not None and given == expected
    if question_type == "fill_blank":
        case = bool(correct_answer.get("case_sensitive", False))
        given = _norm_text(answer_data.get("text"), case_sensitive=case)
        if not given:
            return False
        accepted = correct_answer.get("answers") or []
        return any(given == _norm_text(a, case_sensitive=case) for a in accepted)
    # numeric
    given = parse_number(answer_data.get("value"))
    expected = parse_number(correct_answer.get("value"))
    if given is None or expected is None:
        return False
    tolerance = parse_number(correct_answer.get("tolerance")) or Decimal(0)
    return abs(given - expected) <= abs(tolerance)


EXTRA_TYPES = ("true_false", "fill_blank", "numeric")


def auto_grade(
    *,
    question_type: str,
    options,
    correct_answer: dict,
    answer_data: dict,
    marks: int,
) -> tuple[bool | None, int | None]:
    """Returns `(is_correct, marks_awarded)`. Both `None` for `text`
    questions — never auto-graded, always routed to manual review."""
    if question_type == "text":
        return None, None

    if question_type == "mcq":
        correct = answer_data.get("option_id") == correct_answer.get("option_id")
    elif question_type == "msq":
        correct = set(answer_data.get("option_ids", [])) == set(correct_answer.get("option_ids", []))
    elif question_type in EXTRA_TYPES:
        correct = _grade_extra(
            question_type,
            correct_answer if isinstance(correct_answer, dict) else {},
            answer_data if isinstance(answer_data, dict) else {},
        )
    elif question_type == "list":
        mode = correct_answer.get("list_mode")
        if mode == "match":
            correct = answer_data.get("pairs") == correct_answer.get("pairs")
        else:  # "order"
            correct = answer_data.get("sequence") == correct_answer.get("sequence")
    else:
        raise ValueError(f"Unknown question_type: {question_type!r}")

    return correct, (marks if correct else 0)