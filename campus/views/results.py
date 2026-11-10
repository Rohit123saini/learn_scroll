# campus/views/results.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from ..models import (
    AcademicSession,
    Campus,
    CampusParentLink,
    ExamTerm,
    ResultEntry,
    Section,
    StaffProfile,
    StudentEnrollment,
)
from ..throttles import (
    CampusResultsImportThrottle,
    CampusResultsPublishThrottle,
)
from decimal import (
    Decimal,
    InvalidOperation,
)
from ..serializers import (
    ExamTermSerializer,
    ResultEntrySerializer,
)
from rest_framework.parsers import (
    FormParser,
    JSONParser,
    MultiPartParser,
)
from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from ..permissions import (
    IsCampusAdminOrPrincipal,
    IsSectionSubjectStaffOrReadOnly,
    can_manage_section_subject,
    is_any_active_staff,
    is_linked_parent_of_student,
)
from ..bridge import NotifTypes
from ..receipt_pdf import PdfUnavailable
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework.response import Response
from ..soft_delete_views import SoftDeleteViewSetMixin
from ..receipt_pdf import _display_name as _receipt_display_name
from rest_framework.decorators import action
from .. import bridge
from .. import bulk_ops
from .. import tasks as campus_tasks
from ..services import (
    compute_attendance_summary,
    generate_report_card_data,
)
from core.async_utils import dispatch_after_commit
from ..visibility import get_visibility
from .. import grading
from ..audit import log_action
from .. import progress as progress_data
from ..report_card_pdf import render_report_card_pdf
from .. import results_csv
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
