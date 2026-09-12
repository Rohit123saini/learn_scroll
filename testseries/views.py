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
    Question, TestAttempt, TestSeries, TestSeriesPurchase, TestSeriesReview,
    attachment_extension_validator, validate_attachment_size,
)
from .bridge import ask_query_on_series, answer_query_on_series
from .permissions import (
    CanAskQueryOnCheckedAttempt, CanReviewCheckedAttempt, IsSeriesCreatorOrReadOnly,
    user_can_review_attempt,
)
from .serializers import (
    QuestionSerializer, TestAttemptSerializer, TestSeriesReviewSerializer, TestSeriesSerializer,
)



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

        # TASK 5: notify the creator's followers that a new (individual/
        # marketplace) series is live. Only for source="individual" —
        # campus/liveclass-context series already notify their own
        # classroom/section roster through a different, membership-based
        # path (see perform_create's own note above for why THAT
        # notification is scoped the same way; individual discovery is
        # follow/browse-based per §4, campus/liveclass is not). Queued,
        # never inline — see tasks.py::notify_followers_new_testseries
        # for why a synchronous per-follower loop here would be wrong.
        if series.source == TestSeries.Source.INDIVIDUAL:
            from .tasks import notify_followers_new_testseries

            notify_followers_new_testseries.delay(series.id)

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

    def get_permissions(self):
        if self.action == "ask_query":
            return [IsAuthenticated(), CanAskQueryOnCheckedAttempt()]
        return [IsAuthenticated()]

    def get_queryset(self):
        # Scoped for LIST only (browse-your-own-attempts): a student's
        # own attempts, plus attempts on series they themselves created.
        # This intentionally does NOT also try to include "attempts a
        # campus subject-teacher may review" — resolving that set here
        # would mean this app resolving campus context-membership
        # itself, which is exactly what the golden rule (no campus/
        # liveclass imports/queries from this app) forbids. Retrieving
        # a SINGLE attempt for review is handled separately by
        # get_object() below, which is where the subject-teacher case
        # actually needs to work.
        user = self.request.user
        return TestAttempt.objects.filter(
            db_models.Q(student=user) | db_models.Q(series__creator=user)
        ).distinct()

    def get_object(self):
        # FIX for the gap previously flagged here: `retrieve()` and
        # `review_answer()` both need a non-creator campus subject-
        # teacher (approved via `permissions.user_can_review_attempt()`)
        # to actually reach the permission check, instead of being
        # filtered out by get_queryset()'s narrower "list" scope first.
        # Detail access is therefore resolved independently of
        # get_queryset(): fetch the row unfiltered by owner/creator,
        # then allow it only if the requesting user is the attempt's
        # own student OR is permitted to review it (series creator, or
        # campus subject-teacher via `user_can_review_attempt`). This
        # does not widen who can review anything — `user_can_review_
        # attempt()` is the exact same check the endpoint already
        # relied on; it's just no longer unreachable behind a 404.
        obj = get_object_or_404(
            TestAttempt.objects.select_related("series", "student"),
            pk=self.kwargs.get("pk"),
        )
        is_owner = obj.student_id == self.request.user.id
        if not is_owner and not user_can_review_attempt(self.request.user, obj):
            raise PermissionDenied("You do not have permission to access this attempt.")
        return obj

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

    @action(detail=True, methods=["post"], url_path="ask-query")
    def ask_query(self, request, pk=None):
        """Task 16 — `POST /attempts/{id}/ask-query/`. `get_object()`
        already resolves "own attempt OR permitted reviewer" access;
        `CanAskQueryOnCheckedAttempt` narrows that to "own attempt only"
        for this action. The `status != checked` business rule lives in
        `bridge.ask_query_on_series()` (plain `ValueError`), caught here
        and surfaced as a clean 400 per the acceptance checklist."""
        attempt = self.get_object()

        text = request.data.get("text", "")
        if not str(text).strip():
            raise ValidationError({"text": "This field is required."})

        try:
            doubt = ask_query_on_series(
                attempt=attempt,
                student=request.user,
                text=text,
                is_anonymous=bool(request.data.get("is_anonymous", False)),
            )
        except ValueError as exc:
            raise ValidationError(str(exc))

        # Kept deliberately minimal — only fields `bridge.
        # ask_query_on_series()` is known to set at creation time.
        # Once message/models.py's real `DoubtQuestion` shape is
        # confirmed, swap this for a proper DoubtQuestionSerializer.
        return Response(
            {
                "id": str(doubt.id),
                "text": doubt.text,
                "is_anonymous": doubt.is_anonymous,
                "context_type": doubt.context_type,
                "context_id": str(doubt.context_id),
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], url_path="answer-query")
    def answer_query(self, request, pk=None):
        """Task 16 — teacher-facing counterpart to `ask_query`. Not in the
        task's own file list, but `bridge.py::answer_query_on_series()`
        flags it as needed to actually wire the feature end-to-end (see
        that function's docstring) — added here as the natural
        `TestAttemptViewSet` counterpart.

        `pk` is the ATTEMPT id (same URL shape as `ask_query`/
        `review_answer`); `doubt_id` in the body identifies which query on
        that attempt is being answered. `get_object()` already resolves
        "own attempt OR permitted reviewer" (a series creator always
        qualifies); `answer_query_on_series()` itself does the finer "are
        YOU actually this series' creator" check — a series can have more
        than one permitted reviewer (e.g. a campus subject-teacher via
        `user_can_review_attempt`), but only the creator can answer
        queries, per that function's own docstring."""
        attempt = self.get_object()

        doubt_id = request.data.get("doubt_id")
        if not doubt_id:
            raise ValidationError({"doubt_id": "This field is required."})
        answer_text = request.data.get("answer_text", "")
        if not str(answer_text).strip():
            raise ValidationError({"answer_text": "This field is required."})

        try:
            doubt = answer_query_on_series(doubt_id=doubt_id, teacher=request.user, answer_text=answer_text)
        except ValueError as exc:
            raise ValidationError(str(exc))
        except PermissionError as exc:
            raise PermissionDenied(str(exc))

        # Belt-and-suspenders only: `answer_query_on_series()` already
        # independently confirms `teacher == series.creator` off the
        # doubt itself, so this attempt-id mismatch is never a security
        # gap — just catches a client sending the wrong attempt id in the
        # URL for this `doubt_id` and surfaces it as a 400 instead of a
        # silently-succeeded-on-the-wrong-attempt response.
        if str(doubt.context_id) != str(attempt.id):
            raise ValidationError({"doubt_id": "This query does not belong to this attempt."})

        return Response(
            {
                "id": str(doubt.id),
                "is_answered": doubt.is_answered,
                "answer_text": doubt.answer_text,
                "answered_at": doubt.answered_at,
            }
        )


class TestSeriesReviewViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Task 15. Two access shapes, both wired manually in urls.py (same
    "no DefaultRouter, series_pk explicit in the URL" reasoning
    `question_list`/`question_detail` already use):

      - `/testseries/<series_pk>/reviews/` (list, create) — `list` is a
        public read of one series' reviews (like `TestSeriesViewSet`'s
        published-series browse path); `create` is gated by
        `CanReviewCheckedAttempt` (coarse: "did you ever attempt this
        series") plus the checked-status/already-reviewed business
        rules inside `TestSeriesReviewSerializer.validate()`.
      - `/testseries/reviews/my-view/` (`my_view`, GET only, no
        series_pk) — the creator-aggregate review dashboard: every
        review across every series THIS user created, never anyone
        else's (acceptance checklist: "Creator ka my-view sirf apni
        series ka review-dashboard dikhata hai, doosron ka nahi").
    """

    serializer_class = TestSeriesReviewSerializer
    permission_classes = [IsAuthenticated]

    def get_series(self):
        return get_object_or_404(TestSeries, pk=self.kwargs["series_pk"])

    def get_queryset(self):
        # Only reachable from list()/create() on the nested route, where
        # series_pk is always present (see urls.py) — my_view() below
        # builds its own creator-scoped queryset directly and never
        # calls this.
        return TestSeriesReview.objects.filter(
            series_id=self.kwargs["series_pk"]
        ).select_related("student", "series")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if "series_pk" in self.kwargs:
            ctx["series"] = self.get_series()
        return ctx

    def get_permissions(self):
        if self.action == "create":
            return [IsAuthenticated(), CanReviewCheckedAttempt()]
        return [IsAuthenticated()]

    def perform_create(self, serializer):
        try:
            serializer.save()
        except IntegrityError:
            # Lost a race with a concurrent review-create for the same
            # (series, student) — unique_review_per_student_per_series
            # caught it. Same race-handling shape as
            # TestAttemptViewSet.start()'s IntegrityError handling above,
            # surfaced as a clean 400 instead of a raw 500.
            raise ValidationError("You have already reviewed this series.")

    @action(detail=False, methods=["get"], url_path="my-view")
    def my_view(self, request):
        """Creator-aggregate dashboard: every review across every series
        `request.user` created, plus a per-series {avg, count} rollup —
        scoped to `series__creator=request.user` only, never another
        creator's reviews (the acceptance-checklist requirement)."""
        reviews = (
            TestSeriesReview.objects.filter(series__creator=request.user)
            .select_related("student", "series")
            .order_by("-created_at")
        )
        summary = (
            TestSeries.objects.filter(creator=request.user)
            .annotate(avg=db_models.Avg("reviews__rating"), count=db_models.Count("reviews"))
            .filter(count__gt=0)
            .values("id", "title", "avg", "count")
        )
        return Response({
            "summary": list(summary),
            "reviews": TestSeriesReviewSerializer(reviews, many=True, context={"request": request}).data,
        })