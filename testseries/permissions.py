# testseries/permissions.py
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import TestSeries


class IsSeriesCreatorOrReadOnly(BasePermission):
    """Anyone can read a (visible) series; only its creator can write to
    it. Campus/liveclass creation itself is gated further upstream —
    only their own bridge-wrapped endpoints call `bridge.
    create_context_testseries()` in the first place (§5) — this class
    only covers the direct `TestSeriesViewSet` (individual/marketplace)
    path."""

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        return obj.creator_id == request.user.id


def user_can_review_attempt(user, attempt) -> bool:
    """§5: reviewing is allowed for `series.creator` (covers both
    `individual` and `liveclass` — liveclass's teacher IS the creator)
    or, for `source="campus"`, the campus subject-teacher for that
    context — resolved by campus itself, never by this app directly
    (golden rule: no `campus.Section`/staff imports here).

    RESOLVED (TASK 19) — `campus.bridge.can_review_testseries_attempt(
    user, context_type, context_id) -> bool` now exists (confirmed
    directly against the real `campus/bridge.py`, keyword-only
    `user`/`context_type`/`context_id`, exactly matching the call
    below), so the `try/except ImportError` path is effectively dead
    code today — kept in place as a defensive fallback (still a safe
    `False`/deny, not a crash, if that ever changes) rather than
    removed outright, since removing it buys nothing and a bare
    `campus.bridge` import here would be the one `testseries -> campus`
    coupling this app otherwise has none of.
    """
    series = attempt.series
    if series.creator_id == user.id:
        return True
    if series.source == TestSeries.Source.CAMPUS:
        try:
            from campus.bridge import can_review_testseries_attempt
        except ImportError:
            return False
        return can_review_testseries_attempt(
            user=user, context_type=series.context_type, context_id=series.context_id,
        )
    return False


class CanReviewCheckedAttempt(BasePermission):
    """Task 15 — gate for `TestSeriesReviewViewSet.create()`.

    Deliberately coarse: this only checks that the requesting user has
    *some* `TestAttempt` at all on the series being reviewed (a 403 for
    "you were never even a participant here"). Whether that attempt has
    actually reached `status="checked"` yet — the acceptance-checklist
    requirement ("attempt.status != checked -> clean 400") — is
    intentionally NOT decided here. That's a business-rule check with a
    real payload contract (a 400 the client is meant to show/retry,
    not a 403 permission wall), so it lives in `TestSeriesReviewSerializer
    .validate()` instead — same split `IsSeriesCreatorOrReadOnly`'s
    coarse creator-check already has vs. `TestSeriesSerializer.
    validate()`'s finer is_paid/price_coins business rule.
    """

    message = "You must have attempted this series to review it."

    def has_permission(self, request, view):
        if view.action != "create":
            return True
        series_pk = view.kwargs.get("series_pk")
        if not series_pk or not (request.user and request.user.is_authenticated):
            return False
        from .models import TestAttempt

        return TestAttempt.objects.filter(series_id=series_pk, student=request.user).exists()


class CanAskQueryOnCheckedAttempt(BasePermission):
    """Task 16 — gate for `TestAttemptViewSet.ask_query`.

    Deliberately coarse, same split as `CanReviewCheckedAttempt` above:
    this only confirms the requesting user is asking about their OWN
    attempt (a 403 for "this isn't even your attempt"). Whether that
    attempt has actually reached `status="checked"` yet — the
    acceptance-checklist requirement ("attempt.status != checked ->
    clean 400") — is intentionally NOT decided here; that's a business
    rule with a real payload contract, so it lives in
    `bridge.ask_query_on_series()` (raises plain `ValueError`), caught
    by the view and surfaced as a 400, not a 403.

    Object-level check only — `has_permission` just requires auth,
    since `get_object()` (which this runs against) already does the
    "own attempt OR permitted reviewer" resolution; narrowing to
    "must be the attempt's own student" happens here.
    """

    message = "You can only ask a query about your own attempt."

    def has_object_permission(self, request, view, obj):
        return obj.student_id == request.user.id