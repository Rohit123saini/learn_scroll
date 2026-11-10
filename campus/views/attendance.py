# campus/views/attendance.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    Attendance,
    AttendanceEditLog,
    Section,
    StudentEnrollment,
    Subject,
)
from ..serializers import AttendanceSerializer
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsSectionSubjectStaffOrReadOnly,
    can_manage_section_subject,
    is_any_active_staff,
    is_linked_parent_of_student,
)
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.decorators import action
from .. import attendance_edit
from .. import bulk_ops
from django.core.cache import cache
from ..services import compute_attendance_summary
from datetime import date
from django.utils.dateparse import parse_date
from .. import progress as progress_data
from rest_framework import (
    status,
    viewsets,
)
from django.utils import timezone
from django.db import transaction
from ._common import (
    CampusMemberScopedMixin,
    _truthy,
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
            from ..attendance_push import notify_parents_of_absences

            notify_parents_of_absences(enrollment_ids=[enrollment_id], date=day, subject=subject)

        transaction.on_commit(_push)

    def _push_correction_if_no_longer_absent(self, instance, previous_status):
        """Absent -> anything else (or the row removed): parents who already got
        today's absent push are told it was corrected. Runs after commit."""
        if previous_status != Attendance.Status.ABSENT or instance.status == Attendance.Status.ABSENT:
            return

        def _push(enrollment_id=instance.enrollment_id, day=instance.date, subject=instance.subject):
            from ..attendance_push import notify_parents_of_corrections

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
                from ..attendance_push import notify_parents_of_corrections

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
