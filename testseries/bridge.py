# testseries/bridge.py
"""
Entry point for OTHER apps (`campus`, `tuitionclass`) to create test series
without importing `testseries` models into their own model layer, and
without `testseries` ever importing `campus.Section` / `tuitionclass.
Classroom` back — same "one function is the app boundary" pattern the
design doc references for `assigments`.

Per design doc §6:
  - `campus/bridge.py::create_testseries()` calls this with
    `source="campus"`, `is_paid=False` (campus's own golden constraint —
    also re-enforced server-side in `TestSeries.save()`, defence in
    depth, not a substitute for this).
  - `tuitionclass/bridge.py::create_testseries()` calls this with
    `source="tuitionclass"`, `is_paid`/`price_coins` as chosen by the
    teacher at creation time.

Both callers own roster/context resolution — this function never queries
`campus`/`tuitionclass` itself, it only accepts what the caller already
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
`assigments.bridge.get_submissions_for_context()`, which `campus.
bridge.get_assigments_submissions()` already calls the same way.

TASK 32 — cross-app assumptions this file makes about `message`
confirmed this pass against the real `message/models.py` and
`message/services.py` uploads (see the CONFIRMED notes on
`ask_query_on_series()`/`answer_query_on_series()` below for detail):
  - `DoubtQuestion.context_type`/`context_id` exist as assumed. TRUE.
  - `answer_doubt_question()` accepts `answered_by=`. TRUE.
One new gap surfaced in the process (not a `testseries`-side bug, but
this file's `answer_query_on_series()` is the one exposed to it) —
`answer_doubt_question()`'s own testseries-notify branch depends on
`core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`, which
is NOT confirmed to exist. See the ⚠️ note on `answer_query_on_series()`
below.
"""
import importlib
import logging

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from . import policy
from .models import Question, TestAttempt, TestSeries, _notify

logger = logging.getLogger(__name__)


# Writable `Question` fields a bridge caller may pass (anything else is ignored,
# exactly like a DRF serializer would — never a TypeError out of `Question(**kw)`).
_QUESTION_FIELDS = frozenset({
    "order", "question_type", "text", "attachment", "marks", "negative_marks",
    "topic", "difficulty", "options", "correct_answer", "explanation",
})


class QuestionPayloadError(ValueError):
    """T2 — `questions` for `create_context_testseries()` had one or more bad
    items. `.errors` = `[{"index", "question_number", "message"}, ...]` so the
    calling app can answer with a per-question 400 instead of a bare 500."""

    def __init__(self, errors):
        first = errors[0]["message"] if errors else "Invalid questions."
        super().__init__(f"Nothing was saved. {first}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))
        self.errors = errors


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
    questions: list[dict] | None = None,
    roster=None,
    draft: bool = False,
):
    """
    `questions`: list of dicts matching `Question`'s writable fields
    (`order`, `question_type`, `text`, `attachment`, `marks`, `options`,
    `correct_answer`) — validation shape per design doc §2 still applies,
    each question is `full_clean()`-ed individually below since
    `bulk_create` bypasses `Model.save()`/`clean()`.

    `roster`: iterable of `login.User`, or `None`. Used ONLY to fan out
    the `TESTSERIES_POSTED` notification (§4) — this function never
    queries campus/tuitionclass to build that list itself, the caller
    already has it (same reasoning `assigments`'s roster param uses).
    Individual/marketplace series never call this function at all (they
    go through `TestSeriesViewSet.create` instead, see views.py), which
    is why "no bulk-notify for individual series" (§4) doesn't need a
    branch here.

    T2 — `draft=True` creates the series as a DRAFT (questions optional): the
    creator and the context's authorised editors then add / edit / delete
    questions through the question endpoints and publish when ready. A draft is
    neither announced nor notified — that happens in `TestSeriesViewSet.
    publish`. `draft=False` (default) keeps the old "created PUBLISHED with its
    questions" behaviour exactly.

    A malformed question raises `QuestionPayloadError` (a `ValueError`) BEFORE
    anything is committed.
    """
    if source not in (TestSeries.Source.CAMPUS, TestSeries.Source.TUITIONCLASS):
        raise ValueError("create_context_testseries() is only for source='campus'/'tuitionclass'; "
                          "individual series go through TestSeriesViewSet.create() instead.")

    # TASK 9.1 — a class / campus series is ALWAYS free, whatever the caller
    # passed (TestSeries.save() would also force it; doing it here keeps the
    # value the caller sees and the stored one identical).
    is_paid, price_coins = policy.normalize_pricing(
        source=source, is_paid=is_paid, price_coins=price_coins, strict=False
    )

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
            status=TestSeries.Status.DRAFT if draft else TestSeries.Status.PUBLISHED,
        )
        question_objs, problems = [], []
        for index, raw in enumerate(questions or []):
            if not isinstance(raw, dict):
                problems.append({"index": index, "question_number": index + 1,
                                 "message": f"Q{index + 1}: each question must be an object."})
                continue
            kwargs = {k: v for k, v in raw.items() if k in _QUESTION_FIELDS}
            kwargs.setdefault("order", index + 1)
            question = Question(series=series, **kwargs)
            try:
                # full_clean() (not just save()) because bulk_create() below
                # bypasses Model.save() entirely — this is the one place a
                # malformed question shape (§2 validation) gets caught, not
                # left to surface later as a confusing auto-grade bug.
                question.full_clean()
            except DjangoValidationError as exc:
                problems.append({"index": index, "question_number": index + 1,
                                 "message": f"Q{index + 1}: {' '.join(exc.messages)}"})
            else:
                question_objs.append(question)
        if problems:
            # Raised inside the atomic block: the series row above is rolled back.
            raise QuestionPayloadError(problems)
        Question.objects.bulk_create(question_objs)
        series.recompute_total_marks()

    if draft:
        return series  # nothing to announce until it is published

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

    # TASK 9.2 — announce to the class (notice board + notification/push) once
    # the series row is really committed. `roster_notified` stops a caller that
    # already passed a roster (campus) from being notified a second time.
    roster_notified = bool(roster)
    transaction.on_commit(lambda: announce_series_published(series, roster_notified=roster_notified))

    return series


# ---------------------------------------------------------------------------
# TASK 9.2 — "a class / campus series was published": tell the context.
#
# Golden rule: `testseries` never imports `tuitionclass` / `campus`. Exactly like
# access.py's TESTSERIES_CONTEXT_ACCESS, the owning app registers a function by
# dotted path:
#
#     TESTSERIES_PUBLISH_HOOKS = {"classroom": "tuitionclass.bridge.on_testseries_published"}
#
# (that mapping is the default, settings can override/extend it). The hook gets
# ONE argument — a plain dict (see `_announce_payload`) — and does the context's
# own announcement: for a classroom a notice-board entry + bell/push for the
# pass holders. A `context_type` with no hook (campus "section" today, it
# notifies its roster in create_context_testseries) is simply not announced here.
#
# Exactly-once: `TestSeries.announced_at` is claimed with one conditional UPDATE
# before the hook runs, so publishing twice, a retry, or two workers racing can
# never announce the same series twice. If the hook raises, the claim is
# released (and the failure logged) so a later publish/retry can try again; the
# hook itself is idempotent for the notice board entry (unique source key).
# ---------------------------------------------------------------------------
DEFAULT_PUBLISH_HOOKS = {"classroom": "tuitionclass.bridge.on_testseries_published"}


def _publish_hook_target(context_type):
    mapping = getattr(settings, "TESTSERIES_PUBLISH_HOOKS", None) or {}
    return mapping.get(context_type, DEFAULT_PUBLISH_HOOKS.get(context_type))


def _load_callable(path: str):
    module_path, _, func_name = path.rpartition(".")
    return getattr(importlib.import_module(module_path), func_name)


def _announce_payload(series, *, notify_roster: bool) -> dict:
    return {
        "series_id": str(series.id),
        "context_type": series.context_type,
        "context_id": series.context_id,  # UUID, as stored
        "creator_id": series.creator_id,
        "title": series.title,
        "description": series.description or "",
        "delivery_mode": series.delivery_mode,
        "starts_at": series.starts_at,
        "ends_at": series.ends_at,
        "duration_minutes": series.duration_minutes,
        "total_marks": series.total_marks,
        "notify_roster": notify_roster,
    }


def announce_series_published(series, *, roster_notified: bool = False) -> bool:
    """Announce a PUBLISHED campus / tuition-class series to its context, at most
    once per series. Returns True only when this call did the announcing.
    Never raises (it runs after the publish already succeeded)."""
    try:
        if not policy.is_always_free(series.source) or series.status != TestSeries.Status.PUBLISHED:
            return False
        target = _publish_hook_target(series.context_type)
        if not target:
            return False

        claimed = TestSeries.objects.filter(
            pk=series.pk, announced_at__isnull=True, status=TestSeries.Status.PUBLISHED
        ).update(announced_at=timezone.now())
        if not claimed:
            return False  # already announced (or unpublished meanwhile)

        try:
            _load_callable(target)(_announce_payload(series, notify_roster=not roster_notified))
        except Exception:
            logger.exception("testseries publish hook %s failed for series %s", target, series.pk)
            TestSeries.objects.filter(pk=series.pk).update(announced_at=None)  # allow a retry
            return False
        return True
    except Exception:  # pragma: no cover - last-resort guard, publish must never fail on this
        logger.exception("announce_series_published crashed for series %s", getattr(series, "pk", None))
        return False


def get_attempts_for_context(*, context_type: str, context_id):
    """Returns every `TestAttempt` for every campus/tuitionclass `TestSeries`
    in this `(context_type, context_id)` — the `testseries` analogue of
    `assigments.bridge.get_submissions_for_context()`, added so
    `campus`/`tuitionclass` bridge modules have a context-scoped way to
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

    TASK 32 — CONFIRMED (this pass, against the real `message/models.py`
    upload): `context_type`/`context_id` exist exactly as assumed here —
    nullable `CharField`/`UUIDField` on `DoubtQuestion`, backed by an
    `Index(fields=['context_type', 'context_id'])` and a
    `CheckConstraint` requiring either `group` or this pair to be set.
    No longer an unverified assumption.

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

    TASK 32 — CONFIRMED (this pass, against the real `message/
    services.py` upload): `answer_doubt_question()` really does accept
    `answered_by=` as its own separate parameter (defaults to `actor`
    when omitted), and its own docstring names this exact call site by
    name as the reason that parameter exists. No longer an unverified
    assumption.

    ⚠️ NEW GAP surfaced by this confirmation pass (not previously
    flagged here) — `answer_doubt_question()` unconditionally notifies
    the student when `doubt.context_type == 'testseries_attempt'`, via
    `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`.
    That enum member is flagged as NOT CONFIRMED to exist yet in
    `message/services.py`'s own comment. If it's still missing, the call
    below raises a plain `AttributeError` — *after* the doubt row has
    already been saved as answered (fields commit first, notify is the
    last step) — meaning this function can raise an undocumented,
    unhandled `AttributeError` up to `views.py` even though the answer
    itself succeeded. Deliberately NOT papered over with a try/except
    here (same "flag the gap, don't guess" convention `models.py` uses
    for its own unconfirmed `NotifType` members) — the real fix is
    `core` adding `TESTSERIES_QUERY_ANSWERED`, not swallowing the error.
    `views.py::TestAttemptViewSet.answer_query` should catch
    `AttributeError` alongside `ValueError`/`PermissionError` until
    that's confirmed, or callers will see an opaque 500.
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