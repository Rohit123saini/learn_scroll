# assignment/serializers.py
"""
DRF serializers for the public-facing (personal-assignment) API surface,
plus the shared submission/answer/public-page serializers that campus's
and liveclass's own thin-proxy viewsets (§5.2, §6.1 — not in this app)
would reuse rather than re-declare.

Split into "input" (plain `Serializer`, validation-only, no `.save()`
responsibility of their own — the model method they front does the
actual write) and "output" (`ModelSerializer`) shapes throughout, mirroring
how `AssignmentSubmission`'s own methods (`submit_freeform`,
`submit_structured`, `grade_freeform`, `mark_answer_and_maybe_finalize`)
are already the real unit of business logic — these serializers exist to
validate the HTTP boundary, not to duplicate that logic.

REFRESHED for Task 7's real (fixed) `assignment/models.py` — three things
changed here versus the version originally handed off with Task 8:
  1. `correct_answer` is no longer blanket `write_only`. Task 8's own
     checklist requires it visible to `posted_by`/staff (testseries's own
     hiding rule — hidden from students only), which a global write_only
     flag can never express; visibility is now decided per-request in
     `AssignmentQuestionSerializer.to_representation()` instead.
  2. `AssignmentQuestionSerializer.validate()` now covers the `list`
     question type too (previously only mcq/msq), mirroring
     `AssignmentQuestion.clean()`'s full per-type shape rules verbatim.
     Without this, a bad `list`-shaped payload would sail past this
     serializer's validation, reach `AssignmentQuestion.objects.create()`
     inside `AssignmentCreateSerializer.create()` (which bypasses this
     serializer's own `create()`/`update()` entirely — see that method),
     and blow up as an unhandled `django.core.exceptions.ValidationError`
     (a raw 500) instead of a clean 400, because Task 7 added real
     `clean()`/`full_clean()` enforcement at the model layer that didn't
     exist when this file was first written.
  3. `AssignmentCreateSerializer.create()`/`update()` now also catch that
     same Django `ValidationError` as a belt-and-suspenders fallback (in
     case the two validation copies above ever drift) and re-raise it as
     a DRF `serializers.ValidationError`, and `create()` is wrapped in
     `transaction.atomic()` so a mid-loop question failure can't leave an
     `Assignment` row committed with only some of its questions created.
  4. `AssignmentAnswerSerializer` / `StructuredAnswerInputSerializer` now
     include `answer_attachment`, matching `AssignmentAnswer.
     answer_attachment` — a field Task 7 added that didn't exist in the
     model this file was originally written against.
"""
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .models import (
    Assignment,
    AssignmentAnswer,
    AssignmentQuestion,
    AssignmentSource,
    AssignmentSubmission,
)


class AssignmentQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssignmentQuestion
        fields = ["id", "order", "question_type", "text", "attachment", "marks", "options", "correct_answer"]
        # NOTE: `correct_answer` is intentionally NOT `write_only` here
        # (see module docstring point 1) — it's a normal readable/
        # writable field, and visibility is enforced per-request in
        # `to_representation()` below instead of blanket-hidden from
        # everyone including the assignment's own poster.

    def validate(self, attrs):
        """Verbatim port of `AssignmentQuestion.clean()`'s per-type shape
        rules (see that method's own docstring on why it's a straight
        port rather than a paraphrase) — this copy exists purely so a bad
        payload gets a clean 400 at the HTTP boundary instead of reaching
        the model layer's `full_clean()` and surfacing as an unhandled
        `django.core.exceptions.ValidationError`. `AssignmentCreateSerializer.
        create()` below still wraps the model call in a try/except as a
        second layer, in case this copy and the model's ever drift.
        """
        qtype = attrs.get("question_type", getattr(self.instance, "question_type", None))
        options = attrs.get("options", getattr(self.instance, "options", None))
        correct = attrs.get("correct_answer", getattr(self.instance, "correct_answer", None)) or {}

        if qtype == AssignmentQuestion.QuestionTypeChoices.TEXT:
            # Model's clean() force-empties options/correct_answer for
            # `text` regardless of what was sent — nothing to validate.
            return attrs

        if qtype in (AssignmentQuestion.QuestionTypeChoices.MCQ, AssignmentQuestion.QuestionTypeChoices.MSQ):
            if not isinstance(options, list) or not options:
                raise serializers.ValidationError(
                    {"options": "mcq/msq questions require a non-empty `options` list."}
                )
            option_ids = {opt.get("id") for opt in options}
            if qtype == AssignmentQuestion.QuestionTypeChoices.MCQ:
                if "option_id" not in correct or correct["option_id"] not in option_ids:
                    raise serializers.ValidationError(
                        {"correct_answer": "mcq correct_answer must be {'option_id': <one of options[].id>}."}
                    )
            else:  # MSQ
                option_ids_answer = set(correct.get("option_ids", []))
                if not option_ids_answer or not option_ids_answer.issubset(option_ids):
                    raise serializers.ValidationError(
                        {"correct_answer": "msq correct_answer must be {'option_ids': [subset of options[].id]}."}
                    )
            return attrs

        if qtype == AssignmentQuestion.QuestionTypeChoices.LIST:
            mode = correct.get("list_mode")
            if mode == "match":
                left = options.get("left") if isinstance(options, dict) else None
                right = options.get("right") if isinstance(options, dict) else None
                pairs = correct.get("pairs")
                if not left or not right or not isinstance(pairs, dict):
                    raise serializers.ValidationError(
                        {
                            "correct_answer": (
                                "list/match questions require options={'left': [...], 'right': [...]} "
                                "and correct_answer={'list_mode': 'match', 'pairs': {left_id: right_id, ...}}."
                            )
                        }
                    )
            elif mode == "order":
                if not isinstance(options, list) or not options:
                    raise serializers.ValidationError(
                        {"options": "list/order questions require a non-empty `options` list."}
                    )
                sequence = correct.get("sequence")
                option_ids = {opt.get("id") for opt in options}
                if not isinstance(sequence, list) or set(sequence) != option_ids:
                    raise serializers.ValidationError(
                        {
                            "correct_answer": (
                                "list/order correct_answer must be {'list_mode': 'order', "
                                "'sequence': [every options[].id, in order]}."
                            )
                        }
                    )
            else:
                raise serializers.ValidationError(
                    {"correct_answer": "list questions require correct_answer['list_mode'] to be 'match' or 'order'."}
                )
            return attrs

        return attrs

    def to_representation(self, instance):
        """§7 / Task 8 checklist: `correct_answer` visible only to the
        assignment's `posted_by` or staff — never to the student
        answering it (same hiding rule testseries uses). Popped from the
        output rather than declared `write_only`, so the poster/staff
        actually get to see it (a blanket write_only would have hidden it
        from them too, which defeats "review/audit it" — see this
        model's own docstring on why `correct_answer` exists at all).

        `_assignment_posted_by_id` is an optional pre-seeded attribute:
        when this serializer is nested inside `AssignmentSerializer` /
        `AssignmentCreateSerializer` (its only real use in this app —
        see views.py), the parent sets it on this serializer's `child`
        before rendering so this method never has to run
        `instance.assignment` itself — `prefetch_related("questions")`
        on the assignment queryset does NOT cache each question's
        `.assignment` back-reference, so resolving it here directly would
        N+1 once per question on every list/retrieve. Falls back to the
        real (single, per-question) query only if used standalone.
        """
        rep = super().to_representation(instance)
        request = self.context.get("request")
        posted_by_id = getattr(self, "_assignment_posted_by_id", None)
        if posted_by_id is None:
            posted_by_id = instance.assignment.posted_by_id
        can_see_answer_key = bool(
            request
            and request.user
            and request.user.is_authenticated
            and (request.user.is_staff or posted_by_id == request.user.id)
        )
        if not can_see_answer_key:
            rep.pop("correct_answer", None)
        return rep


class AssignmentSerializer(serializers.ModelSerializer):
    """Read shape — used for list/retrieve."""

    questions = AssignmentQuestionSerializer(many=True, read_only=True)
    posted_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = Assignment
        fields = [
            "id", "source", "context_type", "context_id", "posted_by", "title",
            "description", "attachment", "due_date", "total_marks",
            "has_structured_questions", "data", "questions", "created_at", "updated_at",
        ]
        # source/context_type/context_id/posted_by/total_marks/data are all
        # either server-set or derived (total_marks via
        # recompute_total_marks(), data via bridge.py for context-sourced
        # assignments, which never go through this serializer at all).
        #
        # Task 8 fix: `source` was previously left OUT of this list, on the
        # theory that IsPersonalSourceOnly needed to see it in the raw
        # payload. That reasoning didn't hold up: IsPersonalSourceOnly
        # reads `request.data` directly (the raw dict DRF parsed off the
        # wire), not this serializer's `validated_data` — marking a field
        # read_only only strips it from validated_data, it has no effect
        # on `request.data` at all. So the permission check is unaffected
        # either way, and leaving `source` out of read_only_fields here was
        # just a gap against the Task 8 checklist ("source/context_type/
        # context_id read-only fields hain serializer me"), not a
        # requirement. This is also belt-and-suspenders, not the real
        # enforcement point: this serializer (AssignmentSerializer) is
        # only ever used for list/retrieve output (see
        # AssignmentViewSet.get_serializer_class) — create/update go
        # through AssignmentCreateSerializer below, which has no source/
        # context_type/context_id/posted_by field at all, so there's no
        # payload shape on the write path that could set any of these
        # regardless of what's marked read_only here.
        read_only_fields = ["source", "posted_by", "context_type", "context_id", "total_marks", "data"]

    def to_representation(self, instance):
        # See AssignmentQuestionSerializer.to_representation() — seed the
        # nested serializer's child with this assignment's posted_by_id
        # once, up front, so it never has to query `question.assignment`
        # itself for the correct_answer visibility check.
        self.fields["questions"].child._assignment_posted_by_id = instance.posted_by_id
        return super().to_representation(instance)


class AssignmentCreateSerializer(serializers.ModelSerializer):
    """§7 — the ONLY way to create an `Assignment` through the public API,
    and it is hard-wired to `source=personal` by
    `AssignmentViewSet.perform_create()`. Campus/liveclass assignments are
    created exclusively via `assignment.bridge.create_context_assignment()`
    from those apps' own already-permission-checked endpoints (§1, §7) —
    this serializer has no `source`/`context_type`/`context_id`/`posted_by`
    field at all, so there's no payload shape that could even attempt to
    smuggle one through.
    """

    questions = AssignmentQuestionSerializer(many=True, required=False)

    class Meta:
        model = Assignment
        fields = [
            "id", "title", "description", "attachment", "due_date",
            "total_marks", "has_structured_questions", "questions",
        ]

    def validate(self, attrs):
        has_structured = attrs.get(
            "has_structured_questions", getattr(self.instance, "has_structured_questions", False)
        )
        if has_structured and not attrs.get("questions") and not (self.instance and self.instance.questions.exists()):
            raise serializers.ValidationError(
                {"questions": "has_structured_questions=True requires at least one question."}
            )
        return attrs

    def validate_has_structured_questions(self, value):
        # §2a — "immutable after first submission exists". Duplicated
        # here (not just relying on `Assignment.save()`'s own model-level
        # guard, added in Task 7) so an illegal flip comes back as a
        # clean 400 on this specific field — the model-level guard is the
        # backstop for callers that bypass this serializer entirely, not
        # the primary UX for this one.
        if self.instance and value != self.instance.has_structured_questions:
            if not self.instance.can_change_question_mode():
                raise serializers.ValidationError(
                    "has_structured_questions cannot change once a submission exists for this assignment."
                )
        return value

    @transaction.atomic
    def create(self, validated_data):
        # Atomic: without this, a mid-loop AssignmentQuestion validation
        # failure (caught below) would still leave the Assignment row and
        # any already-created questions committed — a half-built
        # assignment with no way for the client to know it's incomplete.
        questions_data = validated_data.pop("questions", [])
        try:
            assignment = Assignment.objects.create(**validated_data)
            for question_data in questions_data:
                AssignmentQuestion.objects.create(assignment=assignment, **question_data)
        except DjangoValidationError as exc:
            # Belt-and-suspenders (see module docstring point 3): this
            # serializer's own validate()/AssignmentQuestionSerializer.
            # validate() should catch every bad shape before this line
            # ever runs, but the model's full_clean() (Task 7) runs the
            # same checks again — if the two ever drift, surface a clean
            # 400 here instead of an unhandled 500.
            raise serializers.ValidationError({"detail": exc.messages})
        return assignment

    def update(self, instance, validated_data):
        # Nested `questions` are intentionally NOT handled here on update
        # — mutating an existing question set (add/remove/reorder) once
        # answers may already reference those questions is exactly the
        # scoring-inconsistency problem §2a's immutability rule exists to
        # prevent. Question edits go through AssignmentQuestionSerializer
        # directly (e.g. a dedicated question sub-resource), never through
        # this Assignment-level update.
        validated_data.pop("questions", None)
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            # Same belt-and-suspenders reasoning as create() — this is
            # where Assignment.save()'s own has_structured_questions
            # immutability guard (Task 7) would surface if
            # validate_has_structured_questions() above ever missed a
            # case.
            raise serializers.ValidationError({"detail": exc.messages})


class AssignmentAnswerSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source="question.text", read_only=True)
    question_marks = serializers.IntegerField(source="question.marks", read_only=True)

    class Meta:
        model = AssignmentAnswer
        fields = [
            "id", "question", "question_text", "question_marks", "answer_data",
            # `answer_attachment` — added in Task 7 (mirrors
            # `QuestionResponse.answer_attachment`; was missing from the
            # model entirely before that fix, so it was never here either).
            "answer_attachment",
            "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback",
            "reviewed_by", "reviewed_at",
        ]
        read_only_fields = [
            "is_auto_graded", "is_correct", "marks_awarded", "reviewer_feedback",
            "reviewed_by", "reviewed_at",
        ]


class AssignmentSubmissionSerializer(serializers.ModelSerializer):
    answers = AssignmentAnswerSerializer(many=True, read_only=True)
    student = serializers.StringRelatedField(read_only=True)
    is_late = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentSubmission
        fields = [
            "id", "assignment", "student", "written_content", "file",
            "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "feedback", "public_slug",
            "submitted_at", "checked_at", "answers", "is_late",
        ]
        # Every field here except the assignment FK itself is either a
        # roster-time snapshot (roll_number/enrollment_no — set by
        # bridge.py, never by the student), or only ever changed through
        # a dedicated model method (submit_freeform/submit_structured/
        # grade_freeform/publish/unpublish/mark_answer_and_maybe_finalize)
        # fronted by its own action below — never a bare PATCH on this
        # serializer.
        #
        # `assignment` is deliberately NOT in this list — it has to stay
        # writable so a student can name which assignment they're
        # submitting for on create(). See validate() below for why that
        # doesn't mean it's writable on update() too.
        read_only_fields = [
            "student", "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "public_slug", "submitted_at", "checked_at", "answers",
        ]

    def get_is_late(self, obj) -> bool:
        return obj.is_late()

    def validate_assignment(self, assignment):
        """Task 8 fix — two real gaps, both against §2's own "personal-
        assignment flow: there's no bridge-created roster row ... the
        student's own first interaction creates the AssignmentSubmission
        row directly" (see AssignmentSubmissionViewSet.perform_create's
        docstring, and the model's own module docstring point 4):

        1. IMMUTABILITY ON UPDATE — `assignment` was writable on both
           create AND update (Meta.read_only_fields never listed it,
           because it legitimately has to be settable at create time).
           Without this check, a student could PATCH their own
           submission and silently re-point it at a *different*
           assignment post-creation — `unique_submission_per_student`
           does not catch this because the (assignment, student) pair
           genuinely changes to a new, not-yet-used pair. Blocked here:
           once `self.instance` exists, this field may not change.

        2. SOURCE RESTRICTION ON CREATE — §2 says a personal-assignment
           submission is the ONLY case where create() is the real entry
           point; a campus/liveclass-sourced assignment already has its
           AssignmentSubmission row (status=MISSING) created by
           `bridge.create_context_assignment()` at roster time, and
           students there are only ever meant to reach `submit_freeform`/
           `submit_structured` on that existing row. Nothing previously
           stopped a client from POSTing straight to this viewset's
           create() with a campus/liveclass assignment's id — best case
           that hits `unique_submission_per_student` and surfaces as a
           raw, uncaught `IntegrityError` (500); worst case (assignment
           has no roster row yet, e.g. a race with the bridge call) it
           silently creates a submission outside the roster flow
           entirely, with no roll_number/enrollment_no snapshot. Blocked
           here at the source instead: create() only accepts
           source=personal assignments.
        """
        if self.instance is not None and assignment.id != self.instance.assignment_id:
            raise serializers.ValidationError(
                "assignment cannot be changed once a submission has been created."
            )
        if self.instance is None and assignment.source != AssignmentSource.PERSONAL:
            raise serializers.ValidationError(
                "Submissions can only be created directly for personal assignments. "
                "Campus/liveclass submissions are created automatically when the "
                "assignment is posted — use submit_freeform/submit_structured on "
                "the existing submission instead."
            )
        return assignment

    def create(self, validated_data):
        # Belt-and-suspenders alongside validate_assignment() above: two
        # concurrent create() calls for the same (assignment, student)
        # could both pass validate_assignment()'s checks before either
        # write lands (no row lock at the serializer layer), and the
        # second one would previously surface `unique_submission_per_
        # student`'s violation as a raw, unhandled IntegrityError — a
        # 500 for what is, from the client's point of view, an ordinary
        # "you already submitted this" case. Turned into a clean 400
        # instead, matching how AssignmentCreateSerializer.create()
        # above already turns a model-layer error into
        # serializers.ValidationError rather than letting it bubble raw.
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                {"detail": "A submission for this assignment already exists."}
            )


class PublicSubmissionSerializer(serializers.ModelSerializer):
    """§2 'Shareable URL — kaise kaam karta hai'. Auth-free, read-only,
    and deliberately narrower than `AssignmentSubmissionSerializer` — no
    internal ids beyond what the public page needs, no reviewer identity,
    no `feedback`/per-question `reviewer_feedback` distinction hidden
    behind extra fields the frontend doesn't need for this view."""

    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    breakdown = serializers.SerializerMethodField()

    class Meta:
        model = AssignmentSubmission
        fields = [
            "assignment_title", "student_name", "roll_number", "enrollment_no",
            "submitted_at", "status", "written_content", "file", "grade",
            "total_marks_awarded", "breakdown",
        ]

    def get_student_name(self, obj) -> str:
        return obj.student.get_full_name() or obj.student.username

    def get_breakdown(self, obj):
        """Only populated for the structured path (§2: "structured →
        per-question list ... + total_marks_awarded"). Free-form
        submissions get `None` here and rely on `written_content`/`file`/
        `grade` instead — same shape the internal review UI uses, just
        read-only."""
        if not obj.assignment.has_structured_questions:
            return None
        return [
            {
                "question": answer.question.text,
                "marks": answer.question.marks,
                "marks_awarded": answer.marks_awarded,
                "reviewer_feedback": answer.reviewer_feedback,
            }
            for answer in obj.answers.select_related("question").order_by("question__order")
        ]


# --- Plain input serializers for the action endpoints in views.py — no
# model binding of their own, since the model method they front (see each
# docstring) is the actual source of truth for what happens on save. ---

class FreeformSubmitSerializer(serializers.Serializer):
    written_content = serializers.CharField(required=False, allow_blank=True, default="")
    file = serializers.FileField(required=False)


class StructuredAnswerInputSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    answer_data = serializers.JSONField()
    # Matches `AssignmentAnswer.answer_attachment` (Task 7 — mirrors
    # `QuestionResponse.answer_attachment`, missing entirely before that
    # fix). NOTE: actually wiring a real file upload through here is a
    # views.py concern (Task 9, out of scope for this file) — DRF can't
    # cleanly bind one file to one entry inside a `many=True` nested list
    # from a single multipart request the way `TestAttempt.submit(files=
    # request.FILES)` does it (matched by a `f"answer_{question_id}"` key
    # instead, at the view layer). This field documents the shape and
    # supports a pre-uploaded-reference/base64 flow; the view is expected
    # to follow the same `files=` merge pattern testseries uses for true
    # multipart uploads before calling `submit_structured()`.
    answer_attachment = serializers.FileField(required=False, allow_null=True)


class StructuredSubmitSerializer(serializers.Serializer):
    answers = StructuredAnswerInputSerializer(many=True)


class GradeFreeformSerializer(serializers.Serializer):
    """§7 — 'Free-form path: PATCH {id}/grade/ (grade, feedback)'."""

    grade = serializers.CharField(max_length=10)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")


class AnswerReviewSerializer(serializers.Serializer):
    """§7 — 'Structured path: POST {id}/answer/{question_id}/review/'."""

    marks_awarded = serializers.IntegerField(min_value=0)
    feedback = serializers.CharField(required=False, allow_blank=True, default="")