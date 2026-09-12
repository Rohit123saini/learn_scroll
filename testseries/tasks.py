# testseries/tasks.py
"""
Celery safety-net tasks — same shape as `liveclass.expire_and_refund_passes`
/ `liveclass.reconcile_stuck_coin_purchases` (design doc §7).
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import TestAttempt, TestSeries, TestSeriesPurchase, _notify

logger = logging.getLogger(__name__)

# settings.TESTSERIES_AUTO_REFUND_DAYS — how long an escrowed purchase
# can sit unchecked before the buyer is auto-refunded (§7's example: 14).
AUTO_REFUND_DAYS = getattr(settings, "TESTSERIES_AUTO_REFUND_DAYS", 14)
# A smaller, earlier threshold — the proactive reminder that fires
# before the refund does (§7's example: 3).
REMINDER_DAYS = getattr(settings, "TESTSERIES_REMINDER_DAYS", 3)


@shared_task
def send_pending_check_reminders():
    """Daily: nudge creators who have attempts still waiting on their
    review — proactive, fires well before the auto-refund task below."""
    cutoff = timezone.now() - timedelta(days=REMINDER_DAYS)
    pending = TestAttempt.objects.filter(
        status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.PARTIALLY_CHECKED],
        submitted_at__lte=cutoff,
    ).select_related("series", "series__creator")

    by_creator = {}
    for attempt in pending:
        by_creator.setdefault(attempt.series.creator, []).append(attempt)

    for creator, attempts in by_creator.items():
        # No dedicated "review reminder" NotifType is defined in the
        # design doc (§4 only lists TESTSERIES_POSTED/_CHECKED/
        # _PAYOUT_RELEASED) — falling back to core's existing GENERIC
        # type rather than inventing an unlisted enum value.
        _notify(
            recipient=creator,
            notif_type="generic",
            title="Attempts pending review",
            message=f"{len(attempts)} attempt(s) are waiting for you to check.",
            data={"series_ids": [str(a.series_id) for a in attempts]},
        )

    logger.info("send_pending_check_reminders: notified %d creator(s)", len(by_creator))
    return len(by_creator)


@shared_task
def refund_unchecked_paid_attempts():
    """Daily: a paid `TestSeriesPurchase` sitting `escrowed` for more
    than `AUTO_REFUND_DAYS`, whose linked attempt is `submitted` or
    `partially_checked` but never reached `checked` — the creator never
    finished reviewing — gets auto-refunded to the buyer. This is what
    stops the "creator locks payment and just never checks" exploit that
    the escrow-on-full-checking design would otherwise allow (§7)."""
    cutoff = timezone.now() - timedelta(days=AUTO_REFUND_DAYS)
    stuck = TestSeriesPurchase.objects.filter(
        status=TestSeriesPurchase.Status.ESCROWED,
        created_at__lte=cutoff,
        attempt__status__in=[TestAttempt.Status.SUBMITTED, TestAttempt.Status.PARTIALLY_CHECKED],
    ).select_related("attempt", "series")

    count = 0
    for purchase in stuck:
        purchase.refund(reason_note="auto: creator didn't check in time")
        count += 1

    logger.info("refund_unchecked_paid_attempts: refunded %d purchase(s)", count)
    return count


# ---------------------------------------------------------------------------
# TASK 5 — "new test series from someone you follow" fan-out.
#
# Enqueued via `.delay()` from `TestSeriesViewSet.publish()` in views.py,
# only for `source="individual"` series — never called inline, same
# reasoning as this app's sibling features in `post`/`liveclass`: a
# popular creator's follower list can run into the thousands, and a
# synchronous loop inside the publish request/response cycle would make
# that request slow in direct proportion to follower count.
#
# Deliberately does NOT go through this module's own `_notify` helper
# (imported above from `.models`, used by the reminder tasks above for a
# single recipient at a time) — looping `_notify` once per follower would
# mean one `create_notification` call (and, if it ever grows an
# actor/restrict check the way `core.services.create_notification`
# already has, one restrict-check query too) per follower, which doesn't
# scale to a fan-out that can be thousands of rows. Uses
# `core.services.create_bulk_notifications` directly instead — one bulk
# INSERT — with the same manual restrict-exclusion (a single bulk query)
# already used by the equivalent `post`/`liveclass` follower-fan-out
# tasks, so a follower who has restricted the creator still doesn't get
# notified even though this path skips the single-recipient helper.
# ---------------------------------------------------------------------------
@shared_task
def notify_followers_new_testseries(series_id):
    """Notify every ACCEPTED follower of an individual series' creator
    that a new series just went live. MVP version, same as the
    post/classroom equivalents: no per-follower "bell" opt-in yet, every
    accepted follower gets notified.
    """
    from core.models import Notification
    from core.services import create_bulk_notifications
    from user_profile.models import Follow, RestrictUser

    series = TestSeries.objects.select_related("creator").filter(pk=series_id).first()
    if not series:
        # Series was deleted between enqueue and run — nothing left to
        # notify about.
        logger.warning("notify_followers_new_testseries: TestSeries %s no longer exists", series_id)
        return 0

    # Re-checked here, not just trusted at the view's call site — this
    # task could in principle be invoked directly (shell, admin action, a
    # retried/replayed job) without going through
    # TestSeriesViewSet.publish()'s own guard.
    if series.source != TestSeries.Source.INDIVIDUAL:
        return 0
    if series.status != TestSeries.Status.PUBLISHED:
        # Un-published / reverted to draft between enqueue and run —
        # don't notify about something that's no longer live.
        return 0

    follower_ids = set(
        Follow.objects.filter(
            following_id=series.creator_id, status=Follow.Status.ACCEPTED
        ).values_list("follower_id", flat=True)
    )
    if not follower_ids:
        return 0

    # Restrict is defined to be invisible to the restricted user (see
    # create_notification's own docstring in core/services.py) — one bulk
    # query for every follower who has restricted this series' creator,
    # instead of one is_restricted_between() call per follower.
    restricting_follower_ids = set(
        RestrictUser.objects.filter(
            user_id__in=follower_ids, restricted_id=series.creator_id
        ).values_list("user_id", flat=True)
    )
    recipient_ids = follower_ids - restricting_follower_ids
    if not recipient_ids:
        return 0

    creator_name = series.creator.get_full_name() or series.creator.username
    create_bulk_notifications(
        recipient_ids,
        Notification.NotifType.TESTSERIES_CREATED_BY_FOLLOWED,
        f"{creator_name} published a new test series",
        f"'{series.title}' is now live.",
        data={"series_id": str(series.id), "actor_id": str(series.creator_id)},
    )
    logger.info(
        "notify_followers_new_testseries: notified %d follower(s) for series %s",
        len(recipient_ids), series_id,
    )
    return len(recipient_ids)