# campus/models.py
"""
`campus` app — Smart Virtual School/College/Institute OS.
Implements campus_app_design.md phases 1-13.

GOLDEN RULE (unchanged from design doc): `campus` never imports
`tuitionclass` or `message` models directly. The only cross-app model FK in
this whole file is to `login.User` (identity is shared platform-wide,
same as `core.models.Notification` already does). Anything that needs
`tuitionclass`/`message` internals (video room provisioning, group-chat
auto-creation, parent OTP issuance) goes through `core.classroom_chat_bridge`
service functions from a signals.py/services.py layer — NOT from here.
That also means: no signal handlers in this file that import core/message/
tuitionclass. Auto-notify-on-schedule, auto-reminder Celery tasks etc. from
the design doc are service-layer concerns, not model-layer ones.

FEE MODULE — why it looks the way it does:

REVISED (this pass — FEE-2): the previous version of this docstring
said fee was "decoupled end to end from `User.coin`/`CoinLedger`" and
paid through a real Razorpay gateway. Product decision changed that:
Campus fee is now paid FROM the same `user_profile.CoinLedger`-backed
`User.coin` wallet that `tuitionclass` already uses for classes/passes —
no separate payment gateway for fee. `FeePayment.Mode.ONLINE` +
`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` are gone from this flow
(those settings are untouched in settings.py because `tuitionclass`'s
`CoinPurchase` gateway still uses them for topping the wallet up in
the first place — fee just spends what's already in it). The actual
debit happens at the view layer (`FeePaymentViewSet.pay`, campus/
views.py) via `CoinLedger.objects.record_transaction()` — this file
does not import `user_profile` itself, only `FeePayment.refund()`
below documents the matching credit-back call the view layer makes.

IMPORTANT, NOT-YET-FULLY-RESOLVED UNIT MISMATCH (flagged, not
guessed at): `FeePayment.amount`/`FeeStructure.amount` are
`DecimalField(decimal_places=2)` (real currency, can hold paise), but
`user_profile.CoinLedger.amount` is a plain `IntegerField` and
`User.coin` is a whole-number balance — there is no fractional coin.
This pass's decision: fee amounts paid via wallet MUST be a whole
number (no paise) — enforced at the view layer (`FeePaymentViewSet.pay`
rejects a fractional amount with a 400 before ever calling
`record_transaction`), not silently rounded, since silently rounding
real money is worse than rejecting it. If schools genuinely need
paisa-level fee amounts, that needs a coin-side schema change
(e.g. store coins as `DecimalField` too) — out of scope for this pass,
flagged here the same way `RestrictUser`/§13.1/§13.2 were flagged
above rather than guessed at.

What's still reused from `CoinLedger`'s own design, now for real
instead of just as inspiration: a signed, append-only, self-auditing
money trail with an idempotency key so a retried payment can never
double-apply — `FeePayment` keeps its own `gateway_reference` as ITS
idempotency key (a client-retried `pay` call with the same reference
returns the same `FeePayment` row via `get_or_create`, see
`FeePaymentViewSet.pay`), and `CoinLedger.record_transaction`'s OWN
`reference` (set to `f"fee-payment-{payment.id}"`, derived from that
already-deduplicated row, never a fresh random value per call) gives a
second, independent idempotency guarantee at the wallet layer itself.

Two more requirements this pass still has to satisfy that CoinLedger's
shape doesn't need to (because coins used to only ever move through one
channel — the app itself — and fee introduces a second one):
  1. Fee can be paid by the student OR by a linked parent
     (`FeePayment.paid_by` + `payer_role`) — CoinLedger only ever has one
     `user`, campus fee doesn't. The wallet debited is always
     `FeePayment.paid_by`'s (whoever is actually paying), not
     necessarily the student who owes it.
  2. Fee has to work BOTH as self-serve (student/parent pays from their
     own coin wallet, no staff involved) AND as admin-maintained (office
     staff manually records a cash/cheque/bank-transfer payment at the
     counter, no wallet involved at all — a school that collects cash at
     the counter shouldn't have to route that through anyone's coin
     balance). `FeePayment.payment_mode` + `recorded_by` carry that
     distinction; `gateway_reference` is only ever populated for the
     wallet path now and is where the idempotency guarantee above lives.
  3. The whole module is optional per campus (`Campus.fee_module_enabled`)
     — a campus that never turns it on can use every other campus feature
     untouched, exactly as design doc §8 specifies ("purely optional,
     agar campus admin chahe to hi use kare"). Enforced now on every
     fee-writing entry point (`FeeStructureViewSet.perform_create` +
     `.generate_invoices`, `FeeInvoiceViewSet.perform_create`,
     `FeePaymentViewSet.pay`/`.record`), not just structure-creation.

Open design-doc questions NOT resolved by this pass (flagged, not
guessed at, same as RestrictUser was flagged in user_profile/models.py):
  - §13.1 (single-campus vs multi-campus student enrollment): left
    UNRESTRICTED here — StudentEnrollment has no cross-campus uniqueness
    constraint, only a per-section-per-session one. Easy to tighten later
    with a migration if the answer turns out to be "one campus only".
  - §13.2 (who can create a campus / verification gate): RESOLVED this
    pass (G-2) — see `Campus.VerificationStatus`. Campus creation itself
    is still free/self-serve (anyone can create one and is auto-made its
    ADMIN, per `CampusViewSet.perform_create`'s docstring), but the
    campus starts `PENDING` and a platform admin must `approve` it
    (`CampusViewSet.approve`/`.reject`, gated by
    `permissions.IsPlatformAdmin`) before anyone besides the creator can
    be pulled into it — see `permissions.is_campus_approved`'s docstring
    for exactly which endpoints that gates.
"""
import os
import secrets
import uuid
from datetime import time as dt_time, timedelta

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, URLValidator
from django.db import models, transaction
from django.db.models import CheckConstraint, Q, UniqueConstraint
from django.utils import timezone

from common.attachment_validators import attachment_extension_validator, validate_attachment_size
from login.models import User

from . import resource_rules
from .soft_delete import SoftDeleteError, SoftDeleteModel


class ClassMode(models.TextChoices):
    """
    T4 §G — how a class is delivered. `ONLINE` classes expose their
    time to students (and fire the "starts in 5 minutes" reminder);
    `OFFLINE` classes show students only an "Offline class" label, no
    time and no reminder — see `campus.class_schedule` for the single
    place that rule lives.
    """

    ONLINE = "online", "Online"
    OFFLINE = "offline", "Offline"


class CampusBaseModel(models.Model):
    """
    Shared abstract base for every model in this app — UUID primary
    keys, matching the platform-wide convention already used elsewhere
    in this codebase (sequential integer ids would let anyone enumerate
    campuses/students/results by incrementing a URL, which is not
    acceptable for a multi-tenant school-records API). `editable=False`
    keeps it out of ModelForms/serializer input; every FK below still
    points at these models normally — Django resolves the PK type from
    the target model automatically, no extra config needed at the FK
    end.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


# ---------------------------------------------------------------------------
# 1. Structural hierarchy
# ---------------------------------------------------------------------------

def default_weekly_off():
    """No weekly off until the campus admin sets one (ISO weekdays: Mon=1 .. Sun=7,
    e.g. `[7]` for Sunday). Empty keeps every existing campus's leave counting
    exactly as it was."""
    return []


class Campus(CampusBaseModel, SoftDeleteModel):
    class CampusType(models.TextChoices):
        SCHOOL = "school", "School"
        COLLEGE = "college", "College"
        COACHING = "coaching", "Coaching"

    class VerificationStatus(models.TextChoices):
        """
        G-2 fix — resolves module docstring's previously-open §13.2
        question. A campus is created `PENDING` (see `CampusViewSet.
        perform_create`) and stays that way until a platform admin
        (`permissions.is_platform_admin` — Django `is_staff`/
        `is_superuser`, not a campus-scoped role) calls the `approve`/
        `reject` action. `PENDING`/`REJECTED` campuses are NOT
        prevented from existing or from their creator setting up
        structure on them (see `CampusViewSet`'s docstring: creating a
        campus must always leave the creator able to manage it) — what
        `permissions.is_campus_approved` actually gates is OTHER people
        being pulled into it (staff invites, student enrollment, a
        parent verifying a link), so an unverified "campus" can't yet
        onboard anyone but its own creator.
        """

        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campuses_created")
    name = models.CharField(max_length=200)
    type = models.CharField(max_length=20, choices=CampusType.choices)

    # Attendance automation (§5) reads this — a % below this threshold
    # triggers LOW_ATTENDANCE_ALERT via the Celery periodic task.
    attendance_alert_threshold_percent = models.PositiveSmallIntegerField(default=75)

    # Attendance galti-sudhaar: a marked attendance row can be edited by its
    # teacher only for this many hours after it was FIRST marked
    # (`Attendance.marked_at`). After that only a campus admin / principal can
    # change it (with a reason, flagged `was_override` in the audit log).
    # See campus/attendance_edit.py.
    attendance_edit_window_hours = models.PositiveSmallIntegerField(
        default=24, validators=[MinValueValidator(1), MaxValueValidator(720)]
    )

    # Staff leave calendar + quota (see campus/staff_leave.py).
    #   weekly_off_days: ISO weekdays (Mon=1..Sun=7) that never count as leave
    #                    days, e.g. [7] or [6, 7]. Empty = every day counts.
    #   leave_quota:     per-year days allowed per leave type, e.g.
    #                    {"casual": 12, "sick": 10}. A type that is missing is
    #                    unlimited (that's how unpaid / other work).
    # Dates in `CampusHoliday` also never count.
    weekly_off_days = models.JSONField(default=default_weekly_off, blank=True)
    leave_quota = models.JSONField(default=dict, blank=True)

    # Soft toggle, set by the creator — independent of verification
    # (see `VerificationStatus` above): a creator can still deactivate
    # their own approved campus, and (de)activation is not itself an
    # approval signal either way.
    is_active = models.BooleanField(default=True)

    # G-2 fix. Platform-admin approval gate — see `VerificationStatus`
    # above for exactly what this does and doesn't restrict.
    verification_status = models.CharField(
        max_length=10, choices=VerificationStatus.choices, default=VerificationStatus.PENDING, db_index=True
    )
    verified_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campuses_verified"
    )
    verified_at = models.DateTimeField(null=True, blank=True)

    # Per-campus opt-in for the whole fee module (see module docstring).
    # Every other campus feature works identically whether this is on or
    # off — nothing else in this file checks this flag; it's enforced at
    # the service/view layer that creates FeeStructure/FeeInvoice rows.
    fee_module_enabled = models.BooleanField(default=False)

    # [Task 19 — ORG_VS_INDIVIDUAL_MATRIX] WIRED (this pass) — previously
    # a dead admin-visible toggle (`TestSeries.save()` force-freed every
    # `source="campus"` series regardless of this flag, and `campus.
    # bridge.create_testseries()` had no `is_paid`/`price_coins` kwarg to
    # even pass a caller's intent through). Now actually enforced:
    #   - `campus.bridge.create_testseries()` resolves this flag via
    #     `section.school_class.campus.testseries_paid_allowed` and
    #     force-resets `is_paid`/`price_coins` to `False`/`0` whenever
    #     it's `False` — the real, only enforcement point now (see that
    #     function's own docstring).
    #   - `TestSeries.save()` (testseries/models.py) no longer re-checks
    #     `source == CAMPUS` at all — `testseries` never imports `campus`
    #     models (golden rule), so it has no way to see this flag; that
    #     defence-in-depth layer is gone by necessity, not oversight (see
    #     that method's own comment).
    #   - `campus.views.TestSeriesViewSet.create()` also gates on it
    #     directly, before ever calling the bridge, so a request for a
    #     paid series against a campus with this `False` gets a clear
    #     403 instead of a silent downgrade to free.
    # Still defaults to `False` — flipping this for a campus is the only
    # behavior change; every existing campus keeps today's always-free
    # behavior unchanged. See `docs/ORG_VS_INDIVIDUAL_MATRIX.md`.
    testseries_paid_allowed = models.BooleanField(default=False)

    # T4 — per-campus feature flags for the new student-facing modules
    # (same "optional per campus" posture `fee_module_enabled` takes).
    # All three default so existing campuses behave sensibly without any
    # admin action: doubts + reminders ON, class-wide public doubts OFF
    # (decision D4 — doubts are private to author + that class's
    # teachers unless the campus explicitly allows a public toggle).
    doubts_enabled = models.BooleanField(default=True)
    doubts_public_allowed = models.BooleanField(default=False)
    class_reminders_enabled = models.BooleanField(default=True)

    # Live class -> automatic attendance (campus/live_class.py). When a live
    # session ENDS, every active student of the section gets a subject-wise
    # `Attendance` row computed from how long they really stayed in the room:
    #   present = stayed >= `live_attendance_min_percent` % of the class
    #   late    = same, but first joined > `live_attendance_late_minutes` after start
    #   absent  = never joined / stayed less than the minimum
    # Rows a teacher edited by hand (`Attendance.source == manual`) are never
    # touched again. Switch `live_attendance_auto` off to keep attendance 100% manual.
    live_attendance_auto = models.BooleanField(default=True)
    live_attendance_min_percent = models.PositiveSmallIntegerField(
        default=50, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    live_attendance_late_minutes = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(0), MaxValueValidator(120)]
    )

    # Recorded classes (campus/recordings.py). Recording a class films students'
    # faces / voices, so it is OFF until the campus admin switches it on. When
    # on, `class_recording_auto` starts the recording by itself a moment after the
    # teacher is inside the room (the teacher can still start / stop it by hand).
    class_recording_enabled = models.BooleanField(default=False)
    class_recording_auto = models.BooleanField(default=True)

    # Results: per-campus grade bands + pass mark. Grade kabhi store nahi
    # hota (marks se turant nikalta hai — campus.grading), isliye scale
    # badalne par purane grade stale nahi rehte. Khaali/invalid scale =
    # CBSE-style default (campus.grading.DEFAULT_GRADE_SCALE).
    grade_scale = models.JSONField(default=list, blank=True)
    pass_percent = models.DecimalField(max_digits=5, decimal_places=2, default=33)

    # Campus Settings (campus/settings_views.py, rules in campus/settings_rules.py).
    #   grading_system:    cbse (letter grades, default) | percent (percentage only, no letter) | gpa (letter + grade
    #                      points per band). See campus/grading.py.
    #   half_day_weekdays: ISO weekdays (Mon=1..Sun=7) that are working days but only half a day (e.g. [6]).
    #                      Never overlaps `weekly_off_days`. Shown in the working-days calendar; leave counting is
    #                      unchanged (a half-working Saturday still counts as one leave day).
    grading_system = models.CharField(
        max_length=10, default="cbse",
        choices=[("cbse", "CBSE letter grades"), ("percent", "Percentage only"), ("gpa", "GPA")],
    )
    half_day_weekdays = models.JSONField(default=default_weekly_off, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def soft_delete_check(self):
        # A campus that has ever taken a fee payment must be kept (deactivate it instead).
        if FeePayment.objects.filter(status=FeePayment.Status.SUCCESS, invoice__campus_id=self.pk).exists():
            raise SoftDeleteError(
                "This campus has recorded fee payments, so it cannot be deleted. Deactivate it instead.",
                code="campus_has_payments", status=409,
            )

    def __str__(self):
        return self.name


class AcademicSession(CampusBaseModel, SoftDeleteModel):
    """e.g. "2026-27". Every model below this point is scoped to one of
    these — a school "restarting" after any disruption is just starting a
    new session, never a schema/data problem (design doc §9)."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="sessions")
    name = models.CharField(max_length=20)  # "2026-27"
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        constraints = [
            # Only one row per campus can be "current" — enforced at the
            # DB level via a partial unique index rather than trusted to
            # application code (same reasoning as every other constraint
            # in this codebase: admin bulk-edits and shell access bypass
            # view-level checks).
            UniqueConstraint(
                fields=["campus"],
                condition=Q(is_current=True),
                name="one_current_session_per_campus",
            ),
        ]

    def save(self, *args, **kwargs):
        """
        Flips every other session of this campus to `is_current=False`
        *before* this row is written, inside the same transaction — the
        DB-level `one_current_session_per_campus` constraint would
        otherwise reject this row the instant two sessions of the same
        campus are `is_current=True` at once, including the plain
        `AcademicSession.objects.create(..., is_current=True)` case
        (bulk-import, shell, admin), not just the `set-current` API
        action.
        """
        if self.is_current:
            with transaction.atomic():
                AcademicSession.objects.filter(campus_id=self.campus_id, is_current=True).exclude(
                    pk=self.pk
                ).update(is_current=False)
                super().save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.campus.name} - {self.name}"


class Department(CampusBaseModel, SoftDeleteModel):
    """Optional — college-level grouping. Left un-session-scoped since
    departments don't get recreated every year the way class rosters do."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.campus.name} - {self.name}"


class SchoolClass(CampusBaseModel, SoftDeleteModel):
    # Named SchoolClass, not Class, to avoid shadowing the Python keyword.
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="classes")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="classes")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField(max_length=100)  # "Class 10", "B.Sc 2nd Year"

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["session", "campus"])]

    def soft_delete_check(self):
        n = StudentEnrollment.objects.filter(section__school_class_id=self.pk, status=StudentEnrollment.Status.ACTIVE).count()
        if n:
            raise SoftDeleteError(
                f"{n} active student(s) are enrolled in this class. Move or withdraw them first.",
                code="class_has_students", status=409,
            )

    def __str__(self):
        return f"{self.name} ({self.session.name})"


class Section(CampusBaseModel, SoftDeleteModel):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=20)  # "A", "B"

    # [ADDED — section-group chat wiring] Mirrors tuitionclass.Classroom's
    # own chat_group_enabled/linked_conversation_id pair exactly (see
    # core/classroom_chat_bridge.py's module docstring, VERIFIED note on
    # Classroom) — this is where core.classroom_chat_bridge.
    # create_section_group() persists the message.Group/Conversation it
    # creates for this section, so repeat calls are idempotent (return
    # the existing group) instead of creating a duplicate every time.
    # UUIDField (not a FK to message.Conversation) for the same reason
    # Classroom uses one: campus must never import message models
    # directly (golden rule, this file's own module docstring) — the
    # actual Group/Conversation row is looked up by this id, lazily,
    # only from inside core/classroom_chat_bridge.py.
    chat_group_enabled = models.BooleanField(default=False)
    linked_conversation_id = models.UUIDField(null=True, blank=True)
    # [T4 §D] Max ACTIVE students for this section (NULL = unlimited).
    # Enforced by services.enroll_student(); admin/principal may override.
    capacity = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [UniqueConstraint(fields=["school_class", "name"], condition=Q(is_deleted=False), name="unique_section_per_class_alive")]

    def soft_delete_check(self):
        n = self.enrollments.filter(status=StudentEnrollment.Status.ACTIVE).count()
        if n:
            raise SoftDeleteError(
                f"{n} active student(s) are enrolled in this section. Move or withdraw them first.",
                code="section_has_students", status=409,
            )

    def __str__(self):
        return f"{self.school_class} - {self.name}"


class Subject(CampusBaseModel, SoftDeleteModel):
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="subjects")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="subjects")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Room(CampusBaseModel, SoftDeleteModel):
    """Physical or virtual room label, used only for timetable
    clash-detection (§5). Optional per campus — TimetableEntry.room is
    nullable, so campuses that don't care about room-booking clashes can
    simply never create Room rows."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=100)
    is_virtual = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.campus.name} - {self.name}"


class StaffProfile(CampusBaseModel, SoftDeleteModel):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        PRINCIPAL_HOD = "principal_hod", "Principal/HOD"
        CLASS_TEACHER = "class_teacher", "Class Teacher"
        SUBJECT_TEACHER = "subject_teacher", "Subject Teacher"
        # [T4 §C / D3] Scoped manager: sees/manages ONLY the sections where
        # he/she holds an approved SubjectTeacherassigments or a
        # ClassTeacherassigments (see permissions.py). Unlike a plain
        # subject_teacher he may also mark daily attendance / post section
        # notices for those sections.
        MODERATOR = "moderator", "Moderator"
        NON_TEACHING = "non_teaching", "Non-Teaching Staff"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="staff_profiles")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_staff_profiles")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)
    # [T4 §A] Optional department scope. For a Principal/HOD it limits the
    # participants view to that department (NULL = whole campus, the old
    # G-5 behaviour); for other staff it is just their home department.
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="staff_profiles"
    )

    class Meta:
        # One user can hold a staff row at more than one campus — but only
        # one role-row per (campus, user) pair.
        ordering = ["id"]
        constraints = [UniqueConstraint(fields=["campus", "user"], condition=Q(is_deleted=False), name="unique_staff_profile_per_campus_alive")]
        indexes = [models.Index(fields=["campus", "role"])]

    def __str__(self):
        return f"{self.user.username} @ {self.campus.name} ({self.role})"


class ClassTeacherassigments(CampusBaseModel, SoftDeleteModel):
    section = models.OneToOneField(Section, on_delete=models.CASCADE, related_name="class_teacher_assigments")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="class_teacher_of")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.section} - {self.staff.user.username}"


class SubjectTeacherassigments(CampusBaseModel, SoftDeleteModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="subject_teacher_assigmentss")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="teacher_assigmentss")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="subject_assigmentss")
    # The class-teacher who approved/rejected this — matches the "class
    # teacher subject-teacher ko allow karega" flow from the design doc.
    approved_by = models.ForeignKey(
        StaffProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="approvals_made"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    # When the class-teacher/admin approved or rejected this request —
    # referenced by the approve/reject actions in views.py, and by
    # tests.py's `test_class_teacher_can_approve`.
    responded_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            UniqueConstraint(
                fields=["section", "subject", "staff"], condition=Q(is_deleted=False),
                name="unique_subject_teacher_assigments_alive",
            )
        ]

    def __str__(self):
        return f"{self.staff.user.username} - {self.section} - {self.subject} ({self.status})"


class StudentEnrollment(CampusBaseModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRANSFERRED = "transferred", "Transferred"
        GRADUATED = "graduated", "Graduated"
        # Year-end: moved up (or detained -> repeats) into the next session. The
        # old row stays as history; a fresh ACTIVE row exists in the new session.
        PROMOTED = "promoted", "Promoted"
        # [T4 §D] soft removal (no hard delete — history/audit).
        WITHDRAWN = "withdrawn", "Withdrawn"

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_enrollments")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="enrollments")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="enrollments")
    roll_number = models.CharField(max_length=30, blank=True)
    # [ADDED — Task 11] Closes the GAP the unified `assigments` app's own
    # models.py flagged against this exact model: `assigmentsSubmission.
    # enrollment_no` (assigments app) was shipped as an always-blank
    # snapshot field because `StudentEnrollment` had no dedicated
    # `enrollment_no` of its own to snapshot — only `(student, section,
    # session)` as the de-facto enrollment key. This is the school's own
    # enrollment/admission number (distinct from `roll_number`, which is
    # section-scoped and can change every session) — blank-by-default so
    # existing rows don't need a backfill value to pass validation;
    # campuses that care about tracking it can start setting it going
    # forward, and `campus.bridge.create_assigments()` now has a real
    # value to snapshot into new `assigments.assigmentsSubmission` rows
    # instead of always passing `enrollment_no=""`.
    enrollment_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    # [T4 §D] when the row stopped being ACTIVE (transfer / withdraw).
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-session__start_date"]
        constraints = [
            # A student gets exactly one row per section per session — new
            # year, new row; last year's row stays as read-only history.
            # Deliberately NOT unique across campuses/sessions overall —
            # see module docstring, open question §13.1.
            UniqueConstraint(fields=["student", "section", "session"], name="unique_enrollment_per_section_session")
        ]
        indexes = [
            models.Index(fields=["session", "status"]),
            # [PERF] roster / capacity / "who is in this section this year" filter on all three
            models.Index(fields=["section", "session", "status"], name="enroll_sec_sess_status_idx"),
            # [PERF] "my classes", parent-link active_enrollment, student-scoped visibility
            models.Index(fields=["student", "status"], name="enroll_student_status_idx"),
        ]

    def __str__(self):
        return f"{self.student.username} - {self.section} ({self.session.name})"


class CampusParentLink(CampusBaseModel):
    """Confirms a parent's campus-scoped access to a specific student.
    Deliberately thin — actual OTP/token issuance and verification stays
    inside the `message` app's ParentAccessCode/ParentToken flow (already
    wired through `core.classroom_chat_bridge.resolve_parent_from_token()`);
    this row is only ever written AFTER that verification succeeds, and it
    exists so campus-side code (fee, attendance, results, notices) has a
    campus-scoped student<->parent pointer without importing `message`
    internals directly."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="parent_links")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_parent_links")
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_child_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [UniqueConstraint(fields=["campus", "student", "parent"], name="unique_campus_parent_link")]

    def __str__(self):
        return f"{self.parent.username} -> {self.student.username} @ {self.campus.name}"


class CampusInviteCode(CampusBaseModel):
    """
    [ADDED — Task 13, growth list §D/G13] "Invite your classmates to
    [Campus Name]" — one enrolled batch/section brings its whole
    section onto the platform, self-serve, no admin action per new
    student.

    Deliberately scoped to a single `section`, not the whole campus:
    `StudentEnrollment` (see above) requires a `section` FK to create a
    row at all — there's no "campus member, no section" concept
    anywhere else in this app — so a code that resolved to "just this
    campus" would have nowhere to actually enroll the redeemer without
    a second "now pick your section" step. A section-scoped code also
    matches the real-world flow better: it's a class-teacher/admin
    generating "join OUR batch", not a stranger joining an entire
    school blind. `session` is deliberately NOT stored separately here
    (unlike `StudentEnrollment.session`) — it's always resolved off
    `section.school_class.session` at redeem time (see
    `campus_invite.py`), the same FK chain `StudentEnrollmentSerializer.
    validate()` already asserts must match, so there's no way for this
    row to point at a stale/mismatched session.

    One code can be reused by every classmate until it's deactivated,
    expires, or (optionally) hits `max_uses` — unlike `ParentAccessCode`
    (message app), which is minted fresh per-parent. That's intentional:
    a parent code is a private 1:1 credential; this is a public "here's
    our batch's join code" meant to be pasted into a WhatsApp group.
    """

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="invite_codes")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="invite_codes")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="campus_invite_codes_created")

    # Same "no 0/O/1/I" alphabet as `message.ParentAccessCode` — see
    # `generate_for()` below — but shorter (7 chars): this code is meant
    # to be read aloud / typed by a classmate, not just tapped from a
    # link, and it's scoped to one section (far smaller collision space
    # to defend than a platform-wide parent code) so 7 chars is still
    # plenty of entropy against the throttled redeem endpoint.
    code = models.CharField(max_length=12, unique=True, db_index=True)
    label = models.CharField(max_length=80, blank=True, default="")

    is_active = models.BooleanField(default=True)
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # None = unlimited
    uses_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["section", "is_active"])]

    ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    DEFAULT_TTL_DAYS = 365  # ~one academic year — admin can always deactivate sooner

    @classmethod
    def _generate_code(cls):
        return "".join(secrets.choice(cls.ALPHABET) for _ in range(7))

    @classmethod
    def generate_for(cls, *, section, campus, created_by, label="", max_uses=None, ttl_days=None):
        ttl_days = ttl_days if ttl_days is not None else cls.DEFAULT_TTL_DAYS
        for _ in range(5):
            code = cls._generate_code()
            if not cls.objects.filter(code=code).exists():
                return cls.objects.create(
                    campus=campus,
                    section=section,
                    created_by=created_by,
                    code=code,
                    label=label,
                    max_uses=max_uses,
                    expires_at=timezone.now() + timedelta(days=ttl_days) if ttl_days else None,
                )
        raise RuntimeError("Invite code generate nahi ho paaya, dobara try karo.")

    @property
    def is_expired(self):
        return self.expires_at is not None and self.expires_at <= timezone.now()

    @property
    def is_usable(self):
        if not self.is_active or self.is_expired:
            return False
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return False
        return True

    @property
    def unavailable_reason(self):
        """T2.8 — WHY this code can't take new joins: "revoked" | "expired" |
        "full", or "" while it is usable. Lets the app show a specific message
        (and a teacher know whether to rotate or just raise the cap) instead
        of one generic "no longer active"."""
        if not self.is_active:
            return "revoked"
        if self.is_expired:
            return "expired"
        if self.max_uses is not None and self.uses_count >= self.max_uses:
            return "full"
        return ""

    @property
    def remaining_uses(self):
        """Seats left, or None when the code is unlimited."""
        if self.max_uses is None:
            return None
        return max(0, self.max_uses - self.uses_count)

    def __str__(self):
        return f"{self.code} -> {self.section} @ {self.campus.name}"


# ---------------------------------------------------------------------------
# 2. Notices
# ---------------------------------------------------------------------------

class Notice(CampusBaseModel, SoftDeleteModel):
    """Scope is whichever of campus/department/school_class/section is
    set — nullable FKs so one model covers every scope level instead of
    four near-duplicate ones. Section-group auto-creation and the actual
    group-chat message send happen in the service layer via
    `core.classroom_chat_bridge.create_section_group()`, not here."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="notices")
    department = models.ForeignKey(Department, on_delete=models.CASCADE, null=True, blank=True, related_name="notices")
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, null=True, blank=True, related_name="notices")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, null=True, blank=True, related_name="notices")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="notices")

    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="campus_notices_posted")
    title = models.CharField(max_length=200)
    body = models.TextField()
    pin_until = models.DateTimeField(null=True, blank=True)

    # --- COMMUNICATION: kind / audience / scheduling (rules in campus/notice_rules.py) ----------
    class Kind(models.TextChoices):
        NORMAL = "normal", "Notice"
        EMERGENCY = "emergency", "Emergency broadcast"

    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Students, parents and staff"
        STUDENTS = "students", "Students only"
        PARENTS = "parents", "Parents only"
        STAFF = "staff", "Staff only"

    class Status(models.TextChoices):
        PUBLISHED = "published", "Published"
        SCHEDULED = "scheduled", "Scheduled"
        CANCELLED = "cancelled", "Cancelled"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.NORMAL, db_index=True)
    # Who gets the PUSH (the scope FKs above decide WHERE: whole campus / department / class / section).
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.EVERYONE)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PUBLISHED, db_index=True)
    # Set while status=scheduled: when the every-minute task should publish it.
    scheduled_for = models.DateTimeField(null=True, blank=True)
    # When it actually went out (== created_at for an immediate notice).
    published_at = models.DateTimeField(null=True, blank=True)
    # How many people the push fan-out was addressed to (0 until delivered).
    recipient_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["campus", "-created_at"]),
            models.Index(fields=["status", "scheduled_for"], name="campus_notice_due_idx"),
        ]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# 3. Tuition classes (coin-free)
# ---------------------------------------------------------------------------

class CampusLiveSession(CampusBaseModel, SoftDeleteModel):
    """Deliberately lightweight and coin-free — no pass/escrow/coin field
    exists on this model at all, by design, so it's structurally
    impossible for this to accidentally grow into a second marketplace.
    Video-room provisioning reuses `tuitionclass`/`message` infra only via
    `core.classroom_chat_bridge.provision_video_room(...)` from the
    service layer."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="live_sessions")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="live_sessions")
    teacher = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="live_sessions_taught")
    scheduled_at = models.DateTimeField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    room_id = models.CharField(max_length=150, blank=True)  # video token/room identifier
    # T4 §G — `online` (default for a live session: it IS the video
    # class) or `offline` (an in-person class tracked as a session:
    # students see no time, no video room is provisioned, no reminder).
    mode = models.CharField(max_length=10, choices=ClassMode.choices, default=ClassMode.ONLINE, db_index=True)
    # T4 §G — set (atomically, by the reminder task) the moment the
    # "class starts in N minutes" notification is fanned out, so the
    # every-minute beat job can never send it twice for one session.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)

    # --- One-tap "Go live" from the timetable (campus/live_class.py) ---------
    # The weekly period this session was started from + the calendar day it
    # ran on. (timetable_entry, occurrence_date) is unique among non-cancelled
    # sessions, so a double tap / two devices / a retry can never create a
    # second room for the same period.
    timetable_entry = models.ForeignKey(
        "TimetableEntry", on_delete=models.SET_NULL, null=True, blank=True, related_name="live_sessions"
    )
    occurrence_date = models.DateField(null=True, blank=True)
    # Real start / end of the class (scheduled_at is only the plan). Attendance
    # percentages are measured against started_at..ended_at.
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    started_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_live_sessions_started"
    )
    # Pinged by the teacher's app every ~20-30 s. A LIVE session whose heartbeat
    # goes silent is auto-ended by `tasks.sweep_live_sessions` (app crash / no network).
    # Stays NULL for a session started by an old app that never sends heartbeats —
    # such a session is never auto-ended on silence (only by the max-hours cap).
    live_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # Stamped once `live_class.build_attendance` has run (also when it
    # deliberately skipped), so the catch-up sweep never retries forever.
    attendance_built_at = models.DateTimeField(null=True, blank=True)

    # --- Recorded class (campus/recordings.py owns every transition) --------
    # ONE recording per session: none -> recording -> processing -> ready | failed.
    # `recording_url` is only ever shown to people who may watch it (see
    # `recordings.can_watch`); `recording_hidden` lets the teacher / a manager pull a
    # ready recording back from students without deleting the file.
    class RecordingStatus(models.TextChoices):
        NONE = "none", "Not recorded"
        RECORDING = "recording", "Recording"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    recording_status = models.CharField(
        max_length=12, choices=RecordingStatus.choices, default=RecordingStatus.NONE, db_index=True
    )
    recording_egress_id = models.CharField(max_length=100, blank=True)
    recording_url = models.CharField(max_length=600, blank=True)
    recording_started_at = models.DateTimeField(null=True, blank=True)
    recording_stopped_at = models.DateTimeField(null=True, blank=True)
    recording_ready_at = models.DateTimeField(null=True, blank=True)
    recording_duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    recording_hidden = models.BooleanField(default=False)
    # Stamped on the FIRST automatic start try, so a misconfigured video backend is
    # tried once per session, never once per heartbeat.
    recording_attempted_at = models.DateTimeField(null=True, blank=True)
    # Short machine code of the last failure (`not_configured`, `start_failed`,
    # `egress_failed`, `no_file`, `timeout`) — never a raw exception text.
    recording_error = models.CharField(max_length=40, blank=True)
    # Claimed (atomically) by the "recording is ready" push task, so a repeated webhook /
    # two workers can never buzz a whole section twice for one recording.
    recording_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["scheduled_at"]
        indexes = [
            models.Index(fields=["section", "scheduled_at"]),
            models.Index(fields=["section", "subject", "recording_status"], name="campus_live_rec_idx"),
            # the every-minute reminder sweep filters on exactly these
            models.Index(fields=["status", "mode", "scheduled_at"], name="campus_live_reminder_idx"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["timetable_entry", "occurrence_date"],
                condition=Q(timetable_entry__isnull=False) & ~Q(status="cancelled"),
                name="campus_live_one_per_entry_day",
            ),
        ]

    def __str__(self):
        return f"{self.subject} - {self.section} @ {self.scheduled_at}"


class CampusLiveParticipant(CampusBaseModel):
    """
    Who was really inside a live session's room, and for how long — the raw
    data the automatic attendance is computed from.

    One row per continuous stay: `joined_at` is stamped by the `join` / `go-live`
    endpoints, `last_seen_at` by the heartbeat, `left_at` by `leave`, by the
    session ending, or (a student whose app just died) by the next join closing
    the stale row at `last_seen_at`. Teachers are recorded too (cheap, and handy
    for "teacher joined late") — only enrolled students feed attendance.
    """

    session = models.ForeignKey(CampusLiveSession, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_live_participations")
    joined_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["joined_at"]
        indexes = [
            models.Index(fields=["session", "user"], name="campus_liveprt_sess_user_idx"),
        ]

    def __str__(self):
        return f"{self.user_id} in {self.session_id} ({self.joined_at})"


# ---------------------------------------------------------------------------
# 5. Smart timetable + attendance automation
# ---------------------------------------------------------------------------

class TimeSlot(CampusBaseModel, SoftDeleteModel):
    class Day(models.IntegerChoices):
        MONDAY = 1, "Monday"
        TUESDAY = 2, "Tuesday"
        WEDNESDAY = 3, "Wednesday"
        THURSDAY = 4, "Thursday"
        FRIDAY = 5, "Friday"
        SATURDAY = 6, "Saturday"
        SUNDAY = 7, "Sunday"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="time_slots")
    day_of_week = models.PositiveSmallIntegerField(choices=Day.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    label = models.CharField(max_length=50, blank=True)  # "Period 3"

    class Meta:
        ordering = ["day_of_week", "start_time"]

    def __str__(self):
        return f"{self.get_day_of_week_display()} {self.start_time}-{self.end_time}"


class TimetableEntry(CampusBaseModel, SoftDeleteModel):
    """Clash-detection happens at save-time (design doc §5 explicitly asks
    for this, not just at the serializer layer) so nothing that writes a
    TimetableEntry directly — admin, shell, a future bulk-import script —
    can bypass it. Implemented as `clean()` + a `save()` override that
    calls `full_clean()` first, the standard Django way to make model
    validation unconditional rather than opt-in per caller."""

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="timetable_entries")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="timetable_entries")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="timetable_entries")
    time_slot = models.ForeignKey(TimeSlot, on_delete=models.CASCADE, related_name="timetable_entries")
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True, related_name="timetable_entries")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="timetable_entries")
    # T4 §G — default `offline` (a normal in-person period): students
    # get an "Offline class" label instead of the time and no reminder.
    # `online` periods show the time and get the 5-minute reminder.
    mode = models.CharField(max_length=10, choices=ClassMode.choices, default=ClassMode.OFFLINE, db_index=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["session", "time_slot"])]

    def clean(self):
        # Overlap-aware (not just same TimeSlot row) — see campus/timetable_conflicts.py.
        # Non-field list on purpose: safe for the admin form and the API mixin.
        from .timetable_conflicts import find_conflicts_for_entry

        conflicts = find_conflicts_for_entry(self)
        if conflicts:
            msgs = list(dict.fromkeys(c["message"] for c in conflicts))
            raise ValidationError(msgs)

    def _lock_resources(self):
        """Serialise concurrent writers touching the same teacher/section/room so two
        simultaneous saves cannot both pass clean() (no-op on SQLite)."""
        for model, pk in ((StaffProfile, self.staff_id), (Section, self.section_id), (Room, self.room_id)):
            if pk:
                list(model.objects.select_for_update().filter(pk=pk).values_list("pk", flat=True))

    def save(self, *args, **kwargs):
        with transaction.atomic():
            self._lock_resources()
            self.full_clean()
            super().save(*args, **kwargs)

    def restore_check(self):
        # Someone may have booked the same teacher / room / section while this was in the trash.
        try:
            self.clean()
        except ValidationError as exc:
            raise SoftDeleteError(
                "Cannot restore: " + "; ".join(exc.messages), code="timetable_conflict", status=409,
            )

    def __str__(self):
        return f"{self.section} - {self.subject} - {self.time_slot}"


class Attendance(CampusBaseModel):
    class Status(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"
        LATE = "late", "Late"
        LEAVE = "leave", "Leave"

    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, null=True, blank=True, related_name="attendance_records")
    status = models.CharField(max_length=10, choices=Status.choices)
    marked_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="attendance_marked")
    # Edit window anchor: when this row was FIRST marked. Never changes on edit
    # (otherwise every edit would re-open the window). Rows that existed before
    # the edit-window feature get the migration time.
    marked_at = models.DateTimeField(default=timezone.now, editable=False)
    last_edited_at = models.DateTimeField(null=True, blank=True, editable=False)
    edit_count = models.PositiveSmallIntegerField(default=0, editable=False)

    class Source(models.TextChoices):
        MANUAL = "manual", "Marked by a teacher"
        AUTO = "auto", "Automatic (live class)"

    # `auto` rows are written by campus/live_class.build_attendance when a live
    # class ends; the moment a teacher changes one it becomes `manual` and the
    # automation never overwrites it again. Existing rows are all `manual`.
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL, editable=False)
    # Minutes the student really stayed in the live room (auto rows only).
    minutes_present = models.PositiveSmallIntegerField(null=True, blank=True, editable=False)
    live_session = models.ForeignKey(
        "CampusLiveSession", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="attendance_rows", editable=False,
    )

    class Meta:
        ordering = ["-date"]
        constraints = [UniqueConstraint(fields=["enrollment", "date", "subject"], name="unique_attendance_per_day_subject")]
        indexes = [
            models.Index(fields=["enrollment", "date"]),
            # [PERF] daily absent-push / digest / campus-wide day sweeps filter on (date, status)
            models.Index(fields=["date", "status"], name="att_date_status_idx"),
        ]

    def __str__(self):
        return f"{self.enrollment} - {self.date} ({self.status})"


class AttendanceEditLog(CampusBaseModel):
    """
    Append-only audit trail of every CHANGE or DELETE of an already-marked
    attendance row (first-time marking is not logged here — nothing to correct
    yet). One row per student per edit: who, when, old -> new status, and the
    reason they gave. Written only by `attendance_edit.record_edits`; never
    edited or deleted through the API / admin (see `AttendanceEditLogAdmin`).

    The FKs to section / enrollment / subject are SET_NULL so the history
    survives a restructure; `student_label` keeps a readable name regardless.
    """

    class Action(models.TextChoices):
        EDIT = "edit", "Edit"
        CLEAR = "clear", "Clear"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="attendance_edit_logs")
    section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_edit_logs")
    enrollment = models.ForeignKey(
        StudentEnrollment, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_edit_logs"
    )
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_edit_logs")
    student_label = models.CharField(max_length=150, blank=True)
    date = models.DateField()
    action = models.CharField(max_length=10, choices=Action.choices, default=Action.EDIT)
    old_status = models.CharField(max_length=10)
    new_status = models.CharField(max_length=10, blank=True)  # blank for CLEAR
    reason = models.CharField(max_length=300, blank=True)
    edited_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="attendance_edits")
    edited_at = models.DateTimeField(default=timezone.now, db_index=True)
    original_marked_at = models.DateTimeField(null=True, blank=True)
    # True when the edit happened AFTER the teacher edit window had closed
    # (only an admin / principal can do that).
    was_override = models.BooleanField(default=False)

    class Meta:
        ordering = ["-edited_at"]
        indexes = [
            models.Index(fields=["section", "date"], name="att_editlog_section_date_idx"),
            models.Index(fields=["enrollment", "date"], name="att_editlog_enroll_date_idx"),
            models.Index(fields=["campus", "-edited_at"], name="att_editlog_campus_time_idx"),
        ]

    def __str__(self):
        return f"{self.student_label or self.enrollment_id} {self.date}: {self.old_status}->{self.new_status or 'cleared'}"


# Note: attendance %-summary is a service function (compute_attendance_summary),
# not a model — same "don't store what you can cheaply recompute"
# reasoning as FeeInvoice.amount_paid below. The low-attendance alert is a
# Celery periodic task, not a model concern.


# ---------------------------------------------------------------------------
# 6. Homework / assigmentss + syllabus tracker
# ---------------------------------------------------------------------------

class assigments(CampusBaseModel):
    """[DEPRECATED — Task 11] Superseded by the unified `assigments` app
    (`assigments.assigments`, `source="campus"`, `context_type="section"`,
    `context_id=<this section's id>`). Every write path in this app now
    goes through `campus.bridge.create_assigments()` ->
    `assigments.bridge.create_context_assigments()` instead —
    `assigmentsViewSet` in views.py is a thin proxy over the unified
    model, not this one, going forward. Existing rows are kept purely as
    historical data (backfilled into the unified app by the one-time
    `migrate_campus_assigmentss_to_unified` management command); nothing
    in this app should create a NEW row here again. `save()` below
    enforces that — see its own docstring for the one legitimate
    exception (the migration command itself).
    """

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="assigmentss")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assigmentss")
    posted_by = models.ForeignKey(StaffProfile, on_delete=models.SET_NULL, null=True, related_name="assigmentss_posted")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    attachment = models.FileField(upload_to="campus/assigmentss/", null=True, blank=True)
    due_date = models.DateField()
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="assigmentss")

    class Meta:
        ordering = ["-due_date"]
        indexes = [models.Index(fields=["section", "due_date"])]

    def __str__(self):
        return self.title

    def save(self, *args, migration_write=False, **kwargs):
        """[DEPRECATED — Task 11] Refuses to write a NEW row (an update
        to an already-migrated historical row is still allowed — nothing
        left in this app should be doing that either, but blocking reads
        of existing data from ever being edited isn't this guard's job,
        only stopping the model from silently growing new rows once the
        rest of the app has moved off it). `migration_write=True` is the
        one sanctioned bypass, used only by
        `migrate_campus_assigmentss_to_unified` for the deliberate,
        one-time historical backfill — never pass it from application
        code.
        """
        if self.pk is None and not migration_write:
            raise RuntimeError(
                "campus.assigments is deprecated (Task 11) — new assigmentss must be created "
                "via campus.bridge.create_assigments(), which writes to the unified "
                "assigments.assigments model instead. If this is the one-time historical "
                "backfill, call save(migration_write=True)."
            )
        super().save(*args, **kwargs)


class assigmentsSubmission(CampusBaseModel):
    """[DEPRECATED — Task 11] See `assigments`'s docstring above — same
    reasoning, same `save()` guard, superseded by
    `assigments.assigmentsSubmission`.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        LATE = "late", "Late"
        MISSING = "missing", "Missing"

    assigments = models.ForeignKey(assigments, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_assigments_submissions")
    submitted_at = models.DateTimeField(null=True, blank=True)
    file = models.FileField(upload_to="campus/submissions/", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MISSING, db_index=True)
    grade = models.CharField(max_length=10, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [UniqueConstraint(fields=["assigments", "student"], name="campus_unique_submission_per_student")]

    def __str__(self):
        return f"{self.student.username} - {self.assigments.title} ({self.status})"

    def save(self, *args, migration_write=False, **kwargs):
        """See `assigments.save()` above — identical guard/rationale."""
        if self.pk is None and not migration_write:
            raise RuntimeError(
                "campus.assigmentsSubmission is deprecated (Task 11) — new submissions now "
                "live on the unified assigments.assigmentsSubmission model (pre-created by "
                "campus.bridge.create_assigments(), submitted/graded via that app's own model "
                "methods). If this is the one-time historical backfill, call "
                "save(migration_write=True)."
            )
        super().save(*args, **kwargs)


class SyllabusUnit(CampusBaseModel, SoftDeleteModel):
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="syllabus_units")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="syllabus_units")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="syllabus_units")
    title = models.CharField(max_length=200)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title


class SyllabusProgress(CampusBaseModel):
    syllabus_unit = models.OneToOneField(SyllabusUnit, on_delete=models.CASCADE, related_name="progress")
    covered_on = models.DateField(null=True, blank=True)  # null = not yet covered
    covered_by = models.ForeignKey(StaffProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name="syllabus_units_covered")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.syllabus_unit} - {'covered' if self.covered_on else 'pending'}"


# ---------------------------------------------------------------------------
# 6b. Homework Diary (daily, class-wise)
# ---------------------------------------------------------------------------

class HomeworkDiaryEntry(CampusBaseModel):
    """
    HOMEWORK DIARY — "roz ka homework", section (class-wise) + subject-wise.

    A teacher writes one line of homework for ONE `(section, subject)` for a
    given day (`diary_date`, defaults to today) with a `due_date` and an
    optional attachment (worksheet photo / PDF). Students of that section
    and their linked parents read it, class-wise, like a paper school diary.

    Deliberately NOT the unified `assignments` app: a diary line has no
    submission / grading / plagiarism machinery — it is a lightweight daily
    note ("Ex 4.2 Q1-5 karke aana"). Anything that needs submissions and
    marks is still an `assignments` row (`campus.assigmentss/`). Keeping the
    two apart means a diary post never creates a per-student submission row.

    `campus` / `session` are derived server-side from the section (never
    client-supplied), same as `CampusDoubt`. Never hard-deleted from the API
    — `is_active=False` hides it (history / audit).

    `due_reminder_sent_at` is the atomic claim marker for the "homework due
    tomorrow" push (`tasks.send_homework_diary_due_reminders`): one reminder
    per entry, however many times the task runs.
    """

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="homework_diary_entries")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="homework_diary_entries")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="homework_diary_entries")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="homework_diary_entries")
    posted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_homework_diary_posted"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    diary_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField()
    attachment = models.FileField(
        upload_to="campus/homework_diary/%Y/%m/", null=True, blank=True,
        validators=[attachment_extension_validator, validate_attachment_size],
    )
    is_active = models.BooleanField(default=True)
    due_reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-diary_date", "-created_at"]
        indexes = [
            # Explicit names (<=30 chars): the migration is hand-written, so
            # these must not depend on Django's auto-generated hash suffix.
            models.Index(fields=["section", "-diary_date"], name="hw_diary_section_date_idx"),
            models.Index(fields=["section", "due_date"], name="hw_diary_section_due_idx"),
            models.Index(fields=["campus", "session", "-diary_date"], name="hw_diary_campus_sess_idx"),
            models.Index(fields=["due_date", "due_reminder_sent_at"], name="hw_diary_due_reminder_idx"),
        ]
        constraints = [
            CheckConstraint(
                condition=Q(due_date__gte=models.F("diary_date")),
                name="homework_diary_due_not_before_date",
            ),
        ]

    def __str__(self):
        return f"{self.title} ({self.subject_id}/{self.section_id}) {self.diary_date}"


# ---------------------------------------------------------------------------
# 7. Results
# ---------------------------------------------------------------------------

class ExamTerm(CampusBaseModel, SoftDeleteModel):
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="exam_terms")
    name = models.CharField(max_length=100)  # "Mid-Term", "Final"
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.name} ({self.session.name})"


class ResultEntry(CampusBaseModel, SoftDeleteModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"

    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="results")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="results")
    exam_term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name="results")
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    max_marks = models.DecimalField(max_digits=6, decimal_places=2)
    remarks = models.CharField(max_length=255, blank=True)
    entered_by = models.ForeignKey(StaffProfile, on_delete=models.SET_NULL, null=True, related_name="results_entered")
    # Draft marks sirf staff ko dikhte hain; student/parent ko sirf PUBLISHED.
    # Default published: purana behaviour (turant publish) aur purane rows
    # waise hi rehte hain.
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PUBLISHED, db_index=True)
    published_at = models.DateTimeField(null=True, blank=True)
    # Lock = sheet final; sirf admin/principal unlock kar sakta hai.
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="results_locked"
    )

    class Meta:
        ordering = ["id"]
        constraints = [UniqueConstraint(
            fields=["enrollment", "subject", "exam_term"], condition=Q(is_deleted=False), name="unique_result_per_exam_alive"
        )]
        # [PERF] marks sheet / publish / lock = one (exam_term, subject) slice across a whole section
        indexes = [models.Index(fields=["exam_term", "subject"], name="result_term_subject_idx")]

    def __str__(self):
        return f"{self.enrollment} - {self.subject} - {self.exam_term} ({self.marks_obtained}/{self.max_marks})"


# Report cards are generated on-demand from ResultEntry rows (a view, not
# a model) — ResultEntry stays the single source of truth, same reasoning
# as attendance %-summary above.


# ---------------------------------------------------------------------------
# 8. Optional / future-ready modules
# ---------------------------------------------------------------------------

class DigitalIDCard(CampusBaseModel, SoftDeleteModel):
    """QR-based; scanning-hardware integration is future work. For now
    this just needs to exist and generate a stable, unique token."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="digital_id_cards")
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="digital_id_cards")
    qr_token = models.CharField(max_length=100, unique=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    valid_until = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self):
        return f"{self.user.username} - {self.campus.name} ID"


class FeeStructure(CampusBaseModel, SoftDeleteModel):
    """A fee DEFINITION (e.g. "Term 1 Tuition — Class 10", ₹15,000, due
    2026-07-15) — not a payment. See module docstring for why this is a
    DecimalField-based, fully decoupled table rather than anything built
    on top of `user_profile.CoinLedger`."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="fee_structures")
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.CASCADE, null=True, blank=True, related_name="fee_structures"
    )  # null = campus-wide fee, not tied to one class
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="fee_structures")
    title = models.CharField(max_length=150)
    # DecimalField, not PositiveIntegerField like User.coin — this is real
    # currency, and float/int rounding is not acceptable for money.
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    is_active = models.BooleanField(default=True)

    # FINANCE — fee head, concession eligibility and late-fine policy (see
    # campus/finance_rules.py for the exact fine rules).
    class Category(models.TextChoices):
        TUITION = "tuition", "Tuition"
        ADMISSION = "admission", "Admission"
        TRANSPORT = "transport", "Transport"
        EXAM = "exam", "Exam"
        LAB = "lab", "Lab / Library"
        HOSTEL = "hostel", "Hostel"
        UNIFORM = "uniform", "Uniform / Books"
        OTHER = "other", "Other"

    class FineType(models.TextChoices):
        NONE = "none", "No late fine"
        FLAT = "flat", "Flat amount (once)"
        PER_DAY = "per_day", "Amount per day late"
        PERCENT = "percent", "Percent of unpaid (once)"

    category = models.CharField(max_length=15, choices=Category.choices, default=Category.OTHER)
    allow_concession = models.BooleanField(default=True)
    late_fine_type = models.CharField(max_length=10, choices=FineType.choices, default=FineType.NONE)
    late_fine_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    late_fine_grace_days = models.PositiveSmallIntegerField(default=0)
    late_fine_cap = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["due_date"]
        indexes = [models.Index(fields=["campus", "session"])]

    def soft_delete_check(self):
        if FeePayment.objects.filter(status=FeePayment.Status.SUCCESS, invoice__fee_structure_id=self.pk).exists():
            raise SoftDeleteError(
                "Students have already paid against this fee structure. Switch it off (is_active) instead of deleting it.",
                code="fee_structure_has_payments", status=409,
            )

    def __str__(self):
        return f"{self.title} - {self.campus.name} ({self.amount})"


class FeeInvoice(CampusBaseModel, SoftDeleteModel):
    """One student's obligation against one FeeStructure. `status` is a
    cached/indexed field (needed so an admin dashboard can filter "who's
    overdue" without aggregating FeePayment on every request) but it is
    NEVER hand-set from a view — always recomputed via `recompute_status()`
    below, the same idempotent-recompute pattern as
    `core.Notification.mark_read()`."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partially Paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        WAIVED = "waived", "Waived"

    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="fee_invoices")
    fee_structure = models.ForeignKey(FeeStructure, on_delete=models.CASCADE, related_name="invoices")
    # amount_due = max(base_amount - concession_amount, 0) + fine_amount   (finance_rules.amount_due)
    # `amount_due` stays THE number everything else reads (gateway, dashboard, reminders).
    amount_due = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # FINANCE
    campus = models.ForeignKey(
        Campus, on_delete=models.CASCADE, null=True, blank=True, related_name="fee_invoices"
    )  # denormalised from the enrollment (set in save()) — fast per-campus reports + invoice numbers
    invoice_no = models.CharField(max_length=40, blank=True, db_index=True)  # INV/2026-27/000123
    base_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    concession_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fine_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    fine_waived = models.BooleanField(default=False)
    fine_computed_on = models.DateField(null=True, blank=True)
    adjustment_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["enrollment", "fee_structure"], condition=Q(is_deleted=False), name="unique_invoice_per_structure_alive"
            ),
            UniqueConstraint(
                fields=["campus", "invoice_no"], condition=~Q(invoice_no=""), name="unique_invoice_no_per_campus"
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "status"]),
            # [PERF] per-campus finance lists / summaries / defaulter sweeps (denormalised `campus`)
            models.Index(fields=["campus", "status"], name="feeinv_campus_status_idx"),
            models.Index(fields=["campus", "-created_at"], name="feeinv_campus_created_idx"),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            if self.campus_id is None and self.enrollment_id:
                self.campus_id = self.enrollment.section.school_class.campus_id
            if not self.base_amount and self.amount_due:
                self.base_amount = self.amount_due  # invoice made with only amount_due (old callers)
            if not self.invoice_no and self.campus_id:
                from . import finance  # local: finance imports this module

                with transaction.atomic():
                    self.invoice_no = finance.next_invoice_no(self)
                    return super().save(*args, **kwargs)
        return super().save(*args, **kwargs)

    @property
    def amount_paid(self):
        """Computed from successful payments, not stored redundantly —
        avoids the exact read-modify-write drift risk flagged on
        `User.followers_count`/`coin` in login/models.py. Cheap enough at
        invoice-detail scale; if a dashboard needs this in bulk later,
        that's an `.annotate()` at the queryset level, not a new column
        here.

        [PERF] `FeeInvoiceViewSet.list` DOES annotate `paid_total_ann` (one grouped
        query for the whole page instead of 2 queries per invoice). Only present
        on rows loaded by that annotated queryset; every other caller (payment
        flows, recompute_status) still hits the DB so it can never read a stale
        figure."""
        pre = self.__dict__.get("paid_total_ann")
        if pre is not None:
            return pre or 0
        total = self.payments.filter(status=FeePayment.Status.SUCCESS).aggregate(total=models.Sum("amount"))["total"]
        return total or 0

    def recompute_status(self):
        """Idempotent — only writes when the derived status actually
        changed. Call this inside the same transaction right after a
        FeePayment is marked SUCCESS (see FeePayment.mark_success)."""
        if self.status == self.Status.WAIVED:
            return  # waiving is an explicit admin decision — a payment event must not undo it
        from . import finance  # local: finance imports this module

        finance.refresh_fine(self)  # keeps amount_due = base - concession + fine
        paid = self.amount_paid
        if self.fee_structure.due_date and paid < self.amount_due:
            from django.utils import timezone

            new_status = self.Status.OVERDUE if timezone.now().date() > self.fee_structure.due_date else self.Status.PENDING
        else:
            new_status = self.Status.PAID
        if paid and paid < self.amount_due:
            new_status = self.Status.PARTIAL
        if new_status != self.status:
            self.status = new_status
            self.save(update_fields=["status"])

    def soft_delete_check(self):
        if self.payments.filter(status=FeePayment.Status.SUCCESS).exists():
            raise SoftDeleteError(
                "This invoice has a successful payment. Refund it first, or waive/adjust the invoice instead.",
                code="invoice_has_payments", status=409,
            )

    def __str__(self):
        return f"{self.enrollment} - {self.fee_structure.title} ({self.status})"


class FeePayment(CampusBaseModel):
    """One money-movement event against a FeeInvoice. Supports both the
    self-serve path (student or parent pays from their `user_profile`
    coin wallet — `payment_mode=WALLET`, `gateway_reference` set,
    `recorded_by` null) and the admin-maintained path (office staff
    records a cash/cheque/bank-transfer payment at the counter —
    `payment_mode` != WALLET, `recorded_by` set, no gateway reference at
    all, no wallet touched). Both are first-class, not one bolted onto
    the other, per the requirement that fee has to be maintainable
    either way at production level.

    `gateway_reference` is the idempotency key for the wallet path only
    (FEE-2 — previously an actual payment-gateway reference for a
    Razorpay integration that has been removed from this flow;
    `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` in settings.py are still used
    elsewhere, by `tuitionclass.CoinPurchase`, to top the wallet up — just
    not by fee anymore). Callers on the wallet path should
    `get_or_create(gateway_reference=..., defaults={...})` (see
    `FeePaymentViewSet.pay`), the same idempotency pattern
    `CoinLedger.reference` already established in this codebase, so a
    retried request can never double-apply a payment.
    """

    class PayerRole(models.TextChoices):
        STUDENT = "student", "Student"
        PARENT = "parent", "Parent"
        ADMIN = "admin", "Admin (walk-in / office-recorded)"

    class Mode(models.TextChoices):
        # FEE-2: was ONLINE ("online", "Online") — a real Razorpay
        # gateway path. Replaced with WALLET: the amount is debited
        # directly from the payer's user_profile.CoinLedger-backed
        # User.coin balance instead of any external gateway.
        WALLET = "wallet", "Wallet (Coins)"
        # FEE-5: real money through the payment gateway (Razorpay Checkout —
        # UPI / card / net-banking). Created PENDING with the gateway order id
        # in `gateway_reference`, settled to SUCCESS only by a verified
        # signature / signed webhook (campus/fee_gateway.py). Paise-capable.
        ONLINE = "online", "Online (UPI / Card)"
        CASH = "cash", "Cash"
        CHEQUE = "cheque", "Cheque"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    invoice = models.ForeignKey(FeeInvoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Who actually paid — deliberately separate from invoice.enrollment.student,
    # because a linked parent (via CampusParentLink) can pay a child's fee
    # and the row should say who paid, not just who owes it.
    paid_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_payments_made"
    )
    payer_role = models.CharField(max_length=10, choices=PayerRole.choices)

    payment_mode = models.CharField(max_length=15, choices=Mode.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)

    # Idempotency key — only ever populated for payment_mode=ONLINE.
    gateway_reference = models.CharField(max_length=150, blank=True, db_index=True)

    # FEE-5 — ONLINE payments only. `gateway_payment_id` = Razorpay pay_xxx (the
    # transaction id printed on the receipt), `gateway_method` = upi | card |
    # netbanking | wallet as reported by the gateway. `paid_at` = when the money
    # actually settled (an online row is created minutes before it is paid).
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    gateway_method = models.CharField(max_length=20, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    # FINANCE — set when a gateway reconciliation run matched this payment to Razorpay's report.
    reconciled_at = models.DateTimeField(null=True, blank=True)

    # Staff who manually entered a cash/cheque/bank-transfer payment at
    # the office. Null for the online self-serve path.
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_payments_recorded"
    )
    notes = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["invoice", "-created_at"]),
            # [PERF] FeeInvoice.amount_paid / paid_total_ann aggregate = WHERE invoice_id=? AND status='success'
            models.Index(fields=["invoice", "status"], name="feepay_invoice_status_idx"),
        ]
        constraints = [
            CheckConstraint(condition=Q(amount__gt=0), name="feepayment_amount_positive"),
            # Only enforce uniqueness on non-blank gateway references — the
            # manual/cash path never sets this field, so blank rows must
            # not collide with each other.
            UniqueConstraint(
                fields=["gateway_reference"],
                condition=~Q(gateway_reference=""),
                name="unique_nonblank_gateway_reference",
            ),
        ]

    def mark_success(self):
        """Idempotent, same pattern as `core.Notification.mark_read()` —
        only flips status and triggers the invoice recompute once, so
        calling this twice (e.g. a retried webhook after the DB write
        already landed) is a safe no-op."""
        if self.status != self.Status.SUCCESS:
            self.status = self.Status.SUCCESS
            if self.paid_at is None:
                self.paid_at = timezone.now()
            self.save(update_fields=["status", "paid_at"])
            self.invoice.recompute_status()

    def refund(self):
        """FEE-4: reverses a wallet payment (e.g. student paid the
        wrong invoice). Only meaningful for `payment_mode=WALLET` —
        cash/cheque/bank-transfer payments were never debited from a
        wallet in the first place, so there is nothing here to credit
        back through `CoinLedger`; those refunds are a manual
        counter-side/accounting matter outside this model, same as they
        already were before wallet payments existed.

        Idempotent, same shape as `mark_success()`: only actually moves
        money and flips status on a currently-SUCCESS payment; calling
        it again on an already-REFUNDED row is a safe no-op.
        `FeeInvoice.amount_paid` only sums `status=SUCCESS` payments, so
        once this flips to REFUNDED the invoice's `recompute_status()`
        naturally stops counting it — no separate "reversed" bookkeeping
        needed on the invoice side.

        Raises `ValueError` if called on a non-SUCCESS payment (nothing
        to refund) or a non-WALLET payment (nothing was debited to
        credit back).
        """
        if self.status != self.Status.SUCCESS:
            raise ValueError(f"Cannot refund a payment with status={self.status!r}; only SUCCESS payments can be refunded.")
        if self.payment_mode == self.Mode.ONLINE:
            raise ValueError(
                "Online (UPI / card) payments are refunded from the Razorpay dashboard; "
                "the invoice updates by itself when the refund is processed."
            )
        if self.payment_mode != self.Mode.WALLET:
            raise ValueError(
                f"Cannot refund payment_mode={self.payment_mode!r} through the wallet — "
                "no CoinLedger debit was ever made for this payment to credit back."
            )
        if self.paid_by_id is None:
            raise ValueError("Cannot refund a wallet payment with no paid_by user on record.")

        from user_profile.models import CoinLedger

        with transaction.atomic():
            CoinLedger.objects.record_transaction(
                user=self.paid_by,
                transaction_type=CoinLedger.TransactionType.REFUND,
                amount=int(self.amount),
                reference=f"fee-refund-{self.id}",
                description=f"Refund for fee payment on {self.invoice.fee_structure.title}",
                metadata={"invoice_id": str(self.invoice_id), "fee_payment_id": str(self.id)},
            )
            self.status = self.Status.REFUNDED
            self.save(update_fields=["status"])
            self.invoice.recompute_status()

    def __str__(self):
        return f"{self.invoice} - {self.amount} ({self.status})"


class FeeConcession(CampusBaseModel):
    """FINANCE — a named discount scheme (\"Sibling 10%\", \"Merit scholarship ₹5000\"). It does
    nothing by itself: it is applied to a student through a FeeConcessionGrant."""

    class Reason(models.TextChoices):
        SIBLING = "sibling", "Sibling"
        MERIT = "merit", "Merit"
        STAFF_WARD = "staff_ward", "Staff ward"
        SCHOLARSHIP = "scholarship", "Scholarship"
        FINANCIAL_AID = "financial_aid", "Financial aid"
        OTHER = "other", "Other"

    class Kind(models.TextChoices):
        PERCENT = "percent", "Percent"
        FLAT = "flat", "Flat amount"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="fee_concessions")
    name = models.CharField(max_length=100)
    reason = models.CharField(max_length=15, choices=Reason.choices, default=Reason.OTHER)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.PERCENT)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    # blank = every fee head; else only fees of this FeeStructure.Category
    applies_to_category = models.CharField(max_length=15, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(fields=["campus", "name"], name="unique_concession_name_per_campus"),
            CheckConstraint(condition=Q(value__gt=0), name="feeconcession_value_positive"),
            CheckConstraint(
                condition=~Q(kind="percent") | Q(value__lte=100), name="feeconcession_percent_max_100"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.value}{'%' if self.kind == 'percent' else ''})"


class FeeConcessionGrant(CampusBaseModel):
    """FINANCE — a concession given to one student enrollment (= one session). `fee_structure`
    null = applies to every eligible fee of that session; set = only that fee. Granting /
    revoking re-prices the student's existing invoices (campus/finance.py)."""

    concession = models.ForeignKey(FeeConcession, on_delete=models.CASCADE, related_name="grants")
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="fee_concession_grants")
    fee_structure = models.ForeignKey(
        FeeStructure, on_delete=models.CASCADE, null=True, blank=True, related_name="concession_grants"
    )
    granted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["concession", "enrollment"], condition=Q(fee_structure__isnull=True),
                name="unique_grant_all_fees",
            ),
            UniqueConstraint(
                fields=["concession", "enrollment", "fee_structure"], condition=Q(fee_structure__isnull=False),
                name="unique_grant_one_fee",
            ),
        ]
        indexes = [
            models.Index(fields=["enrollment", "is_active"]),
            # [PERF] concession list `grants_count` (active grants per concession)
            models.Index(fields=["concession", "is_active"], name="feegrant_conc_active_idx"),
        ]

    def __str__(self):
        return f"{self.concession.name} -> {self.enrollment}"


class FeeInvoiceCounter(CampusBaseModel):
    """FINANCE — gap-free invoice number sequence per (campus, session); row-locked on use."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="+")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="+")
    last_no = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [UniqueConstraint(fields=["campus", "session"], name="unique_invoice_counter")]


class FeeReconciliationRun(CampusBaseModel):
    """FINANCE — one reconciliation pass (a Razorpay CSV import, an API pull, or the internal
    consistency check). Immutable record; its items are what a human works through."""

    class Source(models.TextChoices):
        RAZORPAY_CSV = "razorpay_csv", "Razorpay CSV report"
        RAZORPAY_API = "razorpay_api", "Razorpay API"
        INTERNAL = "internal", "Internal checks"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="fee_reconciliation_runs")
    source = models.CharField(max_length=15, choices=Source.choices)
    period_from = models.DateField(null=True, blank=True)
    period_to = models.DateField(null=True, blank=True)
    file_name = models.CharField(max_length=200, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["campus", "-created_at"])]


class FeeReconciliationItem(CampusBaseModel):
    """FINANCE — one finding of a run (see campus/finance_recon.py for the categories)."""

    class Resolution(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"
        IGNORED = "ignored", "Ignored"
        AUTO = "auto", "Matched"
        SUPERSEDED = "superseded", "Superseded by a newer run"

    run = models.ForeignKey(FeeReconciliationRun, on_delete=models.CASCADE, related_name="items")
    category = models.CharField(max_length=30, db_index=True)
    severity = models.CharField(max_length=10)
    suggested_action = models.CharField(max_length=15, default="none")
    detail = models.CharField(max_length=300, blank=True)
    payment = models.ForeignKey(FeePayment, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    invoice = models.ForeignKey(FeeInvoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    gateway_order_id = models.CharField(max_length=100, blank=True, db_index=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True, db_index=True)
    our_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    gateway_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    our_status = models.CharField(max_length=15, blank=True)
    gateway_status = models.CharField(max_length=15, blank=True)
    gateway_method = models.CharField(max_length=20, blank=True)
    gateway_fee = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    gateway_tax = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    settlement_id = models.CharField(max_length=60, blank=True)
    resolution = models.CharField(max_length=12, choices=Resolution.choices, default=Resolution.OPEN, db_index=True)
    resolution_action = models.CharField(max_length=20, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-run__created_at", "severity", "category"]
        indexes = [models.Index(fields=["run", "resolution"])]


class CampusAnalyticsSnapshot(CampusBaseModel):
    """Pre-computed cache for admin/HOD dashboards, filled by a Celery
    periodic task — never written from a request-time view."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="analytics_snapshots")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="analytics_snapshots")
    computed_at = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict, blank=True)  # attendance-trend, subject-wise avg, teacher workload, etc.

    class Meta:
        ordering = ["-computed_at"]
        indexes = [models.Index(fields=["campus", "-computed_at"])]

    def __str__(self):
        return f"{self.campus.name} snapshot @ {self.computed_at}"


# ---------------------------------------------------------------------------
# [T4 §B] Audit log — append-only record of every admin-side structural /
# assignment / roster action (who did what, when). Never edited or deleted
# through the API; `actor` is SET_NULL so removing a user keeps history.
# ---------------------------------------------------------------------------
class CampusAuditLog(CampusBaseModel):
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="audit_logs")
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_audit_logs")
    action = models.CharField(max_length=60, db_index=True)  # e.g. "section.create", "enrollment.transfer"
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["campus", "-created_at"]), models.Index(fields=["campus", "action"])]

    def __str__(self):
        return f"{self.campus_id} {self.action} {self.target_type}:{self.target_id}"


# ---------------------------------------------------------------------------
# T4 §G — idempotency log for the weekly-timetable "class starts soon" reminder
# ---------------------------------------------------------------------------

class TimetableReminderLog(CampusBaseModel):
    """
    One row per (timetable entry, calendar date) once the "class starts in
    N minutes" reminder has been fanned out for that occurrence. A weekly
    `TimetableEntry` repeats forever, so unlike `CampusLiveSession`
    (which carries its own `reminder_sent_at`) there is no single row to
    stamp — this log is the dedupe key the every-minute beat task claims
    with `get_or_create` before sending anything. Pure bookkeeping; safe
    to prune after a few days.
    """

    entry = models.ForeignKey(TimetableEntry, on_delete=models.CASCADE, related_name="reminder_logs")
    occurrence_date = models.DateField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            UniqueConstraint(fields=["entry", "occurrence_date"], name="unique_timetable_reminder_per_day"),
        ]
        indexes = [models.Index(fields=["occurrence_date"])]

    def __str__(self):
        return f"{self.entry_id} @ {self.occurrence_date}"


# ---------------------------------------------------------------------------
# Substitute teacher — absent teacher -> per-period substitute for ONE date
# ---------------------------------------------------------------------------

class TeacherAbsence(CampusBaseModel):
    """
    A staff member is away for `date_from..date_to` (inclusive). Pure
    bookkeeping + the anchor the substitute workflow hangs off: every weekly
    `TimetableEntry` of `staff` that falls on one of those dates is a period
    that needs cover (see `campus.substitutes.periods_for_absence`).

    `reason` is staff-only (admin/principal/the teacher) — it is never put in
    anything a student or parent can read (timetable overlay, push).

    Never hard-deleted from the API: `is_active=False` = cancelled (the
    teacher turned up after all); cancelling also cancels the substitutions
    that were created for it.
    """

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="teacher_absences")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="absences")
    date_from = models.DateField()
    date_to = models.DateField()
    reason = models.CharField(max_length=200, blank=True)
    marked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_teacher_absences_marked"
    )
    # Which part of each day the teacher is away. `full` = whole day (default,
    # the old behaviour); a half-day leave records `first_half` / `second_half`
    # (periods starting before / at-or-after `substitutes.HALF_DAY_SPLIT`).
    class Part(models.TextChoices):
        FULL = "full", "Full day"
        FIRST_HALF = "first_half", "First half"
        SECOND_HALF = "second_half", "Second half"

    part = models.CharField(max_length=12, choices=Part.choices, default=Part.FULL)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_from", "-created_at"]
        indexes = [
            models.Index(fields=["campus", "date_from", "date_to"], name="teacher_absence_range_idx"),
            models.Index(fields=["staff", "is_active"], name="teacher_absence_staff_idx"),
        ]
        constraints = [
            CheckConstraint(condition=Q(date_to__gte=models.F("date_from")), name="teacher_absence_range_valid"),
        ]

    def __str__(self):
        return f"{self.staff_id} absent {self.date_from}..{self.date_to}"


class SubstituteAssignment(CampusBaseModel):
    """
    ONE period (`entry`, a weekly `TimetableEntry`) on ONE calendar `date`
    taught by `substitute` instead of the timetable's own teacher. The weekly
    timetable itself is never edited — a substitution is an overlay that
    exists for that date only, so next week the original teacher is back
    without anyone having to undo anything.

    `original_staff` is a snapshot of `entry.staff` at assignment time (the
    timetable row may be re-assigned later; history should still say who was
    away). `absence` links back to the `TeacherAbsence` that triggered it
    (nullable: an admin may also arrange cover without a recorded absence,
    e.g. a teacher called in at 7 am).

    Only one ACTIVE substitution per (entry, date) — enforced by a partial
    unique constraint, so two admins racing to cover the same period cannot
    both win. Cancelled rows stay as history (`status=cancelled`).
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="substitute_assignments")
    entry = models.ForeignKey(TimetableEntry, on_delete=models.CASCADE, related_name="substitutions")
    date = models.DateField()
    substitute = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="substitute_duties")
    original_staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="substituted_out")
    absence = models.ForeignKey(
        TeacherAbsence, on_delete=models.SET_NULL, null=True, blank=True, related_name="substitutions"
    )
    assigned_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_substitutes_assigned"
    )
    note = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["date", "entry__time_slot__start_time", "created_at"]
        indexes = [
            models.Index(fields=["campus", "date"], name="substitute_campus_date_idx"),
            models.Index(fields=["substitute", "date"], name="substitute_staff_date_idx"),
            models.Index(fields=["entry", "date"], name="substitute_entry_date_idx"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["entry", "date"],
                condition=Q(status="active"),
                name="unique_active_substitute_per_period",
            ),
        ]

    def __str__(self):
        return f"{self.entry_id} on {self.date}: {self.substitute_id} for {self.original_staff_id} ({self.status})"


# ---------------------------------------------------------------------------
# T4 §F — doubts (student -> that subject-class's teachers)
# ---------------------------------------------------------------------------

class CampusDoubt(CampusBaseModel):
    """
    A student's question inside ONE subject-class — i.e. one
    `(section, subject)` pair that has an approved
    `SubjectTeacherassigments` (the same unit `GET /campus/my/classes/`
    hands the student). Scoped to `campus` + `session` like every other
    table in this app so a session rollover never mixes years.

    Visibility (decision D4): private by default — the author, that
    subject's approved teacher(s), the section's class-teacher and campus
    admin/principal. `is_public` (only ever settable when
    `Campus.doubts_public_allowed`) additionally shows it, read-only, to
    the other students of the same section. Never `hard delete`d —
    `is_active=False` hides it (history/audit).
    """

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        ANSWERED = "answered", "Answered"
        RESOLVED = "resolved", "Resolved"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="doubts")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="doubts")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="doubts")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="doubts")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_doubts")
    text = models.TextField()
    attachment = models.FileField(
        upload_to="campus/doubts/%Y/%m/", null=True, blank=True,
        validators=[attachment_extension_validator, validate_attachment_size],
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    is_public = models.BooleanField(default=False)
    resolved_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_doubts_resolved"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["campus", "session", "-created_at"]),
            models.Index(fields=["section", "subject", "status"]),
            models.Index(fields=["author", "-created_at"]),
        ]

    def __str__(self):
        return f"Doubt({self.subject_id}/{self.section_id}) by {self.author_id} [{self.status}]"


class CampusDoubtReply(CampusBaseModel):
    """A reply on a `CampusDoubt` — from the author (follow-up) or from
    one of that class's teachers. `is_staff_reply` is a snapshot taken
    when the reply is written (so a later role change never rewrites who
    "answered"); it is what flips the doubt to `ANSWERED`."""

    doubt = models.ForeignKey(CampusDoubt, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_doubt_replies")
    text = models.TextField()
    attachment = models.FileField(
        upload_to="campus/doubts/%Y/%m/", null=True, blank=True,
        validators=[attachment_extension_validator, validate_attachment_size],
    )
    is_staff_reply = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["doubt", "created_at"])]

    def __str__(self):
        return f"Reply({self.doubt_id}) by {self.author_id}"



# ---------------------------------------------------------------------------
# Parents <-> teachers: PTM scheduling + rules-based chat
# (rules live in `campus.parent_comms`, `campus.ptm`, `campus.parent_chat`)
# ---------------------------------------------------------------------------

class ParentTeacherSettings(CampusBaseModel):
    """Per-campus knobs for parent<->teacher communication. One row per campus,
    created lazily with the defaults below (`parent_comms.get_settings`). Edited
    by campus admin/principal only. Everything a school may reasonably want to
    tune is here so no rule is hard-coded in a view."""

    campus = models.OneToOneField(Campus, on_delete=models.CASCADE, related_name="parent_teacher_settings")

    # --- chat rules -------------------------------------------------------
    chat_enabled = models.BooleanField(default=True)
    # Teachers may only SEND inside this window (campus local time). Parents may
    # always send; outside the window their message is stored but nobody is
    # buzzed (it is waiting for the teacher in the morning).
    chat_start_time = models.TimeField(default=dt_time(8, 0))
    chat_end_time = models.TimeField(default=dt_time(20, 0))
    # Max messages one side may send in a row without a reply from the other.
    chat_max_unanswered = models.PositiveSmallIntegerField(default=3)
    chat_message_max_chars = models.PositiveSmallIntegerField(default=1000)

    # --- PTM rules --------------------------------------------------------
    ptm_enabled = models.BooleanField(default=True)
    # A parent can't book a slot that starts in less than this many minutes.
    ptm_booking_cutoff_minutes = models.PositiveIntegerField(default=60)
    # A parent can't cancel / reschedule a booking within this many minutes of
    # its start (staff always can).
    ptm_cancel_cutoff_minutes = models.PositiveIntegerField(default=120)
    # Active bookings one parent may hold in ONE event (all children together).
    ptm_max_bookings_per_parent = models.PositiveSmallIntegerField(default=6)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "parent-teacher settings"

    def __str__(self):
        return f"ParentTeacherSettings({self.campus_id})"


class PTMEvent(CampusBaseModel):
    """One parent-teacher-meeting day. Admin/principal creates it (a `DRAFT`),
    teachers open their slots inside `start_time..end_time`, then it is
    `PUBLISHED` (parents are told and can book). `CLOSED` = booking stopped but
    existing bookings stand; `CANCELLED` = the day is off (every booking is
    cancelled and parents told)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="ptm_events")
    title = models.CharField(max_length=120)
    description = models.CharField(max_length=500, blank=True)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField(default=10)
    mode = models.CharField(max_length=10, choices=ClassMode.choices, default=ClassMode.OFFLINE)
    venue = models.CharField(max_length=200, blank=True)
    # Which sections' parents are invited. `all_sections=True` = the whole campus.
    all_sections = models.BooleanField(default=True)
    sections = models.ManyToManyField(Section, blank=True, related_name="ptm_events")
    booking_opens_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="ptm_events_created")
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["campus", "date"], name="ptm_event_campus_date_idx"),
            models.Index(fields=["campus", "status"], name="ptm_event_campus_status_idx"),
        ]
        constraints = [
            CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="ptm_event_window_valid"),
        ]

    def __str__(self):
        return f"PTM {self.title} {self.date} ({self.status})"


class PTMSlot(CampusBaseModel):
    """One bookable meeting window of ONE teacher inside a `PTMEvent`. Slots are
    generated from the teacher's availability window (`ptm.open_slots`); a
    teacher can block a slot (lunch, a call) without deleting history."""

    event = models.ForeignKey(PTMEvent, on_delete=models.CASCADE, related_name="slots")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="ptm_slots")
    start_time = models.TimeField()
    end_time = models.TimeField()
    location = models.CharField(max_length=200, blank=True)
    is_blocked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["start_time", "staff_id"]
        indexes = [models.Index(fields=["event", "staff"], name="ptm_slot_event_staff_idx")]
        constraints = [
            UniqueConstraint(fields=["event", "staff", "start_time"], name="ptm_unique_slot_per_teacher_time"),
            CheckConstraint(condition=Q(end_time__gt=models.F("start_time")), name="ptm_slot_window_valid"),
        ]

    def __str__(self):
        return f"{self.event_id} {self.staff_id} {self.start_time}-{self.end_time}"


class PTMBooking(CampusBaseModel):
    """A parent's booking of ONE slot for ONE child. `event`/`campus`/`staff` are
    denormalised copies of the slot's, so the uniqueness rules below are plain
    DB constraints (two parents racing for the same slot cannot both win)."""

    class Status(models.TextChoices):
        BOOKED = "booked", "Booked"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No show"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="ptm_bookings")
    event = models.ForeignKey(PTMEvent, on_delete=models.CASCADE, related_name="bookings")
    slot = models.ForeignKey(PTMSlot, on_delete=models.CASCADE, related_name="bookings")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="ptm_bookings")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ptm_bookings")
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="ptm_bookings_as_parent")
    # what the parent wants to talk about (visible to the teacher)
    topic = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BOOKED, db_index=True)

    cancelled_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="ptm_bookings_cancelled")
    cancelled_by_role = models.CharField(max_length=10, blank=True)  # "parent" | "staff"
    cancel_reason = models.CharField(max_length=200, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    # outcome, written by the teacher after the meeting
    summary_for_parent = models.TextField(blank=True)  # parent can read
    private_note = models.TextField(blank=True)        # teacher/admin only — NEVER serialised to parents
    completed_at = models.DateTimeField(null=True, blank=True)

    rescheduled_from = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="rescheduled_to")
    reminder_24h_sent_at = models.DateTimeField(null=True, blank=True)
    reminder_1h_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["event__date", "slot__start_time", "created_at"]
        indexes = [
            models.Index(fields=["parent", "status"], name="ptm_booking_parent_idx"),
            models.Index(fields=["staff", "status"], name="ptm_booking_staff_idx"),
            models.Index(fields=["event", "status"], name="ptm_booking_event_idx"),
        ]
        constraints = [
            # a slot holds at most ONE live (not cancelled) booking
            UniqueConstraint(
                fields=["slot"],
                condition=Q(status__in=["booked", "completed", "no_show"]),
                name="ptm_one_live_booking_per_slot",
            ),
            # one live booking per child per teacher per event
            UniqueConstraint(
                fields=["event", "student", "staff"],
                condition=Q(status__in=["booked", "completed", "no_show"]),
                name="ptm_one_live_booking_per_student_teacher",
            ),
        ]

    def __str__(self):
        return f"PTMBooking({self.slot_id}) parent={self.parent_id} student={self.student_id} [{self.status}]"


class ParentChatThread(CampusBaseModel):
    """ONE conversation between ONE linked parent and ONE teacher ABOUT ONE
    child. Not a free-form DM: who may talk, when, and how much is decided by
    `campus.parent_chat` (and the campus's `ParentTeacherSettings`)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="parent_chat_threads")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_chat_threads_as_student")
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_chat_threads_as_parent")
    teacher = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="parent_chat_threads")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    closed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="parent_chat_threads_closed")
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=200, blank=True)

    # a report from either side; admin/principal reviews flagged threads
    flagged_at = models.DateTimeField(null=True, blank=True, db_index=True)
    flagged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="parent_chat_threads_flagged")
    flag_reason = models.CharField(max_length=300, blank=True)

    parent_last_read_at = models.DateTimeField(null=True, blank=True)
    teacher_last_read_at = models.DateTimeField(null=True, blank=True)
    parent_muted = models.BooleanField(default=False)  # no push for the parent; bell row still arrives
    teacher_muted = models.BooleanField(default=False)

    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_preview = models.CharField(max_length=120, blank=True)
    last_sender_role = models.CharField(max_length=10, blank=True)  # "parent" | "teacher"
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="parent_chat_threads_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["campus", "status"], name="pchat_thread_campus_status_idx"),
            models.Index(fields=["parent", "-last_message_at"], name="pchat_thread_parent_idx"),
            models.Index(fields=["teacher", "-last_message_at"], name="pchat_thread_teacher_idx"),
        ]
        constraints = [
            UniqueConstraint(fields=["campus", "student", "parent", "teacher"], name="pchat_unique_thread"),
        ]

    def __str__(self):
        return f"ParentChat({self.parent_id}<->{self.teacher_id} about {self.student_id}) [{self.status}]"


class ParentChatMessage(CampusBaseModel):
    """Text only, by design (no media, no links — see `parent_chat.validate_body`)."""

    class Role(models.TextChoices):
        PARENT = "parent", "Parent"
        TEACHER = "teacher", "Teacher"

    thread = models.ForeignKey(ParentChatThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_chat_messages")
    sender_role = models.CharField(max_length=10, choices=Role.choices)
    body = models.TextField()
    # sent outside the campus chat window (stored silently, nobody was pushed)
    after_hours = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["thread", "created_at"], name="pchat_msg_thread_idx")]

    def __str__(self):
        return f"ParentChatMessage({self.thread_id}) by {self.sender_id}"


class CampusHoliday(CampusBaseModel, SoftDeleteModel):
    """A day the campus is closed. It never counts as a leave day (a leave that
    spans it is shorter by one). One row per (campus, date); admin/principal
    manage them through `/campus/holidays/`."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="holidays")
    date = models.DateField(db_index=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["date"]
        constraints = [UniqueConstraint(fields=["campus", "date"], condition=Q(is_deleted=False), name="campus_holiday_unique_date_alive")]

    def __str__(self):
        return f"{self.campus_id} {self.date} {self.name}"


class CampusProfile(CampusBaseModel):
    """School profile — the \"about this school\" card (board, affiliation, address, contact). One row per campus,
    created the first time an admin saves it; a campus without a row simply has an empty profile.
    The school's name and type stay on `Campus` itself."""

    class Board(models.TextChoices):
        CBSE = "cbse", "CBSE"
        ICSE = "icse", "ICSE / ISC"
        STATE = "state", "State board"
        IB = "ib", "IB"
        CAMBRIDGE = "cambridge", "Cambridge (IGCSE)"
        UNIVERSITY = "university", "University / college"
        OTHER = "other", "Other"

    campus = models.OneToOneField(Campus, on_delete=models.CASCADE, related_name="profile")
    tagline = models.CharField(max_length=120, blank=True)
    board = models.CharField(max_length=12, choices=Board.choices, blank=True)
    affiliation_no = models.CharField(max_length=30, blank=True)
    udise_code = models.CharField(max_length=11, blank=True)
    principal_name = models.CharField(max_length=100, blank=True)
    established_year = models.PositiveSmallIntegerField(null=True, blank=True)
    address_line1 = models.CharField(max_length=150, blank=True)
    address_line2 = models.CharField(max_length=150, blank=True)
    city = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    pincode = models.CharField(max_length=10, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(max_length=200, blank=True)
    # School logo (png / jpg / webp, max 2 MB). Checked by `settings_rules.clean_logo_upload` (type from the file's
    # first bytes, not from its name); set / removed only through settings/<campus>/logo/.
    logo = models.FileField(upload_to="campus/logos/%Y/%m/", null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile of {self.campus_id}"


class StaffLeave(CampusBaseModel):
    """
    A staff member's leave APPLICATION: they apply (`pending`), a campus
    admin / principal approves or rejects it, and either side can cancel it
    while it is still useful. Rules live in `campus.staff_leave`.

    * `date_from..date_to` is inclusive; weekly-off days and `CampusHoliday`
      dates don't count (`working_days`). `is_half_day` is only valid for a single-day leave and counts 0.5 day.
    * `reason` is the applicant's own text — visible to the applicant and to
      admin / principal only (never to other staff, students or parents).
    * Approving a leave also records a `TeacherAbsence` for the same dates
      (`absence`), so the existing substitute-teacher flow picks it up with no
      extra step. Cancelling an approved leave cancels that absence (and the
      cover arranged for it). If an active absence already covered the dates,
      none is created and `absence` stays empty (we never touch an absence we
      didn't create).
    * Never hard-deleted: history stays (`status`, who decided and when).
    """

    class LeaveType(models.TextChoices):
        CASUAL = "casual", "Casual"
        SICK = "sick", "Sick"
        EARNED = "earned", "Earned"
        UNPAID = "unpaid", "Unpaid"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="staff_leaves")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="leaves")
    leave_type = models.CharField(max_length=10, choices=LeaveType.choices)
    date_from = models.DateField()
    date_to = models.DateField()
    is_half_day = models.BooleanField(default=False)

    class HalfDayPart(models.TextChoices):
        FIRST_HALF = "first_half", "First half"
        SECOND_HALF = "second_half", "Second half"

    # Only set when `is_half_day`; blank otherwise.
    half_day_part = models.CharField(max_length=12, choices=HalfDayPart.choices, blank=True)
    # Days this leave costs, frozen when it was applied / edited: calendar days
    # minus the campus's weekly-off days and holidays (0.5 for a half day).
    # 0 only on rows made before this field existed -> `days` falls back.
    working_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    reason = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # Set when an admin / principal applied FOR the staff member.
    applied_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_staff_leaves_applied_for"
    )
    edited_at = models.DateTimeField(null=True, blank=True)

    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_staff_leaves_decided"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)

    cancelled_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_staff_leaves_cancelled"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    absence = models.ForeignKey(
        TeacherAbsence, on_delete=models.SET_NULL, null=True, blank=True, related_name="leaves"
    )

    class Meta:
        ordering = ["-date_from", "-created_at"]
        indexes = [
            models.Index(fields=["campus", "status"], name="staff_leave_campus_status_idx"),
            models.Index(fields=["staff", "status"], name="staff_leave_staff_status_idx"),
            models.Index(fields=["campus", "date_from", "date_to"], name="staff_leave_range_idx"),
        ]
        constraints = [
            CheckConstraint(condition=Q(date_to__gte=models.F("date_from")), name="staff_leave_range_valid"),
            CheckConstraint(
                condition=Q(is_half_day=False) | Q(date_from=models.F("date_to")),
                name="staff_leave_half_day_single_date",
            ),
        ]

    @property
    def days(self):
        if self.working_days:
            return float(self.working_days)
        return 0.5 if self.is_half_day else float((self.date_to - self.date_from).days + 1)

    def __str__(self):
        return f"{self.staff_id} {self.leave_type} {self.date_from}..{self.date_to} [{self.status}]"


# ---------------------------------------------------------------------------
# 13. Class resources (content share) — subject-wise folders of PDF / video / ...
# ---------------------------------------------------------------------------

class ResourceFolder(CampusBaseModel):
    """
    A folder of class resources for ONE `(section, subject)` — e.g. Class 10-A /
    Maths. A folder may have sub-folders ONE level deep ("Chapter 3") and never
    deeper (kept shallow on purpose: phone UI, easy to browse).

    `campus` / `session` are derived server-side from the section's class, and
    a sub-folder always copies `section` / `subject` from its parent — none of
    them is client-supplied (same rule as `HomeworkDiaryEntry` / `CampusDoubt`).

    Never hard-deleted from the API: `is_active=False` hides the folder (and its
    resources) — history / audit stays. Names are unique among ACTIVE sibling
    folders only, so an archived name can be reused.

    Who may create / edit / post (enforced in the service/permission layer, not
    here): management, the section's class teacher, and a subject teacher whose
    `SubjectTeacherassigments` for this `(section, subject)` is approved — the
    same rule as `CampusAccess.canMarkAttendance`.
    """

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="resource_folders")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="resource_folders")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="resource_folders")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="resource_folders")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=120)
    order = models.PositiveSmallIntegerField(default=0)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_resource_folders_created"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "name"]
        indexes = [
            models.Index(fields=["section", "subject", "is_active"], name="res_folder_sec_subj_idx"),
            models.Index(fields=["parent", "is_active"], name="res_folder_parent_idx"),
            models.Index(fields=["campus", "session"], name="res_folder_campus_sess_idx"),
        ]
        constraints = [
            # NULL parents never collide in a plain unique constraint, hence two conditional ones.
            UniqueConstraint(
                fields=["section", "subject", "name"],
                condition=Q(parent__isnull=True, is_active=True),
                name="res_folder_unique_active_root",
            ),
            UniqueConstraint(
                fields=["parent", "name"],
                condition=Q(parent__isnull=False, is_active=True),
                name="res_folder_unique_active_child",
            ),
            CheckConstraint(condition=~Q(parent=models.F("id")), name="res_folder_not_own_parent"),
        ]

    def __str__(self):
        return f"{self.name} ({self.section_id}/{self.subject_id})"

    def _sync_scope(self):
        """Derive scope fields (never trusted from the client)."""
        if self.parent_id:
            parent = self.parent
            self.section_id = parent.section_id
            self.subject_id = parent.subject_id
        if self.section_id:
            school_class = self.section.school_class
            self.campus_id = school_class.campus_id
            self.session_id = school_class.session_id

    def clean(self):
        super().clean()
        self._sync_scope()
        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": "A folder cannot be its own parent."})
            if self.parent.parent_id:
                raise ValidationError({"parent": "Folders can be nested only one level deep."})
        if self.subject_id and self.campus_id and self.subject.campus_id != self.campus_id:
            raise ValidationError({"subject": "Subject does not belong to this campus."})

    def save(self, *args, **kwargs):
        self._sync_scope()
        super().save(*args, **kwargs)


class ClassResource(CampusBaseModel):
    """
    One shared study resource inside a `ResourceFolder`: an uploaded file (PDF,
    document, image, video) OR an external link (YouTube / Drive / website) —
    exactly one of `file` / `external_url` (DB check constraint).

    `kind` says what it is for the UI (icon, viewer): a YouTube video is
    `kind=video` + `external_url`; an uploaded worksheet is `kind=pdf` + `file`.
    `kind=link` is a plain website link and needs `external_url`. For uploads
    `kind` must match the file extension (`resource_rules.kind_for_filename`;
    serializers can pre-fill it with `ClassResource.guess_kind`).

    `campus` / `session` / `section` / `subject` are copied from the folder on
    every save (denormalised only so the students' "all resources for my
    section" query and the permission checks need no join). Never client-supplied.

    Readers (enforced in the service/permission layer): students enrolled in the
    section, parents linked to them, and the campus staff allowed to see that
    section. `is_published=False` is a draft — visible only to the poster,
    management and the section's class teacher. Never hard-deleted from the API:
    `is_active=False` hides it.

    Video note: files are stored as uploaded. A transcode (H.264 / faststart,
    thumbnail, `duration_seconds`) is a later step that fills `thumbnail` and
    `duration_seconds`; the model already has room for both.
    """

    class Kind(models.TextChoices):
        PDF = resource_rules.KIND_PDF, "PDF"
        VIDEO = resource_rules.KIND_VIDEO, "Video"
        DOCUMENT = resource_rules.KIND_DOCUMENT, "Document"
        IMAGE = resource_rules.KIND_IMAGE, "Image"
        LINK = resource_rules.KIND_LINK, "Link"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="class_resources")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="class_resources")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="class_resources")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="class_resources")
    folder = models.ForeignKey(ResourceFolder, on_delete=models.CASCADE, related_name="resources")
    posted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_class_resources_posted"
    )

    kind = models.CharField(max_length=10, choices=Kind.choices, db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    file = models.FileField(
        upload_to="campus/resources/%Y/%m/", blank=True,
        validators=[resource_rules.resource_extension_validator, resource_rules.validate_resource_size],
    )
    external_url = models.URLField(max_length=500, blank=True, validators=[URLValidator(schemes=["http", "https"])])
    thumbnail = models.FileField(
        upload_to="campus/resources/thumbs/%Y/%m/", blank=True,
        validators=[resource_rules.thumbnail_extension_validator, resource_rules.validate_thumbnail_size],
    )
    # Filled from the upload on save (for display / "12 MB" label); 0 for links.
    original_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    is_published = models.BooleanField(default=True)
    is_pinned = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    # Push bookkeeping: set (atomically, by `tasks.send_class_resource_posted`)
    # the moment the "new resource" push is claimed. A draft that is published
    # later is pushed once then; a resource is never pushed twice. Never
    # client-supplied (not in the serializer).
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "order", "-created_at"]
        indexes = [
            models.Index(fields=["folder", "is_active", "-created_at"], name="res_folder_active_idx"),
            models.Index(fields=["section", "subject", "-created_at"], name="res_sec_subj_idx"),
            models.Index(fields=["campus", "session", "-created_at"], name="res_campus_sess_idx"),
        ]
        constraints = [
            # exactly one source: an uploaded file XOR an external link
            CheckConstraint(
                condition=(~Q(file="") & Q(external_url="")) | (Q(file="") & ~Q(external_url="")),
                name="class_resource_file_xor_link",
            ),
            CheckConstraint(
                condition=~Q(kind="link") | ~Q(external_url=""),
                name="class_resource_link_has_url",
            ),
        ]

    def __str__(self):
        return f"{self.title} [{self.kind}] ({self.folder_id})"

    @staticmethod
    def guess_kind(filename):
        """Kind for an uploaded file name (None if the extension isn't allowed)."""
        return resource_rules.kind_for_filename(filename)

    def _sync_scope(self):
        folder = self.folder
        self.section_id = folder.section_id
        self.subject_id = folder.subject_id
        self.campus_id = folder.campus_id
        self.session_id = folder.session_id

    def clean(self):
        super().clean()
        if self.folder_id:
            self._sync_scope()
        has_file = bool(self.file)
        has_url = bool(self.external_url)
        if has_file == has_url:
            raise ValidationError("Add either a file or a link — exactly one.")
        if self.kind == self.Kind.LINK and not has_url:
            raise ValidationError({"external_url": "A link resource needs a URL."})
        if has_file:
            guessed = self.guess_kind(self.file.name)
            if guessed and self.kind != guessed:
                raise ValidationError({"kind": f"This file is a {guessed}, not a {self.kind}."})

    def save(self, *args, **kwargs):
        self._sync_scope()
        if self.file and not getattr(self.file, "_committed", True):
            # a fresh upload: remember its name + size for the UI
            self.file_size = getattr(self.file, "size", 0) or 0
            self.original_name = os.path.basename(self.file.name)[:255]
        elif not self.file:
            self.file_size = 0
        super().save(*args, **kwargs)


class ClassResourceView(CampusBaseModel):
    """
    "Who opened this resource" — one row per `(resource, user)`, bumped on every
    open. Lets a teacher see how many students opened a PDF / video. Written by
    the open/download endpoint only (never by the client directly); counting is
    done with an aggregate, there is no counter on `ClassResource` (no races).
    """

    resource = models.ForeignKey(ClassResource, on_delete=models.CASCADE, related_name="views")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_resource_views")
    view_count = models.PositiveIntegerField(default=1)
    first_viewed_at = models.DateTimeField(auto_now_add=True)
    last_viewed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_viewed_at"]
        indexes = [models.Index(fields=["resource", "-last_viewed_at"], name="res_view_resource_idx")]
        constraints = [UniqueConstraint(fields=["resource", "user"], name="res_view_unique_per_user")]

    def __str__(self):
        return f"{self.user_id} opened {self.resource_id} x{self.view_count}"


# ---------------------------------------------------------------------------
# 14. Class notes (teacher) + private student notes — campus/notes.py
# ---------------------------------------------------------------------------

class ClassNote(CampusBaseModel):
    """
    A teacher's NOTE for a subject-class: typed text and/or one attached file
    (PDF / image / document), optionally tied to ONE class (`live_session`) so it
    shows up next to that class's recording.

    Lighter than a `ClassResource` on purpose — no folders: a note is "what we did
    in today's class". Use class resources for the permanent library.

    `campus` / `section` / `subject` are always derived from the live session when
    one is given (never client-supplied then). Readers: students of the section,
    linked parents and the campus staff allowed to see the section; a DRAFT
    (`is_published=False`) only its poster and people who manage the subject.
    Never hard-deleted from the API (`is_active=False`).
    """

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="class_notes")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="class_notes")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="class_notes")
    live_session = models.ForeignKey(
        CampusLiveSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="class_notes"
    )
    posted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_class_notes_posted"
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    file = models.FileField(
        upload_to="campus/notes/%Y/%m/", blank=True,
        validators=[resource_rules.resource_extension_validator, resource_rules.validate_resource_size],
    )
    original_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    is_pinned = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Claimed by the "new note" push task (same at-most-once idea as ClassResource.notified_at).
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["section", "subject", "-created_at"], name="cnote_sec_subj_idx"),
            models.Index(fields=["live_session", "-created_at"], name="cnote_live_idx"),
        ]
        constraints = [
            CheckConstraint(condition=~Q(body="") | ~Q(file=""), name="class_note_has_content"),
        ]

    def __str__(self):
        return f"Note({self.title}) {self.section_id}/{self.subject_id}"

    def _sync_scope(self):
        if self.live_session_id:
            live = self.live_session
            self.section_id = live.section_id
            self.subject_id = live.subject_id
            self.campus_id = live.section.school_class.campus_id
        elif self.section_id:
            self.campus_id = self.section.school_class.campus_id

    def save(self, *args, **kwargs):
        self._sync_scope()
        if self.file and not getattr(self.file, "_committed", True):
            self.file_size = getattr(self.file, "size", 0) or 0
            self.original_name = os.path.basename(self.file.name)[:255]
        elif not self.file:
            self.file_size = 0
        super().save(*args, **kwargs)


class StudentNote(CampusBaseModel):
    """
    A student's PRIVATE note on a subject (optionally on one recorded class, with
    the second of the video it was written at). Visible to its author ONLY — not to
    teachers, parents or admins: it is the student's own scratch pad. Soft-deleted.
    """

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_student_notes")
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="student_notes")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="student_notes")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="student_notes")
    live_session = models.ForeignKey(
        CampusLiveSession, on_delete=models.SET_NULL, null=True, blank=True, related_name="student_notes"
    )
    at_seconds = models.PositiveIntegerField(null=True, blank=True)
    text = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["at_seconds", "created_at"]
        indexes = [
            models.Index(fields=["author", "live_session", "at_seconds"], name="snote_author_live_idx"),
            models.Index(fields=["author", "subject", "-created_at"], name="snote_author_subj_idx"),
        ]

    def __str__(self):
        return f"StudentNote({self.author_id}) {self.subject_id}"


# ---------------------------------------------------------------------------
# SAFETY — consent log, data delete requests, campus backups.
# Rules: `campus/safety_rules.py` (pure) · DB logic: `consent.py`, `data_deletion.py`, `backup.py`.
# ---------------------------------------------------------------------------
class CampusConsentRecord(CampusBaseModel):
    """
    One row per consent decision — APPEND-ONLY (a change of mind is a NEW row, never an edit), so the log is
    usable as proof of "who agreed to what, when, under which policy text".

    `subject` is the person who gave / withdrew it; `student` is set when it is about a child (a parent giving
    `child_data` for one linked student). Users are SET_NULL so the row survives an account deletion (the user
    row itself is anonymised by `login.account_deletion`, never removed).
    """

    class Status(models.TextChoices):
        GRANTED = "granted", "Granted"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class Source(models.TextChoices):
        APP = "app", "In the app"
        ADMIN_ENTRY = "admin_entry", "Entered by campus staff"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="consent_records")
    subject = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_consents_given")
    student = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_consents_about")
    purpose = models.CharField(max_length=30, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    policy_version = models.CharField(max_length=20, blank=True)
    source = models.CharField(max_length=15, choices=Source.choices, default=Source.APP)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_consents_recorded")
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["campus", "-created_at"], name="consent_campus_time_idx"),
            models.Index(fields=["campus", "purpose", "status"], name="consent_campus_purpose_idx"),
            models.Index(fields=["subject", "purpose", "-created_at"], name="consent_subject_purpose_idx"),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("CampusConsentRecord rows are append-only and cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("CampusConsentRecord rows are append-only and cannot be deleted.")

    def __str__(self):
        return f"{self.campus_id} {self.purpose} {self.status} subject={self.subject_id}"


class CampusPrivacyPolicy(CampusBaseModel):
    """
    Per-campus switches for parent / child privacy (rules: `privacy_rules.py`, DB logic: `child_privacy.py`).
    One row per campus, created the first time an admin saves it; a campus without a row uses the defaults below
    (nothing enforced — existing campuses behave exactly as before).
    """

    campus = models.OneToOneField(Campus, on_delete=models.CASCADE, related_name="privacy_policy")
    # When ON, optional features (photo / media sharing, AI) may use a student's data only with a valid consent:
    # a MINOR needs the linked parents' consent, an adult their own. Core records are never blocked.
    enforce_guardian_consent = models.BooleanField(default=False)
    # A student with no date of birth on file: treat as a minor (the privacy-safe default) until the admin fills it.
    unknown_dob_is_minor = models.BooleanField(default=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Privacy policy of {self.campus_id}"


class CampusDataDeletionRequest(CampusBaseModel):
    """
    "Delete my / my child's data from this campus" — a parent, student or staff member asks, a campus
    admin / principal approves or rejects (with a note), then executes. Execution erases personal content and
    keeps the records the school must keep (attendance, results, fees); see `campus/data_deletion.py`.

    `due_at` is the promised answer date (`CAMPUS_DELETION_SLA_DAYS`, default 30 days). One OPEN
    (pending / approved) request per person per campus.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Kind(models.TextChoices):
        MY_DATA = "my_data", "My own data"
        CHILD_DATA = "child_data", "My child's data"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="data_deletion_requests")
    requester = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_deletion_requests")
    subject = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_deletion_subject_of")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    reason = models.CharField(max_length=500, blank=True)
    wants_account_deletion = models.BooleanField(default=False)  # asks for the whole LearnScroll account too
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    due_at = models.DateTimeField(db_index=True)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_deletion_reviewed")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=500, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)  # {"erased": {...}, "retained": [...], ...}
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["campus", "status", "-created_at"], name="delreq_campus_status_idx")]
        constraints = [
            UniqueConstraint(
                fields=["campus", "subject"],
                condition=Q(status__in=["pending", "approved"]),
                name="unique_open_deletion_request",
            )
        ]

    def __str__(self):
        return f"DeletionRequest({self.campus_id}, subject={self.subject_id}) [{self.status}]"


def _campus_backup_path(instance, filename):
    # `campus/` is a private media prefix; the file is never served by URL anyway (download is an
    # authenticated view that streams it), the random name just keeps it unguessable on disk.
    return f"campus/backups/{timezone.now():%Y/%m}/{uuid.uuid4().hex}.json.gz"


class CampusBackup(CampusBaseModel):
    """
    A gzip-JSON snapshot of one campus's setup (classes, sections, staff, timetable, enrollments, parent
    links) and optionally its records (attendance, marks, fees). Contains ids of users, not user profiles.
    See `campus/backup.py` for what is in it and how a restore behaves (non-destructive by default).
    """

    class Kind(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULED = "scheduled", "Scheduled"
        PRE_RESTORE = "pre_restore", "Taken before a restore"

    class Status(models.TextChoices):
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="backups")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_backups_created")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.MANUAL)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.READY)
    groups = models.JSONField(default=list, blank=True)  # ["setup"] or ["setup", "records"]
    file = models.FileField(upload_to=_campus_backup_path, null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    row_counts = models.JSONField(default=dict, blank=True)  # {"campus.section": 12, ...}
    note = models.CharField(max_length=200, blank=True)
    error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["campus", "-created_at"], name="backup_campus_time_idx")]

    def __str__(self):
        return f"CampusBackup({self.campus_id}, {self.kind}, {self.status}, {self.created_at})"


# ---------------------------------------------------------------------------
# Admission — enquiry -> application -> interview -> offer -> enrolled
# ---------------------------------------------------------------------------

class AdmissionCounter(CampusBaseModel):
    """ADMISSION — gap-free application number sequence per (campus, session); row-locked on use."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="+")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="+")
    last_no = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [UniqueConstraint(fields=["campus", "session"], name="unique_admission_counter")]


class AdmissionApplication(CampusBaseModel):
    """
    One child's admission journey at a campus (rules: `campus.admission`).

        enquiry -> applied -> interview -> offered -> enrolled
                       +-> waitlisted -+      +-> rejected / withdrawn

    * Created by campus admin / principal (walk-in, phone, CSV import). Parents
      have no account yet, so nothing here is visible to students / parents.
    * `enrolled` is reached ONLY through `admission.enroll()` — it links a real
      student account, creates the `StudentEnrollment` and the admission-fee
      invoices. Never hard-deleted: history stays.
    * `remarks` are staff-only notes; never put in a push body.
    """

    class Status(models.TextChoices):
        ENQUIRY = "enquiry", "Enquiry"
        APPLIED = "applied", "Applied"
        INTERVIEW = "interview", "Interview / test"
        WAITLISTED = "waitlisted", "Waitlisted"
        OFFERED = "offered", "Seat offered"
        ENROLLED = "enrolled", "Enrolled"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    class Source(models.TextChoices):
        WALK_IN = "walk_in", "Walk-in"
        PHONE = "phone", "Phone"
        REFERRAL = "referral", "Referral"
        ONLINE = "online", "Online / social"
        IMPORT = "import", "Imported"
        OTHER = "other", "Other"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="admission_applications")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="admission_applications")
    school_class = models.ForeignKey(
        SchoolClass, on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_applications"
    )
    # Section the seat is allotted in (optional until enrolment).
    section = models.ForeignKey(
        Section, on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_applications"
    )
    application_no = models.CharField(max_length=40, blank=True)  # ADM/2026-27/0001
    applicant_name = models.CharField(max_length=150)
    date_of_birth = models.DateField(null=True, blank=True)
    guardian_name = models.CharField(max_length=150, blank=True)
    guardian_phone = models.CharField(max_length=20, blank=True, db_index=True)
    guardian_email = models.EmailField(blank=True)
    previous_school = models.CharField(max_length=150, blank=True)
    address = models.CharField(max_length=300, blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.WALK_IN)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ENQUIRY, db_index=True)
    follow_up_on = models.DateField(null=True, blank=True)
    interview_on = models.DateTimeField(null=True, blank=True)
    remarks = models.CharField(max_length=500, blank=True)
    decision_note = models.CharField(max_length=200, blank=True)
    decided_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_admissions_decided"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    # Set by `admission.enroll()`.
    student = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_admission_applications"
    )
    enrollment = models.OneToOneField(
        StudentEnrollment, on_delete=models.SET_NULL, null=True, blank=True, related_name="admission_application"
    )
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="campus_admissions_created"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["campus", "session", "status"], name="admission_campus_status_idx"),
            models.Index(fields=["campus", "follow_up_on"], name="admission_followup_idx"),
        ]
        constraints = [
            UniqueConstraint(
                fields=["campus", "application_no"], condition=~Q(application_no=""),
                name="unique_admission_no_per_campus",
            ),
        ]

    def __str__(self):
        return f"{self.application_no or self.pk} {self.applicant_name} [{self.status}]"

