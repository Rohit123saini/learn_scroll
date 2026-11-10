# campus/views/enrollment.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..audit import AuditedModelViewSetMixin
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsRosterManagerOrReadOnly,
    is_campus_admin_or_principal,
    is_campus_approved,
)
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from ..roster import RosterError
from ..models import (
    Section,
    StudentEnrollment,
)
from ..serializers import StudentEnrollmentSerializer
from rest_framework.decorators import action
from .. import bridge
from .. import roster as campus_roster
from rest_framework import viewsets
from ._common import (
    CampusMemberScopedMixin,
    RosterAPIError,
    _truthy,
)


class StudentEnrollmentViewSet(AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    G-2 fix: enrolling a student is a "pull someone else into this
    campus" action, same reasoning as `StaffProfileViewSet` above — it
    additionally requires the section's campus to be
    `verification_status=APPROVED` (see `permissions.is_campus_approved`).

    [T4 §D] All writes go through `campus.roster` (capacity check, roll-number
    auto-assign, audit log, section chat-group sync). Writes are allowed to
    admin/principal AND the class-teacher of that section. DELETE is a SOFT
    withdraw (status=withdrawn) — enrollment history is never hard-deleted.
    Capacity: a full section returns 400 `{"code": "section_full"}`; admin /
    principal may pass `override_capacity=true`.
    """
    audit_prefix = "enrollment"
    serializer_class = StudentEnrollmentSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "student"
    member_student_path = "student"
    permission_classes = [IsAuthenticated, IsRosterManagerOrReadOnly]

    def get_queryset(self):
        # [PERF] StudentEnrollmentSerializer.student_detail reads student.* per row -> join once.
        return self.filter_queryset_to_my_campuses(StudentEnrollment.objects.select_related("student"), self.request)

    def _object_section(self):
        pk = self.kwargs.get("pk")
        if not pk:
            return None
        en = StudentEnrollment.objects.filter(pk=pk).select_related("section__school_class").first()
        return en.section if en else None

    def get_campus_id_for_permission_check(self, request):
        if self.kwargs.get("pk"):
            sec = self._object_section()
            if sec:
                return sec.school_class.campus_id
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def get_roster_section_id(self, request):
        """Section whose roster is being changed: the existing row's section
        (update/transfer/remove/delete) or the body's `section` (create)."""
        sec = self._object_section()
        if sec:
            return sec.pk
        return request.data.get("section") or None

    def perform_create(self, serializer):
        data = serializer.validated_data
        section = data["section"]
        campus_id = section.school_class.campus_id
        if not is_campus_approved(campus_id):
            raise PermissionDenied("This campus is pending platform verification and can't enroll students yet.")
        if data.get("status", StudentEnrollment.Status.ACTIVE) != StudentEnrollment.Status.ACTIVE:
            serializer.save()  # historical row import (graduated etc.) — no seat is consumed
            return
        override = _truthy(self.request.data.get("override_capacity")) and is_campus_admin_or_principal(
            self.request.user, campus_id
        )
        if StudentEnrollment.objects.filter(
            student=data["student"], section=section, session=data["session"],
            status=StudentEnrollment.Status.ACTIVE,
        ).exists():
            raise RosterAPIError(RosterError("already_enrolled", "Student is already enrolled in this section."))
        try:
            row, _created = campus_roster.enroll_student(
                section=section, student=data["student"], session=data["session"],
                roll_number=data.get("roll_number", ""), enrollment_no=data.get("enrollment_no", ""),
                actor=self.request.user, override_capacity=override,
            )
        except RosterError as exc:
            raise RosterAPIError(exc)
        serializer.instance = row

    def perform_update(self, serializer):
        old_section = serializer.instance.section
        new_section = serializer.validated_data.get("section", old_section)
        if new_section.pk != old_section.pk:
            campus_id = new_section.school_class.campus_id
            override = _truthy(self.request.data.get("override_capacity")) and is_campus_admin_or_principal(
                self.request.user, campus_id
            )
            try:
                campus_roster._check_capacity(new_section, serializer.instance.session, override)
            except RosterError as exc:
                raise RosterAPIError(exc)
        super().perform_update(serializer)
        bridge.sync_section_group(old_section)
        if new_section.pk != old_section.pk:
            bridge.sync_section_group(new_section)

    def perform_destroy(self, instance):
        campus_roster.withdraw_student(instance, actor=self.request.user, reason="deleted via API")

    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        """Move an ACTIVE student to another section (same campus + session).
        Body: `{"section": <target id>, "override_capacity": bool}`."""
        enrollment = self.get_object()
        campus_id = enrollment.section.school_class.campus_id
        target = Section.objects.filter(
            pk=request.data.get("section"), school_class__campus_id=campus_id
        ).select_related("school_class").first()
        if target is None:
            return Response({"detail": "Target section not found in this campus.", "code": "bad_target"}, status=400)
        override = _truthy(request.data.get("override_capacity")) and is_campus_admin_or_principal(request.user, campus_id)
        try:
            new = campus_roster.transfer_student(enrollment, target, actor=request.user, override_capacity=override)
        except RosterError as exc:
            raise RosterAPIError(exc)
        return Response(self.get_serializer(new).data)

    @action(detail=True, methods=["post"])
    def remove(self, request, pk=None):
        """Soft-remove (withdraw) a student from the section."""
        enrollment = self.get_object()
        en = campus_roster.withdraw_student(enrollment, actor=request.user, reason=str(request.data.get("reason", ""))[:200])
        return Response(self.get_serializer(en).data)
