# campus/serializers.py
import os
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from . import finance_rules as fr
from . import notice_rules
from .models import (
    FeeConcession,
    FeeConcessionGrant,
    FeeReconciliationItem,
    FeeReconciliationRun,
    AcademicSession,
    StaffLeave,
    assigments,
    assigmentsSubmission,
    Attendance,
    Campus,
    CampusHoliday,
    CampusAnalyticsSnapshot,
    CampusDoubt,
    CampusDoubtReply,
    CampusInviteCode,
    CampusLiveSession,
    CampusParentLink,
    ClassMode,
    ClassTeacherassigments,
    Department,
    DigitalIDCard,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    HomeworkDiaryEntry,
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
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
            "attendance_alert_threshold_percent", "attendance_edit_window_hours",
            # Live class auto attendance knobs (admin / principal).
            "live_attendance_auto", "live_attendance_min_percent", "live_attendance_late_minutes",
            # Recorded classes (admin / principal): off by default, see campus/recordings.py.
            "class_recording_enabled", "class_recording_auto",
            # Staff leave calendar + quota (admin / principal set these).
            "weekly_off_days", "leave_quota",
            # Campus Settings (campus/settings_views.py) — read-only here, changed through /campus/settings/<id>/.
            "grading_system", "half_day_weekdays", "pass_percent",
            "created_by", "created_at",
            # T4 — additive per-campus feature flags for the new modules.
            "doubts_enabled", "doubts_public_allowed", "class_reminders_enabled",
            # G-2 fix — surfaced read-only so the creator can see status;
            # only `CampusViewSet.approve`/`.reject` (platform-admin only)
            # can change it, never a plain PATCH here.
            "verification_status", "verified_by", "verified_at",
        ]
        read_only_fields = [
            "id", "created_by", "created_at",
            "verification_status", "verified_by", "verified_at",
            "grading_system", "half_day_weekdays", "pass_percent",
        ]

    def validate_weekly_off_days(self, value):
        """ISO weekdays Mon=1..Sun=7, e.g. [7] or [6, 7]. Empty = no weekly off."""
        if not isinstance(value, list) or any(
            isinstance(d, bool) or not isinstance(d, int) or not 1 <= d <= 7 for d in value
        ):
            raise serializers.ValidationError("Use a list of weekdays 1 (Mon) to 7 (Sun), e.g. [7].")
        return sorted(set(value))

    def validate_leave_quota(self, value):
        """Days per year per leave type, e.g. {"casual": 12, "sick": 10}."""
        if not isinstance(value, dict):
            raise serializers.ValidationError('Use an object like {"casual": 12, "sick": 10}.')
        allowed = StaffLeave.LeaveType.values
        clean = {}
        for leave_type, days in value.items():
            if leave_type not in allowed:
                raise serializers.ValidationError(f"Unknown leave type '{leave_type}'. Use: {', '.join(allowed)}.")
            if isinstance(days, bool) or not isinstance(days, (int, float)) or not 0 <= days <= 366:
                raise serializers.ValidationError(f"'{leave_type}' must be a number of days from 0 to 366.")
            clean[leave_type] = days
        return clean


class CampusHolidaySerializer(serializers.ModelSerializer):
    class Meta:
        model = CampusHoliday
        fields = ["id", "campus", "date", "name"]
        read_only_fields = ["id"]

    def validate_name(self, value):
        value = " ".join(str(value or "").split())
        if not value:
            raise serializers.ValidationError("Give the holiday a name.")
        return value

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        day = attrs.get("date", getattr(self.instance, "date", None))
        if campus is not None and day is not None:
            dup = CampusHoliday.objects.filter(campus=campus, date=day)
            if self.instance is not None:
                dup = dup.exclude(pk=self.instance.pk)
            if dup.exists():
                raise serializers.ValidationError({"date": "This date is already a holiday."})
        return attrs


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
        fields = ["id", "school_class", "name", "capacity"]
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
        fields = ["id", "campus", "user", "user_detail", "role", "is_active", "department"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        department = attrs.get("department", getattr(self.instance, "department", None))
        if department and campus and department.campus_id != campus.id:
            raise serializers.ValidationError({"department": "Doesn't belong to this campus."})
        return attrs


# ============================================================
# Phase 2 — assigmentss & enrollment
# ============================================================
class ClassTeacherassigmentsSerializer(serializers.ModelSerializer):
    staff_detail = StaffProfileSerializer(source="staff", read_only=True)

    class Meta:
        model = ClassTeacherassigments
        fields = ["id", "section", "staff", "staff_detail"]
        read_only_fields = ["id"]

    def validate(self, attrs):
        section = attrs.get("section", getattr(self.instance, "section", None))
        staff = attrs.get("staff", getattr(self.instance, "staff", None))
        if section and staff and staff.campus_id != section.school_class.campus_id:
            raise serializers.ValidationError("This staff member doesn't belong to this section's campus.")
        return attrs


class SubjectTeacherassigmentsSerializer(serializers.ModelSerializer):
    staff_detail = StaffProfileSerializer(source="staff", read_only=True)
    subject_detail = SubjectSerializer(source="subject", read_only=True)

    class Meta:
        model = SubjectTeacherassigments
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
        fields = ["id", "student", "student_detail", "section", "session", "roll_number", "enrollment_no", "status", "left_at"]
        read_only_fields = ["id", "left_at"]
        # [T4 §D] The (student, section, session) uniqueness is enforced by the
        # DB constraint + campus.roster (which re-activates a withdrawn row and
        # returns a clean 400 "already_enrolled" for an active duplicate), so
        # the auto UniqueTogetherValidator must not reject re-enrolment first.
        validators = []

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

    # 🔥 FIX — frontend "parent view" gap: attendance-summary/report-card
    # (campus/views.py) both need an `enrollment` id to look anything up,
    # and both already allow a linked parent to read them
    # (`is_linked_parent_of_student`). `StudentEnrollmentViewSet` DOES
    # technically allow a parent to GET (its `IsCampusAdminOrPrincipal`
    # permission already lets every safe method through — see that
    # class's own `has_permission()`), but only by listing/filtering the
    # WHOLE campus's enrollments and searching for their child's row
    # client-side, which is both wasteful (one call per child instead of
    # getting it inline with the link they already fetched) and leakier
    # than it needs to be (exposes every other student's enrollment to
    # get one). Computed here instead — read-only, scoped to exactly the
    # one row the caller is already allowed to see.
    active_enrollment = serializers.SerializerMethodField()

    class Meta:
        model = CampusParentLink
        fields = [
            "id", "campus", "student", "student_detail", "parent", "parent_detail",
            "active_enrollment", "created_at",
        ]
        read_only_fields = fields

    def get_active_enrollment(self, obj):
        # [PERF] CampusParentLinkViewSet prefetches the student's ACTIVE enrollments (newest session
        # first, section+class joined) onto `student._active_enrollments_prefetched` — pick from that
        # in Python instead of one query per link. Other callers keep the old single query.
        prefetched = getattr(obj.student, "_active_enrollments_prefetched", None) if obj.student_id else None
        if prefetched is not None:
            enrollment = next((e for e in prefetched if e.section.school_class.campus_id == obj.campus_id), None)
        else:
            enrollment = (
                StudentEnrollment.objects.filter(
                    student_id=obj.student_id,
                    section__school_class__campus_id=obj.campus_id,
                    status=StudentEnrollment.Status.ACTIVE,
                )
                .select_related("section", "section__school_class")
                .order_by("-session__start_date")
                .first()
            )
        if not enrollment:
            return None
        return {
            "id": enrollment.id,
            "section_id": enrollment.section_id,
            "section_name": enrollment.section.name,
            "school_class_id": enrollment.section.school_class_id,
            "school_class_name": enrollment.section.school_class.name,
            "department_id": enrollment.section.school_class.department_id,
            "session_id": enrollment.session_id,
        }


class CampusInviteCodeSerializer(serializers.ModelSerializer):
    """[ADDED — Task 13/G13] Read-only from the API's point of view —
    creation only ever happens via `CampusInviteCodeGenerateView`
    (`campus_invite.py`), never a plain POST here, since generating one
    needs the `can_manage_section_subject` authority check, not a plain
    model-permission check. Unlike `CampusParentLinkSerializer`'s parent
    code, the plaintext `code` is always included here (not masked) —
    this code is meant to be shared openly with a whole batch, not kept
    private to one parent."""

    section_detail = serializers.SerializerMethodField()
    created_by_detail = MinimalUserSerializer(source="created_by", read_only=True)
    is_usable = serializers.BooleanField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    # T2.8 — additive: why it is unusable ("revoked"/"expired"/"full"/"") and
    # how many seats are left (null = unlimited).
    unavailable_reason = serializers.CharField(read_only=True)
    remaining_uses = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = CampusInviteCode
        fields = [
            "id", "campus", "section", "section_detail", "code", "label",
            "is_active", "max_uses", "uses_count", "expires_at", "created_at",
            "created_by", "created_by_detail", "is_usable", "is_expired",
            "unavailable_reason", "remaining_uses",
        ]
        read_only_fields = fields

    def get_section_detail(self, obj):
        return {
            "id": obj.section_id,
            "name": obj.section.name,
            "school_class_id": obj.section.school_class_id,
            "school_class_name": obj.section.school_class.name,
        }


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
            # COMMUNICATION — audience + scheduling are writable; kind/status/published_at/
            # recipient_count are set by the server only (an emergency goes through
            # `notices/emergency/`, a scheduled notice through `scheduled_for`).
            "kind", "audience", "status", "scheduled_for", "published_at", "recipient_count",
        ]
        read_only_fields = ["id", "posted_by", "created_at", "kind", "status", "published_at", "recipient_count"]
        # Teacher quick-send: sirf title se notice bhejna allowed hai.
        extra_kwargs = {"body": {"required": False, "allow_blank": True}}

    def validate(self, attrs):
        campus = attrs.get("campus", getattr(self.instance, "campus", None))
        for field in ("department", "school_class", "section"):
            value = attrs.get(field, getattr(self.instance, field, None))
            if value is None:
                continue
            scope_campus_id = value.campus_id if field != "section" else value.school_class.campus_id
            if campus and scope_campus_id != campus.id:
                raise serializers.ValidationError({field: "Doesn't belong to the given campus."})
        scheduled_for = attrs.get("scheduled_for")
        if scheduled_for is not None:
            try:
                notice_rules.validate_schedule(scheduled_for, timezone.now())
            except notice_rules.NoticeRuleError as exc:
                raise serializers.ValidationError({"scheduled_for": exc.message})
        return attrs


# ============================================================
# Phase 4 — tuition classes
# ============================================================
class CampusLiveSessionSerializer(serializers.ModelSerializer):
    """
    T4 §G — `mode` (online/offline) is writable by staff. For a viewer who
    is NOT active staff of the campus, an OFFLINE session's `scheduled_at`
    is replaced with `null` and `time_hidden=true` / `label="Offline
    class"` are added (rule lives in `campus.class_schedule`). Staff always
    get the real time. Response shape is otherwise unchanged (additive).
    """

    class Meta:
        model = CampusLiveSession
        fields = [
            "id", "section", "subject", "teacher", "scheduled_at", "status", "room_id", "mode",
            # Live class (one-tap go-live): real start/end + the period it came from.
            "timetable_entry", "occurrence_date", "started_at", "ended_at", "attendance_built_at",
            # Recorded class — state only; the playable URL is served by /recordings/{id}/.
            "recording_status", "recording_hidden", "recording_duration_seconds",
        ]
        read_only_fields = [
            "id", "status", "room_id",
            "timetable_entry", "occurrence_date", "started_at", "ended_at", "attendance_built_at",
            "recording_status", "recording_hidden", "recording_duration_seconds",
        ]

    def to_representation(self, instance):
        from .class_schedule import OFFLINE_LABEL, time_visible_to, viewer_is_staff_for

        data = super().to_representation(instance)
        campus_id = instance.section.school_class.campus_id
        visible = time_visible_to(instance.mode, viewer_is_staff_for(self.context.get("request"), campus_id))
        data["time_hidden"] = not visible
        data["label"] = OFFLINE_LABEL if instance.mode == ClassMode.OFFLINE else None
        if not visible:
            data["scheduled_at"] = None
        return data

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


class TimetableConflictCheckSerializer(serializers.Serializer):
    """Input of POST /timetable-entries/check-conflicts/ (nothing is saved)."""

    section = serializers.PrimaryKeyRelatedField(queryset=Section.objects.select_related("school_class"))
    staff = serializers.PrimaryKeyRelatedField(queryset=StaffProfile.objects.all())
    time_slot = serializers.PrimaryKeyRelatedField(queryset=TimeSlot.objects.all())
    session = serializers.PrimaryKeyRelatedField(queryset=AcademicSession.objects.all())
    room = serializers.PrimaryKeyRelatedField(queryset=Room.objects.all(), required=False, allow_null=True)
    entry = serializers.PrimaryKeyRelatedField(queryset=TimetableEntry.objects.all(), required=False, allow_null=True)

    def validate(self, attrs):
        campus_id = attrs["section"].school_class.campus_id
        others = [attrs["staff"], attrs["time_slot"], attrs["session"]] + ([attrs["room"]] if attrs.get("room") else [])
        if any(o.campus_id != campus_id for o in others):
            raise serializers.ValidationError("Section, staff, time slot, room and session must belong to the same campus.")
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
        fields = ["id", "section", "subject", "staff", "time_slot", "room", "session", "mode"]
        read_only_fields = ["id"]

    def to_representation(self, instance):
        """T4 §G — students/parents never receive the slot of an OFFLINE
        period: `time_slot` is `null`, `time_slot_detail` is `null`,
        `time_hidden=true`, `label="Offline class"`. Online periods (and
        everything for staff) carry the real `time_slot` plus an inline
        `time_slot_detail` so the app needn't make a second call."""
        from .class_schedule import OFFLINE_LABEL, time_visible_to, viewer_is_staff_for

        data = super().to_representation(instance)
        campus_id = instance.section.school_class.campus_id
        visible = time_visible_to(instance.mode, viewer_is_staff_for(self.context.get("request"), campus_id))
        data["time_hidden"] = not visible
        data["label"] = OFFLINE_LABEL if instance.mode == ClassMode.OFFLINE else None
        if visible:
            slot = instance.time_slot
            data["time_slot_detail"] = {
                "id": str(slot.id),
                "day_of_week": slot.day_of_week,
                "start_time": slot.start_time.isoformat(),
                "end_time": slot.end_time.isoformat(),
                "label": slot.label,
            }
        else:
            data["time_slot"] = None
            data["time_slot_detail"] = None
        return data


class AttendanceSerializer(serializers.ModelSerializer):
    # Galti-sudhaar: required (by the view) when an already-marked row's status
    # is changed — see campus/attendance_edit.py. Write-only, never echoed back.
    reason = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=300)

    class Meta:
        model = Attendance
        fields = [
            "id", "enrollment", "date", "subject", "status", "marked_by",
            "marked_at", "last_edited_at", "edit_count", "reason",
            # Live class auto attendance: `auto` rows come from a live session.
            "source", "minutes_present", "live_session",
        ]
        read_only_fields = [
            "id", "marked_by", "marked_at", "last_edited_at", "edit_count",
            "source", "minutes_present", "live_session",
        ]

    def validate(self, attrs):
        # An existing record can only have its STATUS corrected; moving it to
        # another student/day/subject would bypass the window + audit trail.
        if self.instance is not None:
            for field in ("enrollment", "date", "subject"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Cannot be changed on an existing attendance record - correct its status instead."}
                    )
        return attrs


# ============================================================
# Phase 6 — assigmentss & syllabus
# ============================================================
class assigmentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = assigments
        fields = [
            "id", "section", "subject", "posted_by", "title", "description",
            "attachment", "due_date", "session",
        ]
        read_only_fields = ["id", "posted_by"]


class assigmentsSubmissionSerializer(serializers.ModelSerializer):
    student_detail = MinimalUserSerializer(source="student", read_only=True)

    class Meta:
        model = assigmentsSubmission
        fields = [
            "id", "assigments", "student", "student_detail", "submitted_at",
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
    # Marks go out as JSON numbers, not strings (DRF's default for Decimal),
    # so the Flutter client can read them as `num` without a parse step.
    marks_obtained = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=False)
    max_marks = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=False)
    grade = serializers.SerializerMethodField()
    grade_point = serializers.SerializerMethodField()
    passed = serializers.SerializerMethodField()
    is_locked = serializers.SerializerMethodField()

    class Meta:
        model = ResultEntry
        fields = [
            "id", "enrollment", "subject", "exam_term", "marks_obtained",
            "max_marks", "remarks", "entered_by", "status", "published_at",
            "locked_at", "is_locked", "grade", "grade_point", "passed",
        ]
        read_only_fields = ["id", "entered_by", "status", "published_at", "locked_at"]

    def _pct(self, obj):
        from . import grading

        return grading.percent_of(obj.marks_obtained, obj.max_marks)

    def get_grade(self, obj):
        from . import grading

        return grading.grade_for_percent(self._pct(obj), grading.campus_scale(obj.enrollment.section.school_class.campus))

    def get_grade_point(self, obj):
        """GPA campuses only (`Campus.grading_system == "gpa"`); everyone else gets null."""
        from . import grading

        campus = obj.enrollment.section.school_class.campus
        if not grading.uses_points(campus):
            return None
        return grading.grade_point_for_percent(self._pct(obj), grading.campus_scale(campus))

    def get_passed(self, obj):
        from . import grading

        return grading.is_pass(self._pct(obj), obj.enrollment.section.school_class.campus.pass_percent)

    def get_is_locked(self, obj):
        return obj.locked_at is not None

    def validate(self, attrs):
        if self.instance is not None:
            # A row's identity never changes — edit marks, or delete + re-create.
            # (Also stops a PATCH from moving a row into a section the caller
            # was never checked against.)
            for field in ("enrollment", "subject", "exam_term"):
                if field in attrs and attrs[field] != getattr(self.instance, field):
                    raise serializers.ValidationError({field: "Cannot be changed."})
        obtained = attrs.get("marks_obtained", getattr(self.instance, "marks_obtained", None))
        max_marks = attrs.get("max_marks", getattr(self.instance, "max_marks", None))
        if max_marks is not None and max_marks <= 0:
            raise serializers.ValidationError({"max_marks": "Must be greater than 0."})
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
        fields = [
            "id", "campus", "school_class", "session", "title", "amount", "due_date", "is_active",
            "category", "allow_concession", "late_fine_type", "late_fine_value", "late_fine_grace_days",
            "late_fine_cap",
        ]
        read_only_fields = ["id"]

    def validate(self, attrs):
        inst = self.instance
        kind = attrs.get("late_fine_type", inst.late_fine_type if inst else FeeStructure.FineType.NONE)
        value = attrs.get("late_fine_value", inst.late_fine_value if inst else 0)
        cap = attrs.get("late_fine_cap", inst.late_fine_cap if inst else None)
        if kind != FeeStructure.FineType.NONE and not value:
            raise serializers.ValidationError({"late_fine_value": "Enter the fine amount / percent."})
        if kind == FeeStructure.FineType.PERCENT and value and value > 100:
            raise serializers.ValidationError({"late_fine_value": "A percent fine cannot exceed 100."})
        if value is not None and value < 0:
            raise serializers.ValidationError({"late_fine_value": "Cannot be negative."})
        if cap is not None and cap < 0:
            raise serializers.ValidationError({"late_fine_cap": "Cannot be negative."})
        return attrs


class FeeInvoiceSerializer(serializers.ModelSerializer):
    amount_paid = serializers.ReadOnlyField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = FeeInvoice
        fields = [
            "id", "enrollment", "fee_structure", "amount_due", "amount_paid", "status", "created_at",
            "invoice_no", "base_amount", "concession_amount", "fine_amount", "fine_waived", "adjustment_note",
            "balance",
        ]
        read_only_fields = [
            "id", "status", "created_at", "invoice_no", "base_amount", "concession_amount", "fine_amount",
            "fine_waived", "adjustment_note",
        ]

    def get_balance(self, obj):
        return str(max(fr.money(obj.amount_due) - fr.money(obj.amount_paid), fr.ZERO))

    def update(self, instance, validated_data):
        # `amount_due` stays editable for old callers, but the breakdown is the source of truth:
        # translate it into a new base so  base - concession + fine  still equals what was asked.
        if "amount_due" in validated_data:
            wanted = fr.money(validated_data.pop("amount_due"))
            instance.base_amount = max(wanted - fr.money(instance.fine_amount) + fr.money(instance.concession_amount), fr.ZERO)
            instance.amount_due = fr.amount_due(instance.base_amount, instance.concession_amount, instance.fine_amount)
            instance.save(update_fields=["base_amount", "amount_due"])
            instance.recompute_status()
        return super().update(instance, validated_data)


class FeeConcessionSerializer(serializers.ModelSerializer):
    grants_count = serializers.SerializerMethodField()

    class Meta:
        model = FeeConcession
        fields = ["id", "campus", "name", "reason", "kind", "value", "applies_to_category", "is_active",
                  "created_at", "grants_count"]
        read_only_fields = ["id", "created_at", "grants_count"]

    def get_grants_count(self, obj):
        pre = getattr(obj, "active_grants_ann", None)  # [PERF] set by FeeConcessionViewSet list/detail
        return pre if pre is not None else obj.grants.filter(is_active=True).count()

    def validate(self, attrs):
        kind = attrs.get("kind", self.instance.kind if self.instance else "percent")
        value = attrs.get("value", self.instance.value if self.instance else None)
        if value is None or value <= 0:
            raise serializers.ValidationError({"value": "Must be greater than zero."})
        if kind == FeeConcession.Kind.PERCENT and value > 100:
            raise serializers.ValidationError({"value": "A percent concession cannot exceed 100."})
        cat = attrs.get("applies_to_category", self.instance.applies_to_category if self.instance else "")
        if cat and cat not in FeeStructure.Category.values:
            raise serializers.ValidationError({"applies_to_category": "Unknown fee category."})
        return attrs


class FeeConcessionGrantSerializer(serializers.ModelSerializer):
    concession_name = serializers.CharField(source="concession.name", read_only=True)
    student_name = serializers.SerializerMethodField()
    fee_title = serializers.CharField(source="fee_structure.title", read_only=True, default=None)

    class Meta:
        model = FeeConcessionGrant
        fields = ["id", "concession", "concession_name", "enrollment", "student_name", "fee_structure",
                  "fee_title", "note", "is_active", "granted_by", "created_at", "revoked_at"]
        read_only_fields = ["id", "granted_by", "created_at", "revoked_at", "is_active"]

    def get_student_name(self, obj):
        u = obj.enrollment.student
        return (f"{u.first_name} {u.last_name}".strip() or u.username)

    def validate(self, attrs):
        concession = attrs.get("concession") or (self.instance.concession if self.instance else None)
        enrollment = attrs.get("enrollment") or (self.instance.enrollment if self.instance else None)
        fs = attrs.get("fee_structure", self.instance.fee_structure if self.instance else None)
        campus_id = enrollment.section.school_class.campus_id
        if concession.campus_id != campus_id:
            raise serializers.ValidationError("Concession and student belong to different campuses.")
        if not concession.is_active:
            raise serializers.ValidationError({"concession": "This concession is switched off."})
        if fs is not None:
            if fs.campus_id != campus_id or fs.session_id != enrollment.session_id:
                raise serializers.ValidationError({"fee_structure": "Fee is not part of this student's session."})
            if not fs.allow_concession:
                raise serializers.ValidationError({"fee_structure": "This fee does not allow concessions."})
        return attrs


class FeeReconciliationItemSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    invoice_no = serializers.SerializerMethodField()

    class Meta:
        model = FeeReconciliationItem
        fields = [
            "id", "run", "category", "severity", "suggested_action", "detail", "payment", "invoice",
            "invoice_no", "student_name", "gateway_order_id", "gateway_payment_id", "our_amount",
            "gateway_amount", "our_status", "gateway_status", "gateway_method", "gateway_fee", "gateway_tax",
            "settlement_id", "resolution", "resolution_action", "resolution_note", "resolved_at",
        ]
        read_only_fields = fields

    def _invoice(self, obj):
        if obj.invoice_id:
            return obj.invoice
        return obj.payment.invoice if obj.payment_id else None

    def get_invoice_no(self, obj):
        inv = self._invoice(obj)
        return inv.invoice_no if inv else ""

    def get_student_name(self, obj):
        inv = self._invoice(obj)
        if not inv:
            return ""
        u = inv.enrollment.student
        return (f"{u.first_name} {u.last_name}".strip() or u.username)


class FeeReconciliationRunSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    open_items = serializers.SerializerMethodField()

    class Meta:
        model = FeeReconciliationRun
        fields = ["id", "campus", "source", "period_from", "period_to", "file_name", "created_at",
                  "created_by_name", "summary", "open_items"]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (f"{u.first_name} {u.last_name}".strip() or u.username) if u else ""

    def get_open_items(self, obj):
        pre = getattr(obj, "open_items_ann", None)  # [PERF] set by FeeReconciliationRunViewSet
        if pre is not None:
            return pre
        return obj.items.filter(resolution="open", severity__in=("warn", "error")).count()


class FeePaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeePayment
        fields = [
            "id", "invoice", "amount", "paid_by", "payer_role", "payment_mode",
            "status", "gateway_reference", "gateway_payment_id", "gateway_method",
            "recorded_by", "notes", "created_at", "paid_at",
        ]
        read_only_fields = [
            "id", "status", "recorded_by", "created_at", "gateway_payment_id", "gateway_method", "paid_at",
        ]

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("amount must be positive.")
        return value


class CampusAnalyticsSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CampusAnalyticsSnapshot
        fields = ["id", "campus", "session", "computed_at", "data"]
        read_only_fields = fields


# ============================================================
# T4 §F — doubts
# ============================================================
class CampusDoubtReplySerializer(serializers.ModelSerializer):
    author_detail = MinimalUserSerializer(source="author", read_only=True)

    class Meta:
        model = CampusDoubtReply
        fields = ["id", "doubt", "author", "author_detail", "text", "attachment", "is_staff_reply", "created_at"]
        read_only_fields = ["id", "doubt", "author", "is_staff_reply", "created_at"]

    def validate_text(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Reply can't be empty.")
        return value


class CampusDoubtSerializer(serializers.ModelSerializer):
    """`campus`/`session` are derived server-side from the section — never
    client-supplied. `status`/`resolved_*` only change through the
    `resolve`/`reopen` actions and by a staff reply."""

    author_detail = MinimalUserSerializer(source="author", read_only=True)
    subject_name = serializers.CharField(source="subject.name", read_only=True)
    replies_count = serializers.SerializerMethodField()

    class Meta:
        model = CampusDoubt
        fields = [
            "id", "campus", "session", "section", "subject", "subject_name", "author", "author_detail",
            "text", "attachment", "status", "is_public", "resolved_by", "resolved_at",
            "replies_count", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "campus", "session", "author", "status", "resolved_by", "resolved_at",
            "created_at", "updated_at",
        ]

    def get_replies_count(self, obj):
        annotated = getattr(obj, "replies_count", None)
        if annotated is not None:
            return annotated
        return obj.replies.filter(is_active=True).count()

    def validate_text(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Doubt text can't be empty.")
        return value


class CampusDoubtDetailSerializer(CampusDoubtSerializer):
    replies = serializers.SerializerMethodField()

    class Meta(CampusDoubtSerializer.Meta):
        fields = CampusDoubtSerializer.Meta.fields + ["replies"]

    def get_replies(self, obj):
        replies = [r for r in obj.replies.all() if r.is_active]
        return CampusDoubtReplySerializer(replies, many=True, context=self.context).data


# ============================================================
# Homework Diary (daily, class-wise)
# ============================================================
# A teacher may back-fill yesterday's diary or plan a few days ahead, but a
# fat-fingered year (2025 -> 2205) must not pollute every student's diary.
HOMEWORK_MAX_BACKDATE_DAYS = 14
HOMEWORK_MAX_AHEAD_DAYS = 30
HOMEWORK_MAX_DUE_SPAN_DAYS = 180


class HomeworkDiaryEntrySerializer(serializers.ModelSerializer):
    """`campus` / `session` / `posted_by` are derived server-side (never
    client-supplied). `section` is fixed at create time — to "move" a diary
    line, delete it and post it again; that keeps the push audience honest.

    `remove_attachment=true` (PATCH) drops the current file without
    uploading a new one."""

    subject_name = serializers.CharField(source="subject.name", read_only=True)
    section_label = serializers.SerializerMethodField()
    posted_by_detail = MinimalUserSerializer(source="posted_by", read_only=True)
    attachment_name = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    remove_attachment = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = HomeworkDiaryEntry
        fields = [
            "id", "campus", "session", "section", "section_label", "subject", "subject_name",
            "posted_by", "posted_by_detail", "title", "description", "diary_date", "due_date",
            "attachment", "attachment_name", "is_overdue", "remove_attachment",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "campus", "session", "posted_by", "created_at", "updated_at"]
        extra_kwargs = {
            "description": {"required": False, "allow_blank": True},
            "diary_date": {"required": False},
            "attachment": {"required": False, "allow_null": True},
        }

    def get_section_label(self, obj):
        section = obj.section
        return f"{section.school_class.name} - {section.name}"

    def get_attachment_name(self, obj):
        return os.path.basename(obj.attachment.name) if obj.attachment else ""

    def get_is_overdue(self, obj):
        return obj.due_date < timezone.localdate()

    def validate_title(self, value):
        value = (value or "").strip()
        if not value:
            raise serializers.ValidationError("Homework can't be empty.")
        return value

    def validate(self, attrs):
        instance = self.instance
        section = attrs.get("section", getattr(instance, "section", None))
        subject = attrs.get("subject", getattr(instance, "subject", None))

        if instance is not None and "section" in attrs and attrs["section"].pk != instance.section_id:
            raise serializers.ValidationError(
                {"section": "A diary entry can't be moved to another section — delete it and post again."}
            )
        if section is not None and subject is not None and subject.campus_id != section.school_class.campus_id:
            raise serializers.ValidationError({"subject": "This subject doesn't belong to the section's campus."})

        today = timezone.localdate()
        diary_date = attrs.get("diary_date", getattr(instance, "diary_date", None)) or today
        due_date = attrs.get("due_date", getattr(instance, "due_date", None))

        diary_date_changed = "diary_date" in attrs and (instance is None or attrs["diary_date"] != instance.diary_date)
        if diary_date_changed:
            if diary_date < today - timedelta(days=HOMEWORK_MAX_BACKDATE_DAYS):
                raise serializers.ValidationError(
                    {"diary_date": f"Diary date can be at most {HOMEWORK_MAX_BACKDATE_DAYS} days in the past."}
                )
            if diary_date > today + timedelta(days=HOMEWORK_MAX_AHEAD_DAYS):
                raise serializers.ValidationError(
                    {"diary_date": f"Diary date can be at most {HOMEWORK_MAX_AHEAD_DAYS} days ahead."}
                )
        if due_date is not None:
            if due_date < diary_date:
                raise serializers.ValidationError({"due_date": "Due date can't be before the diary date."})
            if due_date > diary_date + timedelta(days=HOMEWORK_MAX_DUE_SPAN_DAYS):
                raise serializers.ValidationError(
                    {"due_date": f"Due date can be at most {HOMEWORK_MAX_DUE_SPAN_DAYS} days after the diary date."}
                )
        return attrs

    def create(self, validated_data):
        validated_data.pop("remove_attachment", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        remove = validated_data.pop("remove_attachment", False)
        old_name = instance.attachment.name if instance.attachment else None
        storage = instance.attachment.storage if instance.attachment else None
        replaced = bool(validated_data.get("attachment"))
        if remove and not replaced:
            validated_data["attachment"] = None
        instance = super().update(instance, validated_data)
        new_name = instance.attachment.name if instance.attachment else None
        if old_name and old_name != new_name and (remove or replaced):
            try:  # best-effort: an orphaned file must never fail the edit
                storage.delete(old_name)
            except Exception:  # pragma: no cover
                pass
        return instance


# ---------------------------------------------------------------------------
# Staff leave (campus/staff_leave.py, campus/staff_leave_views.py)
# ---------------------------------------------------------------------------
class StaffLeaveApplySerializer(serializers.Serializer):
    """Input for applying. Business rules (dates, overlap, ...) live in
    `campus.staff_leave.apply_for_leave` and fail with a stable `code`."""

    campus = serializers.UUIDField(required=False)
    # An admin / principal recording leave FOR this staff member (StaffProfile id).
    staff = serializers.UUIDField(required=False)
    leave_type = serializers.ChoiceField(choices=StaffLeave.LeaveType.choices)
    date_from = serializers.DateField()
    date_to = serializers.DateField(required=False, allow_null=True)
    is_half_day = serializers.BooleanField(required=False, default=False)
    half_day_part = serializers.ChoiceField(choices=StaffLeave.HalfDayPart.choices, required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class StaffLeaveEditSerializer(serializers.Serializer):
    """Input for editing a PENDING leave: send only what changes."""

    leave_type = serializers.ChoiceField(choices=StaffLeave.LeaveType.choices, required=False)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False, allow_null=True)
    is_half_day = serializers.BooleanField(required=False)
    half_day_part = serializers.ChoiceField(choices=StaffLeave.HalfDayPart.choices, required=False)
    reason = serializers.CharField(required=False, allow_blank=True, max_length=300)


class StaffLeaveSerializer(serializers.ModelSerializer):
    """Output only. `can_decide` / `can_cancel` tell the app which buttons to
    show to THIS user (the server re-checks on every action)."""

    staff_name = serializers.SerializerMethodField()
    staff_role = serializers.CharField(source="staff.role", read_only=True)
    leave_type_label = serializers.CharField(source="get_leave_type_display", read_only=True)
    days = serializers.FloatField(read_only=True)
    decided_by_name = serializers.SerializerMethodField()
    applied_by_name = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    can_decide = serializers.SerializerMethodField()
    can_cancel = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()

    class Meta:
        model = StaffLeave
        fields = [
            "id", "campus", "staff", "staff_name", "staff_role", "leave_type", "leave_type_label",
            "date_from", "date_to", "is_half_day", "half_day_part", "days", "reason", "status", "created_at",
            "edited_at", "applied_by", "applied_by_name",
            "decided_by", "decided_by_name", "decided_at", "decision_note",
            "cancelled_at", "absence", "is_mine", "can_decide", "can_cancel", "can_edit",
        ]
        read_only_fields = fields

    def _me(self):
        request = self.context.get("request")
        return getattr(request, "user", None)

    def _is_admin(self, obj):
        return obj.campus_id in self.context.get("admin_campus_ids", ())

    def get_staff_name(self, obj):
        u = obj.staff.user
        return (f"{u.first_name} {u.last_name}".strip() or u.username)

    def get_decided_by_name(self, obj):
        u = obj.decided_by
        if u is None:
            return None
        return (f"{u.first_name} {u.last_name}".strip() or u.username)

    def get_applied_by_name(self, obj):
        u = obj.applied_by
        if u is None:
            return None
        return (f"{u.first_name} {u.last_name}".strip() or u.username)

    def get_is_mine(self, obj):
        me = self._me()
        return bool(me and obj.staff.user_id == me.pk)

    def get_can_decide(self, obj):
        if obj.status != StaffLeave.Status.PENDING or not self._is_admin(obj):
            return False
        # Your own leave: only when nobody else at the campus could decide it.
        return (not self.get_is_mine(obj)) or obj.campus_id in self.context.get("sole_approver_campus_ids", ())

    def get_can_edit(self, obj):
        return obj.status == StaffLeave.Status.PENDING and self.get_is_mine(obj)

    def get_can_cancel(self, obj):
        today = timezone.localdate()
        admin, mine = self._is_admin(obj), self.get_is_mine(obj)
        if obj.status == StaffLeave.Status.PENDING:
            return admin or mine
        if obj.status == StaffLeave.Status.APPROVED and obj.date_to >= today:
            return admin or (mine and obj.date_from > today)
        return False
