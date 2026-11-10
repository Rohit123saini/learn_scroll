# campus/admin.py
from django.contrib import admin

from .models import (
    AdmissionApplication,
    AdmissionCounter,
    CampusBackup,
    CampusConsentRecord,
    CampusDataDeletionRequest,
    CampusPrivacyPolicy,
    ParentChatThread,
    ParentTeacherSettings,
    PTMBooking,
    PTMEvent,
    PTMSlot,
    SubstituteAssignment,
    TeacherAbsence,
    AcademicSession,
    assigments,
    assigmentsSubmission,
    Attendance,
    AttendanceEditLog,
    StaffLeave,
    Campus,
    CampusAnalyticsSnapshot,
    CampusHoliday,
    CampusProfile,
    ClassNote,
    ClassResource,
    ClassResourceView,
    CampusInviteCode,
    CampusLiveParticipant,
    CampusLiveSession,
    CampusParentLink,
    ClassTeacherassigments,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeConcession,
    FeeConcessionGrant,
    FeeInvoice,
    FeeInvoiceCounter,
    FeePayment,
    FeeReconciliationItem,
    FeeReconciliationRun,
    FeeStructure,
    HomeworkDiaryEntry,
    Notice,
    ResourceFolder,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StudentNote,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
    SyllabusProgress,
    SyllabusUnit,
    TimeSlot,
    TimetableEntry,
)


class ReadOnlyAdminMixin:
    """
    Audit-only: view/search/filter in /admin/, but no add/change/delete
    from here. Same reasoning as this codebase's existing CoinLedgerAdmin
    — these rows are written exclusively through model methods that also
    keep a second invariant in sync (FeePayment.mark_success()/.refund()
    recompute the parent FeeInvoice's status and, for refund(), move real
    money through CoinLedger; FeeInvoice.status is documented on the
    model itself as "NEVER hand-set from a view — always recomputed via
    recompute_status()"; CampusAnalyticsSnapshot is "filled by a Celery
    periodic task — never written from a request-time view"). A raw
    admin edit bypasses all of that and can desync amount_paid/status/
    wallet balance from what actually happened — so this mixin removes
    the ability to do that, rather than trusting every admin user to
    remember not to.
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "created_by", "is_active", "fee_module_enabled", "created_at")
    list_filter = ("type", "is_active", "fee_module_enabled")
    search_fields = ("name",)


@admin.register(CampusProfile)
class CampusProfileAdmin(admin.ModelAdmin):
    list_display = ("campus", "board", "city", "state", "phone", "updated_at")
    list_filter = ("board",)
    search_fields = ("campus__name", "city", "affiliation_no")
    raw_id_fields = ("campus",)


@admin.register(AcademicSession)
class AcademicSessionAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "start_date", "end_date", "is_current")
    list_filter = ("campus", "is_current")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "campus")
    list_filter = ("campus",)


@admin.register(SchoolClass)
class SchoolClassAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "session", "department")
    list_filter = ("campus", "session")


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("name", "school_class")
    list_filter = ("school_class__campus",)


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "campus", "department")
    list_filter = ("campus",)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "is_virtual")
    list_filter = ("campus", "is_virtual")
    search_fields = ("name",)


@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "campus", "role", "is_active")
    list_filter = ("campus", "role", "is_active")
    search_fields = ("user__username",)


@admin.register(ClassTeacherassigments)
class ClassTeacherassigmentsAdmin(admin.ModelAdmin):
    list_display = ("section", "staff")


@admin.register(SubjectTeacherassigments)
class SubjectTeacherassigmentsAdmin(admin.ModelAdmin):
    list_display = ("staff", "subject", "section", "status", "approved_by")
    list_filter = ("status",)


@admin.register(StudentEnrollment)
class StudentEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "section", "session", "roll_number", "status")
    list_filter = ("session", "status")
    search_fields = ("student__username", "roll_number")


@admin.register(CampusParentLink)
class CampusParentLinkAdmin(admin.ModelAdmin):
    list_display = ("parent", "student", "campus", "created_at")
    list_filter = ("campus",)
    search_fields = ("parent__username", "student__username")


@admin.register(CampusInviteCode)
class CampusInviteCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "section", "campus", "is_active", "uses_count", "max_uses", "expires_at", "created_at")
    list_filter = ("campus", "is_active")
    search_fields = ("code", "label")


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ("title", "campus", "section", "kind", "audience", "status", "scheduled_for", "posted_by", "created_at")
    list_filter = ("campus", "kind", "status", "audience")


@admin.register(CampusLiveSession)
class CampusLiveSessionAdmin(admin.ModelAdmin):
    list_display = ("subject", "section", "teacher", "scheduled_at", "status", "started_at", "ended_at", "attendance_built_at")
    list_filter = ("status", "section__school_class__campus")
    search_fields = ("room_id",)
    readonly_fields = (
        "started_at", "ended_at", "started_by", "live_heartbeat_at", "attendance_built_at", "timetable_entry",
        "occurrence_date", "recording_status", "recording_egress_id", "recording_url", "recording_started_at",
        "recording_stopped_at", "recording_ready_at", "recording_duration_seconds", "recording_attempted_at",
        "recording_error", "recording_notified_at",
    )


@admin.register(CampusLiveParticipant)
class CampusLiveParticipantAdmin(admin.ModelAdmin):
    """Presence log the live-class attendance is computed from: view only."""
    list_display = ("session", "user", "joined_at", "last_seen_at", "left_at")
    search_fields = ("user__username",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TimeSlot)
class TimeSlotAdmin(admin.ModelAdmin):
    list_display = ("campus", "day_of_week", "start_time", "end_time", "label")
    list_filter = ("campus", "day_of_week")


@admin.register(TimetableEntry)
class TimetableEntryAdmin(admin.ModelAdmin):
    # TimetableEntry.save() runs full_clean() (see models.py — clash
    # detection is enforced there so nothing, admin included, can bypass
    # it), so an admin add/edit still goes through the same
    # staff/section/room clash checks a normal create does.
    list_display = ("section", "subject", "staff", "time_slot", "room", "session")
    list_filter = ("session", "section__school_class__campus")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "date", "subject", "status", "source", "minutes_present", "marked_by", "marked_at", "edit_count")
    list_filter = ("status", "date")
    search_fields = ("enrollment__student__username",)
    readonly_fields = ("marked_at", "last_edited_at", "edit_count")


@admin.register(AttendanceEditLog)
class AttendanceEditLogAdmin(admin.ModelAdmin):
    """Append-only audit trail: view only — no add / change / delete."""

    list_display = ("edited_at", "student_label", "date", "old_status", "new_status", "reason", "edited_by", "was_override")
    list_filter = ("was_override", "action", "campus")
    search_fields = ("student_label", "reason", "edited_by__username")
    date_hierarchy = "edited_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(assigments)
class assigmentsAdmin(admin.ModelAdmin):
    list_display = ("title", "section", "subject", "due_date", "posted_by")
    list_filter = ("section__school_class__campus", "due_date")
    search_fields = ("title",)


@admin.register(assigmentsSubmission)
class assigmentsSubmissionAdmin(admin.ModelAdmin):
    list_display = ("assigments", "student", "status", "submitted_at", "grade")
    list_filter = ("status",)
    search_fields = ("student__username", "assigments__title")


@admin.register(SyllabusUnit)
class SyllabusUnitAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "section", "session", "order")
    list_filter = ("session", "subject")


@admin.register(SyllabusProgress)
class SyllabusProgressAdmin(admin.ModelAdmin):
    list_display = ("syllabus_unit", "covered_on", "covered_by")
    list_filter = ("covered_on",)


@admin.register(ExamTerm)
class ExamTermAdmin(admin.ModelAdmin):
    list_display = ("name", "session", "start_date", "end_date")
    list_filter = ("session",)


@admin.register(ResultEntry)
class ResultEntryAdmin(admin.ModelAdmin):
    list_display = (
        "enrollment", "subject", "exam_term", "marks_obtained", "max_marks", "status", "locked_at", "entered_by",
    )
    list_filter = ("status", "exam_term", "subject")
    search_fields = ("enrollment__student__username",)
    # Publish/lock state changes go through the API (it notifies, audits and
    # enforces the lock); admin can read them but not flip them by hand.
    readonly_fields = ("published_at", "locked_at", "locked_by")


@admin.register(DigitalIDCard)
class DigitalIDCardAdmin(admin.ModelAdmin):
    list_display = ("user", "campus", "qr_token", "issued_at", "valid_until")
    list_filter = ("campus",)
    search_fields = ("user__username", "qr_token")


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    # Editable, unlike FeeInvoice/FeePayment below — this is just a fee
    # DEFINITION (models.py: "not a payment"), so it carries no derived
    # money-invariant an admin edit could desync.
    list_display = ("title", "campus", "school_class", "session", "category", "amount", "due_date",
                    "late_fine_type", "is_active")
    list_filter = ("campus", "session", "is_active", "category", "late_fine_type")
    search_fields = ("title",)


@admin.register(FeeInvoice)
class FeeInvoiceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # status is documented on the model as never hand-set — always via
    # recompute_status(), called from FeePayment.mark_success()/.refund().
    list_display = ("invoice_no", "enrollment", "fee_structure", "base_amount", "concession_amount", "fine_amount",
                    "amount_due", "amount_paid", "status", "created_at")
    list_filter = ("status", "fee_structure__campus")
    search_fields = ("invoice_no", "enrollment__student__username")


@admin.register(FeePayment)
class FeePaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # Every row here should only ever be created via FeePaymentViewSet
    # (pay/record), and only ever transition status via mark_success()/
    # refund() — both of which also touch FeeInvoice.recompute_status()
    # and, for refund(), a real CoinLedger credit-back. A raw admin edit
    # of `status` would desync all of that from what actually happened
    # to the money, so this admin is view/search-only.
    list_display = ("invoice", "amount", "paid_by", "payer_role", "payment_mode", "gateway_method", "status", "recorded_by", "paid_at", "created_at")
    list_filter = ("status", "payment_mode")
    search_fields = ("gateway_reference", "gateway_payment_id", "paid_by__username", "invoice__enrollment__student__username")


@admin.register(FeeConcession)
class FeeConcessionAdmin(admin.ModelAdmin):
    list_display = ("name", "campus", "reason", "kind", "value", "applies_to_category", "is_active")
    list_filter = ("campus", "reason", "kind", "is_active")
    search_fields = ("name",)


@admin.register(FeeConcessionGrant)
class FeeConcessionGrantAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # Grants re-price invoices — only via the API (FeeConcessionGrantViewSet), never a raw admin edit.
    list_display = ("concession", "enrollment", "fee_structure", "is_active", "granted_by", "created_at")
    list_filter = ("is_active", "concession__campus")


@admin.register(FeeInvoiceCounter)
class FeeInvoiceCounterAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("campus", "session", "last_no")


@admin.register(FeeReconciliationRun)
class FeeReconciliationRunAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("campus", "source", "period_from", "period_to", "created_by", "created_at")
    list_filter = ("campus", "source")


@admin.register(FeeReconciliationItem)
class FeeReconciliationItemAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("category", "severity", "resolution", "gateway_payment_id", "our_amount", "gateway_amount")
    list_filter = ("category", "severity", "resolution")
    search_fields = ("gateway_payment_id", "gateway_order_id")


@admin.register(CampusAnalyticsSnapshot)
class CampusAnalyticsSnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # "filled by a Celery periodic task — never written from a
    # request-time view" (models.py) — admin should only ever read these.
    list_display = ("campus", "session", "computed_at")
    list_filter = ("campus", "session")


@admin.register(HomeworkDiaryEntry)
class HomeworkDiaryEntryAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "section", "diary_date", "due_date", "posted_by", "is_active")
    list_filter = ("campus", "is_active", "diary_date")
    search_fields = ("title", "description", "posted_by__username")
    raw_id_fields = ("campus", "session", "section", "subject", "posted_by")
    readonly_fields = ("due_reminder_sent_at", "created_at", "updated_at")


@admin.register(TeacherAbsence)
class TeacherAbsenceAdmin(admin.ModelAdmin):
    list_display = ("staff", "campus", "date_from", "date_to", "is_active", "marked_by")
    list_filter = ("is_active", "campus")
    raw_id_fields = ("staff", "marked_by")


@admin.register(SubstituteAssignment)
class SubstituteAssignmentAdmin(admin.ModelAdmin):
    list_display = ("entry", "date", "substitute", "original_staff", "status", "campus")
    list_filter = ("status", "campus", "date")
    raw_id_fields = ("entry", "substitute", "original_staff", "absence", "assigned_by")


# ---------------------------------------------------------------------------
# Parents se baat — PTM + teacher<->parent chat
# NOTE: `ParentChatMessage` is deliberately NOT registered. Chats are visible
# to their two participants only (and to admin/principal after a report, via
# the audited app screen) - not to anyone with a /admin/ login. The thread row
# below carries metadata only (who, status, report flag), and is read-only.
# ---------------------------------------------------------------------------

@admin.register(ParentTeacherSettings)
class ParentTeacherSettingsAdmin(admin.ModelAdmin):
    list_display = ("campus", "chat_enabled", "chat_start_time", "chat_end_time", "ptm_enabled")
    raw_id_fields = ("campus",)


@admin.register(PTMEvent)
class PTMEventAdmin(admin.ModelAdmin):
    list_display = ("title", "campus", "date", "start_time", "end_time", "status", "created_by")
    list_filter = ("status", "campus", "date")
    search_fields = ("title",)
    raw_id_fields = ("campus", "created_by")
    readonly_fields = ("created_at", "published_at", "closed_at")


@admin.register(PTMSlot)
class PTMSlotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("event", "staff", "start_time", "end_time", "is_blocked")
    list_filter = ("is_blocked", "event__campus")


@admin.register(PTMBooking)
class PTMBookingAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # read-only: bookings change through campus.ptm (slot/uniqueness rules +
    # notifications); a raw admin edit would bypass both. `private_note` is a
    # teacher-only note, so it is not shown here either.
    list_display = ("event", "slot", "staff", "student", "parent", "status", "campus")
    list_filter = ("status", "campus", "event__date")
    exclude = ("private_note",)


@admin.register(ParentChatThread)
class ParentChatThreadAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("campus", "student", "parent", "teacher", "status", "flagged_at", "last_message_at")
    list_filter = ("status", "campus")
    exclude = ("last_message_preview",)


@admin.register(StaffLeave)
class StaffLeaveAdmin(admin.ModelAdmin):
    list_display = ("staff", "leave_type", "date_from", "date_to", "is_half_day", "status", "decided_by", "created_at")
    list_filter = ("status", "leave_type", "campus")
    search_fields = ("staff__user__username", "reason")
    date_hierarchy = "date_from"
    readonly_fields = ("created_at", "decided_at", "cancelled_at", "edited_at")


@admin.register(CampusHoliday)
class CampusHolidayAdmin(admin.ModelAdmin):
    list_display = ("campus", "date", "name")
    list_filter = ("campus",)
    search_fields = ("name",)
    date_hierarchy = "date"


@admin.register(ResourceFolder)
class ResourceFolderAdmin(admin.ModelAdmin):
    list_display = ("name", "subject", "section", "parent", "campus", "is_active", "created_by")
    list_filter = ("is_active", "campus")
    search_fields = ("name",)
    raw_id_fields = ("campus", "session", "section", "subject", "parent", "created_by")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ClassResource)
class ClassResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "folder", "subject", "section", "is_published", "is_active", "posted_by", "created_at")
    list_filter = ("kind", "is_published", "is_active", "campus")
    search_fields = ("title", "description", "posted_by__username")
    raw_id_fields = ("campus", "session", "section", "subject", "folder", "posted_by")
    readonly_fields = ("original_name", "file_size", "notified_at", "created_at", "updated_at")


@admin.register(ClassResourceView)
class ClassResourceViewAdmin(admin.ModelAdmin):
    # written by the open endpoint only — admin just reads
    list_display = ("resource", "user", "view_count", "last_viewed_at")
    raw_id_fields = ("resource", "user")
    readonly_fields = ("resource", "user", "view_count", "first_viewed_at", "last_viewed_at")

    def has_add_permission(self, request):
        return False


@admin.register(ClassNote)
class ClassNoteAdmin(admin.ModelAdmin):
    list_display = ("title", "subject", "section", "is_published", "is_pinned", "is_active", "posted_by", "created_at")
    list_filter = ("is_published", "is_active", "campus")
    search_fields = ("title", "body", "posted_by__username")
    raw_id_fields = ("campus", "section", "subject", "live_session", "posted_by")
    readonly_fields = ("original_name", "file_size", "notified_at", "created_at", "updated_at")


@admin.register(StudentNote)
class StudentNoteAdmin(admin.ModelAdmin):
    """A student's notes are PRIVATE to the student: the admin shows who wrote one and
    where, never the text, and nothing can be added or edited here."""
    list_display = ("author", "subject", "section", "live_session", "at_seconds", "is_active", "created_at")
    list_filter = ("is_active", "campus")
    search_fields = ("author__username",)
    exclude = ("text",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class _ReadOnlyAdmin(admin.ModelAdmin):
    """SAFETY models are changed through the API (it audits, notifies and enforces the rules) - the /admin/
    screen only lets staff look and search."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CampusConsentRecord)
class CampusConsentRecordAdmin(_ReadOnlyAdmin):
    """Append-only proof of consent: view only."""
    list_display = ("created_at", "campus", "purpose", "status", "policy_version", "source", "subject", "student")
    list_filter = ("purpose", "status", "source", "campus")
    search_fields = ("subject__username", "student__username", "note")
    date_hierarchy = "created_at"


@admin.register(CampusPrivacyPolicy)
class CampusPrivacyPolicyAdmin(admin.ModelAdmin):
    """Per-campus parent / child privacy switches (campus/privacy_rules.py). Normally set from the app."""
    list_display = ("campus", "enforce_guardian_consent", "unknown_dob_is_minor", "updated_by", "updated_at")
    list_filter = ("enforce_guardian_consent", "unknown_dob_is_minor")
    readonly_fields = ("updated_by", "updated_at")


@admin.register(CampusDataDeletionRequest)
class CampusDataDeletionRequestAdmin(_ReadOnlyAdmin):
    list_display = ("created_at", "campus", "kind", "status", "subject", "requester", "due_at", "completed_at")
    list_filter = ("status", "kind", "campus")
    search_fields = ("subject__username", "requester__username")
    exclude = ("reason",)  # the person's own words: shown in the app to the decision makers, not here


@admin.register(CampusBackup)
class CampusBackupAdmin(_ReadOnlyAdmin):
    list_display = ("created_at", "campus", "kind", "status", "size_bytes", "expires_at", "created_by")
    list_filter = ("kind", "status", "campus")
    exclude = ("file",)  # the file holds the whole campus; it is only downloaded through the audited API view


@admin.register(AdmissionApplication)
class AdmissionApplicationAdmin(admin.ModelAdmin):
    list_display = ("application_no", "applicant_name", "campus", "school_class", "status", "follow_up_on", "created_at")
    list_filter = ("status", "source", "campus")
    search_fields = ("application_no", "applicant_name", "guardian_name", "guardian_phone")
    raw_id_fields = ("student", "enrollment", "created_by", "decided_by", "section", "school_class")


admin.site.register(AdmissionCounter)


# ---------------------------------------------------------------------------
# SOFT DELETE in /admin/ (see soft_delete.py): the admin never erases a row either.
# - list shows trash too (filter `is_deleted`), delete / bulk-delete = move to trash + audit row,
# - "Restore selected" brings it back (cascade batch included),
# - a delete the model refuses (paid invoice, class with active students ...) is simply not offered.
# Hard erasure = `manage.py purge_soft_deleted`.
# ---------------------------------------------------------------------------
from django.contrib import messages  # noqa: E402

from .audit import _campus_id_of, log_action  # noqa: E402
from .soft_delete import SoftDeleteError, SoftDeleteModel  # noqa: E402

_SD_FIELDS = ("is_deleted", "deleted_at", "deleted_by", "delete_batch")


class SoftDeleteAdminMixin:
    actions = ["restore_selected"]

    def get_queryset(self, request):
        qs = self.model.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        return qs.order_by(*ordering) if ordering else qs

    def get_list_filter(self, request):
        return tuple(super().get_list_filter(request)) + ("is_deleted",)

    def get_readonly_fields(self, request, obj=None):
        return tuple(super().get_readonly_fields(request, obj)) + _SD_FIELDS

    def has_delete_permission(self, request, obj=None):
        ok = super().has_delete_permission(request, obj)
        if ok and obj is not None and not obj.is_deleted:
            try:
                obj.soft_delete_check()
            except SoftDeleteError:
                return False
        return ok

    def _audit(self, request, obj, verb, **meta):
        campus_id = _campus_id_of(obj) or (obj.pk if obj._meta.model_name == "campus" else None)
        if campus_id:
            name = obj._meta.model_name
            log_action(campus_id, request.user, f"{name}.{verb}", target=obj, summary=f"admin {verb} {name}: {obj}"[:300], **meta)

    def _soft(self, request, obj):
        if obj.is_deleted:
            return
        try:
            batch, cascaded = obj.soft_delete(user=request.user)
        except SoftDeleteError as err:
            self.message_user(request, f"{obj}: {err.message}", level=messages.ERROR)
            return
        self._audit(request, obj, "delete", soft_delete=True, batch=str(batch), cascaded=dict(cascaded), via="admin")

    def delete_model(self, request, obj):
        self._soft(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in list(queryset):
            self._soft(request, obj)

    @admin.action(description="Restore selected (from trash)")
    def restore_selected(self, request, queryset):
        done = 0
        for obj in list(queryset.filter(is_deleted=True)):
            try:
                restored = obj.restore(user=request.user)
            except SoftDeleteError as err:
                self.message_user(request, f"{obj}: {err.message}", level=messages.ERROR)
                continue
            self._audit(request, obj, "restore", cascaded=dict(restored), via="admin")
            done += 1
        self.message_user(request, f"Restored {done} record(s).")


for _model, _old in list(admin.site._registry.items()):
    if issubclass(_model, SoftDeleteModel) and not isinstance(_old, SoftDeleteAdminMixin):
        _cls = type(_old.__class__.__name__, (SoftDeleteAdminMixin, _old.__class__), {})
        admin.site.unregister(_model)
        admin.site.register(_model, _cls)
