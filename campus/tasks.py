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
`check_assignment_ontime_streak_rewards` import `user_profile.models.
CoinLedger` directly — NOT through a `bridge`-style indirection layer.
This is deliberate, not an inconsistency with the `campus` -> `core`/
`message` golden rule elsewhere in this app: `CoinLedger`'s own class
docstring explicitly designs `record_transaction()` as the shared,
cross-app write path every coin-changing action (in any app) should
call through — it is meant to be imported directly, unlike
`core.Notification`/`message` internals, which `campus` is deliberately
walled off from via `core.classroom_chat_bridge`. Register both new
tasks against the same daily beat schedule `check_low_attendance` and
`send_assignment_due_reminders` already use.
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
    the payout — same settings-constant shape as `liveclass`'s
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
    `send_assignment_due_reminders` is: no state of its own, only reads
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
def send_assignment_due_reminders():
    """
    `ASSIGNMENT_DUE_REMINDER` (design doc §6) — for every campus
    `Assignment` whose `due_date` is today, notify every student whose
    submission is still `MISSING`.

    [FIX — Task 11] QUERY REDIRECT: `campus.Assignment`/
    `campus.AssignmentSubmission` are deprecated (see `campus.Assignment`'s
    own docstring in models.py) — this now reads the unified
    `assignment.models.Assignment`/`AssignmentSubmission` instead,
    filtered to `source=AssignmentSource.CAMPUS`. `context_id` is an
    opaque `Section.id` on that model (never a real FK — golden rule,
    §1), so `assignment.title`/`due_date` come straight off the unified
    row; nothing here needs to resolve `context_id` back into a real
    `Section` at all, since the notification body only needs the
    assignment's own title/due_date, not anything section-specific.

    ⚠️ NOT RESOLVED — DUPLICATE-NOTIFICATION RISK, flagged rather than
    silently guessed around: `assignment.tasks.send_due_reminders()`
    (Task 10) already sweeps EVERY `AssignmentSubmission` with no
    `source` filter at all — i.e. it already covers campus-sourced
    submissions too. If both that task and this one are wired into the
    project's Celery beat schedule, a campus student due today gets TWO
    separate reminder notifications (one from each task, each creating
    its own `core.Notification` row via a different call path —
    `assignment.tasks` calls `core.services.create_notification`
    directly, this one goes through `campus.bridge.notify()`). This
    wasn't something either task's own pass could resolve alone: it's a
    genuine cross-app scheduling decision (which of the two — or neither
    — actually gets registered in beat for campus-sourced assignments)
    that needs an explicit answer from whoever owns the beat schedule,
    not a guess baked into either task. Options, none picked here:
    (a) exclude `source=campus` rows from `assignment.tasks.
    send_due_reminders()`'s own query, (b) never schedule THIS task and
    accept the generic assignment-app reminder for campus students too,
    or (c) schedule both but only for disjoint day-of-week/hour windows
    (fragile, not recommended). campus_app_design.md §5.4 (referenced in
    this module's own docstring above) is the design doc that should
    settle this.

    Idempotency: this task's OWN re-runs are still safe more than once a
    day for the reason the module docstring gives (no state of its own,
    reads `AssignmentSubmission.status` fresh each time) — a re-run just
    re-notifies students still missing, never double-notifies ones who've
    since submitted. This does NOT extend across the two different tasks
    described above; that's the unresolved risk flagged there, not this
    one.
    """
    from assignment.models import Assignment, AssignmentSource, AssignmentSubmission

    today = timezone.now().date()
    reminded = 0
    campus_assignments = Assignment.objects.filter(source=AssignmentSource.CAMPUS, due_date=today)
    for assignment in campus_assignments:
        missing_students = [
            sub.student
            for sub in AssignmentSubmission.objects.filter(
                assignment=assignment, status=AssignmentSubmission.SubmissionStatus.MISSING
            ).select_related("student")
        ]
        if not missing_students:
            continue
        bridge.notify(
            users=missing_students,
            notif_type=NotifTypes.ASSIGNMENT_DUE_REMINDER,
            title="Assignment due today",
            body=f"{assignment.title} is due today and you haven't submitted yet.",
        )
        reminded += len(missing_students)
    return {"reminded": reminded}


@shared_task
def check_assignment_ontime_streak_rewards():
    """
    F-3 — daily engagement bonus for on-time assignment submissions.
    For every ACTIVE enrollment, recomputes the student's current
    on-time streak within their section
    (`services.compute_assignment_ontime_streak`) and pays a
    `CoinLedger` bonus every time it crosses a fresh multiple of
    `settings.CAMPUS_ASSIGNMENT_STREAK_COUNT` (default 5 — i.e. the
    5th, 10th, 15th, ... consecutive on-time submission).
    `settings.CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS` (default 15) sets
    the payout.

    Idempotency/race-condition posture: identical to
    `check_attendance_streak_rewards` above — see that task's
    docstring. The reference here is keyed on `(enrollment id, streak
    length, the due_date the streak reached that length)` instead of an
    attendance date, for the same "can only happen once, ever" reason.

    ✅ RESOLVED (Task 14) — `services.compute_assignment_ontime_streak()`
    now exists (it did not before this pass — that was the `ImportError`
    both streak tasks hit on every run) and reads through `campus.
    bridge.get_assignment_submissions()`, the same unified-`assignment`-
    app redirect `send_assignment_due_reminders()` above already applies
    — never the deprecated `campus.AssignmentSubmission` model. See that
    function's own docstring in services.py for the two points it flags
    as assumed-not-confirmed (the on-time status string, and
    `get_assignment_submissions()`'s return-type), which weren't
    resolvable without `assignment/models.py`.
    """
    from user_profile.models import CoinLedger

    from .models import CampusParentLink, StudentEnrollment
    from .services import compute_assignment_ontime_streak

    streak_count = settings.CAMPUS_ASSIGNMENT_STREAK_COUNT
    bonus_coins = settings.CAMPUS_ASSIGNMENT_STREAK_BONUS_COINS

    rewarded = 0
    enrollments = StudentEnrollment.objects.filter(
        status=StudentEnrollment.Status.ACTIVE, section__school_class__campus__is_active=True
    ).select_related("student", "section__school_class__campus")

    for enrollment in enrollments:
        streak, last_due_date = compute_assignment_ontime_streak(enrollment.student, enrollment.section)
        if streak == 0 or streak % streak_count != 0:
            continue
        reference = f"campus_assignment_streak:{enrollment.id}:{streak}:{last_due_date.isoformat()}"
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
            description=f"{streak}-submission on-time assignment streak bonus",
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
            body=f"{enrollment.student.username} earned {bonus_coins} coins for {streak} on-time assignments in a row.",
        )
        rewarded += 1
    return {"rewarded": rewarded}


@shared_task
def send_fee_due_reminders():
    """
    FEE-6: `FEE_DUE_REMINDER` — for every `FeeInvoice` whose status is
    still PENDING, PARTIAL, or OVERDUE and whose `FeeStructure.due_date`
    is today or already past, notify the student + any linked parents.
    Same shape as `send_assignment_due_reminders` above and the same
    re-run safety reasoning: this task carries no state of its own, it
    only reads `FeeInvoice.status`/`FeeStructure.due_date`, so running
    it more than once a day just re-notifies invoices that are still
    unpaid, never double-notifies ones that have since been settled —
    `FeeInvoice.status` only ever leaves this filter once
    `recompute_status()` (models.py) has actually marked it PAID or
    WAIVED off the back of a real FeePayment.

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
    reminded = 0
    due_invoices = (
        FeeInvoice.objects.filter(
            status__in=[FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE],
            fee_structure__due_date__lte=today,
        )
        .select_related("enrollment__student", "enrollment__section__school_class", "fee_structure")
    )

    for invoice in due_invoices:
        student = invoice.enrollment.student
        campus_id = invoice.enrollment.section.school_class.campus_id
        recipients = [student] + list(
            CampusParentLink.objects.filter(student=student, campus_id=campus_id).values_list("parent", flat=True)
        )
        remaining = invoice.amount_due - invoice.amount_paid
        body = f"{invoice.fee_structure.title} — {remaining} is still due."
        shortfall = int(remaining) - student.coin
        if shortfall > 0:
            body += f" Your wallet balance ({student.coin} coins) is short by {shortfall} coins."
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.FEE_DUE_REMINDER,
            title="Fee due",
            body=body,
        )
        reminded += 1
    return {"reminded": reminded}


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

    avg_marks = ResultEntry.objects.filter(enrollment__session_id=session_id).aggregate(avg=Avg("marks_obtained"))["avg"]

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