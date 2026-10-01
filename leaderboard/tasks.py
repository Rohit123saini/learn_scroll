"""
leaderboard/tasks.py

Celery tasks that own every WRITE to `LeaderboardEntry` (Task G7). Wired
into `LearnScroll/settings.py` CELERY_BEAT_SCHEDULE:

    "leaderboard-recompute-weekly": {
        "task": "leaderboard.recompute_weekly_boards",
        "schedule": crontab(minute="*/30"),
    },
    "leaderboard-recompute-all-time": {
        "task": "leaderboard.recompute_all_time_boards",
        "schedule": crontab(hour=3, minute=30),
    },

Weekly boards run every 30 min (cheap: only this week's rows are scanned)
so a rank feels close to live; all-time boards run once a day off-peak
since they scan full history and change more slowly in relative terms.
Both are also exposed as a plain management command
(`python manage.py recompute_leaderboards`) for local dev / a manual kick.

Same `dispatch_after_commit`-free, `@shared_task` style as `core/tasks.py` —
these are periodic beat jobs, not request-triggered side effects, so there's
no after-commit race to guard against here.
"""
import logging

from celery import shared_task
from django.db import transaction

from . import services
from .models import LeaderboardEntry

logger = logging.getLogger(__name__)


def _write_board(scope_type, scope_id, period_type, period_key, score_rows):
    """Shared upsert for one board: rank `score_rows` (already
    `[{"user_id","score","metadata"}, ...]`), replace the board's existing
    rows with the freshly-ranked set, and invalidate the read cache. Ties
    broken by user_id, purely for deterministic ordering — this is a
    leaderboard, not a payout, so a stable tie-break is enough."""
    ranked = sorted(score_rows, key=lambda r: (-r["score"], str(r["user_id"])))

    with transaction.atomic():
        LeaderboardEntry.objects.filter(
            scope_type=scope_type, scope_id=scope_id, period_type=period_type, period_key=period_key,
        ).delete()
        LeaderboardEntry.objects.bulk_create([
            LeaderboardEntry(
                scope_type=scope_type,
                scope_id=scope_id,
                period_type=period_type,
                period_key=period_key,
                user_id=row["user_id"],
                score=row["score"],
                rank=index + 1,
                metadata=row.get("metadata") or {},
            )
            for index, row in enumerate(ranked)
        ])

    services.invalidate_board(scope_type, scope_id, period_type, period_key)
    return len(ranked)


def recompute_test_series_board(series_id, period_type: str, period_key: str) -> int:
    scores = services.compute_test_series_scores(series_id, period_type, period_key)
    return _write_board(LeaderboardEntry.ScopeType.TEST_SERIES, series_id, period_type, period_key, scores)


def recompute_campus_section_board(section_id, period_type: str, period_key: str) -> int:
    scores = services.compute_campus_section_scores(section_id, period_type, period_key)
    return _write_board(LeaderboardEntry.ScopeType.CAMPUS_SECTION, section_id, period_type, period_key, scores)


def recompute_engagement_board(period_type: str, period_key: str) -> int:
    scores = services.compute_engagement_scores(period_type, period_key)
    return _write_board(LeaderboardEntry.ScopeType.ENGAGEMENT, None, period_type, period_key, scores)


def _active_test_series_ids():
    from testseries.models import TestAttempt, TestSeries

    published = set(TestSeries.objects.filter(status=TestSeries.Status.PUBLISHED).values_list("id", flat=True))
    # A series only ever needs a board once it has at least one checked
    # attempt — recomputing an untouched series' empty board every run is
    # pure waste, and views.py already returns an empty list for a series
    # that simply has no LeaderboardEntry rows yet.
    attempted = set(
        TestAttempt.objects.filter(status=TestAttempt.Status.CHECKED, series_id__in=published)
        .values_list("series_id", flat=True).distinct()
    )
    return attempted


def _active_section_ids():
    from campus.models import StudentEnrollment

    return set(
        StudentEnrollment.objects.filter(status=StudentEnrollment.Status.ACTIVE)
        .values_list("section_id", flat=True).distinct()
    )


def _recompute_period(period_type: str) -> dict:
    period_key = services.current_week_key() if period_type == LeaderboardEntry.PeriodType.WEEKLY else "all"

    series_done = 0
    for series_id in _active_test_series_ids():
        try:
            recompute_test_series_board(series_id, period_type, period_key)
            series_done += 1
        except Exception:
            logger.exception("leaderboard: failed recomputing test_series board %s (%s)", series_id, period_type)

    sections_done = 0
    for section_id in _active_section_ids():
        try:
            recompute_campus_section_board(section_id, period_type, period_key)
            sections_done += 1
        except Exception:
            logger.exception("leaderboard: failed recomputing campus_section board %s (%s)", section_id, period_type)

    try:
        recompute_engagement_board(period_type, period_key)
        engagement_done = True
    except Exception:
        logger.exception("leaderboard: failed recomputing engagement board (%s)", period_type)
        engagement_done = False

    return {
        "period_type": period_type,
        "period_key": period_key,
        "test_series_boards": series_done,
        "campus_section_boards": sections_done,
        "engagement_board": engagement_done,
    }


@shared_task(name="leaderboard.recompute_weekly_boards")
def recompute_weekly_boards():
    """Every board, current ISO week only. Safe to run frequently — only
    this week's activity is scanned, not full history."""
    summary = _recompute_period(LeaderboardEntry.PeriodType.WEEKLY)
    logger.info("leaderboard.recompute_weekly_boards: %s", summary)
    return summary


@shared_task(name="leaderboard.recompute_all_time_boards")
def recompute_all_time_boards():
    """Every board, full history. Heavier — scheduled once a day, off-peak."""
    summary = _recompute_period(LeaderboardEntry.PeriodType.ALL_TIME)
    logger.info("leaderboard.recompute_all_time_boards: %s", summary)
    return summary


@shared_task(name="leaderboard.recompute_everything")
def recompute_everything():
    """Both periods, back to back — used by the management command and
    available for a manual `.delay()` kick (e.g. right after a data
    migration/seed) instead of waiting for the next beat tick."""
    return {
        "weekly": recompute_weekly_boards(),
        "all_time": recompute_all_time_boards(),
    }
