# liveclass/tasks.py
"""
Celery tasks that make "recurring schedule", "reminder", "waitlist seat
opened", and "session auto-close" actually DO something at the right time,
instead of just sitting as rows in the database with nothing to trigger
them. See LearnScroll/celery.py for how to run the worker + beat process,
and CELERY_BEAT_SCHEDULE in settings.py for the cadence each one runs on.

Design discipline (same as views.py/signals.py):
    - Every batch task is best-effort per-row: one bad row (a stale FK, a
      corrupt schedule) is logged and skipped, never aborts the whole run.
    - session generation is idempotent — get_or_create keyed on
      (schedule, scheduled_start) — so re-running this task (a retried
      beat tick, a manual trigger) never creates duplicate sessions.
    - auto-complete saves each ClassSession individually (never a bulk
      .update()) specifically so the existing post_save signal in
      signals.py still fires and does its normal cleanup (LiveKit
      teardown, close polls, force-checkout participants, clear waitlist)
      — that logic already exists and is correct, this task just needs to
      be one more path that triggers a status transition into COMPLETED.
    - notification delivery (reminders, waitlist promotion) never raises
      past send_notification() — a bad/unconfigured channel is logged in
      notifications.py, never lets a notification failure block anything.
"""

import logging
import os
import shutil
import zoneinfo
from datetime import date, datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# How far ahead of "now" we pre-generate ClassSession rows for an active
# schedule. Re-running with a different value is safe (idempotent) — this
# only controls how far in advance students can see/join upcoming classes.
SESSION_GENERATION_WINDOW_DAYS = 14

# Grace period past scheduled_end before a forgotten-to-close session gets
# auto-completed. Long enough that a teacher wrapping up 5-10 minutes late
# doesn't get their session yanked mid-sentence; short enough that nothing
# stays "live" (billing LiveKit, blocking attendance/waitlist cleanup)
# indefinitely just because nobody clicked /end/.
AUTO_COMPLETE_GRACE_MINUTES = 30

# How far back refresh_stale_enrolled_counts looks for passes that expired
# since it last ran. Should be >= this task's own beat interval (see
# CELERY_BEAT_SCHEDULE in settings.py) with some slack, so a slow/delayed
# beat tick never lets a batch of expiries fall in the gap between two runs
# and get missed entirely.
REFRESH_ENROLLED_COUNT_LOOKBACK_MINUTES = 60

# How far back expire_and_refund_passes looks for passes that expired since
# it last ran — same "rolling window, not full-table rescan" reasoning as
# REFRESH_ENROLLED_COUNT_LOOKBACK_MINUTES above. Keep this >= this task's own
# beat interval (with slack) so a slow/delayed beat tick can never let a
# batch of expiries fall in the gap between two runs and get missed.
EXPIRE_REFUND_LOOKBACK_MINUTES = 60

# How long a CoinPurchase can sit PENDING (order created, gateway
# checkout never completed/verified — the client crashed, the user
# closed the checkout without paying, or a webhook never arrived) before
# reconcile_stuck_coin_purchases below gives up on it and marks it
# FAILED, so it becomes retry-able instead of hanging forever in limbo.
COIN_PURCHASE_PENDING_TIMEOUT = timedelta(hours=2)

# How long a ChunkedUpload can sit with no chunk activity before
# cleanup_stale_chunked_uploads treats it as abandoned (client crashed,
# closed the app, or lost network mid-upload) and reclaims its temp disk
# usage. Keyed off updated_at, not created_at, so a slow-but-alive upload
# that's still actively sending chunks is never swept mid-flight.
CHUNKED_UPLOAD_STALE_AFTER = timedelta(hours=6)

# How long to keep terminal-status (COMPLETED/ABORTED/EXPIRED/FAILED)
# ChunkedUpload rows around for audit/debugging before
# cleanup_stale_chunked_uploads purges them outright.
CHUNKED_UPLOAD_ROW_RETENTION_DAYS = 14

_WEEKDAY_CODE = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def _dates_for_schedule(schedule, window_start: date, window_end: date):
    """Yield every calendar date in [window_start, window_end] this
    schedule's recurrence rule fires on. Pure function, no DB/holiday
    lookups here — that's handled by the caller — so this stays easy to
    unit-test on its own."""
    from .models import ClassSchedule

    start = max(window_start, schedule.start_date)
    end = window_end if schedule.end_date is None else min(window_end, schedule.end_date)
    if start > end:
        return

    rtype = schedule.recurrence_type
    d = start
    while d <= end:
        fire = False
        if rtype == ClassSchedule.RecurrenceType.SPECIFIC_DATE:
            fire = d == schedule.start_date
        elif rtype == ClassSchedule.RecurrenceType.DAILY:
            fire = True
        elif rtype == ClassSchedule.RecurrenceType.WEEKDAY:
            fire = d.weekday() < 5
        elif rtype == ClassSchedule.RecurrenceType.WEEKEND:
            fire = d.weekday() >= 5
        elif rtype == ClassSchedule.RecurrenceType.WEEKLY:
            # NOTE (fix — CRITICAL): compare case-insensitively. Storage is
            # now normalized to lowercase at the serializer (see
            # ClassScheduleSerializer.validate()), but this is the one
            # check standing between "schedule has a typo/legacy bad case"
            # and "this schedule silently never generates a single session,
            # forever, with no error anywhere" — so it stays defensive even
            # though the write side is now fixed too.
            configured_days = {str(x).strip().lower()[:3] for x in (schedule.days_of_week or [])}
            fire = _WEEKDAY_CODE[d.weekday()] in configured_days
        elif rtype == ClassSchedule.RecurrenceType.MONTHLY:
            fire = schedule.day_of_month is not None and d.day == schedule.day_of_month
        elif rtype == ClassSchedule.RecurrenceType.YEARLY:
            fire = d.month == schedule.start_date.month and d.day == schedule.start_date.day

        if fire:
            yield d
        d += timedelta(days=1)


@shared_task(name="liveclass.generate_upcoming_sessions")
def generate_upcoming_sessions():
    """Turn every active ClassSchedule's recurrence rule into actual,
    joinable ClassSession rows for the next SESSION_GENERATION_WINDOW_DAYS.
    Safe to re-run any time (hourly, via CELERY_BEAT_SCHEDULE) — skips
    dates that already have a session for that schedule, and skips
    ClassHoliday-marked dates (schedule-specific or classroom-wide, via
    ClassSchedule.is_off_on which already existed but had no caller)."""
    from .models import ClassSchedule, ClassSession

    window_start = timezone.localdate()
    window_end = window_start + timedelta(days=SESSION_GENERATION_WINDOW_DAYS)

    created_count = 0
    schedules = ClassSchedule.objects.filter(is_active=True).select_related("classroom")

    for schedule in schedules:
        try:
            tz = zoneinfo.ZoneInfo(schedule.timezone)
        except Exception:
            logger.warning(
                "ClassSchedule %s has invalid timezone %r — falling back to "
                "project default.", schedule.pk, schedule.timezone,
            )
            tz = timezone.get_default_timezone()

        for d in _dates_for_schedule(schedule, window_start, window_end):
            try:
                if schedule.is_off_on(d):
                    continue

                start_naive = datetime.combine(d, schedule.start_time)
                scheduled_start = start_naive.replace(tzinfo=tz)
                scheduled_end = scheduled_start + timedelta(minutes=schedule.duration_minutes)

                _, created = ClassSession.objects.get_or_create(
                    schedule=schedule,
                    scheduled_start=scheduled_start,
                    defaults={
                        "classroom": schedule.classroom,
                        "scheduled_end": scheduled_end,
                    },
                )
                if created:
                    created_count += 1
            except Exception:
                logger.exception(
                    "Failed generating session for schedule %s on %s — skipped.", schedule.pk, d
                )

    logger.info("generate_upcoming_sessions: created %s new session(s).", created_count)
    return created_count


@shared_task(name="liveclass.auto_complete_overdue_sessions")
def auto_complete_overdue_sessions():
    """Force-complete any session still SCHEDULED/LIVE well past its
    scheduled_end — catches a teacher who forgot to click /end/. Saves
    each row individually (never .update()) so the ClassSession post_save
    signal in signals.py fires normally and does its usual cleanup.

    NOTE (fix — same actual_end gap as ClassSessionViewSet.end): this
    path marks a session COMPLETED too, so it needs to stamp actual_end
    for the same reason — PassPurchase.charge_for_session() keys the
    per-day escrow release off it. Stamped as "now" (when the sweep
    caught it), not scheduled_end, since that's the truthful "when the
    system actually closed this out" — scheduled_end is just what
    triggered the sweep to look at it AUTO_COMPLETE_GRACE_MINUTES later.
    """
    from .models import ClassSession

    cutoff = timezone.now() - timedelta(minutes=AUTO_COMPLETE_GRACE_MINUTES)
    overdue = ClassSession.objects.filter(
        status__in=[ClassSession.Status.SCHEDULED, ClassSession.Status.LIVE],
        scheduled_end__lt=cutoff,
    )

    completed_count = 0
    for session in overdue:
        try:
            session.status = ClassSession.Status.COMPLETED
            session.actual_end = timezone.now()
            session.save(update_fields=["status", "actual_end"])
            completed_count += 1
            # NOTE (fix): a teacher who forgot to click /end/ previously got
            # no signal that the platform closed their session for them —
            # confusing if they come back expecting it still LIVE. Queued,
            # same reasoning as every other notification here: a failed
            # send must never re-raise into this loop and skip cleanup for
            # the rest of the batch.
            notify_session_auto_completed.delay(session.id)
        except Exception:
            logger.exception("Failed to auto-complete overdue session %s", session.pk)

    if completed_count:
        logger.info("auto_complete_overdue_sessions: completed %s session(s).", completed_count)
    return completed_count


@shared_task(name="liveclass.send_due_reminders")
def send_due_reminders():
    """Fire every ClassReminder whose remind_at has arrived and hasn't been
    sent yet. Marks is_sent right after processing (whether or not the
    channel actually delivered) so a retried/overlapping run of this task
    never double-processes the same row — delivery failures worth
    investigating show up in notifications.py's logs instead."""
    from .models import ClassReminder
    from .notifications import send_notification

    due = list(
        ClassReminder.objects.filter(is_sent=False, remind_at__lte=timezone.now())
        .select_related("user", "session__classroom")
    )

    sent_count = 0
    for reminder in due:
        try:
            session = reminder.session
            title = f"Class starting soon: {session.classroom.title}"
            message = (
                f"{session.classroom.title} starts at "
                f"{timezone.localtime(session.scheduled_start):%d %b, %I:%M %p}."
            )
            ok = send_notification(
                reminder.user, title, message, channel=reminder.channel,
                data={
                    "type": "class_reminder",
                    "session_id": str(session.id),
                    "classroom_id": str(session.classroom_id),
                },
            )
            reminder.is_sent = True
            reminder.save(update_fields=["is_sent"])
            if ok:
                sent_count += 1
        except Exception:
            logger.exception("Failed processing reminder %s", reminder.pk)

    if due:
        logger.info("send_due_reminders: processed %s, delivered %s.", len(due), sent_count)
    return sent_count


@shared_task(name="liveclass.refresh_stale_enrolled_counts")
def refresh_stale_enrolled_counts():
    """NOTE (fix — this task didn't exist at all before): Classroom.enrolled_count
    is a denormalized "distinct students with a currently valid pass" counter
    (see refresh_enrolled_count() in models.py). It's kept in sync on every
    PassPurchase save (signal in models.py), but a purchase EXPIRING doesn't
    save anything — nothing ever touches that PassPurchase row again once
    it's created, so enrolled_count silently drifts upward and stays wrong
    for any classroom whose passes just aged out without a fresh purchase
    replacing them. refresh_enrolled_count()'s own docstring already called
    this out as a required periodic job; it just never existed. Scoped to
    classrooms with at least one pass that expired since this task last ran
    (a rolling lookback, not "all classrooms with any expired pass ever")
    so a long-lived platform doesn't re-scan its entire classroom table on
    every tick — a classroom whose count is already correct costs one query
    and gets skipped."""
    from .models import Classroom, PassPurchase

    lookback_start = timezone.now() - timedelta(minutes=REFRESH_ENROLLED_COUNT_LOOKBACK_MINUTES)
    classroom_ids = (
        PassPurchase.objects.filter(
            status=PassPurchase.Status.SUCCESS,
            expires_at__gte=lookback_start,
            expires_at__lte=timezone.now(),
        )
        .values_list("class_pass__classroom_id", flat=True)
        .distinct()
    )

    refreshed = 0
    for classroom in Classroom.objects.filter(id__in=classroom_ids):
        try:
            classroom.refresh_enrolled_count()
            refreshed += 1
        except Exception:
            logger.exception("Failed refreshing enrolled_count for classroom %s", classroom.pk)

    if refreshed:
        logger.info("refresh_stale_enrolled_counts: refreshed %s classroom(s).", refreshed)
    return refreshed


@shared_task(name="liveclass.expire_and_refund_passes")
def expire_and_refund_passes():
    """NOTE (fix — the actual "student loses money" gap): a PassPurchase's
    per-day escrow design (see PassPurchase in models.py) already stops a
    quiet/stopped-teaching classroom from draining a pass all at once —
    remaining_balance only ever gets released one taught day at a time.
    But once expires_at passes, charge_for_session() refuses to release
    anything more (charge_date falls outside the validity window) AND
    nothing was ever calling reverse() for the leftover either — the row
    just sits there, status still SUCCESS, is_active still True, with
    whatever remaining_balance never got taught for stuck in escrow
    forever. The student technically COULD still hit cancel() themselves
    (reverse() doesn't check expiry), but a pass that's already expired
    gives them no reason to think there's anything left to go claim, so
    in practice that money silently never comes back. This is the
    automatic version of exactly what cancel()/refund() do manually:
    sweep every SUCCESS pass past its expires_at with money still sitting
    in escrow and reverse() it, so an inactive student is made whole
    without having to notice and ask for it. Scoped to a rolling lookback
    (same pattern as refresh_stale_enrolled_counts above) so a long-lived
    platform doesn't rescan every expired pass ever on every tick.
    """
    from .models import PassPurchase
    from django.db import transaction

    lookback_start = timezone.now() - timedelta(minutes=EXPIRE_REFUND_LOOKBACK_MINUTES)
    candidates = PassPurchase.objects.filter(
        status=PassPurchase.Status.SUCCESS,
        is_active=True,
        expires_at__gte=lookback_start,
        expires_at__lt=timezone.now(),
    )

    refunded_count = 0
    for purchase in candidates:
        try:
            with transaction.atomic():
                locked = PassPurchase.objects.select_for_update().get(pk=purchase.pk)
                # Re-check under the lock — status/is_active/remaining_balance
                # could have changed (a student's own cancel() racing this
                # same sweep) between the filter() above and getting here.
                if locked.status != PassPurchase.Status.SUCCESS or not locked.is_active:
                    continue
                if locked.remaining_balance <= 0:
                    continue
                locked.reverse()
            refunded_count += 1
        except Exception:
            logger.exception("Failed to expire-refund purchase %s", purchase.pk)

    if refunded_count:
        logger.info("expire_and_refund_passes: refunded %s expired purchase(s).", refunded_count)
    return refunded_count


@shared_task(name="liveclass.reconcile_stuck_coin_purchases")
def reconcile_stuck_coin_purchases():
    """Beat job (Pass 7) — closes the actual "payment retry" gap alongside
    CoinPurchaseViewSet.retry: a purchase can go PENDING and then just
    never resolve (client crashed after redirect to the gateway, user
    closed the tab, a webhook silently never arrived) with no FAILED/
    SUCCESS transition ever happening on our side. Left alone that
    purchase sits invisible to the student forever — retry() only works
    on a FAILED row, so a stuck PENDING row is neither usable nor
    recoverable without this sweep.

    Deliberately conservative: does NOT call the gateway to check real
    status (no gateway is actually wired in yet — see
    _create_gateway_order's docstring in views.py). Once a real gateway
    client exists, replace the straight mark_failed() call below with an
    actual "fetch payment status" API call first, and only mark_failed()
    if the gateway also reports it as failed/expired — a purchase that
    actually succeeded on the gateway's side but whose webhook was just
    slow should never be marked FAILED out from under a student.
    """
    from django.db import transaction

    from .models import CoinPurchase

    cutoff = timezone.now() - COIN_PURCHASE_PENDING_TIMEOUT
    stuck = CoinPurchase.objects.filter(status=CoinPurchase.Status.PENDING, created_at__lt=cutoff)

    reconciled = 0
    for purchase in stuck:
        try:
            with transaction.atomic():
                locked = CoinPurchase.objects.select_for_update().get(pk=purchase.pk)
                if locked.status != CoinPurchase.Status.PENDING:
                    continue  # resolved by a verify() call that raced this sweep
                locked.mark_failed("Payment not completed within the expected window.")
            reconciled += 1
        except Exception:
            logger.exception("Failed to reconcile stuck coin purchase %s", purchase.pk)

    if reconciled:
        logger.info("reconcile_stuck_coin_purchases: marked %s stuck purchase(s) as failed.", reconciled)
    return reconciled


@shared_task(name="liveclass.notify_waitlist_promotion")
def notify_waitlist_promotion(student_id, session_id):
    """Fired from SessionWaitlistViewSet.promote() the instant a waitlist
    row is promoted (a seat opened up). Kept as its own async task — not
    run inline inside the view — so a slow or failing notification
    provider can never delay the request/response.

    NOTE (fix): this task originally took `waitlist_entry_id` and re-looked
    the row up by pk — but the caller (promote()) deletes that
    SessionWaitlist row as part of the same request, with no transaction
    boundary between the two. Depending on broker/worker timing, this task
    could easily run AFTER the row was already gone, silently returning
    False and no-op'ing the push with nothing in the logs pointing at why.
    Takes the plain (student_id, session_id) values the caller already has
    in hand instead, so there's nothing left to race against."""
    from .models import ClassSession
    from .notifications import send_notification

    session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
    if not session:
        return False

    from django.contrib.auth import get_user_model

    User = get_user_model()
    student = User.objects.filter(pk=student_id).first()
    if not student:
        return False

    title = "A seat opened up!"
    message = f"A seat is now open in {session.classroom.title} — join now."
    return send_notification(
        student, title, message, channel="push",
        data={
            "type": "waitlist_seat_open",
            "session_id": str(session.id),
            "classroom_id": str(session.classroom_id),
        },
    )


@shared_task(name="liveclass.notify_classroom_shared")
def notify_classroom_shared(share_id):
    """Fired from ClassroomViewSet.share() the instant an IN-APP share
    lands (to_user_id was given) — the in-app bell row is created
    synchronously in the view (see create_notification there); this is
    just the push half, queued the same way every other notify_*.delay()
    call site in this file is, so a slow/unconfigured push provider never
    adds latency to the share request itself.

    Takes share_id (not the (classroom_id, shared_by_id, shared_with_id)
    tuple) so the task always reflects exactly the row the view just
    created, and re-looks everything up from it rather than trusting
    values passed across the broker — same reasoning as
    notify_waitlist_promotion's NOTE (fix) below about not racing a
    caller that might delete/mutate rows out from under a queued task.
    """
    from .models import ClassroomShare
    from .notifications import send_notification

    share = (
        ClassroomShare.objects.select_related("classroom", "shared_by", "shared_with")
        .filter(pk=share_id)
        .first()
    )
    if not share or not share.shared_with:
        return False

    sharer_name = share.shared_by.get_full_name() or share.shared_by.username
    title = "A class was shared with you"
    message = f"{sharer_name} shared '{share.classroom.title}' with you."
    return send_notification(
        share.shared_with, title, message, channel="push",
        data={
            "type": "classroom_shared",
            "classroom_id": str(share.classroom_id),
            "shared_by": str(share.shared_by_id),
        },
    )


@shared_task(name="liveclass.notify_purchase_refunded")
def notify_purchase_refunded(purchase_id):
    """Fired from PassPurchase.reverse() the instant a refund lands (a
    single refund, one of the batch refunds ClassroomViewSet.close()
    issues, or a student's own cancel()). NOTE (fix): refunds used to be
    completely silent to the student — coins came back and the ledger
    recorded it, but nothing ever told them, so "I lost access and don't
    know why" looked identical to a bug from their side.

    NOTE (fix — wrong amount shown): this used to quote purchase.coins_spent
    as "credited back", but reverse() only ever refunds remaining_balance
    (coins_spent minus whatever coins_released already legitimately went
    to the teacher for classes actually held — see the per-day escrow
    design on PassPurchase in models.py) — coins_released is untouched by
    reverse(), so remaining_balance here still reflects exactly what was
    handed back. Quoting coins_spent overstated the refund on every
    purchase that had at least one taught day charged against it (the
    exact case a mid-pass close/cancel produces), telling a student they
    got more money back than they actually did.
    """
    from .models import PassPurchase
    from .notifications import send_notification

    purchase = (
        PassPurchase.objects.select_related("student", "class_pass__classroom")
        .filter(pk=purchase_id)
        .first()
    )
    if not purchase:
        return False

    classroom = purchase.class_pass.classroom
    title = "Pass refunded"
    message = (
        f"Your pass for {classroom.title} was refunded — "
        f"{purchase.remaining_balance} coin(s) credited back to your wallet."
    )
    return send_notification(
        purchase.student, title, message, channel="push",
        data={
            "type": "pass_refunded",
            "classroom_id": str(classroom.id),
            "pass_purchase_id": str(purchase.id),
        },
    )


@shared_task(name="liveclass.notify_classroom_flagged")
def notify_classroom_flagged(classroom_id):
    """Fired from models._auto_flag_classroom the instant a classroom
    crosses AUTO_FLAG_THRESHOLD pending reports and drops out of Explore.
    NOTE (fix): the teacher previously had no way to find out — they'd
    only notice if enrollment mysteriously stalled."""
    from .models import Classroom
    from .notifications import send_notification

    classroom = Classroom.objects.filter(pk=classroom_id).select_related("teacher").first()
    if not classroom:
        return False

    title = "Your classroom is under review"
    message = (
        f"{classroom.title} has received multiple reports and is temporarily hidden "
        f"from search while our team reviews it. Your existing students still have access."
    )
    return send_notification(
        classroom.teacher, title, message, channel="push",
        data={"type": "classroom_flagged", "classroom_id": str(classroom.id)},
    )


@shared_task(name="liveclass.notify_session_auto_completed")
def notify_session_auto_completed(session_id):
    """Fired from auto_complete_overdue_sessions the instant a forgotten-to-
    close session is force-completed by the platform. NOTE (fix): the
    teacher previously got no signal that this happened — confusing if
    they come back expecting the session still LIVE."""
    from .models import ClassSession
    from .notifications import send_notification

    session = ClassSession.objects.filter(pk=session_id).select_related("classroom__teacher").first()
    if not session:
        return False

    classroom = session.classroom
    title = "Session auto-closed"
    message = (
        f"Your session for {classroom.title} ran past its scheduled end time and was "
        f"automatically marked completed. Recording/attendance are finalized as usual."
    )
    return send_notification(
        classroom.teacher, title, message, channel="push",
        data={"type": "session_auto_completed", "session_id": str(session.id), "classroom_id": str(classroom.id)},
    )


# ---------------------------------------------------------------------------
# NOTE (fix): the events below (join-request lifecycle, grading, certificate
# issuance, urgent notices, doubt answers) already had a matching
# create_notification()/create_bulk_notifications() call in views.py — so an
# in-app (bell icon) row was always created — but NONE of them ever queued an
# actual push. A user with the app closed/backgrounded got no FCM ping for
# any of these, same class of gap send_due_reminders/notify_waitlist_promotion/
# notify_purchase_refunded/notify_classroom_flagged/notify_session_auto_completed
# above already existed to close for their own events. Same discipline as
# every task above: queued (never sent inline from the view, so a slow/failing
# push provider can't add latency to the request/DB transaction that triggered
# it), and send_notification()'s own internal handling means a bad/unconfigured
# channel is logged, never raised back into this task.
# ---------------------------------------------------------------------------
@shared_task(name="liveclass.notify_join_request_received")
def notify_join_request_received(join_request_id):
    """Fired the instant a student raises a join request — the classroom's
    teacher previously only found out by opening the app and remembering to
    check the Join Requests inbox."""
    from .models import ClassJoinRequest
    from .notifications import send_notification

    join_request = (
        ClassJoinRequest.objects.select_related("student", "classroom__teacher")
        .filter(pk=join_request_id)
        .first()
    )
    if not join_request:
        return False

    classroom = join_request.classroom
    student_name = join_request.student.get_full_name() or join_request.student.username
    title = "New join request"
    message = f"{student_name} wants to join '{classroom.title}'."
    return send_notification(
        classroom.teacher, title, message, channel="push",
        data={
            "type": "join_request_received",
            "join_request_id": str(join_request.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.notify_join_request_accepted")
def notify_join_request_accepted(join_request_id):
    """Fired the instant a teacher/co-teacher/moderator accepts a join
    request — the student now has access and should know right away rather
    than stumbling onto it next time they open the app."""
    from .models import ClassJoinRequest
    from .notifications import send_notification

    join_request = (
        ClassJoinRequest.objects.select_related("student", "classroom")
        .filter(pk=join_request_id)
        .first()
    )
    if not join_request:
        return False

    classroom = join_request.classroom
    title = "Join request accepted"
    message = f"You now have access to '{classroom.title}'."
    return send_notification(
        join_request.student, title, message, channel="push",
        data={
            "type": "join_request_accepted",
            "join_request_id": str(join_request.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.notify_join_request_rejected")
def notify_join_request_rejected(join_request_id):
    """Fired the instant a join request is rejected — same reasoning as
    notify_join_request_accepted above."""
    from .models import ClassJoinRequest
    from .notifications import send_notification

    join_request = (
        ClassJoinRequest.objects.select_related("student", "classroom")
        .filter(pk=join_request_id)
        .first()
    )
    if not join_request:
        return False

    classroom = join_request.classroom
    title = "Join request rejected"
    message = f"Your request to join '{classroom.title}' was rejected."
    return send_notification(
        join_request.student, title, message, channel="push",
        data={
            "type": "join_request_rejected",
            "join_request_id": str(join_request.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.notify_assignment_graded")
def notify_assignment_graded(submission_id):
    """Fired the instant a teacher grades a submission."""
    from .models import AssignmentSubmission
    from .notifications import send_notification

    submission = (
        AssignmentSubmission.objects.select_related("student", "assignment__classroom")
        .filter(pk=submission_id)
        .first()
    )
    if not submission:
        return False

    assignment = submission.assignment
    title = "Assignment graded"
    message = f"'{assignment.title}' was graded — score: {submission.score}."
    return send_notification(
        submission.student, title, message, channel="push",
        data={
            "type": "assignment_graded",
            "submission_id": str(submission.id),
            "assignment_id": str(assignment.id),
            "classroom_id": str(assignment.classroom_id),
        },
    )


@shared_task(name="liveclass.notify_certificate_issued")
def notify_certificate_issued(certificate_id):
    """Fired the instant a teacher/co-teacher/moderator issues a
    certificate — a nice "you earned something" moment that's worth an
    actual push, not just a silent bell-icon row."""
    from .models import Certificate
    from .notifications import send_notification

    certificate = (
        Certificate.objects.select_related("student", "classroom")
        .filter(pk=certificate_id)
        .first()
    )
    if not certificate:
        return False

    classroom = certificate.classroom
    title = "Certificate issued"
    message = f"You've been issued a certificate for '{classroom.title}'."
    return send_notification(
        certificate.student, title, message, channel="push",
        data={
            "type": "certificate_issued",
            "certificate_id": str(certificate.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.notify_notice_posted")
def notify_notice_posted(notice_id, student_ids):
    """Fired only for URGENT notices (same gating NoticeViewSet.perform_create
    already applies for the in-app fan-out) — routine notices don't push,
    only ones the teacher explicitly flagged urgent. Best-effort per
    recipient, same as every other batch job in this file: one bad/deleted
    user in student_ids is logged and skipped, never aborts the rest of the
    fan-out."""
    from .models import Notice
    from .notifications import send_notification

    notice = Notice.objects.select_related("classroom").filter(pk=notice_id).first()
    if not notice:
        return 0

    classroom = notice.classroom
    title = f"Urgent: {notice.title}"
    message = f"New urgent notice in '{classroom.title}'."
    data = {"type": "notice_posted", "notice_id": str(notice.id), "classroom_id": str(classroom.id)}

    from django.contrib.auth import get_user_model

    User = get_user_model()
    sent_count = 0
    for student in User.objects.filter(id__in=student_ids):
        try:
            if send_notification(student, title, message, channel="push", data=data):
                sent_count += 1
        except Exception:
            logger.exception("Failed pushing urgent notice %s to user %s", notice_id, student.pk)

    return sent_count


# ---------------------------------------------------------------------------
# NOTE (fix — production notification coverage audit, 2nd pass): six more
# real student/teacher-facing events had NO notification of any kind
# (not even the silent in-app bell row) before this — see the matching
# create_notification()/create_bulk_notifications() + notify_*.delay() call
# sites added alongside these in views.py. Same discipline as every task
# above: queued (never sent inline from the view/action), and
# send_notification()'s own try/except means a bad/unconfigured channel is
# logged, never raised back into these tasks.
# ---------------------------------------------------------------------------
@shared_task(name="liveclass.notify_session_live")
def notify_session_live(session_id, exclude_user_id=None):
    """Fired the instant a session flips SCHEDULED -> LIVE (the
    teacher/co-teacher's own /join/ call). Students who never set an
    explicit ClassReminder previously had NO way to know class had
    actually started short of opening the app and checking — this is the
    "your teacher just went live" push that closes that gap. Fanned out to
    every student with a currently active pass on the classroom, minus
    whoever's join() call triggered the transition (they don't need a push
    telling them the class they're already in has started)."""
    from .models import ClassSession, PassPurchase
    from .notifications import send_notification

    session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
    if not session:
        return 0

    classroom = session.classroom
    student_ids = (
        PassPurchase.objects.filter(
            class_pass__classroom=classroom,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .exclude(student_id=exclude_user_id)
        .values_list("student_id", flat=True)
        .distinct()
    )

    from django.contrib.auth import get_user_model

    User = get_user_model()
    title = f"{classroom.title} is live now"
    message = "Your teacher just started the class — tap to join."
    data = {"type": "session_live", "session_id": str(session.id), "classroom_id": str(classroom.id)}

    sent_count = 0
    for student in User.objects.filter(id__in=list(student_ids)):
        try:
            if send_notification(student, title, message, channel="push", data=data):
                sent_count += 1
        except Exception:
            logger.exception("Failed pushing session_live (session %s) to user %s", session_id, student.pk)
    return sent_count


@shared_task(name="liveclass.notify_session_cancelled")
def notify_session_cancelled(classroom_id, classroom_title, session_id, scheduled_start_iso, exclude_user_id=None):
    """Fired when a teacher cancels/deletes a session (perform_destroy, or a
    status update to CANCELLED) — students who had it on their schedule
    otherwise find out only by opening the app and noticing the session is
    gone. Takes plain values rather than a session_id-only lookup because
    the row may already be deleted by the time this runs (perform_destroy
    queues this before/at the same point the instance is removed)."""
    from .notifications import send_notification
    from .models import PassPurchase

    student_ids = (
        PassPurchase.objects.filter(
            class_pass__classroom_id=classroom_id,
            status=PassPurchase.Status.SUCCESS,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .exclude(student_id=exclude_user_id)
        .values_list("student_id", flat=True)
        .distinct()
    )

    from django.contrib.auth import get_user_model

    User = get_user_model()
    title = f"Session cancelled: {classroom_title}"
    message = f"A scheduled session ({scheduled_start_iso}) for '{classroom_title}' was cancelled."
    data = {"type": "session_cancelled", "classroom_id": str(classroom_id)}
    if session_id:
        data["session_id"] = str(session_id)

    sent_count = 0
    for student in User.objects.filter(id__in=list(student_ids)):
        try:
            if send_notification(student, title, message, channel="push", data=data):
                sent_count += 1
        except Exception:
            logger.exception("Failed pushing session_cancelled (classroom %s) to user %s", classroom_id, student.pk)
    return sent_count


@shared_task(name="liveclass.notify_assignment_posted")
def notify_assignment_posted(assignment_id, student_ids):
    """Fired when a teacher/co-teacher/moderator posts a new assignment —
    same fan-out shape as notify_notice_posted. Students previously found
    out about a new assignment only by happening to open the Assignments
    tab; a due-date-bearing task deserves an actual push, not just relying
    on the student to check."""
    from .models import Assignment
    from .notifications import send_notification

    assignment = Assignment.objects.select_related("classroom").filter(pk=assignment_id).first()
    if not assignment:
        return 0

    classroom = assignment.classroom
    title = "New assignment posted"
    message = f"'{assignment.title}' was posted in '{classroom.title}'."
    data = {"type": "assignment_posted", "assignment_id": str(assignment.id), "classroom_id": str(classroom.id)}

    from django.contrib.auth import get_user_model

    User = get_user_model()
    sent_count = 0
    for student in User.objects.filter(id__in=student_ids):
        try:
            if send_notification(student, title, message, channel="push", data=data):
                sent_count += 1
        except Exception:
            logger.exception("Failed pushing assignment_posted %s to user %s", assignment_id, student.pk)
    return sent_count


@shared_task(name="liveclass.notify_submission_received")
def notify_submission_received(submission_id):
    """Fired the instant a student submits an assignment — the teacher-side
    counterpart to notify_assignment_graded. Previously a teacher had no
    signal a submission had come in short of manually reopening the
    grading queue for every assignment they'd posted."""
    from .models import AssignmentSubmission
    from .notifications import send_notification

    submission = (
        AssignmentSubmission.objects.select_related("student", "assignment__classroom")
        .filter(pk=submission_id)
        .first()
    )
    if not submission:
        return False

    assignment = submission.assignment
    classroom = assignment.classroom
    student_name = submission.student.get_full_name() or submission.student.username
    title = "New submission to grade"
    message = f"{student_name} submitted '{assignment.title}' in '{classroom.title}'."
    return send_notification(
        classroom.teacher, title, message, channel="push",
        data={
            "type": "submission_received",
            "submission_id": str(submission.id),
            "assignment_id": str(assignment.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.notify_staff_added")
def notify_staff_added(staff_id):
    """Fired the instant a teacher adds a co-teacher/moderator — the added
    user previously had no signal they'd gained manage rights on a
    classroom short of stumbling onto it in the app."""
    from .models import ClassroomStaff
    from .notifications import send_notification

    staff = ClassroomStaff.objects.select_related("user", "classroom").filter(pk=staff_id).first()
    if not staff:
        return False

    classroom = staff.classroom
    title = "You were added as staff"
    message = f"You've been added as {staff.get_role_display()} on '{classroom.title}'."
    return send_notification(
        staff.user, title, message, channel="push",
        data={"type": "staff_added", "classroom_id": str(classroom.id)},
    )


@shared_task(name="liveclass.notify_review_posted")
def notify_review_posted(review_id):
    """Fired the instant a student leaves a classroom review — a teacher
    previously found out only by opening the classroom's review list."""
    from .models import ClassroomReview
    from .notifications import send_notification

    review = ClassroomReview.objects.select_related("student", "classroom__teacher").filter(pk=review_id).first()
    if not review:
        return False

    classroom = review.classroom
    student_name = review.student.get_full_name() or review.student.username
    title = "New review"
    message = f"{student_name} left a {review.rating}-star review on '{classroom.title}'."
    return send_notification(
        classroom.teacher, title, message, channel="push",
        data={"type": "review_posted", "classroom_id": str(classroom.id)},
    )


@shared_task(name="liveclass.notify_report_reviewed")
def notify_report_reviewed(report_id):
    """Fired the instant platform staff resolve a classroom report — the
    student who filed it previously had no way to know the outcome short
    of re-checking their own report list."""
    from .models import ClassroomReport
    from .notifications import send_notification

    report = ClassroomReport.objects.select_related("reported_by", "classroom").filter(pk=report_id).first()
    if not report:
        return False

    classroom = report.classroom
    title = "Your report was reviewed"
    message = f"Your report on '{classroom.title}' was marked '{report.get_status_display()}'."
    return send_notification(
        report.reported_by, title, message, channel="push",
        data={"type": "report_reviewed", "classroom_id": str(classroom.id)},
    )


@shared_task(name="liveclass.notify_query_answered")
def notify_query_answered(query_id):
    """Fired the instant a teacher/co-teacher/moderator answers a student's
    doubt."""
    from .models import ClassQuery
    from .notifications import send_notification

    query = ClassQuery.objects.select_related("asked_by", "classroom").filter(pk=query_id).first()
    if not query:
        return False

    classroom = query.classroom
    title = "Your doubt was answered"
    message = f"'{query.question[:60]}' has been answered in '{classroom.title}'."
    return send_notification(
        query.asked_by, title, message, channel="push",
        data={
            "type": "query_answered",
            "query_id": str(query.id),
            "classroom_id": str(classroom.id),
        },
    )


@shared_task(name="liveclass.cleanup_stale_chunked_uploads")
def cleanup_stale_chunked_uploads():
    """Sweeps abandoned ChunkedUpload rows (liveclass/chunked_upload_views.py)
    and reclaims their temp disk usage. Three passes, each isolated in its
    own try/except (same one-bad-row-never-blocks-the-rest discipline as
    every other task in this file):

    1. Any row still IN_PROGRESS/PROCESSING with no chunk activity for
       CHUNKED_UPLOAD_STALE_AFTER gets its temp chunk directory deleted and
       flipped to EXPIRED.
    2. Old terminal rows (COMPLETED/ABORTED/EXPIRED/FAILED) older than
       CHUNKED_UPLOAD_ROW_RETENTION_DAYS get deleted outright — pure DB
       cleanup, no disk I/O (COMPLETED rows already had their temp files
       removed at complete()-time).
    3. Defensive sweep: any directory under CHUNKED_UPLOAD_TMP_ROOT with no
       matching ChunkedUpload row at all (e.g. the row got deleted by
       something else, or a directory was created but the DB write failed)
       and is older than the staleness window gets removed too — this is
       what actually guarantees disk usage can't grow unbounded even if
       pass 1 ever misses a case.
    """
    from django.conf import settings as dj_settings

    from .models import ChunkedUpload

    tmp_root = str(getattr(dj_settings, "CHUNKED_UPLOAD_TMP_ROOT", ""))
    cutoff = timezone.now() - CHUNKED_UPLOAD_STALE_AFTER
    expired_count, freed_bytes = 0, 0

    # ---- 1. Expire stale in-flight uploads --------------------------------
    stale_qs = ChunkedUpload.objects.filter(
        status__in=[ChunkedUpload.Status.IN_PROGRESS, ChunkedUpload.Status.PROCESSING],
        updated_at__lt=cutoff,
    )
    for upload in stale_qs.iterator():
        try:
            upload_dir = os.path.join(tmp_root, str(upload.upload_id)) if tmp_root else None
            if upload_dir and os.path.isdir(upload_dir):
                freed_bytes += _chunked_upload_dir_size(upload_dir)
                shutil.rmtree(upload_dir, ignore_errors=True)
            ChunkedUpload.objects.filter(pk=upload.pk).update(
                status=ChunkedUpload.Status.EXPIRED,
                error_message="Expired — no chunk activity for over 6 hours.",
            )
            expired_count += 1
        except Exception:
            logger.exception(
                "cleanup_stale_chunked_uploads: failed to expire upload_id=%s", upload.upload_id
            )

    # ---- 2. Purge old terminal rows ----------------------------------------
    purged_count = 0
    try:
        old_cutoff = timezone.now() - timedelta(days=CHUNKED_UPLOAD_ROW_RETENTION_DAYS)
        purged_count, _ = ChunkedUpload.objects.filter(
            status__in=[
                ChunkedUpload.Status.COMPLETED,
                ChunkedUpload.Status.ABORTED,
                ChunkedUpload.Status.EXPIRED,
                ChunkedUpload.Status.FAILED,
            ],
            updated_at__lt=old_cutoff,
        ).delete()
    except Exception:
        logger.exception("cleanup_stale_chunked_uploads: failed to purge old rows")

    # ---- 3. Defensive orphan-directory sweep -------------------------------
    orphan_count = 0
    try:
        if tmp_root and os.path.isdir(tmp_root):
            known_ids = set(str(u) for u in ChunkedUpload.objects.values_list("upload_id", flat=True))
            for entry in os.scandir(tmp_root):
                if not entry.is_dir() or entry.name in known_ids:
                    continue
                try:
                    mtime = timezone.datetime.fromtimestamp(
                        entry.stat().st_mtime, tz=timezone.get_current_timezone()
                    )
                except OSError:
                    continue
                if mtime < cutoff:
                    freed_bytes += _chunked_upload_dir_size(entry.path)
                    shutil.rmtree(entry.path, ignore_errors=True)
                    orphan_count += 1
    except Exception:
        logger.exception("cleanup_stale_chunked_uploads: orphan directory sweep failed")

    logger.info(
        "cleanup_stale_chunked_uploads: expired=%s purged_rows=%s orphan_dirs=%s freed_mb=%.1f",
        expired_count, purged_count, orphan_count, freed_bytes / (1024 * 1024),
    )
    return {
        "expired": expired_count,
        "purged_rows": purged_count,
        "orphan_dirs": orphan_count,
        "freed_mb": round(freed_bytes / (1024 * 1024), 1),
    }


def _chunked_upload_dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total

@shared_task(name="liveclass.transcribe_recording")
def transcribe_recording(session_id):
    """SCAFFOLD (Pass 7) — the queueing/state-machine half of captions is
    real; the actual speech-to-text call is a stub pending a provider
    decision (AWS Transcribe / Google Speech-to-Text / a hosted Whisper
    API all fit here equally — none of ClassSession.caption_status/
    caption_url care which one produced the result).

    Intended trigger: queue this via `.delay(session.id)` right after
    LiveKitWebhookView's egress_ended handling fills in recording_url
    (see views.py) — NOT wired up automatically in this pass, since
    firing it unconditionally would mean paying for transcription on
    every single recorded session whether or not captions were ever
    asked for. Wire the `.delay()` call in once a provider is chosen and
    a product decision is made on whether captions are opt-in
    (Classroom-level toggle, mirroring recording_enabled) or default-on.
    """
    from .models import ClassSession

    session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
    if session is None or not session.recording_url:
        logger.warning("transcribe_recording: session %s has no recording to transcribe.", session_id)
        return False

    session.caption_status = ClassSession.CaptionStatus.PROCESSING
    session.save(update_fields=["caption_status"])

    try:
        # STUB — replace with a real call, e.g.:
        #   caption_url = transcribe_provider.transcribe(session.recording_url)
        # Left unimplemented deliberately (see docstring) rather than
        # faked, so this task fails loudly/visibly instead of silently
        # "succeeding" with no real captions.
        raise NotImplementedError(
            "No speech-to-text provider wired in yet — see transcribe_recording's docstring."
        )
    except Exception:
        logger.exception("transcribe_recording failed for session %s", session_id)
        session.caption_status = ClassSession.CaptionStatus.FAILED
        session.save(update_fields=["caption_status"])
        return False