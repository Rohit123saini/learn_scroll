# testseries/bridge.py
"""
Entry point for OTHER apps (`campus`, `liveclass`) to create test series
without importing `testseries` models into their own model layer, and
without `testseries` ever importing `campus.Section` / `liveclass.
Classroom` back — same "one function is the app boundary" pattern the
design doc references for `assignment`.

Per design doc §6:
  - `campus/bridge.py::create_testseries()` calls this with
    `source="campus"`, `is_paid=False` (campus's own golden constraint —
    also re-enforced server-side in `TestSeries.save()`, defence in
    depth, not a substitute for this).
  - `liveclass/bridge.py::create_testseries()` calls this with
    `source="liveclass"`, `is_paid`/`price_coins` as chosen by the
    teacher at creation time.

Both callers own roster/context resolution — this function never queries
`campus`/`liveclass` itself, it only accepts what the caller already
resolved (golden rule, restated in the design doc's §1 and §6).

BUG FIX (this pass) — `create_context_testseries()`'s roster-notify
block below was importing `_NotifTypeGap` from `.models`, but
`testseries/models.py`'s own module docstring (confirmed against this
pass's upload) says that placeholder class is GONE — `core.models.
Notification.NotifType` now has `TESTSERIES_POSTED` for real, every
call site inside `models.py` itself already reads it directly off
`Notification.NotifType`. This module was never updated to match, so
`from .models import Question, TestSeries, _NotifTypeGap, _notify`
would raise `ImportError` at import time — i.e. this file could not
have been imported successfully once `_NotifTypeGap` was removed from
`models.py`, meaning `create_context_testseries()` was never actually
exercised against the real model. Fixed below: `_NotifTypeGap` import
dropped, `Notification.NotifType.TESTSERIES_POSTED` imported lazily
(function-local) instead, same lazy-import-for-`core`/`user_profile`
reasoning `models.py`'s own `_notify()`/`_record_coin_transaction()`
already use.

ADDITION (this pass) — `get_attempts_for_context()` at the bottom of
this file. `campus/bridge.py::can_review_testseries_attempt()` and a
review endpoint on `campus`'s `TestSeriesViewSet` both need a way to
list `TestAttempt` rows for a `(context_type, context_id)` pair without
importing `TestAttempt` directly — the `testseries` analogue of
`assignment.bridge.get_submissions_for_context()`, which `campus.
bridge.get_assignment_submissions()` already calls the same way.
"""
from django.db import transaction

from .models import Question, TestAttempt, TestSeries, _notify


def create_context_testseries(
    *,
    source: str,
    context_type: str,
    context_id,
    creator,
    title: str,
    description: str = "",
    is_paid: bool = False,
    price_coins: int = 0,
    duration_minutes=None,
    attempts_allowed: int = 1,
    questions: list[dict],
    roster=None,
):
    """
    `questions`: list of dicts matching `Question`'s writable fields
    (`order`, `question_type`, `text`, `attachment`, `marks`, `options`,
    `correct_answer`) — validation shape per design doc §2 still applies,
    each question is `full_clean()`-ed individually below since
    `bulk_create` bypasses `Model.save()`/`clean()`.

    `roster`: iterable of `login.User`, or `None`. Used ONLY to fan out
    the `TESTSERIES_POSTED` notification (§4) — this function never
    queries campus/liveclass to build that list itself, the caller
    already has it (same reasoning `assignment`'s roster param uses).
    Individual/marketplace series never call this function at all (they
    go through `TestSeriesViewSet.create` instead, see views.py), which
    is why "no bulk-notify for individual series" (§4) doesn't need a
    branch here.
    """
    if source not in (TestSeries.Source.CAMPUS, TestSeries.Source.LIVECLASS):
        raise ValueError("create_context_testseries() is only for source='campus'/'liveclass'; "
                          "individual series go through TestSeriesViewSet.create() instead.")

    with transaction.atomic():
        series = TestSeries.objects.create(
            source=source,
            context_type=context_type,
            context_id=context_id,
            creator=creator,
            title=title,
            description=description,
            is_paid=is_paid,
            price_coins=price_coins,
            duration_minutes=duration_minutes,
            attempts_allowed=attempts_allowed,
            status=TestSeries.Status.PUBLISHED,
        )
        question_objs = [Question(series=series, **q_kwargs) for q_kwargs in questions]
        for question in question_objs:
            # full_clean() (not just save()) because bulk_create() below
            # bypasses Model.save() entirely — this is the one place a
            # malformed question shape (§2 validation) gets caught, not
            # left to surface later as a confusing auto-grade bug.
            question.full_clean()
        Question.objects.bulk_create(question_objs)
        series.recompute_total_marks()

    if roster:
        # Lazy import — same reasoning `models.py`'s own `_notify()`/
        # `_record_coin_transaction()` already give for `core`/
        # `user_profile`: avoids a hard import-time cycle between
        # `testseries` and `core`.
        from core.models import Notification

        for user in roster:
            _notify(
                recipient=user,
                notif_type=Notification.NotifType.TESTSERIES_POSTED,
                title=f"New test series: {series.title}",
                message=series.description[:200],
                data={"series_id": str(series.id)},
            )

    return series


def get_attempts_for_context(*, context_type: str, context_id):
    """Returns every `TestAttempt` for every campus/liveclass `TestSeries`
    in this `(context_type, context_id)` — the `testseries` analogue of
    `assignment.bridge.get_submissions_for_context()`, added so
    `campus`/`liveclass` bridge modules have a context-scoped way to
    list attempts for review without ever touching `TestAttempt`/
    `TestSeries` directly (golden rule).

    Unfiltered by permission, same contract `get_submissions_for_
    context()` documents on its own side: the caller (e.g. `campus.
    bridge.can_review_testseries_attempt()`, or a review endpoint in
    `campus/views.py`) is responsible for any further staff/student-
    scoped narrowing.
    """
    return TestAttempt.objects.filter(
        series__context_type=context_type, series__context_id=context_id
    ).select_related("series", "student")


# ---------------------------------------------------------------------------
# Task 16 — "post-result query-to-teacher". Reuses `message.DoubtQuestion`
# (no new model) via the generic `context_type`/`context_id` opaque
# pointer added to it this pass (see `message/models.py`'s docstring on
# `DoubtQuestion`) rather than `testseries` growing its own Q&A model.
#
# Golden rule direction, confirmed for this feature: `testseries` ->
# `message` only (lazy imports below, same reasoning `_notify()`/
# `_record_coin_transaction()` in models.py already give for `core`/
# `user_profile`) — `message` itself never imports `testseries` back;
# `DoubtQuestion.context_type`/`context_id` are bare opaque values to it.
# ---------------------------------------------------------------------------

def ask_query_on_series(*, attempt: "TestAttempt", student, text: str, is_anonymous: bool = False):
    """Student asks the series creator a doubt about their OWN attempt,
    only once it's actually `checked` — exact Task 16 requirement.
    Creates a `message.DoubtQuestion` with `group=None`/`conversation=
    None` (a testseries query has neither) and `context_type=
    "testseries_attempt"`, `context_id=<attempt.id>`.

    Raises plain `ValueError` (not a DRF exception — same "services stay
    HTTP-decoupled" convention `message/services.py`'s own module
    docstring states) for both "not your attempt" and "not checked yet";
    `views.py::TestAttemptViewSet.ask_query` maps this to a clean 400.
    The coarser "do you even have an attempt on this series at all" gate
    is `permissions.CanAskQueryOnCheckedAttempt`, checked before this
    function is ever called.
    """
    from message.models import DoubtQuestion

    if attempt.student_id != student.id:
        raise ValueError("You can only ask a query about your own attempt.")
    if attempt.status != TestAttempt.Status.CHECKED:
        raise ValueError("You can only ask a query after your attempt has been fully checked.")

    return DoubtQuestion.objects.create(
        group=None,
        conversation=None,
        author=student,
        text=text,
        is_anonymous=is_anonymous,
        context_type="testseries_attempt",
        context_id=attempt.id,
    )


def answer_query_on_series(*, doubt_id, teacher, answer_text: str):
    """Answer-side counterpart to `ask_query_on_series()` above.

    ⚠️ Not in Task 16's own "files banegi/badlengi" list, but added
    anyway — without SOME entrypoint that can verify "is this teacher
    actually this series' creator" before calling `message.services.
    answer_doubt_question()`, the acceptance checklist's "teacher
    answers -> student gets TESTSERIES_QUERY_ANSWERED" item has no route
    to actually happen through: `message` itself can never resolve that
    check (golden rule — no `message` -> `testseries` import), so
    `testseries` has to be the one holding this entrypoint. Flagging
    this explicitly rather than quietly leaving the feature half-wired;
    wire `views.py::TestAttemptViewSet.answer_query` (or whatever
    teacher-facing endpoint you prefer) to call this.

    Resolves `doubt.context_id` back to its `TestAttempt` -> `TestSeries`
    here, testseries-side, confirms `teacher == series.creator`, THEN
    calls `message.services.answer_doubt_question(actor=None, ...)` —
    the "already authorized by a trusted caller" convention
    `add_members_to_group`/`update_group_member_role` in that module
    already use, since `message` has no way to independently re-verify
    a testseries creator relationship itself.

    Raises `ValueError` if the doubt doesn't exist / isn't a testseries
    query; `PermissionError` if `teacher` isn't that series' creator —
    `views.py` maps these to a 400 / 403 respectively.
    """
    from message.models import DoubtQuestion
    from message.services import answer_doubt_question

    try:
        doubt = DoubtQuestion.objects.get(pk=doubt_id, context_type="testseries_attempt")
    except DoubtQuestion.DoesNotExist:
        raise ValueError("Query not found.")

    attempt = TestAttempt.objects.select_related("series").filter(pk=doubt.context_id).first()
    if attempt is None or attempt.series.creator_id != teacher.id:
        raise PermissionError("Only the series creator can answer this query.")

    # `actor=None` skips message/services.py's group-admin/mod check (this
    # doubt has no group to check against anyway — see models.py's Task 16
    # comment). `answered_by=teacher` is passed SEPARATELY so the already-
    # verified creator above still ends up recorded as the answerer instead
    # of being silently dropped (previous version of this function passed
    # neither, which meant `DoubtQuestion.answered_by` never got set here —
    # fixed as part of the same Task 16 pass that added `answered_by` as
    # its own parameter to `answer_doubt_question()`).
    return answer_doubt_question(doubt=doubt, actor=None, answer_text=answer_text, answered_by=teacher)