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
    """TASK G1 — kept under its old name so existing beat entries / manual
    calls / tests keep working, but T2.9 moved the real work to
    `core.tasks.send_streak_at_risk_reminders`: one bell row per user per day
    and a push that honours each user's mute list, push toggle and quiet
    hours / DND (the old version multicast straight through
    `send_push_to_users` and ignored all of that). Returns that task's summary
    plus a `notified` key (rows created) for backward compatibility."""
    from core.tasks import send_streak_at_risk_reminders

    summary = send_streak_at_risk_reminders()
    summary = dict(summary or {})
    summary["notified"] = summary.get("created", 0)
    return summary


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
        from core.tasks import push_for_notification_row

        # N6: one push PER bell row (not a send_push_to_users multicast): the multicast ignored the per-type matrix, quiet
        # hours / DND and the daily reminder cap. WEEKLY_RECAP_READY is a non-urgent reminder type, so
        # send_push_for_notification applies the cap + same-type collapse and the Snooze / Turn off buttons ride along.
        pushed = 0
        for user_id, recap in notified_user_ids:
            try:
                row = create_notification(
                    user_id,
                    Notification.NotifType.WEEKLY_RECAP_READY,
                    "Your Week is ready \U0001F4CA",
                    f"{recap.tests_attempted} tests, {recap.classes_attended} classes, "
                    f"{recap.posts_liked_received} likes this week \u2014 see your recap.",
                    data={
                        "type": "weekly_recap_ready",
                        "week_start": recap.week_start.isoformat(),
                        "recap_id": recap.id,
                    },
                )
                # Bell rows must never be lost because the push channel is down / unconfigured: push_for_notification_row
                # never raises, and a failed row creation only skips that one user.
                if push_for_notification_row(row):
                    pushed += 1
                # P-F3_F4: the same week as ONE e-mail report (queued; channel_policy decides if this user gets it)
                try:
                    from core.tasks import send_weekly_report_email

                    send_weekly_report_email.delay(user_id, recap.id)
                except Exception:
                    logger.exception("generate_weekly_recaps: could not queue the report e-mail for user_id=%s", user_id)
            except Exception:
                logger.exception("generate_weekly_recaps: notify failed for user_id=%s", user_id)
        logger.info("generate_weekly_recaps: push attempted for %s of %s users", pushed, len(notified_user_ids))

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


@shared_task
def automod_ai_screen(target_type, target_id, user_id, text):
    """Optional AI second pass for auto-moderation (see common/moderation_ai.py).
    Queued from user_profile/automod.py only when AUTOMOD_AI_ENABLED is on and
    the cheap word-list pass found nothing. Flag-only, never raises."""
    from common.moderation_ai import classify

    from .automod import record_flag

    flagged, reason = classify(text)
    if flagged:
        record_flag(
            target_type=target_type, target_id=target_id, user_id=user_id,
            text=text, reason=reason, source="ai",
        )
    return flagged


@shared_task(name="user_profile.warn_overdue_child_safety_cases")
def warn_overdue_child_safety_cases():
    """Hourly: child-safety cases past their SLA -> warn staff once (user_profile/child_safety.py)."""
    from .child_safety import warn_overdue_cases

    return warn_overdue_cases()


@shared_task(name="user_profile.sweep_report_sla")
def sweep_report_sla():
    """P-F15, hourly: open reports past their review deadline -> stamp once + ONE summary alert to staff (report_sla.py)."""
    from .report_sla import sweep

    return sweep()


@shared_task(name="user_profile.tasks.prune_old_profile_visits")
def prune_old_profile_visits():
    """P9 - delete ProfileVisit rows older than PROFILE_VISIT_RETENTION_DAYS (default 90). Daily."""
    from . import profile_insights

    n = profile_insights.prune_old_visits()
    logger.info("prune_old_profile_visits: deleted %s rows", n)
    return n


@shared_task(name="user_profile.reconcile_payments")
def reconcile_payments():
    """P-G2, every 15 min: PENDING online payments (coin top-ups + campus fees) whose webhook never arrived are checked
    against Razorpay and settled through the same idempotent code the webhook uses. Mismatches are logged at ERROR."""
    from django.conf import settings

    if not getattr(settings, "PAYMENT_RECONCILE_ENABLED", True):
        return {"skipped": "PAYMENT_RECONCILE_ENABLED is off"}
    from common.payment_reconcile import run_all

    return run_all()


@shared_task(bind=True, name="user_profile.send_coin_receipt_email", max_retries=3, acks_late=True)
def send_coin_receipt_email(self, purchase_id):
    """P-G2: e-mail the coin top-up receipt (PDF attached) once. Best effort: a mail problem never touches the payment."""
    from .receipt_email import send_coin_receipt

    if not send_coin_receipt(purchase_id):
        raise self.retry(exc=RuntimeError(f"coin receipt email {purchase_id} not sent"), countdown=60 * (self.request.retries + 1))
    return True
