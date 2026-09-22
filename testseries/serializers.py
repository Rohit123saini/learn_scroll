# testseries/serializers.py
import json
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers

from . import policy
from .models import (
    Question, QuestionResponse, TestAttempt, TestCertificate, TestProctorEvent, TestRecording,
    TestSeries, TestSeriesPurchase, TestSeriesReview, attachment_extension_validator,
    validate_attachment_size,
)


def display_name(user) -> str:
    """Public-facing name for leaderboards / certificates (never the email)."""
    if user is None:
        return ""
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = [
            "id", "series", "order", "question_type", "text",
            "attachment", "marks", "negative_marks", "topic", "difficulty",
            "options", "correct_answer", "explanation",
        ]
        read_only_fields = ["id", "series"]

    def validate_attachment(self, value):
        # Mirrors the FileField validators on Question.attachment itself
        # (models.py) — run here too so a bad extension/oversized file
        # comes back as a clean DRF 400 instead of depending on
        # full_clean() inside Question.save() being caught upstream.
        if value:
            try:
                attachment_extension_validator(value)
                validate_attachment_size(value)
            except DjangoValidationError as exc:
                raise serializers.ValidationError(exc.messages)
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        # A student attempting the test must never see the answer key —
        # only the series creator (who can also review) gets it back.
        # `explanation` is part of the solution, so it is withheld exactly
        # the same way; students get it from the `solutions` endpoint once
        # results are released.
        if not (user and getattr(user, "is_authenticated", False) and instance.series.creator_id == user.id):
            data.pop("correct_answer", None)
            data.pop("explanation", None)
        return data

    def validate(self, attrs):
        # Mirror Question.clean()'s shape rules here too so a bad
        # request comes back as a clean DRF 400 instead of surfacing as
        # a ValidationError from full_clean() inside Question.save().
        # Build a throwaway (unsaved) instance rather than passing
        # self.instance.__dict__ as kwargs — that dict carries internal
        # Django state (e.g. `_state`) that isn't a valid field kwarg.
        temp = Question() if self.instance is None else Question(
            question_type=self.instance.question_type,
            options=self.instance.options,
            correct_answer=self.instance.correct_answer,
        )
        for key, value in attrs.items():
            setattr(temp, key, value)
        try:
            temp.clean()
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
        return attrs


class QuestionResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = QuestionResponse
        fields = [
            "id", "question", "answer_data", "answer_attachment", "is_auto_graded", "is_correct",
            "marks_awarded", "penalty", "time_spent_seconds", "reviewer_feedback", "reviewed_by", "reviewed_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_owner_student = user and instance.attempt.student_id == user.id
        is_reviewer = user and instance.attempt.series.creator_id == user.id
        # Before an attempt is checked, a student shouldn't see per-question
        # correctness for still-ungraded text answers (nothing to leak
        # for auto-graded ones, they're already resolved at submit-time).
        if is_owner_student and not is_reviewer and instance.marks_awarded is None and not instance.is_auto_graded:
            data["reviewer_feedback"] = ""
        return data


# Fields that decide how a test is RUN. Once a student has started an attempt
# they are frozen — changing the window / timer / pass mark under someone's feet
# would be unfair and would make already-issued certificates meaningless.
FROZEN_AFTER_ATTEMPTS = (
    "delivery_mode", "starts_at", "ends_at", "duration_minutes", "pass_percentage", "proctoring",
)


class TestSeriesSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True, read_only=True)
    creator = serializers.PrimaryKeyRelatedField(read_only=True)
    # Model properties, not DB fields — must be declared explicitly so
    # ModelSerializer picks them up at all; declaring them read_only
    # here is sufficient (no need to also list in Meta.read_only_fields,
    # which is only for auto-generated fields).
    avg_rating = serializers.FloatField(read_only=True)
    review_count = serializers.IntegerField(read_only=True)
    question_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()
    window_state = serializers.SerializerMethodField()

    class Meta:
        model = TestSeries
        fields = [
            "id", "source", "context_type", "context_id", "creator", "title",
            "description", "is_paid", "price_coins", "duration_minutes",
            "total_marks", "status", "attempts_allowed", "questions", "question_count",
            "avg_rating", "review_count", "created_at", "updated_at",
            # ---- advanced delivery / certification (migration 0003)
            "delivery_mode", "starts_at", "ends_at", "late_entry_minutes", "window_state",
            "proctoring", "record_live",
            "pass_percentage", "certificate_enabled", "certificate_title",
            "result_release", "results_released_at", "show_solutions",
            "share_slug", "share_url",
        ]
        # `source`/`context_type`/`context_id` are provenance — set once at
        # creation (INDIVIDUAL here, or CAMPUS/LIVECLASS via bridge.py) and
        # never client-writable afterward, or a creator could PATCH their own
        # individual series into impersonating a campus/liveclass context it
        # was never actually created through. `status` is read-only too —
        # the only supported transition (draft -> published) has real
        # preconditions (has questions) and side effects (total_marks
        # recompute) that live in the `publish` action; allowing a direct
        # PATCH here would let a creator skip both. `share_slug` is minted by
        # `publish`; `results_released_at` by the `release-results` action.
        read_only_fields = [
            "id", "creator", "source", "context_type", "context_id",
            "status", "total_marks", "created_at", "updated_at",
            "results_released_at", "share_slug",
        ]

    # ---- output ----------------------------------------------------------
    def get_question_count(self, obj):
        annotated = getattr(obj, "q_count", None)  # set by the viewset queryset
        return annotated if annotated is not None else obj.questions.count()

    def get_share_url(self, obj):
        if not obj.share_slug:
            return None
        template = getattr(settings, "TESTSERIES_SHARE_URL_TEMPLATE", "")
        if template:
            return template.format(slug=obj.share_slug)
        request = self.context.get("request")
        path = f"/testseries/public/{obj.share_slug}/"
        return request.build_absolute_uri(path) if request else path

    def get_window_state(self, obj):
        return obj.window_state()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_creator = bool(user and getattr(user, "is_authenticated", False) and instance.creator_id == user.id)
        if not is_creator:
            # [SECURITY FIX] the full question list (with options) used to be
            # embedded in every series payload — so anyone could read a PAID
            # test's questions without buying it, and every draft. Students
            # get `question_count` here; the questions themselves come from
            # `/{id}/questions/`, which is gated on having an attempt.
            data.pop("questions", None)
        return data

    # ---- input -----------------------------------------------------------
    def validate(self, attrs):
        inst = self.instance

        def get(name, default=None):
            return attrs.get(name, getattr(inst, name, default))

        # Provenance is read-only, so on create this endpoint always builds an
        # INDIVIDUAL series (see the viewset); campus / liveclass series come
        # from their bridges.
        source = getattr(inst, "source", TestSeries.Source.INDIVIDUAL)

        # --- pricing policy (config-driven: settings.TESTSERIES_PRICING_POLICY)
        # Strict only when it matters: on create, or when the client actually
        # touches the pricing fields. A plain title edit on a legacy free
        # individual series must not suddenly be rejected.
        if inst is None or "is_paid" in attrs or "price_coins" in attrs:
            try:
                policy.normalize_pricing(
                    source=source,
                    is_paid=bool(get("is_paid", False)),
                    price_coins=get("price_coins", 0) or 0,
                    strict=True,
                )
            except policy.PolicyError as exc:
                raise serializers.ValidationError({exc.field: exc.message})

        # --- delivery window
        mode = get("delivery_mode", TestSeries.DeliveryMode.SELF_PACED)
        starts_at, ends_at = get("starts_at"), get("ends_at")
        if mode != TestSeries.DeliveryMode.SELF_PACED and starts_at is None:
            raise serializers.ValidationError({"starts_at": "Required for scheduled and live tests."})
        if starts_at and ends_at and ends_at <= starts_at:
            raise serializers.ValidationError({"ends_at": "Must be after starts_at."})
        if get("result_release") == TestSeries.ResultRelease.AFTER_END and ends_at is None:
            raise serializers.ValidationError({"result_release": "'after_end' needs ends_at to be set."})

        # --- certification
        if get("certificate_enabled", False) and not get("pass_percentage"):
            raise serializers.ValidationError(
                {"pass_percentage": "Set a pass mark (1-100) to issue certificates."}
            )

        # --- don't change the rules of a test people are already sitting
        if inst is not None and inst.attempts.exists():
            changed = [
                f for f in FROZEN_AFTER_ATTEMPTS if f in attrs and attrs[f] != getattr(inst, f)
            ]
            if changed:
                raise serializers.ValidationError(
                    {f: "Cannot be changed after students have started attempting this test." for f in changed}
                )
        return attrs


class TestSeriesPurchaseSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestSeriesPurchase
        fields = [
            "id", "series", "buyer", "coins_spent", "status",
            "attempt", "created_at", "released_at", "refunded_at",
        ]
        read_only_fields = fields


class TestAttemptSerializer(serializers.ModelSerializer):
    responses = QuestionResponseSerializer(many=True, read_only=True)
    # Server clock, so the client can compute the countdown from the SERVER's
    # `deadline_at` instead of trusting the device clock.
    server_time = serializers.SerializerMethodField()
    certificate_code = serializers.SerializerMethodField()
    results_released = serializers.SerializerMethodField()

    class Meta:
        model = TestAttempt
        fields = [
            "id", "series", "student", "attempt_number", "auto_score",
            "final_score", "status", "checked_by", "roll_number",
            "enrollment_no", "submitted_at", "checked_at", "responses",
            # ---- advanced (migration 0003)
            "started_at", "deadline_at", "server_time", "submitted_late",
            "percentage", "passed", "integrity_flags",
            "certificate_code", "results_released",
        ]
        # `TestAttemptViewSet` only mixes in Retrieve/List today (no create/
        # update action uses this serializer for writes — start/submit/review
        # all go through their own model methods), so none of these are
        # currently reachable as client input. Marked read-only anyway as
        # defence-in-depth: `series`/`roll_number`/`enrollment_no` are set
        # once from the `start` action's own snapshot, never patched.
        read_only_fields = fields

    def get_server_time(self, obj):
        return timezone.now()

    def get_certificate_code(self, obj):
        cert = getattr(obj, "certificate", None)  # reverse OneToOne: missing -> AttributeError subclass
        return cert.code if cert is not None and cert.is_valid else None

    def get_results_released(self, obj):
        return obj.series.results_visible()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_creator = bool(user and instance.series.creator_id == getattr(user, "id", None))
        is_owner = bool(user and instance.student_id == getattr(user, "id", None))
        # Result-release policy (instant / after the window ends / manual).
        # Only the OWNING STUDENT is held back — the creator and reviewers
        # always see everything. An in-progress attempt has no result yet.
        if (
            is_owner and not is_creator
            and instance.status != TestAttempt.Status.IN_PROGRESS
            and not instance.series.results_visible()
        ):
            for key in ("auto_score", "final_score", "percentage", "passed", "certificate_code"):
                data[key] = None
            data["responses"] = []
        return data


class TestAttemptStartSerializer(serializers.Serializer):
    """Task 28. Used by `TestAttemptViewSet.start()` (views.py) —
    NOT a `ModelSerializer`: the row itself is still created via
    `TestSeriesPurchase.purchase_and_start_attempt()` (paid) or
    `TestAttempt.objects.create()` (free) in the view, exactly as
    before this task. This serializer has two narrower jobs:

    1. Normalize the `roll_number`/`enrollment_no` snapshot the view
       used to build by hand off `request.data` (same clip-to-30-chars
       behavior as before, just declared as real fields instead of
       inline dict-building).
    2. Enforce the `attempts_allowed` cap (`TestSeries`) as a clean 400
       *before* either creation path runs, instead of only surfacing
       it later as a raw `IntegrityError` off the (series, student,
       attempt_number) constraint — which wouldn't even catch it
       correctly, since exceeding the cap with a fresh attempt_number
       doesn't collide with anything.

    `series`/`student` are passed in via context (never client input)
    — same "provenance resolved server-side" pattern
    `TestSeriesReviewSerializer.validate()` below already uses for
    `series`. The count check here is a good-faith pre-check, not the
    sole guarantee against a same-student double-submit race — the
    view still recomputes `attempt_number` right before creating the
    row (and, for the paid path, `purchase_and_start_attempt()`
    recomputes it again under a row lock), so a genuine race loses to
    the unique constraint and is handled there, not here.
    """

    roll_number = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    enrollment_no = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        series = self.context["series"]
        student = self.context["student"]
        used = TestAttempt.objects.filter(series=series, student=student).count()
        if used >= series.attempts_allowed:
            raise serializers.ValidationError(
                f"You have already used all {series.attempts_allowed} attempt(s) allowed for this test series."
            )
        return attrs


class TestSeriesReviewSerializer(serializers.ModelSerializer):
    """Task 15. `series`/`student`/`attempt` are all resolved server-side
    in `validate()` below (never client-writable — a student sends only
    `rating`/`comment`), same "provenance fields are read-only, set by
    the server" reasoning `TestSeriesSerializer` already uses for
    `source`/`context_type`/`context_id`.

    `rating` is declared explicitly (rather than left to the default
    ModelSerializer mapping from `PositiveSmallIntegerField`) so an
    out-of-range value comes back as a clean DRF 400 from field-level
    validation, instead of surfacing later as a `DjangoValidationError`
    out of `TestSeriesReview.full_clean()` inside `create()`.
    """

    student = serializers.PrimaryKeyRelatedField(read_only=True)
    series = serializers.PrimaryKeyRelatedField(read_only=True)
    rating = serializers.IntegerField(min_value=1, max_value=5)

    class Meta:
        model = TestSeriesReview
        fields = ["id", "series", "student", "attempt", "rating", "comment", "created_at", "updated_at"]
        read_only_fields = ["id", "series", "student", "attempt", "created_at", "updated_at"]

    def validate(self, attrs):
        # `series` is put in context by TestSeriesReviewViewSet.
        # get_serializer_context() — only present on the nested
        # `/testseries/{series_pk}/reviews/` route, which is the only
        # route `create()` is ever reachable from (see urls.py).
        series = self.context.get("series")
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if series is None or not (user and user.is_authenticated):
            raise serializers.ValidationError("Reviews must be created via a specific series' reviews endpoint.")

        # This is the acceptance-checklist's core rule: an unchecked (or
        # nonexistent) attempt is a clean 400, not a 403 — see
        # permissions.CanReviewCheckedAttempt's docstring for why that
        # split exists.
        attempt = TestAttempt.objects.filter(series=series, student=user).order_by("-attempt_number").first()
        if attempt is None:
            raise serializers.ValidationError("You must attempt this series before reviewing it.")
        if attempt.status != TestAttempt.Status.CHECKED:
            raise serializers.ValidationError(
                "You can only review this series after your attempt has been fully checked."
            )
        if TestSeriesReview.objects.filter(series=series, student=user).exists():
            raise serializers.ValidationError("You have already reviewed this series.")

        attrs["series"] = series
        attrs["student"] = user
        attrs["attempt"] = attempt
        return attrs

    def create(self, validated_data):
        try:
            return TestSeriesReview.create_review(
                attempt=validated_data["attempt"],
                rating=validated_data["rating"],
                comment=validated_data.get("comment", ""),
            )
        except DjangoValidationError as exc:
            # Defence-in-depth: TestSeriesReview.clean() re-checks the
            # same status/uniqueness rules validate() above already
            # checked — this only fires if that state changed in the
            # gap between validate() and create() (a genuine race, not
            # the common case), same shape QuestionSerializer.validate()
            # already uses to convert a model-layer ValidationError into
            # a clean DRF one instead of a raw 500.
            raise serializers.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)


# =====================================================================
# ADVANCED FEATURES
# =====================================================================
class ProgressSaveSerializer(serializers.Serializer):
    """`PATCH /attempts/{id}/save/` — server-side autosave of in-progress
    answers. Same `{question_id: answer_data}` shape as `submit`."""

    MAX_BYTES = 512 * 1024

    answers = serializers.DictField(child=serializers.JSONField())

    def validate_answers(self, value):
        if len(json.dumps(value, separators=(",", ":"))) > self.MAX_BYTES:
            raise serializers.ValidationError("Autosave payload is too large.")
        return value


class ProctorEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestProctorEvent
        fields = ["id", "event_type", "occurred_at", "meta", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"occurred_at": {"required": False}, "meta": {"required": False}}

    def validate_meta(self, value):
        if not isinstance(value, dict) or len(json.dumps(value)) > 2048:
            raise serializers.ValidationError("meta must be a small JSON object (max 2 KB).")
        return value

    def validate_occurred_at(self, value):
        # A client clock can't claim the future (or years ago) to game a review.
        now = timezone.now()
        if value > now + timedelta(minutes=5):
            return now
        return value


class TestRecordingSerializer(serializers.ModelSerializer):
    class Meta:
        model = TestRecording
        fields = [
            "id", "series", "attempt", "kind", "status", "url",
            "started_at", "ended_at", "duration_seconds",
        ]
        read_only_fields = fields


class TestCertificateSerializer(serializers.ModelSerializer):
    """Owner's / creator's view of a certificate."""

    series_title = serializers.CharField(source="series.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    is_valid = serializers.BooleanField(read_only=True)
    verify_path = serializers.SerializerMethodField()

    class Meta:
        model = TestCertificate
        fields = [
            "id", "code", "title", "series", "series_title", "student", "student_name",
            "score", "total_marks", "percentage", "issued_at", "is_valid", "revoked_reason", "verify_path",
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        return display_name(obj.student)

    def get_verify_path(self, obj):
        return f"/testseries/certificates/verify/{obj.code}/"


class CertificateVerifySerializer(serializers.ModelSerializer):
    """PUBLIC (unauthenticated) verification result — deliberately minimal:
    enough for an employer to confirm authenticity, nothing that identifies a
    student beyond the name printed on the certificate."""

    series_title = serializers.CharField(source="series.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    valid = serializers.BooleanField(source="is_valid", read_only=True)

    class Meta:
        model = TestCertificate
        fields = [
            "code", "title", "series_title", "student_name", "score", "total_marks",
            "percentage", "issued_at", "valid", "revoked_reason",
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        return display_name(obj.student)


class PublicSeriesSerializer(serializers.ModelSerializer):
    """PUBLIC preview behind a share link. Marketing info only: no questions,
    no options, no answers — the same information the list card shows."""

    creator_name = serializers.SerializerMethodField()
    avg_rating = serializers.FloatField(read_only=True)
    review_count = serializers.IntegerField(read_only=True)
    question_count = serializers.SerializerMethodField()
    window_state = serializers.SerializerMethodField()

    class Meta:
        model = TestSeries
        fields = [
            "id", "title", "description", "source", "is_paid", "price_coins", "duration_minutes",
            "total_marks", "attempts_allowed", "question_count", "delivery_mode", "starts_at", "ends_at",
            "window_state", "pass_percentage", "certificate_enabled", "certificate_title",
            "proctoring", "avg_rating", "review_count", "creator_name", "share_slug",
        ]
        read_only_fields = fields

    def get_creator_name(self, obj):
        return display_name(obj.creator)

    def get_question_count(self, obj):
        annotated = getattr(obj, "q_count", None)
        return annotated if annotated is not None else obj.questions.count()

    def get_window_state(self, obj):
        return obj.window_state()

