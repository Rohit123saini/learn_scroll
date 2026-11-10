# campus/staff_leave_views.py
"""
STAFF LEAVE API  —  `/campus/staff-leaves/`   (rules: `campus.staff_leave`)

    GET   staff-leaves/                 list   (?campus= ?status=pending|approved|rejected|cancelled
                                                 ?staff= ?mine=1 ?date_from= ?date_to=)
    POST  staff-leaves/                 apply  {campus?, leave_type, date_from, date_to?, is_half_day?,
                                                half_day_part?, reason?, staff?}
                                        `staff` = StaffProfile id: admin / principal records leave FOR that
                                        person (saved as approved). A campus's only admin / principal gets
                                        their own leave approved automatically.
    GET   staff-leaves/{id}/            retrieve
    POST  staff-leaves/{id}/edit/       applicant, PENDING only; send only what changes
    POST  staff-leaves/{id}/approve/    {note?}            admin / principal (not on their own leave)
    POST  staff-leaves/{id}/reject/     {note}  (required) admin / principal (not on their own leave)
    POST  staff-leaves/{id}/cancel/     applicant (pending, or approved-not-started) / admin-principal
    GET   staff-leaves/summary/         ?campus= &year= [&staff=]  days taken / pending per type
                                        (+ quota / remaining when the campus sets a quota)

Who sees what
  * Every active staff member: THEIR OWN leaves.
  * Admin / principal of a campus: every leave of that campus (incl. the reason).
  * Students / parents: nothing (404/empty) — leave is staff business.

Errors carry a stable machine `code` (`leave_overlap`, `cannot_decide_own_leave`,
`leave_not_pending`, ...) in the project's error envelope.

Approving records a `TeacherAbsence` (substitute-teacher flow); cancelling an
approved leave releases it. Push goes out after commit via Celery
(`campus.staff_leave_push`).
"""
import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import staff_leave as svc
from . import tasks as campus_tasks
from .audit import log_action
from .audit import AuditedModelViewSetMixin
from .soft_delete_views import SoftDeleteViewSetMixin
from .models import CampusHoliday, StaffLeave, StaffProfile
from .permissions import is_campus_admin_or_principal
from .serializers import CampusHolidaySerializer, StaffLeaveApplySerializer, StaffLeaveEditSerializer, StaffLeaveSerializer
from .substitutes import display_name
from .throttles import CampusStaffLeaveApplyThrottle
from .views import CampusMemberScopedMixin

logger = logging.getLogger(__name__)

_APPROVER_ROLES = (StaffProfile.Role.ADMIN, StaffProfile.Role.PRINCIPAL_HOD)


class StaffLeaveAPIError(APIException):
    """4xx with a stable machine `code` (e.g. `leave_overlap`) + human `detail`;
    the project's exception handler puts `machine_code` in the envelope's `code`."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code
        self.status_code = err.status


def _truthy(value):
    return str(value or "").lower() in ("1", "true", "yes", "on")


def _date_param(params, name):
    raw = params.get(name)
    if not raw:
        return None
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({name: "Use the format YYYY-MM-DD."})
    return value


class StaffLeaveViewSet(
    CampusMemberScopedMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = StaffLeaveSerializer
    campus_field_path = "campus"
    member_scope = "none"  # students / parents never see staff leave
    # Per-object rules (own vs admin) are enforced in the queryset + service.
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "head", "options"]

    def get_throttles(self):
        if self.action in ("create", "edit"):
            return [CampusStaffLeaveApplyThrottle()]
        return super().get_throttles()

    # ---- who am I here ------------------------------------------------------
    def _admin_campus_ids(self):
        cached = getattr(self, "_admin_ids", None)
        if cached is None:
            cached = set(
                StaffProfile.objects.filter(
                    user=self.request.user, is_active=True, role__in=_APPROVER_ROLES
                ).values_list("campus_id", flat=True)
            )
            self._admin_ids = cached
        return cached

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if getattr(self, "request", None) is not None and self.request.user.is_authenticated:
            ctx["admin_campus_ids"] = self._admin_campus_ids()
            ctx["sole_approver_campus_ids"] = self._sole_approver_campus_ids()
        return ctx

    def _sole_approver_campus_ids(self):
        """Campuses where the caller is an admin / principal and nobody else is
        (their own leave can then be decided by them)."""
        cached = getattr(self, "_sole_ids", None)
        if cached is None:
            cached = {c for c in self._admin_campus_ids() if not svc.other_approver_exists(c, self.request.user.pk)}
            self._sole_ids = cached
        return cached

    # ---- queryset -----------------------------------------------------------
    def get_queryset(self):
        qs = StaffLeave.objects.select_related("staff__user", "decided_by", "campus")
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        # Staff see their own; admin / principal see their whole campus.
        qs = qs.filter(Q(staff__user=self.request.user) | Q(campus_id__in=self._admin_campus_ids()))
        params = self.request.query_params
        if params.get("status"):
            state = params["status"].lower()
            if state not in StaffLeave.Status.values:
                raise ValidationError({"status": f"One of: {', '.join(StaffLeave.Status.values)}."})
            qs = qs.filter(status=state)
        if params.get("staff"):
            try:
                qs = qs.filter(staff_id=params["staff"])
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"staff": "Invalid id."})
        if _truthy(params.get("mine")):
            qs = qs.filter(staff__user=self.request.user)
        date_from, date_to = _date_param(params, "date_from"), _date_param(params, "date_to")
        if date_from:
            qs = qs.filter(date_to__gte=date_from)  # still running on/after date_from
        if date_to:
            qs = qs.filter(date_from__lte=date_to)
        return qs

    # ---- apply ----------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        ser = StaffLeaveApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            with transaction.atomic():
                leave = svc.apply_for_leave(
                    user=request.user,
                    campus_id=data.get("campus"),
                    leave_type=data["leave_type"],
                    date_from=data["date_from"],
                    date_to=data.get("date_to"),
                    is_half_day=data.get("is_half_day", False),
                    half_day_part=data.get("half_day_part", ""),
                    reason=data.get("reason", ""),
                    staff_id=data.get("staff"),
                )
                span = f"{leave.leave_type} {leave.date_from}..{leave.date_to}"
                if leave.applied_by_id:  # an admin recorded it for the staff member (approved at once)
                    log_action(
                        leave.campus_id, request.user, "staff_leave.apply_for", target=leave,
                        summary=f"leave recorded for {display_name(leave.staff.user)}: {span}",
                        absence=str(leave.absence_id) if leave.absence_id else None,
                    )
                    dispatch_after_commit(
                        campus_tasks.send_staff_leave_notifications, str(leave.pk), "recorded_by_admin"
                    )
                elif leave.status == StaffLeave.Status.APPROVED:  # the campus's only approver
                    log_action(
                        leave.campus_id, request.user, "staff_leave.auto_approve", target=leave,
                        summary=f"leave applied and auto-approved (no other approver): {span}",
                        absence=str(leave.absence_id) if leave.absence_id else None,
                    )
                else:
                    log_action(
                        leave.campus_id, request.user, "staff_leave.apply", target=leave,
                        summary=f"leave applied: {span}",
                    )
                    dispatch_after_commit(campus_tasks.send_staff_leave_notifications, str(leave.pk), "requested")
        except svc.StaffLeaveError as exc:
            raise StaffLeaveAPIError(exc)
        leave = self.get_queryset().get(pk=leave.pk)
        return Response(self.get_serializer(leave).data, status=status.HTTP_201_CREATED)

    # ---- edit (pending only) ----------------------------------------------------
    @action(detail=True, methods=["post"], url_path="edit")
    def edit(self, request, pk=None):
        leave = self.get_object()  # 404 for a leave the caller may not even see
        ser = StaffLeaveEditSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                leave = svc.edit_leave(leave_id=leave.pk, actor=request.user, **ser.validated_data)
                log_action(
                    leave.campus_id, request.user, "staff_leave.edit", target=leave,
                    summary=f"leave edited: {leave.leave_type} {leave.date_from}..{leave.date_to}",
                )
                dispatch_after_commit(campus_tasks.send_staff_leave_notifications, str(leave.pk), "edited")
        except svc.StaffLeaveError as exc:
            raise StaffLeaveAPIError(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=leave.pk)).data)

    # ---- decide -----------------------------------------------------------------
    def _decide(self, request, approve):
        leave = self.get_object()  # 404 for a leave the caller may not even see
        try:
            with transaction.atomic():
                leave = svc.decide(
                    leave_id=leave.pk, approver=request.user, approve=approve, note=request.data.get("note", "")
                )
                verb = "approve" if approve else "reject"
                log_action(
                    leave.campus_id, request.user, f"staff_leave.{verb}", target=leave,
                    summary=(
                        f"leave {verb}d: {display_name(leave.staff.user)} "
                        f"{leave.leave_type} {leave.date_from}..{leave.date_to}"
                    ),
                    absence=str(leave.absence_id) if leave.absence_id else None,
                )
                dispatch_after_commit(
                    campus_tasks.send_staff_leave_notifications, str(leave.pk), "approved" if approve else "rejected"
                )
        except svc.StaffLeaveError as exc:
            raise StaffLeaveAPIError(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=leave.pk)).data)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        return self._decide(request, True)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        return self._decide(request, False)

    # ---- cancel ---------------------------------------------------------------------
    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        leave = self.get_object()
        try:
            with transaction.atomic():
                leave, released, previous = svc.cancel(leave_id=leave.pk, actor=request.user)
                if previous != StaffLeave.Status.CANCELLED:  # a real change, not an idempotent repeat
                    log_action(
                        leave.campus_id, request.user, "staff_leave.cancel", target=leave,
                        summary=(
                            f"leave cancelled ({previous}): {display_name(leave.staff.user)} "
                            f"{leave.date_from}..{leave.date_to}; {len(released)} substitute(s) released"
                        ),
                    )
                    for sub in released:
                        dispatch_after_commit(campus_tasks.send_substitute_notifications, str(sub.pk), "cancelled")
                    by_owner = leave.staff.user_id == request.user.pk
                    if not by_owner:
                        dispatch_after_commit(
                            campus_tasks.send_staff_leave_notifications, str(leave.pk), "cancelled_by_admin"
                        )
                    elif previous == StaffLeave.Status.APPROVED:
                        dispatch_after_commit(
                            campus_tasks.send_staff_leave_notifications, str(leave.pk), "cancelled_by_staff"
                        )
        except svc.StaffLeaveError as exc:
            raise StaffLeaveAPIError(exc)
        data = self.get_serializer(self.get_queryset().get(pk=leave.pk)).data
        data["released_substitutes"] = len(released)
        return Response(data)

    # ---- summary ---------------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        """Days of leave per type for one calendar year. Own by default; an
        admin / principal may pass `staff=<id>` for someone at their campus.
        Admins also get `pending_for_approval` (how many applications wait)."""
        params = request.query_params
        try:
            year = int(params.get("year") or timezone.localdate().year)
            if not 2000 <= year <= 2100:
                raise ValueError
        except (TypeError, ValueError):
            raise ValidationError({"year": "Use a 4-digit year."})
        try:
            if params.get("staff"):
                try:
                    staff = StaffProfile.objects.select_related("user", "campus").filter(pk=params["staff"], is_active=True).first()
                except (ValueError, TypeError, DjangoValidationError):
                    staff = None
                if staff is None:
                    raise svc.StaffLeaveError("not_found", "Staff member not found.", 404)
                if staff.user_id != request.user.pk and not is_campus_admin_or_principal(request.user, staff.campus_id):
                    raise PermissionDenied("Only a campus admin or principal can see someone else's leave summary.")
            else:
                staff = svc.applicant_profile(request.user, params.get("campus"))
        except svc.StaffLeaveError as exc:
            raise StaffLeaveAPIError(exc)
        out = svc.summary_for_staff(staff, year)
        out["staff"] = str(staff.pk)
        out["campus"] = str(staff.campus_id)
        if is_campus_admin_or_principal(request.user, staff.campus_id):
            out["pending_for_approval"] = StaffLeave.objects.filter(
                campus_id=staff.campus_id, status=StaffLeave.Status.PENDING
            ).exclude(staff__user=request.user).count()
        return Response(out)


class CampusHolidayViewSet(SoftDeleteViewSetMixin, AuditedModelViewSetMixin, CampusMemberScopedMixin, viewsets.ModelViewSet):
    """`/campus/holidays/` — days the campus is closed (they never count as leave days).

        GET     ?campus= ?year= | ?date_from= ?date_to=   anyone in the campus
        POST    {campus, date, name}                       admin / principal
        PATCH / DELETE                                      admin / principal

    Adding or removing a holiday only affects leave applied afterwards (a leave's
    day count is fixed when it is applied or edited).
    """

    audit_prefix = "holiday"
    serializer_class = CampusHolidaySerializer
    campus_field_path = "campus"

    def get_queryset(self):
        qs = self.filter_queryset_to_my_campuses(CampusHoliday.objects.all(), self.request)
        params = self.request.query_params
        if params.get("year"):
            try:
                qs = qs.filter(date__year=int(params["year"]))
            except (TypeError, ValueError):
                raise ValidationError({"year": "Use a 4-digit year."})
        date_from, date_to = _date_param(params, "date_from"), _date_param(params, "date_to")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return qs

    # The permission class checks the campus named in the request body; also make
    # sure the row being changed really belongs to a campus the caller runs.
    def perform_update(self, serializer):
        if not is_campus_admin_or_principal(self.request.user, serializer.instance.campus_id):
            raise PermissionDenied("Only a campus admin or principal can change holidays.")
        super().perform_update(serializer)

    def perform_destroy(self, instance):
        if not is_campus_admin_or_principal(self.request.user, instance.campus_id):
            raise PermissionDenied("Only a campus admin or principal can change holidays.")
        super().perform_destroy(instance)
