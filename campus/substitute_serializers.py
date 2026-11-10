# campus/substitute_serializers.py
"""
Serializers for the substitute-teacher API (see `campus.substitutes` for the
rules and `campus.substitute_views` for the endpoints).

Privacy: `TeacherAbsence.reason` is shown ONLY to campus admin/principal and
to the absent teacher themself — never to colleagues, students or parents.
"""
from django.utils import timezone
from rest_framework import serializers

from . import substitutes as sub_svc
from .models import StaffProfile, SubstituteAssignment, TeacherAbsence, TimetableEntry
from .permissions import is_campus_admin_or_principal


def _staff_name(staff) -> str:
    return sub_svc.display_name(staff.user) if staff is not None else ""


def _section_label(section) -> str:
    return f"{section.school_class.name} - {section.name}"


class TeacherAbsenceSerializer(serializers.ModelSerializer):
    """`campus` is derived from `staff` (never client-supplied). `date_to`
    defaults to `date_from` (a one-day absence is the common case)."""

    staff_name = serializers.SerializerMethodField()
    marked_by_name = serializers.SerializerMethodField()
    periods_total = serializers.SerializerMethodField()
    periods_covered = serializers.SerializerMethodField()
    periods_uncovered = serializers.SerializerMethodField()

    class Meta:
        model = TeacherAbsence
        fields = [
            "id", "campus", "staff", "staff_name", "date_from", "date_to", "part", "reason",
            "is_active", "marked_by", "marked_by_name", "created_at", "cancelled_at",
            "periods_total", "periods_covered", "periods_uncovered",
        ]
        read_only_fields = ["id", "campus", "part", "is_active", "marked_by", "created_at", "cancelled_at"]
        extra_kwargs = {
            "date_to": {"required": False},
            "reason": {"required": False, "allow_blank": True},
        }

    # ---- computed ------------------------------------------------------------
    def get_staff_name(self, obj):
        return _staff_name(obj.staff)

    def get_marked_by_name(self, obj):
        return sub_svc.display_name(obj.marked_by) if obj.marked_by_id else ""

    def _counts(self, obj):
        cache = self.context.setdefault("_absence_counts", {})
        if obj.pk not in cache:
            try:
                rows = sub_svc.periods_for_absence(obj)
            except sub_svc.SubstituteError:  # a legacy/odd row longer than the cap
                rows = []
            covered = sum(1 for r in rows if r["substitution"] is not None)
            cache[obj.pk] = (len(rows), covered)
        return cache[obj.pk]

    def get_periods_total(self, obj):
        return self._counts(obj)[0]

    def get_periods_covered(self, obj):
        return self._counts(obj)[1]

    def get_periods_uncovered(self, obj):
        total, covered = self._counts(obj)
        return total - covered

    # ---- validation -------------------------------------------------------------
    def validate_staff(self, staff):
        if not staff.is_active:
            raise serializers.ValidationError("This staff member is not active.")
        return staff

    def validate(self, attrs):
        staff = attrs["staff"]
        date_from = attrs["date_from"]
        date_to = attrs.get("date_to") or date_from
        attrs["date_to"] = date_to
        try:
            sub_svc.clamp_range(date_from, date_to)
        except sub_svc.SubstituteError as exc:
            raise serializers.ValidationError({"date_to": exc.message})
        if date_to < timezone.localdate():
            raise serializers.ValidationError({"date_to": "This absence is already over."})
        clash = sub_svc.overlapping_absence(staff.pk, date_from, date_to)
        if clash is not None:
            raise serializers.ValidationError(
                {"date_from": f"Already marked absent {clash.date_from} to {clash.date_to}. Cancel that one first."}
            )
        attrs["reason"] = (attrs.get("reason") or "").strip()
        return attrs

    # ---- privacy -------------------------------------------------------------------
    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        may_see_reason = bool(
            user is not None
            and getattr(user, "is_authenticated", False)
            and (instance.staff.user_id == user.pk or is_campus_admin_or_principal(user, instance.campus_id))
        )
        if not may_see_reason:
            data["reason"] = ""
        return data


class SubstituteAssignmentSerializer(serializers.ModelSerializer):
    """Write: `entry`, `date`, `substitute`, `note`. Everything else is derived
    (campus / original teacher / absence link / assigner) by
    `substitutes.assign_substitute`."""

    substitute_name = serializers.SerializerMethodField()
    original_staff_name = serializers.SerializerMethodField()
    subject = serializers.SerializerMethodField()
    subject_name = serializers.SerializerMethodField()
    section = serializers.SerializerMethodField()
    section_label = serializers.SerializerMethodField()
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    mode = serializers.SerializerMethodField()
    entry = serializers.PrimaryKeyRelatedField(queryset=TimetableEntry.objects.select_related(
        "time_slot", "session", "subject", "section__school_class", "staff__user"))
    substitute = serializers.PrimaryKeyRelatedField(queryset=StaffProfile.objects.select_related("user"))

    class Meta:
        model = SubstituteAssignment
        fields = [
            "id", "campus", "entry", "date", "status", "note",
            "substitute", "substitute_name", "original_staff", "original_staff_name",
            "absence", "assigned_by", "created_at", "cancelled_at",
            "subject", "subject_name", "section", "section_label", "start_time", "end_time", "mode",
        ]
        read_only_fields = [
            "id", "campus", "status", "original_staff", "absence", "assigned_by", "created_at", "cancelled_at",
        ]
        extra_kwargs = {"note": {"required": False, "allow_blank": True}}

    def get_substitute_name(self, obj):
        return _staff_name(obj.substitute)

    def get_original_staff_name(self, obj):
        return _staff_name(obj.original_staff)

    def get_subject(self, obj):
        return str(obj.entry.subject_id)

    def get_subject_name(self, obj):
        return obj.entry.subject.name

    def get_section(self, obj):
        return str(obj.entry.section_id)

    def get_section_label(self, obj):
        return _section_label(obj.entry.section)

    # Assignment lists are staff-only (students use the timetable overlay), so
    # the real period time is always safe to include here.
    def get_start_time(self, obj):
        return obj.entry.time_slot.start_time.isoformat()

    def get_end_time(self, obj):
        return obj.entry.time_slot.end_time.isoformat()

    def get_mode(self, obj):
        return obj.entry.mode

    def validate_note(self, value):
        return (value or "").strip()[:200]
