# user_profile/views/activity_views.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from ..activity import (
    ActivityLimitSerializer,
    HeartbeatSerializer,
)
from ..models import (
    DailyUsage,
    Streak,
    UserPreference,
)
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.throttling import (
    ScopedRateThrottle,
    UserRateThrottle,
)
from .. import activity
from .. import role_settings
from rest_framework import status
from django.utils import timezone
from ._common import logger


# ---------------------------------------------------------------------------
# P14-BE — activity log + time spent
# ---------------------------------------------------------------------------
class ActivityView(GenericAPIView):
    """GET/PATCH /profile/activity/ — the "Your activity" screen.

    GET: last 7 local days (oldest -> newest, zero-filled) of time spent
    (`DailyUsage`) + likes / comments / shares the user made, week totals,
    today's usage vs. the optional daily limit, and the user's most recent
    saved / liked posts (`?limit=`, default 20, max 50). A likes/comments/
    shares value of `null` means "couldn't be computed" (post-model lookup
    failed, see activity.py), not zero.

    PATCH {"daily_limit_minutes": 60 | null}: set / clear the daily-limit
    reminder (stored on `UserPreference`; null or 0 turns it off).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = ActivityLimitSerializer
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_activity"

    @extend_schema(
        parameters=[OpenApiParameter("limit", OpenApiTypes.INT, description="Saved/liked list size (max 50).")],
        description="Last 7 days of time spent + likes/comments/shares made, plus saved and liked posts.",
    )
    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", activity.DEFAULT_LIST_LIMIT))
        except (TypeError, ValueError):
            limit = activity.DEFAULT_LIST_LIMIT
        limit = max(1, min(limit, activity.MAX_LIST_LIMIT))

        dates = activity.window_dates()
        usage_rows = dict(
            DailyUsage.objects.filter(user=request.user, date__gte=dates[0])
            .values_list("date", "seconds")
        )
        days, totals = activity.build_week(request.user, usage_rows)

        preference = UserPreference.objects.filter(user=request.user).first()
        daily_limit = getattr(preference, "daily_limit_minutes", None)
        today_seconds = usage_rows.get(dates[-1], 0)

        return Response({
            "status": True,
            "message": "Activity fetched successfully.",
            "data": {
                "range": {"start": dates[0].isoformat(), "end": dates[-1].isoformat(), "days": len(dates)},
                "days": days,
                "totals": totals,
                "today": {
                    "seconds": today_seconds,
                    "daily_limit_minutes": daily_limit,
                    "limit_reached": bool(daily_limit and today_seconds >= daily_limit * 60),
                },
                "daily_limit_minutes": daily_limit,
                "parent_limit_minutes": role_settings.parent_limit_floor(request.user),  # S12 (null = no parent limit)
                "saved_posts": activity.saved_posts(request.user, limit),
                "liked_posts": activity.liked_posts(request.user, limit),
            },
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=ActivityLimitSerializer,
        responses={200: ActivityLimitSerializer},
        description="Set or clear (null / 0) the daily time-limit reminder.",
    )
    def patch(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        limit = serializer.validated_data["daily_limit_minutes"]
        # S12: a parent-set limit can be lowered by the child, never raised or removed.
        floor = role_settings.parent_limit_floor(request.user)
        if floor is not None and (limit is None or limit > floor):
            return Response({
                "status": False,
                "code": "limit_set_by_parent",
                "message": f"Your parent set a daily limit of {floor} minutes. You can only choose a lower limit.",
                "parent_limit_minutes": floor,
            }, status=status.HTTP_403_FORBIDDEN)
        preference = UserPreference.for_user(request.user)
        preference.daily_limit_minutes = limit
        preference.save(update_fields=["daily_limit_minutes", "updated_at"])

        # A changed limit re-arms today's one-per-day nudge.
        DailyUsage.objects.filter(user=request.user, date=timezone.localdate()).update(limit_notified=False)

        return Response({
            "status": True,
            "message": "Daily limit updated successfully.",
            "data": {"daily_limit_minutes": limit},
        }, status=status.HTTP_200_OK)


class ActivityHeartbeatView(GenericAPIView):
    """POST /profile/activity/heartbeat/ {"seconds": 30} — the app calls this
    every ~30-60 s while it is in the FOREGROUND (stop on pause/background),
    sending the foreground seconds since its previous heartbeat. Bounds and
    anti-inflation rules: `DailyUsage.objects.record_heartbeat`.

    Returns today's total and, when this beat is the one that crossed the
    user's daily limit, `limit_reached: true` (once per day) — the client can
    show its own nudge from that; a bell notification is also written.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = HeartbeatSerializer
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_activity_heartbeat"

    @extend_schema(request=HeartbeatSerializer, description="Foreground time heartbeat.")
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        # T2.3 - heartbeat "best effort": lock 3 retry ke baad bhi na khule to 200 {ok: false} (client agli beat me
        # wahi seconds dobara bhej sakta hai), 500 nahi.
        from common.db_retry import best_effort_write

        _beat = best_effort_write(
            lambda: DailyUsage.objects.record_heartbeat(request.user, serializer.validated_data["seconds"]),
            default=None, label="activity.heartbeat",
        )
        if _beat is None:
            return Response({
                "status": False,
                "ok": False,
                "message": "Heartbeat abhi save nahi hua.",
                "code": "heartbeat_busy",
            }, status=status.HTTP_200_OK)
        usage, credited, limit_reached = _beat

        if limit_reached:
            try:
                from core.models import Notification

                from ..services import _notify

                minutes = UserPreference.objects.filter(user=request.user).values_list(
                    "daily_limit_minutes", flat=True
                ).first()
                _notify(
                    request.user,
                    getattr(Notification.NotifType, "DAILY_LIMIT_REACHED", "daily_limit_reached"),
                    "Daily limit reached ⏰",
                    f"You've spent {minutes} minutes on LearnScroll today — time for a break?",
                    data={"type": "daily_limit_reached", "daily_limit_minutes": minutes},
                )
            except Exception:
                logger.exception("daily limit notification failed (user=%s)", request.user.pk)

        # Daily goal: har heartbeat pe check (aaj complete ho chuka ho to bina
        # lock ke turant laut aata hai). Fail ho to heartbeat kabhi fail nahi hona chahiye.
        goal = None
        try:
            goal = Streak.objects.evaluate_daily_goal(request.user)
        except Exception:
            logger.exception("daily goal evaluation failed (user=%s)", request.user.pk)

        return Response({
            "status": True,
            "ok": True,
            "message": "Heartbeat recorded.",
            "data": {"today_seconds": usage.seconds, "credited_seconds": credited},
            "limit_reached": limit_reached,
            "goal": goal,
        }, status=status.HTTP_200_OK)
