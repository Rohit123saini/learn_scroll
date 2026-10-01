"""
user_profile/tasks.py

UPDATE (issue #4): the root-cause fix now exists —
`user_profile/signals.py` recomputes followers_count/following_count
from the real Follow rows on every Follow post_save/post_delete (incl.
admin deletes and user.delete() cascades), and the API views no longer
update the counters by hand. Everything below that says "the actual fix
would be a signal" is therefore historical.

This task is kept ONLY as a low-frequency safety net for the one thing
signals cannot see: `QuerySet.update()` / `bulk_create()` on Follow
(Django sends no signals for those) and raw SQL. It now runs weekly
(settings.CELERY_BEAT_SCHEDULE) instead of every 6 hours; a non-zero
"corrected" count in its log line means some code path is bypassing the
signals and should be found and fixed.

TASK 28 — followers_count/following_count drift reconciliation.

`followers_count`/`following_count` are kept in sync per-operation today
— whatever code path handles follow/accept/unfollow does its own
`F(...) + 1` / `- 1` right alongside the `Follow` row write, and that's
correct for every write that actually goes through those code paths.

The gap is writes that DON'T go through them:
  - an admin deleting a `Follow` row directly in /admin/
  - `user.delete()` CASCADE-deleting every `Follow` row the deleted user
    was party to (as follower AND as following) — nothing re-runs the
    counter update on the *other* side of each of those rows
  - a data migration, a bulk `.delete()`/`.update()` run from a shell,
    a one-off fixup script
None of those fire the same increment/decrement logic the API views use,
so the stored counters can silently drift from what `Follow` rows
actually say.

This task is a detect-and-correct safety net, not the root-cause fix —
the actual fix would be a `Follow` `post_save`/`post_delete` signal that
recomputes from real rows on every change (the same pattern
`post/models.py` already uses for `update_shares_count`/
`update_saves_count`/`update_story_views_count` — see that file), which
makes drift structurally impossible instead of periodically corrected.
That's a bigger change to `user_profile/models.py` than this task asked
for; this reconciliation job is the "note it, don't block on it" version,
matching the same trade-off this codebase already made for
`post.views.TrendingHashtagsAPIView` (bounded recompute now, revisit if
scale demands it later).

⚠️ ASSUMPTION — `user_profile/models.py` wasn't provided when this was
written. Field names below are taken from how `Follow` is already used
elsewhere in this codebase (`post/views.py`'s `HomeFeedView`/
`ExploreFeedAPIView`: `Follow.objects.filter(follower=..., status=
Follow.Status.ACCEPTED).values_list('following_id', ...)`), so
`follower`/`following`/`status`/`Follow.Status.ACCEPTED` are already
confirmed real. If your `User` model's counter fields aren't literally
named `followers_count`/`following_count`, adjust the two `hasattr`
checks and the `recompute_follow_counts` call below — everything else
stays the same.

Wire into settings.py CELERY_BEAT_SCHEDULE (already added):

    CELERY_BEAT_SCHEDULE = {
        ...
        "user-profile-reconcile-follow-counts": {
            "task": "user_profile.tasks.reconcile_follow_counts",
            "schedule": crontab(hour="*/6", minute=15),
        },
    }
"""
import logging

from celery import shared_task
from django.db.models import Count

logger = logging.getLogger(__name__)

# How many users are checked (and, if drifted, fixed) per round trip. Keeps the
# task's memory and per-query cost bounded on a large user table.
_CHUNK_SIZE = 1000


@shared_task
def reconcile_follow_counts():
    """Recompute every user's followers_count/following_count directly
    from `Follow` rows (status=ACCEPTED only — matches how "following" is
    defined everywhere else in this codebase) and correct any drift
    found. Only writes rows that are actually wrong; a run where nothing
    drifted issues zero UPDATEs beyond the two read queries below.

    Returns a summary dict so a manual `.delay()` call, a Flower
    dashboard, or an admin action can see whether drift is actually
    happening in practice. If this keeps finding real correction work on
    every scheduled run, that's a signal the root-cause fix (a `Follow`
    post_delete signal — see module docstring) is overdue, not that this
    task itself is misbehaving.
    """
    from django.contrib.auth import get_user_model

    from .models import Follow

    User = get_user_model()

    if not (hasattr(User, "followers_count") and hasattr(User, "following_count")):
        logger.warning(
            "reconcile_follow_counts: User model has no followers_count/"
            "following_count fields — nothing to reconcile."
        )
        return {"skipped": True}

    # Issue #20 / scalability: walk the user table in id-ordered CHUNKS
    # (keyset pagination) and, per chunk, ask the database for the true counts
    # of just those users with two grouped queries. Memory is bounded by the
    # chunk, not by the number of users (the old version held one dict entry
    # per user-with-follows for the whole table).
    #
    # Users found drifted are fixed with `recompute_follow_counts()` — the
    # SAME single-statement `UPDATE ... SET count = (SELECT COUNT(*) ...)` the
    # signals use — instead of `bulk_update()` of values read earlier. Two
    # advantages: a follow that lands between "read" and "write" is not
    # overwritten with a stale absolute number (the count is recomputed by
    # the database at write time), and it goes through the one code path that
    # also emits `follow_counts_recomputed` (signals.py), the hook a cache
    # layer subscribes to — `bulk_update()`/`update()` fire no model
    # post_save, so without that hook a cached copy would stay stale.
    from .signals import recompute_follow_counts

    accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED).order_by()
    checked = 0
    corrected_followers = 0
    corrected_following = 0
    last_id = 0

    while True:
        chunk = list(
            User.objects.filter(id__gt=last_id)
            .order_by("id")
            .values_list("id", "followers_count", "following_count")[:_CHUNK_SIZE]
        )
        if not chunk:
            break
        last_id = chunk[-1][0]
        ids = [row[0] for row in chunk]
        checked += len(chunk)

        # Every user in the chunk is checked, including ones that appear in
        # neither map below — a user whose real count just dropped to zero can
        # still carry a stale non-zero counter, and only walking all users
        # (not just map keys) catches that direction of drift.
        true_followers = dict(
            accepted.filter(following_id__in=ids)
            .values("following_id").annotate(c=Count("id")).values_list("following_id", "c")
        )
        true_following = dict(
            accepted.filter(follower_id__in=ids)
            .values("follower_id").annotate(c=Count("id")).values_list("follower_id", "c")
        )

        drifted = []
        for uid, followers_count, following_count in chunk:
            fix_followers = followers_count != true_followers.get(uid, 0)
            fix_following = following_count != true_following.get(uid, 0)
            if fix_followers or fix_following:
                drifted.append(uid)
                corrected_followers += fix_followers
                corrected_following += fix_following
        if drifted:
            recompute_follow_counts(drifted)

    total_corrected = corrected_followers + corrected_following
    if total_corrected:
        logger.warning(
            "reconcile_follow_counts: checked %s users, corrected "
            "followers_count on %s and following_count on %s — drift "
            "detected (expected occasionally from admin deletes/cascades; "
            "if this stays high on every run, the real fix is a Follow "
            "post_delete signal, not just this reconciliation).",
            checked, corrected_followers, corrected_following,
        )
    else:
        logger.info("reconcile_follow_counts: checked %s users, no drift found.", checked)

    return {
        "checked": checked,
        "corrected_followers_count": corrected_followers,
        "corrected_following_count": corrected_following,
    }

@shared_task
def send_streak_risk_reminders():
    """
    TASK G1 (growth_and_feature_tasks.md — Streaks) — "don't lose your
    streak" push, once a day, to every user who:
      - has an active streak (`current_streak > 0`), AND
      - has NOT already checked in today (`last_active_date` is
        yesterday, not today — `is_active_today` would be True and
        there'd be nothing at risk).

    Fired at `settings.STREAK_REMINDER_HOUR` (default 22:00 / 10pm) via
    CELERY_BEAT_SCHEDULE's "user-profile-send-streak-risk-reminders"
    entry — "~2 hours before day-end", matching the same midnight
    `timezone.localdate()` rollover `Streak.objects.record_activity()`
    itself uses to decide what "today" means.

    ⚠️ SIMPLIFICATION (same class of trade-off `campus.tasks`'s own
    docstrings flag elsewhere in this codebase — note it, don't block on
    it): this fires on ONE fixed server-time hour for every user,
    regardless of their own timezone. A genuinely per-user "2 hours
    before THEIR midnight" reminder needs a per-user timezone field
    (this codebase's `login.User` doesn't have one today) plus N
    scheduled runs instead of one. Fine for a single-timezone-market
    launch (this app's other daily jobs — campus's 08:00/18:00/19:00
    crontabs — make the same server-time-for-everyone assumption); flag
    for follow-up if/when the user base spans multiple timezones.

    Batches the actual push into ONE `send_push_to_users()` multicast
    call (message/push_utils.py — already used app-wide for chat/mention
    pushes) rather than one call per user, same reasoning
    `create_bulk_notifications` gives for batching the bell-row INSERT.
    The in-app bell row, by contrast, IS written one-by-one via
    `core.services.create_notification` — each user's row needs its own
    `data={"streak_days": ...}` (a different number per user), so a
    single bulk row (one shared `data` dict) wouldn't carry the right
    number per recipient.

    Safe to run more than once a day for the same reason
    `check_attendance_streak_rewards` is: no state of its own beyond
    reading `Streak` rows, and no coins move here — this task only ever
    notifies, never credits (that's `record_activity()`'s job, at
    check-in time). A re-run within the same day would just re-notify
    the same still-at-risk users, which the "safe to retry" framing
    already accepts as harmless rather than exactly-once-critical.
    """
    from datetime import timedelta

    from django.utils import timezone as dj_timezone

    from core.models import Notification
    from core.services import create_notification

    from .models import Streak

    yesterday = dj_timezone.localdate() - timedelta(days=1)

    at_risk = list(
        Streak.objects.filter(current_streak__gt=0, last_active_date=yesterday)
        .select_related("user")
    )

    if not at_risk:
        logger.info("send_streak_risk_reminders: no streaks at risk today.")
        return {"notified": 0}

    for streak in at_risk:
        create_notification(
            streak.user,
            Notification.NotifType.STREAK_AT_RISK,
            "Don't lose your streak! 🔥",
            f"You're on a {streak.current_streak}-day streak — open LearnScroll "
            "before midnight to keep it alive.",
            data={"streak_days": streak.current_streak},
        )

    try:
        from message.push_utils import send_push_to_users

        send_push_to_users(
            [s.user_id for s in at_risk],
            "Don't lose your streak! \U0001F525",
            "Open LearnScroll before midnight to keep your streak alive.",
            data={"type": "streak_at_risk"},
        )
    except Exception:
        # Same "in-app row must never be lost because the push channel
        # is down/unconfigured" contract core.services.create_notification's
        # own docstring documents — the bell rows above have already been
        # written by this point regardless of what happens here.
        logger.exception("send_streak_risk_reminders: push batch failed (bell rows still written).")

    logger.info("send_streak_risk_reminders: notified %s user(s).", len(at_risk))
    return {"notified": len(at_risk)}


@shared_task
def generate_weekly_recaps():
    """TASK G2 (growth_and_feature_tasks.md) — celery-beat entry point,
    scheduled for Sunday night (settings.CELERY_BEAT_SCHEDULE,
    RECAP_GENERATION_HOUR) so it always runs for the week that JUST
    finished (recap.week_bounds()'s Mon-Sun convention).

    Only considers users who have SOME footprint in the app already — a
    `Streak` row (created lazily by StreakView.get()/the check-in flow,
    so effectively "has opened the app at least once") — rather than
    scanning the entire `User` table. A user with a Streak row but zero
    activity in the recapped week still gets skipped below via
    `WeeklyRecap.has_any_activity`: no row, no notification, no blank
    "Your Week: 0/0/0/0" card ever gets generated for someone who didn't
    open the app that week.

    Batches the notification the same way `send_streak_risk_reminders`
    already does: one `create_notification()` call per user for the bell
    row (each needs its own per-user `data={"week_start": ...}`), one
    batched `send_push_to_users()` multicast for the push.
    """
    from datetime import timedelta

    from django.contrib.auth import get_user_model
    from django.utils import timezone as dj_timezone

    from .models import Streak, WeeklyRecap
    from .recap import generate_weekly_recap_for_user, week_bounds

    User = get_user_model()

    today = dj_timezone.localdate()
    this_week_start, _ = week_bounds(today)
    week_start = this_week_start - timedelta(days=7)

    user_ids = Streak.objects.values_list("user_id", flat=True)
    notified_user_ids = []
    generated = 0
    skipped_no_activity = 0

    for user in User.objects.filter(id__in=list(user_ids)).iterator(chunk_size=_CHUNK_SIZE):
        try:
            recap, _created = generate_weekly_recap_for_user(user, week_start=week_start)
        except Exception:
            logger.exception("generate_weekly_recaps: failed for user_id=%s", user.id)
            continue

        generated += 1
        if recap.has_any_activity:
            notified_user_ids.append((user.id, recap))
        else:
            skipped_no_activity += 1

    if notified_user_ids:
        from core.models import Notification
        from core.services import create_notification

        for user_id, recap in notified_user_ids:
            create_notification(
                user_id,
                Notification.NotifType.WEEKLY_RECAP_READY,
                "Your Week is ready \U0001F4CA",
                f"{recap.tests_attempted} tests, {recap.classes_attended} classes, "
                f"{recap.posts_liked_received} likes this week — see your recap.",
                data={"week_start": recap.week_start.isoformat(), "recap_id": recap.id},
            )

        try:
            from message.push_utils import send_push_to_users

            send_push_to_users(
                [uid for uid, _ in notified_user_ids],
                "Your Week is ready \U0001F4CA",
                "See what you got done this week on LearnScroll.",
                data={"type": "weekly_recap_ready", "week_start": week_start.isoformat()},
            )
        except Exception:
            # Same "bell rows must never be lost because the push channel
            # is down/unconfigured" contract send_streak_risk_reminders
            # already documents above.
            logger.exception("generate_weekly_recaps: push batch failed (bell rows still written).")

    logger.info(
        "generate_weekly_recaps: week_start=%s generated=%s notified=%s skipped_no_activity=%s",
        week_start, generated, len(notified_user_ids), skipped_no_activity,
    )
    return {
        "generated": generated,
        "notified": len(notified_user_ids),
        "skipped_no_activity": skipped_no_activity,
    }


# ---------------------------------------------------------------------------
# P13-BE — Achievements / badges (periodic rules)
# ---------------------------------------------------------------------------
@shared_task
def award_weekly_leaderboard_badges(week_key=None):
    """'Weekly Top 10' badge for everyone who finished in the top
    `settings.BADGE_WEEKLY_TOP_N` (default 10) of the app-wide ENGAGEMENT
    weekly board of the week that JUST ended (`week_key` overrides, for a
    manual backfill of an older week).

    Scheduled Monday morning (CELERY_BEAT_SCHEDULE). The weekly board is
    only recomputed every 30 min, so its last run for the finished week can
    be up to 30 min stale — this task therefore recomputes that one board
    once more before reading it, so a late-Sunday post still counts. If the
    recompute fails, the rows already there are used.

    One badge per user ever (unique_user_badge); `context` records the
    first week/rank it was earned in. Returns a summary dict.
    """
    from datetime import timedelta

    from django.conf import settings
    from django.utils import timezone as dj_timezone

    from leaderboard import services as lb_services
    from leaderboard import tasks as lb_tasks
    from leaderboard.models import LeaderboardEntry

    from .services import award_badge

    top_n = int(getattr(settings, "BADGE_WEEKLY_TOP_N", 10))
    weekly = LeaderboardEntry.PeriodType.WEEKLY
    if week_key is None:
        # Same key function the leaderboard itself uses, so the two can
        # never disagree on what "last week" is.
        week_key = lb_services.current_week_key(dj_timezone.now() - timedelta(days=7))

    try:
        lb_tasks.recompute_engagement_board(weekly, week_key)
    except Exception:
        logger.exception("award_weekly_leaderboard_badges: final recompute of %s failed; using existing rows", week_key)

    rows = list(
        LeaderboardEntry.objects.filter(
            scope_type=LeaderboardEntry.ScopeType.ENGAGEMENT,
            scope_id__isnull=True,
            period_type=weekly,
            period_key=week_key,
            rank__lte=top_n,
        ).values_list("user_id", "rank")
    )
    awarded = 0
    for user_id, rank in rows:
        if award_badge(user_id, "weekly_top10", context={"week": week_key, "rank": rank}):
            awarded += 1

    logger.info("award_weekly_leaderboard_badges: week=%s candidates=%s awarded=%s", week_key, len(rows), awarded)
    return {"week": week_key, "candidates": len(rows), "awarded": awarded}


@shared_task
def backfill_badges():
    """Manual one-off (`backfill_badges.delay()`): silently award badges to
    users who already qualified before the badge system existed."""
    from .services import backfill_badges as _backfill

    summary = _backfill()
    logger.info("backfill_badges: %s", summary)
    return summary
