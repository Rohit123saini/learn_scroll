# campus/views/syllabus.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from rest_framework.permissions import IsAuthenticated
from ..permissions import IsSectionSubjectStaffOrReadOnly
from rest_framework.response import Response
from ..models import (
    Section,
    StaffProfile,
    SyllabusProgress,
    SyllabusUnit,
)
from ..soft_delete_views import SoftDeleteViewSetMixin
from ..serializers import (
    SyllabusProgressSerializer,
    SyllabusUnitSerializer,
)
from rest_framework.decorators import action
from rest_framework import (
    mixins,
    viewsets,
)
from django.utils import timezone
from ._common import CampusMemberScopedMixin


# ============================================================
# Phase 6 — syllabus tracker
# ============================================================
class SyllabusUnitViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SyllabusUnitSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "section"
    member_section_path = "section"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SyllabusUnit.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    def perform_create(self, serializer):
        unit = serializer.save()
        SyllabusProgress.objects.get_or_create(syllabus_unit=unit)


class SyllabusProgressViewSet(CampusMemberScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Read + the `mark-covered` action only — there is no generic
    create/update/delete here, `SyllabusProgress` rows are created
    implicitly (a `SyllabusUnit`'s progress row should be provisioned
    when the unit is created; see `SyllabusUnitViewSet.perform_create`)
    and only ever change via `mark_covered`."""
    serializer_class = SyllabusProgressSerializer
    campus_field_path = "syllabus_unit__section__school_class__campus"
    member_scope = "section"
    member_section_path = "syllabus_unit__section"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SyllabusProgress.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        obj_id = self.kwargs.get("pk")
        if obj_id:
            obj = self.get_queryset().filter(pk=obj_id).first()
            if obj:
                unit = obj.syllabus_unit
                return unit.section.school_class.campus_id, unit.section_id, unit.subject_id
        return None, None, None

    @action(detail=True, methods=["post"], url_path="mark-covered")
    def mark_covered(self, request, pk=None):
        progress = self.get_object()
        unit = progress.syllabus_unit
        staff = StaffProfile.objects.filter(
            campus_id=unit.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        progress.covered_on = timezone.now().date()
        progress.covered_by = staff
        progress.save(update_fields=["covered_on", "covered_by"])
        return Response(self.get_serializer(progress).data)
