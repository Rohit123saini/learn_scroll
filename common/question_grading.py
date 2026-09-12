# common/question_grading.py
"""
Shared auto-grading logic — originally `Question.auto_grade()` in
`testseries/models.py`. Moved here as a plain function (no Django/model
imports) so `assignment` (Task 7) can reuse the exact same grading rules
instead of duplicating them.

Deliberately takes `question_type`/`options`/`correct_answer` as plain
values (strings/list/dict) rather than a `Question` instance — that's
what keeps this module import-free of `testseries` and any Django app,
so it stays a genuine shared utility instead of secretly depending on
one app's models. `question_type` is compared against the raw string
values ("text"/"mcq"/"msq"/"list") that `Question.QuestionType` stores
in the DB, not the enum itself.

NOTE for whoever wires this into `assignment` (Task 7): the original
`Question.auto_grade(self, answer_data)` used `self.marks` for the
"full marks on correct" number. That's now the `marks` parameter here
— pass the question's own marks value at the call site. `testseries`
does this in its `Question.auto_grade()` wrapper below (see
`testseries/models.py`); `assignment` should do the same when it wires
this in for Task 7.
"""


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
    elif question_type == "list":
        mode = correct_answer.get("list_mode")
        if mode == "match":
            correct = answer_data.get("pairs") == correct_answer.get("pairs")
        else:  # "order"
            correct = answer_data.get("sequence") == correct_answer.get("sequence")
    else:
        raise ValueError(f"Unknown question_type: {question_type!r}")

    return correct, (marks if correct else 0)