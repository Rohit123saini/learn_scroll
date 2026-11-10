"""
core/continue_learning.py  (H14 - the Home "Continue learning" strip)

    GET /core/continue-learning/

        {
          "resume":   {post_id, attachment_id, title, subject, page, page_count, progress, last_opened_at,
                       attachment: {...signed viewer url...}}  | null    # last PDF / note I was reading, unfinished
          "homework": {pending_count, due_today, overdue: 0, next: {id, title, subject, due_date, days_left}} | null
          "next_test": {series_id, title, subject, starts_at, ends_at, delivery_mode, state: "upcoming"|"open"} | null
          "streak":   {current, active_today} | null
          "errors":   ["homework", ...]        # sections that failed (their value is null)
        }

ONE small request instead of four, and every section has its own try/except (same contract as core/home_bootstrap.py):
a broken section is `null` + listed in `errors`, it never takes the strip down. Nothing here writes.

What is NOT in this payload on purpose (the app already holds it, so it is not fetched twice):
  * the next CLASS - Home already has it from the tuitionclass dashboard (`HomeBootstrap.nextClass`);
  * the daily challenge - `GET /core/daily-challenge/` / the bootstrap.

Sources
  resume     post.reading_progress.continue_item (ReadingProgress rows written by the in-app PDF viewer).
  lecture    post.lectures.continue_video_item (VideoProgress rows written by the lecture player).
  homework   campus.HomeworkDiaryEntry of the caller's ACTIVE enrolments (student only; a parent / teacher gets null).
             The diary has no "done" tick, so "pending" = due today or later. Homework already past its due date is
             not counted (a diary line is a note, not a task the student can still hand in).
  next_test  testseries: SCHEDULED / LIVE series the caller BOUGHT, not finished (window not over) and not yet started
             by the caller. Self-paced series are never "the next test". Campus-assigned tests (no purchase row) are not
             included - they show up in the campus Today screen.
  streak     user_profile.Streak (read only - the check-in itself stays in StreakView.post).
Cost: <= 8 small indexed queries, only the sections that apply to the caller's role run.
"""
import logging

from django.db.models import Q
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class ContinueLearningThrottle(UserRateThrottle):
    scope = "continue_learning"
    rate = "60/min"


def build_homework(user, today=None) -> dict | None:
    from campus.models import HomeworkDiaryEntry, StudentEnrollment

    today = today or timezone.localdate()
    section_ids = list(
        StudentEnrollment.objects.filter(student=user, status=StudentEnrollment.Status.ACTIVE)
        .values_list("section_id", flat=True)
    )
    if not section_ids:
        return None  # not a campus student -> no homework block at all
    qs = HomeworkDiaryEntry.objects.filter(is_active=True, section_id__in=section_ids, due_date__gte=today)
    first = qs.select_related("subject").order_by("due_date", "-created_at").first()
    return {
        "pending_count": qs.count(),
        "due_today": qs.filter(due_date=today).count(),
        "overdue": 0,
        "next": None if first is None else {
            "id": str(first.pk),
            "title": first.title,
            "subject": first.subject.name if first.subject_id else "",
            "due_date": first.due_date.isoformat(),
            "days_left": (first.due_date - today).days,
        },
    }


def build_next_test(user, now=None) -> dict | None:
    from testseries.models import TestSeries, TestSeriesPurchase

    now = now or timezone.now()
    bought = TestSeriesPurchase.objects.filter(buyer=user).exclude(status=TestSeriesPurchase.Status.REFUNDED).values("series_id")
    s = (
        TestSeries.objects.filter(
            pk__in=bought,
            status=TestSeries.Status.PUBLISHED,
            delivery_mode__in=(TestSeries.DeliveryMode.SCHEDULED, TestSeries.DeliveryMode.LIVE),
            starts_at__isnull=False,
        )
        .filter(Q(ends_at__gte=now) | Q(ends_at__isnull=True, starts_at__gte=now))
        .exclude(attempts__student=user)
        .order_by("starts_at")
        .first()
    )
    if s is None:
        return None
    return {
        "series_id": str(s.pk),
        "title": s.title,
        "subject": getattr(s, "subject", "") or "",
        "starts_at": s.starts_at.isoformat(),
        "ends_at": s.ends_at.isoformat() if s.ends_at else None,
        "delivery_mode": s.delivery_mode,
        "state": "upcoming" if s.starts_at > now else "open",
    }


def build_streak(user) -> dict | None:
    from user_profile.models import Streak

    s = Streak.objects.filter(user=user).first()
    if s is None:
        return {"current": 0, "active_today": False}
    return {
        "current": int(s.current_streak or 0),
        "active_today": s.last_active_date == timezone.localdate(),
    }


class ContinueLearningView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ContinueLearningThrottle]

    def get(self, request):
        user = request.user
        errors = []

        def run(name, fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 - one broken section must not break the strip
                logger.warning("continue-learning section %r failed", name, exc_info=True)
                errors.append(name)
                return None

        def resume():
            from post.reading_progress import continue_item

            return continue_item(user, request)

        def lecture():
            from post.lectures import continue_video_item

            return continue_video_item(user, request)

        is_parent = getattr(user, "onboarding_role", "") == "parent"
        return Response({
            "resume": run("resume", resume),
            "lecture": run("lecture", lecture),   # round 4: the video lecture left unfinished (post/lectures.py)
            "homework": None if is_parent else run("homework", lambda: build_homework(user)),
            "next_test": None if is_parent else run("next_test", lambda: build_next_test(user)),
            "streak": run("streak", lambda: build_streak(user)),
            "habit": run("habit", lambda: _habit(user)),  # P-F3_F4: Home habit card (core/habit_loop.py)
            "errors": errors,
        })


def _habit(user):
    from . import habit_loop

    return habit_loop.today_snapshot(user)


class HabitTodayView(APIView):
    """GET /core/habit/today/ - P-F3_F4: today's streak / goal / revision / weekly-report snapshot (read only)."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ContinueLearningThrottle]

    def get(self, request):
        return Response(_habit(request.user))
