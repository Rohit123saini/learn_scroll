# campus/views/campus_viewset.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    Campus,
    StaffProfile,
)
from ..models import CampusAuditLog
from ..throttles import CampusReportExportThrottle
from ..serializers import CampusSerializer
from rest_framework.parsers import (
    FormParser,
    JSONParser,
    MultiPartParser,
)
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsPlatformAdmin,
    ROLE_ACTIONS,
    get_campus_role,
    hod_department_id,
    is_campus_admin_or_principal,
    is_campus_approved,
    user_can,
)
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from rest_framework.decorators import action
from .. import audit_query
from .. import dashboard as campus_dashboard
from .. import panel as campus_panel
from .. import participants as campus_participants
from ..audit import log_action
from rest_framework import (
    status,
    viewsets,
)
from django.utils import timezone
from django.db import transaction
from ._common import (
    _truthy,
    get_my_campus_ids,
)


class CampusViewSet(SoftDeleteViewSetMixin, viewsets.ModelViewSet):
    """
    POST creates a new campus — still free/self-serve, and still
    auto-provisions the creator as that campus's ADMIN `StaffProfile`
    in the same transaction, so "create a campus" always leaves you
    able to manage it (no separate "make myself admin" step). What
    G-2 changes: the campus is created with `verification_status=
    PENDING` (the model default) rather than being implicitly
    "verified" just by existing — see `Campus.VerificationStatus`'s
    docstring and `permissions.is_campus_approved` for exactly what
    that does and doesn't restrict while pending. A platform admin
    (`permissions.IsPlatformAdmin`) moves it to APPROVED/REJECTED via
    the `approve`/`reject` actions below.
    """
    serializer_class = CampusSerializer
    permission_classes = [IsAuthenticated]
    campus_field_path = ""  # a Campus is its own campus (used by the soft-delete trash)

    def get_queryset(self):
        return Campus.objects.filter(id__in=get_my_campus_ids(self.request.user)).distinct()

    def get_permissions(self):
        if self.action in ("approve", "reject"):
            return [IsAuthenticated(), IsPlatformAdmin()]
        return super().get_permissions()

    @transaction.atomic
    def perform_create(self, serializer):
        campus = serializer.save(created_by=self.request.user)
        StaffProfile.objects.create(campus=campus, user=self.request.user, role=StaffProfile.Role.ADMIN)

    # T4 §E — any campus MEMBER (a student included) could previously
    # PATCH/DELETE the campus. Only its admin/principal may.
    def perform_update(self, serializer):
        if not is_campus_admin_or_principal(self.request.user, serializer.instance.id):
            raise PermissionDenied("Only a campus admin/principal can change the campus.")
        serializer.save()

    def perform_destroy(self, instance):
        if not is_campus_admin_or_principal(self.request.user, instance.id):
            raise PermissionDenied("Only a campus admin/principal can delete the campus.")
        self.soft_delete_instance(instance)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """G-2 fix — platform-admin-only. `get_queryset` already scopes
        to the requester's own campuses, which would hide a pending
        campus from anyone but its creator; a platform admin needs to
        see and act on ANY campus regardless of membership, so this
        looks the campus up directly rather than via `get_object()`."""
        campus = Campus.objects.filter(pk=pk).first()
        if campus is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus.verification_status = Campus.VerificationStatus.APPROVED
        campus.verified_by = request.user
        campus.verified_at = timezone.now()
        campus.save(update_fields=["verification_status", "verified_by", "verified_at"])
        return Response(self.get_serializer(campus).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        """G-2 fix — platform-admin-only. See `approve` above for why
        this bypasses `get_object()`."""
        campus = Campus.objects.filter(pk=pk).first()
        if campus is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus.verification_status = Campus.VerificationStatus.REJECTED
        campus.verified_by = request.user
        campus.verified_at = timezone.now()
        campus.save(update_fields=["verification_status", "verified_by", "verified_at"])
        return Response(self.get_serializer(campus).data)

    # ------------------------------------------------------------------
    # [T4 §A-§C] participants / control panel / assignment matrix / import / audit
    # ------------------------------------------------------------------
    def _need(self, request, campus, action):
        if not user_can(request.user, campus.id, action):
            raise PermissionDenied("You don't have permission for this action in this campus.")

    @action(detail=True, methods=["get"], url_path="participants")
    def participants(self, request, pk=None):
        """§A — one paginated, role-scoped list of staff (by role) / students /
        parents. Filters: category, department, class, section, q, page, page_size."""
        campus = self.get_object()
        return Response(campus_participants.list_participants(request, campus.id))

    @action(detail=True, methods=["get"], url_path="participants/summary")
    def participants_summary(self, request, pk=None):
        campus = self.get_object()
        return Response(campus_participants.participants_summary(request, campus.id))

    @action(detail=True, methods=["get"], url_path="control-panel/overview")
    def control_panel_overview(self, request, pk=None):
        campus = self.get_object()
        self._need(request, campus, "panel.overview")
        return Response(campus_panel.overview(campus))

    @action(detail=True, methods=["get"], url_path="dashboard")
    def dashboard(self, request, pk=None):
        """Admin / principal home dashboard: today's attendance %, fees collected
        vs pending, pending approvals, active teachers/students, live classes.
        A department-limited Principal/HOD only sees their department."""
        campus = self.get_object()
        self._need(request, campus, "dashboard.view")
        return Response(campus_dashboard.dashboard(campus, department_id=hod_department_id(request.user, campus.id)))

    @action(detail=True, methods=["get"], url_path="setup-status")
    def setup_status(self, request, pk=None):
        campus = self.get_object()
        self._need(request, campus, "panel.overview")
        return Response(campus_panel.setup_status(campus))

    @action(detail=True, methods=["get"], url_path="my-permissions")
    def my_permissions(self, request, pk=None):
        """Role + which un-scoped actions the UI may show for this user."""
        campus = self.get_object()
        role = get_campus_role(request.user, campus.id)
        return Response({
            "role": role,
            "department": str(hod_department_id(request.user, campus.id) or "") or None,
            "actions": {a: (role in roles) for a, roles in ROLE_ACTIONS.items()},
        })

    @action(detail=True, methods=["get"], url_path="assignment-matrix")
    def assignment_matrix(self, request, pk=None):
        campus = self.get_object()
        self._need(request, campus, "panel.assignment_matrix")
        return Response(campus_panel.assignment_matrix(campus, hod_department_id=hod_department_id(request.user, campus.id)))

    @action(detail=True, methods=["post"], url_path="assignment-matrix/bulk-assign")
    def bulk_assign(self, request, pk=None):
        """Body: {"assignments":[{"staff","section","kind":"class_teacher"|"subject","subject"?}],
        "dry_run": bool, "replace": bool}. Approval is skipped (direct assign)."""
        campus = self.get_object()
        self._need(request, campus, "assignments.direct_assign")
        items = request.data.get("assignments")
        if not isinstance(items, list) or not items:
            return Response({"detail": "assignments must be a non-empty list."}, status=400)
        if len(items) > 500:
            return Response({"detail": "Max 500 assignments per request."}, status=400)
        return Response(campus_panel.bulk_assign(
            campus, request.user, items, dry_run=_truthy(request.data.get("dry_run")),
            replace=_truthy(request.data.get("replace")),
            hod_department_id=hod_department_id(request.user, campus.id),
        ))

    @action(detail=True, methods=["post"], url_path="bulk-import", parser_classes=[MultiPartParser, FormParser, JSONParser])
    def bulk_import(self, request, pk=None):
        """multipart: kind=staff|students|subjects|enrollments, file=<csv>,
        dry_run (default TRUE — validate only), override_capacity."""
        campus = self.get_object()
        self._need(request, campus, "bulk_import")
        kind = request.data.get("kind", "")
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"detail": "file is required (CSV)."}, status=400)
        if kind in ("staff", "students", "enrollments") and not is_campus_approved(campus.id):
            raise PermissionDenied("This campus is pending platform verification and can't add people yet.")
        dry = _truthy(request.data.get("dry_run", "true"))
        override = _truthy(request.data.get("override_capacity"))
        try:
            return Response(campus_panel.bulk_import(campus, request.user, kind, upload, dry_run=dry, override_capacity=override))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)

    @action(detail=True, methods=["get"], url_path="audit-log")
    def audit_log(self, request, pk=None):
        """Audit trail, newest first. Filters + search: see `campus/audit_query.py`
        (q, action, category, actor, target_type, target_id, since, until, order)."""
        campus = self.get_object()
        self._need(request, campus, "audit.view")
        qs = audit_query.filter_audit(
            CampusAuditLog.objects.filter(campus=campus).select_related("actor"), request.query_params
        )
        page = self.paginate_queryset(qs)
        return self.get_paginated_response([audit_query.row_dict(r) for r in page])

    @action(detail=True, methods=["get"], url_path="audit-log/facets")
    def audit_log_facets(self, request, pk=None):
        """What the filter pickers can offer: categories, actions and people that exist in this campus's log."""
        campus = self.get_object()
        self._need(request, campus, "audit.view")
        return Response(audit_query.facets(CampusAuditLog.objects.filter(campus=campus)))

    @action(detail=True, methods=["get"], url_path="audit-log/export", throttle_classes=[CampusReportExportThrottle])
    def audit_log_export(self, request, pk=None):
        """The audit log (with the same filters as the list) as a CSV file. The export itself is audit-logged."""
        campus = self.get_object()
        self._need(request, campus, "audit.view")
        qs = audit_query.filter_audit(
            CampusAuditLog.objects.filter(campus=campus).select_related("actor"), request.query_params
        )
        body, rows, truncated = audit_query.build_csv(qs)
        log_action(
            campus.id, request.user, "audit.export", target_type="Campus", target_id=campus.id,
            summary=f"Exported {rows} audit log rows" + (" (cut at the row limit)" if truncated else ""),
            rows=rows, truncated=truncated,
            filters={k: v for k, v in request.query_params.items() if v and k in (
                "q", "action", "category", "actor", "target_type", "target_id", "since", "until", "order")},
        )
        response = HttpResponse(body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="audit-log_{timezone.localdate():%Y-%m-%d}.csv"'
        response["Cache-Control"] = "no-store"
        response["X-Row-Count"] = str(rows)
        response["X-Truncated"] = "1" if truncated else "0"
        return response
