# testseries/tests.py
"""
Task 17 — sequencing regression-lock tests.

⚠️ ASSUMPTIONS, flagged rather than guessed (same "flag the gap"
convention this codebase already uses elsewhere):

  - No existing `testseries/tests.py` was shared, so this is written as
    a fresh file. If one already exists in your project, merge
    `RegressionLockTests` (and the imports it needs) into it rather
    than overwriting whatever's already there.

  - `_make_user()` below is the ONLY place that constructs a `User`.
    The real custom `User` model wasn't shared, so this tries the
    common `create_user(username=..., password=...)` shape first and
    falls back to `email=` on `TypeError`. If your real `User` model
    needs different required fields (phone/OTP, etc.), fix this one
    helper — nothing else in this file constructs a `User` directly.

  - `test_payout_only_releases_on_checked_not_partially_checked` mocks
    `testseries.models._record_coin_transaction` and
    `testseries.models._notify` rather than exercising the real
    `user_profile.CoinLedger` / `core.models.Notification` machinery —
    neither app's source was shared. This test's actual subject is the
    STATUS state machine (checked vs. partially_checked gating payout
    release), not ledger/notification correctness, which presumably
    already has its own tests elsewhere. Both mocked functions are
    still exercised for real up to the point of the DB write they wrap
    (e.g. `CoinLedger.TransactionType.TESTSERIES_PAYOUT` / `Notification
    .NotifType.TESTSERIES_PAYOUT_RELEASED` still get resolved for real,
    per the module docstring's confirmation that those enum members
    exist) — only the actual ledger/notification row-creation is
    stubbed out.
"""
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from .bridge import ask_query_on_series
from .models import (
    Question,
    TestAttempt,
    TestSeries,
    TestSeriesPurchase,
    TestSeriesReview,
)

User = get_user_model()


def _make_user(username_prefix="user"):
    """See module docstring — the one seam to fix if your real `User`
    model's required fields differ from this guess."""
    unique = uuid.uuid4().hex[:8]
    try:
        return User.objects.create_user(username=f"{username_prefix}_{unique}", password="testpass123")
    except TypeError:
        return User.objects.create_user(email=f"{username_prefix}_{unique}@example.com", password="testpass123")


class RegressionLockTests(TestCase):
    """Task 17 — locks in three sequencing rules from Task 15/16 so a
    future change can't silently regress them:

      1. Reviewing a series before your attempt is `checked` is rejected.
      2. Asking the creator a query before your attempt is `checked` is
         rejected.
      3. Payout escrow only releases once an attempt is fully `checked`
         — never while it's merely `partially_checked` (i.e. never
         before every `text` question on it has been reviewed).
    """

    def setUp(self):
        self.creator = _make_user("creator")
        self.student = _make_user("student")
        self.series = TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL,
            creator=self.creator,
            title="Regression Lock Series",
            status=TestSeries.Status.PUBLISHED,
        )

    # ------------------------------------------------------------------
    # 1. test_review_rejected_before_checked_status
    # ------------------------------------------------------------------
    def test_review_rejected_before_checked_status(self):
        attempt = TestAttempt.objects.create(series=self.series, student=self.student)
        self.assertEqual(attempt.status, TestAttempt.Status.IN_PROGRESS)

        with self.assertRaises(ValidationError):
            TestSeriesReview.create_review(attempt=attempt, rating=5, comment="Too soon")

        self.assertFalse(TestSeriesReview.objects.filter(attempt=attempt).exists())

    # ------------------------------------------------------------------
    # 2. test_query_rejected_before_checked_status
    # ------------------------------------------------------------------
    def test_query_rejected_before_checked_status(self):
        attempt = TestAttempt.objects.create(series=self.series, student=self.student)
        self.assertEqual(attempt.status, TestAttempt.Status.IN_PROGRESS)

        with self.assertRaises(ValueError):
            ask_query_on_series(attempt=attempt, student=self.student, text="Why did I lose marks?")

    # ------------------------------------------------------------------
    # 3. test_payout_only_releases_on_checked_not_partially_checked
    # ------------------------------------------------------------------
    @mock.patch("testseries.models._notify")
    @mock.patch("testseries.models._record_coin_transaction")
    def test_payout_only_releases_on_checked_not_partially_checked(self, mock_record_coin, mock_notify):
        paid_series = TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL,
            creator=self.creator,
            title="Paid Regression Lock Series",
            status=TestSeries.Status.PUBLISHED,
            is_paid=True,
            price_coins=100,
        )
        mcq_question = Question.objects.create(
            series=paid_series, order=1, question_type=Question.QuestionType.MCQ,
            text="2 + 2 = ?", marks=5,
            options=[{"id": "a", "text": "4"}, {"id": "b", "text": "5"}],
            correct_answer={"option_id": "a"},
        )
        text_question = Question.objects.create(
            series=paid_series, order=2, question_type=Question.QuestionType.TEXT,
            text="Explain your reasoning.", marks=10,
        )
        paid_series.recompute_total_marks()

        attempt = TestAttempt.objects.create(series=paid_series, student=self.student)
        purchase = TestSeriesPurchase.objects.create(
            series=paid_series, buyer=self.student, coins_spent=paid_series.price_coins,
            status=TestSeriesPurchase.Status.ESCROWED, attempt=attempt,
        )

        # submit(): the mcq auto-grades instantly, but the text question
        # is still pending review -> PARTIALLY_CHECKED. Escrow must NOT
        # release on this branch at all.
        attempt.submit(answers={str(mcq_question.id): {"option_id": "a"}})
        attempt.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(attempt.status, TestAttempt.Status.PARTIALLY_CHECKED)
        self.assertEqual(purchase.status, TestSeriesPurchase.Status.ESCROWED)
        mock_record_coin.assert_not_called()

        # Reviewer grades the last (only) pending text response -> the
        # attempt finalizes to CHECKED, and ONLY NOW does escrow release.
        attempt.mark_answer_and_maybe_finalize(
            question=text_question, marks_awarded=8, feedback="Good", reviewer=self.creator,
        )
        attempt.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(attempt.status, TestAttempt.Status.CHECKED)
        self.assertEqual(purchase.status, TestSeriesPurchase.Status.RELEASED)
        mock_record_coin.assert_called_once()