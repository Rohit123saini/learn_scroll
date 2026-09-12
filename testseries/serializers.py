# testseries/serializers.py
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import (
    Question, QuestionResponse, TestAttempt, TestSeries, TestSeriesPurchase,
    attachment_extension_validator, validate_attachment_size,
)


class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Question
        fields = [
            "id", "series", "order", "question_type", "text",
            "attachment", "marks", "options", "correct_answer",
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
        if not (user and getattr(user, "is_authenticated", False) and instance.series.creator_id == user.id):
            data.pop("correct_answer", None)
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
            "marks_awarded", "reviewer_feedback", "reviewed_by", "reviewed_at",
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


class TestSeriesSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True, read_only=True)
    creator = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = TestSeries
        fields = [
            "id", "source", "context_type", "context_id", "creator", "title",
            "description", "is_paid", "price_coins", "duration_minutes",
            "total_marks", "status", "attempts_allowed", "questions",
            "created_at", "updated_at",
        ]
        # `source`/`context_type`/`context_id` are provenance — set once at
        # creation (INDIVIDUAL here, or CAMPUS/LIVECLASS via bridge.py) and
        # never client-writable afterward, or a creator could PATCH their own
        # individual series into impersonating a campus/liveclass context it
        # was never actually created through. `status` is read-only too —
        # the only supported transition (draft -> published) has real
        # preconditions (has questions) and side effects (total_marks
        # recompute) that live in the `publish` action; allowing a direct
        # PATCH here would let a creator skip both.
        read_only_fields = [
            "id", "creator", "source", "context_type", "context_id",
            "status", "total_marks", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        # is_paid=False => price_coins must be 0. TestSeries.save()
        # already normalizes this silently, but surfacing it here gives
        # the client an honest 400 instead of a silent rewrite of what
        # they submitted.
        is_paid = attrs.get("is_paid", getattr(self.instance, "is_paid", False))
        price_coins = attrs.get("price_coins", getattr(self.instance, "price_coins", 0))
        if not is_paid and price_coins:
            raise serializers.ValidationError({"price_coins": "price_coins must be 0 when is_paid is False."})

        # §5 defence-in-depth at the serializer layer too, not just
        # TestSeries.save() — campus series are never paid, no matter
        # what a (misbehaving) client sends to this endpoint.
        source = attrs.get("source", getattr(self.instance, "source", None))
        if source == TestSeries.Source.CAMPUS and is_paid:
            raise serializers.ValidationError({"is_paid": "Campus test series must always be free."})
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

    class Meta:
        model = TestAttempt
        fields = [
            "id", "series", "student", "attempt_number", "auto_score",
            "final_score", "status", "checked_by", "roll_number",
            "enrollment_no", "submitted_at", "checked_at", "responses",
        ]
        # `TestAttemptViewSet` only mixes in Retrieve/List today (no create/
        # update action uses this serializer for writes — start/submit/review
        # all go through their own model methods), so none of these are
        # currently reachable as client input. Marked read-only anyway as
        # defence-in-depth: `series`/`roll_number`/`enrollment_no` are set
        # once from the `start` action's own snapshot, never patched.
        read_only_fields = [
            "id", "series", "student", "attempt_number", "auto_score", "final_score",
            "status", "checked_by", "roll_number", "enrollment_no", "submitted_at", "checked_at",
        ]