# campus/admin.py
from django.contrib import admin

from .models import (
    AcademicSession,
    Assignment,
    AssignmentSubmission,
    Attendance,
    Campus,
    CampusAnalyticsSnapshot,
    CampusLiveSession,
    CampusParentLink,
    ClassTeacherAssignment,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherAssignment,
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


@admin.register(ClassTeacherAssignment)
class ClassTeacherAssignmentAdmin(admin.ModelAdmin):
    list_display = ("section", "staff")


@admin.register(SubjectTeacherAssignment)
class SubjectTeacherAssignmentAdmin(admin.ModelAdmin):
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


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ("title", "campus", "section", "posted_by", "created_at")
    list_filter = ("campus",)


@admin.register(CampusLiveSession)
class CampusLiveSessionAdmin(admin.ModelAdmin):
    list_display = ("subject", "section", "teacher", "scheduled_at", "status")
    list_filter = ("status", "section__school_class__campus")
    search_fields = ("room_id",)


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
    list_display = ("enrollment", "date", "subject", "status", "marked_by")
    list_filter = ("status", "date")
    search_fields = ("enrollment__student__username",)


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = ("title", "section", "subject", "due_date", "posted_by")
    list_filter = ("section__school_class__campus", "due_date")
    search_fields = ("title",)


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    list_display = ("assignment", "student", "status", "submitted_at", "grade")
    list_filter = ("status",)
    search_fields = ("student__username", "assignment__title")


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
    list_display = ("enrollment", "subject", "exam_term", "marks_obtained", "max_marks", "entered_by")
    list_filter = ("exam_term", "subject")
    search_fields = ("enrollment__student__username",)


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
    list_display = ("title", "campus", "school_class", "session", "amount", "due_date", "is_active")
    list_filter = ("campus", "session", "is_active")
    search_fields = ("title",)


@admin.register(FeeInvoice)
class FeeInvoiceAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # status is documented on the model as never hand-set — always via
    # recompute_status(), called from FeePayment.mark_success()/.refund().
    list_display = ("enrollment", "fee_structure", "amount_due", "amount_paid", "status", "created_at")
    list_filter = ("status", "fee_structure__campus")
    search_fields = ("enrollment__student__username",)


@admin.register(FeePayment)
class FeePaymentAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # Every row here should only ever be created via FeePaymentViewSet
    # (pay/record), and only ever transition status via mark_success()/
    # refund() — both of which also touch FeeInvoice.recompute_status()
    # and, for refund(), a real CoinLedger credit-back. A raw admin edit
    # of `status` would desync all of that from what actually happened
    # to the money, so this admin is view/search-only.
    list_display = ("invoice", "amount", "paid_by", "payer_role", "payment_mode", "status", "recorded_by", "created_at")
    list_filter = ("status", "payment_mode")
    search_fields = ("gateway_reference", "paid_by__username", "invoice__enrollment__student__username")


@admin.register(CampusAnalyticsSnapshot)
class CampusAnalyticsSnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    # "filled by a Celery periodic task — never written from a
    # request-time view" (models.py) — admin should only ever read these.
    list_display = ("campus", "session", "computed_at")
    list_filter = ("campus", "session")