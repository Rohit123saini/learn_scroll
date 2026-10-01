"""
user_profile/recap.py

TASK G2 (growth_and_feature_tasks.md — Daily/weekly "recap" screen).

Aggregation for `WeeklyRecap` (models.py). Deliberately its own module
rather than living inline in tasks.py/views.py — it reaches into THREE
other apps' models (testseries.TestAttempt, tuitionclass.SessionParticipant,
post.PostLike), and every one of those imports is lazy (function-local),
same circular-import reasoning `user_profile/services.py`'s `_notify()`
docstring already gives for its own lazy `core.services` import: those
apps don't import `user_profile` back today, but there's no guarantee one
of them never will, and a top-level import here would be the one direction
that actually risks a cycle (this app's `models.py`/`signals.py` are
already imported very early — during `AUTH_USER_MODEL` app-loading — by
half the rest of the codebase).

Nothing here writes anything except `generate_weekly_recap_for_user`'s own
`WeeklyRecap.objects.update_or_create(...)` call — no coins, no other
app's rows are touched. Read-only aggregation in, one row out.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def week_bounds(reference_date=None):
    """Return (week_start, week_end) — the Monday-Sunday week containing
    `reference_date` (default: today). Both dates are inclusive.

    Monday-start (not the "week ends today" trailing-7-days some apps use)
    because the generation task only ever runs once, on Sunday night, for
    the week that's *just finished* — a fixed Mon-Sun boundary is what
    lets `WeeklyRecapView`/the recap screen say "week of <date>" instead of
    a relative, drifting "last 7 days" label.
    """
    reference_date = reference_date or timezone.localdate()
    week_start = reference_date - timedelta(days=reference_date.weekday())
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def generate_weekly_recap_for_user(user, week_start=None):
    """Aggregate one user's stats for the given week (default: the week
    that just ended, if called on/after that Sunday) and upsert the
    `WeeklyRecap` row for it.

    Returns `(recap, created)` — same shape `get_or_create`/
    `update_or_create` already return, so callers (the celery task, or a
    future "generate mine now" debug endpoint) don't need a different
    contract to check "was this the first time".

    Idempotent per (user, week_start): re-running this for a week that
    already has a row just recomputes and overwrites it (`update_or_create`),
    same "safe to retry" reasoning `send_streak_risk_reminders` already
    documents for its own task — nothing here credits coins or sends a
    notification itself (the caller, `tasks.generate_weekly_recaps`,
    decides whether a freshly-generated row is *new* enough to notify
    about).
    """
    from .models import Streak, WeeklyRecap

    if week_start is None:
        # Called with no week_start = "the week that just ended" — i.e.
        # last week relative to today, since this only ever runs on a
        # Sunday night crontab for the week that just finished.
        today = timezone.localdate()
        this_week_start, _ = week_bounds(today)
        week_start = this_week_start - timedelta(days=7)

    week_end = week_start + timedelta(days=6)
    # Aggregation queries are timezone-aware datetime ranges, inclusive of
    # the whole of week_end (through 23:59:59.999999), not just its date —
    # every timestamp column touched below (submitted_at/joined_at/
    # created_at) is a DateTimeField.
    range_start = timezone.make_aware(
        timezone.datetime.combine(week_start, timezone.datetime.min.time())
    )
    range_end = timezone.make_aware(
        timezone.datetime.combine(week_end, timezone.datetime.max.time())
    )

    tests_attempted = _count_tests_attempted(user, range_start, range_end)
    classes_attended = _count_classes_attended(user, range_start, range_end)
    posts_liked_received = _count_posts_liked_received(user, range_start, range_end)

    try:
        streak_days = user.streak.current_streak
    except Streak.DoesNotExist:
        streak_days = 0

    recap, created = WeeklyRecap.objects.update_or_create(
        user=user,
        week_start=week_start,
        defaults={
            "week_end": week_end,
            "tests_attempted": tests_attempted,
            "classes_attended": classes_attended,
            "posts_liked_received": posts_liked_received,
            "streak_days": streak_days,
        },
    )
    return recap, created


def _count_tests_attempted(user, range_start, range_end):
    """Distinct TestAttempt rows this user SUBMITTED (any status from
    SUBMITTED onward — submitting is the "attempted" action; whether it's
    since been checked doesn't matter for this count) within the week.
    Filtered on `submitted_at`, not `started_at` — an attempt started
    Saturday night and submitted Monday belongs to the week it was
    actually finished in.
    """
    from testseries.models import TestAttempt

    return TestAttempt.objects.filter(
        student=user,
        submitted_at__isnull=False,
        submitted_at__range=(range_start, range_end),
    ).count()


def _count_classes_attended(user, range_start, range_end):
    """Distinct tuition-class sessions this user actually joined as a real
    student (role=STUDENT, is_trial=False — same exclusion
    SessionParticipant's own `is_trial` docstring says the engagement
    report/attendance-% already applies) within the week. `.distinct()`
    on `session_id` because a flaky connection can create more than one
    SessionParticipant row for the same (session, user) across a
    reconnect — this counts CLASSES attended, not join-events.
    """
    from tuitionclass.models import SessionParticipant

    return (
        SessionParticipant.objects.filter(
            user=user,
            role=SessionParticipant.Role.STUDENT,
            is_trial=False,
            joined_at__range=(range_start, range_end),
        )
        .values("session_id")
        .distinct()
        .count()
    )


def _count_posts_liked_received(user, range_start, range_end):
    """Likes/reactions received on THIS user's own posts within the week
    (not likes this user gave out — the recap card's "posts liked" line is
    a social-proof number about you, same as the streak chip).
    """
    from post.models import PostLike

    return PostLike.objects.filter(
        post__user=user,
        created_at__range=(range_start, range_end),
    ).count()
