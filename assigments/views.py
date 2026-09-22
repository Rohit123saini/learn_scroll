# assigments/views.py
"""
§7 permissions and endpoint shapes, wired up as DRF viewsets. Every
action here delegates the actual write to a model method
(`assigmentsSubmission.submit_freeform` / `.submit_structured` /
`.grade_freeform` / `.mark_answer_and_maybe_finalize` / `.publish` /
`.unpublish`) — this file's job is request validation, permission
checks, and response shaping, never reimplementing that logic inline.
"""
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from common.pagination import StandardPagination
from common.question_csv import parse_csv

from .bridge import notify_submission_received
from .models import (
    assigments,
    assigmentsQuestion,
    assigmentsSource,
    assigmentsStatus,
    assigmentsSubmission,
    assigmentsVisibility,
)
from .permissions import (
    IsassigmentsStaffOrOwner, IsAssignmentPosterOrReadOnly, IsPersonalSourceOnly, IsSubmissionStudent,
)
from .throttling import assigmentsExploreThrottle, assigmentsPublicPageThrottle
from .serializers import (
    AnswerReviewSerializer,
    assigmentsAnswerSerializer,
    assigmentsCreateSerializer,
    assigmentsExploreSerializer,
    assigmentsSerializer,
    assigmentsSubmissionSerializer,
    FreeformSubmitSerializer,
    GradeFreeformSerializer,
    PublicSubmissionSerializer,
    PublishSerializer,
    RubricGradeSerializer,
    StructuredSubmitSerializer,
)

MAX_CSV_BYTES = 1024 * 1024


class assigmentsViewSet(viewsets.ModelViewSet):
    """§7 — `create` is personal-only. Campus/liveclass assigmentss never
    reach this viewset; they're created via
    `assigments.bridge.create_context_assigments()` from those apps' own
    already-permission-checked endpoints, then surfaced to their users
    through campus's/liveclass's own thin-proxy viewsets (§5.2/§6.1 — not
    in this app), not through this one.
    """

    permission_classes = [permissions.IsAuthenticated, IsPersonalSourceOnly, IsAssignmentPosterOrReadOnly]

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return assigmentsCreateSerializer
        if self.action == "explore":
            return assigmentsExploreSerializer
        return assigmentsSerializer

    def get_queryset(self):
        """Non-staff users see assigmentss they posted, plus personal
        assigmentss they hold a submission for (covers the case where a
        personal assigments's submission row was created before the
        assigments object itself is re-fetched by a different client)."""
        user = self.request.user
        qs = (
            assigments.objects.all()
            .prefetch_related("questions")
            .annotate(n_participants=Count("submissions", distinct=True))
        )
        if user.is_staff:
            return qs
        visible = Q(posted_by=user) | Q(source=assigmentsSource.PERSONAL, submissions__student=user)
        if self.action != "list":
            # A published, non-private personal assignment can be OPENED (retrieve /
            # join) by anyone who has its id or link. The plain LIST stays "mine +
            # joined"; discovery of other people's work goes through `explore`.
            visible |= Q(
                source=assigmentsSource.PERSONAL,
                status=assigmentsStatus.PUBLISHED,
                visibility__in=[assigmentsVisibility.LINK, assigmentsVisibility.PUBLIC],
            )
        return qs.filter(visible).distinct()

    def perform_create(self, serializer):
        # Hard-wired regardless of what the client sent — see
        # IsPersonalSourceOnly's own docstring for why this, not that
        # permission class alone, is the real enforcement point.
        # A new personal assignment starts as a private DRAFT; the poster
        # publishes it (see `publish`) once it is ready.
        serializer.save(
            source=assigmentsSource.PERSONAL,
            posted_by=self.request.user,
            status=assigmentsStatus.DRAFT,
            visibility=assigmentsVisibility.PRIVATE,
        )

    # ------------------------------------------------------------------
    # PUBLISH / SHARE / EXPLORE
    # ------------------------------------------------------------------
    @staticmethod
    def _publish_problems(assignment) -> list:
        """Everything that must be true before other people can see this."""
        problems = []
        if not assignment.title.strip():
            problems.append("Give it a title.")
        has_content = bool(
            assignment.description.strip() or assignment.attachment
            or assignment.has_structured_questions or assignment.rubric
        )
        if not has_content:
            problems.append("Add a description, an attachment, questions or a rubric — there is nothing to do yet.")
        if assignment.has_structured_questions:
            questions = list(assignment.questions.all())
            if not questions:
                problems.append("Structured assignments need at least one question.")
            missing = [q.order for q in questions
                       if q.question_type != assigmentsQuestion.QuestionTypeChoices.TEXT and not q.correct_answer]
            if missing:
                problems.append(f"Fill in the answer key for question(s): {', '.join(str(m) for m in missing)}.")
        if assignment.due_date and assignment.due_date < timezone.localdate():
            problems.append("The due date is already in the past.")
        return problems

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """Poster publishes a personal assignment / project: `{"visibility": "public" | "link"}`
        (default public). `public` = listed in Explore for everyone + share link;
        `link` = unlisted, anyone with the link. Re-publishing keeps the same link."""
        assignment = self.get_object()
        serializer = PublishSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        problems = self._publish_problems(assignment)
        if problems:
            raise ValidationError({"detail": "This assignment isn't ready to publish.", "problems": problems})
        try:
            assignment.publish_listing(serializer.validated_data["visibility"])
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages})
        return Response(assigmentsSerializer(assignment, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def unpublish(self, request, pk=None):
        assignment = self.get_object()
        assignment.unpublish_listing()
        return Response(assigmentsSerializer(assignment, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def join(self, request, pk=None):
        """Anyone the assignment is open to starts working on it: creates THEIR
        submission row (status `missing`, exactly like a roster row for campus /
        live-class members) and returns it. Idempotent. From there the normal
        `submit_freeform` / `submit_structured` actions apply."""
        assignment = self.get_object()
        if assignment.source != assigmentsSource.PERSONAL:
            raise ValidationError("Only personal assignments can be joined this way.")
        if not assignment.is_open_to(request.user):
            raise ValidationError("This assignment is not available.")
        if assignment.status == assigmentsStatus.ARCHIVED:
            raise ValidationError("This assignment is closed.")
        submission, created = assigmentsSubmission.objects.get_or_create(
            assigments=assignment, student=request.user
        )
        return Response(
            assigmentsSubmissionSerializer(submission, context={"request": request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], throttle_classes=[assigmentsExploreThrottle])
    def explore(self, request):
        """Everything published as `public`, newest first. Query params:
        `search`, `tag`, `kind` (assignment|project), `difficulty`,
        `ordering` (`new` default | `popular`), `page`, `page_size`."""
        qs = (
            assigments.objects.filter(
                source=assigmentsSource.PERSONAL,
                status=assigmentsStatus.PUBLISHED,
                visibility=assigmentsVisibility.PUBLIC,
            )
            .select_related("posted_by")
            .annotate(
                n_participants=Count("submissions", distinct=True),
                n_questions=Count("questions", distinct=True),
            )
        )
        params = request.query_params
        if params.get("search"):
            term = params["search"].strip()
            qs = qs.filter(Q(title__icontains=term) | Q(description__icontains=term))
        if params.get("kind") in ("assignment", "project"):
            qs = qs.filter(kind=params["kind"])
        if params.get("difficulty") in ("easy", "medium", "hard"):
            qs = qs.filter(difficulty=params["difficulty"])
        if params.get("tag"):
            # JSON "contains": supported on PostgreSQL (production); not on SQLite.
            qs = qs.filter(tags__contains=[params["tag"].strip().lower()])
        qs = qs.order_by("-n_participants", "-published_at") if params.get("ordering") == "popular" \
            else qs.order_by("-published_at", "-created_at")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        serializer = self.get_serializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(
        detail=True, methods=["post"], url_path="questions-import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def questions_import(self, request, pk=None):
        """CSV upload of structured questions WITH the answer key filled in
        (`common/question_csv.py` documents the columns). Only while nobody has
        started the assignment yet — same rule as changing the question mode."""
        assignment = self.get_object()
        if not assignment.has_structured_questions:
            raise ValidationError("Turn on has_structured_questions before importing questions.")
        if not assignment.can_change_question_mode():
            raise ValidationError("Questions can't be added once someone has started this assignment.")
        upload = request.FILES.get("file")
        if upload is None:
            raise ValidationError({"file": "Attach a CSV file."})
        if upload.size > MAX_CSV_BYTES:
            raise ValidationError({"file": "CSV is larger than 1 MB."})

        next_order = (assignment.questions.aggregate(m=Max("order"))["m"] or 0) + 1
        result = parse_csv(upload.read(), start_order=next_order)
        if result.errors:
            return Response(
                {"detail": "The CSV has problems — nothing was imported.",
                 "errors": [{"row": row, "message": msg} for row, msg in result.errors]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            with transaction.atomic():
                for q in result.questions:
                    assigmentsQuestion.objects.create(
                        assigments=assignment,
                        order=q["order"], question_type=q["question_type"], text=q["text"],
                        marks=q["marks"], options=q["options"], correct_answer=q["correct_answer"],
                    )
        except (DjangoValidationError, IntegrityError) as exc:
            detail = exc.messages if isinstance(exc, DjangoValidationError) else ["Duplicate question order."]
            raise ValidationError({"detail": detail})
        assignment.refresh_from_db()
        return Response({"created": len(result.questions), "total_marks": assignment.total_marks},
                        status=status.HTTP_201_CREATED)


class assigmentsSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = assigmentsSubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        qs = assigmentsSubmission.objects.select_related("assigments", "student").prefetch_related(
            "answers__question"
        )
        assignment_id = self.request.query_params.get("assigments")
        if assignment_id:
            qs = qs.filter(assigments_id=assignment_id)
        if user.is_staff:
            return qs
        return qs.filter(Q(student=user) | Q(assigments__posted_by=user)).distinct()

    def get_permissions(self):
        if self.action in ("grade", "review_answer", "grade_rubric"):
            return [permissions.IsAuthenticated(), IsassigmentsStaffOrOwner()]
        if self.action in ("submit_freeform", "submit_structured", "publish", "unpublish"):
            return [permissions.IsAuthenticated(), IsSubmissionStudent()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        """§2 — personal-assigments flow: there's no bridge-created
        roster row to attach to (no roster exists for a personal
        assigments), so the student's own first interaction creates the
        `assigmentsSubmission` row directly. `unique_submission_per_
        student` still guards against a duplicate. Campus/liveclass
        submissions, by contrast, already exist (status=MISSING) the
        moment `bridge.create_context_assigments()` ran — students there
        only ever reach the `submit_*` actions below, never this create().
        """
        serializer.save(student=self.request.user)

    @action(detail=True, methods=["patch"])
    def submit_freeform(self, request, pk=None):
        """§2 free-form path. Goes to SUBMITTED/LATE — grading is the
        separate `grade` action below, matching "ek hi manual grade
        step"."""
        submission = self.get_object()
        if submission.assigments.status == assigmentsStatus.ARCHIVED:
            raise ValidationError("This assignment is closed.")
        serializer = FreeformSubmitSerializer(data=request.data, context={"assignment": submission.assigments})
        serializer.is_valid(raise_exception=True)
        submission.submit_freeform(**serializer.validated_data)
        notify_submission_received(submission)
        return Response(assigmentsSubmissionSerializer(submission).data)

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
        `assigmentsSubmission.submit_structured()` still just finds
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
        return Response(assigmentsSubmissionSerializer(submission).data)

    @action(detail=True, methods=["patch"], url_path="grade-rubric")
    def grade_rubric(self, request, pk=None):
        """Project grading: `{"scores": {"Design": 8, "Code": 12}, "feedback": "…"}` —
        one score per rubric criterion, validated against the assignment's rubric."""
        submission = self.get_object()
        serializer = RubricGradeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            submission.grade_rubric(**serializer.validated_data)
        except DjangoValidationError as exc:
            raise ValidationError({"detail": exc.messages})
        return Response(assigmentsSubmissionSerializer(submission).data)

    @action(detail=True, methods=["patch"])
    def grade(self, request, pk=None):
        """§7 — 'Free-form path: PATCH {id}/grade/ (grade, feedback)'."""
        submission = self.get_object()
        serializer = GradeFreeformSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submission.grade_freeform(**serializer.validated_data)
        return Response(assigmentsSubmissionSerializer(submission).data)

    @action(detail=True, methods=["post"], url_path=r"answer/(?P<question_id>[^/.]+)/review")
    def review_answer(self, request, pk=None, question_id=None):
        """§7 — 'Structured path: POST {id}/answer/{question_id}/review/
        ... sirf text-type assigmentsAnswer pe allowed.'"""
        submission = self.get_object()
        question = get_object_or_404(submission.assigments.questions, pk=question_id)
        if question.question_type != assigmentsQuestion.QuestionTypeChoices.TEXT:
            return Response(
                {"detail": "Only text-type questions can be manually reviewed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = AnswerReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        answer = submission.mark_answer_and_maybe_finalize(
            question=question, reviewed_by=request.user, **serializer.validated_data
        )
        return Response(assigmentsAnswerSerializer(answer).data)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        """§2 — always mints a fresh slug (see `assigmentsSubmission.
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
    """§2 — 'GET /assigments/public/{public_slug}/ — auth-free, sirf tab
    data deta hai jab public_slug non-empty ho.'"""

    permission_classes = [permissions.AllowAny]
    # [HARDENING] — see PRODUCTION_DESIGN.md §1.1/§6. Requires
    # DEFAULT_THROTTLE_RATES["assigments_public_page"] to be set in
    # settings.py or DRF falls back to no limit for this scope.
    throttle_classes = [assigmentsPublicPageThrottle]
    serializer_class = PublicSubmissionSerializer
    lookup_field = "public_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        # Blank public_slug = unpublished by definition (see model
        # comment on the field) — excluded here so an unpublished /
        # never-published submission 404s outright, rather than being
        # "reachable" via an empty-string URL segment.
        return assigmentsSubmission.objects.exclude(public_slug="").select_related("assigments", "student")


class PublicAssignmentView(generics.RetrieveAPIView):
    """`GET /assigments/p/<slug>/` — auth-free preview behind a share link.
    Only PUBLISHED, non-private, personal assignments; no questions or answer keys
    (see `assigmentsExploreSerializer`). Uses the same dedicated throttle scope as
    the submission share page."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = [assigmentsPublicPageThrottle]
    serializer_class = assigmentsExploreSerializer
    lookup_field = "public_slug"
    lookup_url_kwarg = "slug"

    def get_queryset(self):
        return (
            assigments.objects.filter(
                source=assigmentsSource.PERSONAL,
                status=assigmentsStatus.PUBLISHED,
                visibility__in=[assigmentsVisibility.LINK, assigmentsVisibility.PUBLIC],
            )
            .exclude(public_slug__isnull=True)
            .select_related("posted_by")
            .annotate(
                n_participants=Count("submissions", distinct=True),
                n_questions=Count("questions", distinct=True),
            )
        )
