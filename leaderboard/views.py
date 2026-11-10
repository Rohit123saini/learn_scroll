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
    GET /api/leaderboard/board/?scope_type=study_time&scope_id=<conversation uuid>&period=weekly   (S5: study room minutes)

    GET /api/leaderboard/my-rank/?scope_type=...&scope_id=...&period=...
"""
import uuid

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
    elif scope_type == LeaderboardEntry.ScopeType.STUDY_TIME:
        # S5 — scope_id = Conversation.id (study room board); khali = app-wide board (settings se allowed ho to).
        if scope_id:
            try:
                scope_id = str(uuid.UUID(str(scope_id)))
            except ValueError:
                raise ValidationError({"scope_id": "Must be a valid conversation id."})
    elif not scope_id:
        raise ValidationError({"scope_id": "Required for this scope_type."})

    if scope_type == LeaderboardEntry.ScopeType.TEST_SERIES:
        allowed = board_permissions.can_view_test_series_board(request.user, scope_id)
    elif scope_type == LeaderboardEntry.ScopeType.CAMPUS_SECTION:
        allowed = board_permissions.can_view_campus_section_board(request.user, scope_id)
    elif scope_type == LeaderboardEntry.ScopeType.STUDY_TIME:
        allowed = board_permissions.can_view_study_time_board(request.user, scope_id)
    else:
        allowed = board_permissions.can_view_engagement_board(request.user)

    if not allowed:
        return scope_type, scope_id, period, Response(
            {"detail": "You don't have access to this leaderboard."}, status=status.HTTP_403_FORBIDDEN
        )
    return scope_type, scope_id, period, None


# P-F16 -- boards that any signed-in stranger can open. A minor's identity is masked there for adult / unknown-age viewers.
# campus_section boards (classmates, staff, linked parents) and per-room study boards (room members) are not masked.
def _is_public_board(scope_type, scope_id) -> bool:
    return scope_type in (LeaderboardEntry.ScopeType.ENGAGEMENT, LeaderboardEntry.ScopeType.TEST_SERIES) or (
        scope_type == LeaderboardEntry.ScopeType.STUDY_TIME and not scope_id
    )


def _masked_minor_ids(request, scope_type, scope_id, entries):
    if not _is_public_board(scope_type, scope_id):
        return set()
    from common import minor_policy

    ids = [getattr(e, "user_id", None) for e in entries if e is not None]
    return minor_policy.masked_ids_for_viewer(request.user, ids, feature=minor_policy.LEADERBOARD)


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

        # Block applies here too: nobody in a block relationship with me shows up on my board
        # (ranks of the others are not renumbered - that would change real scores' positions).
        from user_profile.services import blocked_user_ids

        hidden = blocked_user_ids(request.user)
        if hidden:
            rows = [r for r in rows if getattr(r, "user_id", None) not in hidden]

        ctx = {"request": request, "masked_minor_ids": _masked_minor_ids(request, scope_type, scope_id, rows)}
        return Response({
            "scope_type": scope_type,
            "scope_id": scope_id,
            "period": period,
            "period_key": period_key,
            "count": total,
            "results": LeaderboardEntrySerializer(rows, many=True, context=ctx).data,
            "my_rank": LeaderboardEntrySerializer(my_entry, context=ctx).data if my_entry else None,
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
