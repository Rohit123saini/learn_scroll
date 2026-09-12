# assignment/tests.py
"""
Model-layer tests — deliberately not view/API tests. This app's actual
complexity lives in the model methods (`submit_freeform`,
`submit_structured`, `mark_answer_and_maybe_finalize`, `publish`/
`unpublish`, `recompute_total_marks`, `can_change_question_mode`); the
view/serializer layer is thin by design (it validates the HTTP boundary
and delegates to these methods, per views.py's own module docstring), so
that's where a test suite's effort belongs first.

NOT covered here (see PRODUCTION_DESIGN.md §4 for why each is flagged
rather than silently skipped): permission/IDOR tests requiring an
`APIClient` + real user-factory fixture, `bridge.py`'s roster-shaped
inputs, and constraint-level concurrency tests requiring
`TransactionTestCase`.
"""
import datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from login.models import User

from .models import Assignment, AssignmentQuestion, AssignmentSource, AssignmentSubmission
from .tasks import send_due_reminders

# [FIX — Task 10] The mcq/msq fixtures below previously used
# `options=["3", "4", "5"]` (plain strings) and `correct_answer="4"` (a
# bare string) — neither shape `AssignmentQuestion.clean()` actually
# accepts (options must be a list of dicts with an `"id"` key;
# correct_answer must be `{"option_id": <one of those ids>}` — see that
# method's own MCQ/MSQ branch). `full_clean()` runs unconditionally from
# `AssignmentQuestion.save()`, so every test below that created an mcq
# question with the old shape would have raised `ValidationError` in
# `setUp()` before a single test method ever ran. Fixed to the shape the
# model actually validates.
#
# [ASSUMPTION — NOT VERIFIED] `answer_data`'s shape for an mcq answer is
# opaque to this app (`common.question_grading.auto_grade()` is the only
# thing that interprets it — see models.py's own note that this module
# wasn't available to verify against). `{"option_id": ...}` is used here
# to mirror `correct_answer`'s confirmed shape; if the real `auto_grade()`
# expects something else for mcq `answer_data`, update these fixtures to
# match it.
MCQ_OPTIONS = [{"id": "opt_3", "text": "3"}, {"id": "opt_4", "text": "4"}, {"id": "opt_5", "text": "5"}]
MCQ_CORRECT_ANSWER = {"option_id": "opt_4"}
MCQ_ANSWER_CORRECT = {"option_id": "opt_4"}
MCQ_ANSWER_WRONG = {"option_id": "opt_3"}


def _make_user(username: str) -> User:
    return User.objects.create(username=username)


def _make_assignment(**kwargs) -> Assignment:
    defaults = {
        "source": AssignmentSource.PERSONAL,
        "title": "Test assignment",
    }
    defaults.update(kwargs)
    return Assignment.objects.create(**defaults)


class FreeformSubmissionTests(TestCase):
    def setUp(self):
        self.student = _make_user("student1")
        self.assignment = _make_assignment(due_date=timezone.now().date() + datetime.timedelta(days=1))
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_submit_on_time_is_submitted(self):
        self.submission.submit_freeform(written_content="my answer")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.SUBMITTED)
        self.assertEqual(self.submission.written_content, "my answer")

    def test_submit_after_due_date_is_late(self):
        self.assignment.due_date = timezone.now().date() - datetime.timedelta(days=1)
        self.assignment.save(update_fields=["due_date"])
        self.submission.submit_freeform(written_content="late answer")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.LATE)
        self.assertTrue(self.submission.is_late())

    def test_grade_freeform_sets_checked(self):
        self.submission.submit_freeform(written_content="answer")
        self.submission.grade_freeform(grade="A", feedback="Nice work")
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.CHECKED)
        self.assertIsNotNone(self.submission.checked_at)
        self.assertEqual(self.submission.grade, "A")


class StructuredSubmissionTests(TestCase):
    def setUp(self):
        self.student = _make_user("student2")
        self.staff = _make_user("staff1")
        self.staff.is_staff = True
        self.staff.save(update_fields=["is_staff"])

        self.assignment = _make_assignment(has_structured_questions=True)
        self.mcq = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=1,
            question_type=AssignmentQuestion.QuestionTypeChoices.MCQ,
            text="2+2?", marks=5, options=MCQ_OPTIONS, correct_answer=MCQ_CORRECT_ANSWER,
        )
        self.text_q = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=2,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT,
            text="Explain your reasoning.", marks=10,
        )
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_total_marks_auto_summed_from_questions(self):
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 15)  # 5 + 10

    def test_mcq_auto_graded_correctly(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        mcq_answer = self.submission.answers.get(question=self.mcq)
        self.assertTrue(mcq_answer.is_auto_graded)
        self.assertTrue(mcq_answer.is_correct)
        self.assertEqual(mcq_answer.marks_awarded, 5)

    def test_pending_text_question_leaves_partially_checked(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.PARTIALLY_CHECKED)
        self.assertIsNone(self.submission.checked_at)

    def test_reviewing_last_pending_answer_finalizes_submission(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_CORRECT},
            {"question_id": self.text_q.id, "answer_data": "because math"},
        ])
        self.submission.mark_answer_and_maybe_finalize(
            question=self.text_q, marks_awarded=8, feedback="Good", reviewed_by=self.staff,
        )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, AssignmentSubmission.SubmissionStatus.CHECKED)
        self.assertEqual(self.submission.total_marks_awarded, 13)  # 5 (mcq) + 8 (text)
        self.assertIsNotNone(self.submission.checked_at)

    def test_wrong_mcq_answer_scores_zero(self):
        self.submission.submit_structured([
            {"question_id": self.mcq.id, "answer_data": MCQ_ANSWER_WRONG},
            {"question_id": self.text_q.id, "answer_data": "guess"},
        ])
        mcq_answer = self.submission.answers.get(question=self.mcq)
        self.assertFalse(mcq_answer.is_correct)
        self.assertEqual(mcq_answer.marks_awarded, 0)


class QuestionModeImmutabilityTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment(has_structured_questions=True)
        self.student = _make_user("student3")

    def test_can_change_mode_with_no_submissions(self):
        self.assertTrue(self.assignment.can_change_question_mode())

    def test_cannot_change_mode_once_a_real_submission_exists(self):
        submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)
        submission.submit_freeform(written_content="x")  # any non-MISSING status
        self.assertFalse(self.assignment.can_change_question_mode())

    def test_missing_only_submissions_do_not_lock_mode(self):
        # A bridge-pre-created MISSING row (roster entry who hasn't
        # touched the assignment yet) must NOT count as "a submission
        # exists" for immutability purposes — only an actual attempt does.
        AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)
        self.assertTrue(self.assignment.can_change_question_mode())


class PublicSlugLifecycleTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment()
        self.student = _make_user("student4")
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def test_publish_sets_a_nonempty_slug(self):
        slug = self.submission.publish()
        self.assertTrue(slug)
        self.assertEqual(self.submission.public_slug, slug)

    def test_republishing_generates_a_new_slug(self):
        first = self.submission.publish()
        second = self.submission.publish()
        self.assertNotEqual(first, second)

    def test_unpublish_blanks_the_slug(self):
        self.submission.publish()
        self.submission.unpublish()
        self.assertEqual(self.submission.public_slug, "")


class RecomputeTotalMarksTests(TestCase):
    def setUp(self):
        self.assignment = _make_assignment(has_structured_questions=True)

    def test_deleting_a_question_reduces_total_marks(self):
        q1 = AssignmentQuestion.objects.create(
            assignment=self.assignment, order=1,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, text="Q1", marks=10,
        )
        AssignmentQuestion.objects.create(
            assignment=self.assignment, order=2,
            question_type=AssignmentQuestion.QuestionTypeChoices.TEXT, text="Q2", marks=15,
        )
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 25)

        q1.delete()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.total_marks, 15)


class DueReminderIdempotencyTests(TestCase):
    """[FIX — Task 10] Covers the acceptance criterion that re-running
    `send_due_reminders()` must not re-notify the same submission — see
    tasks.py's own [FIX — Task 10] docstring note for the cache-based
    dedup this exercises. `create_notification` is mocked rather than
    exercised for real: its exact signature is still an unverified
    assumption (see tasks.py's own docstring), and these tests are about
    the sweep's dedup/retry logic, not `core.services` itself."""

    def setUp(self):
        cache.clear()
        self.student = _make_user("student5")
        self.assignment = _make_assignment(due_date=timezone.now().date())
        self.submission = AssignmentSubmission.objects.create(assignment=self.assignment, student=self.student)

    def tearDown(self):
        cache.clear()

    @patch("assignment.tasks.create_notification")
    def test_running_sweep_twice_sends_only_one_notification(self, mock_create_notification):
        first_count = send_due_reminders(lookahead_hours=24)
        second_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 0)
        self.assertEqual(mock_create_notification.call_count, 1)

    @patch("assignment.tasks.create_notification", side_effect=Exception("boom"))
    def test_failed_notification_is_retried_on_next_sweep(self, mock_create_notification):
        first_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(first_count, 0)  # failed send — not counted, and not marked as sent
        mock_create_notification.side_effect = None
        second_count = send_due_reminders(lookahead_hours=24)
        self.assertEqual(second_count, 1)