# assignment/views.py
"""
§7 permissions and endpoint shapes, wired up as DRF viewsets. Every
action here delegates the actual write to a model method
(`AssignmentSubmission.submit_freeform` / `.submit_structured` /
`.grade_freeform` / `.mark_answer_and_maybe_finalize` / `.publish` /
`.unpublish`) — this file's job is request validation, permission
checks, and response shaping, never reimplementing that logic inline.
"""
import json

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .bridge import notify_submission_received
from .models import Assignment, AssignmentQuestion, AssignmentSource, AssignmentSubmission
from .permissions import IsAssignmentStaffOrOwner, IsPersonalSourceOnly, IsSubmissionStudent
from .throttling import AssignmentPublicPageThrottle
from .serializers import (
    AnswerReviewSerializer,
    AssignmentAnswerSerializer,
    AssignmentCreateSerializer,
    AssignmentSerializer,
    AssignmentSubmissionSerializer,
    FreeformSubmitSerializer,
    GradeFreeformSerializer,
    PublicSubmissionSerializer,
    StructuredSubmitSerializer,
)


class AssignmentViewSet(viewsets.ModelViewSet):
    """§7 — `create` is personal-only. Campus/liveclass assignments never
    reach this viewset; they're created via
    `assignment.bridge.create_context_assignment()` from those apps' own
    already-permission-checked endpoints, then surfaced to their users
    through campus's/liveclass's own thin-proxy viewsets (§5.2/§6.1 — not
    in this app), not through this one.
    """

    permission_classes = [permissions.IsAuthenticated, IsPersonalSourceOnly]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return AssignmentCreateSerializer
        return AssignmentSerializer

    def get_queryset(self):
        """Non-staff users see assignments they posted, plus personal
        assignments they hold a submission for (covers the case where a
        personal assignment's submission row was created before the
        Assignment object itself is re-fetched by a different client)."""
        user = self.request.user
        qs = Assignment.objects.all().prefetch_related("questions")
        if user.is_staff:
            return qs
        return qs.filter(
            Q(posted_by=user) | Q(source=AssignmentSource.PERSONAL, submissions__student=user)
        ).distinct()

    def perform_create(self, serializer):
        # Hard-wired regardless of what the client sent — see
        # IsPersonalSourceOnly's own docstring for why this, not that
        # permission class alone, is the real enforcement point.
        serializer.save(source=AssignmentSource.PERSONAL, posted_by=self.request.user)


class AssignmentSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = AssignmentSubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = AssignmentSubmission.objects.select_related("assignment", "student").prefetch_related(
            "answers__question"
        )
        if user.is_staff:
            return qs
        return qs.filter(Q(student=user) | Q(assignment__posted_by=user)).distinct()

    def get_permissions(self):
        if self.action in ("grade", "review_answer"):
            return [permissions.IsAuthenticated(), IsAssignmentStaffOrOwner()]
        if self.action in ("submit_freeform", "submit_structured", "publish", "unpublish"):
            return [permissions.IsAuthenticated(), IsSubmissionStudent()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        """§2 — personal-assignment flow: there's no bridge-created
        roster row to attach to (no roster exists for a personal
        assignment), so the student's own first interaction creates the
        `AssignmentSubmission` row directly. `unique_submission_per_
        student` still guards against a duplicate. Campus/liveclass
        submissions, by contrast, already exist (status=MISSING) the
        moment `bridge.create_context_assignment()` ran — students there
        only ever reach the `submit_*` actions below, never this create().
        """
        serializer.save(student=self.request.user)

    @action(detail=True, methods=["patch"])
    def submit_freeform(self, request, pk=None):
        """§2 free-form path. Goes to SUBMITTED/LATE — grading is the
        separate `grade` action below, matching "ek hi manual grade
        step"."""
        submission = self.get_object()
        serializer = FreeformSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission.submit_freeform(**serializer.validated_data)
        notify_submission_received(submission)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["post"])
    def submit_structured(self, request, pk=None):
        """§2a structured path — auto-grades on the way in, resolves
        straight to CHECKED or PARTIALLY_CHECKED depending on whether any
        `text` question is still pending review.

        [FIX / Task 9] — closes the gap `StructuredAnswerInputSerializer.
        answer_attachment`'s own docstring flagged as deferred to this
        view: DRF cannot bind a file to one entry inside a `many=True`
        nested list from a single multipart request body, since multipart
        has no native nested-structure syntax. So for a multipart
        request, the client sends `answers` as a JSON-encoded *string*
        (not nested multipart fields) and any per-question file under a
        flat `answer_<question_id>` key in `request.FILES` — the same
        `files.get(f"answer_{question_id}")` convention
        `TestAttempt.submit()` uses. This merges that file back onto its
        matching answer dict before the serializer ever sees it, so
        `AssignmentSubmission.submit_structured()` still just finds
        `answer_attachment` already present in the entry, same as a
        plain JSON (no file) request. A non-multipart, JSON-only request
        (no `text`-question file answers) is untouched — `answers` is
        already a list there, so the `isinstance(..., str)` check below
        is False and this is a no-op.
        """
        submission = self.get_object()
        data = request.data
        answers = data.get("answers")
        if isinstance(answers, str):
            try:
                answers = json.loads(answers)
            except (TypeError, ValueError):
                return Response(
                    {"answers": "Must be valid JSON when submitted as multipart/form-data."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not isinstance(answers, list):
                return Response(
                    {"answers": "Must be a JSON list of answer objects."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            for entry in answers:
                if not isinstance(entry, dict):
                    continue
                file_obj = request.FILES.get(f"answer_{entry.get('question_id')}")
                if file_obj is not None:
                    entry["answer_attachment"] = file_obj
            data = {**data, "answers": answers}
        serializer = StructuredSubmitSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        submission.submit_structured(serializer.validated_data["answers"])
        notify_submission_received(submission)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["patch"])
    def grade(self, request, pk=None):
        """§7 — 'Free-form path: PATCH {id}/grade/ (grade, feedback)'."""
        submission = self.get_object()
        serializer = GradeFreeformSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission.grade_freeform(**serializer.validated_data)
        return Response(AssignmentSubmissionSerializer(submission).data)

    @action(detail=True, methods=["post"], url_path=r"answer/(?P<question_id>[^/.]+)/review")
    def review_answer(self, request, pk=None, question_id=None):
        """§7 — 'Structured path: POST {id}/answer/{question_id}/review/
        ... sirf text-type AssignmentAnswer pe allowed.'"""
        submission = self.get_object()
        question = get_object_or_404(submission.assignment.questions, pk=question_id)
        if question.question_type != AssignmentQuestion.QuestionTypeChoices.TEXT:
            return Response(
                {"detail": "Only text-type questions can be manually reviewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = AnswerReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        answer = submission.mark_answer_and_maybe_finalize(
            question=question, reviewed_by=request.user, **serializer.validated_data
        )
        return Response(AssignmentAnswerSerializer(answer).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """§2 — always mints a fresh slug (see `AssignmentSubmission.
        publish()`'s own docstring for why re-publishing never reuses the
        previous URL)."""
        submission = self.get_object()
        return Response({"public_slug": submission.publish()})

    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        submission = self.get_object()
        submission.unpublish()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicSubmissionView(generics.RetrieveAPIView):
    """§2 — 'GET /assignment/public/{public_slug}/ — auth-free, sirf tab
    data deta hai jab public_slug non-empty ho.'"""

    permission_classes = [permissions.AllowAny]
    # [HARDENING] — see PRODUCTION_DESIGN.md §1.1/§6. Requires
    # DEFAULT_THROTTLE_RATES["assignment_public_page"] to be set in
    # settings.py or DRF falls back to no limit for this scope.
    throttle_classes = [AssignmentPublicPageThrottle]
    serializer_class = PublicSubmissionSerializer
    lookup_field = "public_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        # Blank public_slug = unpublished by definition (see model
        # comment on the field) — excluded here so an unpublished /
        # never-published submission 404s outright, rather than being
        # "reachable" via an empty-string URL segment.
        return AssignmentSubmission.objects.exclude(public_slug="").select_related("assignment", "student")