# campus/serializers.py
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

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

User = get_user_model()


class DjangoCleanValidationMixin:
    """
    Some models in this app (`TimetableEntry`, most notably) enforce
    their invariants in `clean()`/`save()` rather than only at the
    serializer layer, deliberately — see that model's docstring: it
    must be impossible to bypass from the admin or a shell, not just
    from this API. DRF's default exception handler does NOT convert a
    raw `django.core.exceptions.ValidationError` into a 400 the way it
    does for `rest_framework.exceptions.ValidationError`, so without
    this it would surface as an unhandled 500. This mixin re-raises it
    as the DRF flavor so the model's own validation still produces a
    clean, normal-looking 400 response through this API.
    """

    def save(self, **kwargs):
        try:
            return super().save(**kwargs)
        except DjangoValidationError as e:
            raise serializers.ValidationError(getattr(e, "message_dict", e.messages))


class MinimalUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name"]


# ============================================================
# Phase 1 — structural hierarchy
# ============================================================
class CampusSerializer(serializers.ModelSerializer):
    created_by = MinimalUserSerializer(read_only=True)

    class Meta:
        model = Campus
        fields = [
            "id", "name", "type", "is_active", "fee_module_enabled",
            "attendance_alert_threshold_percent", "created_by", "created_at",
            # G-2 fix — surfaced read-only so the creator can see status;
            # only `CampusViewSet.approve`/`.reject` (platform-admin only)
            # can change it, never a plain PATCH here.
            "verification_status", "verified_by", "verified_at",
        ]
        read_only_fields = [
            "id", "created_by", "created_at",
            "verification_status", "verified_by", "verified_at",
        ]


class AcademicSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AcademicSession
        fields = ["id", "campus", "name", "start_date", "end_date", "is_current"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        start = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if start and end and end <= start:
            raise serializers.ValidationError("end_date must be after start_date.")
        return attrs


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "campus", "name"]
        read_only_fields = ["id"]


class SchoolClassSerializer(serializers.ModelSerializer):
    class Meta:
        model = SchoolClass
        fields = ["id", "campus", "department", "session", "name"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        department = attrs.get("department", getattr(self.instance, "department", None))
        session = attrs.get("session", getattr(self.instance, "session", None))
        if department and campus and department.campus_id != campus.id:
            raise serializers.ValidationError({"department": "Doesn't belong to this campus."})
        if session and campus and session.campus_id != campus.id:
            raise serializers.ValidationError({"session": "Doesn't belong to this campus."})
        return attrs


class SectionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Section
        fields = ["id", "school_class", "name"]
        read_only_fields = ["id"]


class SubjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "campus", "department", "name", "code"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        department = attrs.get("department", getattr(self.instance, "department", None))
        if department and campus and department.campus_id != campus.id:
            raise serializers.ValidationError({"department": "Doesn't belong to this campus."})
        return attrs


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ["id", "campus", "name", "is_virtual"]
        read_only_fields = ["id"]


class StaffProfileSerializer(serializers.ModelSerializer):
    user_detail = MinimalUserSerializer(source="user", read_only=True)

    class Meta:
        model = StaffProfile
        fields = ["id", "campus", "user", "user_detail", "role", "is_active"]
        read_only_fields = ["id"]


# ============================================================
# Phase 2 — assignments & enrollment
# ============================================================
class ClassTeacherAssignmentSerializer(serializers.ModelSerializer):
    staff_detail = StaffProfileSerializer(source="staff", read_only=True)

    class Meta:
        model = ClassTeacherAssignment
        fields = ["id", "section", "staff", "staff_detail"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        section = attrs.get("section", getattr(self.instance, "section", None))
        staff = attrs.get("staff", getattr(self.instance, "staff", None))
        if section and staff and staff.campus_id != section.school_class.campus_id:
            raise serializers.ValidationError("This staff member doesn't belong to this section's campus.")
        return attrs


class SubjectTeacherAssignmentSerializer(serializers.ModelSerializer):
    staff_detail = StaffProfileSerializer(source="staff", read_only=True)
    subject_detail = SubjectSerializer(source="subject", read_only=True)

    class Meta:
        model = SubjectTeacherAssignment
        fields = [
            "id", "section", "subject", "staff", "staff_detail", "subject_detail",
            "approved_by", "status", "responded_at",
        ]
        read_only_fields = ["id", "approved_by", "status", "responded_at"]

    def validate(self, attrs):
        section = attrs.get("section", getattr(self.instance, "section", None))
        subject = attrs.get("subject", getattr(self.instance, "subject", None))
        staff = attrs.get("staff", getattr(self.instance, "staff", None))
        campus_id = section.school_class.campus_id if section else None
        if subject and campus_id and subject.campus_id != campus_id:
            raise serializers.ValidationError({"subject": "Doesn't belong to this section's campus."})
        if staff and campus_id and staff.campus_id != campus_id:
            raise serializers.ValidationError({"staff": "Doesn't belong to this section's campus."})
        return attrs


class StudentEnrollmentSerializer(serializers.ModelSerializer):
    student_detail = MinimalUserSerializer(source="student", read_only=True)

    class Meta:
        model = StudentEnrollment
        fields = ["id", "student", "student_detail", "section", "session", "roll_number", "status"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        section = attrs.get("section", getattr(self.instance, "section", None))
        session = attrs.get("session", getattr(self.instance, "session", None))
        if section and session and section.school_class.session_id != session.id:
            raise serializers.ValidationError({"session": "This section doesn't belong to the given session."})
        return attrs


class CampusParentLinkSerializer(serializers.ModelSerializer):
    """Read-only from the API's point of view — creation only ever
    happens via `ParentLinkVerifyView`, never a plain POST here (see
    that model's docstring)."""
    student_detail = MinimalUserSerializer(source="student", read_only=True)
    parent_detail = MinimalUserSerializer(source="parent", read_only=True)

    class Meta:
        model = CampusParentLink
        fields = ["id", "campus", "student", "student_detail", "parent", "parent_detail", "created_at"]
        read_only_fields = fields


# ============================================================
# Phase 3 — notices
# ============================================================
class NoticeSerializer(serializers.ModelSerializer):
    posted_by = MinimalUserSerializer(read_only=True)

    class Meta:
        model = Notice
        fields = [
            "id", "campus", "department", "school_class", "section", "session",
            "posted_by", "title", "body", "pin_until", "created_at",
        ]
        read_only_fields = ["id", "posted_by", "created_at"]

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        for field in ("department", "school_class", "section"):
            value = attrs.get(field, getattr(self.instance, field, None))
            if value is None:
                continue
            scope_campus_id = value.campus_id if field != "section" else value.school_class.campus_id
            if campus and scope_campus_id != campus.id:
                raise serializers.ValidationError({field: "Doesn't belong to the given campus."})
        return attrs


# ============================================================
# Phase 4 — live classes
# ============================================================
class CampusLiveSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampusLiveSession
        fields = ["id", "section", "subject", "teacher", "scheduled_at", "status", "room_id"]
        read_only_fields = ["id", "status", "room_id"]

    def validate(self, attrs):
        section = attrs.get("section", getattr(self.instance, "section", None))
        subject = attrs.get("subject", getattr(self.instance, "subject", None))
        teacher = attrs.get("teacher", getattr(self.instance, "teacher", None))
        campus_id = section.school_class.campus_id if section else None
        if subject and campus_id and subject.campus_id != campus_id:
            raise serializers.ValidationError({"subject": "Doesn't belong to this section's campus."})
        if teacher and campus_id and teacher.campus_id != campus_id:
            raise serializers.ValidationError({"teacher": "Doesn't belong to this section's campus."})
        return attrs


# ============================================================
# Phase 5 — timetable & attendance
# ============================================================
class TimeSlotSerializer(serializers.ModelSerializer):
    class Meta:
        model = TimeSlot
        fields = ["id", "campus", "day_of_week", "start_time", "end_time", "label"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        start = attrs.get("start_time", getattr(self.instance, "start_time", None))
        end = attrs.get("end_time", getattr(self.instance, "end_time", None))
        if start and end and end <= start:
            raise serializers.ValidationError("end_time must be after start_time.")
        return attrs


class TimetableEntrySerializer(DjangoCleanValidationMixin, serializers.ModelSerializer):
    """
    Clash-detection lives in `TimetableEntry.clean()` (model layer, by
    design — see that model's docstring). `DjangoCleanValidationMixin`
    above is what turns that into a normal 400 through this API instead
    of a 500.
    """

    class Meta:
        model = TimetableEntry
        fields = ["id", "section", "subject", "staff", "time_slot", "room", "session"]
        read_only_fields = ["id"]


class AttendanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Attendance
        fields = ["id", "enrollment", "date", "subject", "status", "marked_by"]
        read_only_fields = ["id", "marked_by"]


# ============================================================
# Phase 6 — assignments & syllabus
# ============================================================
class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = [
            "id", "section", "subject", "posted_by", "title", "description",
            "attachment", "due_date", "session",
        ]
        read_only_fields = ["id", "posted_by"]


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    student_detail = MinimalUserSerializer(source="student", read_only=True)

    class Meta:
        model = AssignmentSubmission
        fields = [
            "id", "assignment", "student", "student_detail", "submitted_at",
            "file", "status", "grade", "feedback",
        ]
        read_only_fields = ["id", "student", "submitted_at", "status"]


class SyllabusUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyllabusUnit
        fields = ["id", "subject", "section", "session", "title", "order"]
        read_only_fields = ["id"]


class SyllabusProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = SyllabusProgress
        fields = ["id", "syllabus_unit", "covered_on", "covered_by"]
        read_only_fields = ["id", "covered_on", "covered_by"]


# ============================================================
# Phase 7 — results
# ============================================================
class ExamTermSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamTerm
        fields = ["id", "session", "name", "start_date", "end_date"]
        read_only_fields = ["id"]


class ResultEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = ResultEntry
        fields = [
            "id", "enrollment", "subject", "exam_term", "marks_obtained",
            "max_marks", "remarks", "entered_by",
        ]
        read_only_fields = ["id", "entered_by"]

    def validate(self, attrs):
        obtained = attrs.get("marks_obtained", getattr(self.instance, "marks_obtained", None))
        max_marks = attrs.get("max_marks", getattr(self.instance, "max_marks", None))
        if obtained is not None and max_marks is not None and obtained > max_marks:
            raise serializers.ValidationError({"marks_obtained": "Cannot exceed max_marks."})
        return attrs


# ============================================================
# Phase 8 — optional modules
# ============================================================
class DigitalIDCardSerializer(serializers.ModelSerializer):
    user_detail = MinimalUserSerializer(source="user", read_only=True)

    class Meta:
        model = DigitalIDCard
        fields = ["id", "user", "user_detail", "campus", "qr_token", "issued_at", "valid_until"]
        read_only_fields = ["id", "qr_token", "issued_at"]


class FeeStructureSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeeStructure
        fields = ["id", "campus", "school_class", "session", "title", "amount", "due_date", "is_active"]
        read_only_fields = ["id"]


class FeeInvoiceSerializer(serializers.ModelSerializer):
    amount_paid = serializers.ReadOnlyField()

    class Meta:
        model = FeeInvoice
        fields = ["id", "enrollment", "fee_structure", "amount_due", "amount_paid", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]


class FeePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeePayment
        fields = [
            "id", "invoice", "amount", "paid_by", "payer_role", "payment_mode",
            "status", "gateway_reference", "recorded_by", "notes", "created_at",
        ]
        read_only_fields = ["id", "status", "recorded_by", "created_at"]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("amount must be positive.")
        return value


class CampusAnalyticsSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampusAnalyticsSnapshot
        fields = ["id", "campus", "session", "computed_at", "data"]
        read_only_fields = fields