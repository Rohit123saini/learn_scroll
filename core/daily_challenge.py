"""
core/daily_challenge.py

T2.3 - the Home "Daily challenge" card.

    GET /core/daily-challenge/

        {
          "date": "2026-10-12",
          "challenge": {                  # null when no free test exists yet
              "series": { ...PublicSeriesSerializer... },   # same shape as onboarding sample tests
              "title": "...", "subject": "...", "question_count": 20, "duration_minutes": 30
          },
          "completed_today": false,       # the user started a test (any series) today
          "streak": 3                     # current day streak, 0 if none
        }

One FREE published test per day. It is the same for everybody with the same target exam
(so a class / friends can do it together), and stable for the whole day: the pick only
depends on the date and the exam - NOT on what the user attempted - so the card doesn't
change under the user's finger the moment they start it.

Read-only: nothing is created or written here. "Completed" means the user STARTED an attempt
on any series today (the point of the card is getting a student to practise daily); the
test's own result screens handle grading.
"""
from django.db.models import Count
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


def _pool(exam: str):
    from testseries.models import TestSeries

    qs = (
        TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED, price_coins=0)
        .exclude(source=TestSeries.Source.CAMPUS)
        .annotate(q_count=Count("questions", distinct=True))
        .filter(q_count__gt=0)
        .select_related("creator")
        .order_by("id")  # stable order => stable daily pick
    )
    if exam:
        from .onboarding_quickstart import prefer_exam

        matched = prefer_exam(qs, exam)
        if matched.exists():
            return matched
    return qs


def pick_challenge_series(exam: str, day):
    """Deterministic series for `day` (a date) or None."""
    pool = list(_pool(exam or "")[:200])
    if not pool:
        return None
    return pool[day.toordinal() % len(pool)]


def build_daily_challenge(user, request=None) -> dict:
    """The whole `GET /core/daily-challenge/` payload for `user` (also used by `core/home_bootstrap.py`,
    so the Home bootstrap and the standalone endpoint can never drift apart)."""
    today = timezone.localdate()

    challenge = None
    try:
        from testseries.serializers import PublicSeriesSerializer

        # Parents don't take tests: no challenge -> the app hides the card.
        is_parent = getattr(user, "onboarding_role", "") == "parent"
        series = None if is_parent else pick_challenge_series(getattr(user, "target_exam", ""), today)
        if series is not None:
            data = PublicSeriesSerializer(series, context={"request": request}).data
            challenge = {
                "series": data,
                "title": series.title,
                "subject": series.subject or "",
                "question_count": getattr(series, "q_count", None),
                "duration_minutes": data.get("duration_minutes"),
            }
    except Exception:  # noqa: BLE001 - a card must never break Home
        import logging

        logging.getLogger(__name__).warning("daily challenge pick failed", exc_info=True)
        challenge = None

    completed = False
    try:
        from testseries.models import TestAttempt

        completed = TestAttempt.objects.filter(student=user, started_at__date=today).exists()
    except Exception:  # noqa: BLE001
        completed = False

    streak = 0
    try:
        from user_profile.models import Streak

        s = Streak.objects.filter(user=user).first()
        streak = int(s.current_streak) if s else 0
    except Exception:  # noqa: BLE001
        streak = 0

    return {
        "date": today.isoformat(),
        "challenge": challenge,
        "completed_today": completed,
        "streak": streak,
    }


class DailyChallengeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(build_daily_challenge(request.user, request))
