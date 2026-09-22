# testseries/models.py
"""
`testseries` app — implements testseries_app_design.md (v1) end to end:
TestSeries, Question, QuestionResponse, TestSeriesPurchase, TestAttempt.

Golden rule (same as `assigments`): this app NEVER imports `campus` or
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

✅ Task 28 — RESOLVED. Multi-attempt (`attempts_allowed > 1`) is now
implemented: `TestAttempt`'s uniqueness constraint is scoped to
`(series, student, attempt_number)` (was `(series, student)` — the
actual blocker), `TestSeriesPurchase.purchase_and_start_attempt()` now
actually passes the computed attempt number through to
`TestAttempt.objects.create()` (previously computed but silently
unused, so every paid attempt landed on the default `attempt_number=1`
regardless), and the `attempts_allowed` cap is enforced as a clean 400
in `TestAttemptStartSerializer.validate()` (serializers.py) before
`TestAttemptViewSet.start()` (views.py) creates a row through either
the paid or free path. `TestSeriesReview` staying "one per series
ever, not one per attempt" was re-confirmed as intended — see that
model's docstring for the one open question this pass left flagged
(which attempt's checked-status gates review eligibility once a
student has more than one).

⚠️ NEW GAP (Task 15, this pass) — `TestSeriesReview.create_review()`
below references `core.models.Notification.NotifType.
TESTSERIES_REVIEW_RECEIVED`, same lazy-import pattern as every other
`_notify()` call site in this file. That enum member does not exist on
`core.models.Notification.NotifType` as of this pass (only the three
listed above are confirmed) — flagged explicitly rather than guessed
at, same as the now-resolved `TESTSERIES_POSTED` gap was. Until `core`
adds it, `create_review()` will raise `AttributeError` at the point of
the notify call — i.e. review creation itself (the row + uniqueness +
checked-status guard) is real and testable independently, but the
notify-the-creator step needs that enum member added first.

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
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import IntegrityError, models, transaction
from django.utils import timezone

from login.models import User

from . import policy

from common.attachment_validators import (
    ATTACHMENT_ALLOWED_EXTENSIONS,  # noqa: F401 — re-exported, some callers import it from here
    ATTACHMENT_MAX_SIZE_MB,  # noqa: F401 — re-exported, some callers import it from here
    attachment_extension_validator,
    validate_attachment_size,
)
from common.question_grading import auto_grade as _shared_auto_grade

# ---------------------------------------------------------------------
# Attachment validation now LIVES in common/attachment_validators.py —
# shared with `assigments` (Task 7) so both apps enforce identical
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
    """UUID PK — same reasoning as `campus`/`assigments`: these rows get
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

    # [Task 28 — RESOLVED] Multi-attempt is now real: uniqueness on
    # `TestAttempt` is scoped to (series, student, attempt_number) —
    # see that model's constraint — and this field is the cap on how
    # many attempt_numbers a student can ever create, enforced as a
    # clean 400 in `TestAttemptStartSerializer.validate()`
    # (serializers.py) before `TestAttemptViewSet.start()` (views.py)
    # creates a row through either the paid or free path.
    attempts_allowed = models.PositiveIntegerField(default=1)

    # ------------------------------------------------------------------
    # ADVANCED DELIVERY (migration 0003). Every field below has a default
    # that reproduces the pre-existing behaviour exactly (self-paced, no
    # proctoring, no certificate, instant results), so existing series and
    # existing clients keep working unchanged.
    # ------------------------------------------------------------------
    class DeliveryMode(models.TextChoices):
        SELF_PACED = "self_paced", "Self paced"
        SCHEDULED = "scheduled", "Scheduled window"
        LIVE = "live", "Live (video)"

    class Proctoring(models.TextChoices):
        OFF = "off", "Off"
        CAMERA = "camera", "Camera (recorded)"

    class ResultRelease(models.TextChoices):
        INSTANT = "instant", "Instantly after checking"
        AFTER_END = "after_end", "After the test window ends"
        MANUAL = "manual", "When the creator releases them"

    # self_paced: start any time.  scheduled: start any time inside
    # [starts_at, ends_at).  live: everyone starts together at starts_at
    # (late entry allowed for `late_entry_minutes`), hosted on a live video
    # room, hard stop at ends_at.
    delivery_mode = models.CharField(
        max_length=12, choices=DeliveryMode.choices, default=DeliveryMode.SELF_PACED, db_index=True
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    late_entry_minutes = models.PositiveSmallIntegerField(default=0)

    # camera: each attempt gets its own recorded video room the creator can
    # review afterwards. record_live: save the host's live-session video so
    # students can replay it together with the test.
    proctoring = models.CharField(max_length=8, choices=Proctoring.choices, default=Proctoring.OFF)
    record_live = models.BooleanField(default=True)

    # Certification. `pass_percentage` alone gives pass/fail; add
    # `certificate_enabled` and a passing attempt automatically earns a
    # verifiable certificate.
    pass_percentage = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    certificate_enabled = models.BooleanField(default=False)
    certificate_title = models.CharField(max_length=200, blank=True)

    # When may a student see their score and the solutions?
    result_release = models.CharField(
        max_length=10, choices=ResultRelease.choices, default=ResultRelease.INSTANT
    )
    results_released_at = models.DateTimeField(null=True, blank=True)
    show_solutions = models.BooleanField(default=True)

    # Public share link: /testseries/public/<share_slug>/ — minted on publish.
    share_slug = models.CharField(max_length=24, unique=True, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["source", "context_type", "context_id"]),
            models.Index(fields=["creator", "status"]),
        ]

    def save(self, *args, **kwargs):
        # Defence-in-depth (NON-strict): any source whose pricing policy is
        # "forbidden" (campus, by default) is forced free no matter what a
        # caller sets, and a free series always has price 0. The strict,
        # user-facing enforcement (400s for "individual must be paid", price
        # ranges, ...) lives in `policy.normalize_pricing(strict=True)`, called
        # by the serializer and the publish action — deliberately NOT here, or
        # every legacy free individual series would start failing on
        # unrelated saves such as `recompute_total_marks()`.
        self.is_paid, self.price_coins = policy.normalize_pricing(
            source=self.source, is_paid=self.is_paid, price_coins=self.price_coins, strict=False
        )
        super().save(*args, **kwargs)

    # -- advanced-delivery helpers ------------------------------------
    def ensure_share_slug(self, *, save: bool = True) -> str:
        """Mint the public share slug once (idempotent)."""
        if self.share_slug:
            return self.share_slug
        for _ in range(8):
            slug = policy.generate_share_slug()
            if not TestSeries.objects.filter(share_slug=slug).exists():
                self.share_slug = slug
                if save:
                    self.save(update_fields=["share_slug"])
                return slug
        raise RuntimeError("Could not mint a unique share slug.")  # pragma: no cover — 8 collisions in a row

    def window_state(self, now=None) -> str:
        """policy.OPEN / NOT_STARTED / LATE_CLOSED / ENDED — may a student start now?"""
        return policy.window_state(
            mode=self.delivery_mode,
            now=now or timezone.now(),
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            late_entry_minutes=self.late_entry_minutes,
        )

    def results_visible(self, now=None) -> bool:
        return policy.results_visible(
            release_mode=self.result_release,
            now=now or timezone.now(),
            ends_at=self.ends_at,
            released_at=self.results_released_at,
        )

    @property
    def is_live_delivery(self) -> bool:
        return self.delivery_mode == self.DeliveryMode.LIVE

    @property
    def question_count(self) -> int:
        return self.questions.count()

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

    @property
    def review_count(self) -> int:
        """Task 15. Plain `.count()`, not a denormalized field — unlike
        `total_marks` (which is read on every attempt-submit/question-
        edit path and worth caching), review counts aren't on a hot
        read path anywhere yet; add caching later if that changes."""
        return self.reviews.count()

    @property
    def avg_rating(self) -> float | None:
        """`None` (not `0`) when there are no reviews yet — a series
        with zero reviews and a series rated straight `0`s are not the
        same thing, and callers (e.g. a "sort by rating" browse view)
        need to be able to tell them apart."""
        result = self.reviews.aggregate(avg=models.Avg("rating"))["avg"]
        return round(result, 2) if result is not None else None

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

    # ---- advanced (migration 0003) -----------------------------------
    class Difficulty(models.TextChoices):
        EASY = "easy", "Easy"
        MEDIUM = "medium", "Medium"
        HARD = "hard", "Hard"

    # Marks DEDUCTED for a wrong (but attempted) auto-graded answer.
    # Blank answers are never penalised. 0 = no negative marking.
    negative_marks = models.PositiveIntegerField(default=0)
    # Free-text label ("Kinematics") — feeds per-topic accuracy analytics.
    topic = models.CharField(max_length=80, blank=True)
    difficulty = models.CharField(max_length=6, choices=Difficulty.choices, blank=True)
    # Shown with the solution once results are released.
    explanation = models.TextField(blank=True)

    class Meta:
        unique_together = ("series", "order")
        ordering = ["order"]

    def clean(self):
        super().clean()
        if self.marks is not None and self.negative_marks and self.negative_marks > self.marks:
            raise ValidationError("negative_marks cannot be more than the question's marks.")
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
        actual grading logic now lives there (shared with `assigments`,
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

    # Negative marking applied to THIS answer (0 unless it was attempted and
    # wrong on a question with negative_marks > 0). Net score of an attempt =
    # sum(marks_awarded) - sum(penalty), floored at 0 (see policy.net_score).
    penalty = models.PositiveIntegerField(default=0)
    # Client-reported seconds spent on the question (optional; analytics only).
    time_spent_seconds = models.PositiveIntegerField(null=True, blank=True)

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
        that to a 402, same as the campus fee module.

        [FIX — Task 28] `attempt_no` was previously computed here but
        never actually passed to `TestAttempt.objects.create()` below —
        every paid attempt silently landed on the model's
        `attempt_number` default (1), so a second paid attempt for the
        same (series, student) always collided with the first one under
        the (then series+student-only) unique constraint instead of
        becoming attempt #2. Now passed through explicitly. Computed
        under the `select_for_update()` lock above (not trusted from a
        caller-supplied value) so a concurrent `start()` call for the
        same student can't race past this count check — the same
        protection `CoinLedger.record_transaction`'s own row lock
        already relies on."""
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
        attempt = TestAttempt.objects.create(
            series=series, student=locked_buyer, attempt_number=attempt_no, **attempt_snapshot_kwargs
        )
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

    # [Task 28 — RESOLVED] Multi-attempt is real: uniqueness is scoped
    # to (series, student, attempt_number) via the constraint below,
    # set from `TestAttempt.objects.filter(series=..., student=...)
    # .count() + 1` at creation time (see `TestSeriesPurchase.
    # purchase_and_start_attempt()` for the paid path and
    # `TestAttemptViewSet.start()` for the free path). The
    # `attempts_allowed` cap (TestSeries) on how many numbers a student
    # can create is enforced in `TestAttemptStartSerializer.validate()`
    # (serializers.py), not here — same "serializer owns the
    # client-facing 400" split this file already uses elsewhere (e.g.
    # `TestSeriesReviewSerializer.validate()`).
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

    # `assigments` app's snapshot pattern — campus/liveclass callers pass
    # these from the roster at submit-time rather than this app resolving
    # them itself (golden rule: no direct campus/liveclass imports).
    roll_number = models.CharField(max_length=30, blank=True)
    enrollment_no = models.CharField(max_length=30, blank=True)

    submitted_at = models.DateTimeField(null=True, blank=True)
    # Only set when status becomes "checked" — NOT on "partially_checked".
    checked_at = models.DateTimeField(null=True, blank=True)

    # ---- server-side timer + autosave (migration 0003) ------------------
    # Set when the attempt row is created (see save()). NULL only on legacy
    # rows created before this migration — those stay untimed rather than
    # being handed a made-up deadline.
    started_at = models.DateTimeField(null=True, blank=True)
    # min(started_at + duration, series.ends_at). NULL = untimed.
    deadline_at = models.DateTimeField(null=True, blank=True)
    # Latest answers the client saved via PATCH /attempts/{id}/save/ —
    # survives app kill / device change, and is what the auto-submit task
    # grades when the student never presses Submit.
    draft_answers = models.JSONField(default=dict, blank=True)
    draft_saved_at = models.DateTimeField(null=True, blank=True)
    # Submitted after deadline + grace (see policy.late_policy()).
    submitted_late = models.BooleanField(default=False)

    # ---- result / certification --------------------------------------
    percentage = models.FloatField(null=True, blank=True)
    # None = the series has no pass mark.
    passed = models.BooleanField(null=True, blank=True)
    # Denormalised count of proctoring events (tab switch, face missing, ...).
    integrity_flags = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            # [FIX — Task 28] Was `fields=["series", "student"]`, which
            # made a second attempt for the same (series, student)
            # impossible at the DB level regardless of `attempt_number`
            # — the real blocker behind multi-attempt never actually
            # working even once the application code tried to create
            # attempt #2. `attempt_number` now part of the key, so each
            # numbered attempt gets its own row; the `attempts_allowed`
            # cap itself is a business rule, not a DB constraint — see
            # `TestAttemptStartSerializer.validate()` (serializers.py).
            models.UniqueConstraint(
                fields=["series", "student", "attempt_number"],
                name="unique_attempt_per_student_series_number",
            ),
        ]

    def save(self, *args, **kwargs):
        """First save of a new attempt stamps `started_at` and the server-side
        `deadline_at`. Timing is a property of the ROW, not of the client, so
        it survives reinstalls, device changes and clock tampering."""
        if self._state.adding and self.started_at is None:
            self.started_at = timezone.now()
            if self.deadline_at is None:
                series = self.series
                self.deadline_at = policy.compute_deadline(
                    started_at=self.started_at,
                    duration_minutes=series.duration_minutes,
                    window_end=series.ends_at if series.delivery_mode != TestSeries.DeliveryMode.SELF_PACED else None,
                )
        super().save(*args, **kwargs)

    def save_progress(self, answers: dict) -> None:
        """Server-side autosave (`PATCH /attempts/{id}/save/`). Only dict
        answers are kept; anything else is dropped rather than trusted."""
        self.draft_answers = {str(k): v for k, v in (answers or {}).items() if isinstance(v, dict)}
        self.draft_saved_at = timezone.now()
        self.save(update_fields=["draft_answers", "draft_saved_at"])

    @staticmethod
    def _clean_seconds(value):
        try:
            seconds = int(value)
        except (TypeError, ValueError):
            return None
        return seconds if 0 <= seconds <= 86_400 else None

    @transaction.atomic
    def submit(self, answers: dict, files=None, timings=None, auto: bool = False):
        """`answers`: {question_id: answer_data dict}. `files`: optional
        mapping (typically `request.FILES`) of `f"answer_{question_id}"`
        -> uploaded file — an image/file answer for a `text` question
        (e.g. a photo of handwritten work). `timings`: optional
        {question_id: seconds}.

        Bulk-creates one `QuestionResponse` per question, auto-grades the
        auto-gradable types immediately (with negative marking), and
        resolves the attempt straight to `checked` when there are no `text`
        questions — a fully-objective series with its answer key already
        filled in never waits on a reviewer.

        Hardening vs. the previous version:
          * a malformed answer (string / list / unhashable options) is graded
            as "no answer" instead of raising a 500 out of the grader;
          * a submit that arrives after `deadline + grace` follows
            `policy.late_policy()` (default: grade the last server-side
            autosave, not the late payload) and sets `submitted_late`;
          * `auto_score` / `final_score` are NET of negative marking.
        """
        files = files or {}
        timings = timings if isinstance(timings, dict) else {}
        answers = answers if isinstance(answers, dict) else {}

        now = timezone.now()
        if auto:
            # Closed by the server (auto-submit task) — the student never pressed
            # Submit, so grade exactly what the autosave already held. This is
            # not "late": there was no late client submission to penalise.
            answers = self.draft_answers if isinstance(self.draft_answers, dict) else {}
            files = {}
        elif policy.is_past_deadline(now=now, deadline=self.deadline_at):
            self.submitted_late = True
            if policy.late_policy() == "use_draft":
                answers = self.draft_answers if isinstance(self.draft_answers, dict) else {}
                files = {}

        questions = list(self.series.questions.all())
        responses = []
        for question in questions:
            answer_data = policy.coerce_answer_data(answers.get(str(question.id)))
            is_auto_graded = question.question_type != Question.QuestionType.TEXT
            try:
                is_correct, marks_awarded = question.auto_grade(answer_data)
            except (TypeError, AttributeError, ValueError):
                # Structurally invalid answer for this question type.
                answer_data = {}
                is_correct, marks_awarded = (False, 0) if is_auto_graded else (None, None)
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
                    penalty=policy.negative_penalty(
                        question_type=question.question_type,
                        is_correct=is_correct,
                        answer_data=answer_data,
                        negative_marks=question.negative_marks,
                    ),
                    time_spent_seconds=self._clean_seconds(timings.get(str(question.id))),
                )
            )
        QuestionResponse.objects.bulk_create(responses)

        self.auto_score = policy.net_score(
            sum(r.marks_awarded or 0 for r in responses if r.is_auto_graded),
            sum(r.penalty for r in responses),
        )
        self.submitted_at = now
        # The draft has served its purpose; keep the row light.
        self.draft_answers = {}

        has_pending_text = any(q.question_type == Question.QuestionType.TEXT for q in questions)
        if not has_pending_text:
            # Fully auto-gradable series — resolves straight to checked,
            # no manual-review step exists for this attempt at all.
            self._finalize(reviewer=None)
        else:
            self.status = self.Status.PARTIALLY_CHECKED
            self.save(update_fields=[
                "auto_score", "submitted_at", "submitted_late", "draft_answers", "status",
            ])

    def _finalize(self, *, reviewer=None):
        """Resolve this attempt to `checked`: net final score, percentage,
        pass/fail, escrow release, student notification, and — if the series
        is certified and the student passed — the certificate. Single exit
        point for both paths (all-auto submit, and last manual review)."""
        from core.models import Notification

        totals = self.responses.aggregate(marks=models.Sum("marks_awarded"), penalty=models.Sum("penalty"))
        self.final_score = policy.net_score(totals["marks"] or 0, totals["penalty"] or 0)
        self.percentage = policy.percentage(self.final_score, self.series.total_marks)
        self.passed = policy.has_passed(self.percentage, self.series.pass_percentage)
        self.status = self.Status.CHECKED
        self.checked_at = timezone.now()
        self.checked_by = reviewer
        self.save(update_fields=[
            "auto_score", "submitted_at", "submitted_late", "draft_answers",
            "final_score", "percentage", "passed", "status", "checked_at", "checked_by",
        ])

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

        if self.series.certificate_enabled and self.passed:
            TestCertificate.issue_for_attempt(self)

    @transaction.atomic
    def mark_answer_and_maybe_finalize(self, *, question: Question, marks_awarded: int, feedback: str = "", reviewer=None):
        """Reviewer grades one `text` response. If that was the LAST
        pending `text` response on this attempt, finalizes the whole
        attempt (see `_finalize`) in the same call. If other `text`
        responses are still pending, only this one response's review is
        saved — no notification/payout on partial progress (escrow holds
        until fully checked, §8)."""
        response = self.responses.select_related("question").get(question=question)
        response.mark_answer(marks_awarded, feedback=feedback, reviewer=reviewer)

        still_pending = self.responses.filter(is_auto_graded=False, marks_awarded__isnull=True).exists()
        if still_pending:
            return
        self._finalize(reviewer=reviewer)

    def __str__(self):
        return f"Attempt: {self.student} on {self.series} [{self.status}]"


class TestSeriesReview(TestSeriesBaseModel):
    """Task 15. A student's rating/review of a `TestSeries`, gated on
    their OWN `TestAttempt` having actually reached `status="checked"`
    — reviewing is about having seen a real result, not merely having
    attempted the series (design doc's exact requirement for this
    task). One review per (series, student) ever — not one per
    attempt — via the `UniqueConstraint` below; now that `attempts_
    allowed` > 1 is real (Task 28), this was re-confirmed rather than
    silently kept as-is: a student still only gets one say on a series
    overall, not one per retry. `attempt` is still stored (as a
    `OneToOneField`, not a plain FK) so a review is traceable back to
    exactly which checked attempt earned it, and so `clean()` below can
    verify status/ownership directly off that FK without a second
    query.

    [Task 28 — FLAGGED, not changed] `TestSeriesReviewSerializer.
    validate()` (serializers.py) looks at the student's LATEST attempt
    only (`order_by("-attempt_number").first()`). With multi-attempt
    now real: a student CHECKED on attempt #1 but mid-way through an
    IN_PROGRESS attempt #2 cannot review yet under this rule, even
    though they do have a checked result. "Latest attempt only" vs
    "any checked attempt is enough" is a genuine product call this
    pass has no confirmed answer for — left as the existing
    latest-only behavior, not silently changed either way.

    Primary validation (attempt-not-checked -> clean 400, already-
    reviewed -> clean 400) lives in `TestSeriesReviewSerializer.
    validate()` (serializers.py), same "serializer owns the client-
    facing 400, model owns defence-in-depth" split `TestSeriesSerializer
    .validate()` already uses for is_paid/price_coins. `clean()`/
    `save()` here are that second layer, not the primary one — they
    exist so `TestSeriesReview.objects.create(...)` can never silently
    create a row that violates either rule even if some future call
    site bypasses the serializer.
    """

    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="reviews")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="testseries_reviews")
    # OneToOne, not a plain FK: a given checked attempt can back at most
    # one review — same "one row per real-world event" reasoning as
    # TestSeriesPurchase.attempt above.
    attempt = models.OneToOneField(TestAttempt, on_delete=models.CASCADE, related_name="review")

    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["series", "student"], name="unique_review_per_student_per_series"),
        ]
        ordering = ["-created_at"]

    def clean(self):
        super().clean()
        if not self.attempt_id:
            return
        if self.student_id and self.attempt.student_id != self.student_id:
            raise ValidationError("attempt must belong to the reviewing student.")
        if self.series_id and self.attempt.series_id != self.series_id:
            raise ValidationError("attempt must belong to the series being reviewed.")
        if self.attempt.status != TestAttempt.Status.CHECKED:
            raise ValidationError("Can only review a series after your attempt has been fully checked.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    @transaction.atomic
    def create_review(cls, *, attempt: "TestAttempt", rating: int, comment: str = "") -> "TestSeriesReview":
        """Single creation entrypoint — `TestSeriesReviewSerializer.
        create()` calls this instead of `TestSeriesReview.objects.
        create()` directly, so the `TESTSERIES_REVIEW_RECEIVED`
        notify-the-creator step can never be forgotten at a call site.
        Same "validation + side effect together, one function" shape as
        `TestSeriesPurchase.purchase_and_start_attempt()` above."""
        from core.models import Notification

        review = cls.objects.create(
            series=attempt.series,
            student=attempt.student,
            attempt=attempt,
            rating=rating,
            comment=comment,
        )
        _notify(
            recipient=review.series.creator,
            notif_type=Notification.NotifType.TESTSERIES_REVIEW_RECEIVED,
            title="New review received",
            message=f"{review.student} rated '{review.series.title}' {review.rating}\u2605.",
            data={"review_id": str(review.id), "series_id": str(review.series_id)},
        )
        return review

    def __str__(self):
        return f"Review: {self.student} -> {self.series} ({self.rating}\u2605)"


# =====================================================================
# ADVANCED FEATURES (migration 0003)
# =====================================================================
class TestCertificate(TestSeriesBaseModel):
    """A verifiable certificate, issued automatically the moment an attempt
    on a `certificate_enabled` series is checked with a passing percentage
    (see `TestAttempt._finalize`).

    One certificate per (series, student): if the student passes on several
    attempts, the FIRST passing attempt earns it — retries never mint
    duplicates. `code` is what appears on the certificate and is looked up
    by the public, throttled, unauthenticated verify endpoint
    (`GET /testseries/certificates/verify/<code>/`), so an employer or
    another institute can confirm it without an account.
    """

    attempt = models.OneToOneField(TestAttempt, on_delete=models.CASCADE, related_name="certificate")
    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="certificates")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="testseries_certificates")

    code = models.CharField(max_length=24, unique=True, db_index=True)
    title = models.CharField(max_length=200)

    # Snapshot at issue time — a certificate must not change if the series
    # is edited or the attempt is later re-evaluated.
    score = models.PositiveIntegerField()
    total_marks = models.PositiveIntegerField()
    percentage = models.FloatField()

    issued_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(fields=["series", "student"], name="unique_certificate_per_student_series"),
        ]

    @property
    def is_valid(self) -> bool:
        return self.revoked_at is None

    @classmethod
    def issue_for_attempt(cls, attempt: "TestAttempt"):
        """Idempotent. Returns `(certificate, created)`."""
        from core.models import Notification

        existing = cls.objects.filter(series=attempt.series, student=attempt.student).first()
        if existing is not None:
            return existing, False

        series = attempt.series
        cert = None
        for _ in range(6):
            try:
                with transaction.atomic():
                    cert = cls.objects.create(
                        attempt=attempt,
                        series=series,
                        student=attempt.student,
                        code=policy.generate_certificate_code(),
                        title=series.certificate_title or series.title,
                        score=attempt.final_score or 0,
                        total_marks=series.total_marks,
                        percentage=attempt.percentage or 0.0,
                    )
                break
            except IntegrityError:
                # Either a (astronomically unlikely) code collision, or we lost
                # a race with a concurrent finalize for the same student.
                existing = cls.objects.filter(series=series, student=attempt.student).first()
                if existing is not None:
                    return existing, False
        if cert is None:  # pragma: no cover — six code collisions in a row
            raise RuntimeError("Could not allocate a unique certificate code.")

        _notify(
            recipient=attempt.student,
            notif_type=Notification.NotifType.CERTIFICATE_ISSUED,
            title="Certificate earned",
            message=f"You passed '{series.title}' — your certificate {cert.code} is ready.",
            data={"certificate_code": cert.code, "series_id": str(series.id), "attempt_id": str(attempt.id)},
        )
        return cert, True

    def revoke(self, reason: str = ""):
        self.revoked_at = timezone.now()
        self.revoked_reason = reason[:200]
        self.save(update_fields=["revoked_at", "revoked_reason"])

    def __str__(self):
        return f"Certificate {self.code} — {self.student} / {self.series}"


class TestLiveSession(TestSeriesBaseModel):
    """The live video room for a `delivery_mode="live"` series: the creator
    hosts (video + audio), students join as viewers while they take the test,
    and — if `series.record_live` — the whole session is recorded to S3 via
    LiveKit egress so it can be replayed alongside the test.

    Deliberately separate from `liveclass.ClassSession` (golden rule: this app
    never imports `liveclass`); it talks to the same LiveKit project through
    `testseries.live`."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        LIVE = "live", "Live"
        ENDED = "ended", "Ended"

    series = models.OneToOneField(TestSeries, on_delete=models.CASCADE, related_name="live_session")
    room_name = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.SCHEDULED, db_index=True)
    host = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="testseries_live_hosted"
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Live session for {self.series_id} [{self.status}]"


class TestRecording(TestSeriesBaseModel):
    """One LiveKit egress job. `LIVE_SESSION` = the host's live room;
    `PROCTOR` = one student's camera room for one attempt. `url` is filled in
    asynchronously by LiveKit's `egress_ended` webhook
    (`POST /testseries/livekit-webhook/`)."""

    class Kind(models.TextChoices):
        LIVE_SESSION = "live_session", "Live session"
        PROCTOR = "proctor", "Proctoring"

    class Status(models.TextChoices):
        RECORDING = "recording", "Recording"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    series = models.ForeignKey(TestSeries, on_delete=models.CASCADE, related_name="recordings")
    attempt = models.ForeignKey(
        TestAttempt, on_delete=models.CASCADE, null=True, blank=True, related_name="recordings"
    )
    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    room_name = models.CharField(max_length=80)
    egress_id = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECORDING, db_index=True)
    url = models.URLField(max_length=500, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["series", "kind"], name="ts_rec_series_kind_idx")]

    def __str__(self):
        return f"{self.get_kind_display()} recording {self.egress_id} [{self.status}]"


class TestProctorEvent(TestSeriesBaseModel):
    """A client-reported integrity signal during an attempt. Advisory only —
    it feeds a review list for the creator; it never auto-fails anyone."""

    class EventType(models.TextChoices):
        APP_BACKGROUND = "app_background", "App sent to background"
        TAB_SWITCH = "tab_switch", "Switched app / tab"
        FACE_MISSING = "face_missing", "Face not visible"
        MULTIPLE_FACES = "multiple_faces", "Multiple faces"
        CAMERA_OFF = "camera_off", "Camera turned off"
        NETWORK_DROP = "network_drop", "Network dropped"
        OTHER = "other", "Other"

    attempt = models.ForeignKey(TestAttempt, on_delete=models.CASCADE, related_name="proctor_events")
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    occurred_at = models.DateTimeField(default=timezone.now)
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["occurred_at"]
        indexes = [models.Index(fields=["attempt", "event_type"], name="ts_pev_attempt_type_idx")]

    def __str__(self):
        return f"{self.event_type} @ {self.occurred_at:%H:%M:%S} (attempt {self.attempt_id})"
