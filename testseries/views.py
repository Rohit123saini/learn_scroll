# testseries/views.py
import json

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.db import models as db_models
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import (
    Question, TestAttempt, TestSeries, TestSeriesPurchase,
    attachment_extension_validator, validate_attachment_size,
)
from .permissions import IsSeriesCreatorOrReadOnly, user_can_review_attempt
from .serializers import QuestionSerializer, TestAttemptSerializer, TestSeriesSerializer



class TestSeriesViewSet(viewsets.ModelViewSet):
    """§5: `source="individual"` — any authenticated user creates
    directly here, choosing `is_paid`/`price_coins` themselves.
    `source="campus"`/`"liveclass"` series are NOT created through this
    endpoint at all — they arrive via `bridge.create_context_testseries()`,
    called from campus/liveclass's own bridge-wrapped endpoints (which
    have already done their staff/teacher checks). This viewset still
    lists/retrieves them (read path is shared), just never creates them.
    """

    queryset = TestSeries.objects.all()
    serializer_class = TestSeriesSerializer
    permission_classes = [IsAuthenticated, IsSeriesCreatorOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        source = self.request.query_params.get("source")
        if source:
            qs = qs.filter(source=source)
        # Individual/marketplace discovery is browse-based (§4 — no
        # follower/subscriber concept yet), so published individual
        # series are visible to everyone; a creator additionally sees
        # their own drafts. Campus/liveclass series are scoped by
        # context upstream by the calling bridge endpoint, not filtered
        # here — this app never resolves context membership itself.
        return qs.filter(
            db_models.Q(status=TestSeries.Status.PUBLISHED) | db_models.Q(creator=self.request.user)
        )

    def perform_create(self, serializer):
        # No bulk TESTSERIES_POSTED notification here — that's a
        # roster-driven notification only meaningful for campus/liveclass
        # context series, which don't go through this method at all
        # (see bridge.create_context_testseries). Individual series rely
        # on browse-based discovery per §4.
        serializer.save(creator=self.request.user, source=TestSeries.Source.INDIVIDUAL)

    @action(detail=True, methods=["post"])
    def publish(self, request, pk=None):
        series = self.get_object()
        if series.creator_id != request.user.id:
            raise PermissionDenied("Only the creator can publish this series.")
        if series.status != TestSeries.Status.DRAFT:
            raise ValidationError("Only a draft series can be published.")
        if not series.questions.exists():
            raise ValidationError("Cannot publish a series with no questions.")
        series.recompute_total_marks(save=False)
        series.status = TestSeries.Status.PUBLISHED
        series.save(update_fields=["status", "total_marks"])
        return Response(TestSeriesSerializer(series, context={"request": request}).data)


class QuestionViewSet(viewsets.ModelViewSet):
    """Nested under a series: `/testseries/{series_pk}/questions/`.
    Editing is blocked once the parent series leaves `draft` — attempts
    may already exist against it, and editing questions afterward would
    make existing scoring inconsistent (design doc §2)."""

    serializer_class = QuestionSerializer
    permission_classes = [IsAuthenticated]

    def get_series(self):
        return get_object_or_404(TestSeries, pk=self.kwargs["series_pk"])

    def get_queryset(self):
        return Question.objects.filter(series_id=self.kwargs["series_pk"])

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        return ctx

    def _check_draft_and_owner(self, series):
        if series.creator_id != self.request.user.id:
            raise PermissionDenied("Only the creator can modify questions.")
        if series.status != TestSeries.Status.DRAFT:
            raise ValidationError("Questions can only be added, edited, or removed while the series is a draft.")

    def perform_create(self, serializer):
        series = self.get_series()
        self._check_draft_and_owner(series)
        serializer.save(series=series)
        series.recompute_total_marks()

    def perform_update(self, serializer):
        series = self.get_series()
        self._check_draft_and_owner(series)
        serializer.save()
        series.recompute_total_marks()

    def perform_destroy(self, instance):
        series = instance.series
        self._check_draft_and_owner(series)
        instance.delete()
        series.recompute_total_marks()


class TestAttemptViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Students see/submit only their own attempts; a series creator (or,
    for campus context, the resolved subject-teacher — see
    `permissions.user_can_review_attempt`) can see and review attempts
    against their own series through the same endpoint."""

    serializer_class = TestAttemptSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return TestAttempt.objects.filter(
            db_models.Q(student=user) | db_models.Q(series__creator=user)
        ).distinct()

    @action(detail=False, methods=["post"], url_path=r"start/(?P<series_id>[^/.]+)")
    def start(self, request, series_id=None):
        """Idempotent: an existing attempt (including an already-paid,
        in-progress retry) is returned as-is rather than re-charging or
        erroring — §5's "existing purchase = continue, don't re-charge"
        rule. `TestAttempt` is unique per (series, student) regardless of
        `is_paid` (MVP, attempts_allowed=1), so this one lookup covers both
        the paid and unpaid idempotency cases — no separate purchase-status
        lookup is needed."""
        series = get_object_or_404(TestSeries, pk=series_id, status=TestSeries.Status.PUBLISHED)

        existing = TestAttempt.objects.filter(series=series, student=request.user).first()
        if existing:
            return Response(TestAttemptSerializer(existing, context={"request": request}).data)

        # Coerced to str + truncated to the model's max_length (30): these
        # come straight from request.data, and an unexpected type (list,
        # int, an over-length string) would otherwise surface as a raw
        # 500 from the model layer instead of just being safely clipped.
        snapshot = {
            "roll_number": str(request.data.get("roll_number", ""))[:30],
            "enrollment_no": str(request.data.get("enrollment_no", ""))[:30],
        }

        try:
            if series.is_paid:
                purchase = TestSeriesPurchase.purchase_and_start_attempt(series=series, buyer=request.user, **snapshot)
                attempt = purchase.attempt
            else:
                attempt = TestAttempt.objects.create(series=series, student=request.user, **snapshot)
        except ValueError as exc:
            # Insufficient coin balance, per CoinLedger.record_transaction — §5/§6.
            return Response({"detail": str(exc)}, status=status.HTTP_402_PAYMENT_REQUIRED)
        except IntegrityError:
            # Lost a race with a concurrent start() call for the same
            # (series, student) — unique_attempt_per_student_per_series
            # caught it. purchase_and_start_attempt() is @transaction.atomic,
            # so a losing paid attempt's coin debit was already rolled back;
            # return the winner's attempt instead of surfacing a 500.
            existing = TestAttempt.objects.filter(series=series, student=request.user).first()
            if existing:
                return Response(TestAttemptSerializer(existing, context={"request": request}).data)
            raise

        return Response(TestAttemptSerializer(attempt, context={"request": request}).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], parser_classes=[JSONParser, MultiPartParser, FormParser])
    def submit(self, request, pk=None):
        """Body: `{"answers": {question_id: answer_data, ...}}` as plain
        JSON — OR, to attach an image/file answer to a `text` question,
        `multipart/form-data` with `answers` as a JSON-encoded string
        field (multipart can't carry nested JSON) plus one file per
        attached answer under `answer_<question_id>`."""
        attempt = self.get_object()
        if attempt.student_id != request.user.id:
            raise PermissionDenied("You can only submit your own attempt.")
        if attempt.status != TestAttempt.Status.IN_PROGRESS:
            raise ValidationError("This attempt has already been submitted.")

        answers_raw = request.data.get("answers", {})
        if isinstance(answers_raw, str):
            try:
                answers = json.loads(answers_raw)
            except (TypeError, ValueError):
                raise ValidationError({"answers": "Must be valid JSON when sent as a form field."})
        else:
            answers = answers_raw or {}

        for field_name, uploaded_file in request.FILES.items():
            if not field_name.startswith("answer_"):
                continue
            try:
                attachment_extension_validator(uploaded_file)
                validate_attachment_size(uploaded_file)
            except DjangoValidationError as exc:
                raise ValidationError({field_name: exc.messages})

        attempt.submit(answers=answers, files=request.FILES)
        attempt.refresh_from_db()
        return Response(TestAttemptSerializer(attempt, context={"request": request}).data)

    @action(detail=True, methods=["post"], url_path=r"answer/(?P<question_id>[^/.]+)/review")
    def review_answer(self, request, pk=None, question_id=None):
        """§5: `POST /attempts/{id}/answer/{question_id}/review/` — the
        actual per-question review endpoint. `text`-type only; auto-graded
        responses come back 400 (`QuestionResponse.mark_answer` itself
        also guards this — see models.py — this is the same check
        surfaced as a clean API error before we even try)."""
        attempt = self.get_object()
        if not user_can_review_attempt(request.user, attempt):
            raise PermissionDenied("You are not permitted to review this attempt.")

        response = get_object_or_404(attempt.responses, question_id=question_id)
        if response.is_auto_graded:
            raise ValidationError("Only text-type (manually-graded) responses can be reviewed this way.")

        marks_awarded = request.data.get("marks_awarded")
        if marks_awarded is None:
            raise ValidationError({"marks_awarded": "This field is required."})
        try:
            marks_awarded = int(marks_awarded)
        except (TypeError, ValueError):
            raise ValidationError({"marks_awarded": "Must be an integer."})
        if marks_awarded < 0:
            raise ValidationError({"marks_awarded": "Cannot be negative."})
        if marks_awarded > response.question.marks:
            raise ValidationError({"marks_awarded": f"Cannot exceed the question's marks ({response.question.marks})."})

        attempt.mark_answer_and_maybe_finalize(
            question=response.question,
            marks_awarded=marks_awarded,
            feedback=request.data.get("feedback", ""),
            reviewer=request.user,
        )
        attempt.refresh_from_db()
        return Response(TestAttemptSerializer(attempt, context={"request": request}).data)