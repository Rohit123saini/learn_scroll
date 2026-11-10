# campus/tasks.py
"""
Celery tasks for `campus` (design doc §5/§6/§8/§13). Follows the
existing beat-schedule pattern this codebase already uses elsewhere —
periodic tasks are registered in the project's Celery beat schedule
(not here; this module only defines the task functions themselves).

`shared_task` degrades to a plain-function decorator if Celery isn't
installed in a given deployment/test environment, so importing this
module never hard-fails — callers that do `tasks.some_task.delay(...)`
already wrap that in a try/except and fall back to calling the task as
a plain synchronous function (see `AcademicSessionViewSet.rollover`),
which still works against the plain-function fallback below since a
bare function has no `.delay`, so the `except Exception` in that
try/except catches the `AttributeError` and calls it directly.

F-3 (this pass): `check_attendance_streak_rewards`/
`check_assigments_ontime_streak_rewards` import `user_profile.models.
CoinLedger` directly — NOT through a `bridge`-style indirection layer.
This is deliberate, not an inconsistency with the `campus` -> `core`/
`message` golden rule elsewhere in this app: `CoinLedger`'s own class
docstring explicitly designs `record_transaction()` as the shared,
cross-app write path every coin-changing action (in any app) should
call through — it is meant to be imported directly, unlike
`core.Notification`/`message` internals, which `campus` is deliberately
walled off from via `core.classroom_chat_bridge`. Register both new
tasks against the same daily beat schedule `check_low_attendance` and
`send_assigments_due_reminders` already use.
"""
import logging

try:
    from celery import shared_task
except ImportError:  # pragma: no cover - exercised only when Celery isn't installed
    def shared_task(*decorator_args, **decorator_kwargs):
        def _wrap(func):
            return func
        # Support both @shared_task and @shared_task(...) call styles.
        if len(decorator_args) == 1 and callable(decorator_args[0]) and not decorator_kwargs:
            return decorator_args[0]
        return _wrap

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import bridge
from .bridge import NotifTypes

logger = logging.getLogger(__name__)


@shared_task
def rollover_session(campus_id, new_session_id):
    """
    Carries forward ACTIVE enrollments from this campus's other
    sessions into `new_session_id` (design doc §13 — "school kabhi bhi
    naya session start karke seamlessly continue kar sakta hai").

    A `Section` belongs to one `AcademicSession` (via its
    `SchoolClass`), so this can't just re-point old enrollment rows —
    it matches each old enrollment to the new session's section with
    the SAME class name + section name (the obvious "same class,
    promoted" carry-forward) and creates a fresh enrollment there.
    Enrollments whose class/section name has no match in the new
    session (e.g. the student's class doesn't exist yet under the new
    session) are skipped and counted, not guessed at — matches the
    "flagged, not guessed at" posture the rest of this app takes on
    genuinely ambiguous cases.
    """
    from .models import AcademicSession, Section, StudentEnrollment

    new_session = AcademicSession.objects.filter(pk=new_session_id, campus_id=campus_id).first()
    if not new_session:
        return {"detail": "Session not found for this campus.", "carried_forward": 0, "skipped": 0}

    old_enrollments = StudentEnrollment.objects.filter(
        section__school_class__campus_id=campus_id,
        status=StudentEnrollment.Status.ACTIVE,
    ).exclude(session_id=new_session_id).select_related("section__school_class")

    carried_forward = 0
    skipped = 0
    with transaction.atomic():
        for enrollment in old_enrollments:
            target_section = Section.objects.filter(
                school_class__campus_id=campus_id,
                school_class__session_id=new_session_id,
                school_class__name=enrollment.section.school_class.name,
                name=enrollment.section.name,
            ).first()
            if not target_section:
                skipped += 1
                continue
            _, created = StudentEnrollment.objects.get_or_create(
                student=enrollment.student,
                section=target_section,
                session=new_session,
                defaults={"roll_number": enrollment.roll_number, "status": StudentEnrollment.Status.ACTIVE},
            )
            carried_forward += int(created)

    return {"carried_forward": carried_forward, "skipped": skipped}


@shared_task
def check_low_attendance():
    """
    `campus-daily-attendance-check` (design doc §5) — for every ACTIVE
    enrollment, recompute the overall attendance %-age and fire
    `LOW_ATTENDANCE_ALERT` (to the student + any linked parent) if it's
    below that campus's `attendance_alert_threshold_percent`. Meant to
    be registered against a daily beat schedule, not called per-request.
    """
    from .models import Campus, CampusParentLink, StudentEnrollment
    from .services import compute_attendance_summary

    alerted = 0
    for campus in Campus.objects.filter(is_active=True):
        enrollments = StudentEnrollment.objects.filter(
            section__school_class__campus=campus, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student")
        for enrollment in enrollments:
            summary = compute_attendance_summary(enrollment)
            if summary["total"] == 0:
                continue
            if summary["percent"] < campus.attendance_alert_threshold_percent:
                recipients = [enrollment.student] + list(
                    CampusParentLink.objects.filter(student=enrollment.student, campus=campus).values_list(
                        "parent", flat=True
                    )
                )
                bridge.notify(
                    users=recipients,
                    notif_type=NotifTypes.LOW_ATTENDANCE_ALERT,
                    title="Low attendance alert",
                    body=f"Attendance is at {summary['percent']}%, below the {campus.attendance_alert_threshold_percent}% threshold.",
                )
                alerted += 1
    return {"alerted": alerted}


@shared_task
def check_attendance_streak_rewards():
    """
    F-3 — daily engagement bonus for attendance streaks. Sibling to
    `check_low_attendance` above (same per-campus/per-enrollment loop
    shape) but rewards a good pattern instead of flagging a bad one.
    For every ACTIVE enrollment, recomputes the current streak
    (`services.compute_attendance_streak`) and pays a `CoinLedger`
    bonus every time it crosses a fresh multiple of
    `settings.CAMPUS_ATTENDANCE_STREAK_DAYS` (default 7 — i.e. day 7,
    14, 21, ... of an unbroken run of PRESENT/LATE daily marks).
    `settings.CAMPUS_ATTENDANCE_STREAK_BONUS_COINS` (default 10) sets
    the payout — same settings-constant shape as `tuitionclass`'s
    `REFERRAL_BONUS_COINS`/`CLASSROOM_REFERRAL_JOIN_BONUS_COINS`, so
    ops can retune either number without a deploy.

    Idempotency — deliberately no new model/column to track "already
    rewarded": the reward's `reference=` is built from
    `(enrollment id, streak length, the date the streak reached that
    length)`. A genuine streak can only reach a given length on one
    specific date ever (dates don't repeat), so this reference is
    naturally unique per real milestone — `CoinLedger.objects.
    record_transaction()`'s own reference check (inside its row lock)
    is the actual double-credit guard. The `.filter(...).exists()`
    pre-check below is only there to skip the `bridge.notify()` call on
    a re-run within the same lock-free window; on the (rare) chance two
    workers race past that pre-check for the very same milestone, the
    ledger itself still can't double-credit — worst case is one
    duplicate notification, not a duplicate coin grant. If the streak
    later breaks and a new one eventually reaches the same length again,
    that's a different `last_date`, so it's correctly treated as a new,
    separately-earned milestone rather than blocked forever.

    Safe to run more than once a day for the same reason
    `send_assigments_due_reminders` is: no state of its own, only reads
    `Attendance` rows.
    """
    from user_profile.models import CoinLedger

    from .models import Campus, CampusParentLink, StudentEnrollment
    from .services import compute_attendance_streak

    streak_days = settings.CAMPUS_ATTENDANCE_STREAK_DAYS
    bonus_coins = settings.CAMPUS_ATTENDANCE_STREAK_BONUS_COINS

    rewarded = 0
    for campus in Campus.objects.filter(is_active=True):
        enrollments = StudentEnrollment.objects.filter(
            section__school_class__campus=campus, status=StudentEnrollment.Status.ACTIVE
        ).select_related("student")
        for enrollment in enrollments:
            streak, last_date = compute_attendance_streak(enrollment)
            if streak == 0 or streak % streak_days != 0:
                continue
            reference = f"campus_attendance_streak:{enrollment.id}:{streak}:{last_date.isoformat()}"
            if CoinLedger.objects.filter(user=enrollment.student, reference=reference).exists():
                continue

            # ⚠️ NOT FIXED THIS PASS — Task 14 checklist asks for a
            # Task 5 fraud-guard call here before crediting the reward.
            # Task 5's fraud-guard function (name, module, signature —
            # whether it's a check-and-raise, a check-and-return-bool,
            # or something CoinLedger.record_transaction already applies
            # internally for transaction_type=CAMPUS_REWARD) was not
            # part of this pass's upload, and guessing a call here risks
            # inventing a function that doesn't exist — exactly the
            # ImportError/AttributeError class of bug this task exists
            # to remove. Please share the Task 5 fraud-guard file (or
            # just its function signature) to close this.
            CoinLedger.objects.record_transaction(
                user=enrollment.student,
                transaction_type=CoinLedger.TransactionType.CAMPUS_REWARD,
                amount=bonus_coins,
                reference=reference,
                description=f"{streak}-day attendance streak bonus",
            )
            recipients = [enrollment.student] + list(
                CampusParentLink.objects.filter(student=enrollment.student, campus=campus).values_list(
                    "parent", flat=True
                )
            )
            bridge.notify(
                users=recipients,
                notif_type=NotifTypes.CAMPUS_REWARD_EARNED,
                title="Attendance streak bonus!",
                body=f"{enrollment.student.username} earned {bonus_coins} coins for a {streak}-day attendance streak.",
            )
            rewarded += 1
    return {"rewarded": rewarded}


@shared_task
def send_assigments_due_reminders():
    """
    `assigments_DUE_REMINDER` (design doc §6) — for every campus
    `assigments` whose `due_date` is today, notify every student whose
    submission is still `MISSING`.

    [FIX — Task 11] QUERY REDIRECT: `campus.assigments`/
    `campus.assigmentsSubmission` are deprecated (see `campus.assigments`'s
    own docstring in models.py) — this now reads the unified
    `assigments.models.assigments`/`assigmentsSubmission` instead,
    filtered to `source=assigmentsSource.CAMPUS`. `context_id` is an
    opaque `Section.id` on that model (never a real FK — golden rule,
    §1), so `assigments.title`/`due_date` come straight off the unified
    row; nothing here needs to resolve `context_id` back into a real
    `Section` at all, since the notification body only needs the
    assigments's own title/due_date, not anything section-specific.

    ⚠️ NOT RESOLVED — DUPLICATE-NOTIFICATION RISK, flagged rather than
    silently guessed around: `assigments.tasks.send_due_reminders()`
    (Task 10) already sweeps EVERY `assigmentsSubmission` with no
    `source` filter at all — i.e. it already covers campus-sourced
    submissions too. If both that task and this one are wired into the
    project's Celery beat schedule, a campus student due today gets TWO
    separate reminder notifications (one from each task, each creating
    its own `core.Notification` row via a different call path —
    `assigments.tasks` calls `core.services.create_notification`
    directly, this one goes through `campus.bridge.notify()`). This
    wasn't something either task's own pass could resolve alone: it's a
    genuine cross-app scheduling decision (which of the two — or neither
    — actually gets registered in beat for campus-sourced assigmentss)
    that needs an explicit answer from whoever owns the beat schedule,
    not a guess baked into either task. Options, none picked here:
    (a) exclude `source=campus` rows from `assigments.tasks.
    send_due_reminders()`'s own query, (b) never schedule THIS task and
    accept the generic assigments-app reminder for campus students too,
    or (c) schedule both but only for disjoint day-of-week/hour windows
    (fragile, not recommended). campus_app_design.md §5.4 (referenced in
    this module's own docstring above) is the design doc that should
    settle this.

    Idempotency: this task's OWN re-runs are still safe more than once a
    day for the reason the module docstring gives (no state of its own,
    reads `assigmentsSubmission.status` fresh each time) — a re-run just
    re-notifies students still missing, never double-notifies ones who've
    since submitted. This does NOT extend across the two different tasks
    described above; that's the unresolved risk flagged there, not this
    one.
    """
    from assignments.models import assigments, assigmentsSource, assigmentsSubmission

    today = timezone.now().date()
    reminded = 0
    campus_assigmentss = assigments.objects.filter(source=assigmentsSource.CAMPUS, due_date=today)
    for campus_item in campus_assigmentss:
        missing_students = [
            sub.student
            for sub in assigmentsSubmission.objects.filter(
                assigments=campus_item, status=assigmentsSubmission.SubmissionStatus.MISSING
            ).select_related("student")
        ]
        if not missing_students:
            continue
        # push=True: "due today and you haven't submitted" must reach the phone.
        # This task owns CAMPUS-sourced assignments; the platform-wide sweep
        # (assignments.tasks) now skips source=campus, so nobody gets two pushes.
        bridge.notify(
            users=missing_students,
            notif_type=NotifTypes.assigments_DUE_REMINDER,
            title="assigments due today",
            body=f"{campus_item.title} is due today and you haven't submitted yet.",
            data={
                "type": NotifTypes.assigments_DUE_REMINDER,
                "context_type": campus_item.context_type,
                "context_id": str(campus_item.context_id or ""),
            },
            push=True,
        )
        reminded += len(missing_students)
    return {"reminded": reminded}


@shared_task
def check_assigments_ontime_streak_rewards():
    """
    F-3 — daily engagement bonus for on-time assigments submissions.
    For every ACTIVE enrollment, recomputes the student's current
    on-time streak within their section
    (`services.compute_assigments_ontime_streak`) and pays a
    `CoinLedger` bonus every time it crosses a fresh multiple of
    `settings.CAMPUS_assigments_STREAK_COUNT` (default 5 — i.e. the
    5th, 10th, 15th, ... consecutive on-time submission).
    `settings.CAMPUS_assigments_STREAK_BONUS_COINS` (default 15) sets
    the payout.

    Idempotency/race-condition posture: identical to
    `check_attendance_streak_rewards` above — see that task's
    docstring. The reference here is keyed on `(enrollment id, streak
    length, the due_date the streak reached that length)` instead of an
    attendance date, for the same "can only happen once, ever" reason.

    ✅ RESOLVED (Task 14) — `services.compute_assigments_ontime_streak()`
    now exists (it did not before this pass — that was the `ImportError`
    both streak tasks hit on every run) and reads through `campus.
    bridge.get_assigments_submissions()`, the same unified-`assigments`-
    app redirect `send_assigments_due_reminders()` above already applies
    — never the deprecated `campus.assigmentsSubmission` model. See that
    function's own docstring in services.py for the two points it flags
    as assumed-not-confirmed (the on-time status string, and
    `get_assigments_submissions()`'s return-type), which weren't
    resolvable without `assigments/models.py`.

    ✅ RESOLVED (this pass) — the `due_date=None` crash flagged in
    `services.compute_assigments_ontime_streak()`'s own docstring
    (design doc §16 item 5) is fixed: that function now also returns
    `last_submission_id`, the streak-extending submission's own `id`,
    which is always set whenever `streak > 0` regardless of whether
    `last_due_date` is. The reference below uses `last_due_date` when
    it's set and falls back to `last_submission_id` when it isn't,
    instead of calling `.isoformat()` on a possibly-`None` value —
    see that function's docstring for why the fallback is just as safe
    an idempotency key. This does not decide whether a due-date-less
    campus assigments *should* count towards the streak at all — that
    product-rule question is still open and unrelated to this fix.
    """
    from user_profile.models import CoinLedger

    from .models import CampusParentLink, StudentEnrollment
    from .services import compute_assigments_ontime_streak

    streak_count = settings.CAMPUS_assigments_STREAK_COUNT
    bonus_coins = settings.CAMPUS_assigments_STREAK_BONUS_COINS

    rewarded = 0
    enrollments = StudentEnrollment.objects.filter(
        status=StudentEnrollment.Status.ACTIVE, section__school_class__campus__is_active=True
    ).select_related("student", "section__school_class__campus")

    for enrollment in enrollments:
        streak, last_due_date, last_submission_id = compute_assigments_ontime_streak(
            enrollment.student, enrollment.section
        )
        if streak == 0 or streak % streak_count != 0:
            continue
        reference_key = last_due_date.isoformat() if last_due_date is not None else f"sub{last_submission_id}"
        reference = f"campus_assigments_streak:{enrollment.id}:{streak}:{reference_key}"
        if CoinLedger.objects.filter(user=enrollment.student, reference=reference).exists():
            continue

        # ⚠️ NOT FIXED THIS PASS — same Task 5 fraud-guard gap flagged
        # in check_attendance_streak_rewards() above; see that call's
        # comment for why it isn't guessed at here either.
        CoinLedger.objects.record_transaction(
            user=enrollment.student,
            transaction_type=CoinLedger.TransactionType.CAMPUS_REWARD,
            amount=bonus_coins,
            reference=reference,
            description=f"{streak}-submission on-time assigments streak bonus",
        )
        campus = enrollment.section.school_class.campus
        recipients = [enrollment.student] + list(
            CampusParentLink.objects.filter(student=enrollment.student, campus=campus).values_list(
                "parent", flat=True
            )
        )
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.CAMPUS_REWARD_EARNED,
            title="On-time streak bonus!",
            body=f"{enrollment.student.username} earned {bonus_coins} coins for {streak} on-time assigmentss in a row.",
        )
        rewarded += 1
    return {"rewarded": rewarded}


@shared_task
def accrue_late_fines():
    """FINANCE — daily (07:30, i.e. BEFORE the 08:00 reminders so they quote the fresh balance):
    re-derive the late fine of every overdue unpaid invoice whose FeeStructure has a fine policy.
    Idempotent — the fine is a pure function of (policy, due date, payments, today); running it
    twice the same day changes nothing."""
    from . import finance

    changed = finance.refresh_open_invoices_for_campus()
    logger.info("campus.accrue_late_fines: %s invoice(s) updated", changed)
    return changed


@shared_task
def publish_due_notices():
    """COMMUNICATION — every minute: publish scheduled notices whose `scheduled_for` has passed
    (push + bell rows to their audience). Each notice is claimed with one conditional UPDATE, so
    running this twice, or racing a manual "send now", never delivers a notice twice."""
    from . import notices

    published = notices.publish_due()
    if published:
        logger.info("campus.publish_due_notices: %s notice(s) published", published)
    return published


@shared_task
def send_fee_due_reminders():
    """
    FEE-6: `FEE_DUE_REMINDER` / `FEE_OVERDUE_REMINDER` — for every
    `FeeInvoice` whose status is still PENDING, PARTIAL, or OVERDUE,
    notify the student + any linked parents once its `FeeStructure.
    due_date` is within `settings.CAMPUS_FEE_REMINDER_DAYS_BEFORE` days
    away, due today, or already past. Same shape as
    `send_assigments_due_reminders` above and the same re-run safety
    reasoning: this task carries no state of its own, it only reads
    `FeeInvoice.status`/`FeeStructure.due_date`, so running it more than
    once a day just re-notifies invoices that are still unpaid, never
    double-notifies ones that have since been settled — `FeeInvoice.
    status` only ever leaves this filter once `recompute_status()`
    (models.py) has actually marked it PAID or WAIVED off the back of a
    real FeePayment.

    [ADDED — feature: Fee Reminder Notifications] Three distinct copy/
    type branches instead of one generic message, based on
    `due_date - today`:
      - `due_date > today` (within the reminder window): "Fee due in N
        day(s)" — `FEE_DUE_REMINDER`. Only fires once the invoice is
        WITHIN the window (`days_until <=
        settings.CAMPUS_FEE_REMINDER_DAYS_BEFORE`), not from the moment
        the invoice is created — a fee due in 45 days doesn't need a
        reminder yet. Because the task carries no per-invoice "already
        reminded today" flag, it re-fires once a day for every day left
        in that window (e.g. day 3, day 2, day 1 out of a 3-day
        window) rather than exactly once — same "harmless re-run,
        never a double-notify within one run" posture the module
        docstring already commits to for this whole file; a single
        stronger "notified once per invoice" guarantee would need a new
        column (e.g. `FeeInvoice.last_reminded_at`), which is a real
        schema change, out of scope for this pass.
      - `due_date == today`: "Fee due today" — still `FEE_DUE_REMINDER`
        (same type as the advance warning; only the copy is more
        urgent), since a client distinguishing "due in 3 days" from
        "due today" only needs the message text, not a third type.
      - `due_date < today`: "Fee overdue" — `FEE_OVERDUE_REMINDER`, the
        new, separate type (see `NotifTypes.FEE_OVERDUE_REMINDER`'s own
        comment in bridge.py) so a client can style/badge an overdue
        fee differently from a same-day or advance reminder.

    FEE-3/FEE-6: since fee is now paid from the coin wallet (FEE-2),
    also flags a wallet-balance hint in the reminder body when the
    STUDENT's own `User.coin` balance is short of what's still owed —
    this only checks the student's balance, never a linked parent's
    (a parent may intend to pay from their own wallet; we don't guess
    which wallet will actually be used, only surface the student's own
    shortfall since that's the one this task can name without
    ambiguity).
    """
    from .models import CampusParentLink, FeeInvoice

    today = timezone.now().date()
    window_days = getattr(settings, "CAMPUS_FEE_REMINDER_DAYS_BEFORE", 3)
    reminded = 0
    due_invoices = (
        FeeInvoice.objects.filter(
            status__in=[FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE],
            fee_structure__due_date__lte=today + timezone.timedelta(days=window_days),
        )
        .select_related("enrollment__student", "enrollment__section__school_class", "fee_structure")
    )

    from django.core.cache import cache

    # P-F14: how often an OVERDUE invoice is re-reminded (days). Default 1 = daily, as before.
    overdue_every = max(1, int(getattr(settings, "CAMPUS_FEE_OVERDUE_REMIND_EVERY_DAYS", 1) or 1))

    for invoice in due_invoices:
        student = invoice.enrollment.student
        campus_id = invoice.enrollment.section.school_class.campus_id
        parent_ids = list(
            CampusParentLink.objects.filter(student=student, campus_id=campus_id).values_list("parent", flat=True)
        )
        recipients = [student] + parent_ids
        remaining = invoice.amount_due - invoice.amount_paid
        days_until = (invoice.fee_structure.due_date - today).days

        # P-F14: at most ONE reminder per invoice per day (per `overdue_every` days once overdue), even if the
        # task is re-run / double-scheduled. Atomic `cache.add`; a cache outage just falls back to the old behaviour.
        bucket = today.toordinal() // overdue_every if days_until < 0 else today.toordinal()
        try:
            if not cache.add(f"campus:fee-reminder:{invoice.pk}:{bucket}", 1, timeout=(overdue_every + 1) * 86400):
                continue
        except Exception:
            logger.exception("fee reminder marker failed (invoice=%s); sending anyway.", invoice.pk)

        if days_until > 0:
            notif_type = NotifTypes.FEE_DUE_REMINDER
            title = "Fee due soon"
            day_word = "day" if days_until == 1 else "days"
            body = f"{invoice.fee_structure.title} — {remaining} due in {days_until} {day_word}."
        elif days_until == 0:
            notif_type = NotifTypes.FEE_DUE_REMINDER
            title = "Fee due today"
            body = f"{invoice.fee_structure.title} — {remaining} is due today."
        else:
            notif_type = NotifTypes.FEE_OVERDUE_REMINDER
            title = "Fee overdue"
            body = f"{invoice.fee_structure.title} — {remaining} is overdue."

        from . import fee_gateway  # local: avoids an import cycle at load time

        if fee_gateway.online_enabled():
            # UPI / card is available (FEE-5) — a coin shortfall is no longer the blocker.
            body += " You can pay online (UPI / card) in the app."
        else:
            shortfall = int(remaining) - student.coin
            if shortfall > 0:
                body += f" Your wallet balance ({student.coin} coins) is short by {shortfall} coins."
        # push=True: a fee reminder that only lands in the bell is not a reminder.
        # Honours NotificationPreference (push toggle / muted types / quiet hours).
        data = {
            "type": notif_type,
            "student_id": str(student.pk),
            "campus_id": str(campus_id),
        }
        if parent_ids:
            # P-F14: a parent must not read "Your wallet balance (N coins) is short" (that is the STUDENT's wallet)
            # and needs to know WHICH child the fee is for -> separate, parent-facing copy.
            child = (student.get_full_name() or "").strip() or student.username
            parent_title = f"{title}: {child}"
            parent_body = f"{invoice.fee_structure.title} — {remaining} " + (
                f"due in {days_until} {'day' if days_until == 1 else 'days'}." if days_until > 0
                else "is due today." if days_until == 0 else "is overdue."
            )
            if fee_gateway.online_enabled():
                parent_body += " You can pay online (UPI / card) in the app."
            bridge.notify(
                users=parent_ids, notif_type=notif_type, title=parent_title, body=parent_body, data=data, push=True,
            )
        bridge.notify(users=[student], notif_type=notif_type, title=title, body=body, data=data, push=True)
        reminded += 1
    return {"reminded": reminded}


@shared_task
def send_parent_weekly_summaries():
    """
    P-F14 — PARENT ENGAGEMENT: once a week (Sunday evening) every parent with a linked child gets "how was the
    week" — attendance of the last 7 days, marks trend, fees due — as a bell row + push + e-mail. The same numbers
    (plus a WhatsApp-ready text) are in the app at `GET /campus/parent-weekly/`.

    Logic lives in `parent_weekly_push.send_all` (one send per parent per ISO week, so a re-run is harmless; a
    parent with nothing to report gets nothing). Kill switch: `settings.CAMPUS_PARENT_WEEKLY_ENABLED` (default True).
    """
    if not getattr(settings, "CAMPUS_PARENT_WEEKLY_ENABLED", True):
        return {"disabled": True}
    from .parent_weekly_push import send_all

    return send_all()


@shared_task
def refresh_analytics_snapshot(campus_id, session_id):
    """
    `campus-refresh-analytics-snapshot` (design doc §8) — pre-computes
    the JSON blob `CampusAnalyticsSnapshotViewSet` serves, so an admin/
    HOD dashboard never has to aggregate on request. Deliberately
    minimal (attendance-trend/subject-avg/teacher-workload/syllabus-%
    are all listed in the design doc as candidate fields; this pass
    fills the ones cheaply derivable from existing service functions
    and leaves the rest as a follow-up rather than guessing at a shape
    no dashboard has asked for yet).
    """
    from django.db.models import Avg

    from .models import CampusAnalyticsSnapshot, ResultEntry, StudentEnrollment, SyllabusProgress
    from .services import compute_attendance_summary

    enrollments = StudentEnrollment.objects.filter(
        section__school_class__campus_id=campus_id, session_id=session_id, status=StudentEnrollment.Status.ACTIVE
    )
    percentages = [compute_attendance_summary(e)["percent"] for e in enrollments]
    avg_attendance = round(sum(percentages) / len(percentages), 2) if percentages else None

    avg_marks = ResultEntry.objects.filter(
        enrollment__session_id=session_id, status=ResultEntry.Status.PUBLISHED
    ).aggregate(avg=Avg("marks_obtained"))["avg"]

    total_units = SyllabusProgress.objects.filter(syllabus_unit__session_id=session_id).count()
    covered_units = SyllabusProgress.objects.filter(
        syllabus_unit__session_id=session_id, covered_on__isnull=False
    ).count()
    syllabus_completion_percent = round((covered_units / total_units) * 100, 2) if total_units else None

    data = {
        "avg_attendance_percent": avg_attendance,
        "avg_marks_obtained": float(avg_marks) if avg_marks is not None else None,
        "syllabus_completion_percent": syllabus_completion_percent,
        "active_enrollments": enrollments.count(),
    }
    return CampusAnalyticsSnapshot.objects.create(campus_id=campus_id, session_id=session_id, data=data).id

@shared_task
def send_fee_paid_notification(payment_id):
    """C3 — push \"fee payment received\" to the student + linked parents after an
    ONLINE payment settles (dispatched from fee_gateway.settle_online_payment).
    Idempotent per payment (see fee_paid_push)."""
    from .fee_paid_push import notify_fee_paid

    return notify_fee_paid(payment_id)


@shared_task
def send_fee_receipt_email(payment_id):
    """
    Task 5 (Fee Invoice PDF Receipt), subtask 3/4 — emails the PDF
    receipt to the paying student and, if one exists, a linked parent
    with an email on file. Fire-and-forget: dispatched via
    `core.async_utils.dispatch_after_commit` right after a `FeePayment`
    settles to SUCCESS (`FeePaymentViewSet.pay`/`.record`), the same
    "never blocks the request, never raises into the caller" contract
    `bridge.notify` already uses for push — this is that same shape for
    the one channel campus needs email for, kept local to this app
    rather than routed through `tuitionclass.notifications` (golden rule:
    campus never imports tuitionclass).

    Degrades to a logged warning, never an exception, in every
    unconfigured/missing-data case: no `reportlab` installed, no email
    on file for anyone involved, or the SMTP send itself failing —
    same "unconfigured channel is a no-op" contract
    `tuitionclass/notifications.py` documents for its own channels.
    """
    from django.core.mail import EmailMessage

    from .models import CampusParentLink, FeePayment
    from .receipt_pdf import PdfUnavailable, _display_name, render_pdf

    payment = (
        FeePayment.objects.select_related(
            "invoice__enrollment__student",
            "invoice__enrollment__section__school_class__campus",
            "invoice__fee_structure",
            "paid_by",
            "recorded_by",
        )
        .filter(pk=payment_id, status=FeePayment.Status.SUCCESS)
        .first()
    )
    if payment is None:
        logger.warning("send_fee_receipt_email: payment %s not found or not SUCCESS; skipping.", payment_id)
        return

    invoice = payment.invoice
    student = invoice.enrollment.student
    campus = invoice.enrollment.section.school_class.campus

    recipients = {student.email} if getattr(student, "email", None) else set()
    parent_emails = CampusParentLink.objects.filter(campus=campus, student=student).values_list(
        "parent__email", flat=True
    )
    recipients.update(email for email in parent_emails if email)

    # N8 - transactional (no opt-out), but never re-mail an address the provider reported dead (bounce / complaint).
    from core.channel_policy import deliverable_emails

    recipients = set(deliverable_emails(sorted(recipients)))

    if not recipients:
        logger.info("send_fee_receipt_email: no deliverable email for anyone tied to payment %s; skipping.", payment_id)
        return

    try:
        pdf_bytes = render_pdf(
            receipt_no=f"RCPT-{payment.id}",
            campus_name=campus.name,
            campus_type=campus.type,
            student_name=_display_name(student),
            fee_title=invoice.fee_structure.title,
            invoice_id=str(invoice.id),
            amount=payment.amount,
            amount_due=invoice.amount_due,
            amount_paid_total=invoice.amount_paid,
            payment_mode=payment.payment_mode,
            payer_role=payment.payer_role,
            paid_by=payment.paid_by,
            recorded_by=payment.recorded_by,
            status=payment.status,
            notes=payment.notes,
            paid_at=payment.paid_at or payment.created_at,
            transaction_id=payment.gateway_payment_id,
            gateway_method=payment.gateway_method,
        )
    except PdfUnavailable as exc:
        logger.warning("send_fee_receipt_email: %s (payment %s); skipping email.", exc, payment_id)
        return

    try:
        email = EmailMessage(
            subject=f"Fee receipt - {invoice.fee_structure.title} ({campus.name})",
            body=(
                f"Dear {_display_name(student)},\n\n"
                f"We've received a payment of Rs. {payment.amount:.2f} towards "
                f"'{invoice.fee_structure.title}' at {campus.name}.\n\n"
                f"The receipt is attached as a PDF for your records.\n\n"
                f"— {campus.name}"
            ),
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None) or None,
            to=list(recipients),
        )
        email.attach(f"fee-receipt-{payment.id}.pdf", pdf_bytes, "application/pdf")
        email.send(fail_silently=False)
    except Exception:
        logger.exception("send_fee_receipt_email: failed to send email for payment %s.", payment_id)


# ---------------------------------------------------------------------------
# T4 §G — "class starts in 5 minutes" reminder (online classes only)
# ---------------------------------------------------------------------------

def _class_reminder_recipients(section_id, session_id, teacher_user_id):
    from .class_schedule import active_student_ids_for_section

    recipients = set(active_student_ids_for_section(section_id, session_id))
    if teacher_user_id:
        recipients.add(teacher_user_id)
    return list(recipients)


def _minutes_left(starts_at, now):
    return max(1, int(round((starts_at - now).total_seconds() / 60.0)))


def _send_class_starting(*, recipients, subject_name, section_label, starts_at, now, data):
    local_start = timezone.localtime(starts_at)
    minutes = _minutes_left(starts_at, now)
    bridge.notify(
        users=recipients,
        notif_type=NotifTypes.CAMPUS_CLASS_STARTING,
        title=f"{subject_name} class starts in {minutes} min",
        body=f"{subject_name} ({section_label}) starts at {local_start:%I:%M %p}.",
        data=data,
        # T2.9: this reminder is useless as a silent bell row — push it
        # (preferences / quiet hours are honoured by the push layer).
        push=True,
    )


@shared_task
def send_class_start_reminders():
    """
    T4 §G — beat job, runs EVERY MINUTE. For each ONLINE class that starts
    within the next `CAMPUS_CLASS_REMINDER_LEAD_MINUTES` (default 5) it
    sends one `CAMPUS_CLASS_STARTING` notification to the section's active
    students and the teacher. OFFLINE classes never get one (decision D5).

    Two sources, same rule:
      * `CampusLiveSession` (mode=online, status=scheduled) — idempotent via
        `reminder_sent_at`, claimed with a conditional UPDATE so two
        overlapping workers can't both send.
      * weekly `TimetableEntry` (mode=online) — the slot's next occurrence
        is resolved to a real datetime in `settings.TIME_ZONE`; idempotent
        via the `TimetableReminderLog(entry, date)` unique key. A weekly
        slot that already has an explicit live session around the same time
        is skipped (it is the same class; the live-session reminder
        covers it).

    Cancelled/ended/live sessions are skipped, as are campuses with
    `class_reminders_enabled=False` or `is_active=False`. A session whose
    start already passed is never reminded (no "starts in 0 min" noise).
    """
    from .class_schedule import due_live_sessions, due_timetable_occurrences, has_explicit_live_session, reminder_lead
    from .models import CampusLiveSession, TimetableReminderLog
    from .substitutes import effective_teacher_user_id

    now = timezone.now()
    window_end = now + reminder_lead()
    sent_live = sent_timetable = 0

    for live in due_live_sessions(now, window_end):
        claimed = CampusLiveSession.objects.filter(pk=live.pk, reminder_sent_at__isnull=True).update(
            reminder_sent_at=now
        )
        if not claimed:
            continue
        recipients = _class_reminder_recipients(live.section_id, None, live.teacher.user_id)
        if not recipients:
            continue
        _send_class_starting(
            recipients=recipients,
            subject_name=live.subject.name,
            section_label=str(live.section),
            starts_at=live.scheduled_at,
            now=now,
            data={
                "type": NotifTypes.CAMPUS_CLASS_STARTING,
                "mode": "online",
                "live_session_id": str(live.id),
                "section_id": str(live.section_id),
                "subject_id": str(live.subject_id),
                "starts_at": live.scheduled_at.isoformat(),
            },
        )
        sent_live += 1

    for entry, starts_at in due_timetable_occurrences(now, window_end):
        if has_explicit_live_session(entry, starts_at):
            continue
        local_day = timezone.localtime(starts_at).date()
        with transaction.atomic():
            _, created = TimetableReminderLog.objects.get_or_create(entry=entry, occurrence_date=local_day)
        if not created:
            continue
        # Substitute teacher: remind whoever actually takes the period today —
        # the substitute if one is assigned, nobody on the teaching side if the
        # timetable's teacher is absent and uncovered (the students are still told).
        recipients = _class_reminder_recipients(
            entry.section_id, entry.session_id, effective_teacher_user_id(entry, local_day)
        )
        if not recipients:
            continue
        _send_class_starting(
            recipients=recipients,
            subject_name=entry.subject.name,
            section_label=str(entry.section),
            starts_at=starts_at,
            now=now,
            data={
                "type": NotifTypes.CAMPUS_CLASS_STARTING,
                "mode": "online",
                "timetable_entry_id": str(entry.id),
                "section_id": str(entry.section_id),
                "subject_id": str(entry.subject_id),
                "starts_at": starts_at.isoformat(),
            },
        )
        sent_timetable += 1

    return {"live_sessions": sent_live, "timetable": sent_timetable}


@shared_task
def prune_timetable_reminder_logs(keep_days=7):
    """Housekeeping for `TimetableReminderLog` (dedupe rows are only
    needed for the day they guard)."""
    from datetime import timedelta

    from .models import TimetableReminderLog

    cutoff = timezone.localdate() - timedelta(days=keep_days)
    deleted, _ = TimetableReminderLog.objects.filter(occurrence_date__lt=cutoff).delete()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Substitute teacher
# ---------------------------------------------------------------------------

@shared_task
def send_substitute_notifications(assignment_id, kind="assigned"):
    """Fan the substitute-teacher notifications out (see `campus.substitute_push`).

    `kind="assigned"`  -> the substitute + the section's students/parents.
    `kind="cancelled"` -> the former substitute only.

    Dispatched by the API after commit. Skips quietly when the row's state no
    longer matches the event (assigned-then-cancelled within the queue delay
    must not tell students about a cover that is already off)."""
    from .models import SubstituteAssignment
    from .substitute_push import notify_substitute_assigned, notify_substitute_cancelled

    assignment = (
        SubstituteAssignment.objects.filter(pk=assignment_id)
        .select_related(
            "substitute__user",
            "original_staff__user",
            "entry__subject",
            "entry__time_slot",
            "entry__section__school_class",
        )
        .first()
    )
    if assignment is None:
        return {"sent": 0}
    try:
        if kind == "assigned" and assignment.status == SubstituteAssignment.Status.ACTIVE:
            return {"sent": notify_substitute_assigned(assignment)}
        if kind == "cancelled" and assignment.status == SubstituteAssignment.Status.CANCELLED:
            return {"sent": notify_substitute_cancelled(assignment)}
    except Exception:
        logger.exception("send_substitute_notifications failed (assignment=%s kind=%s).", assignment_id, kind)
    return {"sent": 0}


# ---------------------------------------------------------------------------
# Staff leave
# ---------------------------------------------------------------------------

@shared_task
def send_staff_leave_notifications(leave_id, kind):
    """Fan a staff-leave notification out (see `campus.staff_leave_push`).

    `kind`: `requested` | `edited` | `approved` | `rejected` | `cancelled_by_admin` |
    `cancelled_by_staff` | `recorded_by_admin`. Dispatched by the API after commit. Skips quietly when
    the row's state no longer matches the event (e.g. a "requested" push for a
    leave that was already cancelled by the time the worker ran)."""
    from .models import StaffLeave
    from .staff_leave_push import notify_leave_decided, notify_leave_edited, notify_leave_requested

    leave = StaffLeave.objects.filter(pk=leave_id).select_related("staff__user", "campus").first()
    if leave is None:
        return {"sent": 0}
    expected = {
        "requested": StaffLeave.Status.PENDING,
        "edited": StaffLeave.Status.PENDING,
        "recorded_by_admin": StaffLeave.Status.APPROVED,
        "approved": StaffLeave.Status.APPROVED,
        "rejected": StaffLeave.Status.REJECTED,
        "cancelled_by_admin": StaffLeave.Status.CANCELLED,
        "cancelled_by_staff": StaffLeave.Status.CANCELLED,
    }.get(kind)
    if expected is None or leave.status != expected:
        return {"sent": 0}
    try:
        if kind == "requested":
            return {"sent": notify_leave_requested(leave)}
        if kind == "edited":
            return {"sent": notify_leave_edited(leave)}
        return {"sent": notify_leave_decided(leave, kind)}
    except Exception:
        logger.exception("send_staff_leave_notifications failed (leave=%s kind=%s).", leave_id, kind)
    return {"sent": 0}


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

@shared_task
def send_admission_notifications(application_id, kind):
    """Welcome push for a just-enrolled student (see `campus.admission_push`).

    `kind`: `enrolled`. Dispatched by the API after commit. Skips quietly when the
    row's state no longer matches the event."""
    from .admission_push import notify_enrolled
    from .models import AdmissionApplication

    app = (
        AdmissionApplication.objects.filter(pk=application_id)
        .select_related("student", "campus", "school_class", "section")
        .first()
    )
    if app is None or kind != "enrolled" or app.status != AdmissionApplication.Status.ENROLLED:
        return {"sent": 0}
    try:
        return {"sent": notify_enrolled(app)}
    except Exception:
        logger.exception("send_admission_notifications failed (application=%s kind=%s).", application_id, kind)
    return {"sent": 0}


@shared_task
def send_admission_followup_digest():
    """Daily: tell each campus's admins how many admission follow-ups are due (beat, 09:30)."""
    from .admission_push import notify_followup_digest

    try:
        return {"campuses": notify_followup_digest()}
    except Exception:
        logger.exception("send_admission_followup_digest failed.")
    return {"campuses": 0}


# ---------------------------------------------------------------------------
# Homework Diary
# ---------------------------------------------------------------------------

@shared_task
def send_homework_diary_posted(entry_id):
    """Fan the "new homework" push out to the section's students + parents.

    Dispatched by `HomeworkDiaryViewSet.perform_create` once the row is
    committed. Skips quietly if the entry was deleted/hidden in the meantime.
    """
    from .homework_diary_push import notify_homework_posted
    from .models import HomeworkDiaryEntry

    entry = (
        HomeworkDiaryEntry.objects.filter(pk=entry_id, is_active=True)
        .select_related("subject", "posted_by")
        .first()
    )
    if entry is None:
        return {"sent": 0}
    try:
        return {"sent": notify_homework_posted(entry)}
    except Exception:
        logger.exception("send_homework_diary_posted failed (entry=%s).", entry_id)
        return {"sent": 0}


@shared_task
def send_homework_diary_due_reminders():
    """"Homework due tomorrow" push (beat: daily, 17:00).

    Looks for active diary entries whose `due_date` is `today + N` days
    (`CAMPUS_HOMEWORK_DIARY_REMINDER_DAYS_BEFORE`, default 1) and that have not
    been reminded yet. Each entry is CLAIMED atomically
    (`due_reminder_sent_at`) before sending, so a re-run or two overlapping
    workers can never double-send; if the send itself blows up the claim is
    released so the next run retries it.
    """
    from datetime import timedelta

    from .homework_diary_push import notify_homework_due
    from .models import HomeworkDiaryEntry

    days_before = max(0, int(getattr(settings, "CAMPUS_HOMEWORK_DIARY_REMINDER_DAYS_BEFORE", 1)))
    target = timezone.localdate() + timedelta(days=days_before)
    entry_ids = list(
        HomeworkDiaryEntry.objects.filter(
            is_active=True, due_date=target, due_reminder_sent_at__isnull=True
        ).values_list("pk", flat=True)
    )
    reminded = 0
    for entry_id in entry_ids:
        claimed = HomeworkDiaryEntry.objects.filter(
            pk=entry_id, is_active=True, due_reminder_sent_at__isnull=True
        ).update(due_reminder_sent_at=timezone.now())
        if not claimed:
            continue  # another worker got it
        entry = HomeworkDiaryEntry.objects.select_related("subject", "posted_by").get(pk=entry_id)
        try:
            notify_homework_due(entry)
            reminded += 1
        except Exception:
            logger.exception("send_homework_diary_due_reminders failed (entry=%s).", entry_id)
            HomeworkDiaryEntry.objects.filter(pk=entry_id).update(due_reminder_sent_at=None)
    return {"reminded": reminded}


# ---------------------------------------------------------------------------
# Class resources (content share)
# ---------------------------------------------------------------------------

@shared_task
def send_class_resource_posted(resource_id):
    """Fan the "new resource" push out to the section's students + parents.

    Dispatched by `ClassResourceViewSet` once the row is committed (on create
    when published, and when a draft is published later). The push is CLAIMED
    atomically via `notified_at` first, so a retry / two workers / a repeated
    publish can never double-send. At-most-once on purpose: if the send itself
    blows up the claim is kept (a duplicate buzz to 60 families is worse than
    one missed push — the resource is in the app either way).

    Skips quietly for drafts / hidden / deleted resources, and stays silent
    (but still claims) when the same poster is bursting uploads into one
    folder — see `resource_push.is_burst`.
    """
    from .models import ClassResource
    from .resource_push import is_burst, notify_resource_posted

    resource = (
        ClassResource.objects.filter(
            pk=resource_id, is_active=True, is_published=True, folder__is_active=True, notified_at__isnull=True
        )
        .select_related("subject", "folder", "posted_by")
        .first()
    )
    if resource is None:
        return {"sent": 0}
    quiet = is_burst(resource)  # must run before our own claim is stamped
    claimed = ClassResource.objects.filter(pk=resource.pk, notified_at__isnull=True).update(
        notified_at=timezone.now()
    )
    if not claimed or quiet:
        return {"sent": 0, "skipped": "burst" if quiet else "claimed"}
    try:
        return {"sent": notify_resource_posted(resource)}
    except Exception:
        logger.exception("send_class_resource_posted failed (resource=%s).", resource_id)
        return {"sent": 0}


# ---------------------------------------------------------------------------
# Recorded classes + class notes push (campus/recording_push.py)
# ---------------------------------------------------------------------------

@shared_task
def send_recording_ready(live_id):
    """"Class recording is ready" push to the section's students + parents.

    Dispatched after commit by the LiveKit `egress_ended` handler
    (`recordings.finish_recording`) and when a teacher un-hides a recording that was
    never announced. CLAIMED atomically via `recording_notified_at` first, so a repeated
    webhook / two workers can never double-send (at-most-once, like the resource push).
    Skips quietly for a recording that is not READY or is hidden from students.
    """
    from .models import CampusLiveSession
    from .recording_push import notify_recording_ready

    live = (
        CampusLiveSession.objects.filter(
            pk=live_id,
            recording_status=CampusLiveSession.RecordingStatus.READY,
            recording_hidden=False,
            recording_notified_at__isnull=True,
        )
        .select_related("section__school_class", "subject", "teacher__user")
        .first()
    )
    if live is None:
        return {"sent": 0}
    claimed = CampusLiveSession.objects.filter(pk=live.pk, recording_notified_at__isnull=True).update(
        recording_notified_at=timezone.now()
    )
    if not claimed:
        return {"sent": 0, "skipped": "claimed"}
    try:
        return {"sent": notify_recording_ready(live)}
    except Exception:
        logger.exception("send_recording_ready failed (live session=%s).", live_id)
        return {"sent": 0}


@shared_task
def send_class_note_posted(note_id):
    """"New note" push to the section's students + parents.

    Dispatched after commit by `ClassNoteViewSet` (on create when published, and when a
    draft is published later). Claimed via `notified_at`; stays silent (but still
    claims) when the same poster is bursting notes into one subject.
    """
    from .models import ClassNote
    from .recording_push import is_note_burst, notify_note_posted

    note = (
        ClassNote.objects.filter(pk=note_id, is_active=True, is_published=True, notified_at__isnull=True)
        .select_related("subject", "posted_by")
        .first()
    )
    if note is None:
        return {"sent": 0}
    quiet = is_note_burst(note)  # must run before our own claim is stamped
    claimed = ClassNote.objects.filter(pk=note.pk, notified_at__isnull=True).update(notified_at=timezone.now())
    if not claimed or quiet:
        return {"sent": 0, "skipped": "burst" if quiet else "claimed"}
    try:
        return {"sent": notify_note_posted(note)}
    except Exception:
        logger.exception("send_class_note_posted failed (note=%s).", note_id)
        return {"sent": 0}


# ---------------------------------------------------------------------------
# Results publish push
# ---------------------------------------------------------------------------

@shared_task
def send_results_published(entry_ids):
    """One push per student for the just-published ResultEntry ids.

    Dispatched after commit by `ResultEntryViewSet` (publish / bulk / import).
    Draft or since-unpublished rows are skipped inside the notifier.
    """
    from .results_push import notify_results_published

    try:
        return {"students": notify_results_published(entry_ids)}
    except Exception:
        logger.exception("send_results_published failed.")
        return {"students": 0}


# ---------------------------------------------------------------------------
# Parents se baat — PTM scheduling + teacher<->parent chat
# (rules: campus.ptm / campus.parent_chat; notifications: campus.ptm_push /
#  campus.parent_chat_push)
# ---------------------------------------------------------------------------

_PTM_BOOKING_RELATED = ("event", "slot", "staff__user", "student", "parent")


@shared_task
def send_ptm_event_notifications(event_id, kind="published"):
    """Fan a PTM event announcement out to the invited parents (and, on cancel,
    the teachers who had opened slots).

    `kind="published"` / `"cancelled"`. Dispatched by the API after commit.
    Skips quietly when the event's state no longer matches (published-then-
    cancelled inside the queue delay must not announce a meeting that is off).
    """
    from .models import PTMEvent
    from .ptm_push import notify_event_cancelled, notify_event_published

    event = PTMEvent.objects.filter(pk=event_id).first()
    if event is None:
        return {"sent": 0}
    try:
        if kind == "published" and event.status in (PTMEvent.Status.PUBLISHED, PTMEvent.Status.CLOSED):
            return {"sent": notify_event_published(event)}
        if kind == "cancelled" and event.status == PTMEvent.Status.CANCELLED:
            return {"sent": notify_event_cancelled(event)}
    except Exception:
        logger.exception("send_ptm_event_notifications failed (event=%s kind=%s).", event_id, kind)
    return {"sent": 0}


@shared_task
def send_ptm_booking_notifications(booking_id, kind="booked", old_id=None):
    """One booking's notification.

    `booked`      -> the teacher.
    `rescheduled` -> the other side of whoever moved it (`booking_id` = the NEW
                     booking, `old_id` = the one it replaced).
    `cancelled`   -> the other side of whoever cancelled.
    `outcome`     -> the parent (only when the teacher wrote a summary for them).
    """
    from .models import PTMBooking
    from .ptm_push import (
        notify_booking_booked,
        notify_booking_cancelled,
        notify_booking_rescheduled,
        notify_outcome,
    )

    booking = PTMBooking.objects.filter(pk=booking_id).select_related(*_PTM_BOOKING_RELATED).first()
    if booking is None:
        return {"sent": 0}
    try:
        if kind == "booked" and booking.status == PTMBooking.Status.BOOKED:
            return {"sent": notify_booking_booked(booking)}
        if kind == "rescheduled" and booking.status == PTMBooking.Status.BOOKED and old_id:
            old = PTMBooking.objects.filter(pk=old_id).select_related(*_PTM_BOOKING_RELATED).first()
            if old is not None:
                return {"sent": notify_booking_rescheduled(booking, old)}
        if kind == "cancelled" and booking.status == PTMBooking.Status.CANCELLED:
            return {"sent": notify_booking_cancelled(booking)}
        if kind == "outcome" and booking.status in (PTMBooking.Status.COMPLETED, PTMBooking.Status.NO_SHOW):
            return {"sent": notify_outcome(booking)}
    except Exception:
        logger.exception("send_ptm_booking_notifications failed (booking=%s kind=%s).", booking_id, kind)
    return {"sent": 0}


@shared_task
def send_ptm_reminders():
    """"Your PTM is tomorrow / in an hour" push (beat: every 5 minutes).

    * 1 h reminder  - a BOOKED meeting that starts in the next 60 minutes.
    * 24 h reminder - a BOOKED meeting that starts within 24 h, but only when it
      was booked at least 24 h ahead (a parent who books at 9 pm for 8 am next
      day does not get a "tomorrow" reminder seconds later).
    Each reminder is CLAIMED atomically (`reminder_*_sent_at`) before sending, so
    overlapping workers / a re-run never double-send; a failed send releases
    the claim so the next tick retries.
    """
    from datetime import timedelta

    from . import ptm
    from .models import PTMBooking, PTMEvent
    from .ptm_push import notify_reminder

    now = timezone.now()
    today = timezone.localdate()
    candidates = (
        PTMBooking.objects.filter(
            status=PTMBooking.Status.BOOKED,
            event__status__in=(PTMEvent.Status.PUBLISHED, PTMEvent.Status.CLOSED),
            event__date__gte=today,
            event__date__lte=today + timedelta(days=1),
        )
        .select_related(*_PTM_BOOKING_RELATED)
    )
    sent = 0
    for booking in candidates:
        remaining = ptm.booking_start(booking) - now
        if remaining <= timedelta(0):
            continue
        for hours, field, due in (
            (1, "reminder_1h_sent_at", remaining <= timedelta(hours=1)),
            (
                24,
                "reminder_24h_sent_at",
                remaining <= timedelta(hours=24)
                and remaining > timedelta(hours=1)
                and booking.created_at <= ptm.booking_start(booking) - timedelta(hours=24),
            ),
        ):
            if not due or getattr(booking, field) is not None:
                continue
            claimed = PTMBooking.objects.filter(
                pk=booking.pk, status=PTMBooking.Status.BOOKED, **{f"{field}__isnull": True}
            ).update(**{field: now})
            if not claimed:
                continue
            try:
                notify_reminder(booking, hours)
                sent += 1
            except Exception:
                logger.exception("send_ptm_reminders failed (booking=%s hours=%s).", booking.pk, hours)
                PTMBooking.objects.filter(pk=booking.pk).update(**{field: None})
    return {"reminded": sent}


@shared_task
def send_parent_chat_notification(message_id):
    """Tell the other side of a thread about one new message (see
    `campus.parent_chat_push.notify_parent_message`). Dispatched after commit."""
    from .models import ParentChatMessage
    from .parent_chat_push import notify_parent_message

    message = (
        ParentChatMessage.objects.filter(pk=message_id)
        .select_related("thread__parent", "thread__teacher__user", "thread__student")
        .first()
    )
    if message is None:
        return {"sent": 0}
    try:
        return {"sent": notify_parent_message(message)}
    except Exception:
        logger.exception("send_parent_chat_notification failed (message=%s).", message_id)
        return {"sent": 0}


@shared_task
def send_parent_chat_flag_notification(thread_id):
    """A chat was reported -> tell the campus admin/principal (no message text)."""
    from .models import ParentChatThread
    from .parent_chat_push import notify_thread_flagged

    thread = ParentChatThread.objects.filter(pk=thread_id, flagged_at__isnull=False).first()
    if thread is None:
        return {"sent": 0}
    try:
        return {"sent": notify_thread_flagged(thread)}
    except Exception:
        logger.exception("send_parent_chat_flag_notification failed (thread=%s).", thread_id)
        return {"sent": 0}


# beat runs every 10 minutes (settings.CELERY_BEAT_SCHEDULE) - keep in sync
PARENT_CHAT_DIGEST_TICK_MINUTES = 10


@shared_task
def send_parent_chat_morning_digest():
    """ONE push per teacher for parent messages that arrived after hours (beat:
    every 10 minutes).

    Runs for a campus only on the tick in which its chat window OPENS (its
    `chat_start_time` falls inside the last tick), so a teacher is buzzed once
    a morning, not every 10 minutes. A campus whose window is "always open"
    (start == end) never has after-hours messages, so it is skipped.
    """
    from . import parent_comms as pc
    from .models import ParentTeacherSettings
    from .parent_chat_push import notify_morning_digest

    now = pc.local_now()
    now_min = now.hour * 60 + now.minute
    told = 0
    for s in ParentTeacherSettings.objects.filter(chat_enabled=True).only(
        "campus_id", "chat_start_time", "chat_end_time"
    ):
        if s.chat_start_time == s.chat_end_time:
            continue
        since_open = (now_min - (s.chat_start_time.hour * 60 + s.chat_start_time.minute)) % (24 * 60)
        if since_open >= PARENT_CHAT_DIGEST_TICK_MINUTES:
            continue
        try:
            told += notify_morning_digest(s.campus_id)
        except Exception:
            logger.exception("send_parent_chat_morning_digest failed (campus=%s).", s.campus_id)
    return {"teachers": told}


# ---------------------------------------------------------------------------
# Live class — auto-end silent sessions + attendance catch-up
# ---------------------------------------------------------------------------

@shared_task
def sweep_live_sessions():
    """
    Beat job, runs EVERY MINUTE (see CELERY_BEAT_SCHEDULE). Thin wrapper over
    `campus.live_class.sweep_live_sessions`:

      * a LIVE session whose teacher heartbeat went silent for
        CAMPUS_LIVE_STALE_MINUTES (5) is auto-ended at the last heartbeat;
      * any live session older than CAMPUS_LIVE_MAX_HOURS (6) is force-ended;
      * ended sessions whose automatic attendance never got built (a crash
        between "ended" and "built") are rebuilt — idempotent, never touches
        a row a teacher edited.
    """
    from .live_class import sweep_live_sessions as _sweep

    return _sweep()


@shared_task
def build_live_session_attendance(session_id):
    """Build (or refresh) the automatic attendance of one ended live session.
    Idempotent; kept as a task so it can be re-queued by hand or from the shell."""
    from .live_class import build_attendance

    return build_attendance(session_id)


# ---------------------------------------------------------------------------
# SAFETY — scheduled backups + overdue data-deletion warnings
# (see campus/backup.py and campus/data_deletion.py; beat entries in settings.py)
# ---------------------------------------------------------------------------
@shared_task
def run_scheduled_campus_backups():
    """
    Nightly: every campus with no READY scheduled backup in the last `CAMPUS_BACKUP_SCHEDULE_DAYS` (7) gets a
    setup + records backup, then old / surplus backups of that campus are pruned. One campus failing never stops
    the others (the failed attempt stays visible as a FAILED row).
    """
    from . import backup as backup_svc
    from . import safety_rules as safety_rules

    made = failed = removed = 0
    for campus in backup_svc.campuses_due_for_backup():
        try:
            backup_svc.create_backup(
                campus, groups=list(safety_rules.BACKUP_GROUPS), kind=safety_rules.KIND_SCHEDULED, note="Scheduled")
            made += 1
        except backup_svc.BackupError as exc:
            failed += 1
            logger.warning("scheduled campus backup failed (campus=%s): %s", campus.pk, exc.code)
        except Exception:  # pragma: no cover - never let one campus stop the loop
            failed += 1
            logger.exception("scheduled campus backup crashed (campus=%s)", campus.pk)
        try:
            removed += backup_svc.prune_backups(campus)
        except Exception:  # pragma: no cover
            logger.exception("campus backup prune failed (campus=%s)", campus.pk)
    return {"made": made, "failed": failed, "pruned": removed}


@shared_task
def warn_overdue_deletion_requests():
    """
    Daily: tell the campus admins about a data-deletion request that passed its due date. Sent on the first
    overdue day and then once a week, so it nags without spamming.
    """
    from . import data_deletion as deletion_svc

    now = timezone.now()
    sent = 0
    for req in deletion_svc.overdue_open_requests(now).select_related("requester"):
        days_late = (now - req.due_at).days
        if days_late % 7 != 0:
            continue
        try:
            deletion_svc.notify_overdue(req)
            sent += 1
        except Exception:  # pragma: no cover - a notification failure must not stop the loop
            logger.exception("overdue deletion warning failed (request=%s)", req.pk)
    return {"warned": sent}
