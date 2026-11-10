# campus/views/campus_testseries.py — split out of the old campus/views.py (P-C4). Code is moved verbatim.

from decimal import InvalidOperation
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework.response import Response
from ..models import (
    Section,
    StaffProfile,
)
from testseries.models import (
    TestAttempt,
    TestSeries,
)
from .. import bridge
from ..permissions import can_manage_section_subject
from django.shortcuts import get_object_or_404
from rest_framework import (
    status,
    viewsets,
)
from django.db import transaction
from .campus_assignments import _my_section_ids_and_campus_map


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
