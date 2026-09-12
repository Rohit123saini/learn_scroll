# campus/models.py
"""
`campus` app — Smart Virtual School/College/Institute OS.
Implements campus_app_design.md phases 1-13.

GOLDEN RULE (unchanged from design doc): `campus` never imports
`liveclass` or `message` models directly. The only cross-app model FK in
this whole file is to `login.User` (identity is shared platform-wide,
same as `core.models.Notification` already does). Anything that needs
`liveclass`/`message` internals (video room provisioning, group-chat
auto-creation, parent OTP issuance) goes through `core.classroom_chat_bridge`
service functions from a signals.py/services.py layer — NOT from here.
That also means: no signal handlers in this file that import core/message/
liveclass. Auto-notify-on-schedule, auto-reminder Celery tasks etc. from
the design doc are service-layer concerns, not model-layer ones.

FEE MODULE — why it looks the way it does:

REVISED (this pass — FEE-2): the previous version of this docstring
said fee was "decoupled end to end from `User.coin`/`CoinLedger`" and
paid through a real Razorpay gateway. Product decision changed that:
Campus fee is now paid FROM the same `user_profile.CoinLedger`-backed
`User.coin` wallet that `liveclass` already uses for classes/passes —
no separate payment gateway for fee. `FeePayment.Mode.ONLINE` +
`RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` are gone from this flow
(those settings are untouched in settings.py because `liveclass`'s
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
import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import CheckConstraint, Q, UniqueConstraint

from login.models import User


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

class Campus(CampusBaseModel):
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

    # [Task 19 — ORG_VS_INDIVIDUAL_MATRIX] Future-proofing toggle only —
    # does NOT change any current behavior. `TestSeries.save()`
    # (testseries/models.py) still unconditionally forces `is_paid=False`
    # / `price_coins=0` for every `source="campus"` series regardless of
    # this flag's value — that force-free enforcement is the real
    # invariant today, same as `campus.bridge.create_testseries()` never
    # accepting an `is_paid`/`price_coins` kwarg at all. This field is
    # deliberately just an admin-visible, defaulted-off record of intent
    # ("could this campus ever be allowed to run paid test series") for
    # a future task to actually wire up — that future task would need to
    # (a) relax `TestSeries.save()`'s force-False for campus-sourced
    # series when the campus it belongs to has this set, and (b) add an
    # `is_paid`/`price_coins` kwarg to `campus.bridge.create_testseries()`
    # gated on it. Neither of those changes is made here. See
    # `docs/ORG_VS_INDIVIDUAL_MATRIX.md` for the full current
    # org-vs-individual paid/unpaid matrix across every source.
    testseries_paid_allowed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class AcademicSession(CampusBaseModel):
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


class Department(CampusBaseModel):
    """Optional — college-level grouping. Left un-session-scoped since
    departments don't get recreated every year the way class rosters do."""

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="departments")
    name = models.CharField(max_length=150)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.campus.name} - {self.name}"


class SchoolClass(CampusBaseModel):
    # Named SchoolClass, not Class, to avoid shadowing the Python keyword.
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="classes")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="classes")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField(max_length=100)  # "Class 10", "B.Sc 2nd Year"

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["session", "campus"])]

    def __str__(self):
        return f"{self.name} ({self.session.name})"


class Section(CampusBaseModel):
    school_class = models.ForeignKey(SchoolClass, on_delete=models.CASCADE, related_name="sections")
    name = models.CharField(max_length=20)  # "A", "B"

    class Meta:
        ordering = ["name"]
        constraints = [UniqueConstraint(fields=["school_class", "name"], name="unique_section_per_class")]

    def __str__(self):
        return f"{self.school_class} - {self.name}"


class Subject(CampusBaseModel):
    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="subjects")
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="subjects")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Room(CampusBaseModel):
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


class StaffProfile(CampusBaseModel):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        PRINCIPAL_HOD = "principal_hod", "Principal/HOD"
        CLASS_TEACHER = "class_teacher", "Class Teacher"
        SUBJECT_TEACHER = "subject_teacher", "Subject Teacher"
        NON_TEACHING = "non_teaching", "Non-Teaching Staff"

    campus = models.ForeignKey(Campus, on_delete=models.CASCADE, related_name="staff_profiles")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_staff_profiles")
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        # One user can hold a staff row at more than one campus — but only
        # one role-row per (campus, user) pair.
        ordering = ["id"]
        constraints = [UniqueConstraint(fields=["campus", "user"], name="unique_staff_profile_per_campus")]
        indexes = [models.Index(fields=["campus", "role"])]

    def __str__(self):
        return f"{self.user.username} @ {self.campus.name} ({self.role})"


class ClassTeacherAssignment(CampusBaseModel):
    section = models.OneToOneField(Section, on_delete=models.CASCADE, related_name="class_teacher_assignment")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="class_teacher_of")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.section} - {self.staff.user.username}"


class SubjectTeacherAssignment(CampusBaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="subject_teacher_assignments")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="teacher_assignments")
    staff = models.ForeignKey(StaffProfile, on_delete=models.CASCADE, related_name="subject_assignments")
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
            UniqueConstraint(fields=["section", "subject", "staff"], name="unique_subject_teacher_assignment")
        ]

    def __str__(self):
        return f"{self.staff.user.username} - {self.section} - {self.subject} ({self.status})"


class StudentEnrollment(CampusBaseModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        TRANSFERRED = "transferred", "Transferred"
        GRADUATED = "graduated", "Graduated"

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="campus_enrollments")
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="enrollments")
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="enrollments")
    roll_number = models.CharField(max_length=30, blank=True)
    # [ADDED — Task 11] Closes the GAP the unified `assignment` app's own
    # models.py flagged against this exact model: `AssignmentSubmission.
    # enrollment_no` (assignment app) was shipped as an always-blank
    # snapshot field because `StudentEnrollment` had no dedicated
    # `enrollment_no` of its own to snapshot — only `(student, section,
    # session)` as the de-facto enrollment key. This is the school's own
    # enrollment/admission number (distinct from `roll_number`, which is
    # section-scoped and can change every session) — blank-by-default so
    # existing rows don't need a backfill value to pass validation;
    # campuses that care about tracking it can start setting it going
    # forward, and `campus.bridge.create_assignment()` now has a real
    # value to snapshot into new `assignment.AssignmentSubmission` rows
    # instead of always passing `enrollment_no=""`.
    enrollment_no = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True)

    class Meta:
        ordering = ["-session__start_date"]
        constraints = [
            # A student gets exactly one row per section per session — new
            # year, new row; last year's row stays as read-only history.
            # Deliberately NOT unique across campuses/sessions overall —
            # see module docstring, open question §13.1.
            UniqueConstraint(fields=["student", "section", "session"], name="unique_enrollment_per_section_session")
        ]
        indexes = [models.Index(fields=["session", "status"])]

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


# ---------------------------------------------------------------------------
# 2. Notices
# ---------------------------------------------------------------------------

class Notice(CampusBaseModel):
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

    posted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="notices_posted")
    title = models.CharField(max_length=200)
    body = models.TextField()
    pin_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["campus", "-created_at"])]

    def __str__(self):
        return self.title


# ---------------------------------------------------------------------------
# 3. Live classes (coin-free)
# ---------------------------------------------------------------------------

class CampusLiveSession(CampusBaseModel):
    """Deliberately lightweight and coin-free — no pass/escrow/coin field
    exists on this model at all, by design, so it's structurally
    impossible for this to accidentally grow into a second marketplace.
    Video-room provisioning reuses `liveclass`/`message` infra only via
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

    class Meta:
        ordering = ["scheduled_at"]
        indexes = [models.Index(fields=["section", "scheduled_at"])]

    def __str__(self):
        return f"{self.subject} - {self.section} @ {self.scheduled_at}"


# ---------------------------------------------------------------------------
# 5. Smart timetable + attendance automation
# ---------------------------------------------------------------------------

class TimeSlot(CampusBaseModel):
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


class TimetableEntry(CampusBaseModel):
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

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["session", "time_slot"])]

    def clean(self):
        clashing = TimetableEntry.objects.filter(session=self.session, time_slot=self.time_slot).exclude(pk=self.pk)
        if clashing.filter(staff=self.staff).exists():
            raise ValidationError("This staff member already has an entry in this time slot.")
        if clashing.filter(section=self.section).exists():
            raise ValidationError("This section already has an entry in this time slot.")
        if self.room_id and clashing.filter(room=self.room).exists():
            raise ValidationError("This room is already booked in this time slot.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

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

    class Meta:
        ordering = ["-date"]
        constraints = [UniqueConstraint(fields=["enrollment", "date", "subject"], name="unique_attendance_per_day_subject")]
        indexes = [models.Index(fields=["enrollment", "date"])]

    def __str__(self):
        return f"{self.enrollment} - {self.date} ({self.status})"


# Note: attendance %-summary is a service function (compute_attendance_summary),
# not a model — same "don't store what you can cheaply recompute"
# reasoning as FeeInvoice.amount_paid below. The low-attendance alert is a
# Celery periodic task, not a model concern.


# ---------------------------------------------------------------------------
# 6. Homework / assignments + syllabus tracker
# ---------------------------------------------------------------------------

class Assignment(CampusBaseModel):
    """[DEPRECATED — Task 11] Superseded by the unified `assignment` app
    (`assignment.Assignment`, `source="campus"`, `context_type="section"`,
    `context_id=<this section's id>`). Every write path in this app now
    goes through `campus.bridge.create_assignment()` ->
    `assignment.bridge.create_context_assignment()` instead —
    `AssignmentViewSet` in views.py is a thin proxy over the unified
    model, not this one, going forward. Existing rows are kept purely as
    historical data (backfilled into the unified app by the one-time
    `migrate_campus_assignments_to_unified` management command); nothing
    in this app should create a NEW row here again. `save()` below
    enforces that — see its own docstring for the one legitimate
    exception (the migration command itself).
    """

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="assignments")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="assignments")
    posted_by = models.ForeignKey(StaffProfile, on_delete=models.SET_NULL, null=True, related_name="assignments_posted")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    attachment = models.FileField(upload_to="campus/assignments/", null=True, blank=True)
    due_date = models.DateField()
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="assignments")

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
        `migrate_campus_assignments_to_unified` for the deliberate,
        one-time historical backfill — never pass it from application
        code.
        """
        if self.pk is None and not migration_write:
            raise RuntimeError(
                "campus.Assignment is deprecated (Task 11) — new assignments must be created "
                "via campus.bridge.create_assignment(), which writes to the unified "
                "assignment.Assignment model instead. If this is the one-time historical "
                "backfill, call save(migration_write=True)."
            )
        super().save(*args, **kwargs)


class AssignmentSubmission(CampusBaseModel):
    """[DEPRECATED — Task 11] See `Assignment`'s docstring above — same
    reasoning, same `save()` guard, superseded by
    `assignment.AssignmentSubmission`.
    """

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        LATE = "late", "Late"
        MISSING = "missing", "Missing"

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="assignment_submissions")
    submitted_at = models.DateTimeField(null=True, blank=True)
    file = models.FileField(upload_to="campus/submissions/", null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MISSING, db_index=True)
    grade = models.CharField(max_length=10, blank=True)
    feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [UniqueConstraint(fields=["assignment", "student"], name="unique_submission_per_student")]

    def __str__(self):
        return f"{self.student.username} - {self.assignment.title} ({self.status})"

    def save(self, *args, migration_write=False, **kwargs):
        """See `Assignment.save()` above — identical guard/rationale."""
        if self.pk is None and not migration_write:
            raise RuntimeError(
                "campus.AssignmentSubmission is deprecated (Task 11) — new submissions now "
                "live on the unified assignment.AssignmentSubmission model (pre-created by "
                "campus.bridge.create_assignment(), submitted/graded via that app's own model "
                "methods). If this is the one-time historical backfill, call "
                "save(migration_write=True)."
            )
        super().save(*args, **kwargs)


class SyllabusUnit(CampusBaseModel):
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
# 7. Results
# ---------------------------------------------------------------------------

class ExamTerm(CampusBaseModel):
    session = models.ForeignKey(AcademicSession, on_delete=models.CASCADE, related_name="exam_terms")
    name = models.CharField(max_length=100)  # "Mid-Term", "Final"
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ["start_date"]

    def __str__(self):
        return f"{self.name} ({self.session.name})"


class ResultEntry(CampusBaseModel):
    enrollment = models.ForeignKey(StudentEnrollment, on_delete=models.CASCADE, related_name="results")
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE, related_name="results")
    exam_term = models.ForeignKey(ExamTerm, on_delete=models.CASCADE, related_name="results")
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    max_marks = models.DecimalField(max_digits=6, decimal_places=2)
    remarks = models.CharField(max_length=255, blank=True)
    entered_by = models.ForeignKey(StaffProfile, on_delete=models.SET_NULL, null=True, related_name="results_entered")

    class Meta:
        ordering = ["id"]
        constraints = [UniqueConstraint(fields=["enrollment", "subject", "exam_term"], name="unique_result_per_exam")]

    def __str__(self):
        return f"{self.enrollment} - {self.subject} - {self.exam_term} ({self.marks_obtained}/{self.max_marks})"


# Report cards are generated on-demand from ResultEntry rows (a view, not
# a model) — ResultEntry stays the single source of truth, same reasoning
# as attendance %-summary above.


# ---------------------------------------------------------------------------
# 8. Optional / future-ready modules
# ---------------------------------------------------------------------------

class DigitalIDCard(CampusBaseModel):
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


class FeeStructure(CampusBaseModel):
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

    class Meta:
        ordering = ["due_date"]
        indexes = [models.Index(fields=["campus", "session"])]

    def __str__(self):
        return f"{self.title} - {self.campus.name} ({self.amount})"


class FeeInvoice(CampusBaseModel):
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
    amount_due = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [UniqueConstraint(fields=["enrollment", "fee_structure"], name="unique_invoice_per_structure")]
        indexes = [models.Index(fields=["enrollment", "status"])]

    @property
    def amount_paid(self):
        """Computed from successful payments, not stored redundantly —
        avoids the exact read-modify-write drift risk flagged on
        `User.followers_count`/`coin` in login/models.py. Cheap enough at
        invoice-detail scale; if a dashboard needs this in bulk later,
        that's an `.annotate()` at the queryset level, not a new column
        here."""
        total = self.payments.filter(status=FeePayment.Status.SUCCESS).aggregate(total=models.Sum("amount"))["total"]
        return total or 0

    def recompute_status(self):
        """Idempotent — only writes when the derived status actually
        changed. Call this inside the same transaction right after a
        FeePayment is marked SUCCESS (see FeePayment.mark_success)."""
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
    elsewhere, by `liveclass.CoinPurchase`, to top the wallet up — just
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

    # Staff who manually entered a cash/cheque/bank-transfer payment at
    # the office. Null for the online self-serve path.
    recorded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="fee_payments_recorded"
    )
    notes = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["invoice", "-created_at"])]
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
            self.save(update_fields=["status"])
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