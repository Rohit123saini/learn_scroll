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

    ⚠️ GAP (same shape as the CoinLedger/NotifType gaps flagged in
    models.py — explicitly called out rather than guessed at):
    `campus.bridge.can_review_testseries_attempt(user, context_type,
    context_id) -> bool` does not exist yet. Until `campus` adds it,
    a campus subject-teacher will get `False` here — a safe default
    (denies access) rather than silently granting a review permission
    this app can't actually verify.
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
