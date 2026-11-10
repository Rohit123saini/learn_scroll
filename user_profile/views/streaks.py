# user_profile/views/streaks.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from ..models import (
    CoinLedgerBusy,
    SpendLimitExceeded,
    Streak,
    StreakFreezeError,
    WeeklyRecap,
)
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.throttling import (
    ScopedRateThrottle,
    UserRateThrottle,
)
from ..serializers import (
    StreakSerializer,
    WeeklyRecapSerializer,
)
from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404
from django.conf import settings
from rest_framework import status
from ._common import _wallet_busy_response


class StreakView(GenericAPIView):
    """
    TASK G1 (growth_and_feature_tasks.md — Streaks) — GET/POST
    /profile/streak/, same GET-always-200 / lazy-row shape as
    `UserPreferenceView` right above (`Streak.objects.get_or_create`
    instead of `UserPreference.for_user`, same idea).

    GET: read-only — current streak state, for the profile screen and
    the home-app-bar streak chip. Never advances the streak itself, so
    polling it (e.g. on every app-bar rebuild) is always safe.

    POST: today's check-in — the ONE call site that should ever advance
    a user's streak. The frontend calls this once per app session (e.g.
    from the home screen's `initState`), not on every screen or every
    API call — `Streak.objects.record_activity()` is already itself a
    same-day no-op, so an accidental extra POST call the same day is
    harmless, but it's still one deliberate call, not a per-request side
    effect threaded through unrelated views.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = StreakSerializer

    @extend_schema(
        responses={200: StreakSerializer},
        description="Get the current user's streak state (current/longest streak, "
                     "whether today is already checked in).",
    )
    def get(self, request):
        streak, _created = Streak.objects.get_or_create(user=request.user)
        serializer = self.get_serializer(streak)
        return Response({
            "status": True,
            "message": "Streak fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=None,
        responses={200: StreakSerializer},
        description="Record today's check-in (once-a-day primary action). Idempotent — "
                     "calling this again the same day makes no further change. Returns "
                     "the updated streak plus milestone_reached/bonus_coins if a streak "
                     "milestone (settings.STREAK_MILESTONE_DAYS) was just hit.",
    )
    def post(self, request):
        # T2.3 - streak "best effort": lock 3 retry ke baad bhi na khule to 500 nahi, 200 {ok: false}.
        from common.db_retry import best_effort_write

        _result = best_effort_write(
            lambda: Streak.objects.record_activity(request.user), default=None, label="streak.record_activity",
        )
        if _result is None:
            return Response({
                "status": False,
                "ok": False,
                "message": "Streak abhi save nahi ho paya, thodi der me dobara try hoga.",
                "code": "streak_busy",
            }, status=status.HTTP_200_OK)
        streak, milestone_hit = _result

        bonus = 0
        if milestone_hit is not None:
            # Lazy imports — same circular-import reasoning UserPreferenceView's
            # neighbours above already document for core.models/.services.
            from core.models import Notification

            from ..services import _notify

            bonus = settings.STREAK_MILESTONE_BONUS_COINS.get(milestone_hit, 0)
            _notify(
                request.user,
                Notification.NotifType.STREAK_MILESTONE_REACHED,
                f"🔥 {milestone_hit}-day streak!",
                (
                    f"You've kept LearnScroll going for {milestone_hit} days in a row"
                    + (f" — +{bonus} coins credited." if bonus else ".")
                ),
                data={"streak_days": milestone_hit, "bonus_coins": bonus},
            )

        serializer = self.get_serializer(streak)
        return Response({
            "status": True,
            "message": "Streak updated successfully.",
            "data": serializer.data,
            "milestone_reached": milestone_hit,
            "bonus_coins": bonus,
            # Aaj kitne freeze tokens kharch hue (0 = koi nahi) — client "freeze used" dikha sakta hai.
            "freeze_used": getattr(streak, "freezes_consumed_now", 0),
        }, status=status.HTTP_200_OK)


class StreakFreezeBuyView(GenericAPIView):
    """POST /profile/streak/freeze/ — coins se ek streak freeze token kharido.

    Cost aur holding-cap `settings.STREAK_FREEZE_COST_COINS` /
    `STREAK_FREEZE_MAX_TOKENS` se (GET /profile/streak/ bhi dono batata hai,
    client hardcode na kare). Token missed din par `Streak.objects.
    record_activity` apne-aap kharch karta hai.

    201 -> {status, message, data: <streak>, coin_balance}
    400 -> cap poora (code=freeze_limit) ya coins kam (code=insufficient_coins)
    503 -> wallet busy, retry.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = StreakSerializer
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_streak_freeze"

    @extend_schema(request=None, responses={201: StreakSerializer})
    def post(self, request):
        try:
            streak = Streak.objects.buy_freeze(request.user)
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except StreakFreezeError as exc:
            return Response(
                {"status": False, "code": "freeze_limit", "message": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except SpendLimitExceeded as exc:  # S9: user ki spend limit / minor cap (ValueError ka subclass, isliye pehle)
            return Response(
                {"status": False, "code": "spend_limit", "message": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        except ValueError:
            # CoinLedger.record_transaction: balance negative ho jaata.
            return Response(
                {"status": False, "code": "insufficient_coins",
                 "message": "Freeze kharidne ke liye coins kam hain."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        coin_balance = type(request.user).objects.filter(pk=request.user.pk).values_list(
            "coin", flat=True
        ).first()
        return Response({
            "status": True,
            "message": "Streak freeze mil gaya.",
            "data": self.get_serializer(streak).data,
            "coin_balance": coin_balance,
        }, status=status.HTTP_201_CREATED)


class DailyGoalView(GenericAPIView):
    """GET/PATCH /profile/daily-goal/ — "aaj ka N minute challenge".

    Progress `DailyUsage` (foreground heartbeat, /profile/activity/heartbeat/)
    se aata hai — koi alag timer nahi. GET aaj ka state deta hai (aur agar
    goal ab poora ho chuka ho to use turant complete mark karta hai).
    PATCH {"goal_minutes": 20} — sirf `settings.STREAK_DAILY_GOAL_OPTIONS` me se.
    """

    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_daily_goal"

    @extend_schema(description="Today's daily-goal progress.")
    def get(self, request):
        return Response({
            "status": True,
            "message": "Daily goal fetched successfully.",
            "data": Streak.objects.evaluate_daily_goal(request.user),
        }, status=status.HTTP_200_OK)

    @extend_schema(description="Change the daily-goal minutes.")
    def patch(self, request):
        try:
            minutes = int(request.data.get("goal_minutes"))
        except (TypeError, ValueError):
            return Response(
                {"status": False, "message": "goal_minutes number hona chahiye."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            state = Streak.objects.set_daily_goal(request.user, minutes)
        except ValueError as exc:
            return Response({"status": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({
            "status": True,
            "message": "Daily goal updated.",
            "data": state,
        }, status=status.HTTP_200_OK)


class WeeklyRecapView(GenericAPIView):
    """TASK G2 (growth_and_feature_tasks.md) — GET the current user's most
    recent `WeeklyRecap` row.

    Read-only by design: recaps are only ever produced by the
    `generate_weekly_recaps` celery task (tasks.py) every Sunday night —
    this view never generates one on the fly, same "one sanctioned write
    path" boundary `StreakView` already draws for `Streak` (see that
    class's own docstring). A user who hasn't had a recap generated yet
    (brand-new signup, or the very first Sunday hasn't run yet) gets a
    clean 404 rather than a fabricated all-zero row.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = WeeklyRecapSerializer

    @extend_schema(
        responses={200: WeeklyRecapSerializer},
        description="Get the current user's most recent weekly recap "
                    "(tests attempted, classes attended, likes received, streak).",
    )
    def get(self, request):
        recap = (
            WeeklyRecap.objects.filter(user=request.user).order_by("-week_start").first()
        )
        if recap is None:
            return Response({
                "status": False,
                "message": "No recap generated yet — check back after your first full week.",
                "data": None,
            }, status=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(recap)
        return Response({
            "status": True,
            "message": "Recap fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)


class WeeklyRecapCardAPIView(GenericAPIView):
    """TASK G2 — GET a shareable PNG "Your Week" card for one of the
    current user's OWN recaps (recap_card.py does the actual drawing).

    Scoped to `request.user`'s own rows only (`WeeklyRecap.objects.filter
    (user=request.user, id=recap_id)` below) — same "you can only ever act
    on your own row" boundary `CoinWithdrawalRequestView` already draws for
    withdrawal requests; there's no legitimate reason for user A to fetch
    user B's recap card image, even though the numbers on it aren't
    otherwise sensitive.

    Returns HTTP 501 (not a 500) if Pillow isn't installed on this
    deployment — same `ImageUnavailable`-to-501 contract
    `TestCertificatePdfView` (testseries/views.py) already uses for
    `reportlab`/`PdfUnavailable`, so a missing optional dependency is a
    clean, documented response instead of an unhandled crash.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        responses={200: OpenApiTypes.BINARY},
        description="Render this recap as a shareable PNG card (image/png).",
    )
    def get(self, request, recap_id):
        from ..recap_card import ImageUnavailable, render_png

        recap = get_object_or_404(WeeklyRecap, id=recap_id, user=request.user)

        try:
            png_bytes = render_png(
                username=request.user.username,
                week_start=recap.week_start,
                week_end=recap.week_end,
                tests_attempted=recap.tests_attempted,
                classes_attended=recap.classes_attended,
                posts_liked_received=recap.posts_liked_received,
                streak_days=recap.streak_days,
            )
        except ImageUnavailable as exc:
            return Response({
                "status": False,
                "message": str(exc),
                "data": None,
            }, status=status.HTTP_501_NOT_IMPLEMENTED)

        from django.http import HttpResponse

        response = HttpResponse(png_bytes, content_type="image/png")
        response["Content-Disposition"] = (
            f'inline; filename="learnscroll_week_{recap.week_start.isoformat()}.png"'
        )
        return response
