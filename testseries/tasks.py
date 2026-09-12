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

from .models import TestAttempt, TestSeriesPurchase, _notify

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
