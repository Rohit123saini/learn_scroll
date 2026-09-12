"""
common/question_grading.py

Shared auto-grading utility for any app that clones `testseries.Question`'s
type/grading shape — today that's just `assignment` (assignment_app_design.md
§2a), and per that same section `testseries` is expected to import this too
once it exists, so neither app duplicates `_auto_grade()` and the two drift
apart the moment one of them tweaks partial-credit rules.

THIS IS NOT A DJANGO APP — deliberately. It's a pure-function module with no
models, no migrations, no settings entry. That's the whole point (§8.6 of the
design doc): a shared Django *app* between `assignment` and `testseries`
would recreate exactly the cross-app coupling problem `core`/`campus`/
`assignment` all go out of their way to avoid via bridge.py. A stdlib-only
utility module has no such coupling — either app can `import` it without
taking on a dependency edge in the app graph.

IMPORTANT — provenance flag: `testseries.Question`'s actual as-built
type/grading semantics were **not available to verify** in this pass (no
testseries source was provided alongside login/models.py, core/models.py, or
assignment_app_design.md). The behaviour below is written to match what the
design doc *describes* (§2a: "text/subjective, multiple-choice, list-based",
"same `mark_answer()` semantics", "msq/list partial-credit non-goal" per §8.4/
§8.7). Treat this as [NOT YET VERIFIED] against the real testseries
implementation, same as the doc's own liveclass-migration caveat in §6 — diff
this against `testseries/grading.py` (once it exists) before assuming the two
are actually identical, and update both call sites together if they're not.
"""
from dataclasses import dataclass
from typing import Any


class QuestionType:
    """Mirrors `AssignmentQuestion.question_type` / (future)
    `testseries.Question.question_type` choices. Kept as plain string
    constants here (not a Django TextChoices) because this module has no
    Django dependency at all — the calling app's own TextChoices enum is
    the source of truth; these are just the string values every clone is
    expected to use, so a typo here would be a loud `!=` failure rather than
    a silent divergence.
    """

    TEXT = "text"
    MCQ = "mcq"
    MSQ = "msq"
    LIST = "list"

    AUTO_GRADABLE = frozenset({MCQ, MSQ, LIST})


@dataclass(frozen=True)
class GradingResult:
    """Return shape for `auto_grade()`. `is_auto_graded=False` means the
    caller MUST leave `marks_awarded`/`is_correct` as None and route the
    answer to a human reviewer (`AssignmentAnswer.mark_answer` /
    `AssignmentSubmission.mark_answer_and_maybe_finalize`) — never guess."""

    is_auto_graded: bool
    is_correct: bool | None
    marks_awarded: int | None


def auto_grade(*, question_type: str, marks: int, correct_answer: Any, answer_data: Any) -> GradingResult:
    """Grade one answer against one question, if the question type supports
    auto-grading at all.

    - `text` — never auto-graded (subjective). Always returns
      `is_auto_graded=False`; the model layer is responsible for routing
      this to `partially_checked` / human review, never for calling this
      function's result as if it were final.
    - `mcq` — `correct_answer` is a single option id, `answer_data` is a
      single option id. Full marks on exact match, else zero. No partial
      credit (only one option is even selectable).
    - `msq` / `list` — `correct_answer` and `answer_data` are both
      collections. Graded as **all-or-nothing set equality** — full marks
      if the sets match exactly, else zero. Partial credit for a partially
      -correct multi-select or partially-correct ordered list is an
      explicit **non-goal** per the design doc (§8.4/§8.7, "msq/list
      partial-credit non-goal wahi hai jo testseries doc §8.4 me hai") —
      do not "improve" this into partial scoring without updating that
      decision in both docs first.
      `list` intentionally ignores order (set comparison, not sequence
      comparison) — [NOT YET VERIFIED]: flip to an ordered comparison here
      if testseries's real `list` semantics turn out to require an exact
      sequence match rather than an unordered set match.
    """
    if question_type == QuestionType.TEXT:
        return GradingResult(is_auto_graded=False, is_correct=None, marks_awarded=None)

    if question_type == QuestionType.MCQ:
        is_correct = answer_data == correct_answer
        return GradingResult(is_auto_graded=True, is_correct=is_correct, marks_awarded=marks if is_correct else 0)

    if question_type in (QuestionType.MSQ, QuestionType.LIST):
        given = set(answer_data or [])
        expected = set(correct_answer or [])
        is_correct = given == expected
        return GradingResult(is_auto_graded=True, is_correct=is_correct, marks_awarded=marks if is_correct else 0)

    raise ValueError(f"Unknown question_type for auto_grade(): {question_type!r}")
