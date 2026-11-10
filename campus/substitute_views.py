# campus/substitute_views.py
"""
SUBSTITUTE TEACHER API

    Absences  — `/campus/teacher-absences/`
      GET    teacher-absences/                    list  (?campus= ?staff= ?status=active|cancelled|all ?date=YYYY-MM-DD ?upcoming=1)
      POST   teacher-absences/                    mark a teacher absent   {staff, date_from, date_to?, reason?}
      GET    teacher-absences/{id}/               retrieve (+ periods_total / covered / uncovered)
      GET    teacher-absences/{id}/periods/       every period that needs cover, with its substitute
      POST   teacher-absences/{id}/cancel/        the teacher is back — also cancels the cover made for it

    Substitutes — `/campus/substitutes/`
      GET    substitutes/                         list  (?section= ?substitute= ?absence= ?date= ?date_from= ?date_to=
                                                          ?status=active|cancelled|all ?mine=1 ?campus=)
      POST   substitutes/                         assign / replace  {entry, date, substitute, note?}
      POST   substitutes/{id}/cancel/             cancel one cover
      GET    substitutes/suggestions/?entry=&date=   who is free (best first)
      GET    substitutes/overlay/?section=&date_from=&date_to=
                                                  what to draw on the section's timetable
                                                  (readable by the section's students/parents too)

Who may do what
  * Mark / cancel an absence: campus admin or principal/HOD only.
  * Assign / replace / cancel a substitute: admin/principal anywhere, or the
    section's class-teacher / moderator for THEIR section
    (`can_manage_section_subject` with no subject).
  * Read absences + substitutes: active staff of the campus (the absence
    `reason` only for admin/principal and the teacher themself). Students and
    parents get ONLY the overlay — names + which period, never the reason.

Push: assigning fans out (Celery, after commit) to the substitute and the
section; cancelling tells the former substitute. See `campus.substitute_push`.
The rules live in `campus.substitutes`.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.async_utils import dispatch_after_commit

from . import substitutes as sub_svc
from . import tasks as campus_tasks
from .audit import log_action
from .models import (
    Section,
    StaffProfile,
    SubstituteAssignment,
    TeacherAbsence,
    TimetableEntry,
)
from .permissions import (
    IsCampusAdminOrPrincipal,
    IsSectionSubjectStaffOrReadOnly,
    can_manage_section_subject,
    is_campus_admin_or_principal,
)
from .substitute_serializers import SubstituteAssignmentSerializer, TeacherAbsenceSerializer
from .throttles import CampusSubstituteAssignThrottle
from .views import CampusMemberScopedMixin
from .visibility import get_visibility

logger = logging.getLogger(__name__)


class SubstituteAPIError(APIException):
    """400 with a stable machine `code` (e.g. `substitute_busy`) + human
    `detail`; the project's exception handler puts `machine_code` in the
    envelope's `code` so the app can translate it."""

    status_code = 400

    def __init__(self, err):
        super().__init__(detail=err.message)
        self.machine_code = err.code


def _date_param(request, name, *, required=False):
    raw = request.query_params.get(name)
    if not raw:
        if required:
            raise ValidationError({name: "This query parameter is required."})
        return None
    try:
        value = parse_date(raw)
    except ValueError:
        value = None
    if value is None:
        raise ValidationError({name: "Use the format YYYY-MM-DD."})
    return value


def _entry_or_none(entry_id):
    if not entry_id:
        return None
    try:
        return (
            TimetableEntry.objects.select_related(
                "time_slot", "session", "subject", "section__school_class", "staff__user"
            )
            .filter(pk=entry_id)
            .first()
        )
    except (ValueError, TypeError, DjangoValidationError):
        return None


def _week_bounds(today):
    monday = today - timedelta(days=today.isoweekday() - 1)
    return monday, monday + timedelta(days=6)


def _period_row(row):
    """JSON for one `periods_for_absence` row."""
    entry, day, sub = row["entry"], row["date"], row["substitution"]
    slot = entry.time_slot
    return {
        "entry": str(entry.pk),
        "date": day.isoformat(),
        "subject": str(entry.subject_id),
        "subject_name": entry.subject.name,
        "section": str(entry.section_id),
        "section_label": f"{entry.section.school_class.name} - {entry.section.name}",
        "start_time": slot.start_time.isoformat(),
        "end_time": slot.end_time.isoformat(),
        "slot_label": slot.label,
        "mode": entry.mode,
        "room": entry.room.name if entry.room_id else "",
        "covered": sub is not None,
        "substitution": (
            {
                "id": str(sub.pk),
                "substitute": str(sub.substitute_id),
                "substitute_name": sub_svc.display_name(sub.substitute.user),
                "note": sub.note,
            }
            if sub is not None
            else None
        ),
    }


# ============================================================
# Absences
# ============================================================
class TeacherAbsenceViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = TeacherAbsenceSerializer
    campus_field_path = "campus"
    member_scope = "none"  # staff only; students/parents never see absences
    permission_classes = [IsAuthenticated, IsCampusAdminOrPrincipal]
    # No PUT/PATCH/DELETE: an absence is created or cancelled. To extend leave,
    # add a second absence for the extra days.
    http_method_names = ["get", "post", "head", "options"]

    def get_throttles(self):
        if self.action == "create":
            return [CampusSubstituteAssignThrottle()]
        return super().get_throttles()

    def get_campus_id_for_permission_check(self, request):
        """Writes are judged by the campus of the STAFF member in the body (or
        of the stored row for detail actions), never by a client `campus`."""
        pk = self.kwargs.get("pk")
        if pk:
            try:
                obj = TeacherAbsence.objects.filter(pk=pk).values_list("campus_id", flat=True).first()
            except (ValueError, TypeError, DjangoValidationError):
                obj = None
            return obj
        staff_id = request.data.get("staff")
        if staff_id:
            try:
                return StaffProfile.objects.filter(pk=staff_id).values_list("campus_id", flat=True).first()
            except (ValueError, TypeError, DjangoValidationError):
                return None
        return None

    def get_queryset(self):
        qs = TeacherAbsence.objects.select_related("staff__user", "marked_by", "campus")
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        params = self.request.query_params
        if params.get("staff"):
            try:
                qs = qs.filter(staff_id=params["staff"])
            except (ValueError, TypeError, DjangoValidationError):
                raise ValidationError({"staff": "Invalid id."})
        # The "active only" default is a LIST convenience; detail routes
        # (retrieve / periods / cancel) must still resolve a cancelled row so
        # cancel stays idempotent instead of 404-ing on the second tap.
        state = (params.get("status") or ("active" if self.action == "list" else "all")).lower()
        if state == "active":
            qs = qs.filter(is_active=True)
        elif state == "cancelled":
            qs = qs.filter(is_active=False)
        day = _date_param(self.request, "date")
        if day:
            qs = qs.filter(date_from__lte=day, date_to__gte=day)
        if str(params.get("upcoming", "")).lower() in ("1", "true", "yes", "on"):
            qs = qs.filter(date_to__gte=timezone.localdate())
        return qs

    @transaction.atomic
    def perform_create(self, serializer):
        staff = serializer.validated_data["staff"]
        absence = serializer.save(campus_id=staff.campus_id, marked_by=self.request.user)
        log_action(
            absence.campus_id, self.request.user, "teacher_absence.create", target=absence,
            summary=f"teacher absent: {sub_svc.display_name(staff.user)} {absence.date_from}..{absence.date_to}",
        )

    @action(detail=True, methods=["get"], url_path="periods")
    def periods(self, request, pk=None):
        """Every period of the absent teacher inside the absence, soonest
        first, each with its current substitute (or `covered=false`)."""
        absence = self.get_object()
        if not is_campus_admin_or_principal(request.user, absence.campus_id):
            raise PermissionDenied("Only a campus admin/principal can manage substitutes.")
        try:
            rows = sub_svc.periods_for_absence(absence)
        except sub_svc.SubstituteError as exc:
            raise SubstituteAPIError(exc)
        items = [_period_row(r) for r in rows]
        return Response(
            {
                "absence": str(absence.pk),
                "total": len(items),
                "covered": sum(1 for i in items if i["covered"]),
                "uncovered": sum(1 for i in items if not i["covered"]),
                "periods": items,
            }
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """The teacher is back. Deactivates the absence and cancels the cover
        made for it (each former substitute is told). Idempotent."""
        absence = self.get_object()  # IsCampusAdminOrPrincipal already judged the stored campus
        with transaction.atomic():
            cancelled = sub_svc.cancel_absence(absence)
            for sub in cancelled:
                dispatch_after_commit(campus_tasks.send_substitute_notifications, str(sub.pk), "cancelled")
            if cancelled or not absence.is_active:
                log_action(
                    absence.campus_id, request.user, "teacher_absence.cancel", target=absence,
                    summary=f"absence cancelled ({len(cancelled)} substitute(s) released)",
                )
        absence.refresh_from_db()
        data = self.get_serializer(absence).data
        data["released_substitutes"] = len(cancelled)
        return Response(data)


# ============================================================
# Substitute assignments
# ============================================================
class SubstituteAssignmentViewSet(CampusMemberScopedMixin, viewsets.ModelViewSet):
    serializer_class = SubstituteAssignmentSerializer
    campus_field_path = "campus"
    member_scope = "none"  # the list is staff-only; students/parents use `overlay`
    permission_classes = [IsAuthenticated, IsSectionSubjectStaffOrReadOnly]
    # An assignment is created (or replaced by creating another) and cancelled;
    # never edited in place — that keeps the "who was told what" history honest.
    http_method_names = ["get", "post", "head", "options"]

    def get_throttles(self):
        if self.action == "create":
            return [CampusSubstituteAssignThrottle()]
        return super().get_throttles()

    # ---- permission input ------------------------------------------------------
    def get_section_subject_for_permission_check(self, request):
        """Judged by the STORED row for detail actions (cancel) and by the
        entry's own section for create — never by ids a client could point at a
        section it does manage. No subject: cover is a section-level duty
        (admin / principal / class-teacher / moderator)."""
        pk = self.kwargs.get("pk")
        if pk:
            try:
                row = (
                    SubstituteAssignment.objects.filter(pk=pk)
                    .values_list("campus_id", "entry__section_id")
                    .first()
                )
            except (ValueError, TypeError, DjangoValidationError):
                row = None
            if row:
                return row[0], row[1], None
            return None, None, None
        entry = _entry_or_none(request.data.get("entry"))
        if entry is None:
            return None, None, None
        return entry.section.school_class.campus_id, entry.section_id, None

    # ---- queryset ----------------------------------------------------------------
    def get_queryset(self):
        qs = SubstituteAssignment.objects.select_related(
            "substitute__user", "original_staff__user", "entry__time_slot", "entry__subject",
            "entry__section__school_class",
        )
        qs = self.filter_queryset_to_my_campuses(qs, self.request)
        params = self.request.query_params
        for key, lookup in (
            ("section", "entry__section_id"), ("substitute", "substitute_id"), ("absence", "absence_id"),
        ):
            if params.get(key):
                try:
                    qs = qs.filter(**{lookup: params[key]})
                except (ValueError, TypeError, DjangoValidationError):
                    raise ValidationError({key: "Invalid id."})
        state = (params.get("status") or ("active" if self.action == "list" else "all")).lower()
        if state in ("active", "cancelled"):
            qs = qs.filter(status=state)
        day = _date_param(self.request, "date")
        if day:
            qs = qs.filter(date=day)
        date_from, date_to = _date_param(self.request, "date_from"), _date_param(self.request, "date_to")
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        if str(params.get("mine", "")).lower() in ("1", "true", "yes", "on"):
            qs = qs.filter(substitute__user=self.request.user)
        return qs

    # ---- create = assign / replace ------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        vd = serializer.validated_data
        entry, substitute = vd["entry"], vd["substitute"]
        try:
            with transaction.atomic():
                result = sub_svc.assign_substitute(
                    entry=entry,
                    on_date=vd["date"],
                    substitute=substitute,
                    assigned_by=request.user,
                    note=vd.get("note", ""),
                )
                if result.created:
                    if result.replaced is not None:
                        dispatch_after_commit(
                            campus_tasks.send_substitute_notifications, str(result.replaced.pk), "cancelled"
                        )
                    dispatch_after_commit(
                        campus_tasks.send_substitute_notifications, str(result.assignment.pk), "assigned"
                    )
                    log_action(
                        result.assignment.campus_id, request.user, "substitute.assign", target=result.assignment,
                        summary=(
                            f"substitute: {sub_svc.display_name(substitute.user)} covers "
                            f"{entry.subject.name} {entry.section_id} on {vd['date']}"
                        ),
                        replaced=str(result.replaced.pk) if result.replaced is not None else "",
                    )
        except sub_svc.SubstituteError as exc:
            raise SubstituteAPIError(exc)

        assignment = self.get_queryset().filter(pk=result.assignment.pk).first() or result.assignment
        data = self.get_serializer(assignment).data
        return Response(data, status=status.HTTP_201_CREATED if result.created else status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        """Cancel one cover (the former substitute is told). Idempotent: a
        second call on an already-cancelled row changes and sends nothing."""
        assignment = self.get_object()
        with transaction.atomic():
            changed = sub_svc.cancel_substitution(assignment)
            if changed:
                dispatch_after_commit(campus_tasks.send_substitute_notifications, str(assignment.pk), "cancelled")
                log_action(
                    assignment.campus_id, request.user, "substitute.cancel", target=assignment,
                    summary=f"substitute cancelled for {assignment.date}",
                )
        assignment.refresh_from_db()
        return Response(self.get_serializer(assignment).data)

    # ---- who is free? ---------------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="suggestions")
    def suggestions(self, request):
        """Candidates for ONE period on ONE date, best first. Needs the same
        right as assigning (it exposes colleagues' availability)."""
        entry = _entry_or_none(request.query_params.get("entry"))
        day = _date_param(request, "date", required=True)
        if entry is None:
            return Response({"detail": "Period not found."}, status=status.HTTP_404_NOT_FOUND)
        campus_id = entry.section.school_class.campus_id
        if not can_manage_section_subject(request.user, campus_id, entry.section_id, None):
            raise PermissionDenied("You can't manage substitutes for this section.")
        if day.isoweekday() != entry.time_slot.day_of_week:
            raise SubstituteAPIError(sub_svc.SubstituteError("wrong_weekday", "That date isn't a day this period runs on."))
        return Response(
            {
                "entry": str(entry.pk),
                "date": day.isoformat(),
                "candidates": sub_svc.suggest_substitutes(entry, day),
            }
        )

    # ---- timetable overlay -----------------------------------------------------------
    @action(detail=False, methods=["get"], url_path="overlay")
    def overlay(self, request):
        """What to draw on a section's weekly timetable between two dates
        (default: this Monday-Sunday): `substituted` (who) and `uncovered`
        (teacher absent, nobody assigned). Readable by anyone who can see the
        section — staff of the campus, its students, a linked parent — and
        deliberately free of the absence reason."""
        section_id = request.query_params.get("section")
        if not section_id:
            raise ValidationError({"section": "This query parameter is required."})
        try:
            section = Section.objects.select_related("school_class").filter(pk=section_id).first()
        except (ValueError, TypeError, DjangoValidationError):
            section = None
        if section is None:
            return Response({"detail": "Section not found."}, status=status.HTTP_404_NOT_FOUND)

        campus_id = section.school_class.campus_id
        vis = get_visibility(request.user, request)
        if not (vis.is_staff_in(campus_id) or section.pk in vis.visible_section_ids):
            # same posture as every other section-scoped endpoint: not yours -> 404
            return Response({"detail": "Section not found."}, status=status.HTTP_404_NOT_FOUND)

        date_from = _date_param(request, "date_from")
        date_to = _date_param(request, "date_to")
        if date_from is None and date_to is None:
            date_from, date_to = _week_bounds(timezone.localdate())
        elif date_from is None:
            date_from = date_to
        elif date_to is None:
            date_to = date_from
        try:
            items = sub_svc.overlay_for_section(section.pk, date_from, date_to)
        except sub_svc.SubstituteError as exc:
            raise SubstituteAPIError(exc)
        return Response({"section": str(section.pk), "date_from": date_from.isoformat(),
                         "date_to": date_to.isoformat(), "items": items})
