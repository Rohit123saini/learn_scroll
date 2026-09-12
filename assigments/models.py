# assignment/models.py
"""
New, unified `assignment` app — replaces `campus.Assignment`/
`AssignmentSubmission` and `liveclass.Assignment`/`AssignmentSubmission`
(assignment_app_design.md, full doc). Also adds a third flow that existed
in neither old app: personal/self-assignment with a shareable public
verification URL.

Every design decision below is traceable to a section of that doc — cited
inline (§N) the same way core/models.py cites task numbers — so nothing
here is a guess dressed up as a fact.

GOLDEN RULE (§1, same shape as core/campus's own rule): this app never
imports `campus.*` or `liveclass.*` models. Both directions go through
`bridge.py` — `assignment/bridge.py` for campus/liveclass → assignment,
and `campus/bridge.py` / `liveclass/bridge.py` (not in this file) for the
reverse. `context_type` + `context_id` below are an **opaque soft
reference**, never a hard FK — this app has no idea what a "section" or a
"classroom" actually is, and must never gain one.

WHAT'S IN THIS FILE:
  1. `AssignmentBaseModel` — UUID-PK abstract base (§2, "UUID PK... kyunki
     personal-assignment URLs public share hongi"). Applied to all four
     concrete models here, not just `Assignment`, so nothing in this app
     leaks a sequential-integer enumeration surface even indirectly (e.g.
     via `AssignmentAnswer` ids appearing in a per-question review API).
  2. `Assignment` — the posted assignment itself. `source`/`context_type`/
     `context_id` (§1) route it to personal / campus / liveclass without
     this app knowing which. Deliberately has **no** `is_paid`/`price`
     field at all (§4 — "structurally impossible", same pattern as
     `campus.CampusLiveSession`), not just one defaulted to False.
  3. `AssignmentQuestion` / `AssignmentAnswer` (§2a) — the structured-
     question path. Verified field-for-field against the real
     `testseries/models.py` source (`Question`/`QuestionResponse`): same
     `clean()`/`save()` shape-validation per question_type, same
     `answer_attachment` field, same `mark_answer()` bounds-checking and
     `is_correct` semantics. Grading is delegated to `common.
     question_grading.auto_grade()` (verified against the real module —
     a plain `(question_type, options, correct_answer, answer_data,
     marks) -> (is_correct, marks_awarded)` function, not the
     `GradingResult`-returning, `options`-less draft this file was
     originally written against; that earlier mismatch would have raised
     an `ImportError` on `GradingResult`/`QuestionType` at import time and
     is now fixed, along with `submit_structured()`'s call site — see
     that method's own docstring for the full list of what changed).
  4. `AssignmentSubmission` — one student's attempt. Snapshots
     `roll_number`/`enrollment_no` at submit time (never re-derived) so a
     submission stays independently verifiable even if enrollment changes
     later. Carries the free-form path (`written_content`/`file`) *and*
     the structured path (`AssignmentAnswer` rows) — mutually exclusive in
     practice, gated by `Assignment.has_structured_questions`. Also owns
     the shareable public-verification `public_slug` feature (§2, the
     genuinely new flow this app adds).
  5. Every FileField (`Assignment.attachment`, `AssignmentQuestion.
     attachment`, `AssignmentSubmission.file`) runs `common.
     attachment_validators.ATTACHMENT_VALIDATORS` — the same extension +
     size rules `testseries` already enforces, moved to `common` so
     nothing here redefines them (see that module's own docstring).

WHAT'S DELIBERATELY NOT HERE (see the doc's own §8 "Open items" — carried
forward rather than silently resolved):
  - No `enrollment_no` dedicated field exists yet on the campus side
    (§5 GAP) — `AssignmentSubmission.enrollment_no` will simply be blank
    for campus-sourced submissions until that's decided. Not this app's
    call to make.
  - No hard `source="personal"` restriction on who may call `.publish()`
    — §2 explicitly says this is docs-only, not a field-level block.
  - `has_structured_questions` immutability-after-first-submission (§2a):
    the serializer is still the primary enforcement point per the doc (it
    can surface a clean field-level 400 instead of a 500), using
    `can_change_question_mode()` below so both places check the exact same
    condition. `Assignment.save()` now *also* raises `ValidationError` on
    an illegal flip, as a model-level backstop for callers that bypass the
    serializer entirely (management commands, `bridge.py`, shell) — belt
    and suspenders, not a redundant duplicate.
"""
import secrets
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from common.attachment_validators import attachment_extension_validator, validate_attachment_size
from common.question_grading import auto_grade
from login.models import User

# §2a / common/attachment_validators.py's own docstring: "assignment
# reuses the exact same rules instead of redefining them" — every
# user-uploaded FileField in this app (teacher's assignment attachment,
# a question's attachment, a student's submitted file) runs through the
# same extension + size checks `testseries` already uses, so there's one
# place that decides what an "attachment" is allowed to be, not three.
ATTACHMENT_VALIDATORS = [attachment_extension_validator, validate_attachment_size]


class AssignmentSource(models.TextChoices):
    """§1 — which of the three flows an `Assignment` belongs to. A plain
    string, not a FK, precisely so this app never has to know the shape of
    whatever "campus" or "liveclass" actually are."""

    PERSONAL = "personal", "Personal"
    CAMPUS = "campus", "Campus"
    LIVECLASS = "liveclass", "LiveClass"


class AssignmentBaseModel(models.Model):
    """§2 — UUID PK on every concrete model in this app (not just
    `Assignment`) because personal-assignment ids show up in public,
    unauthenticated URLs (`public_slug` aside — the id itself is also
    exposed via API responses), and a sequential integer PK would let
    anyone enumerate every assignment/submission/question in the system
    just by incrementing a number. `campus.CampusBaseModel` uses the same
    pattern for the same reason."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Assignment(AssignmentBaseModel):
    source = models.CharField(max_length=10, choices=AssignmentSource.choices, db_index=True)

    # §1 — opaque soft-reference to whatever posted this. `context_type` is
    # "section" | "classroom" | "" (blank for source=personal).
    # `context_id` is NEVER resolved to a real row from this app — the
    # caller (campus/liveclass bridge) already did that before calling
    # `assignment.bridge.create_context_assignment()`, and only the caller
    # ever dereferences it again.
    context_type = models.CharField(max_length=20, blank=True)
    context_id = models.UUIDField(null=True, blank=True)

    posted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posted_assignments",
    )

    title = models.CharField(max_length=200)

    # For source=personal this field IS the assignment: the student's own
    # written content, not a teacher's instructions. Kept as one field
    # rather than two (`instructions` vs `personal_content`) because the
    # doc treats it as literally the same slot used two different ways
    # (§2 table) — a serializer-level label swap by `source`, not a schema
    # difference.
    description = models.TextField(blank=True)

    attachment = models.FileField(
        upload_to="assignment/attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    # Optional — personal assignments are self-paced (§2: "personal
    # assignments me due date optional").
    due_date = models.DateField(null=True, blank=True)

    # NOTE: there is deliberately no `is_paid` / `price` field anywhere on
    # this model. §4 — "Assignment model me price/is_paid/koin field hi
    # nahi hai... structurally impossible rakha gaya hai", mirroring
    # `campus.CampusLiveSession`'s "free for students" enforcement. If a
    # future "premium assignment review" feature is ever wanted, that is
    # an explicit new decision (and almost certainly a new field on THIS
    # model, made deliberately) — never route it around this omission by
    # stuffing a price into `data` below.

    # Auto-summed from AssignmentQuestion.marks when
    # has_structured_questions=True (see `recompute_total_marks()` and the
    # post_save/post_delete signals below); otherwise a manual, optional
    # scale set directly by whoever posts the assignment.
    total_marks = models.PositiveIntegerField(null=True, blank=True)

    has_structured_questions = models.BooleanField(default=False)

    # §3 — "{"context_type": ..., "context_id": ...}" for client-side
    # deep-linking, same shape as `core.Notification.data`. Populated by
    # `bridge.create_context_assignment()`, not hand-maintained here.
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "context_type", "context_id"]),
            models.Index(fields=["posted_by", "due_date"]),
        ]

    def __str__(self):
        return self.title

    def can_change_question_mode(self) -> bool:
        """§2a — `has_structured_questions` is immutable once any
        submission exists for this assignment (changing the mode after
        students have started answering makes scoring inconsistent, same
        reasoning as `TestSeries.status="published"` locking after
        publish). This is a query, not an enforcement point on `save()` —
        the actual block lives in the serializer's `validate()` per the
        doc's own division of responsibility; this method exists so both
        the serializer and any other caller check the exact same
        condition instead of re-deriving it.
        """
        return not self.submissions.exclude(status=AssignmentSubmission.SubmissionStatus.MISSING).exists()

    def save(self, *args, **kwargs):
        """Model-level backstop for the `has_structured_questions`
        immutability rule (§2a). The serializer is still the primary
        enforcement point (it can return a clean 400 with a field-level
        error instead of a 500), but relying on the serializer alone means
        any other caller — a management command, a shell script, a future
        bridge.py helper — could flip the flag after submissions exist
        without anything stopping it. This is the same "belt and
        suspenders" split the doc uses elsewhere (query helper +
        serializer check); this just adds the model as the third layer so
        the invariant holds even outside the API.

        Skipped entirely on first create (`self.pk` is None — there's
        nothing to compare against yet) and skipped whenever the flag
        isn't actually changing, so this never adds a query to the common
        case of saving unrelated fields.
        """
        if self.pk:
            try:
                old = Assignment.objects.only("has_structured_questions").get(pk=self.pk)
            except Assignment.DoesNotExist:
                old = None
            if (
                old is not None
                and old.has_structured_questions != self.has_structured_questions
                and not self.can_change_question_mode()
            ):
                raise ValidationError(
                    "has_structured_questions cannot be changed once a submission exists for this assignment."
                )
        super().save(*args, **kwargs)

    def recompute_total_marks(self) -> None:
        """§2a — `total_marks` is "auto" (sum of `AssignmentQuestion.marks`)
        when `has_structured_questions=True`. Called from the
        AssignmentQuestion post_save/post_delete signals below rather than
        computed on read, so `total_marks` stays a plain, indexable/
        filterable column instead of a property that hits the DB on every
        access.
        """
        if not self.has_structured_questions:
            return
        total = self.questions.aggregate(total=Sum("marks"))["total"]
        # update() (not .save()) — avoids re-triggering this model's own
        # save-time side effects and avoids a stale in-memory `self` write
        # racing a concurrent question add/remove.
        Assignment.objects.filter(pk=self.pk).update(total_marks=total)


@receiver(pre_save, sender=Assignment)
def _delete_old_assignment_attachment_on_change(sender, instance: Assignment, **kwargs):
    """Same storage-agnostic cleanup pattern as `login.models`'s
    `profile_photo` signal — goes through `field.storage`, never a raw
    filesystem path, so this keeps working unchanged if/when
    `DEFAULT_FILE_STORAGE` moves to S3/GCS/Azure."""
    if not instance.pk:
        return
    try:
        old_attachment = sender.objects.only("attachment").get(pk=instance.pk).attachment
    except sender.DoesNotExist:
        return
    if old_attachment and old_attachment != instance.attachment:
        old_attachment.storage.delete(old_attachment.name)


@receiver(post_delete, sender=Assignment)
def _delete_assignment_attachment_on_delete(sender, instance: Assignment, **kwargs):
    """Fires for both `instance.delete()` and bulk `queryset.delete()` —
    see login/models.py's identical comment on why a `delete()` override
    would silently miss the bulk case."""
    if instance.attachment:
        instance.attachment.storage.delete(instance.attachment.name)


class AssignmentQuestion(AssignmentBaseModel):
    """§2a — field-for-field clone of `testseries.Question`, verified
    against the real `testseries/models.py` source (previously
    [NOT YET VERIFIED] — that source is now available). `clean()`/`save()`
    below replicate `Question.clean()`/`Question.save()`'s per-type shape
    validation exactly, not just the field list, because a "field-level
    identical" clone that accepts garbage `options`/`correct_answer` shapes
    testseries itself rejects isn't actually identical.
    """

    class QuestionTypeChoices(models.TextChoices):
        # Literal string values, not imported from `common.question_
        # grading` — the real `common/question_grading.py` (now
        # available) is a pure-function module with no `QuestionType`
        # class at all; it takes/returns raw strings, and `testseries.
        # Question.QuestionType` itself defines these locally rather
        # than importing them from anywhere. Matching that: same values
        # ("text"/"mcq"/"msq"/"list"), defined locally here too.
        TEXT = "text", "Text / Subjective"
        MCQ = "mcq", "Multiple Choice (single answer)"
        MSQ = "msq", "Multiple Select (multiple answers)"
        LIST = "list", "List / Ordered Items"

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="questions")

    # No default — matches `testseries.Question.order` exactly. A caller
    # must pick an explicit order; silently defaulting to 0 (the old
    # behavior here) risked every question landing on the same order
    # value and colliding on the uniqueness constraint below instead of
    # failing with a clear "you forgot to set order" error.
    order = models.PositiveIntegerField()
    # max_length=4 matches `testseries.Question.question_type` exactly —
    # every choice value ("text", "mcq", "msq", "list") is <=4 chars, so a
    # wider column here would silently permit values testseries itself
    # can never store, which defeats the point of a shape clone.
    question_type = models.CharField(max_length=4, choices=QuestionTypeChoices.choices, db_index=True)
    text = models.TextField()
    attachment = models.FileField(
        upload_to="assignment/question_attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )
    marks = models.PositiveIntegerField()

    # mcq: ["opt_a", "opt_b", ...] (option ids/labels — shape is caller's
    # choice, this app treats it as an opaque list). msq/list: same idea,
    # a list of selectable/orderable items. text: unused (blank list).
    options = models.JSONField(default=list, blank=True)

    # mcq: a single option id from `options`. msq/list: a list/subset of
    # `options`. text: unused (blank) — a `text` question is never
    # auto-graded, so there is no "correct answer" to store, only a human
    # reviewer's judgment on the submitted `AssignmentAnswer.answer_data`.
    correct_answer = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["assignment", "order"], name="unique_question_order_per_assignment"),
        ]

    def clean(self):
        """Verbatim port of `testseries.Question.clean()`'s per-type shape
        rules — same four branches, same error conditions, same
        force-empty-on-text behavior. Kept as a straight port rather than
        a paraphrase so the two stay diffable against each other."""
        super().clean()
        if self.question_type == self.QuestionTypeChoices.TEXT:
            self.options = []
            self.correct_answer = {}
            return

        if self.question_type in (self.QuestionTypeChoices.MCQ, self.QuestionTypeChoices.MSQ):
            if not isinstance(self.options, list) or not self.options:
                raise ValidationError("mcq/msq questions require a non-empty `options` list.")
            option_ids = {opt.get("id") for opt in self.options}
            if self.question_type == self.QuestionTypeChoices.MCQ:
                if "option_id" not in self.correct_answer or self.correct_answer["option_id"] not in option_ids:
                    raise ValidationError("mcq correct_answer must be {'option_id': <one of options[].id>}.")
            else:  # MSQ
                option_ids_answer = set(self.correct_answer.get("option_ids", []))
                if not option_ids_answer or not option_ids_answer.issubset(option_ids):
                    raise ValidationError("msq correct_answer must be {'option_ids': [subset of options[].id]}.")
            return

        if self.question_type == self.QuestionTypeChoices.LIST:
            mode = self.correct_answer.get("list_mode")
            if mode == "match":
                left = self.options.get("left") if isinstance(self.options, dict) else None
                right = self.options.get("right") if isinstance(self.options, dict) else None
                pairs = self.correct_answer.get("pairs")
                if not left or not right or not isinstance(pairs, dict):
                    raise ValidationError(
                        "list/match questions require options={'left': [...], 'right': [...]} "
                        "and correct_answer={'list_mode': 'match', 'pairs': {left_id: right_id, ...}}."
                    )
            elif mode == "order":
                if not isinstance(self.options, list) or not self.options:
                    raise ValidationError("list/order questions require a non-empty `options` list.")
                sequence = self.correct_answer.get("sequence")
                option_ids = {opt.get("id") for opt in self.options}
                if not isinstance(sequence, list) or set(sequence) != option_ids:
                    raise ValidationError(
                        "list/order correct_answer must be {'list_mode': 'order', "
                        "'sequence': [every options[].id, in order]}."
                    )
            else:
                raise ValidationError("list questions require correct_answer['list_mode'] to be 'match' or 'order'.")

    def save(self, *args, **kwargs):
        # Same exclude list, same reasoning as `testseries.Question.save()`:
        # options/correct_answer's shape is already validated per-type by
        # clean() above (called unconditionally by full_clean()), so they're
        # excluded here to avoid a redundant/incompatible generic JSONField
        # check; every other field (text, marks, question_type, order, and
        # the (assignment, order) uniqueness) stays IN the validated set.
        self.full_clean(exclude=["options", "correct_answer"])
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Q{self.order}: {self.text[:40]}"

    def is_auto_gradable(self) -> bool:
        # Matches testseries's own inline convention exactly
        # (`question.question_type != Question.QuestionType.TEXT` in
        # `TestAttempt.submit()`) rather than a separate AUTO_GRADABLE
        # set — there is no such set in the real `common/question_
        # grading.py`.
        return self.question_type != self.QuestionTypeChoices.TEXT


@receiver(post_save, sender=AssignmentQuestion)
def _recompute_total_marks_on_question_save(sender, instance: AssignmentQuestion, **kwargs):
    instance.assignment.recompute_total_marks()


@receiver(post_delete, sender=AssignmentQuestion)
def _recompute_total_marks_on_question_delete(sender, instance: AssignmentQuestion, **kwargs):
    # instance.assignment may already be gone from the DB if this fired as
    # part of the assignment's own CASCADE delete — the FK is still
    # readable off the in-memory `instance` either way, and
    # recompute_total_marks() no-ops safely via `.filter(pk=...).update()`
    # against a possibly-already-deleted Assignment row (matches 0 rows,
    # simply does nothing).
    instance.assignment.recompute_total_marks()


class AssignmentSubmission(AssignmentBaseModel):
    class SubmissionStatus(models.TextChoices):
        MISSING = "missing", "Missing"
        SUBMITTED = "submitted", "Submitted"
        LATE = "late", "Late"
        # §2 — "naya, testseries.TestAttempt jaisa hi": only reachable via
        # the structured path, when at least one `text` question is still
        # awaiting human review. The free-form path never passes through
        # this status — it goes straight submitted/late → checked in one
        # manual grade step.
        PARTIALLY_CHECKED = "partially_checked", "Partially Checked"
        CHECKED = "checked", "Checked"

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name="submissions")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="assignment_submissions")

    # --- free-form path fields — only meaningful when
    # assignment.has_structured_questions is False. ---
    written_content = models.TextField(blank=True)
    file = models.FileField(
        upload_to="assignment/submissions/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    # --- snapshots, taken once at submit/pre-create time, never
    # re-derived. §2: "so a submission stays verifiable even if the
    # student's enrollment later changes". ---
    roll_number = models.CharField(max_length=30, blank=True)
    # §5 GAP — campus.StudentEnrollment has no dedicated `enrollment_no`
    # field today, only `(student, section, session)` as the de-facto
    # enrollment key. Until that's decided, campus-sourced submissions
    # will simply pass enrollment_no="" through `bridge.py`. Not this
    # app's decision to make — flagged, not guessed around.
    enrollment_no = models.CharField(max_length=30, blank=True)

    status = models.CharField(
        max_length=20, choices=SubmissionStatus.choices, default=SubmissionStatus.MISSING, db_index=True
    )

    # Free-form path: manual letter/score, human-entered. Structured path:
    # displayable but derivable from total_marks_awarded — kept as a real
    # column (not a property) purely for backward-compat / simple display,
    # per §2's own note.
    grade = models.CharField(max_length=10, blank=True)

    # Structured path only — sum of AssignmentAnswer.marks_awarded, only
    # fully populated once every question (including every `text`
    # question) has been reviewed. See `_recompute_structured_status()`.
    total_marks_awarded = models.PositiveIntegerField(null=True, blank=True)

    # Free-form path: the assignment-level comment. Structured path: an
    # optional overall remark — per-question detail lives on
    # AssignmentAnswer.reviewer_feedback instead.
    feedback = models.TextField(blank=True)

    # §2 — the actual new feature this app adds. Blank = not published, no
    # public page exists at all. Non-empty = `GET
    # /assignment/public/{public_slug}/` serves a read-only view.
    # `unique=True` + `blank=True` has the same NULL-vs-'' footgun noted in
    # login/models.py's `phone` field, EXCEPT here it's harmless: every
    # unpublished submission shares the value `""`, but Django/Postgres
    # both treat blank CharField uniqueness the same way phone's comment
    # warns about — multiple `''` rows WILL collide on a real unique
    # constraint. To avoid silently reintroducing that exact bug, blank
    # slugs are excluded from uniqueness via a partial constraint instead
    # of relying on `unique=True` (which does not distinguish blank from
    # non-blank here the way phone's `null=True` does for CharField).
    public_slug = models.CharField(max_length=40, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    # Only set when status transitions to CHECKED — not on
    # PARTIALLY_CHECKED, matching testseries's equivalent semantics per
    # the doc.
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["assignment", "student"], name="unique_submission_per_student"),
            # See public_slug comment above — only enforce uniqueness
            # among rows that have actually published (non-blank slug),
            # so the many `""` "not published" rows never collide.
            models.UniqueConstraint(
                fields=["public_slug"],
                condition=~models.Q(public_slug=""),
                name="unique_nonblank_public_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["assignment", "status"]),
        ]

    def __str__(self):
        return f"{self.student} - {self.assignment} ({self.status})"

    def is_late(self) -> bool:
        if not self.assignment.due_date or not self.submitted_at:
            return False
        return self.submitted_at.date() > self.assignment.due_date

    # ---------------------------------------------------------------
    # Free-form path
    # ---------------------------------------------------------------
    def submit_freeform(self, *, written_content: str = "", file=None) -> None:
        """§2 table — free-form path only. Goes to SUBMITTED or LATE and
        stops there; grading is a separate, explicit manual step
        (`grade_freeform`) — matching the doc's "seedha submitted/late →
        checked, ek hi manual grade step" description.
        """
        self.written_content = written_content
        if file is not None:
            self.file = file
        self.submitted_at = timezone.now()
        self.status = self.SubmissionStatus.LATE if self.is_late() else self.SubmissionStatus.SUBMITTED
        self.save(update_fields=["written_content", "file", "submitted_at", "status", "updated_at"])

    def grade_freeform(self, *, grade: str, feedback: str = "") -> None:
        self.grade = grade
        self.feedback = feedback
        self.status = self.SubmissionStatus.CHECKED
        self.checked_at = timezone.now()
        self.save(update_fields=["grade", "feedback", "status", "checked_at", "updated_at"])

    # ---------------------------------------------------------------
    # Structured path — §2a, ported from the real `TestAttempt.submit()`
    # (verified against `testseries/models.py`, not the earlier
    # description-only draft).
    # ---------------------------------------------------------------
    def submit_structured(self, answers: list[dict]) -> None:
        """`answers = [{"question_id": ..., "answer_data": ..., "answer_attachment": <file, optional>}, ...]`

        Bulk-creates one `AssignmentAnswer` per question, mirroring
        `TestAttempt.submit()` field-for-field:
          - `is_auto_graded` is computed from `question_type != TEXT`
            *before* grading (same as `TestAttempt.submit()`), not derived
            from the grading call's return value.
          - `common.question_grading.auto_grade()` returns a plain
            `(is_correct, marks_awarded)` tuple (confirmed against the
            real module — it is NOT the `GradingResult` object this file
            previously assumed, and it requires `options=`, which the
            previous version of this method omitted entirely: a real bug,
            now fixed) and both are `None` for `text` questions.
          - `answer_attachment` is only ever taken for non-auto-graded
            (`text`) questions — same as `TestAttempt.submit()`'s
            `None if is_auto_graded else files.get(...)` — an
            auto-graded question's attachment slot (if a caller sent one
            anyway) is silently ignored rather than stored, since there is
            nothing for a reviewer to look at there.
          - `reviewed_at`/`reviewed_by` are deliberately left unset here
            (default `None`) even for auto-graded answers — testseries
            never sets them at submit-time either, only inside
            `mark_answer()`'s actual human-review call. "Auto-graded" and
            "reviewed by a person" are different facts; conflating them
            was a bug in the previous version of this method.

        Resolves this submission straight to CHECKED (if no `text`
        question exists) or PARTIALLY_CHECKED (if at least one does and
        is still awaiting human review) — no SUBMITTED/LATE intermediate
        status on this path, per §2's submit-flow description.
        """
        questions = {str(q.id): q for q in self.assignment.questions.all()}
        answer_rows = []
        for entry in answers:
            question = questions[str(entry["question_id"])]
            answer_data = entry["answer_data"]
            is_auto_graded = question.question_type != AssignmentQuestion.QuestionTypeChoices.TEXT
            is_correct, marks_awarded = auto_grade(
                question_type=question.question_type,
                options=question.options,
                correct_answer=question.correct_answer,
                answer_data=answer_data,
                marks=question.marks,
            )
            answer_rows.append(
                AssignmentAnswer(
                    submission=self,
                    question=question,
                    answer_data=answer_data,
                    answer_attachment=None if is_auto_graded else entry.get("answer_attachment"),
                    is_auto_graded=is_auto_graded,
                    is_correct=is_correct,
                    marks_awarded=marks_awarded,
                )
            )
        AssignmentAnswer.objects.bulk_create(answer_rows)

        self.submitted_at = timezone.now()
        self._recompute_structured_status()

    def _recompute_structured_status(self) -> None:
        """Shared by `submit_structured()` and
        `mark_answer_and_maybe_finalize()` — both need the same
        "has every text question been reviewed yet?" check, so it lives
        in one place rather than two copies drifting apart."""
        pending_review = self.answers.filter(
            question__question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, marks_awarded__isnull=True
        ).exists()
        if pending_review:
            self.status = self.SubmissionStatus.PARTIALLY_CHECKED
            self.checked_at = None
        else:
            total = self.answers.aggregate(total=Sum("marks_awarded"))["total"] or 0
            self.total_marks_awarded = total
            self.status = self.SubmissionStatus.CHECKED
            self.checked_at = timezone.now()
        self.save(update_fields=["submitted_at", "status", "checked_at", "total_marks_awarded", "updated_at"])

    def mark_answer_and_maybe_finalize(self, *, question: "AssignmentQuestion", marks_awarded: int,
                                        feedback: str = "", reviewed_by: User) -> "AssignmentAnswer":
        """§2a — "review flow bhi identical" to testseries. Reviews exactly
        one `text`-type `AssignmentAnswer`, then re-runs
        `_recompute_structured_status()` so the submission flips from
        PARTIALLY_CHECKED to CHECKED the moment the *last* pending text
        question gets reviewed — callers never need to separately "check
        if we're done", this does it on every call.
        """
        answer = self.answers.get(question=question)
        answer.mark_answer(marks_awarded=marks_awarded, feedback=feedback, reviewed_by=reviewed_by)
        self._recompute_structured_status()
        return answer

    # ---------------------------------------------------------------
    # Shareable public URL — §2, the genuinely new feature
    # ---------------------------------------------------------------
    def publish(self) -> str:
        """Always mints a fresh slug, even if one already existed —
        matches the doc's "unpublish -> naya publish naya random slug
        deta hai, purana URL turant dead ho jata hai" behaviour: calling
        this again re-publishes under a brand-new, unguessable URL rather
        than handing back the previous one."""
        self.public_slug = secrets.token_urlsafe(24)
        self.save(update_fields=["public_slug", "updated_at"])
        return self.public_slug

    def unpublish(self) -> None:
        self.public_slug = ""
        self.save(update_fields=["public_slug", "updated_at"])


@receiver(pre_save, sender=AssignmentSubmission)
def _delete_old_submission_file_on_change(sender, instance: AssignmentSubmission, **kwargs):
    """Same pattern as the two attachment-cleanup signals above / the
    original `profile_photo` signal in login/models.py."""
    if not instance.pk:
        return
    try:
        old_file = sender.objects.only("file").get(pk=instance.pk).file
    except sender.DoesNotExist:
        return
    if old_file and old_file != instance.file:
        old_file.storage.delete(old_file.name)


@receiver(post_delete, sender=AssignmentSubmission)
def _delete_submission_file_on_delete(sender, instance: AssignmentSubmission, **kwargs):
    if instance.file:
        instance.file.storage.delete(instance.file.name)


class AssignmentAnswer(AssignmentBaseModel):
    """§2a — field-for-field clone of `testseries.QuestionResponse`,
    verified against the real `testseries/models.py` source (previously
    [NOT YET VERIFIED] — now confirmed, and two real gaps fixed below:
    the missing `answer_attachment` field, and `mark_answer()` setting
    `is_correct` when testseries's version never does)."""

    submission = models.ForeignKey(AssignmentSubmission, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(AssignmentQuestion, on_delete=models.CASCADE, related_name="answers")

    # mcq: a single option id. msq/list: a list. text: free-form text.
    # Opaque JSON on purpose — this model doesn't need to know the exact
    # shape, only `common.question_grading.auto_grade()` and whatever
    # frontend renders it do. `default=dict` matches
    # `QuestionResponse.answer_data` exactly.
    answer_data = models.JSONField(default=dict)

    # A student's photo/file answer — e.g. a photo of a handwritten
    # solution for a `text` question. Mirrors
    # `QuestionResponse.answer_attachment` exactly, including the same
    # validators; this field was missing entirely before the real
    # testseries source was available to diff against, which meant the
    # structured path here had no way to accept a file answer at all —
    # a real functional gap, not just a shape mismatch.
    answer_attachment = models.FileField(
        upload_to="assignment/answer_attachments/", null=True, blank=True, validators=ATTACHMENT_VALIDATORS
    )

    is_auto_graded = models.BooleanField()
    is_correct = models.BooleanField(null=True)
    marks_awarded = models.PositiveIntegerField(null=True, blank=True)

    reviewer_feedback = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_assignment_answers"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["submission", "question"], name="unique_answer_per_submission_question"),
        ]

    def __str__(self):
        return f"Answer to {self.question_id} in {self.submission_id}"

    def mark_answer(self, *, marks_awarded: int, feedback: str = "", reviewed_by: User) -> None:
        """Verbatim port of `QuestionResponse.mark_answer()`'s semantics —
        only ever called on a `text`-type answer in practice (mcq/msq/list
        are already fully graded by `auto_grade()` at submit time), so
        calling this on an already auto-graded answer is treated as a
        programming error (`ValueError`), matching testseries exactly,
        not silently allowed as an override the way this method used to.
        `marks_awarded` bounds are also validated against
        `question.marks` for the same reason. `is_correct` is
        deliberately left untouched here (stays whatever it already was —
        `None` for a `text` answer) because a `text` answer has no binary
        correct/incorrect concept, same as `QuestionResponse`'s own
        `is_correct` field doc says; this used to set
        `is_correct = marks_awarded > 0`, which testseries never does.
        """
        if self.is_auto_graded:
            raise ValueError(
                f"AssignmentAnswer {self.pk} is auto-graded; mark_answer() is only valid for text-type answers."
            )
        if marks_awarded < 0:
            raise ValueError(f"marks_awarded ({marks_awarded}) cannot be negative.")
        if marks_awarded > self.question.marks:
            raise ValueError(
                f"marks_awarded ({marks_awarded}) cannot exceed question.marks ({self.question.marks})."
            )
        self.marks_awarded = marks_awarded
        self.reviewer_feedback = feedback
        self.reviewed_by = reviewed_by
        self.reviewed_at = timezone.now()
        self.save(update_fields=["marks_awarded", "reviewer_feedback", "reviewed_by", "reviewed_at"])