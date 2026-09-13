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

[VERIFIED] `core.services.create_notification`'s real signature is now
confirmed: `(recipient, notif_type, title, message="", *, classroom=None,
session=None, data=None, actor=None)`. The keyword args used below
(`recipient`, `notif_type`, `title`, `message`, `data`) all match; the
sweep intentionally omits `actor` (this is a system-triggered reminder,
not something a user did, so the restrict-user check inside
`create_notification` correctly doesn't apply here) and `classroom`/
`session` (not applicable to a due-date reminder). Note that
`create_notification` swallows and logs failures from its own
`Notification.objects.create()` call and returns `None` rather than
raising — but the lazy `is_restricted_between` import/call inside it is
NOT covered by that try/except, so it can still raise past
`create_notification` into this module's own per-row try/except below,
which is why that try/except is kept regardless.
`assignment/bridge.py`'s `notify_submission_received()` made the same
assumption and should be checked against this same confirmed signature
if it hasn't been already.

[FIX — notif_type collision]: this module previously sent
notif_type="assignment_due_reminder" as a raw string literal. That value
is campus's own enum member (`Notification.NotifType.ASSIGNMENT_DUE_
REMINDER`, listed under `CAMPUS_APP_TYPES` in core/models.py) — core
deliberately defines a separate `ASSIGNMENT_DUE_SOON = "assignment_due_
soon"` for this unified assignment app precisely so the two reminder
events aren't aliases of each other. Sending the campus string here
silently misrouted every platform-wide assignment reminder as a campus
one downstream (clients pick deep-link/copy off notif_type). Fixed to
reference `Notification.NotifType.ASSIGNMENT_DUE_SOON` directly — using
the enum member, not another string literal, so a future rename in
core/models.py breaks import-time/at call time instead of silently
reintroducing this bug.

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

from core.models import Notification
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
                notif_type=Notification.NotifType.ASSIGNMENT_DUE_SOON,
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