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
"""
from django.db import transaction

from .models import Question, TestSeries, _NotifTypeGap, _notify


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
        for user in roster:
            _notify(
                recipient=user,
                notif_type=_NotifTypeGap.TESTSERIES_POSTED,
                title=f"New test series: {series.title}",
                message=series.description[:200],
                data={"series_id": str(series.id)},
            )

    return series
