# assigments/plagiarism.py
"""
Task 6 (feature-suggestions doc), Part A — "Assignment Duplicate/Plagiarism
Flag": the text-similarity engine itself.

Split into 3 parts when this was scoped (see
`ASSIGNMENT_TASK6_PLAGIARISM_SPLIT.md` at the repo root for the full
breakdown):
  Part A (this file + the `SubmissionSimilarityFlag` model)  — DONE
  Part B (API wiring: auto-run on submit, teacher review endpoints) — DONE
  Part C (Flutter grading-screen badge)                       — NOT done,
      deliberately deferred the same way Task 38 was in
      `user_profile/models.py` — flagged, not silently skipped.

WHAT THIS DOES: compares one student's just-submitted answer text against
every other submission already on file for the *same* `assigments` (never
across assignments — a shared phrase in two unrelated briefs is not
plagiarism), using `difflib.SequenceMatcher` (stdlib, no new dependency —
matches this app's existing preference for zero-extra-deps utilities like
`common.question_grading`). Anything at or above `SIMILARITY_THRESHOLD`
becomes a `SubmissionSimilarityFlag` row for a teacher to look at on the
grading screen — this module never auto-rejects or auto-penalizes
anything, it only surfaces a signal (matches the suggestions doc's own
framing: "teacher ko ek flag dikhe... possible duplicate", not "auto
zero-mark").

Two independent comparison paths, since `assigmentsSubmission` itself has
two mutually-exclusive content shapes (§2a in models.py):
  - Free-form path: compares `written_content` against other submissions'
    `written_content` for the same assigments.
  - Structured path: compares, per `text`-type question, this
    submission's `assigmentsAnswer.answer_data["text"]` against every
    other submission's answer to that *same* question (comparing across
    different questions would be meaningless).

Deliberately NOT covered here: `file`/`link_url` hand-ins (a PDF/repo-link
diff is a different, much bigger problem — OCR/downloading+diffing repos —
and out of scope for this pass, same "flagged, not guessed around" spirit
as the model's own §5 GAP note) and cross-assignment comparison (a student
submitting last year's own answer to a *reused* assignment is out of
scope too — needs a policy decision this app doesn't get to make).
"""
from __future__ import annotations

from difflib import SequenceMatcher

from django.db import transaction

from .models import assigmentsAnswer, assigmentsQuestion, assigmentsSubmission, SubmissionSimilarityFlag

# Empirically-reasonable defaults, not tuned against a real corpus —
# a teacher can dismiss a false positive in one tap (see the `review`
# action in views.py), so erring slightly toward over-flagging is the
# safer default than under-flagging and missing a real copy.
SIMILARITY_THRESHOLD = 0.75

# Below this many characters, `SequenceMatcher` ratios are noisy (two
# one-line MCQ-style "text" answers like "42" and "24" can score high by
# sheer coincidence) and not worth a teacher's attention either way.
MIN_COMPARABLE_LENGTH = 40

# How much of each side to keep on the flag row for the teacher to read
# without opening both full submissions.
EXCERPT_LENGTH = 300


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _ordered_pair(sub_a: assigmentsSubmission, sub_b: assigmentsSubmission):
    """Always store a flag with a deterministic (lower-id-first) ordering
    of the two submissions, so a later re-check of either submission
    never creates the same pair twice the other way round."""
    return (sub_a, sub_b) if str(sub_a.id) < str(sub_b.id) else (sub_b, sub_a)


def _upsert_flag(*, assigments_id, sub_a, sub_b, question, score: float, text_a: str, text_b: str) -> None:
    first, second = _ordered_pair(sub_a, sub_b)
    text_a, text_b = (text_a, text_b) if first is sub_a else (text_b, text_a)
    existing = SubmissionSimilarityFlag.objects.filter(
        assigments_id=assigments_id, submission_a=first, submission_b=second, question=question,
    ).first()
    if existing is not None and existing.status != SubmissionSimilarityFlag.Status.PENDING:
        # A teacher already reviewed this exact pair (confirmed or
        # dismissed) — a re-run finding the same pair similar again
        # shouldn't silently resurrect or overwrite that decision.
        return
    SubmissionSimilarityFlag.objects.update_or_create(
        assigments_id=assigments_id,
        submission_a=first,
        submission_b=second,
        question=question,
        defaults={
            "similarity_score": score,
            "excerpt_a": text_a[:EXCERPT_LENGTH],
            "excerpt_b": text_b[:EXCERPT_LENGTH],
        },
    )


@transaction.atomic
def check_freeform_submission(submission: assigmentsSubmission) -> None:
    """Free-form path — run after `submission.submit_freeform()`."""
    mine = _normalize(submission.written_content)
    if len(mine) < MIN_COMPARABLE_LENGTH:
        return
    others = (
        assigmentsSubmission.objects.filter(assigments_id=submission.assigments_id)
        .exclude(pk=submission.pk)
        .exclude(written_content="")
        .only("id", "written_content")
    )
    for other in others:
        theirs = _normalize(other.written_content)
        if len(theirs) < MIN_COMPARABLE_LENGTH:
            continue
        score = _similarity(mine, theirs)
        if score >= SIMILARITY_THRESHOLD:
            _upsert_flag(
                assigments_id=submission.assigments_id,
                sub_a=submission,
                sub_b=other,
                question=None,
                score=score,
                text_a=submission.written_content,
                text_b=other.written_content,
            )


def _answer_text(answer: assigmentsAnswer) -> str:
    data = answer.answer_data or {}
    value = data.get("text", "")
    return value if isinstance(value, str) else ""


@transaction.atomic
def check_structured_submission(submission: assigmentsSubmission) -> None:
    """Structured path — run after `submission.submit_structured()`.
    Only `text`-type questions carry free-form prose worth comparing;
    mcq/msq/list answers are already auto-graded exactly right-or-wrong
    by `common.question_grading.auto_grade()` and a "duplicate" mcq
    answer is just... the same correct answer, not a copy."""
    my_answers = submission.answers.filter(
        question__question_type=assigmentsQuestion.QuestionTypeChoices.TEXT
    ).select_related("question")
    for mine in my_answers:
        mine_text = _normalize(_answer_text(mine))
        if len(mine_text) < MIN_COMPARABLE_LENGTH:
            continue
        other_answers = assigmentsAnswer.objects.filter(question=mine.question).exclude(
            submission_id=submission.pk
        ).select_related("submission")
        for other in other_answers:
            theirs_text = _normalize(_answer_text(other))
            if len(theirs_text) < MIN_COMPARABLE_LENGTH:
                continue
            score = _similarity(mine_text, theirs_text)
            if score >= SIMILARITY_THRESHOLD:
                _upsert_flag(
                    assigments_id=submission.assigments_id,
                    sub_a=submission,
                    sub_b=other.submission,
                    question=mine.question,
                    score=score,
                    text_a=_answer_text(mine),
                    text_b=_answer_text(other),
                )


def check_submission_for_duplicates(submission: assigmentsSubmission) -> None:
    """Single entry point views.py calls — dispatches on the same
    `has_structured_questions` flag the model itself gates its two
    submit paths on, so callers never need to know which path ran."""
    if submission.assigments.has_structured_questions:
        check_structured_submission(submission)
    else:
        check_freeform_submission(submission)
