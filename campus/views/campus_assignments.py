# campus/views/campus_assignments.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from rest_framework.permissions import IsAuthenticated
from ..models import (
    Notice,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
)
from ..bridge import NotifTypes
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework.response import Response
from assignments.models import assigments as Unifiedassigments
from assignments.models import assigmentsSource as UnifiedassigmentsSource
from assignments.models import assigmentsSubmission as UnifiedassigmentsSubmission
from ..visibility import accessible_section_ids_and_campus_map
from .. import bridge
from .. import bulk_ops
from ..permissions import (
    can_manage_section_subject,
    is_any_active_staff,
)
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import (
    status,
    viewsets,
)
from django.db import transaction
from ._common import logger


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
