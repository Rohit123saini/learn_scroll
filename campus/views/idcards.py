# campus/views/idcards.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

import uuid
from ..models import DigitalIDCard
from ..serializers import DigitalIDCardSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from ..soft_delete_views import SoftDeleteViewSetMixin
from ..permissions import is_campus_admin_or_principal
from rest_framework import viewsets
from ._common import CampusMemberScopedMixin


# ============================================================
# Phase 8 — optional / future-ready modules
# ============================================================
class DigitalIDCardViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """`qr_token` is always server-generated (never client-supplied) —
    a student can request their own card, an admin/principal can issue
    one for anyone at their campus."""
    serializer_class = DigitalIDCardSerializer
    campus_field_path = "campus"
    member_scope = "student"
    member_student_path = "user"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(DigitalIDCard.objects.all(), self.request)

    def perform_create(self, serializer):
        target_user = serializer.validated_data.get("user")
        campus = serializer.validated_data["campus"]
        if target_user.id != self.request.user.id and not is_campus_admin_or_principal(
            self.request.user, campus.id
        ):
            raise PermissionDenied("Only an admin/principal can issue a card for someone else.")
        serializer.save(qr_token=uuid.uuid4().hex)
