# campus/soft_delete_views.py
"""
ViewSet side of the soft-delete rule (model side: campus/soft_delete.py).

Put `SoftDeleteViewSetMixin` BEFORE `AuditedModelViewSetMixin` / the ModelViewSet:

    class SectionViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, ModelViewSet)

What it adds
- DELETE <id>/            -> soft delete (+ cascade), audit row `<prefix>.delete` with soft_delete=True, batch id
- GET    trash/?campus=   -> deleted rows of that campus (admin / principal only), newest first
- POST   <id>/restore/    -> undo (admin / principal only), audit row `<prefix>.restore`
Models that are not SoftDeleteModel keep whatever the viewset did before.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .audit import _campus_id_of, log_action
from .permissions import is_campus_admin_or_principal
from .soft_delete import SoftDeleteError, SoftDeleteModel


class SoftDeleteConflict(APIException):
    status_code = status.HTTP_409_CONFLICT
    default_detail = "Conflict."
    default_code = "conflict"


def _refuse(err):
    """SoftDeleteError -> structured API error {detail, code}."""
    body = {"detail": err.message, "code": err.code}
    if err.status == 409:
        return SoftDeleteConflict(body)
    return ValidationError(body)


class SoftDeleteViewSetMixin:
    soft_delete_cascade = True  # also hide alive soft-deletable CASCADE children

    # -- helpers ---------------------------------------------------------------------------------
    def _sd_prefix(self, instance):
        return getattr(self, "audit_prefix", "") or instance._meta.model_name

    def _sd_campus_id(self, instance):
        campus_id = _campus_id_of(instance)
        if campus_id is None and instance._meta.model_name == "campus":
            campus_id = instance.pk
        return campus_id

    def _sd_model(self):
        qs = getattr(self, "queryset", None)
        return qs.model if qs is not None else self.get_queryset().model

    def before_soft_delete(self, instance):
        """Per-viewset policy hook (raise SoftDeleteError to refuse)."""

    # -- delete ----------------------------------------------------------------------------------
    def soft_delete_instance(self, instance):
        """Soft-delete + audit. Falls back to a real delete for non-soft models."""
        if not isinstance(instance, SoftDeleteModel):
            return instance.delete()
        campus_id, label = self._sd_campus_id(instance), str(instance)
        try:
            self.before_soft_delete(instance)
            batch, cascaded = instance.soft_delete(user=self.request.user, cascade=self.soft_delete_cascade)
        except SoftDeleteError as err:
            raise _refuse(err)
        prefix = self._sd_prefix(instance)
        if campus_id:
            log_action(
                campus_id, self.request.user, f"{prefix}.delete",
                target=instance, summary=f"delete {prefix} (to trash): {label}"[:300],
                soft_delete=True, batch=str(batch), cascaded=dict(cascaded),
            )

    def perform_destroy(self, instance):
        if isinstance(instance, SoftDeleteModel):
            self.soft_delete_instance(instance)
        else:
            super().perform_destroy(instance)

    # -- trash / restore -------------------------------------------------------------------------
    def _sd_require_admin(self, campus_id):
        if not campus_id or not is_campus_admin_or_principal(self.request.user, campus_id):
            raise PermissionDenied("Only a campus admin or principal can see or restore deleted records.")

    @action(detail=False, methods=["get"], url_path="trash", permission_classes=[IsAuthenticated])
    def trash(self, request):
        campus_id = request.query_params.get("campus")
        if not campus_id:
            raise ValidationError({"campus": "This query parameter is required."})
        self._sd_require_admin(campus_id)
        model = self._sd_model()
        if not issubclass(model, SoftDeleteModel):
            raise NotFound()
        cp = getattr(self, "campus_field_path", "campus")
        lookup = "id" if cp == "" else f"{cp}__id"
        qs = model.all_objects.filter(is_deleted=True, **{lookup: campus_id}).order_by("-deleted_at")
        page = self.paginate_queryset(qs)
        rows = page if page is not None else list(qs)
        data = []
        for obj in rows:
            data.append({
                "id": str(obj.pk),
                "label": str(obj),
                "deleted_at": obj.deleted_at,
                "deleted_by": obj.deleted_by_id,
                "delete_batch": str(obj.delete_batch) if obj.delete_batch else None,
            })
        return self.get_paginated_response(data) if page is not None else Response(data)

    @action(detail=True, methods=["post"], url_path="restore", permission_classes=[IsAuthenticated])
    def restore(self, request, pk=None):
        model = self._sd_model()
        if not issubclass(model, SoftDeleteModel):
            raise NotFound()
        try:
            obj = model.all_objects.filter(pk=pk, is_deleted=True).first()
        except (ValueError, DjangoValidationError):  # malformed id
            obj = None
        if obj is None:
            raise NotFound("No such deleted record.")
        campus_id = self._sd_campus_id(obj)
        self._sd_require_admin(campus_id)
        label = str(obj)
        try:
            restored = obj.restore(user=request.user, cascade=self.soft_delete_cascade)
        except SoftDeleteError as err:
            raise _refuse(err)
        prefix = self._sd_prefix(obj)
        log_action(
            campus_id, request.user, f"{prefix}.restore", target=obj,
            summary=f"restore {prefix}: {label}"[:300], cascaded=dict(restored),
        )
        return Response({"id": str(obj.pk), "restored": True, "cascaded": dict(restored)})
