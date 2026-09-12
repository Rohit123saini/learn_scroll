# assignment/tasks.py
"""
Due-date reminder sweep. A plain function, not a `@shared_task`/`@app.task`
— same reasoning `login/models.py`'s OTPVerification docstring gives for
its own periodic cleanup job: whichever scheduler this project actually
runs (Celery beat, django-cron, a management command on system cron) can
wrap this call, without this module taking a hard dependency on any of
them.

Per the design doc §5.4, campus's own `tasks.send_assignment_due_
reminders` is expected to move to querying `assignment.Assignment`
directly (filtered by `context_type="section"`) rather than duplicating
this sweep — this function is written generically enough (no source/
context filter) to cover personal assignments too, which neither campus
nor liveclass ever will.

[ASSUMPTION — NOT VERIFIED]: `core.services.create_notification`'s exact
signature wasn't available in this pass (only core/models.py was
provided, not core/services.py). The call below assumes keyword args
matching `core.Notification`'s own fields (`recipient`, `notif_type`,
`title`, `message`, `data`) — the same assumption `assignment/bridge.py`'s
`notify_submission_received()` makes. Verify both call sites against the
real `create_notification` signature before relying on this in
production; update together if it differs.

[FIX — Task 10] IDEMPOTENCY: this module previously stated, in this
docstring, that it deliberately did not de-duplicate — "add an explicit
column if de-dup is needed". Task 10's own acceptance checklist now
requires re-running the sweep to never re-notify the same submission, so
that's no longer optional. A DB column (`last_reminded_at` on
`AssignmentSubmission`) was deliberately NOT the fix here even though
it's the more permanent-looking option: Task 10's checklist also requires
`makemigrations assignment` to stay clean, i.e. this task must not change
`models.py`. So the dedup marker lives in the cache instead of the DB —
one `cache.add()` per (submission, due_date) — which is a real tradeoff,
not a free win:
  - `cache.add()` is atomic (set-if-absent), so two overlapping sweep
    runs racing on the same submission can't both send — one wins the
    add, the other sees it already set and skips. A plain get-then-set
    would have that race.
  - Marker TTL is deliberately longer than one calendar day (see
    `_REMINDER_CACHE_TTL_SECONDS`) so a sweep scheduled more than once in
    the same day can't slip past it, while still expiring well before a
    submission could next become "due soon" again on a genuinely later
    due date.
  - Being cache-backed (not a DB column), the dedup marker does not
    survive a cache flush/eviction under memory pressure — an evicted
    marker means a possible duplicate reminder, not a lost one. That's
    the accepted failure mode for choosing "no migration" over "a real
    audit column"; if losing that guarantee is unacceptable, revisit with
    an actual `last_reminded_at` column and a migration instead.
  - On a `create_notification` failure, the marker is rolled back
    (`cache.delete`) before re-raising into the existing per-row
    try/except below, so a failed send is still retried on the next
    sweep rather than being permanently (and silently) suppressed.
"""
import logging
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from core.services import create_notification

from .models import AssignmentSubmission

logger = logging.getLogger(__name__)

_REMINDER_CACHE_KEY = "assignment:due_reminder_sent:{submission_id}:{due_date}"
# 36h: safely spans one calendar day plus scheduler jitter (a sweep that
# runs slightly early/late, or twice in the same day), while still well
# under the days/weeks that would pass before the same submission could
# plausibly enter a new "due soon" window on a later due_date.
_REMINDER_CACHE_TTL_SECONDS = 36 * 60 * 60


def send_due_reminders(*, lookahead_hours: int = 24) -> int:
    """Notifies every student who still has a MISSING submission for an
    assignment due within `lookahead_hours`. Returns how many
    notifications were sent (not counting ones skipped as already-sent),
    so a management command wrapping this can log/report it.

    Idempotent across repeated runs within the same due_date — see the
    module docstring's [FIX — Task 10] note for how and why (cache-based,
    not a DB column).
    """
    now = timezone.now()
    window_end_date = (now + timedelta(hours=lookahead_hours)).date()

    due_soon = AssignmentSubmission.objects.filter(
        status=AssignmentSubmission.SubmissionStatus.MISSING,
        assignment__due_date__isnull=False,
        assignment__due_date__gte=now.date(),
        assignment__due_date__lte=window_end_date,
    ).select_related("assignment", "student")

    # [HARDENING] .iterator() — see PRODUCTION_DESIGN.md §2.5. Avoids
    # materializing the full due-soon queryset in memory at once; at
    # platform scale this can be tens/hundreds of thousands of rows in a
    # busy lookahead window.
    count = 0
    skipped_already_sent = 0
    for submission in due_soon.iterator(chunk_size=2000):
        assignment = submission.assignment
        cache_key = _REMINDER_CACHE_KEY.format(submission_id=submission.id, due_date=assignment.due_date)
        if not cache.add(cache_key, True, timeout=_REMINDER_CACHE_TTL_SECONDS):
            # Someone (this sweep or an overlapping one) already claimed
            # this (submission, due_date) pair — don't re-notify.
            skipped_already_sent += 1
            continue
        try:
            create_notification(
                recipient=submission.student,
                notif_type="assignment_due_reminder",
                title="Assignment Due Soon",
                message=f'"{assignment.title}" is due on {assignment.due_date}.',
                data={"context_type": assignment.context_type, "context_id": str(assignment.context_id or "")},
            )
            count += 1
        except Exception:
            # Roll back the "sent" marker — a create_notification-side
            # failure for this one recipient must not permanently
            # suppress a reminder that never actually went out; the next
            # sweep should retry it.
            cache.delete(cache_key)
            # [HARDENING] — one bad row (e.g. a stale FK, a
            # create_notification-side failure for one recipient) must
            # not abort the whole sweep; every other student still needs
            # their reminder. Logged with the failing submission's id so
            # it's individually re-driveable, not silently dropped.
            logger.warning("assignment.due_reminder_failed submission_id=%s", submission.id, exc_info=True)

    logger.info(
        "assignment.due_reminders_sent count=%d skipped_already_sent=%d lookahead_hours=%d",
        count, skipped_already_sent, lookahead_hours,
    )
    return count