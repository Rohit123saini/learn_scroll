"""
leaderboard/views.py

Task G7 — read-only API over `LeaderboardEntry`. Every view here only
reads (via `services.get_board`/`get_my_rank`); recompute is entirely
`tasks.py`'s job (see that module's docstring). Two endpoints, reused
across all three scopes (Task G7's "reusable widget across
testseries/campus/tuitionclass" — the frontend uses one widget + one service
call with a different `scope_type`/`scope_id`):

    GET /api/leaderboard/board/?scope_type=test_series&scope_id=<uuid>&period=weekly
    GET /api/leaderboard/board/?scope_type=engagement&period=all_time
    GET /api/leaderboard/board/?scope_type=campus_section&scope_id=<uuid>&period=weekly

    GET /api/leaderboard/my-rank/?scope_type=...&scope_id=...&period=...
"""
from rest_framework import permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from . import permissions as board_permissions
from . import services
from .models import LeaderboardEntry
from .serializers import LeaderboardEntrySerializer

_SCOPE_CHOICES = {c for c, _ in LeaderboardEntry.ScopeType.choices}
_PERIOD_CHOICES = {c for c, _ in LeaderboardEntry.PeriodType.choices}

# Page size options a client may request; anything else falls back to the
# default. Kept small and fixed — a leaderboard tab is a top-N list, not a
# full paginated table.
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _parse_scope(request):
    """Shared query-param parsing + authorization for both views below.
    Raises `ValidationError` (400) on a malformed request; returns a 403
    `Response` (not raised) when the scope is well-formed but the user
    isn't allowed to see it, so callers can `return` it directly."""
    scope_type = request.query_params.get("scope_type")
    if scope_type not in _SCOPE_CHOICES:
        raise ValidationError({"scope_type": f"Must be one of {sorted(_SCOPE_CHOICES)}."})

    period = request.query_params.get("period", LeaderboardEntry.PeriodType.WEEKLY)
    if period not in _PERIOD_CHOICES:
        raise ValidationError({"period": f"Must be one of {sorted(_PERIOD_CHOICES)}."})

    scope_id = request.query_params.get("scope_id") or None
    if scope_type == LeaderboardEntry.ScopeType.ENGAGEMENT:
        scope_id = None
    elif not scope_id:
        raise ValidationError({"scope_id": "Required for this scope_type."})

    if scope_type == LeaderboardEntry.ScopeType.TEST_SERIES:
        allowed = board_permissions.can_view_test_series_board(request.user, scope_id)
    elif scope_type == LeaderboardEntry.ScopeType.CAMPUS_SECTION:
        allowed = board_permissions.can_view_campus_section_board(request.user, scope_id)
    else:
        allowed = board_permissions.can_view_engagement_board(request.user)

    if not allowed:
        return scope_type, scope_id, period, Response(
            {"detail": "You don't have access to this leaderboard."}, status=status.HTTP_403_FORBIDDEN
        )
    return scope_type, scope_id, period, None


def _period_key(period):
    if period == LeaderboardEntry.PeriodType.WEEKLY:
        return services.current_week_key()
    return "all"


class LeaderboardBoardAPIView(APIView):
    """Paged, ranked list for one board."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        scope_type, scope_id, period, denied = _parse_scope(request)
        if denied is not None:
            return denied

        try:
            limit = min(int(request.query_params.get("limit", _DEFAULT_LIMIT)), _MAX_LIMIT)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            raise ValidationError({"limit/offset": "Must be integers."})

        period_key = _period_key(period)
        rows, total = services.get_board(scope_type, scope_id, period, period_key, limit=limit, offset=offset)
        my_entry = services.get_my_rank(request.user, scope_type, scope_id, period, period_key)

        return Response({
            "scope_type": scope_type,
            "scope_id": scope_id,
            "period": period,
            "period_key": period_key,
            "count": total,
            "results": LeaderboardEntrySerializer(rows, many=True, context={"request": request}).data,
            "my_rank": LeaderboardEntrySerializer(my_entry, context={"request": request}).data if my_entry else None,
        })


class MyLeaderboardRankAPIView(APIView):
    """Just the caller's own row — for a compact "You're #14 this week"
    chip (profile / result screen) without pulling a whole page."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        scope_type, scope_id, period, denied = _parse_scope(request)
        if denied is not None:
            return denied

        period_key = _period_key(period)
        entry = services.get_my_rank(request.user, scope_type, scope_id, period, period_key)
        if entry is None:
            return Response({
                "scope_type": scope_type, "scope_id": scope_id, "period": period,
                "period_key": period_key, "rank": None, "score": None,
            })
        return Response(LeaderboardEntrySerializer(entry, context={"request": request}).data)
