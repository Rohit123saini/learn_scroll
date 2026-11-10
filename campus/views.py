# campus/views.py
import logging
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, DecimalField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# FEE-2: fee is now paid out of the same wallet tuitionclass already uses,
# not a separate gateway — user_profile owns CoinLedger/record_transaction,
# campus only ever calls through it, never writes to CoinLedger directly
# (same boundary user_profile/models.py's own CoinLedger docstring lays out).
from user_profile.models import CoinLedger

# [Task 11] `assigmentsViewSet`/`assigmentsSubmissionViewSet` below are now
# thin proxies over the unified `assigments` app (see `campus.assigments`'s
# own [DEPRECATED] docstring in models.py) rather than campus's own
# deprecated assigments/assigmentsSubmission models — imported directly at
# module level, same reasoning as `campus.bridge`'s own Task 11 addition
# docstring gives: `assigments` is a confirmed, fully-built sibling app,
# not an unverified dependency that needs a lazy-import degrade. Aliased
# ("Unified...") so a reader never confuses these with campus's own,
# now-deprecated `assigments`/`assigmentsSubmission` models — this file no
# longer imports those at all, since nothing here touches them any more;
# the one remaining reader of the old rows is the one-time
# `migrate_campus_assigmentss_to_unified` management command.
from assignments.models import assigments as Unifiedassigments
from assignments.models import assigmentsSource as UnifiedassigmentsSource
from assignments.models import assigmentsSubmission as UnifiedassigmentsSubmission

# [Task 13] Same posture as the `assigments` imports directly above:
# `testseries` is a confirmed, fully-built sibling app for this task
# (its own `testseries/bridge.py` module docstring documents this exact
# `source="campus"` calling contract), not an unverified dependency —
# hard top-level import, no lazy-import degrade.
from testseries.models import TestAttempt, TestSeries

# (notice push now goes through bridge.notify(push=True) in NoticeViewSet.perform_create.)
# FIX (this pass) — ParentLinkVerifyView now resolves directly against
# ParentAccessCode instead of the broken bridge.resolve_parent_from_token
# unpack; see that view's docstring below.
from message.models import ParentAccessCode
# Task 5 subtask 3 — same fire-and-forget dispatch bridge.notify already
# uses for push, reused here for FeePaymentViewSet's receipt-email side
# effect (see campus/tasks.py::send_fee_receipt_email).
from core.async_utils import dispatch_after_commit

from . import bridge
from . import notice_rules
from . import notices as notices_service
from . import attendance_edit
from . import audit_query
from . import fee_gateway
from . import bulk_ops
from . import grading
from . import live_class
from . import recordings
from . import results_csv
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .receipt_pdf import PdfUnavailable, render_pdf
from . import finance, finance_rules
from .finance_views import need as finance_need
from .invoice_pdf import render_invoice_pdf
from .report_card_pdf import render_report_card_pdf
from . import progress as progress_data
from .receipt_pdf import _display_name as _receipt_display_name
from .services import compute_attendance_summary, generate_report_card_data
# [T4 §A-§D] participants / control-panel / roster / audit
from . import panel as campus_panel
from . import dashboard as campus_dashboard
from . import year_end
from . import participants as campus_participants
from . import roster as campus_roster
from .audit import AuditedModelViewSetMixin, log_action
from .soft_delete_views import SoftDeleteViewSetMixin
from .models import CampusAuditLog
from .roster import RosterError
# B-4 fix — see campus/throttles.py module docstring for why these are
# separate classes (each with its own fixed `scope`) rather than a
# shared `throttle_scope` attribute: several of them apply to different
# @action methods living on the same ViewSet.
from .throttles import (
    CampusDoubtPostThrottle,
    CampusFeePaymentThrottle,
    CampusLiveSessionJoinThrottle,
    CampusEmergencyBroadcastThrottle,
    CampusNoticePostThrottle,
    CampusParentLinkVerifyThrottle,
    CampusRecordingControlThrottle,
    CampusReportExportThrottle,
    CampusResultsImportThrottle,
    CampusResultsPublishThrottle,
)
from .models import (
    AcademicSession,
    Attendance,
    AttendanceEditLog,
    Campus,
    CampusAnalyticsSnapshot,
    CampusDoubt,
    CampusDoubtReply,
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
    Notice,
    ResultEntry,
    Room,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
    SubjectTeacherassigments,
    SubstituteAssignment,
    SyllabusProgress,
    SyllabusUnit,
    TimeSlot,
    TimetableEntry,
)
from .permissions import (
    IsCampusAdminOrPrincipal,
    IsPlatformAdmin,
    IsRosterManagerOrReadOnly,
    IsSectionSubjectStaffOrReadOnly,
    ROLE_ACTIONS,
    get_campus_role,
    hod_department_id,
    user_can,
    can_manage_section_subject,
    can_post_notice,
    is_any_active_staff,
    is_campus_admin_or_principal,
    is_campus_approved,
    is_class_teacher_of_section,
    is_linked_parent_of_student,
)
from .serializers import (
    AcademicSessionSerializer,
    AttendanceSerializer,
    CampusAnalyticsSnapshotSerializer,
    CampusDoubtDetailSerializer,
    CampusDoubtReplySerializer,
    CampusDoubtSerializer,
    CampusLiveSessionSerializer,
    CampusParentLinkSerializer,
    CampusSerializer,
    ClassTeacherassigmentsSerializer,
    DepartmentSerializer,
    DigitalIDCardSerializer,
    ExamTermSerializer,
    FeeInvoiceSerializer,
    FeePaymentSerializer,
    FeeStructureSerializer,
    NoticeSerializer,
    ResultEntrySerializer,
    RoomSerializer,
    SchoolClassSerializer,
    SectionSerializer,
    StaffProfileSerializer,
    SyllabusProgressSerializer,
    SyllabusUnitSerializer,
    TimeSlotSerializer,
    TimetableConflictCheckSerializer,
    TimetableEntrySerializer,
    StudentEnrollmentSerializer,
    SubjectSerializer,
    SubjectTeacherassigmentsSerializer,
)

from . import visibility as vis_mod  # noqa: E402
from .visibility import accessible_section_ids_and_campus_map, get_visibility  # noqa: E402

logger = logging.getLogger(__name__)


def _truthy(value):
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


class RosterAPIError(APIException):
    """400 with a stable machine `code` (e.g. section_full) + human `detail`."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        # the project's exception handler (tuitionclass.exceptions) puts this in the envelope's "code"
        self.machine_code = err.code


def get_my_campus_ids(user):
    """
    Every campus a user has *some* legitimate reason to see rows from:
    as active staff, as an enrolled student, or as a linked parent
    (design doc §11 — Parent gets read-only access to a linked
    child's campus data). Centralized here so every viewset's
    campus-visibility check — including `CampusViewSet` itself — stays
    in sync as new membership routes (e.g. parent links) get added.
    """
    return vis_mod.get_my_campus_ids(user)


class CampusMemberScopedMixin:
    """
    Shared "which campus does this row belong to" + "am I a member of
    that campus" plumbing for every viewset below. `campus_field_path`
    is a Django `__`-lookup from the model to its `Campus` FK (e.g.
    `''` for `Campus` itself, `'campus'` for a direct FK, `'school_
    class__campus'` for something scoped through `SchoolClass`).
    """
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_campus_id_for_permission_check(self, request):
        # POST/PATCH bodies carry the FK by whatever field the
        # serializer exposes as top-level `campus` (already true for
        # every model here except the ones scoped through `section`/
        # `school_class`, which override this method below), GET/
        # detail routes fall back to the object's own campus.
        campus_id = request.data.get("campus") or request.query_params.get("campus")
        if campus_id:
            return campus_id
        obj_id = self.kwargs.get("pk")
        if obj_id:
            obj = self.get_queryset().filter(pk=obj_id).first()
            if obj:
                return self._campus_id_from_instance(obj)
        return None

    def _campus_id_from_instance(self, obj):
        target = obj
        for part in self.campus_field_path.split("__"):
            target = getattr(target, part)
        return target.id

    # T4 §E — how rows are narrowed for a NON-staff member (student /
    # parent) of the campus. Active staff always keep campus-wide access.
    #   None      -> no narrowing (campus-wide data, e.g. exam terms)
    #   "section" -> `member_section_path` must be in the visible sections
    #   "student" -> `member_student_path` must be self / a linked child
    #   "none"    -> hidden from students/parents entirely
    # Override `member_scope_q(vis)` for anything custom.
    member_scope = None
    member_section_path = None
    member_student_path = None

    def member_scope_q(self, vis):
        if self.member_scope is None:
            return None
        if self.member_scope == "section":
            return Q(**{f"{self.member_section_path}__in": vis.visible_section_ids})
        if self.member_scope == "student":
            return Q(**{f"{self.member_student_path}__in": vis.visible_student_ids})
        return Q(pk__in=[])

    def filter_queryset_to_my_campuses(self, qs, request):
        vis = get_visibility(request.user, request)
        cp = self.campus_field_path
        campus_id = request.query_params.get("campus")
        if campus_id:
            qs = qs.filter(**{f"{cp}__id": campus_id})
        qs = qs.filter(**{f"{cp}__id__in": vis.member_campus_ids})
        if vis.nonstaff_campus_ids:
            member_q = self.member_scope_q(vis)
            if member_q is not None:
                qs = qs.filter(
                    Q(**{f"{cp}__id__in": vis.staff_campus_ids})
                    | (Q(**{f"{cp}__id__in": vis.nonstaff_campus_ids}) & member_q)
                )
        return qs


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


class AcademicSessionViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = AcademicSessionSerializer
    campus_field_path = "campus"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(AcademicSession.objects.all(), self.request)

    @action(detail=True, methods=["post"], url_path="set-current")
    def set_current(self, request, pk=None):
        session = self.get_object()
        if not is_campus_admin_or_principal(request.user, session.campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        session.is_current = True
        session.save(update_fields=["is_current", "updated_at"])
        return Response(self.get_serializer(session).data)

    @action(detail=True, methods=["post"])
    def rollover(self, request, pk=None):
        """Carries forward active enrollments from this campus's other
        sessions into this one (design doc §13). Queued via Celery so a
        large campus's rollover doesn't block the request; falls back
        to a synchronous call if Celery isn't configured in this
        deployment, so the endpoint still works either way."""
        from . import tasks

        new_session = self.get_object()
        if not is_campus_admin_or_principal(request.user, new_session.campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            async_result = tasks.rollover_session.delay(new_session.campus_id, new_session.id)
            return Response({"detail": "Rollover queued.", "task_id": async_result.id}, status=status.HTTP_202_ACCEPTED)
        except Exception:
            result = tasks.rollover_session(new_session.campus_id, new_session.id)
            return Response(result)


    # ------------------------------------------------------------------
    # Year-end: copy structure -> plan -> promote (campus/year_end.py).
    # `self.get_object()` is the NEW session; `from_session` is the old one.
    # Admin / principal only, and a department-limited HOD may not run it.
    # ------------------------------------------------------------------
    def _year_end_context(self, request, data):
        new_session = self.get_object()
        campus = new_session.campus
        if not user_can(request.user, campus.id, "session.promote") or hod_department_id(request.user, campus.id):
            raise PermissionDenied("Only the campus admin / principal can run the year-end promotion.")
        from_id = data.get("from_session")
        old_session = AcademicSession.objects.filter(pk=from_id, campus=campus).first() if from_id else None
        if old_session is None:
            raise ValidationError({"from_session": "Pick the old session of this campus."})
        return campus, old_session, new_session

    @staticmethod
    def _year_end_call(fn, *args, **kwargs):
        try:
            return Response(fn(*args, **kwargs))
        except year_end.YearEndError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["get"], url_path="promotion-plan")
    def promotion_plan(self, request, pk=None):
        campus, old, new = self._year_end_context(request, request.query_params)
        return self._year_end_call(year_end.plan, campus, old, new)

    @action(detail=True, methods=["post"], url_path="copy-structure")
    def copy_structure(self, request, pk=None):
        campus, old, new = self._year_end_context(request, request.data)
        return self._year_end_call(
            year_end.copy_structure, campus, request.user, old, new,
            copy_teachers=_truthy(request.data.get("copy_teachers")),
        )

    @action(detail=True, methods=["post"], url_path="promote")
    def promote(self, request, pk=None):
        """Body: from_session, mapping [{from_class, action, to_class}], overrides
        [{student, action, to_section?}], dry_run (default TRUE), create_missing_sections,
        override_capacity, set_current. Nothing is written unless dry_run is false."""
        from django.core.cache import cache

        campus, old, new = self._year_end_context(request, request.data)
        d = request.data
        dry = _truthy(d.get("dry_run", True))
        lock = f"campus:year_end:{new.pk}"
        if not dry and not cache.add(lock, 1, timeout=900):
            return Response({"detail": "A promotion is already running for this session.", "code": "busy"},
                            status=status.HTTP_409_CONFLICT)
        try:
            return self._year_end_call(
                year_end.promote, campus, request.user, old, new, d.get("mapping") or [],
                overrides=d.get("overrides") or [], dry_run=dry,
                create_missing_sections=_truthy(d.get("create_missing_sections")),
                override_capacity=_truthy(d.get("override_capacity")),
                set_current=_truthy(d.get("set_current")),
            )
        finally:
            if not dry:
                cache.delete(lock)


class DepartmentViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "department"
    serializer_class = DepartmentSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.visible_department_ids)

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Department.objects.all(), self.request)


class SchoolClassViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "class"
    serializer_class = SchoolClassSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.visible_class_ids)

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SchoolClass.objects.all(), self.request)


class SectionViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "section"
    serializer_class = SectionSerializer
    campus_field_path = "school_class__campus"
    member_scope = "section"
    member_section_path = "pk"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Section.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        # Section's campus isn't a top-level `campus` field on the
        # request body — it comes from whichever `school_class` is
        # being pointed at.
        school_class_id = request.data.get("school_class")
        if school_class_id:
            sc = SchoolClass.objects.filter(pk=school_class_id).first()
            if sc:
                return sc.campus_id
        return super().get_campus_id_for_permission_check(request)

    def perform_create(self, serializer):
        section = serializer.save()
        self._audit("create", section)
        # TASK (design doc §3) — section-group auto-creation. Routed
        # through `campus.bridge` (never a direct `message` import).
        #
        # `bridge.create_section_group()` is now hard-wired (design doc
        # §10, Group A) straight through to
        # `core.classroom_chat_bridge.create_section_group()`, which
        # raises `ValueError` unless `actor` is already this section's
        # assigned class-teacher. A section **just created in this same
        # request never has one yet** — that's a separate step, done via
        # `ClassTeacherassigmentsViewSet` afterward — so this call is
        # *expected* to fail here on every normal creation, not just on
        # some edge case. We deliberately don't let that block section
        # creation (`serializer.save()` above has already committed the
        # row): the real, correct trigger for group creation is
        # `ClassTeacherassigmentsViewSet.perform_create()` below, once a
        # class-teacher genuinely exists to be the group's creator/ADMIN.
        # This call is kept here only as an idempotent no-op fast-path
        # for the rare case a class-teacher was somehow already assigned
        # before the section row existed (e.g. a fixture/migration
        # ordering quirk) — `create_section_group()` itself is idempotent,
        # so calling it twice (once here, once from the assigments
        # viewset) is always safe.
        try:
            bridge.create_section_group(section, actor=self.request.user)
        except ValueError:
            logger.info(
                "Skipped section-group creation for section %s: no class-teacher "
                "assigned yet (expected — will be created when one is).",
                section.pk,
            )


    def _require_section_dashboard(self, request, section):
        if not user_can(
            request.user, section.school_class.campus_id, "section.dashboard", section_id=section.id
        ):
            raise PermissionDenied("Only this section's class teacher or a campus admin can view this.")

    @action(detail=True, methods=["get"])
    def dashboard(self, request, pk=None):
        """[T4 §D] Roster + enrolled/capacity count + attendance snapshot +
        pending subject-teacher requests (+ doubts placeholder)."""
        section = self.get_object()
        self._require_section_dashboard(request, section)
        return Response(campus_roster.section_dashboard(section))

    @action(detail=True, methods=["get"])
    def roster(self, request, pk=None):
        """Paginated roster (default: ACTIVE students). `?q=` name/roll search,
        `?status=` active|transferred|graduated|promoted|withdrawn|all."""
        section = self.get_object()
        self._require_section_dashboard(request, section)
        wanted = request.query_params.get("status", "active")
        qs = StudentEnrollment.objects.filter(section=section, session=section.school_class.session)
        if wanted != "all":
            qs = qs.filter(status=wanted)
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(student__username__icontains=q) | Q(student__first_name__icontains=q)
                | Q(student__last_name__icontains=q) | Q(roll_number__iexact=q)
            )
        qs = qs.select_related("student").order_by("roll_number", "student__username")
        page = self.paginate_queryset(qs)
        data = StudentEnrollmentSerializer(page, many=True).data
        payload = self.get_paginated_response(data).data
        payload["capacity"] = campus_roster.capacity_info(section)
        return Response(payload)


class SubjectViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "subject"
    serializer_class = SubjectSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(pk__in=vis.subject_ids())

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Subject.objects.all(), self.request)


class RoomViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "room"
    serializer_class = RoomSerializer
    campus_field_path = "campus"
    member_scope = "none"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Room.objects.all(), self.request)


class StaffProfileViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """Admin/Principal-HOD invite staff (design doc §11). No self-signup
    — a `StaffProfile` is always created BY an existing admin/principal
    of that campus, targeting some other user id.

    G-2 fix: inviting staff is a "pull someone else into this campus"
    action, so it additionally requires the campus to be
    `verification_status=APPROVED` (see `permissions.is_campus_approved`'s
    docstring) — an unverified campus's self-appointed admin can still
    set up sessions/classes/sections/subjects, just not staff other than
    themselves (the creator's own ADMIN row was already created directly
    in `CampusViewSet.perform_create`, bypassing this check, per that
    view's docstring).
    """
    audit_prefix = "staff"
    serializer_class = StaffProfileSerializer
    campus_field_path = "campus"
    member_scope = "none"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(StaffProfile.objects.select_related("user"), self.request)  # [PERF] user_detail

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        if not is_campus_approved(campus.id):
            raise PermissionDenied("This campus is pending platform verification and can't add staff yet.")
        self._audit("create", serializer.save())


class ClassTeacherassigmentsViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    audit_prefix = "class_teacher"
    serializer_class = ClassTeacherassigmentsSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "section"
    member_section_path = "section"

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ClassTeacherassigments.objects.select_related("staff__user"), self.request)  # [PERF] staff_detail

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def perform_create(self, serializer):
        assigments = serializer.save()
        self._audit("create", assigments)
        # This is the moment `bridge.create_section_group()`'s real
        # precondition (design doc §10) first becomes true: the section
        # now has an assigned class-teacher, who is the group's required
        # creator/ADMIN. `actor` must be *this assigments's* staff user
        # — not `self.request.user` — since an admin/principal (not the
        # teacher themself) is typically the one who creates this row.
        # `create_section_group()` is idempotent (returns the existing
        # group unchanged if one was already created — e.g. by
        # `SectionViewSet.perform_create()`'s own fast-path above), so
        # this is always safe to call, never a duplicate-create risk.
        try:
            bridge.create_section_group(assigments.section, actor=assigments.staff.user)
        except ValueError:
            # Shouldn't normally happen (we just made `assigments.staff`
            # this section's class-teacher), but kept as a defensive
            # backstop rather than letting an unexpected mismatch 500 a
            # class-teacher-assigments request — the section still gets
            # its group the next time this endpoint (or a retry) runs.
            logger.warning(
                "create_section_group() unexpectedly rejected staff %s as "
                "class-teacher for section %s right after assigning them.",
                assigments.staff_id, assigments.section_id,
            )


class SubjectTeacherassigmentsViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Create leaves `status=PENDING` (model default) — the class-teacher
    of that section approves/rejects via the two actions below (design
    doc §2's "class-teacher subject-teacher ko allow karega" flow).
    Anyone who can see the section can request; only that section's
    `ClassTeacherassigments` holder (or a campus admin/principal, as a
    fallback for when no class-teacher is assigned yet) can decide.
    """
    audit_prefix = "subject_teacher"
    serializer_class = SubjectTeacherassigmentsSerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return Q(section_id__in=vis.visible_section_ids, status=SubjectTeacherassigments.Status.APPROVED)
    # Overridden below: creating a request needs no special role (any
    # campus member can ask to teach a subject), only approve/reject do.
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(SubjectTeacherassigments.objects.select_related("staff__user", "subject"), self.request)  # [PERF] staff_detail + subject_detail

    def perform_create(self, serializer):
        """[T4 §C] Request-approval flow is unchanged (PENDING). NEW: a campus
        admin/principal may pass `direct=true` to assign a teacher straight
        away (APPROVED, approval skipped). A department-limited HOD can only do
        this inside their own department."""
        section = serializer.validated_data["section"]
        campus_id = section.school_class.campus_id
        user = self.request.user
        if _truthy(self.request.data.get("direct")) and is_campus_admin_or_principal(user, campus_id):
            hod_dept = hod_department_id(user, campus_id)
            if hod_dept and section.school_class.department_id != hod_dept:
                raise PermissionDenied("This section is outside your department.")
            actor_staff = StaffProfile.objects.filter(campus_id=campus_id, user=user, is_active=True).first()
            obj = serializer.save(
                status=SubjectTeacherassigments.Status.APPROVED, approved_by=actor_staff, responded_at=timezone.now()
            )
            bridge.notify(
                users=[obj.staff.user], notif_type=NotifTypes.STAFF_assigments_APPROVED,
                title="Subject assigned", body=f"You've been assigned {obj.subject.name} for {obj.section}.",
            )
            bridge.sync_section_group(obj.section)
            self._audit("direct_assign", obj)
            return
        self._audit("request", serializer.save())

    def _can_decide(self, user, assigments):
        if is_class_teacher_of_section(user, assigments.section_id):
            return True
        return is_campus_admin_or_principal(user, assigments.section.school_class.campus_id)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        assigments = self.get_object()
        if not self._can_decide(request.user, assigments):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assigments.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assigments.status = SubjectTeacherassigments.Status.APPROVED
        assigments.approved_by = deciding_staff
        assigments.responded_at = timezone.now()
        assigments.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        self._audit("approve", assigments)
        bridge.sync_section_group(assigments.section)
        bridge.notify(
            users=[assigments.staff.user],
            notif_type=NotifTypes.STAFF_assigments_APPROVED,
            title="Subject assigments approved",
            body=f"You're approved to teach {assigments.subject.name} for {assigments.section}.",
        )
        return Response(self.get_serializer(assigments).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        assigments = self.get_object()
        if not self._can_decide(request.user, assigments):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        deciding_staff = StaffProfile.objects.filter(
            campus_id=assigments.section.school_class.campus_id, user=request.user, is_active=True
        ).first()
        assigments.status = SubjectTeacherassigments.Status.REJECTED
        assigments.approved_by = deciding_staff
        assigments.responded_at = timezone.now()
        assigments.save(update_fields=["status", "approved_by", "responded_at", "updated_at"])
        self._audit("reject", assigments)
        bridge.notify(
            users=[assigments.staff.user],
            notif_type=NotifTypes.STAFF_assigments_REJECTED,
            title="Subject assigments rejected",
            body=f"Your request to teach {assigments.subject.name} for {assigments.section} was rejected.",
        )
        return Response(self.get_serializer(assigments).data)


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


class NoticeAPIError(APIException):
    """A `notice_rules.NoticeRuleError` as an HTTP error: human `detail` + stable machine `code`
    (the project's exception handler puts `machine_code` into the envelope's `code`)."""

    status_code = 400
    _STATUS_BY_CODE = {"notice_already_sent": 409, "not_scheduled": 409, "duplicate_emergency": 409}

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code
        self.status_code = self._STATUS_BY_CODE.get(err.code, 400)


class NoticeViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    G-1 fix: who may post at a given scope is decided by `permissions.can_post_notice` (campus
    admin/principal-HOD at any scope; a class-teacher only to their own section; nobody else).

    COMMUNICATION (rules in `notice_rules.py`, delivery in `notices.py`):
      * scope  — whole school (no scope FK) / department / class / section, as before.
      * audience — `everyone` (default) | `students` | `parents` | `staff`: who gets the push.
      * scheduled notice — send `scheduled_for` (1 min .. 90 days ahead): it is stored as
        `status=scheduled`, invisible to students/parents, and published by the every-minute task
        `campus.tasks.publish_due_notices`. Until then the poster / an admin may edit it, cancel it
        (`POST notices/{id}/cancel/`) or send it right away (`POST notices/{id}/send-now/`).
      * emergency broadcast — `POST notices/emergency/` (admin / principal only): goes out
        immediately, pinned for 24 h, push bypasses quiet hours and cannot be muted.
    `GET notices/?campus=` lists PUBLISHED notices (what the app always showed);
    `?status=scheduled|cancelled|all` is for the poster / admins (others get nothing extra).
    """
    serializer_class = NoticeSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        return (
            Q(department__isnull=True, school_class__isnull=True, section__isnull=True)
            | Q(department_id__in=vis.visible_department_ids, school_class__isnull=True, section__isnull=True)
            | Q(school_class_id__in=vis.visible_class_ids, section__isnull=True)
            | Q(section_id__in=vis.visible_section_ids)
        )
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------------ visibility
    def _admin_campus_ids(self):
        return set(
            StaffProfile.objects.filter(
                user=self.request.user, is_active=True,
                role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD],
            ).values_list("campus_id", flat=True)
        )

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(Notice.objects.select_related("posted_by"), self.request)  # [PERF] posted_by shown per row
        user = self.request.user
        vis = get_visibility(user, self.request)
        if vis.nonstaff_campus_ids:
            # a staff-only notice is not for students / parents
            qs = qs.exclude(audience=Notice.Audience.STAFF, campus_id__in=vis.nonstaff_campus_ids)
        # Scheduled / cancelled notices: only their poster and the campus admin / principal see them.
        qs = qs.filter(
            Q(status=Notice.Status.PUBLISHED) | Q(posted_by=user) | Q(campus_id__in=self._admin_campus_ids())
        )
        if self.action == "list":
            wanted = (self.request.query_params.get("status") or Notice.Status.PUBLISHED).lower()
            if wanted != "all":
                qs = qs.filter(status=wanted if wanted in notice_rules.STATUSES else Notice.Status.PUBLISHED)
        return qs

    def get_throttles(self):
        # B-4 fix — only the abuse-prone actions (posting fans out to a whole campus/section) get a
        # scoped limit; list/retrieve/update/delete keep the project-wide default.
        if self.action == "create":
            return [CampusNoticePostThrottle()]
        if self.action == "emergency":
            return [CampusEmergencyBroadcastThrottle()]
        return super().get_throttles()

    # ------------------------------------------------------------------ helpers
    def _check_scope_allowed(self, campus, department, school_class, section):
        if not can_post_notice(
            self.request.user,
            campus.id,
            department_id=department.id if department else None,
            school_class_id=school_class.id if school_class else None,
            section_id=section.id if section else None,
        ):
            raise PermissionDenied("You're not allowed to post a notice at this scope.")

    def _check_can_manage(self, notice):
        """Edit / cancel / delete: the poster, or the campus admin / principal."""
        user = self.request.user
        if notice.posted_by_id == user.id or is_campus_admin_or_principal(user, notice.campus_id):
            return
        raise PermissionDenied("Only the person who posted this notice, or the campus admin, can change it.")

    def _rule_error(self, exc):
        return NoticeAPIError(exc)

    # ------------------------------------------------------------------ create / update / delete
    def perform_create(self, serializer):
        data = serializer.validated_data
        campus = data["campus"]
        self._check_scope_allowed(campus, data.get("department"), data.get("school_class"), data.get("section"))
        now = timezone.now()
        if data.get("scheduled_for"):
            notice = serializer.save(posted_by=self.request.user, status=Notice.Status.SCHEDULED)
            return  # nothing goes out yet — `publish_due_notices` (or "send now") delivers it
        notice = serializer.save(
            posted_by=self.request.user, status=Notice.Status.PUBLISHED, published_at=now, scheduled_for=None
        )
        # Best-effort push (never blocks the 201), but no longer silent when it fails.
        notices_service.deliver(notice)

    def perform_update(self, serializer):
        notice = serializer.instance
        self._check_can_manage(notice)
        if not notice_rules.can_edit(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "notice_already_sent", "This notice has already gone out and can no longer be edited."))
        data = serializer.validated_data
        self._check_scope_allowed(
            data.get("campus", notice.campus),
            data.get("department", notice.department),
            data.get("school_class", notice.school_class),
            data.get("section", notice.section),
        )
        serializer.save()

    def perform_destroy(self, instance):
        self._check_can_manage(instance)
        self.soft_delete_instance(instance)

    # ------------------------------------------------------------------ scheduled: cancel / send now
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel a scheduled notice (it will never be pushed)."""
        notice = self.get_object()
        self._check_can_manage(notice)
        if not notice_rules.can_cancel(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "Only a scheduled notice can be cancelled."))
        updated = Notice.objects.filter(pk=notice.pk, status=Notice.Status.SCHEDULED).update(
            status=Notice.Status.CANCELLED)
        if not updated:  # the scheduler published it a moment ago
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "This notice has just been sent."))
        notice.refresh_from_db()
        log_action(notice.campus_id, request.user, "notice.cancel", target=notice, summary=notice.title)
        return Response(self.get_serializer(notice).data)

    @action(detail=True, methods=["post"], url_path="send-now")
    def send_now(self, request, pk=None):
        """Publish a scheduled notice right away (instead of waiting for its time)."""
        notice = self.get_object()
        self._check_can_manage(notice)
        if not notice_rules.can_cancel(notice.status):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "Only a scheduled notice can be sent now."))
        if not notices_service.publish_now(notice):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "not_scheduled", "This notice has already been sent."))
        notice.refresh_from_db()
        log_action(notice.campus_id, request.user, "notice.send_now", target=notice, summary=notice.title)
        return Response(self.get_serializer(notice).data)

    # ------------------------------------------------------------------ emergency broadcast
    @action(detail=False, methods=["post"], url_path="emergency")
    def emergency(self, request):
        """Emergency broadcast — admin / principal of the campus only.

        Body: `campus`, `title`, `body`, optional ONE scope (`school_class` or `section`; none = the
        whole school) and optional `audience` (default everyone), `session` (default: the campus's
        current session). Sent immediately and pinned 24 h; the push bypasses quiet hours and the
        user's mute for this type. Audit-logged. A second identical alert from the same person within
        2 minutes is refused (a double tap is not a second emergency).
        """
        campus_id = request.data.get("campus")
        campus = Campus.objects.filter(pk=campus_id).first() if campus_id else None
        if campus is None:
            raise ValidationError({"campus": "Campus is required."})
        if not is_campus_admin_or_principal(request.user, campus.id):
            raise PermissionDenied("Only the campus admin or principal can send an emergency broadcast.")
        try:
            title, body = notice_rules.validate_emergency(request.data.get("title"), request.data.get("body"))
        except notice_rules.NoticeRuleError as exc:
            raise self._rule_error(exc)

        school_class = section = None
        if request.data.get("section"):
            section = Section.objects.filter(
                pk=request.data["section"], school_class__campus_id=campus.id).select_related("school_class").first()
            if section is None:
                raise ValidationError({"section": "Doesn't belong to the given campus."})
        elif request.data.get("school_class"):
            school_class = SchoolClass.objects.filter(pk=request.data["school_class"], campus_id=campus.id).first()
            if school_class is None:
                raise ValidationError({"school_class": "Doesn't belong to the given campus."})
        audience = request.data.get("audience") or Notice.Audience.EVERYONE
        if audience not in notice_rules.AUDIENCES:
            raise ValidationError({"audience": "Unknown audience."})

        session = None
        if request.data.get("session"):
            session = AcademicSession.objects.filter(pk=request.data["session"], campus_id=campus.id).first()
        if session is None:
            session = AcademicSession.objects.filter(campus_id=campus.id, is_current=True).first()
        if session is None:
            raise self._rule_error(notice_rules.NoticeRuleError(
                "no_session", "Set up the current academic session before sending a broadcast."))

        now = timezone.now()
        last = Notice.objects.filter(
            campus_id=campus.id, posted_by=request.user, kind=Notice.Kind.EMERGENCY, title=title
        ).order_by("-created_at").values_list("created_at", flat=True).first()
        if notice_rules.is_duplicate_emergency(last, now):
            raise self._rule_error(notice_rules.NoticeRuleError(
                "duplicate_emergency", "This alert was just sent. Wait a couple of minutes to send it again."))

        notice = Notice.objects.create(
            campus=campus, session=session, school_class=school_class, section=section,
            posted_by=request.user, title=title, body=body, kind=Notice.Kind.EMERGENCY,
            audience=audience, status=Notice.Status.PUBLISHED, published_at=now,
            pin_until=notice_rules.emergency_pin_until(now),
        )
        sent = notices_service.deliver(notice)
        log_action(
            campus.id, request.user, "notice.emergency", target=notice, summary=title,
            audience=audience, recipients=sent,
            scope="section" if section else "class" if school_class else "campus",
        )
        notice.refresh_from_db()
        return Response(self.get_serializer(notice).data, status=status.HTTP_201_CREATED)


# ============================================================
# Phase 4 — tuition classes (coin-free)
# ============================================================
class CampusLiveSessionViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Scheduling auto-fires a `Notice` + `core.Notification`
    (`CAMPUS_SESSION_SCHEDULED`) to the section, and attempts video-room
    provisioning through `bridge.provision_video_room` — both go through
    `campus.bridge`, never a direct `core`/`message`/`tuitionclass` import
    (design doc §4). `status`/`room_id` stay server-controlled; a
    teacher moves the session forward via the `start`/`end` actions
    below rather than PATCHing those fields directly.
    """
    serializer_class = CampusLiveSessionSerializer
    campus_field_path = "section__school_class__campus"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(
            CampusLiveSession.objects.select_related("section__school_class", "subject", "teacher__user"), self.request
        )
        # Optional list filters (additive; the app's "Live now" banner and the
        # timetable's Go live / Join buttons need only TODAY's sessions, not a
        # whole year): ?section=<id> ?status=live,ended ?timetable_entry=<id>
        # ?date=YYYY-MM-DD (the day the class ran / was planned for).
        p = self.request.query_params
        if p.get("section"):
            qs = qs.filter(section_id=p["section"])
        if p.get("timetable_entry"):
            qs = qs.filter(timetable_entry_id=p["timetable_entry"])
        wanted = [s for s in (p.get("status") or "").split(",") if s in CampusLiveSession.Status.values]
        if wanted:
            qs = qs.filter(status__in=wanted)
        day = parse_date(p.get("date") or "")
        if day:
            qs = qs.filter(Q(occurrence_date=day) | Q(occurrence_date__isnull=True, scheduled_at__date=day))
        return qs

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if not section_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.section.school_class.campus_id, obj.section_id, obj.subject_id
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    # TASK 14 — `join` (below) is a POST action a plain enrolled student
    # must be able to call, but the class-level `IsSectionSubjectStaffOrReadOnly`
    # only allows writes from that section/subject's staff (that's correct
    # for `start`/`end`/`cancel`/create, which stay on the default here).
    # `join` does its own, more specific authorization inline instead
    # (enrolled student OR that section/subject's staff OR campus admin —
    # see its own docstring), so it only needs `IsAuthenticated` at the
    # DRF-permission layer.
    def get_permissions(self):
        # `join` (any enrolled student) and the live-class actions below do their
        # own, more specific authorization inline — a substitute teacher is the
        # session's teacher but usually NOT an approved subject teacher, so the
        # class-level IsSectionSubjectStaffOrReadOnly would wrongly refuse them.
        if self.action in ("join", "end", "heartbeat", "leave", "attendance", "start_recording", "stop_recording"):
            return [IsAuthenticated()]
        return super().get_permissions()

    @transaction.atomic
    def perform_create(self, serializer):
        live_session = serializer.save()
        campus = live_session.section.school_class.campus
        # Best-effort: video-room provisioning (core/message/LiveKit) being
        # down must not block scheduling — `join` already returns a clean
        # 503 for a session with no room_id.
        is_offline = live_session.mode == ClassMode.OFFLINE
        room_id = None
        if not is_offline:  # T4 §G — an offline class has no video room
            try:
                with transaction.atomic():
                    room_id = bridge.provision_video_room(live_session, actor=self.request.user)
            except Exception:
                logger.exception("Video room provisioning failed for live session %s.", live_session.pk)
                room_id = None
        if room_id:
            live_session.room_id = room_id
            live_session.save(update_fields=["room_id"])
        recipients = StudentEnrollment.objects.filter(
            section=live_session.section, status=StudentEnrollment.Status.ACTIVE
        ).values_list("student", flat=True)
        Notice.objects.create(
            campus=campus,
            section=live_session.section,
            session=getattr(live_session.section.school_class, "session", None),
            posted_by=self.request.user,
            title=f"Class scheduled: {live_session.subject.name}",
            # T4 §G — offline classes never leak their time to students.
            body=(
                f"An offline class for {live_session.subject.name} has been scheduled."
                if is_offline
                else f"A live session for {live_session.subject.name} is scheduled at {live_session.scheduled_at}."
            ),
        )
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_SESSION_SCHEDULED,
            title="Class scheduled",
            body=(
                f"{live_session.subject.name}: offline class scheduled."
                if is_offline
                else f"{live_session.subject.name} scheduled at {live_session.scheduled_at}."
            ),
        )

    def perform_update(self, serializer):
        old = serializer.instance
        changed = (
            ("scheduled_at" in serializer.validated_data and serializer.validated_data["scheduled_at"] != old.scheduled_at)
            or ("mode" in serializer.validated_data and serializer.validated_data["mode"] != old.mode)
        )
        instance = serializer.save()
        if changed and instance.reminder_sent_at:
            # rescheduled / mode changed -> the 5-minute reminder is due again
            CampusLiveSession.objects.filter(pk=instance.pk).update(reminder_sent_at=None)

    # B-4 fix — see CampusLiveSessionJoinThrottle's docstring in
    # throttles.py for why `start` (not a separate join endpoint, which
    # this app doesn't have) is the one scoped here: it's what fires
    # the CAMPUS_SESSION_LIVE notification fan-out below.
    @action(detail=True, methods=["post"], throttle_classes=[CampusLiveSessionJoinThrottle])
    def start(self, request, pk=None):
        live_session = self.get_object()
        if live_session.status != CampusLiveSession.Status.SCHEDULED:
            return Response({"detail": "Only a scheduled session can be started."}, status=status.HTTP_400_BAD_REQUEST)
        # Live class: status -> LIVE (+ real `started_at`), room provisioning retry
        # (scheduling-time provisioning is best-effort) and the time-critical
        # push=True "class is live" notification all live in campus/live_class.py
        # so this action and the one-tap Go live behave identically.
        live_class.start_session(live_session, request.user)
        return Response(self.get_serializer(live_session).data)

    # TASK 14 — closes the gap `core.classroom_chat_bridge.provision_video_room()`'s
    # own docstring flags: that function only names the room at scheduling
    # time (`perform_create` above), it deliberately never mints a token.
    # This is the actual "join this live session" endpoint — mints a
    # fresh, per-participant LiveKit token via
    # `core.classroom_chat_bridge.generate_campus_session_token()`
    # (added this same pass, see that function's own docstring).
    #
    # Called directly, NOT through `campus.bridge` — unlike every other
    # cross-app call in this file (`bridge.provision_video_room`,
    # `bridge.create_section_group`, ...). `core.classroom_chat_bridge`'s
    # own module docstring flags why: `campus/bridge.py`'s current
    # contents weren't available in the TASK 14 pass that added
    # `generate_campus_session_token()`, so no wrapper for it could be
    # verified there either. If `campus/bridge.py` later grows a thin
    # `generate_session_token()` wrapper for consistency with the other
    # bridge calls in this file, swap this one call over to it — nothing
    # else here would need to change.
    #
    # Reuses `CampusLiveSessionJoinThrottle` (already imported above) —
    # its own docstring in throttles.py explains it was scoped to `start`
    # only because this app had no separate join endpoint yet; now that
    # one exists, it belongs here too, in addition to `start` (kept there
    # for the notification-fan-out reason that throttle's docstring
    # separately documents).
    @action(detail=True, methods=["post"], throttle_classes=[CampusLiveSessionJoinThrottle])
    def join(self, request, pk=None):
        """
        `POST /campus-live-sessions/{id}/join/` -> `{"room_name": ..., "token": ...}`.

        Only once the session is actually `LIVE` (a student hitting this
        before the teacher's `start` call has nothing to join — LiveKit
        would accept the token but the room itself may not exist yet on
        the media server, since `provision_video_room()` only ever names
        it, never creates it server-side).

        Authorization (checked here, not by a permission class — see
        `get_permissions()` above): the requesting user must be either
        an ACTIVE `StudentEnrollment` in this session's section, or able
        to manage that section/subject (`can_manage_section_subject` —
        assigned subject-teacher, class-teacher, or campus admin/
        principal, the same staff check `IsSectionSubjectStaffOrReadOnly`
        itself is built on, per this file's other usages of that
        helper). Minting a token is not itself that check —
        `generate_campus_session_token()`'s own docstring is explicit
        that the caller owns this, so it happens here.
        """
        live_session = self.get_object()

        if live_session.mode == ClassMode.OFFLINE:
            return Response({"detail": "This is an offline class — there is no video room."}, status=status.HTTP_400_BAD_REQUEST)
        if live_session.status != CampusLiveSession.Status.LIVE:
            return Response(
                {"detail": "This session isn't live yet."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not live_session.room_id:
            # Shouldn't normally happen (perform_create provisions it),
            # but provisioning is itself best-effort (see
            # provision_video_room()/bridge's own handling) — surface a
            # clean 503 rather than minting a token for a room that was
            # never named.
            return Response(
                {"detail": "This session has no video room provisioned."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        section = live_session.section
        campus_id = section.school_class.campus_id
        is_enrolled_student = StudentEnrollment.objects.filter(
            section=section,
            student=request.user,
            status=StudentEnrollment.Status.ACTIVE,
        ).exists()
        if not is_enrolled_student and not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("You aren't enrolled in this section or assigned to teach it.")

        # Local import — campus never imports `core`/`message` directly
        # except through a single door; see this action's own docstring
        # above for why that door is `core.classroom_chat_bridge`
        # directly here rather than `campus.bridge`.
        from core.classroom_chat_bridge import generate_campus_session_token

        try:
            token = generate_campus_session_token(live_session.room_id, request.user)
        except RuntimeError:
            logger.exception(
                "LiveKit not configured — cannot mint join token for session %s", live_session.pk,
            )
            return Response(
                {"detail": "Live video isn't configured on this deployment."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Live class: this is the moment the student is "in the room" — the raw
        # data the automatic attendance is computed from.
        live_class.record_join(live_session, request.user)
        return Response(
            {"room_name": live_session.room_id, "token": token, "livekit_url": live_class.livekit_url()}
        )

    @action(detail=True, methods=["post"])
    def end(self, request, pk=None):
        live_session = self.get_object()
        if not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can end it.")
        # -> ENDED, open stays closed, subject-wise attendance built (source=auto).
        live_session, _changed = live_class.end_session(live_session)
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"])
    def heartbeat(self, request, pk=None):
        """`POST /live-sessions/{id}/heartbeat/` — the app pings this every ~20-30 s
        while it is inside the room (students AND the teacher). Keeps the stay's
        `last_seen_at` fresh; the teacher's ping also keeps the session alive (a
        silent teacher app gets the class auto-ended by the sweeper). Always 200:
        `{"live": false, "status": "ended"}` tells a student's app to leave."""
        live_session = self.get_object()
        if not self._may_be_in_room(request.user, live_session):
            raise PermissionDenied("You aren't enrolled in this section or assigned to teach it.")
        return Response(live_class.heartbeat(live_session, request.user))

    @action(detail=True, methods=["post"])
    def leave(self, request, pk=None):
        """`POST /live-sessions/{id}/leave/` — the user left the room (idempotent)."""
        live_session = self.get_object()
        live_class.record_leave(live_session, request.user)
        return Response({"left": True})

    @action(detail=True, methods=["get"])
    def attendance(self, request, pk=None):
        """`GET /live-sessions/{id}/attendance/` — who came: every active student of
        the section with status / minutes / source (`auto` or `manual`) / join times,
        plus counts. Teacher / manager of the class only."""
        live_session = self.get_object()
        if not live_class.can_run_session(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can see this.")
        return Response(live_class.attendance_report(live_session))

    # ---- recorded classes (rules: campus/recordings.py) -----------------------------
    @action(detail=True, methods=["post"], url_path="start-recording", throttle_classes=[CampusRecordingControlThrottle])
    def start_recording(self, request, pk=None):
        """`POST /live-sessions/{id}/start-recording/` — the teacher's manual Record button
        (the automatic start happens on the teacher's first heartbeat when the campus
        turned `class_recording_auto` on). Refusals come back as
        `{"detail": ..., "code": ...}` with the code of `recordings.RecordingError`
        (`recording_off`, `not_live`, `already_recording`, `already_recorded`, ...)."""
        live_session = self.get_object()
        if not recordings.can_manage_recording(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can record it.")
        try:
            live_session = recordings.start_recording(live_session, request.user)
        except recordings.RecordingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        return Response(self.get_serializer(live_session).data)

    @action(detail=True, methods=["post"], url_path="stop-recording", throttle_classes=[CampusRecordingControlThrottle])
    def stop_recording(self, request, pk=None):
        """`POST /live-sessions/{id}/stop-recording/` — stop the film but keep the class
        running (ending the class stops it too). 409 `not_recording` when none is on."""
        live_session = self.get_object()
        if not recordings.can_manage_recording(request.user, live_session):
            raise PermissionDenied("Only this class's teacher (or a manager of the section) can stop the recording.")
        try:
            live_session, _changed = recordings.stop_recording(live_session, request.user, strict=True)
        except recordings.RecordingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        return Response(self.get_serializer(live_session).data)

    @staticmethod
    def _may_be_in_room(user, live_session):
        return StudentEnrollment.objects.filter(
            section=live_session.section, student=user, status=StudentEnrollment.Status.ACTIVE
        ).exists() or live_class.can_run_session(user, live_session)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        live_session = self.get_object()
        live_session.status = CampusLiveSession.Status.CANCELLED
        live_session.save(update_fields=["status"])
        return Response(self.get_serializer(live_session).data)


# ============================================================
# Phase 5 — timetable & attendance
# ============================================================
class TimeSlotViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = TimeSlotSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        # students only ever learn the slots of ONLINE periods (§G)
        return Q(pk__in=TimetableEntry.objects.filter(
            section_id__in=vis.visible_section_ids, mode=ClassMode.ONLINE
        ).values("time_slot_id"))

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(TimeSlot.objects.all(), self.request)

    def perform_update(self, serializer):
        # Changing a slot's day/time moves every entry using it: refuse if that would
        # double-book a teacher / room / section against another slot's entries.
        from . import timetable_conflicts as tc

        d = serializer.validated_data
        slot = serializer.instance
        day = d.get("day_of_week", slot.day_of_week)
        start = d.get("start_time", slot.start_time)
        end = d.get("end_time", slot.end_time)
        if (day, start, end) != (slot.day_of_week, slot.start_time, slot.end_time):
            found = []
            for e in TimetableEntry.objects.filter(time_slot=slot):
                found += tc.find_conflicts(
                    session_id=e.session_id, day_of_week=day, start_time=start, end_time=end,
                    section_id=e.section_id, staff_id=e.staff_id, room_id=e.room_id,
                    exclude_pk=e.pk, exclude_slot_id=slot.pk,
                )
            if found:
                raise tc.conflict_validation_error(found)
        super().perform_update(serializer)


class TimetableEntryViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Clash-detection is enforced in `TimetableEntry.clean()`/`save()` at
    the model layer (design doc §5) — the serializer's
    `DjangoCleanValidationMixin` turns that into a normal 400 here.
    """
    serializer_class = TimetableEntrySerializer
    campus_field_path = "section__school_class__campus"
    member_scope = "section"
    member_section_path = "section"
    # [T4 §C] admin/principal anywhere; class-teacher / approved subject-teacher
    # (for that subject) / moderator ONLY for their own section.
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(
            TimetableEntry.objects.select_related("section__school_class", "time_slot"), self.request
        )

    def get_section_subject_for_permission_check(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        if not section_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.section.school_class.campus_id, obj.section_id, obj.subject_id
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    def get_campus_id_for_permission_check(self, request):
        section_id = request.data.get("section")
        if section_id:
            section = Section.objects.filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def get_permissions(self):
        # `go-live` authorizes inline (the period's teacher, an assigned teacher,
        # a manager AND today's substitute teacher) — see live_class.can_go_live_for_entry.
        if self.action == "go_live":
            return [IsAuthenticated()]
        return super().get_permissions()

    # ---- conflict detection (see campus/timetable_conflicts.py) ----------------
    def _raise_if_conflicting(self, serializer):
        from . import timetable_conflicts as tc

        d, inst = serializer.validated_data, serializer.instance
        pick = lambda n: d[n] if n in d else getattr(inst, n, None)
        section, staff, slot, session, room = (pick(n) for n in ("section", "staff", "time_slot", "session", "room"))
        if not (section and staff and slot and session):
            return
        campus_id = section.school_class.campus_id
        parts = [staff.campus_id, slot.campus_id, session.campus_id] + ([room.campus_id] if room else [])
        if any(c != campus_id for c in parts):
            # never describe another campus's timetable in an error message
            raise ValidationError({"detail": "Section, staff, time slot, room and session must belong to the same campus.",
                                   "code": "campus_mismatch"})
        conflicts = tc.find_conflicts_for_values(
            session=session, time_slot=slot, section=section, staff=staff, room=room,
            exclude_pk=inst.pk if inst else None,
        )
        if conflicts:
            raise tc.conflict_validation_error(conflicts)

    def perform_create(self, serializer):
        self._raise_if_conflicting(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._raise_if_conflicting(serializer)
        super().perform_update(serializer)

    @action(detail=False, methods=["post"], url_path="check-conflicts")
    def check_conflicts(self, request):
        """Pre-check without saving. Body: section, staff, time_slot, session, [room],
        [entry] (id being edited), [subject] (lets an approved subject teacher use it).
        200 {"has_conflicts": bool, "conflicts": [{type, message, entry}]}"""
        from . import timetable_conflicts as tc

        ser = TimetableConflictCheckSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        conflicts = tc.find_conflicts_for_values(
            session=v["session"], time_slot=v["time_slot"], section=v["section"], staff=v["staff"],
            room=v.get("room"), exclude_pk=v["entry"].pk if v.get("entry") else None,
        )
        return Response({"has_conflicts": bool(conflicts), "conflicts": conflicts})

    @action(detail=False, methods=["get"], url_path="conflicts")
    def conflicts(self, request):
        """Admin/principal audit of clashes already saved. ?campus=<id> [&session=<id>]
        (default: the campus's current session)."""
        from . import timetable_conflicts as tc

        campus_id = request.query_params.get("campus")
        if not campus_id:
            raise ValidationError({"campus": "This query parameter is required."})
        if not is_campus_admin_or_principal(request.user, campus_id):
            raise PermissionDenied("Only an admin or principal can audit the timetable.")
        sessions = AcademicSession.objects.filter(campus_id=campus_id)
        sid = request.query_params.get("session")
        session = sessions.filter(pk=sid).first() if sid else sessions.filter(is_current=True).first()
        if session is None:
            raise ValidationError({"session": "No such session (and the campus has no current one)."})
        entries = list(TimetableEntry.objects.filter(session=session, section__school_class__campus_id=campus_id)
                       .select_related("section__school_class", "subject", "staff__user", "room", "time_slot"))
        out = []
        for kind, a, b in tc.scan_entries(entries):
            da, db = tc.describe_entry(a), tc.describe_entry(b)
            out.append({"type": kind, "message": tc._message(kind, db) , "entries": [da, db]})
        return Response({"campus": str(campus_id), "session": str(session.pk), "count": len(out), "conflicts": out})

    @action(detail=True, methods=["post"], url_path="go-live", throttle_classes=[CampusLiveSessionJoinThrottle])
    def go_live(self, request, pk=None):
        """
        ONE TAP: `POST /timetable/{id}/go-live/` takes today's occurrence of this
        online period live — creates (or resumes / adopts) the live session, makes
        sure the video room exists, tells the section's students "class is live"
        and returns the teacher's own join token, so the app can open the room
        straight away. Idempotent: tapping again just resumes the same room.

        201 when a new session was created, else 200:
            {"session": {...}, "room_name": "...", "token": "...", "livekit_url": "wss://...",
             "created": bool, "resumed": bool, "reopened": bool}
        Errors are `{"detail", "code"}`: not_allowed 403 · offline_class / not_today /
        outside_session / too_early / slot_over 400 · already_ended 409 ·
        video_unavailable / no_room 503 (the start is rolled back — nobody is notified).
        Students' attendance is then built automatically when the class ends.
        """
        entry = self.get_object()
        try:
            with transaction.atomic():
                result = live_class.go_live_from_entry(entry=entry, user=request.user)
                token = live_class.mint_join_token(result.session, request.user)
                live_class.record_join(result.session, request.user)
        except live_class.LiveClassError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=exc.status_code)
        if result.announce:
            # Best-effort and AFTER commit: a push problem must not undo a class that did start.
            try:
                live_class.announce_live(result.session)
            except Exception:
                logger.exception("Class-is-live push failed for live session %s.", result.session.pk)
        serializer = CampusLiveSessionSerializer(result.session, context=self.get_serializer_context())
        return Response(
            {
                "session": serializer.data,
                "room_name": result.session.room_id,
                "token": token,
                "livekit_url": live_class.livekit_url(),
                "created": result.created,
                "resumed": result.resumed,
                "reopened": result.reopened,
            },
            status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK,
        )


class AttendanceViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    `subject=None` means a daily (not period-wise) mark, which only a
    class-teacher/admin can take; a subject-specific mark additionally
    allows that section+subject's approved subject-teacher (design doc
    §5/§11). `marked_by` is always the requesting user, never
    client-supplied.

    Galti-sudhaar (campus/attendance_edit.py): CHANGING or DELETING an
    already-marked row needs a `reason` and must fall inside the campus edit
    window (`Campus.attendance_edit_window_hours`, default 24h from first
    marking) unless the user is a campus admin/principal; every such edit is
    written to `AttendanceEditLog` (+ the campus audit log). `enrollment`,
    `date` and `subject` of an existing row can't be changed — only `status`.
    """
    serializer_class = AttendanceSerializer
    campus_field_path = "enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "enrollment__student"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(Attendance.objects.all(), self.request)

    def get_section_subject_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        subject_id = request.data.get("subject")
        # Teacher 2-tap flow: `POST /attendance/bulk/` section-level hai,
        # enrollment-level nahi.
        if not enrollment_id and request.data.get("section"):
            section = Section.objects.select_related("school_class").filter(pk=request.data["section"]).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id or None
            return None, None, None
        if not enrollment_id and self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.enrollment.section.school_class.campus_id, obj.enrollment.section_id, obj.subject_id
        if enrollment_id:
            enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if enrollment:
                return enrollment.section.school_class.campus_id, enrollment.section_id, subject_id
        return None, None, None

    def _push_parents_if_newly_absent(self, instance, previous_status):
        """Parents get a push only when this row is NEW or CHANGED to absent
        (see attendance_push.py for the full rules). Runs after commit."""
        if instance.status != Attendance.Status.ABSENT or previous_status == Attendance.Status.ABSENT:
            return

        def _push(enrollment_id=instance.enrollment_id, day=instance.date, subject=instance.subject):
            from .attendance_push import notify_parents_of_absences

            notify_parents_of_absences(enrollment_ids=[enrollment_id], date=day, subject=subject)

        transaction.on_commit(_push)

    def _push_correction_if_no_longer_absent(self, instance, previous_status):
        """Absent -> anything else (or the row removed): parents who already got
        today's absent push are told it was corrected. Runs after commit."""
        if previous_status != Attendance.Status.ABSENT or instance.status == Attendance.Status.ABSENT:
            return

        def _push(enrollment_id=instance.enrollment_id, day=instance.date, subject=instance.subject):
            from .attendance_push import notify_parents_of_corrections

            notify_parents_of_corrections(enrollment_ids=[enrollment_id], date=day, subject=subject)

        transaction.on_commit(_push)

    def perform_create(self, serializer):
        serializer.validated_data.pop("reason", None)  # first marking: nothing to correct
        instance = serializer.save(marked_by=self.request.user)
        self._push_parents_if_newly_absent(instance, previous_status=None)

    def perform_update(self, serializer):
        reason = serializer.validated_data.pop("reason", "")
        row = serializer.instance
        previous_status = row.status
        new_status = serializer.validated_data.get("status", previous_status)
        if new_status == previous_status:
            serializer.save()  # no real change: no window / reason / log
            return
        enrollment = row.enrollment
        campus = enrollment.section.school_class.campus
        clean_reason, override = attendance_edit.check_edit(
            user=self.request.user, campus=campus, rows=[row], reason=reason
        )
        with transaction.atomic():
            now = timezone.now()
            # A teacher's correction of an automatic (live class) row makes it manual,
            # so a later rebuild of the live session never overwrites it.
            instance = serializer.save(
                marked_by=self.request.user, last_edited_at=now, edit_count=(row.edit_count or 0) + 1,
                source=Attendance.Source.MANUAL,
            )
            attendance_edit.record_edits(
                user=self.request.user, campus=campus, section=enrollment.section,
                date=instance.date, subject=instance.subject,
                changes=[(instance, previous_status, new_status)],
                reason=clean_reason, override=override, now=now,
            )
        self._push_parents_if_newly_absent(instance, previous_status=previous_status)
        self._push_correction_if_no_longer_absent(instance, previous_status=previous_status)

    def perform_destroy(self, instance):
        reason = self.request.data.get("reason") or self.request.query_params.get("reason") or ""
        enrollment = instance.enrollment
        campus = enrollment.section.school_class.campus
        clean_reason, override = attendance_edit.check_edit(
            user=self.request.user, campus=campus, rows=[instance], reason=reason
        )
        with transaction.atomic():
            attendance_edit.record_edits(
                user=self.request.user, campus=campus, section=enrollment.section,
                date=instance.date, subject=instance.subject,
                changes=[(instance, instance.status, None)],
                reason=clean_reason, override=override,
            )
            was_absent = instance.status == Attendance.Status.ABSENT
            snapshot = (instance.enrollment_id, instance.date, instance.subject)
            instance.delete()
        if was_absent:
            def _push(enrollment_id=snapshot[0], day=snapshot[1], subject=snapshot[2]):
                from .attendance_push import notify_parents_of_corrections

                notify_parents_of_corrections(enrollment_ids=[enrollment_id], date=day, subject=subject)

            transaction.on_commit(_push)

    @action(detail=False, methods=["get"], url_path="edit-log")
    def edit_log(self, request):
        """Galti-sudhaar history: `?section=<id>` (required) `&date=YYYY-MM-DD`
        `&subject=<id>` | `&whole_day=1` `&enrollment=<id>` `&limit=100(max 200)`.
        Only staff who can manage that section (+subject) may read it — never
        students / parents. Newest first: who changed what, when and why."""
        p = request.query_params
        section = bulk_ops.resolve_section(p.get("section"))
        subject_id = p.get("subject") or None
        if not can_manage_section_subject(request.user, section.school_class.campus_id, section.id, subject_id):
            raise PermissionDenied("Only staff who manage this section can see its attendance edit history.")
        qs = AttendanceEditLog.objects.filter(section=section).select_related("edited_by", "subject")
        if subject_id:
            qs = qs.filter(subject_id=subject_id)
        elif _truthy(p.get("whole_day")):
            qs = qs.filter(subject__isnull=True)
        day = parse_date(p.get("date") or "")
        if day:
            qs = qs.filter(date=day)
        if p.get("enrollment"):
            qs = qs.filter(enrollment_id=p["enrollment"])
        try:
            limit = max(1, min(int(p.get("limit") or 100), 200))
        except (TypeError, ValueError):
            limit = 100
        rows = []
        for r in qs[:limit]:
            editor = r.edited_by
            rows.append({
                "id": str(r.id),
                "action": r.action,
                "date": r.date.isoformat(),
                "subject": str(r.subject_id) if r.subject_id else None,
                "subject_name": r.subject.name if r.subject_id else None,
                "enrollment": str(r.enrollment_id) if r.enrollment_id else None,
                "student_label": r.student_label,
                "old_status": r.old_status,
                "new_status": r.new_status or None,
                "reason": r.reason,
                "edited_by": (
                    {"id": editor.id, "username": editor.username,
                     "name": f"{editor.first_name} {editor.last_name}".strip() or editor.username}
                    if editor else None
                ),
                "edited_at": r.edited_at,
                "original_marked_at": r.original_marked_at,
                "was_override": r.was_override,
            })
        return Response({"results": rows})

    @action(detail=False, methods=["get"], url_path="day")
    def day(self, request):
        """`?section=<id>&date=YYYY-MM-DD&subject=<id optional>` -> us din ka
        poora section attendance ek hi query me:
        `{"marked": bool, "records": {enrollment_id: status}}`.
        (Pehle app har student ke liye alag GET karta tha.)"""
        section_id = request.query_params.get("section")
        date = parse_date(request.query_params.get("date") or "")
        if not section_id or not date:
            return Response(
                {"detail": "section and date (YYYY-MM-DD) query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        section = (
            Section.objects.select_related("school_class__campus").filter(pk=section_id).first()
            if bulk_ops._is_uuid(section_id)
            else None
        )
        return Response(
            bulk_ops.attendance_day_snapshot(
                self.get_queryset(),
                section_id=section_id,
                date=date,
                subject_id=request.query_params.get("subject") or None,
                user=request.user,
                campus=section.school_class.campus if section else None,
            )
        )

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """
        2-tap attendance: `{section, date, subject?, default_status?="present",
        overrides: {enrollment_id: status}, client_request_id?, clear?}`.

        Poora active roster `default_status` pe jaata hai, `overrides` sirf
        exceptions (absent/late/leave) hain. Ek transaction, idempotent —
        usi din dobara bhejo to rows update hoti hain.

        * `client_request_id` — double-tap / offline-sync retry se bachav.
          Wahi id (same user) dobara aaye to pichla result `replayed: true`
          ke saath wapas, kuch dobara write nahi hota. Request abhi chal
          rahi ho (parallel duplicate) to 409 `in_progress`.
        * `clear: true` — "Undo" jab din pehle mark hi nahi tha: us din ki
          rows hata deta hai.
        * `reason` — galti-sudhaar: mark ho chuki attendance ka status badalne
          ya clear karne par zaroori (400 `reason_required`); edit window
          (default 24h) baad sirf admin/principal (403 `edit_window_closed`).
          Har badlaav `AttendanceEditLog` me likha jaata hai.
        Permission: `IsSectionSubjectStaffOrReadOnly` (section-level).
        """
        data = request.data
        rid = str(data.get("client_request_id") or "").strip()[:64]
        key = f"campus:att-bulk:{request.user.pk}:{rid}" if rid else None
        if key:
            done = cache.get(key)
            if done is not None:
                return Response({**done, "replayed": True}, status=status.HTTP_200_OK)
            if not cache.add(key + ":lock", 1, 30):
                return Response(
                    {"detail": "This save is already in progress.", "code": "in_progress"},
                    status=status.HTTP_409_CONFLICT,
                )
        try:
            section = bulk_ops.resolve_section(data.get("section"))
            date = parse_date(str(data.get("date") or ""))
            if not date:
                raise bulk_ops.ValidationError({"date": "Valid date (YYYY-MM-DD) is required."})
            subject = bulk_ops.resolve_subject(data.get("subject"), section.school_class.campus_id)
            if _truthy(data.get("clear")):
                result = bulk_ops.clear_attendance_day(
                    section=section, date=date, subject=subject,
                    user=request.user, reason=data.get("reason"),
                )
            else:
                overrides = data.get("overrides") or {}
                if not isinstance(overrides, dict):
                    raise bulk_ops.ValidationError({"overrides": "Must be an object of enrollment_id -> status."})
                result = bulk_ops.bulk_mark_attendance(
                    user=request.user,
                    section=section,
                    date=date,
                    subject=subject,
                    default_status=data.get("default_status") or Attendance.Status.PRESENT,
                    overrides=overrides,
                    reason=data.get("reason"),
                )
            if key:
                cache.set(key, result, 60 * 60 * 24)
            return Response(result, status=status.HTTP_200_OK)
        finally:
            if key:
                cache.delete(key + ":lock")

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """`?enrollment=<id>&subject=<id optional>` -> computed %-age,
        the `compute_attendance_summary` service function (§5) —
        recomputed on demand, never stored."""
        enrollment_id = request.query_params.get("enrollment")
        if not enrollment_id:
            return Response({"detail": "enrollment query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        if not enrollment or not is_any_active_staff(
            request.user, enrollment.section.school_class.campus_id
        ) and enrollment.student_id != request.user.id and not is_linked_parent_of_student(
            request.user, enrollment.student_id
        ):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        subject_id = request.query_params.get("subject")
        subject = Subject.objects.filter(pk=subject_id).first() if subject_id else None
        return Response(compute_attendance_summary(enrollment, subject=subject))

    @action(detail=False, methods=["get"], url_path="breakdown")
    def breakdown(self, request):
        """`?enrollment=<id>` -> the whole attendance picture for the "My progress" screen:
        overall %, subject-wise %, last 6 months %, and the campus alert threshold."""
        enrollment_id = request.query_params.get("enrollment")
        if not enrollment_id or not bulk_ops._is_uuid(enrollment_id):
            return Response({"detail": "enrollment query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        if not enrollment or not is_any_active_staff(
            request.user, enrollment.section.school_class.campus_id
        ) and enrollment.student_id != request.user.id and not is_linked_parent_of_student(
            request.user, enrollment.student_id
        ):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(progress_data.attendance_breakdown(enrollment))


# ============================================================
# Phase 6 — assigmentss & syllabus
# ============================================================
def _my_section_ids_and_campus_map(user):
    """[Task 11 / T4 §E] Sections the user may read section-scoped content
    (assignments, test series, submissions) for: every section of a campus
    they STAFF, but for a student/parent only their own / their child's
    current section(s) — no longer campus-wide."""
    return accessible_section_ids_and_campus_map(user)


def _section_for_assigments(assigments):
    """[Task 11] `assigments.context_id` IS a `Section.id` for every
    `source="campus"` unified assigments (see `campus.bridge.
    create_assigments()`) — this app never stores a real FK back to
    `Section` (golden rule, §1), so this is the one place that opaque id
    gets turned back into a real row, same posture `assigments.bridge`
    itself takes toward never doing this resolution on its own side.
    """
    return Section.objects.filter(pk=assigments.context_id).select_related("school_class").first()


_UNSET = object()


def _batch_assigment_context(rows):
    """[PERF] ONE Section query + ONE StaffProfile query for a whole list of assignments
    (was 2 queries per assignment inside `_serialize_campus_assigments`).
    Returns (sections_by_context_id, staff_id_by_(user_id, campus_id))."""
    context_ids = {a.context_id for a in rows if a.context_id}
    sections = {
        str(s.id): s for s in Section.objects.filter(pk__in=context_ids).select_related("school_class")
    } if context_ids else {}
    posters = {a.posted_by_id for a in rows if a.posted_by_id}
    campus_ids = {s.school_class.campus_id for s in sections.values()}
    staff = {}
    if posters and campus_ids:
        for sid, uid, cid in (
            StaffProfile.objects.filter(user_id__in=posters, campus_id__in=campus_ids, is_active=True)
            .order_by("id").values_list("id", "user_id", "campus_id")
        ):
            staff.setdefault((uid, cid), sid)
    return sections, staff


def _paginate_if_requested(view, request, rows_qs):
    """[PERF] Opt-in pagination for the legacy bare-list endpoints: only when the client sends
    `?page=` or `?page_size=` do they get the standard {count,next,previous,results} envelope
    (capped by MAX_PAGE_SIZE). Without either param the response is the same bare list as before,
    so the shipped Flutter app is not broken. Returns (page_rows_or_None, paginator_or_None)."""
    qp = request.query_params
    if "page" not in qp and "page_size" not in qp:
        return None, None
    from common.pagination import StandardPagination

    paginator = StandardPagination()
    return paginator.paginate_queryset(rows_qs, request, view=view), paginator


def _serialize_campus_assigments(assigments, my_submission=None, *, section=_UNSET, staff_lookup=None):
    """[Task 11] Reconstructs the OLD `campus.assigments` API shape
    (`id, section, subject, posted_by, title, description, attachment,
    due_date, session`) from a NEW `assigments.models.assigments`
    instance, so `assigmentsViewSet`'s response keys stay
    frontend-compatible even though the backing model changed entirely
    (Task 11 acceptance: same JSON keys).

    - `section` = `assigments.context_id` directly.
    - `subject` = `assigments.data.get("subject_id")` — see
      `create_context_assigments()`'s `extra_data` parameter docstring
      (assignments/bridge.py) for why this couldn't be a real FK on the
      unified model.
    - `session` = derived fresh from the section's own
      `school_class.session_id` rather than stored anywhere on the
      unified model — a Section's session doesn't change after the
      fact, so this is always correct and avoids keeping a second,
      potentially-stale copy of a value `Section` already has.
    - `posted_by` = the `StaffProfile.id` for `assigments.posted_by` at
      this campus, looked up fresh — the unified model only stores the
      underlying `login.User`, not the campus-scoped `StaffProfile` row
      the old API exposed. [FLAGGED, not a bug]: if that staff member's
      `StaffProfile` for this campus is later deactivated, this now
      returns `None` where the old, permanently-stored FK would have
      kept returning the same id — a real, deliberate behavior
      difference from before.
    """
    if section is _UNSET:
        section = _section_for_assigments(assigments)
    campus_id = section.school_class.campus_id if section else None
    posted_by_staff_id = None
    if assigments.posted_by_id and campus_id:
        if staff_lookup is not None:
            posted_by_staff_id = staff_lookup.get((assigments.posted_by_id, campus_id))
        else:
            posted_by_staff_id = (
                StaffProfile.objects.filter(user_id=assigments.posted_by_id, campus_id=campus_id, is_active=True)
                .values_list("id", flat=True)
                .first()
            )
    return {
        "id": assigments.id,
        "section": assigments.context_id,
        "subject": assigments.data.get("subject_id"),
        "posted_by": posted_by_staff_id,
        "title": assigments.title,
        "description": assigments.description,
        "attachment": assigments.attachment.url if assigments.attachment else None,
        "due_date": assigments.due_date,
        "session": section.school_class.session_id if section else None,
        # Caller's own submission (student view): lets the list show pending / submitted /
        # checked without opening every assignment. None for staff who hold no row.
        "my_status": my_submission.status if my_submission else None,
        "my_grade": (my_submission.grade or None) if my_submission else None,
    }


def _serialize_campus_submission(submission):
    """[Task 11] Reconstructs the OLD `campus.assigmentsSubmission` API
    shape (`id, assigments, student, submitted_at, file, status, grade,
    feedback`) from a NEW `assigments.models.assigmentsSubmission`
    instance.

    `status` string values are used as-is — "submitted"/"late"/"missing"
    match the old 3-state model's spellings exactly. The unified model
    adds `"checked"`/`"partially_checked"`, states the old model never
    produced (grading there only ever set `grade`/`feedback` directly,
    never moved `status` again after submit). Keys are unchanged
    (satisfies the "same JSON keys" acceptance criterion); the *value*
    can now include those two new strings — a genuine, flagged behavior
    difference, not silently collapsed back into the old lossy 3-state
    enum, since that would throw away real grading-status information
    the new `grade_freeform()` flow now tracks.
    """
    return {
        "id": submission.id,
        "assigments": submission.assigments_id,
        "student": submission.student_id,
        "submitted_at": submission.submitted_at,
        "file": submission.file.url if submission.file else None,
        "written_content": submission.written_content,
        "status": submission.status,
        "grade": submission.grade,
        "feedback": submission.feedback,
        "checked_at": submission.checked_at,
        "roll_number": submission.roll_number,
        # Teacher roster needs names (same shape as MinimalUserSerializer).
        "student_detail": {
            "id": submission.student_id,
            "username": submission.student.username,
            "first_name": submission.student.first_name,
            "last_name": submission.student.last_name,
        },
    }


class assigmentsViewSet(viewsets.ViewSet):
    """[Task 11] Thin proxy over the unified `assigments` app —
    `campus.assigments` (this app's own model) is deprecated (see its
    docstring in models.py); every assigments now actually lives on
    `assigments.models.assigments` with `source="campus"`,
    `context_type="section"`, `context_id=<Section.id>`.

    Deliberately NOT a `ModelViewSet` (nor built on
    `CampusMemberScopedMixin`, whose `filter_queryset_to_my_campuses`/
    `_campus_id_from_instance` both assume a real Django FK chain from
    the model to `Campus` — `context_id` is an opaque `UUIDField`, not
    an FK, so that traversal can't work here) — list/retrieve/create are
    implemented directly against the unified model instead, with the
    OLD response shape (`id, section, subject, posted_by, title,
    description, attachment, due_date, session`) reconstructed by
    `_serialize_campus_assigments()` so existing frontend code keeps
    working unchanged (Task 11 acceptance: same JSON keys).

    update/partial_update/destroy are NOT implemented in this pass —
    the old `ModelViewSet` allowed arbitrary field PATCHes on a posted
    assigments, but the unified model's mutation surface is
    explicit-method-based (e.g. `has_structured_questions` immutability
    once a submission exists) and no design doc input covered what
    "edit a posted campus assigments" should mean against that surface.
    Flagged as an open item rather than guessed at, same as this
    codebase's established convention for genuine gaps (see e.g.
    `campus/bridge.py`'s own STATUS section).
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def list(self, request):
        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        qs = Unifiedassigments.objects.filter(
            source=UnifiedassigmentsSource.CAMPUS, context_type="section", context_id__in=section_ids
        ).order_by("-due_date")
        section_filter = request.query_params.get("section")
        if section_filter:
            qs = qs.filter(context_id=section_filter)
        qs = qs.order_by("-due_date", "id")
        page, paginator = _paginate_if_requested(self, request, qs)
        rows = list(page) if page is not None else list(qs)
        mine = {
            s.assigments_id: s
            for s in UnifiedassigmentsSubmission.objects.filter(
                student=request.user, assigments_id__in=[a.id for a in rows]
            )
        }
        sections, staff = _batch_assigment_context(rows)  # [PERF] 2 queries total, not 2 per row
        data = [
            _serialize_campus_assigments(
                a, mine.get(a.id), section=sections.get(str(a.context_id)), staff_lookup=staff
            )
            for a in rows
        ]
        return paginator.get_paginated_response(data) if paginator is not None else Response(data)

    def retrieve(self, request, pk=None):
        assigments = get_object_or_404(Unifiedassigments.objects.filter(source=UnifiedassigmentsSource.CAMPUS), pk=pk)
        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        if assigments.context_id not in section_ids:
            raise PermissionDenied("You don't have access to this assigments.")
        return Response(_serialize_campus_assigments(assigments))

    @transaction.atomic
    def create(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        title = request.data.get("title")
        raw_due_date = request.data.get("due_date")
        if not (section_id and subject_id and title and raw_due_date):
            return Response(
                {"detail": "section, subject, title and due_date are all required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        due_date = parse_date(raw_due_date) if isinstance(raw_due_date, str) else raw_due_date
        if due_date is None:
            return Response({"due_date": "Must be a valid ISO date."}, status=status.HTTP_400_BAD_REQUEST)

        section = get_object_or_404(Section.objects.select_related("school_class"), pk=section_id)
        subject = get_object_or_404(Subject, pk=subject_id)
        campus_id = section.school_class.campus_id
        # [ASSUMPTION — NOT VERIFIED] mirrors the old `IsSectionSubjectStaffOrReadOnly`
        # permission class's likely check (its own source wasn't part of
        # this pass's upload) using the one already-available helper with
        # a matching name/shape — `can_manage_section_subject` is already
        # used for the equivalent grading-permission check further down
        # in `assigmentsSubmissionViewSet`.
        if not can_manage_section_subject(request.user, campus_id, section.id, subject_id):
            raise PermissionDenied("Only that section/subject's staff can post an assigments.")

        assigments = bridge.create_assigments(
            section=section,
            subject=subject,
            posted_by=request.user,
            title=title,
            description=request.data.get("description", ""),
            attachment=request.FILES.get("attachment"),
            due_date=due_date,
        )
        recipients = StudentEnrollment.objects.filter(section=section, status=StudentEnrollment.Status.ACTIVE)
        Notice.objects.create(
            campus=section.school_class.campus,
            section=section,
            session=section.school_class.session,
            posted_by=request.user,
            title=f"New assigments: {assigments.title}",
            body=f"Due {assigments.due_date}.",
        )
        bridge.notify(
            users=[e.student for e in recipients],
            notif_type=NotifTypes.assigments_POSTED_CAMPUS,
            title="New assigments posted",
            body=assigments.title,
        )
        return Response(_serialize_campus_assigments(assigments), status=status.HTTP_201_CREATED)


class assigmentsSubmissionViewSet(viewsets.ViewSet):
    """[Task 11] Thin proxy over `assigments.models.assigmentsSubmission`
    — see `assigmentsViewSet`'s own docstring above for why this isn't a
    `ModelViewSet`/`CampusMemberScopedMixin` subclass any more, and why
    `_serialize_campus_submission()` exists (old JSON shape:
    `id, assigments, student, submitted_at, file, status, grade,
    feedback` — preserved even though `status` can now additionally be
    `"checked"`/`"partially_checked"`, values the old 3-state model never
    produced; see that function's own docstring).

    Roster pre-create (bulk `MISSING` rows) behavior is unchanged — it
    still happens inside `assigments.bridge.create_context_assigments()`
    itself (called via `campus.bridge.create_assigments()` from
    `assigmentsViewSet.create()` above), not duplicated here.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def _get_scoped_submission(self, request, pk):
        """Mirrors the old `get_object()`'s own reasoning verbatim: looked
        up unrestricted, gated by an explicit `PermissionDenied` (403,
        "you can't do this") rather than a queryset-filtered 404 ("this
        doesn't exist") for someone with no relationship to the row at
        all."""
        submission = get_object_or_404(UnifiedassigmentsSubmission.objects.select_related("assigments", "student"), pk=pk)
        section = _section_for_assigments(submission.assigments)
        campus_id = section.school_class.campus_id if section else None
        user = request.user
        if submission.student_id != user.id and not (campus_id and is_any_active_staff(user, campus_id)):
            raise PermissionDenied("You don't have access to this submission.")
        return submission, section

    def list(self, request):
        section_ids, campus_by_section = _my_section_ids_and_campus_map(request.user)
        staff_campus_ids = set(
            StaffProfile.objects.filter(user=request.user, is_active=True).values_list("campus_id", flat=True)
        )
        qs = UnifiedassigmentsSubmission.objects.filter(
            assigments__source=UnifiedassigmentsSource.CAMPUS,
            assigments__context_type="section",
            assigments__context_id__in=section_ids,
        ).select_related("assigments", "student")
        # `?assigments=<id>` (what the Flutter student sheet / teacher roster send) used to be
        # ignored, so a student got their rows for EVERY assignment of the section and a teacher
        # the whole section's submissions. Invalid id -> empty list, not a 500.
        wanted = request.query_params.get("assigments")
        if wanted:
            qs = qs.filter(assigments_id=wanted) if bulk_ops._is_uuid(wanted) else qs.none()
        # Same breadth the old campus-wide (not section-scoped) filter had:
        # a student sees only their own rows; staff at the relevant campus
        # see every student's row for any section in that campus.
        # [PERF] visibility is decided in SQL now (was: load every submission of every accessible
        # section, then filter in Python — O(students x assignments) rows for a staff user).
        staff_sections = [sid for sid, cid in campus_by_section.items() if cid in staff_campus_ids]
        qs = qs.filter(Q(student_id=request.user.id) | Q(assigments__context_id__in=staff_sections))
        page, paginator = _paginate_if_requested(self, request, qs.order_by("-submitted_at", "id"))
        rows = list(page) if page is not None else list(qs)
        data = [_serialize_campus_submission(s) for s in rows]
        return paginator.get_paginated_response(data) if paginator is not None else Response(data)

    def retrieve(self, request, pk=None):
        submission, _section = self._get_scoped_submission(request, pk)
        return Response(_serialize_campus_submission(submission))

    def create(self, request):
        """Edge case only — normal case is the bulk MISSING pre-create in
        `assigmentsViewSet.create()`. Covers a student enrolled *after*
        an assigments was already posted, who therefore has no
        pre-created row yet. Written directly against the unified model
        (not through `assigments`'s own public-API serializer/viewset,
        whose `validate_assigments()` rejects `create()` for any
        non-personal-source assigments) — this is the same kind of
        trusted, internal bridge-style write `assigments.bridge.
        create_context_assigments()` itself already makes, not a way
        around that public-API restriction.
        """
        assigments_id = request.data.get("assigments")
        assigments = get_object_or_404(
            Unifiedassigments.objects.filter(source=UnifiedassigmentsSource.CAMPUS), pk=assigments_id
        )
        section = _section_for_assigments(assigments)
        if section is None:
            raise PermissionDenied("This assigments's section could not be resolved.")
        enrollment = StudentEnrollment.objects.filter(
            student=request.user, section=section, status=StudentEnrollment.Status.ACTIVE
        ).first()
        if enrollment is None:
            raise PermissionDenied("You can only create your own submission, for a section you're enrolled in.")
        submission, created = UnifiedassigmentsSubmission.objects.get_or_create(
            assigments=assigments,
            student=request.user,
            defaults={"roll_number": enrollment.roll_number, "enrollment_no": enrollment.enrollment_no},
        )
        return Response(
            _serialize_campus_submission(submission),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def partial_update(self, request, pk=None):
        submission, section = self._get_scoped_submission(request, pk)
        if submission.student_id == request.user.id:
            if submission.status in ("checked", "partially_checked"):
                raise bulk_ops.ValidationError({"detail": "This submission is already checked and can't be changed."})
            upload = request.FILES.get("file")
            note = request.data.get("written_content")
            note = submission.written_content if note is None else str(note).strip()
            # A submit needs something to hand in: a new file/photo, a note, or work already attached.
            if upload is None and not note and not submission.file:
                raise bulk_ops.ValidationError({"file": "Attach a file or photo (or write a note) to submit."})
            if upload is not None:
                # `submit_freeform()` saves with update_fields (no full_clean), so the model
                # field validators never ran for this path — enforce them here (pdf/jpg/png/webp, 10 MB).
                from django.core.exceptions import ValidationError as _DjangoValidationError
                from common.attachment_validators import attachment_extension_validator, validate_attachment_size
                try:
                    attachment_extension_validator(upload)
                    validate_attachment_size(upload)
                except _DjangoValidationError as exc:
                    raise bulk_ops.ValidationError({"file": " ".join(exc.messages)})
            # Keep what the student already handed in when only one of file / note is resent
            # (submit_freeform overwrites written_content, and the old file only if a new one comes).
            submission.submit_freeform(written_content=note, file=upload, link_url=submission.link_url)
            # Same post-submit hooks the unified endpoint runs: tell the teacher, scan for duplicates.
            # Neither may ever fail the student's own submit.
            try:
                from assignments.bridge import notify_submission_received
                notify_submission_received(submission)
            except Exception:
                logger.warning("campus.assignment_notify_failed submission_id=%s", submission.id, exc_info=True)
            try:
                from assignments import plagiarism
                plagiarism.check_submission_for_duplicates(submission)
            except Exception:
                logger.warning("campus.assignment_plagiarism_failed submission_id=%s", submission.id, exc_info=True)
            return Response(_serialize_campus_submission(submission))

        campus_id = section.school_class.campus_id if section else None
        subject_id = submission.assigments.data.get("subject_id")
        if campus_id is None or not can_manage_section_subject(request.user, campus_id, section.id, subject_id):
            raise PermissionDenied("Only the student or their subject teacher/admin can update this submission.")
        if submission.status == "missing":
            raise bulk_ops.ValidationError({"detail": "This student hasn't submitted yet, so there is nothing to check."})
        grade = str(request.data.get("grade", "")).strip()
        if not grade:
            raise bulk_ops.ValidationError({"grade": "Enter a grade."})
        if len(grade) > 10:
            raise bulk_ops.ValidationError({"grade": "Grade can be at most 10 characters."})
        submission.grade_freeform(grade=grade, feedback=str(request.data.get("feedback", "")).strip())
        bridge.notify(
            users=[submission.student_id],
            notif_type=bridge.Notification.NotifType.assigments_GRADED,
            title="Assignment checked",
            body=f"{submission.assigments.title}: {grade}",
            data={
                "context_type": "section",
                "context_id": str(submission.assigments.context_id or ""),
                "assignment_id": str(submission.assigments_id),
            },
        )
        return Response(_serialize_campus_submission(submission))


# ============================================================
# Task 13 — test series
# ============================================================
def _section_for_testseries(series):
    """[Task 13] `series.context_id` IS a `Section.id` for every
    `source="campus"` `TestSeries` (see `campus.bridge.
    create_testseries()`) — the same opaque-id-to-real-row resolution
    `_section_for_assigments()` above does, and for the same reason:
    `testseries`, like `assigments`, never stores a real FK back to
    `Section` (golden rule).
    """
    return Section.objects.filter(pk=series.context_id).select_related("school_class").first()


def _serialize_campus_testseries(series):
    """[Task 13] Serializes a campus-sourced `testseries.models.
    TestSeries`. Unlike `_serialize_campus_assigments()` above, there's
    no OLD `campus.TestSeries` model/API shape to stay compatible with
    — this is a new feature, not a migration off a deprecated model —
    so this exposes the unified model's own fields directly instead of
    reconstructing anything.

    No `subject` key: `create_context_testseries()` has no slot to
    persist one (see `campus.bridge.create_testseries()`'s own
    docstring) — a campus test series is scoped to a `Section`, not a
    `Section`+`Subject` pair.

    `series.total_marks` — confirmed as a real stored `PositiveInteger
    Field` against this pass's `testseries/models.py` upload (previously
    flagged here as an unverified assumption, inferred only from
    `recompute_total_marks()`'s name).

    `price_coins` — [Task 19 — ORG_VS_INDIVIDUAL_MATRIX] added this pass
    alongside wiring up `Campus.testseries_paid_allowed`; was missing
    before even though `series.is_paid` was already exposed, because
    `is_paid` was always `False` and `price_coins` was therefore always
    `0` for every campus series — nothing to show. Now that a campus can
    opt in to paid series, `price_coins` is meaningful and belongs
    alongside `is_paid`.
    """
    section = _section_for_testseries(series)
    return {
        "id": series.id,
        "section": series.context_id,
        "creator": series.creator_id,
        "title": series.title,
        "description": series.description,
        "is_paid": series.is_paid,
        "price_coins": series.price_coins,
        "duration_minutes": series.duration_minutes,
        "attempts_allowed": series.attempts_allowed,
        "status": series.status,
        "total_marks": series.total_marks,
        "session": section.school_class.session_id if section else None,
    }


class TestSeriesViewSet(viewsets.ViewSet):
    """[Task 13] Thin proxy over the unified `testseries` app for
    campus-sourced series — the same "opaque `context_id`, no real FK,
    campus resolves it back to a `Section` itself" posture
    `assigmentsViewSet` above takes toward `assigments`, for the same
    golden-rule reason (`testseries/bridge.py`'s own module docstring).
    Not a `ModelViewSet`/`CampusMemberScopedMixin` subclass for the same
    reason `assigmentsViewSet` isn't (see its own docstring).

    Only list/retrieve/create for the series itself. Attempt listing and
    review/grading live on the sibling `TestAttemptViewSet` below, the
    same split `assigmentsViewSet`/`assigmentsSubmissionViewSet` already
    use — now that `campus.bridge.can_review_testseries_attempt()` is
    wired up against the real `testseries/models.py` shape.
    """

    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def list(self, request):
        from testseries.access import editable_context_ids

        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        qs = TestSeries.objects.filter(
            source=TestSeries.Source.CAMPUS, context_type="section", context_id__in=section_ids
        ).order_by("-id")
        # [T2] drafts exist now: a student / parent must never see one. Only the
        # creator and the section's authorised editors do.
        qs = qs.filter(
            Q(status=TestSeries.Status.PUBLISHED)
            | Q(creator=request.user)
            | Q(context_id__in=editable_context_ids(request.user, "section"))
        )
        section_filter = request.query_params.get("section")
        if section_filter:
            qs = qs.filter(context_id=section_filter)
        return Response([_serialize_campus_testseries(s) for s in qs])

    def retrieve(self, request, pk=None):
        series = get_object_or_404(TestSeries.objects.filter(source=TestSeries.Source.CAMPUS), pk=pk)
        from testseries.access import normalize_context_id, user_can_edit_series

        section_ids, _ = _my_section_ids_and_campus_map(request.user)
        # [T2] `context_id` is a UUID, `section_ids` are ints: compare normalised
        # (a plain `in` was always False, so retrieve 403'd for everyone).
        if normalize_context_id(series.context_id) not in {normalize_context_id(i) for i in section_ids}:
            raise PermissionDenied("You don't have access to this test series.")
        if series.status != TestSeries.Status.PUBLISHED and not user_can_edit_series(request.user, series):
            raise PermissionDenied("You don't have access to this test series.")
        return Response(_serialize_campus_testseries(series))

    @transaction.atomic
    def create(self, request):
        section_id = request.data.get("section")
        subject_id = request.data.get("subject")
        title = request.data.get("title")
        questions = request.data.get("questions")
        # [T2] `draft=true` -> create an empty-able DRAFT; questions are then
        # added / edited / deleted through the testseries question endpoints
        # (by the creator or the section's teaching staff) and it is published
        # with `POST /testseries/{id}/publish/`.
        draft = str(request.data.get("draft", "")).strip().lower() in ("1", "true", "yes", "on")
        if draft and questions is None:
            questions = []
        if not (section_id and subject_id and title and (questions or draft)):
            return Response(
                {"detail": "section, subject, title and questions are all required "
                           "(questions may be omitted when draft=true)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(questions, list):
            return Response({"detail": "questions must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        section = get_object_or_404(Section.objects.select_related("school_class"), pk=section_id)
        campus_id = section.school_class.campus_id
        # Task 13 checklist: "Staff permission check bridge call se
        # pehle hota hai, testseries khud trust karta hai caller ko" —
        # the same golden rule `assigmentsViewSet.create()` follows
        # above. `subject_id` is used ONLY for this check — see
        # `campus.bridge.create_testseries()`'s docstring for why it
        # isn't (and can't be) passed through or persisted.
        if not can_manage_section_subject(request.user, campus_id, section.id, subject_id):
            raise PermissionDenied("Only that section/subject's staff can post a test series.")

        # [Task 19 — ORG_VS_INDIVIDUAL_MATRIX] `is_paid`/`price_coins`
        # now actually reach `bridge.create_testseries()` (previously
        # dropped on the floor here — the request body was never even
        # read for them, so a paid campus series could never be created
        # regardless of `Campus.testseries_paid_allowed`). Checked here,
        # BEFORE the bridge call, so a request for a paid series against
        # a campus that hasn't opted in gets an explicit 403 instead of
        # a silent downgrade to free — same "permission check before the
        # bridge call" posture this method already uses for
        # `can_manage_section_subject()` above. `bridge.
        # create_testseries()` re-checks the same flag unconditionally
        # regardless (see its own docstring) — this is belt, that's
        # suspenders, neither is a substitute for the other.
        is_paid = bool(request.data.get("is_paid", False))
        try:
            price_coins = int(request.data.get("price_coins", 0) or 0)
        except (TypeError, ValueError):
            return Response({"detail": "price_coins must be a whole number."}, status=status.HTTP_400_BAD_REQUEST)
        if is_paid and not section.school_class.campus.testseries_paid_allowed:
            raise PermissionDenied("This campus isn't enabled for paid test series.")

        from testseries.bridge import QuestionPayloadError

        try:
            series = bridge.create_testseries(
                section=section,
                creator=request.user,
                title=title,
                description=request.data.get("description", ""),
                duration_minutes=request.data.get("duration_minutes"),
                attempts_allowed=request.data.get("attempts_allowed", 1),
                questions=questions,
                is_paid=is_paid,
                price_coins=price_coins,
                draft=draft,
            )
        except QuestionPayloadError as exc:
            # [T2] was an unhandled 500 (Django ValidationError out of full_clean()).
            return Response(
                {"detail": str(exc), "code": "validation_error", "errors": exc.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(_serialize_campus_testseries(series), status=status.HTTP_201_CREATED)


def _serialize_campus_attempt(attempt, *, include_responses=False):
    """[Task 13] Serializes a `testseries.models.TestAttempt` for
    campus's API. New feature, not a migration — no old shape to stay
    compatible with, so this exposes the unified model's fields
    directly (same posture `_serialize_campus_testseries()` above
    takes).

    `include_responses` pulls in each `QuestionResponse` (question id,
    marks, the student's answer, and reviewer feedback if any) — left
    optional so `list()` (many attempts at once) doesn't do an extra
    query per row for something only the `retrieve()`/review flow
    actually needs.

    Field list cross-checked against the real `TestAttemptSerializer`/
    `QuestionResponseSerializer` (this pass's `testseries/serializers.py`
    upload) — `attempt_number` was missing from an earlier pass of this
    function and has been added to match.
    """
    data = {
        "id": attempt.id,
        "series": attempt.series_id,
        "student": attempt.student_id,
        "attempt_number": attempt.attempt_number,
        "roll_number": attempt.roll_number,
        "enrollment_no": attempt.enrollment_no,
        "status": attempt.status,
        "auto_score": attempt.auto_score,
        "final_score": attempt.final_score,
        "submitted_at": attempt.submitted_at,
        "checked_at": attempt.checked_at,
        "checked_by": attempt.checked_by_id,
    }
    if include_responses:
        data["responses"] = [
            {
                "question": response.question_id,
                "marks": response.question.marks,
                "is_auto_graded": response.is_auto_graded,
                "is_correct": response.is_correct,
                "marks_awarded": response.marks_awarded,
                "answer_data": response.answer_data,
                "answer_attachment": response.answer_attachment.url if response.answer_attachment else None,
                "reviewer_feedback": response.reviewer_feedback,
            }
            for response in attempt.responses.select_related("question").order_by("question__order")
        ]
    return data


class TestAttemptViewSet(viewsets.ViewSet):
    """[Task 13] Thin proxy over `testseries.models.TestAttempt` for
    campus-sourced series — the sibling `assigmentsSubmissionViewSet`
    already establishes for `assigments` (see that class's own
    docstring for why this isn't a `ModelViewSet`/
    `CampusMemberScopedMixin` subclass: `TestSeries.context_id`, like
    `assigments.context_id`, is an opaque UUID field, not a real FK
    `CampusMemberScopedMixin`'s traversal could follow).

    No `create()` — CONFIRMED (this pass's `testseries/views.py` upload)
    a campus student starts/submits their own attempt via `testseries`'s
    own shared `TestAttemptViewSet` actions directly: `POST .../attempts/
    start/<series_id>/` then `POST .../attempts/<id>/submit/` — those
    endpoints already handle `source="campus"` series fine (they only
    branch on `series.is_paid`, which is always `False` for campus,
    taking the free/no-purchase path). Nothing campus-specific is
    missing there, so there's deliberately no campus-side equivalent of
    either action. This viewset only lists/reviews attempts that
    already exist.

    Review/grading, by contrast, IS duplicated here rather than
    delegated to `testseries`'s own `review_answer` action, for a
    concrete reason found this pass: that endpoint's `get_object()`
    filters through `TestAttemptViewSet.get_queryset()` there, which
    only matches `student=user` or `series__creator=user` — a
    non-creator campus staff member (e.g. a subject teacher who didn't
    personally post the series) gets a 404 before `user_can_review_
    attempt()` is ever consulted, even though that class's own
    docstring says such a reviewer should be allowed through (see the
    `[FLAGGED — NOT FIXED]` comment on that `get_queryset()` in
    `testseries/views.py`). `partial_update()` below sidesteps that gap
    entirely by resolving campus review rights independently, via
    `campus.bridge.can_review_testseries_attempt()` (`is_any_active_
    staff()` against the section's campus), then calling `TestAttempt.
    mark_answer_and_maybe_finalize()` directly — the same "trusted,
    internal bridge-style write" posture `assigmentsSubmissionViewSet.
    partial_update()` already takes toward `assigments`'s model layer
    above, not a way around `testseries`'s public API so much as a
    necessary one given that queryset gap.
    """

    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_permissions(self):
        return [permission() for permission in self.permission_classes]

    def _get_scoped_attempt(self, request, pk):
        """Mirrors `assigmentsSubmissionViewSet._get_scoped_submission()`'s
        own reasoning verbatim: looked up unrestricted, then gated by an
        explicit `PermissionDenied` (403) rather than a queryset-filtered
        404, for someone with no relationship to the row at all."""
        attempt = get_object_or_404(
            TestAttempt.objects.filter(series__source=TestSeries.Source.CAMPUS).select_related("series"), pk=pk
        )
        if attempt.student_id != request.user.id and not bridge.can_review_testseries_attempt(
            user=request.user, context_type=attempt.series.context_type, context_id=attempt.series.context_id
        ):
            raise PermissionDenied("You don't have access to this attempt.")
        return attempt

    def list(self, request):
        section_ids, campus_by_section = _my_section_ids_and_campus_map(request.user)
        staff_campus_ids = set(
            StaffProfile.objects.filter(user=request.user, is_active=True).values_list("campus_id", flat=True)
        )
        qs = TestAttempt.objects.filter(
            series__source=TestSeries.Source.CAMPUS,
            series__context_type="section",
            series__context_id__in=section_ids,
        ).select_related("series")
        series_filter = request.query_params.get("series")
        if series_filter:
            qs = qs.filter(series_id=series_filter)
        # Same breadth `assigmentsSubmissionViewSet.list()` uses above: a
        # student sees only their own rows; staff at the relevant campus
        # see every student's row for any section in that campus.
        rows = [
            a for a in qs
            if a.student_id == request.user.id
            or campus_by_section.get(a.series.context_id) in staff_campus_ids
        ]
        return Response([_serialize_campus_attempt(a) for a in rows])

    def retrieve(self, request, pk=None):
        attempt = self._get_scoped_attempt(request, pk)
        return Response(_serialize_campus_attempt(attempt, include_responses=True))

    def partial_update(self, request, pk=None):
        """Reviews ONE question's response on this attempt —
        `question`/`marks_awarded`/`feedback` in the request body — via
        `TestAttempt.mark_answer_and_maybe_finalize()`, which itself
        finalizes the whole attempt (score, status, payout release,
        notification) once every `text` question has been reviewed.
        Student-side submission of an attempt is NOT this method — that
        belongs to whatever public `testseries` attempt-submission
        endpoint already exists (see this class's own docstring); this
        is staff-only grading of an already-submitted attempt.
        """
        attempt = self._get_scoped_attempt(request, pk)
        if not bridge.can_review_testseries_attempt(
            user=request.user, context_type=attempt.series.context_type, context_id=attempt.series.context_id
        ):
            raise PermissionDenied("Only staff at this attempt's campus can review it.")

        question_id = request.data.get("question")
        marks_awarded = request.data.get("marks_awarded")
        if question_id is None or marks_awarded is None:
            return Response(
                {"detail": "question and marks_awarded are both required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        question = get_object_or_404(attempt.series.questions, pk=question_id)
        try:
            attempt.mark_answer_and_maybe_finalize(
                question=question,
                marks_awarded=int(marks_awarded),
                feedback=request.data.get("feedback", ""),
                reviewer=request.user,
            )
        except (ValueError, InvalidOperation) as exc:
            # `mark_answer()`'s own ValueError (auto-graded question,
            # negative marks, marks over question.marks) surfaces as a
            # clean 400 here rather than a 500 — same posture the rest
            # of this file takes toward model-level `ValueError`s (see
            # `FeePaymentViewSet`'s `InvalidOperation` handling).
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(_serialize_campus_attempt(attempt, include_responses=True))


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


# ============================================================
# Phase 7 — results
# ============================================================
class ExamTermViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = ExamTermSerializer
    campus_field_path = "session__campus"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(ExamTerm.objects.all(), self.request)

    def get_campus_id_for_permission_check(self, request):
        session_id = request.data.get("session")
        if session_id:
            s = AcademicSession.objects.filter(pk=session_id).first()
            if s:
                return s.campus_id
        return super().get_campus_id_for_permission_check(request)


def _publish_flag(data):
    """`publish` missing => True (purana "turant publish" behaviour)."""
    value = data.get("publish")
    return True if value is None or value == "" else _truthy(value)


class ResultEntryViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    Marks sheet. A `ResultEntry` is DRAFT (staff-only) or PUBLISHED (student +
    linked parents see it). Plain single-row create/`bulk` default to
    PUBLISHED, exactly as before; the grid sends `publish=false` to keep
    drafts and publishes the sheet in one tap later.

      GET  results/sheet/            roster + saved marks + state + stats
      POST results/bulk/             save sheet (`publish=false` => draft)
      POST results/import/           CSV preview (default) or `commit=true`
      GET  results/import-template/  blank CSV for one section
      POST results/publish/ | unpublish/   whole sheet
      POST results/lock/ | unlock/   sheet final; unlock = admin/principal only
      GET|PUT results/grade-scale/   campus grade bands + pass mark
      GET  results/report-card/      published marks only for students/parents

    A LOCKED sheet accepts no writes at all (edit, delete, new rows, CSV).
    `entered_by` is always the requesting staff member, never client-supplied.
    Publish pushes go out via Celery, ONE push per student.
    """
    serializer_class = ResultEntrySerializer
    campus_field_path = "enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "enrollment__student"
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]

    # ---- querysets / filters -------------------------------------------
    def get_queryset(self):
        qs = ResultEntry.objects.select_related("enrollment__section__school_class__campus", "subject")
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        # Draft marks: staff only. Students/parents never see them.
        vis = get_visibility(self.request.user, self.request)
        qs = qs.filter(Q(status=ResultEntry.Status.PUBLISHED) | Q(**{f"{self.campus_field_path}__id__in": vis.staff_campus_ids}))
        params = self.request.query_params
        for param, field in (
            ("subject", "subject_id"), ("exam_term", "exam_term_id"),
            ("section", "enrollment__section_id"), ("enrollment", "enrollment_id"),
        ):
            value = params.get(param)
            if value:
                if not bulk_ops._is_uuid(value):
                    return qs.none()
                qs = qs.filter(**{field: value})
        st = params.get("status")
        if st in (ResultEntry.Status.DRAFT, ResultEntry.Status.PUBLISHED):
            qs = qs.filter(status=st)
        return qs

    # ---- permission plumbing -------------------------------------------
    def get_campus_id_for_permission_check(self, request):
        # grade-scale PUT / unlock: campus from `campus` or from `section`.
        section_id = request.data.get("section")
        if section_id and bulk_ops._is_uuid(section_id):
            section = Section.objects.select_related("school_class").filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)

    def get_section_subject_for_permission_check(self, request):
        # Update/delete of one row: judge by the STORED row, never the body
        # (the body could name a section the caller does manage).
        if self.kwargs.get("pk"):
            obj = self.get_queryset().filter(pk=self.kwargs["pk"]).first()
            if obj:
                return obj.enrollment.section.school_class.campus_id, obj.enrollment.section_id, obj.subject_id
            return None, None, None
        subject_id = request.data.get("subject")
        if subject_id and not bulk_ops._is_uuid(subject_id):
            return None, None, None
        enrollment_id = request.data.get("enrollment")
        if enrollment_id:
            if not bulk_ops._is_uuid(enrollment_id):
                return None, None, None
            enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if enrollment:
                return enrollment.section.school_class.campus_id, enrollment.section_id, subject_id
            return None, None, None
        # Teacher 2-tap flow: section-level calls (`bulk`, `publish`, `lock` ...).
        section_id = request.data.get("section")
        if section_id and bulk_ops._is_uuid(section_id):
            section = Section.objects.select_related("school_class").filter(pk=section_id).first()
            if section:
                return section.school_class.campus_id, section.id, subject_id
        return None, None, None

    def get_throttles(self):
        if self.action == "import_csv":
            return [CampusResultsImportThrottle()]
        if self.action in ("publish", "unpublish") or (self.action == "bulk" and _publish_flag(self.request.data)):
            return [CampusResultsPublishThrottle()]
        return super().get_throttles()

    # ---- helpers ---------------------------------------------------------
    def _sheet_target(self, data, *, need_subject=True):
        section = bulk_ops.resolve_section(data.get("section"))
        campus_id = section.school_class.campus_id
        subject = bulk_ops.resolve_subject(data.get("subject"), campus_id, required=need_subject)
        exam_term = bulk_ops.resolve_exam_term(data.get("exam_term"), campus_id)
        return section, subject, exam_term

    def _audit(self, section, subject, exam_term, action, summary, **meta):
        log_action(
            section.school_class.campus_id, self.request.user, action, target=exam_term, summary=summary,
            section=str(section.id), subject=str(subject.id), **meta,
        )

    def _push(self, entries):
        """ONE push per student, from a Celery worker, after commit."""
        ids = [str(e.pk) for e in entries]
        if ids:
            dispatch_after_commit(campus_tasks.send_results_published, ids)

    # ---- sheet -------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="sheet")
    def sheet(self, request):
        section, subject, exam_term = self._sheet_target(request.query_params)
        campus = section.school_class.campus
        if not can_manage_section_subject(request.user, campus.id, section.id, subject.id):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(bulk_ops.build_sheet(section, subject, exam_term, campus))

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        """
        Ek table me saare students ke marks: `{section, subject, exam_term,
        max_marks?=100, publish?=true, rows: [{enrollment, marks_obtained,
        max_marks?, remarks?}]}`.

        Khaali `marks_obtained` wali rows skip hoti hain. Upsert hai (dobara
        save = edit). All-or-nothing: koi row galat ho to 400 +
        `{"rows": {enrollment_id: msg}}` aur kuch save nahi hota. LOCKED sheet
        = 400. `publish=false` => naye rows draft, koi notification nahi.
        """
        data = request.data
        section, subject, exam_term = self._sheet_target(data)
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            raise bulk_ops.ValidationError({"rows": "A non-empty list is required."})
        if any(not isinstance(r, dict) for r in rows):
            raise bulk_ops.ValidationError({"rows": "Each row must be an object."})

        summary, changed = bulk_ops.bulk_upsert_results(
            user=request.user, section=section, subject=subject, exam_term=exam_term,
            default_max=data.get("max_marks"), rows=rows, publish=_publish_flag(data),
        )
        self._push(changed)
        return Response(summary, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="publish")
    def publish(self, request):
        section, subject, exam_term = self._sheet_target(request.data)
        rows = bulk_ops.set_sheet_status(section=section, subject=subject, exam_term=exam_term, publish=True)
        self._push(rows)
        self._audit(section, subject, exam_term, "results.publish", f"Published {len(rows)} marks", count=len(rows))
        return Response({"published": len(rows)})

    @action(detail=False, methods=["post"], url_path="unpublish")
    def unpublish(self, request):
        section, subject, exam_term = self._sheet_target(request.data)
        rows = bulk_ops.set_sheet_status(section=section, subject=subject, exam_term=exam_term, publish=False)
        self._audit(section, subject, exam_term, "results.unpublish", f"Unpublished {len(rows)} marks", count=len(rows))
        return Response({"unpublished": len(rows)})

    @action(detail=False, methods=["post"], url_path="lock")
    def lock(self, request):
        section, subject, exam_term = self._sheet_target(request.data)
        n = bulk_ops.set_sheet_lock(
            user=request.user, section=section, subject=subject, exam_term=exam_term, lock=True
        )
        self._audit(section, subject, exam_term, "results.lock", f"Locked sheet ({n} rows)", count=n)
        return Response({"locked": n})

    @action(detail=False, methods=["post"], url_path="unlock", permission_classes=[IsAuthenticated, IsCampusAdminOrPrincipal])
    def unlock(self, request):
        section, subject, exam_term = self._sheet_target(request.data)
        n = bulk_ops.set_sheet_lock(
            user=request.user, section=section, subject=subject, exam_term=exam_term, lock=False
        )
        self._audit(section, subject, exam_term, "results.unlock", f"Unlocked sheet ({n} rows)", count=n)
        return Response({"unlocked": n})

    # ---- CSV -----------------------------------------------------------------
    @action(
        detail=False, methods=["post"], url_path="import", url_name="import",
        parser_classes=[MultiPartParser, FormParser, JSONParser], permission_classes=[IsAuthenticated],
    )
    def import_csv(self, request):
        """
        `{section, exam_term, file | csv_text, subject?, max_marks?=100,
        publish?=false, commit?=false}`. Default = PREVIEW (nothing saved):
        returns per-line errors/warnings + a preview with grades. With
        `commit=true` all-or-nothing: any error => 400-style report, nothing
        saved. Student matched by roll number / enrollment no / username,
        never by name. Each subject needs the caller's own section-subject
        permission.
        """
        data = request.data
        section = bulk_ops.resolve_section(data.get("section"))
        campus = section.school_class.campus
        if not is_any_active_staff(request.user, campus.id):
            raise PermissionDenied("Only campus staff can import results.")
        exam_term = bulk_ops.resolve_exam_term(data.get("exam_term"), campus.id)
        subject = bulk_ops.resolve_subject(data.get("subject"), campus.id) if data.get("subject") else None
        upload = request.FILES.get("file") if hasattr(request, "FILES") else None
        if upload is not None:
            if upload.size > 1_000_000:
                raise bulk_ops.ValidationError({"file": "File too large (max 1 MB)."})
            text = upload.read()
        else:
            text = data.get("csv_text")
        if not text:
            raise bulk_ops.ValidationError({"file": "A CSV file (or csv_text) is required."})
        commit = _truthy(data.get("commit"))
        publish = _truthy(data.get("publish"))
        if commit and publish:
            # the publish throttle guards the push fan-out
            throttle = CampusResultsPublishThrottle()
            if not throttle.allow_request(request, self):
                self.throttled(request, throttle.wait())

        report, changed = bulk_ops.import_results_csv(
            user=request.user, section=section, exam_term=exam_term, text=text, campus=campus,
            subject=subject, default_max=data.get("max_marks"),
            can_manage_subject=lambda sid: can_manage_section_subject(request.user, campus.id, section.id, sid),
            publish=publish, commit=commit,
        )
        if commit and report["errors"]:
            return Response(report, status=status.HTTP_400_BAD_REQUEST)
        self._push(changed)
        return Response(report)

    @action(detail=False, methods=["get"], url_path="import-template", url_name="import-template")
    def import_template(self, request):
        section = bulk_ops.resolve_section(request.query_params.get("section"))
        campus_id = section.school_class.campus_id
        if not is_any_active_staff(request.user, campus_id):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        roster = sorted(
            bulk_ops._active_roster(section),
            key=lambda e: ((0, int(e.roll_number), "") if e.roll_number.isdigit() else (1, 0, e.roll_number.lower())),
        )
        body = results_csv.build_template_csv(
            (e.roll_number or e.enrollment_no or e.student.username, bulk_ops._display_name(e.student))
            for e in roster
        )
        response = HttpResponse("﻿" + body, content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="results_template.csv"'
        return response

    # ---- grade scale -----------------------------------------------------------
    @action(
        detail=False, methods=["get", "put"], url_path="grade-scale", url_name="grade-scale",
        permission_classes=[IsAuthenticated, IsCampusAdminOrPrincipal],
    )
    def grade_scale(self, request):
        campus_id = self.get_campus_id_for_permission_check(request)
        if not campus_id or not bulk_ops._is_uuid(campus_id):
            raise bulk_ops.ValidationError({"campus": "This field is required."})
        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus or campus.id not in get_visibility(request.user, request).member_campus_ids:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.method == "PUT":
            clean, err = grading.validate_grade_scale(request.data.get("grade_scale"))
            if err:
                raise bulk_ops.ValidationError({"grade_scale": err})
            try:
                pass_percent = Decimal(str(request.data.get("pass_percent", campus.pass_percent)))
            except (InvalidOperation, ValueError):
                raise bulk_ops.ValidationError({"pass_percent": "Not a number."})
            if not pass_percent.is_finite() or not (Decimal("0") <= pass_percent <= Decimal("100")):
                raise bulk_ops.ValidationError({"pass_percent": "Must be between 0 and 100."})
            campus.grade_scale, campus.pass_percent = clean, pass_percent.quantize(Decimal("0.01"))
            campus.save(update_fields=["grade_scale", "pass_percent"])
            log_action(
                campus.id, request.user, "results.grade_scale.update", target=campus,
                summary="Grade scale updated", bands=len(clean), pass_percent=float(campus.pass_percent),
            )
        return Response({
            "grade_scale": grading.get_scale(campus.grade_scale), "pass_percent": float(campus.pass_percent),
            "is_default": not campus.grade_scale,
        })

    # ---- single-row write path ---------------------------------------------------
    def _assert_row_unlocked(self, entry):
        if entry.locked_at:
            raise bulk_ops.ValidationError({"detail": "This result is locked. Ask an admin or principal to unlock it."})

    @transaction.atomic
    def perform_create(self, serializer):
        enrollment = serializer.validated_data["enrollment"]
        subject = serializer.validated_data["subject"]
        exam_term = serializer.validated_data["exam_term"]
        campus_id = enrollment.section.school_class.campus_id
        if subject.campus_id != campus_id or exam_term.session.campus_id != campus_id:
            raise bulk_ops.ValidationError({"detail": "Subject / exam term belong to a different campus."})
        # Lock covers NEW rows too, not just existing ones.
        bulk_ops._assert_unlocked(enrollment.section, subject, exam_term)
        staff = StaffProfile.objects.filter(campus_id=campus_id, user=self.request.user, is_active=True).first()
        entry = serializer.save(entered_by=staff, status=ResultEntry.Status.PUBLISHED, published_at=timezone.now())
        recipients = [entry.enrollment.student]
        parent_ids = CampusParentLink.objects.filter(student=entry.enrollment.student).values_list("parent", flat=True)
        recipients += list(parent_ids)
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.RESULT_PUBLISHED,
            title="Result published",
            body=f"{entry.subject.name}: {entry.marks_obtained}/{entry.max_marks}",
        )

    def perform_update(self, serializer):
        self._assert_row_unlocked(serializer.instance)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_row_unlocked(instance)
        self.soft_delete_instance(instance)

    def _report_card_target(self, request):
        """`?enrollment=&exam_term=` -> `(enrollment, exam_term, is_staff, None)` or
        `(None, None, None, error Response)`. Student: own card only; parent: linked
        child only; staff: any card of their campus. 404 (not 403) for everything else."""
        enrollment_id = request.query_params.get("enrollment")
        exam_term_id = request.query_params.get("exam_term")
        if not enrollment_id or not exam_term_id:
            return None, None, None, Response(
                {"detail": "enrollment and exam_term query params are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not bulk_ops._is_uuid(enrollment_id) or not bulk_ops._is_uuid(exam_term_id):
            return None, None, None, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        exam_term = ExamTerm.objects.filter(pk=exam_term_id).first()
        if not enrollment or not exam_term:
            return None, None, None, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus_id = enrollment.section.school_class.campus_id
        is_staff = is_any_active_staff(request.user, campus_id)
        allowed = (
            is_staff
            or enrollment.student_id == request.user.id
            or is_linked_parent_of_student(request.user, enrollment.student_id, campus_id)
        )
        if not allowed:
            return None, None, None, Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return enrollment, exam_term, is_staff, None

    @action(detail=False, methods=["get"], url_path="report-card")
    def report_card(self, request):
        enrollment, exam_term, is_staff, error = self._report_card_target(request)
        if error is not None:
            return error
        return Response(generate_report_card_data(enrollment, exam_term, published_only=not is_staff))

    @action(detail=False, methods=["get"], url_path="report-card-pdf")
    def report_card_pdf(self, request):
        """`?enrollment=&exam_term=` -> the report card as an A4 PDF (same visibility rule
        as `report-card`: a student / parent only ever gets PUBLISHED marks). 404 when
        there is nothing published to print, 501 when `reportlab` is not installed."""
        enrollment, exam_term, is_staff, error = self._report_card_target(request)
        if error is not None:
            return error
        card = generate_report_card_data(enrollment, exam_term, published_only=not is_staff)
        if not card["subjects"]:
            return Response({"detail": "No published marks for this exam."}, status=status.HTTP_404_NOT_FOUND)
        section = enrollment.section
        campus = section.school_class.campus
        attendance = compute_attendance_summary(enrollment)
        try:
            pdf = render_report_card_pdf(
                campus_name=campus.name,
                campus_type=campus.type,
                student_name=_receipt_display_name(enrollment.student),
                roll_number=enrollment.roll_number or "",
                class_label=f"{section.school_class.name} - {section.name}",
                session_name=exam_term.session.name,
                term_name=exam_term.name,
                subjects=card["subjects"],
                total_obtained=card["total_obtained"],
                total_max=card["total_max"],
                percentage=card["percentage"],
                grade=card["grade"],
                passed=card["passed"],
                attendance_percent=attendance["percent"],
                attendance_total=attendance["total"],
                generated_at=timezone.now(),
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="report-card-{enrollment.id}-{exam_term.id}.pdf"'
        return response

    @action(detail=False, methods=["get"], url_path="progress")
    def progress(self, request):
        """`?enrollment=<id>` -> marks trend across the exam terms of the enrollment's session:
        `{terms: [{exam_term, name, start_date, percentage, grade, passed, total_obtained,
        total_max, subjects: [{subject, subject_name, percentage, grade}]}], subjects: [...]}`.
        Students / parents see PUBLISHED marks only; a term with nothing visible is left out."""
        enrollment_id = request.query_params.get("enrollment")
        if not enrollment_id or not bulk_ops._is_uuid(enrollment_id):
            return Response({"detail": "enrollment query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        enrollment = StudentEnrollment.objects.filter(pk=enrollment_id).first()
        if enrollment is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        campus_id = enrollment.section.school_class.campus_id
        is_staff = is_any_active_staff(request.user, campus_id)
        if not (
            is_staff
            or enrollment.student_id == request.user.id
            or is_linked_parent_of_student(request.user, enrollment.student_id, campus_id)
        ):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(progress_data.marks_progress(enrollment, published_only=not is_staff))


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


def _require_fee_module_enabled(campus):
    """FEE-1: single shared gate for every fee-writing entry point, so
    a campus that never opted in (`Campus.fee_module_enabled=False`)
    can't have fee structures, invoices, or payments created against
    it through ANY path — not just `FeeStructureViewSet.perform_create`,
    which was previously the only place this was checked."""
    if not campus.fee_module_enabled:
        raise PermissionDenied("The fee module is not enabled for this campus (design doc §8 — opt-in only).")


class FeeStructureViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = FeeStructureSerializer
    campus_field_path = "campus"
    member_scope = "custom"

    def member_scope_q(self, vis):
        # a student sees campus-wide fees + their own class's fees only
        return Q(school_class__isnull=True) | Q(school_class_id__in=vis.visible_class_ids)
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeeStructure.objects.all(), self.request)

    def perform_create(self, serializer):
        campus = serializer.validated_data["campus"]
        _require_fee_module_enabled(campus)
        serializer.save()

    @action(detail=True, methods=["post"], url_path="generate-invoices")
    def generate_invoices(self, request, pk=None):
        """Bulk-creates a `FeeInvoice` for every currently-active
        enrollment this structure applies to (campus-wide if
        `school_class` is null, else just that class) — idempotent via
        `unique_invoice_per_structure`, so calling this twice never
        double-invoices the same student.

        FEE-1: this used to be reachable even for a campus that had
        since turned `fee_module_enabled` off after the FeeStructure
        was created (the module-disabled check only ran once, at
        FeeStructure-creation time, in `perform_create` above) — gated
        the same way here now."""
        fee_structure = self.get_object()
        _require_fee_module_enabled(fee_structure.campus)
        enrollments = StudentEnrollment.objects.filter(
            section__school_class__campus=fee_structure.campus,
            session=fee_structure.session,
            status=StudentEnrollment.Status.ACTIVE,
        )
        if fee_structure.school_class_id:
            enrollments = enrollments.filter(section__school_class_id=fee_structure.school_class_id)
        created = 0
        for enrollment in enrollments.select_related("section__school_class"):
            # FINANCE: numbers the invoice and applies the student's active concession grants.
            _, was_created = finance.create_invoice_for(enrollment, fee_structure)
            created += int(was_created)
        return Response({"invoices_created": created, "already_existed": enrollments.count() - created})

    # ------------------------------------------------------------------ FINANCE
    def perform_update(self, serializer):
        fine_fields = {"late_fine_type", "late_fine_value", "late_fine_grace_days", "late_fine_cap",
                       "due_date", "allow_concession", "category"}
        campus = serializer.instance.campus
        finance_need(self.request.user, campus, "fee.manage")
        touched = bool(fine_fields & set(serializer.validated_data))
        with transaction.atomic():
            fs = serializer.save()
            repriced = finance.refresh_structure_invoices(fs) if touched else 0
        log_action(campus.id, self.request.user, "finance.structure.update", target=fs,
                   summary=f"Fee '{fs.title}' updated; {repriced} open invoice(s) re-priced")

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        """Re-price every open invoice of this fee (concessions + late fine). Idempotent."""
        fs = self.get_object()
        finance_need(request.user, fs.campus, "fee.manage")
        _require_fee_module_enabled(fs.campus)
        return Response({"invoices_changed": finance.refresh_structure_invoices(fs)})

    @action(detail=False, methods=["post"], url_path="create-plan")
    def create_plan(self, request):
        """One fee split into N instalments that add up EXACTLY (paise-safe).
        Body: campus, session, [school_class], title, total_amount, installments (1-12),
        first_due_date, [every_months=1], [category], [allow_concession=true],
        [late_fine_type, late_fine_value, late_fine_grace_days, late_fine_cap],
        [generate_invoices=false], [dry_run=false]"""
        d = request.data
        campus = get_object_or_404(Campus, pk=d.get("campus")) if d.get("campus") else None
        if campus is None:
            raise ValidationError({"campus": "campus is required."})
        finance_need(request.user, campus, "fee.manage")
        _require_fee_module_enabled(campus)
        session = get_object_or_404(AcademicSession, pk=d.get("session"), campus=campus)
        school_class = None
        if d.get("school_class"):
            school_class = get_object_or_404(SchoolClass, pk=d["school_class"], campus=campus, session=session)
        title = (d.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": "Give the fee a name."})
        try:
            total = finance_rules.parse_money(d.get("total_amount"))
            parts = int(d.get("installments", 1))
            every = int(d.get("every_months", 1))
            first_due = date.fromisoformat(str(d.get("first_due_date")))
        except (ValueError, TypeError) as exc:
            raise ValidationError({"detail": f"Check total_amount / installments / every_months / first_due_date: {exc}"})
        if not 1 <= parts <= 12 or not 1 <= every <= 12:
            raise ValidationError({"installments": "Use 1-12 instalments, 1-12 months apart."})
        category = d.get("category", FeeStructure.Category.OTHER)
        if category not in FeeStructure.Category.values:
            raise ValidationError({"category": "Unknown fee category."})
        fine = {
            "late_fine_type": d.get("late_fine_type", FeeStructure.FineType.NONE),
            "late_fine_value": d.get("late_fine_value", 0) or 0,
            "late_fine_grace_days": int(d.get("late_fine_grace_days", 0) or 0),
            "late_fine_cap": d.get("late_fine_cap") or None,
        }
        check = FeeStructureSerializer(data={
            "campus": campus.id, "session": session.id, "title": title, "amount": str(total),
            "due_date": first_due.isoformat(), "category": category, **{k: v for k, v in fine.items() if v is not None},
        })
        check.is_valid(raise_exception=True)
        preview = [
            {"title": t, "amount": str(a), "due_date": dd.isoformat()}
            for t, a, dd in zip(finance_rules.installment_titles(title, parts),
                                finance_rules.split_amount(total, parts),
                                finance_rules.installment_dates(first_due, parts, every))
        ]
        if d.get("dry_run") in (True, "true", "1", 1):
            return Response({"dry_run": True, "installments": preview})
        structures, invoices = finance.create_installment_plan(
            campus=campus, session=session, school_class=school_class, title=title, total=total, parts=parts,
            first_due=first_due, every_months=every, category=category,
            allow_concession=d.get("allow_concession", True) not in (False, "false", "0", 0), fine=fine,
            generate=d.get("generate_invoices") in (True, "true", "1", 1),
        )
        log_action(campus.id, request.user, "finance.plan.create", target=structures[0],
                   summary=f"Plan '{title}': {parts} instalment(s), total {total}", invoices=invoices)
        return Response({"installments": preview, "structure_ids": [str(x.id) for x in structures],
                         "invoices_created": invoices}, status=status.HTTP_201_CREATED)


class FeeInvoiceViewSet(SoftDeleteViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """
    FEE-1 (gap found, not in the original task list but the same class
    of bug): this is a full `ModelViewSet`, so before this pass a
    campus admin could `POST` a `FeeInvoice` directly here even for a
    campus with `fee_module_enabled=False` — only
    `FeeStructureViewSet.perform_create`/`.generate_invoices` were
    gated, and this viewset's normal create path was never routed
    through either of those. Gated below the same way, for consistency
    with "agar False chuna, to fee-related sab UI/endpoints hidden/403
    rahein" from the task.
    """
    serializer_class = FeeInvoiceSerializer
    campus_field_path = "enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "enrollment__student"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(FeeInvoice.objects.all(), self.request)
        if getattr(self, "action", None) == "list":
            # [PERF] amount_paid + balance each ran a SUM query per invoice (2N+1 for a page).
            # One grouped annotation per page; FeeInvoice.amount_paid reads it (list only, so
            # payment/recompute flows never see a stale cached total).
            qs = qs.annotate(paid_total_ann=Coalesce(
                Sum("payments__amount", filter=Q(payments__status=FeePayment.Status.SUCCESS)),
                Value(Decimal("0")),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ))
        return qs

    def perform_create(self, serializer):
        enrollment = serializer.validated_data["enrollment"]
        _require_fee_module_enabled(enrollment.section.school_class.campus)
        serializer.save()

    # ------------------------------------------------------------------ FINANCE
    def _manage(self, request, invoice):
        campus = invoice.enrollment.section.school_class.campus
        finance_need(request.user, campus, "fee.manage")
        _require_fee_module_enabled(campus)
        return campus

    def _audit(self, request, campus, name, invoice, summary):
        log_action(campus.id, request.user, f"finance.invoice.{name}", target=invoice,
                   summary=summary, invoice_no=invoice.invoice_no)

    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        invoice = finance.refresh_invoice(invoice)
        self._audit(request, campus, "recalculate", invoice, "Invoice re-priced")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="waive")
    def waive(self, request, pk=None):
        """Write the whole invoice off. Body: {"reason": "..."} (required)."""
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        reason = (request.data.get("reason") or "").strip()
        if len(reason) < 3:
            raise ValidationError({"reason": "Give a reason (it is kept in the audit log)."})
        try:
            invoice = finance.waive_invoice(invoice, reason)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "waive", invoice, f"Invoice waived: {reason[:120]}")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="unwaive")
    def unwaive(self, request, pk=None):
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        try:
            invoice = finance.unwaive_invoice(invoice)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "unwaive", invoice, "Waiver removed")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["post"], url_path="waive-fine")
    def waive_fine(self, request, pk=None):
        """Remove the late fine (and stop it re-accruing). Body: {"reason": "...", "restore": false}"""
        invoice = self.get_object()
        campus = self._manage(request, invoice)
        restore = request.data.get("restore") in (True, "true", "1", 1)
        reason = (request.data.get("reason") or "").strip()
        if not restore and len(reason) < 3:
            raise ValidationError({"reason": "Give a reason (it is kept in the audit log)."})
        try:
            invoice = finance.set_fine_waived(invoice, not restore, reason)
        except finance.FinanceError as exc:
            raise ValidationError({"detail": exc.detail})
        self._audit(request, campus, "fine_restore" if restore else "fine_waive", invoice,
                    "Late fine restored" if restore else f"Late fine waived: {reason[:120]}")
        return Response(FeeInvoiceSerializer(invoice).data)

    @action(detail=True, methods=["get"], url_path="invoice-pdf")
    def invoice_pdf(self, request, pk=None):
        """The whole-invoice PDF (fee, concession, fine, payable, paid, balance, payments).
        Same visibility as the invoice itself (student / linked parent / campus staff)."""
        invoice = self.get_object()
        finance.refresh_fine(invoice)
        en = invoice.enrollment
        campus = en.section.school_class.campus
        fs = invoice.fee_structure
        concessions = [g.concession.name for g in finance.eligible_grants(invoice)]
        fine_note = ""
        if invoice.fine_amount and fs.due_date:
            fine_note = f"{max((timezone.localdate() - fs.due_date).days, 0)} days after due date"
        payments = [
            (finance._local_date(p.paid_at or p.created_at), p.payment_mode,
             p.gateway_payment_id or p.gateway_reference or p.notes, p.amount)
            for p in invoice.payments.filter(status=FeePayment.Status.SUCCESS).order_by("paid_at", "created_at")
        ]
        try:
            blob = render_invoice_pdf(
                invoice_no=invoice.invoice_no, campus_name=campus.name, campus_type=campus.type,
                student_name=_receipt_display_name(en.student), roll_number=en.roll_number,
                class_name=en.section.school_class.name, section_name=en.section.name, session_name=fs.session.name,
                fee_title=fs.title, fee_category=fs.category, issued_on=finance._local_date(invoice.created_at),
                due_date=fs.due_date, base_amount=invoice.base_amount, concession_amount=invoice.concession_amount,
                fine_amount=invoice.fine_amount, amount_due=invoice.amount_due, amount_paid=invoice.amount_paid,
                status=invoice.status, concession_names=concessions, fine_note=fine_note, payments=payments,
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        resp = HttpResponse(blob, content_type="application/pdf")
        name = (invoice.invoice_no or str(invoice.id)).replace("/", "-")
        resp["Content-Disposition"] = f'attachment; filename="invoice-{name}.pdf"'
        resp["Cache-Control"] = "no-store"
        return resp

    @action(detail=False, methods=["get"], url_path="my-summary")
    def my_summary(self, request):
        """"Fees dikhe": one call for the student / parent fee screen.

        `?student=<user id>` -> that child (a parent) or yourself; without it -> you + every child
        linked to you. Optional `?campus=`. Each invoice carries what is paid / still owed, a live
        `display_status` (an unpaid invoice past its due date reads "overdue" even if the cached
        column has not been recomputed), whether it can be paid online, and its payments with the
        receipt URL of the successful ones.
        """
        from django.urls import reverse

        qs = self.get_queryset().filter(
            enrollment__section__school_class__campus__fee_module_enabled=True
        )
        student_id = request.query_params.get("student")
        if student_id:
            qs = qs.filter(enrollment__student_id=student_id)
        else:
            mine = {request.user.id}
            mine.update(CampusParentLink.objects.filter(parent=request.user).values_list("student_id", flat=True))
            qs = qs.filter(enrollment__student_id__in=mine)
        qs = qs.select_related(
            "fee_structure", "enrollment__student", "enrollment__section__school_class__campus"
        ).prefetch_related("payments").order_by("fee_structure__due_date", "-created_at")

        today = timezone.localdate()
        online_ok = fee_gateway.online_enabled()
        invoices = []
        tot_due = tot_paid = tot_overdue = Decimal("0.00")
        for inv in qs:
            if inv.fee_structure.late_fine_type != FeeStructure.FineType.NONE and inv.status in (
                    FeeInvoice.Status.PENDING, FeeInvoice.Status.PARTIAL, FeeInvoice.Status.OVERDUE):
                finance.refresh_fine(inv)  # FINANCE: show today's late fine, not last night's
            paid = sum((p.amount for p in inv.payments.all() if p.status == FeePayment.Status.SUCCESS), Decimal("0.00"))
            due = fee_gateway.money(inv.amount_due)
            paid = fee_gateway.money(paid)
            balance = max(due - paid, Decimal("0.00"))
            if inv.status == FeeInvoice.Status.WAIVED:
                display, balance = "waived", Decimal("0.00")
            elif balance <= 0:
                display = "paid"
            elif inv.fee_structure.due_date and inv.fee_structure.due_date < today:
                display = "overdue"
            else:
                display = "partial" if paid > 0 else "pending"
            if display != "waived":
                tot_due += due
                tot_paid += min(paid, due)
                if display == "overdue":
                    tot_overdue += balance
            payments = []
            for p in sorted(inv.payments.all(), key=lambda x: x.created_at, reverse=True):
                if p.status == FeePayment.Status.PENDING and p.payment_mode == FeePayment.Mode.ONLINE:
                    continue  # an unfinished checkout is not something to show a parent
                payments.append({
                    "id": str(p.id),
                    "amount": str(p.amount),
                    "status": p.status,
                    "payment_mode": p.payment_mode,
                    "method": p.gateway_method,
                    "transaction_id": p.gateway_payment_id,
                    "paid_at": (p.paid_at or p.created_at).isoformat(),
                    "receipt_url": request.build_absolute_uri(
                        reverse("fee-payment-receipt-pdf", args=[p.id])
                    ) if p.status == FeePayment.Status.SUCCESS else None,
                })
            student = inv.enrollment.student
            invoices.append({
                "id": str(inv.id),
                "student": str(student.id),
                "student_name": _receipt_display_name(student),
                "fee_title": inv.fee_structure.title,
                "due_date": inv.fee_structure.due_date.isoformat() if inv.fee_structure.due_date else None,
                "amount_due": str(due),
                "amount_paid": str(paid),
                "balance": str(balance),
                # FINANCE breakdown: amount_due = base - concession + fine
                "invoice_no": inv.invoice_no,
                "base_amount": str(inv.base_amount),
                "concession_amount": str(inv.concession_amount),
                "fine_amount": str(inv.fine_amount),
                "invoice_pdf_url": request.build_absolute_uri(reverse("fee-invoice-invoice-pdf", args=[inv.id])),
                "status": inv.status,
                "display_status": display,
                "can_pay_online": online_ok and balance > 0,
                "payments": payments,
            })
        return Response({
            "currency": "INR",
            "online_enabled": online_ok,
            "online_methods": fee_gateway.allowed_methods() if online_ok else [],
            "totals": {
                "due": str(tot_due), "paid": str(tot_paid),
                "balance": str(max(tot_due - tot_paid, Decimal("0.00"))), "overdue": str(tot_overdue),
            },
            "invoices": invoices,
        })

    def get_campus_id_for_permission_check(self, request):
        enrollment_id = request.data.get("enrollment")
        if enrollment_id:
            e = StudentEnrollment.objects.filter(pk=enrollment_id).first()
            if e:
                return e.section.school_class.campus_id
        return super().get_campus_id_for_permission_check(request)


class FeePaymentViewSet(CampusMemberScopedMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """
    FEE-2: `pay` used to be `payment_mode=ONLINE`, landing `PENDING`
    until a separate `confirm` action was called (standing in for a
    Razorpay webhook). That online gateway + confirm step is gone —
    `pay` now debits the payer's `user_profile.CoinLedger`-backed
    wallet synchronously and atomically (`CoinLedger.record_transaction`
    holds a DB row lock for the whole operation), so there is no
    PENDING-until-webhook window to confirm and the `confirm` action
    has been removed as dead code, not left in place unreachable.

    Two write paths, both still first-class: `pay` — student/parent
    self-serve, `payment_mode=WALLET`, resolves straight to SUCCESS or
    a clean 402 in the same request; `record` — office staff logging a
    cash/cheque/bank-transfer payment (no wallet touched at all),
    marked `SUCCESS` immediately, same as before. No generic create/
    update/delete — every write goes through one of those two actions.
    """
    serializer_class = FeePaymentSerializer
    campus_field_path = "invoice__enrollment__section__school_class__campus"
    member_scope = "student"
    member_student_path = "invoice__enrollment__student"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(FeePayment.objects.all(), self.request)

    def _get_invoice_and_check(self, request, allow_self_pay):
        invoice_id = request.data.get("invoice")
        invoice = FeeInvoice.objects.filter(pk=invoice_id).first() if invoice_id else None
        if not invoice:
            return None, Response({"detail": "invoice is required."}, status=status.HTTP_400_BAD_REQUEST)
        campus = invoice.enrollment.section.school_class.campus
        # FEE-1: gate every fee write, not just FeeStructure creation —
        # a disabled module means no new invoices should be payable
        # either, even if the invoice row itself already exists.
        if not campus.fee_module_enabled:
            return None, Response(
                {"detail": "The fee module is not enabled for this campus."}, status=status.HTTP_403_FORBIDDEN
            )
        campus_id = campus.id
        if allow_self_pay:
            is_self = invoice.enrollment.student_id == request.user.id
            is_parent = is_linked_parent_of_student(request.user, invoice.enrollment.student_id, campus_id)
            if not (is_self or is_parent):
                return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        elif not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return None, Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        return invoice, None

    @staticmethod
    def _resolve_whole_coin_amount(raw_amount):
        """FEE-2 unit-mismatch guard (see models.py module docstring):
        FeePayment.amount is a Decimal (paise-capable), CoinLedger.amount/
        User.coin are whole integers. Returns `(amount_decimal, error_response)`
        — `error_response` is set instead of raising so callers can
        `return` it directly, matching this viewset's existing
        `(value, error)` convention (`_get_invoice_and_check` above)."""
        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError):
            return None, Response({"detail": "amount must be a valid number."}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= 0:
            return None, Response({"detail": "amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)
        if amount != amount.to_integral_value():
            return None, Response(
                {"detail": "Wallet payments must be a whole number of coins (no paise)."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return amount, None

    # B-4 fix — see CampusFeePaymentThrottle's docstring in throttles.py:
    # same scope reused across pay/record/refund below since all three
    # move money on an invoice, just through different roles/paths.
    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def pay(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=True)
        if error:
            return error
        amount, error = self._resolve_whole_coin_amount(request.data.get("amount", invoice.amount_due))
        if error:
            return error
        payer_role = (
            FeePayment.PayerRole.STUDENT
            if invoice.enrollment.student_id == request.user.id
            else FeePayment.PayerRole.PARENT
        )
        gateway_reference = request.data.get("gateway_reference") or uuid.uuid4().hex

        # get_or_create keyed on gateway_reference is the FeePayment-level
        # idempotency guard: a client retrying the SAME request (same
        # reference) gets back the row from its first attempt instead of
        # a second FeePayment (which unique_nonblank_gateway_reference
        # would reject anyway) and, critically, without a second coin debit.
        payment, created = FeePayment.objects.get_or_create(
            gateway_reference=gateway_reference,
            defaults=dict(
                invoice=invoice,
                amount=amount,
                paid_by=request.user,
                payer_role=payer_role,
                payment_mode=FeePayment.Mode.WALLET,
                status=FeePayment.Status.PENDING,
            ),
        )
        if not created:
            return Response(FeePaymentSerializer(payment).data, status=status.HTTP_200_OK)

        try:
            CoinLedger.objects.record_transaction(
                user=request.user,
                transaction_type=CoinLedger.TransactionType.SPEND,
                amount=-int(amount),
                # Derived from the already-deduplicated payment row's own
                # id, not a fresh random value per call — a second,
                # independent idempotency guarantee at the wallet layer
                # itself (see models.py module docstring), distinct from
                # the gateway_reference guard above.
                reference=f"fee-payment-{payment.id}",
                description=f"Fee paid for {invoice.fee_structure.title}",
                metadata={"invoice_id": str(invoice.id), "campus_id": str(invoice.enrollment.section.school_class.campus_id)},
                # S9: school fee is not "spending coins on the app" - the user's spend limit / minor cap must not block it.
                enforce_spend_limit=False,
            )
        except ValueError:
            # FEE-3: insufficient balance — clean, catchable, no exception
            # bubbling up as a 500. Payment row is kept as FAILED (not
            # deleted) so there's an audit trail of the attempt; the
            # gateway_reference stays reserved so a genuine retry with the
            # same client-side idempotency key doesn't collide with a NEW
            # attempt at a different amount.
            payment.status = FeePayment.Status.FAILED
            payment.save(update_fields=["status"])
            request.user.refresh_from_db(fields=["coin"])
            shortfall = int(amount) - request.user.coin
            return Response(
                {
                    "detail": "Insufficient coin balance to pay this fee.",
                    "current_balance": request.user.coin,
                    "required": int(amount),
                    "coins_needed": shortfall,
                    # FEE-3: no new coin-purchase gateway here — point the
                    # client at the existing tuitionclass top-up flow instead
                    # of rebuilding one. Campus deliberately never imports
                    # tuitionclass (see models.py GOLDEN RULE), so this is a
                    # generic flag for the frontend to route on, not a
                    # hardcoded tuitionclass URL.
                    "action": "top_up_coins",
                },
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        payment.mark_success()
        # Task 5 subtask 3 — fire-and-forget receipt email, same dispatch
        # bridge.notify uses for push: enqueued only once this request's
        # transaction actually commits, never blocks the response.
        dispatch_after_commit(campus_tasks.send_fee_receipt_email, payment.id)
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    # ---------------------------------------------- FEE-5: online (Razorpay UPI / card)
    @action(detail=False, methods=["post"], url_path="online-order", throttle_classes=[CampusFeePaymentThrottle])
    def online_order(self, request):
        """Start an online payment: -> Razorpay order for Checkout (see campus/fee_gateway.py).
        Same who-may-pay rule as `pay`: the student or a linked parent."""
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=True)
        if error:
            return error
        payer_role = (
            FeePayment.PayerRole.STUDENT
            if invoice.enrollment.student_id == request.user.id
            else FeePayment.PayerRole.PARENT
        )
        return fee_gateway.start_online_payment(
            invoice=invoice, user=request.user, payer_role=payer_role, raw_amount=request.data.get("amount"),
        )

    @action(detail=False, methods=["post"], url_path="online-verify", throttle_classes=[CampusFeePaymentThrottle])
    def online_verify(self, request):
        """After Checkout succeeds: send razorpay_order_id / razorpay_payment_id / razorpay_signature.
        Settles only on a valid signature; the signed webhook is the backup."""
        return fee_gateway.verify_online_payment(
            user=request.user,
            order_id=str(request.data.get("razorpay_order_id") or ""),
            payment_id=str(request.data.get("razorpay_payment_id") or ""),
            signature=str(request.data.get("razorpay_signature") or ""),
        )

    @action(detail=False, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def record(self, request):
        invoice, error = self._get_invoice_and_check(request, allow_self_pay=False)
        if error:
            return error
        finance.refresh_fine(invoice)  # FINANCE: quote today's fine, not yesterday's
        balance = max(finance_rules.money(invoice.amount_due) - finance_rules.money(invoice.amount_paid), finance_rules.ZERO)
        raw = request.data.get("amount")
        if raw in (None, ""):
            raw = balance
        try:  # office money is rupees-and-paise (only WALLET payments need whole coins)
            amount = finance_rules.parse_money(raw)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        payment_mode = request.data.get("payment_mode", FeePayment.Mode.CASH)
        if payment_mode == FeePayment.Mode.WALLET:
            # Office staff shouldn't be able to trigger a wallet debit
            # through the "manual counter payment" path — that's what
            # `pay` is for, and it's the payer's own wallet, not
            # something staff can spend on someone else's behalf here.
            return Response(
                {"detail": "Use the pay action for wallet payments; record is for cash/cheque/bank-transfer only."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        payment = FeePayment.objects.create(
            invoice=invoice,
            amount=amount,
            payer_role=FeePayment.PayerRole.ADMIN,
            payment_mode=payment_mode,
            status=FeePayment.Status.SUCCESS,
            recorded_by=request.user,
            notes=request.data.get("notes", ""),
        )
        payment.invoice.recompute_status()
        # Task 5 subtask 3 — same receipt-email dispatch as the wallet
        # path above; `record` payments are created SUCCESS immediately
        # (no mark_success() call), so this is the equivalent hook here.
        dispatch_after_commit(campus_tasks.send_fee_receipt_email, payment.id)
        return Response(FeePaymentSerializer(payment).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], throttle_classes=[CampusFeePaymentThrottle])
    def refund(self, request, pk=None):
        """FEE-4: reverses a wallet payment and credits the coins back
        via `FeePayment.refund()`. Office/admin action only — same
        permission shape as `record`, not self-serve like `pay`."""
        payment = self.get_object()
        campus_id = payment.invoice.enrollment.section.school_class.campus_id
        if not is_campus_admin_or_principal(request.user, campus_id) and not is_any_active_staff(request.user, campus_id):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        try:
            payment.refund()
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FeePaymentSerializer(payment).data)

    # ------------------------------------------------------ receipt PDF (Task 5, subtask 2/4)
    def _check_receipt_view_permission(self, request, payment):
        """Who can see a payment's receipt: the payer themselves, a
        linked parent of the paying student, or campus admin/staff —
        same three roles `_get_invoice_and_check` already recognizes for
        the invoice as a whole (self-pay vs. office-side), just read-only
        here instead of a write gate."""
        invoice = payment.invoice
        campus_id = invoice.enrollment.section.school_class.campus_id
        is_payer = payment.paid_by_id == request.user.id
        is_self = invoice.enrollment.student_id == request.user.id
        is_parent = is_linked_parent_of_student(request.user, invoice.enrollment.student_id, campus_id)
        is_staff = is_campus_admin_or_principal(request.user, campus_id) or is_any_active_staff(request.user, campus_id)
        return is_payer or is_self or is_parent or is_staff

    @action(detail=True, methods=["get"], url_path="receipt-pdf")
    def receipt_pdf(self, request, pk=None):
        payment = self.get_object()
        if not self._check_receipt_view_permission(request, payment):
            return Response({"detail": "Not allowed."}, status=status.HTTP_403_FORBIDDEN)
        if payment.status != FeePayment.Status.SUCCESS:
            return Response(
                {"detail": "A receipt is only available for a successful payment."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        invoice = payment.invoice
        try:
            pdf = render_pdf(
                receipt_no=f"RCPT-{payment.id}",
                campus_name=invoice.enrollment.section.school_class.campus.name,
                campus_type=invoice.enrollment.section.school_class.campus.type,
                student_name=_receipt_display_name(invoice.enrollment.student),
                fee_title=invoice.fee_structure.title,
                invoice_id=str(invoice.id),
                amount=payment.amount,
                amount_due=invoice.amount_due,
                amount_paid_total=invoice.amount_paid,
                payment_mode=payment.payment_mode,
                payer_role=payment.payer_role,
                paid_by=payment.paid_by,
                recorded_by=payment.recorded_by,
                status=payment.status,
                notes=payment.notes,
                paid_at=payment.paid_at or payment.created_at,
                transaction_id=payment.gateway_payment_id,
                gateway_method=payment.gateway_method,
            )
        except PdfUnavailable as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_501_NOT_IMPLEMENTED)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="fee-receipt-{payment.id}.pdf"'
        return response


class CampusAnalyticsSnapshotViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """Read-only — rows are written exclusively by the
    `campus-refresh-analytics-snapshot` Celery task (design doc §8),
    never from a request."""
    serializer_class = CampusAnalyticsSnapshotSerializer
    campus_field_path = "campus"
    member_scope = "none"
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]

    def get_queryset(self):
        return self.filter_queryset_to_my_campuses(CampusAnalyticsSnapshot.objects.all(), self.request)

    @action(detail=False, methods=["get"])
    def latest(self, request):
        campus_id = request.query_params.get("campus")
        if not campus_id:
            return Response({"detail": "campus query param is required."}, status=status.HTTP_400_BAD_REQUEST)
        snapshot = self.get_queryset().filter(campus_id=campus_id).order_by("-computed_at").first()
        if not snapshot:
            # T0.5 — "abhi snapshot nahi bana" error nahi, ek normal empty state hai
            # (naya campus / Celery task abhi chala nahi). Pehle 404 => client/logs me noise.
            # Ab 200 {empty: true}, aur ek best-effort refresh job queue ho jaata hai
            # (5 min me ek baar per campus) taaki agli baar data mile.
            return Response({
                "empty": True,
                "campus": str(campus_id),
                "computed_at": None,
                "data": {},
                "refresh_queued": self._queue_snapshot_refresh(request, campus_id),
            })
        return Response(self.get_serializer(snapshot).data)

    def _queue_snapshot_refresh(self, request, campus_id):
        from django.core.cache import cache

        from .models import AcademicSession
        from .tasks import refresh_analytics_snapshot

        gate = f"campus_snapshot_refresh_queued:{campus_id}"
        try:
            if not cache.add(gate, 1, 300):
                return False  # abhi-abhi queue hua tha
            # Scope check: sirf apne member-campus ke session pe job (same visibility rules).
            session = (
                self.filter_queryset_to_my_campuses(AcademicSession.objects.all(), request)
                .filter(campus_id=campus_id, is_current=True)
                .first()
            )
            if session is None:
                cache.delete(gate)
                return False
            refresh_analytics_snapshot.delay(str(campus_id), str(session.id))
            return True
        except Exception:  # broker down etc. — read endpoint kabhi fail nahi hona chahiye
            logger.warning("analytics snapshot refresh could not be queued campus=%s", campus_id, exc_info=True)
            return False


class CampusParentLinkViewSet(CampusMemberScopedMixin, viewsets.ReadOnlyModelViewSet):
    """
    Read-only — the only write path is `ParentLinkVerifyView` below,
    which requires a verified `message.ParentAccessCode`/token (via
    `bridge.resolve_parent_from_token`), never a plain POST here (see
    `CampusParentLink`'s model docstring).
    """
    serializer_class = CampusParentLinkSerializer
    campus_field_path = "campus"
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(CampusParentLink.objects.all(), self.request)
        user = self.request.user
        qs = qs.filter(Q(parent=user) | Q(student=user) | Q(campus__staff_profiles__user=user, campus__staff_profiles__is_active=True)).distinct()
        # [PERF] student_detail / parent_detail joined; active_enrollment prefetched (was 3 queries per link).
        return qs.select_related("student", "parent").prefetch_related(Prefetch(
            "student__campus_enrollments",
            queryset=StudentEnrollment.objects.filter(status=StudentEnrollment.Status.ACTIVE)
            .select_related("section__school_class").order_by("-session__start_date"),
            to_attr="_active_enrollments_prefetched",
        ))


class ParentLinkVerifyView(APIView):
    """
    `POST {"campus": <id>, "token": "<parent access code>"}`.

    🔧 FIX (this pass) — this view used to call
    `bridge.resolve_parent_from_token(token)` and unpack the result as
    `parent_user, student_user = ...`. `resolve_parent_from_token()`
    actually returns a `ParentTokenResolution` object with `.student` /
    `.parent_access_code` attributes (Parent Mode is deliberately
    loginless — see `message.models.ParentAccessCode`'s own docstring; it
    has no `.parent` user concept at all) and is not a 2-tuple, so that
    unpack raised a `ValueError`/`TypeError` on every single real call —
    this endpoint 500'd unconditionally. See campus/parent_invite.py's
    module docstring for the original bug note; this view is now fixed
    to resolve directly against `message.ParentAccessCode` instead,
    exactly the way the newer `CampusParentLinkConfirmView` below already
    does, rather than going through the mismatched bridge call. Logic is
    intentionally identical to `CampusParentLinkConfirmView.post()` — kept
    as a separate endpoint only so any existing caller of this URL/name
    (e.g. `CampusService.verifyParentLink()` in the Flutter app) keeps
    working without needing to switch call sites.
    """
    permission_classes = [IsAuthenticated]
    # B-4 fix — see CampusParentLinkVerifyThrottle's docstring in
    # throttles.py: this whole view is a single POST action, so a
    # class-level throttle_classes (no get_throttles() override needed)
    # is enough, unlike NoticeViewSet/FeePaymentViewSet/
    # CampusLiveSessionViewSet above which each have other, unrelated
    # actions that should keep the project-wide default.
    throttle_classes = [CampusParentLinkVerifyThrottle]

    def post(self, request):
        # Accepts either "token" (this endpoint's original, pre-existing
        # param name) or "code" (the name the rest of the parent-invite
        # feature uses) so old and new callers both work unchanged.
        code = (request.data.get("token") or request.data.get("code") or "").strip()
        campus_id = request.data.get("campus")
        if not code or not campus_id:
            return Response({"detail": "campus and token are required."}, status=status.HTTP_400_BAD_REQUEST)

        campus = Campus.objects.filter(pk=campus_id).first()
        if not campus:
            return Response({"detail": "Campus not found."}, status=status.HTTP_404_NOT_FOUND)
        # G-2 fix — linking a parent is another "pull someone else into
        # this campus" action (see `StaffProfileViewSet`'s docstring for
        # the shared reasoning).
        if not is_campus_approved(campus.id):
            return Response(
                {"detail": "This campus is pending platform verification and can't link parents yet."},
                status=status.HTTP_403_FORBIDDEN,
            )

        access_code = ParentAccessCode.objects.filter(code=code, is_active=True).select_related("student").first()
        if not access_code or access_code.is_expired:
            return Response({"detail": "Invalid or expired token."}, status=status.HTTP_400_BAD_REQUEST)

        student = access_code.student
        if not StudentEnrollment.objects.filter(
            student=student, status=StudentEnrollment.Status.ACTIVE, section__school_class__campus_id=campus.id,
        ).exists():
            return Response(
                {"detail": "This student isn't enrolled at this campus."}, status=status.HTTP_400_BAD_REQUEST,
            )

        link, created = CampusParentLink.objects.get_or_create(
            campus=campus, student=student, parent=request.user,
        )
        return Response(
            CampusParentLinkSerializer(link).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# ============================================================
# T4 §E/§G — "my classes" (student/parent subject-class cards)
# ============================================================
class MyClassesView(APIView):
    """
    `GET /campus/my/classes/[?campus=<id>][&student=<id>]`

    One card per SUBJECT-CLASS — an approved `(section, subject, teacher)`
    — of the caller's own / linked child's CURRENT section(s). Each card:
    subject, teacher, `mode` (that of the next session; `offline` when
    there is none), `next_session` (earliest upcoming live session or
    weekly slot, with the §G rule applied: an OFFLINE one is returned as
    `{"mode":"offline","time_hidden":true,"label":"Offline class"}` and
    NO time/day), `next_online_session` (earliest ONLINE one, with its
    time) and small counts (`notices`, `doubts_open`). Assignments /
    tests / doubts lists use the existing endpoints filtered by the
    card's `section` + `subject`.

    Staff-only callers get an empty list (this is the student's view).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from datetime import timedelta

        from .class_schedule import OFFLINE_LABEL, next_occurrence

        vis = get_visibility(request.user, request)
        section_ids = set(vis.visible_section_ids)
        campus_id = request.query_params.get("campus")
        if campus_id:
            section_ids = {s for s in section_ids if str(vis.section_campus.get(s)) == str(campus_id)}
        student_id = request.query_params.get("student")
        if student_id:
            section_ids &= set(
                StudentEnrollment.objects.filter(
                    student_id=student_id, status=StudentEnrollment.Status.ACTIVE, section_id__in=section_ids
                ).values_list("section_id", flat=True)
            ) if str(student_id) in {str(s) for s in vis.visible_student_ids} else set()
        if not section_ids:
            return Response([])

        now = timezone.now()
        assignments = list(
            SubjectTeacherassigments.objects.filter(
                section_id__in=section_ids, status=SubjectTeacherassigments.Status.APPROVED
            ).select_related("subject", "staff__user", "section__school_class")
            .order_by("section_id", "subject__name")
        )

        # candidate next sessions per (section, subject): (start, kind, mode, extra)
        cands = {}
        for live in CampusLiveSession.objects.filter(
            section_id__in=section_ids,
            status__in=[CampusLiveSession.Status.SCHEDULED, CampusLiveSession.Status.LIVE],
            scheduled_at__gte=now - timedelta(hours=3),
        ).order_by("scheduled_at"):
            cands.setdefault((live.section_id, live.subject_id), []).append(
                (live.scheduled_at, "live_session", live.mode, {"live_session_id": str(live.id), "status": live.status})
            )
        for entry in TimetableEntry.objects.filter(section_id__in=section_ids).select_related("time_slot", "session"):
            start = next_occurrence(entry, now)
            if start:
                cands.setdefault((entry.section_id, entry.subject_id), []).append(
                    (start, "timetable", entry.mode, {"timetable_entry_id": str(entry.id)})
                )

        # Substitute teacher: active covers for the coming week, keyed by
        # (timetable entry, date), so a card can say "Substitute: X" for the
        # next timetable period. Names only — never the absence reason.
        from datetime import timedelta as _td

        from . import substitutes as _sub_svc

        today = timezone.localdate()
        subs_by_key = {}
        for s in SubstituteAssignment.objects.filter(
            entry__section_id__in=section_ids,
            status=SubstituteAssignment.Status.ACTIVE,
            date__gte=today,
            date__lte=today + _td(days=8),
        ).select_related("substitute__user"):
            subs_by_key[(str(s.entry_id), s.date)] = s

        open_counts = {}
        for sec, sub in CampusDoubt.objects.filter(
            section_id__in=section_ids, is_active=True, author_id__in=vis.visible_student_ids
        ).exclude(status=CampusDoubt.Status.RESOLVED).values_list("section_id", "subject_id"):
            open_counts[(sec, sub)] = open_counts.get((sec, sub), 0) + 1
        notice_counts = dict(
            Notice.objects.filter(section_id__in=section_ids).values_list("section_id").annotate(n=Count("id"))
        )

        def render(c, hide_offline=True):
            start, kind, mode, extra = c
            hidden = mode == ClassMode.OFFLINE
            out = {"kind": kind, "mode": mode, "time_hidden": hidden,
                   "label": OFFLINE_LABEL if hidden else None,
                   "starts_at": None if hidden else start.isoformat()}
            if not hidden:
                out.update(extra)
            return out

        cards = []
        for a in assignments:
            options = sorted(cands.get((a.section_id, a.subject_id), []), key=lambda c: c[0])
            nxt = options[0] if options else None
            nxt_online = next((c for c in options if c[2] == ClassMode.ONLINE), None)
            t_user = a.staff.user
            substitute = None
            if nxt and nxt[1] == "timetable":
                key = (nxt[3]["timetable_entry_id"], timezone.localtime(nxt[0]).date())
                sub_row = subs_by_key.get(key)
                if sub_row is not None:
                    substitute = {
                        "staff_id": str(sub_row.substitute_id),
                        "name": _sub_svc.display_name(sub_row.substitute.user),
                        "date": sub_row.date.isoformat(),
                    }
            cards.append({
                "id": str(a.id),
                "campus": str(vis.section_campus.get(a.section_id)),
                "section": str(a.section_id),
                "section_name": a.section.name,
                "class_name": a.section.school_class.name,
                "subject": str(a.subject_id),
                "subject_name": a.subject.name,
                "teacher": {
                    "staff_id": str(a.staff_id), "user_id": t_user.id,
                    "name": (f"{t_user.first_name} {t_user.last_name}".strip() or t_user.username),
                },
                "substitute": substitute,
                "mode": nxt[2] if nxt else ClassMode.OFFLINE,
                "next_session": render(nxt) if nxt else None,
                "next_online_session": render(nxt_online) if nxt_online else None,
                "counts": {
                    "notices": notice_counts.get(a.section_id, 0),
                    "doubts_open": open_counts.get((a.section_id, a.subject_id), 0),
                },
            })
        return Response(cards)


# ============================================================
# T4 §F — doubts
# ============================================================
class CampusDoubtViewSet(viewsets.ModelViewSet):
    """
    Doubts inside one subject-class (decision D4 — private by default).

    Who sees a doubt: its author; a linked parent (child's doubts); that
    subject's APPROVED teacher(s) and the section's class-teacher;
    campus admin/principal; and — only when the campus enabled
    `doubts_public_allowed` AND the doubt is `is_public` — the other
    students of that section (read-only). Everyone else gets 404.

    Only an enrolled student of the section can POST a doubt; only the
    author or that class's staff can reply. A staff reply flips the doubt
    to `answered`, the author's follow-up flips it back to `open`.
    Delete = soft delete (`is_active=False`).
    """

    serializer_class = CampusDoubtSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_serializer_class(self):
        if self.action == "retrieve":
            return CampusDoubtDetailSerializer
        return CampusDoubtSerializer

    def get_throttles(self):
        if self.action in ("create", "reply"):
            return [CampusDoubtPostThrottle()]
        return super().get_throttles()

    # ---- helpers
    def _is_class_staff(self, doubt):
        return can_manage_section_subject(
            self.request.user, doubt.campus_id, doubt.section_id, doubt.subject_id
        )

    def get_queryset(self):
        from django.db.models import Exists, OuterRef

        user = self.request.user
        vis = get_visibility(user, self.request)
        qs = (
            CampusDoubt.objects.filter(is_active=True, campus_id__in=vis.member_campus_ids)
            .select_related("author", "subject", "campus")
            .annotate(
                replies_count=Count("replies", filter=Q(replies__is_active=True), distinct=True),
                _teaches=Exists(
                    SubjectTeacherassigments.objects.filter(
                        section_id=OuterRef("section_id"), subject_id=OuterRef("subject_id"),
                        staff__user=user, staff__is_active=True,
                        status=SubjectTeacherassigments.Status.APPROVED,
                    )
                ),
            )
        )
        admin_campuses = StaffProfile.objects.filter(
            user=user, is_active=True,
            role__in=[StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD],
        ).values_list("campus_id", flat=True)
        class_teacher_sections = ClassTeacherassigments.objects.filter(
            staff__user=user, staff__is_active=True
        ).values_list("section_id", flat=True)
        q = (
            Q(author=user)
            | Q(author_id__in=vis.visible_student_ids, campus_id__in=vis.nonstaff_campus_ids)
            | Q(campus_id__in=admin_campuses)
            | Q(section_id__in=class_teacher_sections)
            | Q(_teaches=True)
            | Q(is_public=True, campus__doubts_public_allowed=True, section_id__in=vis.visible_section_ids)
        )
        qs = qs.filter(q)
        # Block: another student's doubt/answer is hidden from me when we block each other
        # (my own doubts, and teacher/admin visibility, are never affected).
        from user_profile.services import blocked_user_ids

        _hidden = blocked_user_ids(user)
        if _hidden:
            qs = qs.exclude(Q(author_id__in=_hidden) & ~Q(author=user) & Q(_teaches=False))
        p = self.request.query_params
        for key in ("section", "subject", "status", "campus"):
            if p.get(key):
                qs = qs.filter(**{f"{key}_id" if key != "status" else "status": p[key]})
        if p.get("mine") in ("1", "true", "True"):
            qs = qs.filter(author=user)
        if self.action == "retrieve":
            qs = qs.prefetch_related(Prefetch("replies", queryset=CampusDoubtReply.objects.select_related("author")))
        return qs

    # ---- create
    @transaction.atomic
    def perform_create(self, serializer):
        user = self.request.user
        section = serializer.validated_data["section"]
        subject = serializer.validated_data["subject"]
        campus = section.school_class.campus
        if not campus.doubts_enabled:
            raise PermissionDenied("Doubts are turned off for this campus.")
        vis = get_visibility(user, self.request)
        enrollment = StudentEnrollment.objects.filter(
            student=user, section=section, status=StudentEnrollment.Status.ACTIVE,
            section_id__in=vis.own_section_ids,
        ).select_related("session").first()
        if not enrollment:
            raise PermissionDenied("Only a student enrolled in this section can post a doubt.")
        teacher_staff = list(
            SubjectTeacherassigments.objects.filter(
                section=section, subject=subject, status=SubjectTeacherassigments.Status.APPROVED
            ).select_related("staff__user")
        )
        if not teacher_staff:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"subject": "This subject has no approved teacher in your section."})
        is_public = bool(serializer.validated_data.get("is_public"))
        if is_public and not campus.doubts_public_allowed:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"is_public": "Public doubts are not enabled for this campus."})
        doubt = serializer.save(author=user, campus=campus, session=enrollment.session)
        recipients = {a.staff.user_id for a in teacher_staff if a.staff.is_active}
        recipients.discard(user.id)
        bridge.notify(
            users=list(recipients),
            notif_type=NotifTypes.CAMPUS_DOUBT_POSTED,
            title=f"New doubt: {subject.name}",
            body=(doubt.text[:120]),
            data={"type": NotifTypes.CAMPUS_DOUBT_POSTED, "doubt_id": str(doubt.id),
                  "section_id": str(section.id), "subject_id": str(subject.id)},
        )

    # ---- edit / delete (author, while still unanswered)
    def partial_update(self, request, *args, **kwargs):
        doubt = self.get_object()
        if doubt.author_id != request.user.id:
            raise PermissionDenied("Only the author can edit a doubt.")
        if doubt.status != CampusDoubt.Status.OPEN or doubt.replies.filter(is_active=True).exists():
            return Response({"detail": "A doubt can't be edited once it has a reply."}, status=400)
        ser = self.get_serializer(doubt, data={k: v for k, v in request.data.items() if k in ("text", "attachment", "is_public")}, partial=True)
        ser.is_valid(raise_exception=True)
        if ser.validated_data.get("is_public") and not doubt.campus.doubts_public_allowed:
            return Response({"is_public": "Public doubts are not enabled for this campus."}, status=400)
        ser.save()
        return Response(ser.data)

    def perform_destroy(self, instance):
        if instance.author_id != self.request.user.id and not self._is_class_staff(instance):
            raise PermissionDenied("Only the author or that class's teacher can delete a doubt.")
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    # ---- where can I ask? (teacher + class group)
    @action(detail=False, methods=["get"], url_path="targets")
    def targets(self, request):
        """`GET /doubts/targets/?section=<id>[&subject=<id>]` — everything the Doubts screen
        needs to offer BOTH ways of asking for one subject-class:

          * the TEACHER (a private campus doubt: `POST /doubts/`) — `can_ask_teacher`,
            `teachers` (the approved teacher(s) of the subject, or of every subject when
            no `subject` is given);
          * the CLASS GROUP (the section's chat group; its doubt queue goes through the
            doubt hub: `POST /doubt-hub/ask/` with `source=classroom_chat` and
            `target_id=group_id`, the list with `source=classroom_chat&parent_id=group_id`)
            — `group_id` is null when the section has no working group yet.

        `can_answer` is true for the subject's teacher / the class teacher / campus
        admin (with `subject`). Any member of the section's campus may call it; a
        section the caller can't see is a 404."""
        from django.core.exceptions import ValidationError as DjangoValidationError
        from rest_framework.exceptions import ValidationError

        section_id = request.query_params.get("section")
        if not section_id:
            raise ValidationError({"section": "A section is required."})
        try:
            section = Section.objects.select_related("school_class__campus").filter(pk=section_id).first()
        except (ValueError, TypeError, DjangoValidationError):
            raise ValidationError({"section": "Invalid id."})
        if section is None:
            return Response({"detail": "Section not found."}, status=404)
        campus = section.school_class.campus
        vis = get_visibility(request.user, request)
        if campus.id not in vis.staff_campus_ids and section.id not in vis.visible_section_ids:
            return Response({"detail": "Section not found."}, status=404)

        subject_id = request.query_params.get("subject") or None
        approved = SubjectTeacherassigments.objects.filter(
            section=section, status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True
        ).select_related("staff__user", "subject")
        if subject_id:
            try:
                approved = approved.filter(subject_id=subject_id)
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"subject": "Invalid id."})
        teachers = []
        for row in approved.order_by("subject__name")[:100]:
            user = row.staff.user
            teachers.append({
                "subject": str(row.subject_id),
                "subject_name": row.subject.name,
                "user_id": user.id,
                "name": (f"{user.first_name} {user.last_name}").strip() or user.username,
            })

        is_student = StudentEnrollment.objects.filter(
            student=request.user, section=section, status=StudentEnrollment.Status.ACTIVE
        ).exists()
        try:
            group_id = bridge.get_section_group_id(section)
        except Exception:  # a missing / broken group must never break the screen
            logger.exception("Could not resolve the chat group of section %s.", section.pk)
            group_id = None
        can_answer = bool(
            subject_id and can_manage_section_subject(request.user, campus.id, section.id, subject_id)
        )
        return Response({
            "section": str(section.id),
            "campus": str(campus.id),
            "subject": str(subject_id) if subject_id else None,
            "doubts_enabled": campus.doubts_enabled,
            "doubts_public_allowed": campus.doubts_public_allowed,
            "can_ask_teacher": bool(is_student and campus.doubts_enabled and teachers),
            "teachers": teachers,
            "group_id": group_id,
            "can_ask_group": bool(is_student and group_id),
            "can_answer": can_answer,
        })

    # ---- actions
    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        doubt = self.get_object()
        if not doubt.campus.doubts_enabled:
            raise PermissionDenied("Doubts are turned off for this campus.")
        is_author = doubt.author_id == request.user.id
        is_staff = self._is_class_staff(doubt)
        if not (is_author or is_staff):
            raise PermissionDenied("Only the author or that class's teacher can reply.")
        ser = CampusDoubtReplySerializer(data=request.data, context=self.get_serializer_context())
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            reply = ser.save(doubt=doubt, author=request.user, is_staff_reply=is_staff and not is_author)
            if reply.is_staff_reply and doubt.status == CampusDoubt.Status.OPEN:
                doubt.status = CampusDoubt.Status.ANSWERED
            elif is_author and doubt.status in (CampusDoubt.Status.ANSWERED, CampusDoubt.Status.RESOLVED):
                doubt.status = CampusDoubt.Status.OPEN
                doubt.resolved_by, doubt.resolved_at = None, None
            doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        if reply.is_staff_reply:
            recipients = [doubt.author_id]
        else:
            recipients = list(
                SubjectTeacherassigments.objects.filter(
                    section_id=doubt.section_id, subject_id=doubt.subject_id,
                    status=SubjectTeacherassigments.Status.APPROVED, staff__is_active=True,
                ).values_list("staff__user_id", flat=True)
            )
        recipients = [u for u in set(recipients) if u != request.user.id]
        bridge.notify(
            users=recipients,
            notif_type=NotifTypes.CAMPUS_DOUBT_REPLIED,
            title=f"Reply on your doubt: {doubt.subject.name}" if reply.is_staff_reply else f"Student replied: {doubt.subject.name}",
            body=reply.text[:120],
            data={"type": NotifTypes.CAMPUS_DOUBT_REPLIED, "doubt_id": str(doubt.id)},
        )
        return Response(CampusDoubtReplySerializer(reply, context=self.get_serializer_context()).data, status=201)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can resolve a doubt.")
        doubt.status = CampusDoubt.Status.RESOLVED
        doubt.resolved_by, doubt.resolved_at = request.user, timezone.now()
        doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can reopen a doubt.")
        doubt.status = CampusDoubt.Status.OPEN
        doubt.resolved_by, doubt.resolved_at = None, None
        doubt.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], url_path="set-public")
    def set_public(self, request, pk=None):
        doubt = self.get_object()
        if doubt.author_id != request.user.id and not self._is_class_staff(doubt):
            raise PermissionDenied("Only the author or that class's teacher can change this.")
        if not doubt.campus.doubts_public_allowed:
            return Response({"detail": "Public doubts are not enabled for this campus."}, status=400)
        doubt.is_public = bool(request.data.get("is_public", True))
        doubt.save(update_fields=["is_public", "updated_at"])
        return Response(CampusDoubtSerializer(doubt, context=self.get_serializer_context()).data)
