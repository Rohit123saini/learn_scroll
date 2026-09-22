# assigments/serializers.py
"""
DRF serializers for the public-facing (personal-assigments) API surface,
plus the shared submission/answer/public-page serializers that campus's
and liveclass's own thin-proxy viewsets (§5.2, §6.1 — not in this app)
would reuse rather than re-declare.

Split into "input" (plain `Serializer`, validation-only, no `.save()`
responsibility of their own — the model method they front does the
actual write) and "output" (`ModelSerializer`) shapes throughout, mirroring
how `assigmentsSubmission`'s own methods (`submit_freeform`,
`submit_structured`, `grade_freeform`, `mark_answer_and_maybe_finalize`)
are already the real unit of business logic — these serializers exist to
validate the HTTP boundary, not to duplicate that logic.

REFRESHED for Task 7's real (fixed) `assigments/models.py` — three things
changed here versus the version originally handed off with Task 8:
  1. `correct_answer` is no longer blanket `write_only`. Task 8's own
     checklist requires it visible to `posted_by`/staff (testseries's own
     hiding rule — hidden from students only), which a global write_only
     flag can never express; visibility is now decided per-request in
     `assigmentsQuestionSerializer.to_representation()` instead.
  2. `assigmentsQuestionSerializer.validate()` now covers the `list`
     question type too (previously only mcq/msq), mirroring
     `assigmentsQuestion.clean()`'s full per-type shape rules verbatim.
     Without this, a bad `list`-shaped payload would sail past this
     serializer's validation, reach `assigmentsQuestion.objects.create()`
     inside `assigmentsCreateSerializer.create()` (which bypasses this
     serializer's own `create()`/`update()` entirely — see that method),
     and blow up as an unhandled `django.core.exceptions.ValidationError`
     (a raw 500) instead of a clean 400, because Task 7 added real
     `clean()`/`full_clean()` enforcement at the model layer that didn't
     exist when this file was first written.
  3. `assigmentsCreateSerializer.create()`/`update()` now also catch that
     same Django `ValidationError` as a belt-and-suspenders fallback (in
     case the two validation copies above ever drift) and re-raise it as
     a DRF `serializers.ValidationError`, and `create()` is wrapped in
     `transaction.atomic()` so a mid-loop question failure can't leave an
     `assigments` row committed with only some of its questions created.
  4. `assigmentsAnswerSerializer` / `StructuredAnswerInputSerializer` now
     include `answer_attachment`, matching `assigmentsAnswer.
     answer_attachment` — a field Task 7 added that didn't exist in the
     model this file was originally written against.
"""
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from rest_framework import serializers

from .models import (
    MAX_RUBRIC_CRITERIA,
    MAX_TAGS,
    SUBMISSION_TYPES,
    assigments,
    assigmentsAnswer,
    assigmentsKind,
    assigmentsQuestion,
    assigmentsSource,
    assigmentsSubmission,
)


def _person_name(user) -> str:
    if user is None:
        return ""
    return (getattr(user, "get_full_name", lambda: "")() or getattr(user, "username", "") or "").strip()


def _share_url(obj, request):
    if not obj.public_slug:
        return None
    template = getattr(settings, "ASSIGNMENTS_SHARE_URL_TEMPLATE", "")
    if template:
        return template.format(slug=obj.public_slug)
    path = f"/assigments/p/{obj.public_slug}/"
    return request.build_absolute_uri(path) if request else path


def validate_tags(value):
    if not isinstance(value, list):
        raise serializers.ValidationError("tags must be a list of strings.")
    clean = []
    for tag in value:
        if not isinstance(tag, str) or not tag.strip():
            raise serializers.ValidationError("Every tag must be a non-empty string.")
        tag = tag.strip().lower()
        if len(tag) > 30:
            raise serializers.ValidationError("A tag can be at most 30 characters.")
        if tag not in clean:
            clean.append(tag)
    if len(clean) > MAX_TAGS:
        raise serializers.ValidationError(f"At most {MAX_TAGS} tags.")
    return clean


def validate_rubric(value):
    if not isinstance(value, list):
        raise serializers.ValidationError("rubric must be a list of {criterion, max_marks}.")
    if len(value) > MAX_RUBRIC_CRITERIA:
        raise serializers.ValidationError(f"At most {MAX_RUBRIC_CRITERIA} rubric criteria.")
    seen, clean = set(), []
    for item in value:
        if not isinstance(item, dict):
            raise serializers.ValidationError("Each rubric entry must be an object.")
        name = str(item.get("criterion", "")).strip()
        try:
            max_marks = int(item.get("max_marks"))
        except (TypeError, ValueError):
            raise serializers.ValidationError(f"max_marks for {name or 'a criterion'} must be a whole number.")
        if not name or len(name) > 80:
            raise serializers.ValidationError("criterion must be 1-80 characters.")
        if not 1 <= max_marks <= 100:
            raise serializers.ValidationError(f"max_marks for {name!r} must be between 1 and 100.")
        if name.lower() in seen:
            raise serializers.ValidationError(f"Duplicate criterion {name!r}.")
        seen.add(name.lower())
        clean.append({"criterion": name, "max_marks": max_marks})
    return clean


def validate_submission_types(value):
    if not isinstance(value, list) or any(v not in SUBMISSION_TYPES for v in value):
        raise serializers.ValidationError(f"submission_types must be a list drawn from {list(SUBMISSION_TYPES)}.")
    return list(dict.fromkeys(value))


class assigmentsQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = assigmentsQuestion
        fields = ["id", "order", "question_type", "text", "attachment", "marks", "options", "correct_answer"]
        # NOTE: `correct_answer` is intentionally NOT `write_only` here
        # (see module docstring point 1) — it's a normal readable/
        # writable field, and visibility is enforced per-request in
        # `to_representation()` below instead of blanket-hidden from
        # everyone including the assigments's own poster.

    def validate(self, attrs):
        """Verbatim port of `assigmentsQuestion.clean()`'s per-type shape
        rules (see that method's own docstring on why it's a straight
        port rather than a paraphrase) — this copy exists purely so a bad
        payload gets a clean 400 at the HTTP boundary instead of reaching
        the model layer's `full_clean()` and surfacing as an unhandled
        `django.core.exceptions.ValidationError`. `assigmentsCreateSerializer.
        create()` below still wraps the model call in a try/except as a
        second layer, in case this copy and the model's ever drift.
        """
        qtype = attrs.get("question_type", getattr(self.instance, "question_type", None))
        options = attrs.get("options", getattr(self.instance, "options", None))
        correct = attrs.get("correct_answer", getattr(self.instance, "correct_answer", None)) or {}

        if qtype == assigmentsQuestion.QuestionTypeChoices.TEXT:
            # Model's clean() force-empties options/correct_answer for
            # `text` regardless of what was sent — nothing to validate.
            return attrs

        if qtype in (assigmentsQuestion.QuestionTypeChoices.MCQ, assigmentsQuestion.QuestionTypeChoices.MSQ):
            if not isinstance(options, list) or not options:
                raise serializers.ValidationError(
                    {"options": "mcq/msq questions require a non-empty `options` list."}
                )
            option_ids = {opt.get("id") for opt in options}
            if qtype == assigmentsQuestion.QuestionTypeChoices.MCQ:
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

        if qtype == assigmentsQuestion.QuestionTypeChoices.LIST:
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
        assigments's `posted_by` or staff — never to the student
        answering it (same hiding rule testseries uses). Popped from the
        output rather than declared `write_only`, so the poster/staff
        actually get to see it (a blanket write_only would have hidden it
        from them too, which defeats "review/audit it" — see this
        model's own docstring on why `correct_answer` exists at all).

        `_assigments_posted_by_id` is an optional pre-seeded attribute:
        when this serializer is nested inside `assigmentsSerializer` /
        `assigmentsCreateSerializer` (its only real use in this app —
        see views.py), the parent sets it on this serializer's `child`
        before rendering so this method never has to run
        `instance.assigments` itself — `prefetch_related("questions")`
        on the assigments queryset does NOT cache each question's
        `.assigments` back-reference, so resolving it here directly would
        N+1 once per question on every list/retrieve. Falls back to the
        real (single, per-question) query only if used standalone.
        """
        rep = super().to_representation(instance)
        request = self.context.get("request")
        posted_by_id = getattr(self, "_assigments_posted_by_id", None)
        if posted_by_id is None:
            posted_by_id = instance.assigments.posted_by_id
        can_see_answer_key = bool(
            request
            and request.user
            and request.user.is_authenticated
            and (request.user.is_staff or posted_by_id == request.user.id)
        )
        if not can_see_answer_key:
            rep.pop("correct_answer", None)
        return rep


class assigmentsSerializer(serializers.ModelSerializer):
    """Read shape — used for list/retrieve."""

    questions = assigmentsQuestionSerializer(many=True, read_only=True)
    posted_by = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = assigments
        fields = [
            "id", "source", "context_type", "context_id", "posted_by", "title",
            "description", "attachment", "due_date", "total_marks",
            "has_structured_questions", "data", "questions", "created_at", "updated_at",
            # ---- publishing + projects (migration 0003)
            "kind", "status", "visibility", "public_slug", "share_url", "published_at",
            "tags", "difficulty", "submission_types", "rubric", "participants_count",
        ]
        # source/context_type/context_id/posted_by/total_marks/data are all
        # either server-set or derived (total_marks via
        # recompute_total_marks(), data via bridge.py for context-sourced
        # assigmentss, which never go through this serializer at all).
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
        # enforcement point: this serializer (assigmentsSerializer) is
        # only ever used for list/retrieve output (see
        # assigmentsViewSet.get_serializer_class) — create/update go
        # through assigmentsCreateSerializer below, which has no source/
        # context_type/context_id/posted_by field at all, so there's no
        # payload shape on the write path that could set any of these
        # regardless of what's marked read_only here.
        read_only_fields = [
            "source", "posted_by", "context_type", "context_id", "total_marks", "data",
            "status", "visibility", "public_slug", "published_at",
        ]

    participants_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()

    def get_participants_count(self, obj) -> int:
        annotated = getattr(obj, "n_participants", None)
        return annotated if annotated is not None else obj.submissions.count()

    def get_share_url(self, obj):
        return _share_url(obj, self.context.get("request"))

    def to_representation(self, instance):
        # See assigmentsQuestionSerializer.to_representation() — seed the
        # nested serializer's child with this assigments's posted_by_id
        # once, up front, so it never has to query `question.assigments`
        # itself for the correct_answer visibility check.
        self.fields["questions"].child._assigments_posted_by_id = instance.posted_by_id
        return super().to_representation(instance)


class assigmentsCreateSerializer(serializers.ModelSerializer):
    """§7 — the ONLY way to create an `assigments` through the public API,
    and it is hard-wired to `source=personal` by
    `assigmentsViewSet.perform_create()`. Campus/liveclass assigmentss are
    created exclusively via `assigments.bridge.create_context_assigments()`
    from those apps' own already-permission-checked endpoints (§1, §7) —
    this serializer has no `source`/`context_type`/`context_id`/`posted_by`
    field at all, so there's no payload shape that could even attempt to
    smuggle one through.
    """

    questions = assigmentsQuestionSerializer(many=True, required=False)

    class Meta:
        model = assigments
        fields = [
            "id", "title", "description", "attachment", "due_date",
            "total_marks", "has_structured_questions", "questions",
            # ---- projects (migration 0003). Publishing itself (status /
            # visibility / slug) is NOT writable here — it goes through the
            # `publish` action, which checks the assignment is actually ready.
            "kind", "tags", "difficulty", "submission_types", "rubric",
        ]

    def validate_tags(self, value):
        return validate_tags(value)

    def validate_rubric(self, value):
        return validate_rubric(value)

    def validate_submission_types(self, value):
        return validate_submission_types(value)

    def validate(self, attrs):
        has_structured = attrs.get(
            "has_structured_questions", getattr(self.instance, "has_structured_questions", False)
        )
        if has_structured and not attrs.get("questions") and not (self.instance and self.instance.questions.exists()):
            raise serializers.ValidationError(
                {"questions": "has_structured_questions=True requires at least one question."}
            )

        # ---- projects / rubric
        rubric = attrs.get("rubric", getattr(self.instance, "rubric", []))
        if rubric and has_structured:
            raise serializers.ValidationError(
                {"rubric": "A rubric grades free-form hand-ins; it can't be combined with structured questions."}
            )
        kind = attrs.get("kind", getattr(self.instance, "kind", assigmentsKind.ASSIGNMENT))
        if kind == assigmentsKind.PROJECT and not attrs.get("submission_types") and not (
            self.instance and self.instance.submission_types
        ):
            attrs["submission_types"] = list(SUBMISSION_TYPES)
        if "rubric" in attrs and attrs["rubric"]:
            # total_marks of a rubric-graded assignment is, by definition, the rubric's sum.
            attrs["total_marks"] = sum(c["max_marks"] for c in attrs["rubric"])
        return attrs

    def validate_has_structured_questions(self, value):
        # §2a — "immutable after first submission exists". Duplicated
        # here (not just relying on `assigments.save()`'s own model-level
        # guard, added in Task 7) so an illegal flip comes back as a
        # clean 400 on this specific field — the model-level guard is the
        # backstop for callers that bypass this serializer entirely, not
        # the primary UX for this one.
        if self.instance and value != self.instance.has_structured_questions:
            if not self.instance.can_change_question_mode():
                raise serializers.ValidationError(
                    "has_structured_questions cannot change once a submission exists for this assigments."
                )
        return value

    @transaction.atomic
    def create(self, validated_data):
        # Atomic: without this, a mid-loop assigmentsQuestion validation
        # failure (caught below) would still leave the assigments row and
        # any already-created questions committed — a half-built
        # assigments with no way for the client to know it's incomplete.
        questions_data = validated_data.pop("questions", [])
        try:
            # [FIX] the local used to be named `assigments`, which shadows the
            # model class of the same name for the WHOLE function body, so the
            # right-hand side `assigments.objects` raised UnboundLocalError on
            # every single create — i.e. no one could create an assignment.
            assignment = assigments.objects.create(**validated_data)
            for question_data in questions_data:
                assigmentsQuestion.objects.create(assigments=assignment, **question_data)
        except DjangoValidationError as exc:
            # Belt-and-suspenders (see module docstring point 3): this
            # serializer's own validate()/assigmentsQuestionSerializer.
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
        # prevent. Question edits go through assigmentsQuestionSerializer
        # directly (e.g. a dedicated question sub-resource), never through
        # this assigments-level update.
        validated_data.pop("questions", None)
        try:
            return super().update(instance, validated_data)
        except DjangoValidationError as exc:
            # Same belt-and-suspenders reasoning as create() — this is
            # where assigments.save()'s own has_structured_questions
            # immutability guard (Task 7) would surface if
            # validate_has_structured_questions() above ever missed a
            # case.
            raise serializers.ValidationError({"detail": exc.messages})


class assigmentsAnswerSerializer(serializers.ModelSerializer):
    question_text = serializers.CharField(source="question.text", read_only=True)
    question_marks = serializers.IntegerField(source="question.marks", read_only=True)

    class Meta:
        model = assigmentsAnswer
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


class assigmentsSubmissionSerializer(serializers.ModelSerializer):
    answers = assigmentsAnswerSerializer(many=True, read_only=True)
    student = serializers.StringRelatedField(read_only=True)
    is_late = serializers.SerializerMethodField()

    class Meta:
        model = assigmentsSubmission
        fields = [
            "id", "assigments", "student", "written_content", "file",
            "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "feedback", "public_slug",
            "submitted_at", "checked_at", "answers", "is_late",
            "link_url", "rubric_scores",
        ]
        # Every field here except the assigments FK itself is either a
        # roster-time snapshot (roll_number/enrollment_no — set by
        # bridge.py, never by the student), or only ever changed through
        # a dedicated model method (submit_freeform/submit_structured/
        # grade_freeform/publish/unpublish/mark_answer_and_maybe_finalize)
        # fronted by its own action below — never a bare PATCH on this
        # serializer.
        #
        # `assigments` is deliberately NOT in this list — it has to stay
        # writable so a student can name which assigments they're
        # submitting for on create(). See validate() below for why that
        # doesn't mean it's writable on update() too.
        read_only_fields = [
            "student", "roll_number", "enrollment_no", "status", "grade",
            "total_marks_awarded", "public_slug", "submitted_at", "checked_at", "answers",
            "link_url", "rubric_scores",
        ]

    def get_is_late(self, obj) -> bool:
        return obj.is_late()

    def validate_assigments(self, assigments):
        """Task 8 fix — two real gaps, both against §2's own "personal-
        assigments flow: there's no bridge-created roster row ... the
        student's own first interaction creates the assigmentsSubmission
        row directly" (see assigmentsSubmissionViewSet.perform_create's
        docstring, and the model's own module docstring point 4):

        1. IMMUTABILITY ON UPDATE — `assigments` was writable on both
           create AND update (Meta.read_only_fields never listed it,
           because it legitimately has to be settable at create time).
           Without this check, a student could PATCH their own
           submission and silently re-point it at a *different*
           assigments post-creation — `unique_submission_per_student`
           does not catch this because the (assigments, student) pair
           genuinely changes to a new, not-yet-used pair. Blocked here:
           once `self.instance` exists, this field may not change.

        2. SOURCE RESTRICTION ON CREATE — §2 says a personal-assigments
           submission is the ONLY case where create() is the real entry
           point; a campus/liveclass-sourced assigments already has its
           assigmentsSubmission row (status=MISSING) created by
           `bridge.create_context_assigments()` at roster time, and
           students there are only ever meant to reach `submit_freeform`/
           `submit_structured` on that existing row. Nothing previously
           stopped a client from POSTing straight to this viewset's
           create() with a campus/liveclass assigments's id — best case
           that hits `unique_submission_per_student` and surfaces as a
           raw, uncaught `IntegrityError` (500); worst case (assigments
           has no roster row yet, e.g. a race with the bridge call) it
           silently creates a submission outside the roster flow
           entirely, with no roll_number/enrollment_no snapshot. Blocked
           here at the source instead: create() only accepts
           source=personal assigmentss.
        """
        if self.instance is not None and assigments.id != self.instance.assigments_id:
            raise serializers.ValidationError(
                "assigments cannot be changed once a submission has been created."
            )
        if self.instance is None and assigments.source != assigmentsSource.PERSONAL:
            raise serializers.ValidationError(
                "Submissions can only be created directly for personal assigmentss. "
                "Campus/liveclass submissions are created automatically when the "
                "assigments is posted — use submit_freeform/submit_structured on "
                "the existing submission instead."
            )
        # [SECURITY FIX] a private / draft personal assignment could be
        # "joined" by anyone who knew its (UUID) id. Only the poster, or anyone
        # once it is published with link/public visibility, may create a submission.
        if self.instance is None:
            request = self.context.get("request")
            user = getattr(request, "user", None)
            if not assigments.is_open_to(user):
                raise serializers.ValidationError("This assignment is not available.")
        return assigments

    def create(self, validated_data):
        # Belt-and-suspenders alongside validate_assigments() above: two
        # concurrent create() calls for the same (assigments, student)
        # could both pass validate_assigments()'s checks before either
        # write lands (no row lock at the serializer layer), and the
        # second one would previously surface `unique_submission_per_
        # student`'s violation as a raw, unhandled IntegrityError — a
        # 500 for what is, from the client's point of view, an ordinary
        # "you already submitted this" case. Turned into a clean 400
        # instead, matching how assigmentsCreateSerializer.create()
        # above already turns a model-layer error into
        # serializers.ValidationError rather than letting it bubble raw.
        try:
            return super().create(validated_data)
        except IntegrityError:
            raise serializers.ValidationError(
                {"detail": "A submission for this assigments already exists."}
            )


class PublicSubmissionSerializer(serializers.ModelSerializer):
    """§2 'Shareable URL — kaise kaam karta hai'. Auth-free, read-only,
    and deliberately narrower than `assigmentsSubmissionSerializer` — no
    internal ids beyond what the public page needs, no reviewer identity,
    no `feedback`/per-question `reviewer_feedback` distinction hidden
    behind extra fields the frontend doesn't need for this view."""

    assigments_title = serializers.CharField(source="assigments.title", read_only=True)
    student_name = serializers.SerializerMethodField()
    breakdown = serializers.SerializerMethodField()

    class Meta:
        model = assigmentsSubmission
        fields = [
            "assigments_title", "student_name", "roll_number", "enrollment_no",
            "submitted_at", "status", "written_content", "file", "grade",
            "total_marks_awarded", "breakdown", "link_url", "rubric_scores",
        ]

    def get_student_name(self, obj) -> str:
        return obj.student.get_full_name() or obj.student.username

    def get_breakdown(self, obj):
        """Only populated for the structured path (§2: "structured →
        per-question list ... + total_marks_awarded"). Free-form
        submissions get `None` here and rely on `written_content`/`file`/
        `grade` instead — same shape the internal review UI uses, just
        read-only."""
        if not obj.assigments.has_structured_questions:
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
    # Project hand-in: a URL (repo, live demo, design file). http(s) only.
    link_url = serializers.URLField(required=False, allow_blank=True, max_length=500, default="")

    def validate_link_url(self, value):
        if value and not value.lower().startswith(("http://", "https://")):
            raise serializers.ValidationError("Only http(s) links are accepted.")
        return value

    def validate(self, attrs):
        # The poster decides which hand-in types this assignment accepts
        # (`submission_types`; empty = all). The view passes the assignment in.
        assignment = self.context.get("assignment")
        if assignment is not None:
            allowed = assignment.allowed_submission_types()
            for field, kind in (("written_content", "text"), ("file", "file"), ("link_url", "link")):
                if attrs.get(field) and kind not in allowed:
                    raise serializers.ValidationError({field: f"This assignment doesn't accept '{kind}' hand-ins."})
        return attrs


class StructuredAnswerInputSerializer(serializers.Serializer):
    question_id = serializers.UUIDField()
    answer_data = serializers.JSONField()
    # Matches `assigmentsAnswer.answer_attachment` (Task 7 — mirrors
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


# =====================================================================
# PUBLISHING / EXPLORE
# =====================================================================
class assigmentsExploreSerializer(serializers.ModelSerializer):
    """Card shown in Explore and behind a share link. Marketing info only — no
    questions, options or answer keys; those are served through the normal
    retrieve once the caller has joined."""

    poster_name = serializers.SerializerMethodField()
    participants_count = serializers.SerializerMethodField()
    question_count = serializers.SerializerMethodField()
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = assigments
        fields = [
            "id", "title", "description", "kind", "tags", "difficulty", "due_date", "total_marks",
            "has_structured_questions", "question_count", "submission_types", "rubric",
            "poster_name", "participants_count", "published_at", "public_slug", "share_url",
        ]
        read_only_fields = fields

    def get_poster_name(self, obj) -> str:
        return _person_name(obj.posted_by)

    def get_participants_count(self, obj) -> int:
        annotated = getattr(obj, "n_participants", None)
        return annotated if annotated is not None else obj.submissions.count()

    def get_question_count(self, obj) -> int:
        annotated = getattr(obj, "n_questions", None)
        return annotated if annotated is not None else obj.questions.count()

    def get_share_url(self, obj):
        return _share_url(obj, self.context.get("request"))


class RubricGradeSerializer(serializers.Serializer):
    """`PATCH submissions/{id}/grade-rubric/` — `{"scores": {criterion: marks}, "feedback": ""}`."""

    scores = serializers.DictField(child=serializers.IntegerField(min_value=0))
    feedback = serializers.CharField(required=False, allow_blank=True, default="")


class PublishSerializer(serializers.Serializer):
    visibility = serializers.ChoiceField(choices=["public", "link"], default="public")
