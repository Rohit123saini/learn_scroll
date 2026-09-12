# testseries/models.py
"""
`testseries` app — implements testseries_app_design.md (v1) end to end:
TestSeries, Question, QuestionResponse, TestSeriesPurchase, TestAttempt.

Golden rule (same as `assignment`): this app NEVER imports `campus` or
`liveclass` models directly. Context is referenced opaquely via
`context_type` (CharField) + `context_id` (UUID) — resolving that back to
a `campus.Section` / `liveclass.Classroom` is the calling bridge's job
(see `campus/bridge.py::create_testseries` / `liveclass/bridge.py::
create_testseries` in the design doc), never this app's.

`user_profile.CoinLedger` is used DIRECTLY (no bridge) — same precedent
as `campus`'s fee-wallet and `message`'s gifting (confirmed in
`user_profile_app_reference.md`, CoinLedger is not bridge-owned).

PREREQUISITE GAP — RESOLVED. `user_profile.CoinLedger.TransactionType`
now has `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT` and
`core.models.Notification.NotifType` now has `TESTSERIES_POSTED`/
`TESTSERIES_CHECKED`/`TESTSERIES_PAYOUT_RELEASED`, so the old
`_TransactionTypeGap`/`_NotifTypeGap` placeholder classes are gone —
every call site below references the real enums directly, imported
lazily (function/method-local) at each use site for the same
import-cycle reason `_record_coin_transaction`/`_notify` already import
`CoinLedger`/`create_notification` lazily rather than at module level.

Everything else below matches the design doc's confirmed decisions:
  - `TestSeries.is_paid` is server-side FORCED False for `source="campus"`
    (campus's golden "always free for students" constraint) inside
    `save()` here — defence-in-depth, even though the real enforcement
    point is the campus-facing serializer/viewset per §5 of the doc.
  - Three question types — `text` (subjective, always manual), `mcq`
    (single-correct, auto-graded), `msq` (multi-select, exact-set
    auto-graded — partial credit is an explicit non-goal, §8 item 4) —
    plus `list` (match-the-following / ordering, exact-match
    auto-graded, disambiguated by `correct_answer["list_mode"]`).
  - Per-question review lives on `QuestionResponse`, not a single
    attempt-level blob — this is the actual "production-level
    requirement" (per-question marks + review) the doc calls out in its
    intro and again in §"Open items" is NOT one of (this is fully
    implemented, not deferred).
  - `TestAttempt.status` has 4 states, notably the new
    `partially_checked` — auto-graded questions can finish instantly
    while `text` questions are still pending review; a series with zero
    `text` questions goes straight to `checked` (see `submit()`).
  - Escrow: `TestSeriesPurchase` mirrors `liveclass.PassPurchase` —
    coins move to escrow on purchase, release to creator only once the
    ENTIRE attempt (including every `text` question) has been reviewed.
    Partial release is an explicit non-goal (§8 item — escrow holds
    until full `checked` status).
"""
import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from login.models import User

from common.attachment_validators import (
    ATTACHMENT_ALLOWED_EXTENSIONS,  # noqa: F401 — re-exported, some callers import it from here
    ATTACHMENT_MAX_SIZE_MB,  # noqa: F401 — re-exported, some callers import it from here
    attachment_extension_validator,
    validate_attachment_size,
)
from common.question_grading import auto_grade as _shared_auto_grade

# ---------------------------------------------------------------------
# Attachment validation now LIVES in common/attachment_validators.py —
# shared with `assignment` (Task 7) so both apps enforce identical
# extension/size rules on their FileFields instead of duplicating them.
# Still applied in the same three places for defence-in-depth:
#   1. Question.attachment / QuestionResponse.answer_attachment
#      FileField(validators=[...]) below — enforced by full_clean().
#   2. QuestionSerializer.validate_attachment() (serializers.py) — so a
#      bad Question.attachment upload comes back as a clean 400.
#   3. views.py::TestAttemptViewSet.submit() — so a bad answer_attachment
#      upload (which never goes through a serializer at all — it's read
#      straight off request.FILES) also comes back as a clean 400,
#      before TestAttempt.submit() ever touches the database.
# ---------------------------------------------------------------------


def _record_coin_transaction(*, user, transaction_type: str, amount: int, reference: str):
    """Thin wrapper around `user_profile.CoinLedger.record_transaction` —
    imported lazily (function-local) to avoid a hard import-time
    dependency cycle between `testseries` and `user_profile`."""
    from user_profile.models import CoinLedger

    return CoinLedger.objects.record_transaction(
        user=user,
        transaction_type=transaction_type,
        amount=amount,
        reference=reference,
    )


def _notify(*, recipient, notif_type: str, title: str, message: str = "", data: dict | None = None):
    """Thin wrapper around `core.services.create_notification` — same
    lazy-import reasoning as `_record_coin_transaction` above."""
    from core.services import create_notification

    return create_notification(
        recipient=recipient,
        notif_type=notif_type,
        title=title,
        message=message,
        data=data or {},
    )


class TestSeriesBaseModel(models.Model):
    """UUID PK — same reasoning as `campus`/`assignment`: these rows get
    referenced from outside this app (context_id-style opaque refs,
    cross-app notification `data` payloads) where a guessable sequential
    integer PK is undesirable."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TestSeries(TestSeriesBaseModel):
    class Source(models.TextChoices):
        INDIVIDUAL = "individual", "Individual / Marketplace"
        CAMPUS = "campus", "Campus"
        LIVECLASS = "liveclass", "LiveClass"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"

    source = models.CharField(max_length=10, choices=Source.choices, db_index=True)

    # Opaque context reference — golden rule: this app never resolves
    # these to real campus.Section / liveclass.Classroom rows itself.
    # Both blank/null for `source="individual"`.
    context_type = models.CharField(max_length=20, blank=True, null=True)
    context_id = models.UUIDField(blank=True, null=True)

    creator = models.ForeignKey(User, on_delete=models.CASCADE, related_name="testseries_created")

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    # Server-side forced False for source="campus" in save() below —
    # campus's "always free for students" golden constraint, defence in
    # depth on top of the campus-facing viewset/serializer (§5).
    is_paid = models.BooleanField(default=False)
    price_coins = models.PositiveIntegerField(default=0)

    duration_minutes = models.PositiveIntegerField(null=True, blank=True)

    # Auto-computed from Question.marks sum on question changes, or a
    # manual override — left as a plain field (not a property) so it can
    # be filtered/sorted/displayed without recomputing on every read.
    total_marks = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # MVP is attempts_allowed=1. Multi-attempt (attempt_number-scoped
    # uniqueness) is an explicit follow-up — see §8 open item 3 and the
    # `TestAttempt` unique constraint comment below.
    attempts_allowed = models.PositiveIntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "context_type", "context_id"]),
            models.Index(fields=["creator", "status"]),
        ]

    def save(self, *args, **kwargs):
        # Defence-in-depth: campus test series are ALWAYS free, no matter
        # what a caller sets — the real enforcement point is the
        # campus-facing viewset/serializer (§5), this is a second layer
        # so a bug there can never actually charge a campus student.
        if self.source == self.Source.CAMPUS:
            self.is_paid = False
            self.price_coins = 0
        if not self.is_paid:
            self.price_coins = 0
        super().save(*args, **kwargs)

    def recompute_total_marks(self, *, save: bool = True) -> int:
        """`total_marks` = sum of this series' Question.marks. Called
        whenever a question is added/edited/removed while the series is
        still `draft` (question set — and therefore total_marks — is
        locked once `published`, see `Question.series` docs below)."""
        total = self.questions.aggregate(total=models.Sum("marks"))["total"] or 0
        self.total_marks = total
        if save:
            self.save(update_fields=["total_marks"])
        return total

    def __str__(self):
        return f"{self.title} ({self.get_source_display()})"


class Question(TestSeriesBaseModel):
    """Production-level requirement: not MCQ-only. Three question types —
    `text` (subjective, always manually reviewed), `mcq` (single-correct,
    auto-graded), `msq` (multi-select, exact-set auto-graded) — plus
    `list` (match-the-following / ordering, exact-match auto-graded, the
    two sub-kinds disambiguated by `correct_answer["list_mode"]`, not a
    separate DB column, to avoid a schema change if a third list sub-kind
    shows up later)."""

    class QuestionType(models.TextChoices):
        TEXT = "text", "Text / Subjective"
        MCQ = "mcq", "Multiple Choice (single)"
        MSQ = "msq", "Multiple Select"
        LIST = "list", "List-based (match / order)"

    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="questions")

    # Display order — locked together with the rest of the question set
    # once the series is published (enforced in serializer, not here —
    # keeping model-level validation limited to shape, not workflow
    # state, matches the rest of this codebase's split).
    order = models.PositiveIntegerField()

    question_type = models.CharField(max_length=4, choices=QuestionType.choices, db_index=True)

    text = models.TextField()

    attachment = models.FileField(
        upload_to="testseries/questions/", null=True, blank=True,
        validators=[attachment_extension_validator, validate_attachment_size],
    )

    marks = models.PositiveIntegerField()

    # Shape depends on question_type — see module docstring / design doc
    # §2 for the full `options`/`correct_answer` shape table per type.
    # `text` type: both stay at their empty defaults (force-emptied in
    # clean() below — defence, a client can send garbage and the server
    # just ignores it for that type).
    options = models.JSONField(default=list, blank=True)
    correct_answer = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("series", "order")
        ordering = ["order"]

    def clean(self):
        super().clean()
        if self.question_type == self.QuestionType.TEXT:
            # Force-empty on save regardless of what a client sent —
            # subjective questions have no auto-gradable shape at all.
            self.options = []
            self.correct_answer = {}
            return

        if self.question_type in (self.QuestionType.MCQ, self.QuestionType.MSQ):
            if not isinstance(self.options, list) or not self.options:
                raise ValidationError("mcq/msq questions require a non-empty `options` list.")
            option_ids = {opt.get("id") for opt in self.options}
            if self.question_type == self.QuestionType.MCQ:
                if "option_id" not in self.correct_answer or self.correct_answer["option_id"] not in option_ids:
                    raise ValidationError("mcq correct_answer must be {'option_id': <one of options[].id>}.")
            else:  # MSQ
                option_ids_answer = set(self.correct_answer.get("option_ids", []))
                if not option_ids_answer or not option_ids_answer.issubset(option_ids):
                    raise ValidationError("msq correct_answer must be {'option_ids': [subset of options[].id]}.")
            return

        if self.question_type == self.QuestionType.LIST:
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
        # Only `options`/`correct_answer` need excluding here — their shape
        # is already validated per-question_type by clean() above (called
        # unconditionally by full_clean() regardless of exclude). Every
        # other field (text, marks, question_type, order, and the
        # (series, order) unique_together) must stay IN the validated set,
        # or a bad value surfaces as a raw IntegrityError instead of a
        # clean ValidationError. (Previous version of this exclude list was
        # inverted — it excluded everything BUT options/correct_answer,
        # silently skipping required-field and uniqueness checks.)
        self.full_clean(exclude=["options", "correct_answer"])
        super().save(*args, **kwargs)

    def auto_grade(self, answer_data: dict) -> tuple[bool | None, int | None]:
        """Thin wrapper around `common.question_grading.auto_grade()` —
        actual grading logic now lives there (shared with `assignment`,
        Task 7) so `TestAttempt.submit()` and any future caller here
        still share one implementation, and other apps share it too
        instead of duplicating it. Returns `(is_correct, marks_awarded)`;
        both `None` for `text` (never auto-graded — always routed to
        manual review). Behavior is unchanged from before the move."""
        return _shared_auto_grade(
            question_type=self.question_type,
            options=self.options,
            correct_answer=self.correct_answer,
            answer_data=answer_data,
            marks=self.marks,
        )

    def __str__(self):
        return f"[{self.series_id}] Q{self.order} ({self.question_type})"


class QuestionResponse(TestSeriesBaseModel):
    """Production-level per-question review — one row per
    (attempt, question), replacing the old single-JSON-blob-per-attempt
    design so every question carries its own marks + reviewer feedback,
    not just the attempt as a whole."""

    attempt = models.ForeignKey("TestAttempt", on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="responses")

    # Shape mirrors Question.correct_answer for the same question_type —
    # see module docstring / design doc §2.
    answer_data = models.JSONField(default=dict)

    # A student's photo/file answer — e.g. a photo of a handwritten
    # solution for a `text` question. Only ever populated for `text`
    # responses (see `TestAttempt.submit()` below, which only looks for
    # an uploaded file on non-auto-graded questions); left as a plain
    # nullable field on every response rather than a text-only column so
    # no schema branch is needed per question_type. Same extension/size
    # validators as `Question.attachment` — see the module-level comment
    # above `ATTACHMENT_ALLOWED_EXTENSIONS`.
    answer_attachment = models.FileField(
        upload_to="testseries/answers/", null=True, blank=True,
        validators=[attachment_extension_validator, validate_attachment_size],
    )

    is_auto_graded = models.BooleanField()

    # Auto-graded types: set at submit-time. `text`: always null — no
    # binary correct/incorrect concept for subjective answers.
    is_correct = models.BooleanField(null=True)

    # Auto-graded: full/zero Question.marks at submit-time. `text`: null
    # until a reviewer calls mark_answer().
    marks_awarded = models.PositiveIntegerField(null=True, blank=True)

    # Per-question review comment — typically used for `text` responses,
    # but allowed on any type (e.g. a note on a wrong MCQ answer).
    reviewer_feedback = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="testseries_responses_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["attempt", "question"], name="unique_response_per_attempt_question"),
        ]

    def mark_answer(self, marks_awarded: int, feedback: str = "", reviewer=None):
        """Only ever called explicitly on `text`-type responses —
        auto-graded types already have marks_awarded set at submit-time,
        so calling this on one is a programming error (accidental
        double-grading), not a valid review action, hence the
        `ValueError` rather than silently overwriting."""
        if self.is_auto_graded:
            raise ValueError(
                f"QuestionResponse {self.pk} is auto-graded; mark_answer() is only valid for text-type responses."
            )
        if marks_awarded < 0:
            raise ValueError(f"marks_awarded ({marks_awarded}) cannot be negative.")
        if marks_awarded > self.question.marks:
            raise ValueError(
                f"marks_awarded ({marks_awarded}) cannot exceed question.marks ({self.question.marks})."
            )
        self.marks_awarded = marks_awarded
        self.reviewer_feedback = feedback
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save(update_fields=["marks_awarded", "reviewer_feedback", "reviewed_by", "reviewed_at"])

    def __str__(self):
        return f"Response: attempt={self.attempt_id} question={self.question_id}"


class TestSeriesPurchase(TestSeriesBaseModel):
    """Direct analogue of `liveclass.PassPurchase`'s escrow design, scoped
    per-attempt instead of per-day: buyer's coins move to escrow
    immediately on purchase, release to the creator only once the WHOLE
    attempt (every question, including every `text` question) has been
    reviewed. Partial release is an explicit non-goal — escrow holds
    until full `checked` status (§8)."""

    class Status(models.TextChoices):
        ESCROWED = "escrowed", "Escrowed"
        RELEASED = "released", "Released"
        REFUNDED = "refunded", "Refunded"

    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="purchases")
    buyer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="testseries_purchases")

    coins_spent = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ESCROWED, db_index=True)

    attempt = models.OneToOneField(
        "TestAttempt", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)

    @classmethod
    @transaction.atomic
    def purchase_and_start_attempt(cls, *, series: TestSeries, buyer: User, **attempt_snapshot_kwargs) -> "TestSeriesPurchase":
        """Locks the buyer row (same `select_for_update()` pattern as
        `PassPurchase`/`CoinLedger.record_transaction`), debits coins,
        creates the escrow row, and starts the linked `TestAttempt` in
        one atomic step. Insufficient balance bubbles up as a
        `ValueError` from `record_transaction()` — caller (view) maps
        that to a 402, same as the campus fee module."""
        from user_profile.models import CoinLedger

        locked_buyer = User.objects.select_for_update().get(pk=buyer.pk)

        attempt_no = TestAttempt.objects.filter(series=series, student=locked_buyer).count() + 1
        _record_coin_transaction(
            user=locked_buyer,
            transaction_type=CoinLedger.TransactionType.TESTSERIES_PURCHASE,
            amount=-series.price_coins,
            reference=f"testseries_purchase:{series.id}:{locked_buyer.id}:{attempt_no}",
        )

        purchase = cls.objects.create(
            series=series,
            buyer=locked_buyer,
            coins_spent=series.price_coins,
            status=cls.Status.ESCROWED,
        )
        attempt = TestAttempt.objects.create(series=series, student=locked_buyer, **attempt_snapshot_kwargs)
        purchase.attempt = attempt
        purchase.save(update_fields=["attempt"])
        return purchase

    def release(self):
        """Called once the linked attempt is fully `checked` — either
        the whole-series-auto-gradable case in `TestAttempt.submit()`,
        or the last `text` review in
        `TestAttempt.mark_answer_and_maybe_finalize()`."""
        from user_profile.models import CoinLedger
        from core.models import Notification

        _record_coin_transaction(
            user=self.series.creator,
            transaction_type=CoinLedger.TransactionType.TESTSERIES_PAYOUT,
            amount=self.coins_spent,
            reference=f"testseries_payout:{self.id}",
        )
        self.status = self.Status.RELEASED
        self.released_at = timezone.now()
        self.save(update_fields=["status", "released_at"])
        _notify(
            recipient=self.series.creator,
            notif_type=Notification.NotifType.TESTSERIES_PAYOUT_RELEASED,
            title="Payout released",
            message=f"You've been paid {self.coins_spent} coins for '{self.series.title}'.",
            data={"purchase_id": str(self.id), "series_id": str(self.series_id)},
        )

    def refund(self, reason_note: str = ""):
        """`PassPurchase.reverse()` analogue — the safety-net refund path
        used by `refund_unchecked_paid_attempts` (design doc §7) when a
        creator never finishes checking within
        `settings.TESTSERIES_AUTO_REFUND_DAYS`."""
        from user_profile.models import CoinLedger

        _record_coin_transaction(
            user=self.buyer,
            transaction_type=CoinLedger.TransactionType.REFUND,
            amount=self.coins_spent,
            reference=f"testseries_refund:{self.id}",
        )
        self.status = self.Status.REFUNDED
        self.refunded_at = timezone.now()
        self.save(update_fields=["status", "refunded_at"])

    def __str__(self):
        return f"Purchase: {self.buyer} -> {self.series} [{self.status}]"


class TestAttempt(TestSeriesBaseModel):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In Progress"
        SUBMITTED = "submitted", "Submitted"
        # New status vs. the old 3-state design: auto-graded questions
        # can finish grading instantly, but at least one `text` question
        # is still review-pending. Without this, a 1-subjective-question
        # attempt was indistinguishable from "nothing checked at all" on
        # a dashboard — production-level per-question review needs this
        # intermediate visibility.
        PARTIALLY_CHECKED = "partially_checked", "Partially Checked"
        CHECKED = "checked", "Checked"

    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="testseries_attempts")

    # attempts_allowed > 1 groundwork only — MVP (attempts_allowed=1) is
    # fully implemented via the unique constraint below; a real
    # multi-attempt uniqueness shape on (series, student, attempt_number)
    # is an explicit follow-up, not guessed at here (§8 open item 3).
    attempt_number = models.PositiveIntegerField(default=1)

    # Sum of already-graded QuestionResponse.marks_awarded at submit-time
    # — text-type responses contribute 0 here until reviewed.
    auto_score = models.PositiveIntegerField(default=0)

    # Full QuestionResponse.marks_awarded sum (auto + reviewed), only set
    # once EVERY question (including every text question) is reviewed.
    # Kept as a denormalized field (not a computed property) so
    # checked_at-style filtering/sorting doesn't need to recompute per
    # row — same read-performance trade-off `core`'s Notification
    # indexes document explicitly.
    final_score = models.PositiveIntegerField(null=True, blank=True)

    status = models.CharField(max_length=17, choices=Status.choices, default=Status.IN_PROGRESS, db_index=True)

    # Last reviewer to fully complete this attempt's checking — distinct
    # from QuestionResponse.reviewed_by, which is already granular
    # per-question; this is just the attempt-level "who finished it" summary.
    checked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="testseries_attempts_checked"
    )

    # `assignment` app's snapshot pattern — campus/liveclass callers pass
    # these from the roster at submit-time rather than this app resolving
    # them itself (golden rule: no direct campus/liveclass imports).
    roll_number = models.CharField(max_length=30, blank=True)
    enrollment_no = models.CharField(max_length=30, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    # Only set when status becomes "checked" — NOT on "partially_checked".
    checked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["series", "student"],
                name="unique_attempt_per_student_per_series",
            ),
        ]

    @transaction.atomic
    def submit(self, answers: dict, files=None):
        """`answers`: {question_id: answer_data dict}. `files`: optional
        mapping (typically `request.FILES`) of `f"answer_{question_id}"`
        -> uploaded file — an image/file answer for a `text` question
        (e.g. a photo of handwritten work). Only ever looked at for
        `text`-type questions; auto-graded types ignore it even if one is
        sent, since there's nothing to review there. Bulk-creates one
        `QuestionResponse` per question, auto-grades the auto-gradable
        types immediately, and resolves the attempt straight to
        `checked` (releasing escrow, if paid) when there are no `text`
        questions at all — a fully-objective series shouldn't wait on a
        reviewer who has nothing to review."""
        from core.models import Notification

        files = files or {}
        questions = list(self.series.questions.all())
        responses = []
        for question in questions:
            answer_data = answers.get(str(question.id), {})
            is_auto_graded = question.question_type != Question.QuestionType.TEXT
            is_correct, marks_awarded = question.auto_grade(answer_data)
            answer_attachment = None if is_auto_graded else files.get(f"answer_{question.id}")
            responses.append(
                QuestionResponse(
                    attempt=self,
                    question=question,
                    answer_data=answer_data,
                    answer_attachment=answer_attachment,
                    is_auto_graded=is_auto_graded,
                    is_correct=is_correct,
                    marks_awarded=marks_awarded,
                )
            )
        QuestionResponse.objects.bulk_create(responses)

        self.auto_score = sum(r.marks_awarded or 0 for r in responses if r.is_auto_graded)

        has_pending_text = any(q.question_type == Question.QuestionType.TEXT for q in questions)
        self.submitted_at = timezone.now()

        if not has_pending_text:
            # Fully auto-gradable series — resolves straight to checked,
            # no manual-review step exists for this attempt at all.
            self.final_score = self.auto_score
            self.status = self.Status.CHECKED
            self.checked_at = timezone.now()
            self.save(update_fields=["auto_score", "final_score", "status", "submitted_at", "checked_at"])

            purchase = getattr(self, "purchase", None)
            if self.series.is_paid and purchase and purchase.status == TestSeriesPurchase.Status.ESCROWED:
                purchase.release()
            _notify(
                recipient=self.student,
                notif_type=Notification.NotifType.TESTSERIES_CHECKED,
                title="Test checked",
                message=f"Your test '{self.series.title}' has been checked. Score: {self.final_score}",
                data={"attempt_id": str(self.id), "series_id": str(self.series_id)},
            )
        else:
            self.status = self.Status.PARTIALLY_CHECKED
            self.save(update_fields=["auto_score", "status", "submitted_at"])

    @transaction.atomic
    def mark_answer_and_maybe_finalize(self, *, question: Question, marks_awarded: int, feedback: str = "", reviewer=None):
        """Reviewer grades one `text` response. If that was the LAST
        pending `text` response on this attempt, finalizes the whole
        attempt (final_score, status=checked, payout release,
        notification) in the same call. If other `text` responses are
        still pending, only this one response's review is saved — no
        notification/payout on partial progress (escrow holds until
        fully checked, §8)."""
        from core.models import Notification

        response = self.responses.select_related("question").get(question=question)
        response.mark_answer(marks_awarded, feedback=feedback, reviewer=reviewer)

        still_pending = self.responses.filter(is_auto_graded=False, marks_awarded__isnull=True).exists()
        if still_pending:
            return

        self.final_score = self.responses.aggregate(total=models.Sum("marks_awarded"))["total"] or 0
        self.status = self.Status.CHECKED
        self.checked_at = timezone.now()
        self.checked_by = reviewer
        self.save(update_fields=["final_score", "status", "checked_at", "checked_by"])

        purchase = getattr(self, "purchase", None)
        if self.series.is_paid and purchase and purchase.status == TestSeriesPurchase.Status.ESCROWED:
            purchase.release()

        _notify(
            recipient=self.student,
            notif_type=Notification.NotifType.TESTSERIES_CHECKED,
            title="Test checked",
            message=f"Your test '{self.series.title}' has been checked. Score: {self.final_score}",
            data={"attempt_id": str(self.id), "series_id": str(self.series_id)},
        )

    def __str__(self):
        return f"Attempt: {self.student} on {self.series} [{self.status}]"