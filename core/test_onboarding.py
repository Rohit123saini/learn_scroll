# core/test_onboarding.py
"""
Tests for TASK G18 (growth_and_feature_tasks.md — Empty states &
first-time-user onboarding): `OnboardingProgress` (models.py),
`OnboardingSuggestionsView` and `OnboardingCompleteView` (views.py).

Scope: this is the "Task 3" of the G18 delivery (backend done, frontend
done, this file is the test pass tying both down) — the flow itself was
already built end-to-end in this same upload (see the TASK G18 comment
blocks in models.py/views.py/urls.py and the Flutter side under
`onboarding/screens/onboarding_screen.dart`); nothing here changes that
code, it only pins down the behaviour those docstrings already claim:

  - suggested_users excludes self and anyone already followed, and
    boosts users who've posted in a category the caller picked as an
    interest (post.UserInterest) ahead of pure follower-count ranking.
  - suggested_campuses is APPROVED + is_active only.
  - sample_test_series is PUBLISHED + free + non-campus only, ordered
    by rating (nulls last).
  - OnboardingCompleteView correctly distinguishes completed vs skipped,
    and GET reflects whichever was last set — used by the Flutter side
    (`login_screen.dart`) to decide whether to route a returning user
    back into the flow at all.
  - both endpoints require authentication.

Run: python manage.py test core.test_onboarding
"""
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from login.models import User

from .models import OnboardingProgress


class OnboardingProgressModelTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com",
        )

    def test_is_finished_false_by_default(self):
        progress = OnboardingProgress.objects.create(user=self.student)
        self.assertFalse(progress.is_finished)

    def test_is_finished_true_when_completed(self):
        progress = OnboardingProgress.objects.create(user=self.student, completed=True)
        self.assertTrue(progress.is_finished)

    def test_is_finished_true_when_skipped(self):
        progress = OnboardingProgress.objects.create(user=self.student, skipped=True)
        self.assertTrue(progress.is_finished)

    def test_str_reflects_state(self):
        progress = OnboardingProgress.objects.create(user=self.student)
        self.assertIn("pending", str(progress))
        progress.skipped = True
        progress.save(update_fields=["skipped"])
        self.assertIn("skipped", str(progress))


class OnboardingSuggestionsViewTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_requires_auth(self):
        anon = APIClient()
        response = anon.get("/core/onboarding/suggestions/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_response_shape(self):
        response = self.client.get("/core/onboarding/suggestions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()),
            {"suggested_users", "suggested_campuses", "sample_test_series"},
        )

    # -- suggested_users -----------------------------------------------

    def test_suggested_users_excludes_self(self):
        response = self.client.get("/core/onboarding/suggestions/")
        ids = [u["id"] for u in response.data["suggested_users"]]
        self.assertNotIn(self.student.id, ids)

    def test_suggested_users_excludes_already_followed(self):
        from user_profile.models import Follow

        other = User.objects.create_user(
            username="student2", password="pass12345", email="student2@example.com",
        )
        Follow.objects.create(follower=self.student, following=other, status=Follow.Status.ACCEPTED)

        response = self.client.get("/core/onboarding/suggestions/")
        ids = [u["id"] for u in response.data["suggested_users"]]
        self.assertNotIn(other.id, ids)

    def test_suggested_users_ranked_by_followers_count_without_interests(self):
        popular = User.objects.create_user(
            username="popular", password="pass12345", email="popular@example.com",
        )
        popular.followers_count = 500
        popular.save(update_fields=["followers_count"])

        quiet = User.objects.create_user(
            username="quiet", password="pass12345", email="quiet@example.com",
        )
        quiet.followers_count = 1
        quiet.save(update_fields=["followers_count"])

        response = self.client.get("/core/onboarding/suggestions/")
        ids = [u["id"] for u in response.data["suggested_users"]]
        self.assertLess(ids.index(popular.id), ids.index(quiet.id))

    def test_suggested_users_boosts_interest_match_over_raw_followers(self):
        """A quieter user who's posted in a category the caller picked as
        an interest should outrank a more-followed user who hasn't."""
        from post.models import Post, UserInterest

        UserInterest.objects.create(user=self.student, category="tech")

        loud_unrelated = User.objects.create_user(
            username="loud", password="pass12345", email="loud@example.com",
        )
        loud_unrelated.followers_count = 1000
        loud_unrelated.save(update_fields=["followers_count"])

        quiet_matching = User.objects.create_user(
            username="matcher", password="pass12345", email="matcher@example.com",
        )
        quiet_matching.followers_count = 2
        quiet_matching.save(update_fields=["followers_count"])
        Post.objects.create(user=quiet_matching, category="tech", content="hello world")

        response = self.client.get("/core/onboarding/suggestions/")
        ids = [u["id"] for u in response.data["suggested_users"]]
        self.assertLess(ids.index(quiet_matching.id), ids.index(loud_unrelated.id))

    def test_suggested_users_limited_to_eight(self):
        for i in range(12):
            User.objects.create_user(
                username=f"bulk{i}", password="pass12345", email=f"bulk{i}@example.com",
            )
        response = self.client.get("/core/onboarding/suggestions/")
        self.assertLessEqual(len(response.data["suggested_users"]), 8)

    # -- suggested_campuses ---------------------------------------------

    def test_suggested_campuses_only_approved_and_active(self):
        from campus.models import Campus

        approved = Campus.objects.create(
            created_by=self.student, name="Approved School", type=Campus.CampusType.SCHOOL,
            verification_status=Campus.VerificationStatus.APPROVED, is_active=True,
        )
        Campus.objects.create(
            created_by=self.student, name="Pending School", type=Campus.CampusType.SCHOOL,
            verification_status=Campus.VerificationStatus.PENDING, is_active=True,
        )
        Campus.objects.create(
            created_by=self.student, name="Inactive School", type=Campus.CampusType.SCHOOL,
            verification_status=Campus.VerificationStatus.APPROVED, is_active=False,
        )

        response = self.client.get("/core/onboarding/suggestions/")
        names = [c["name"] for c in response.data["suggested_campuses"]]
        self.assertEqual(names, ["Approved School"])
        self.assertNotIn("Pending School", names)
        self.assertNotIn("Inactive School", names)

    # -- sample_test_series ----------------------------------------------

    def test_sample_test_series_only_published_free_non_campus(self):
        from testseries.models import TestSeries

        TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.INDIVIDUAL, title="Free Published",
            status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
        )
        TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.INDIVIDUAL, title="Draft",
            status=TestSeries.Status.DRAFT, is_paid=False, price_coins=0,
        )
        TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.INDIVIDUAL, title="Paid",
            status=TestSeries.Status.PUBLISHED, is_paid=True, price_coins=50,
        )
        TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.CAMPUS, title="Campus Free",
            status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
        )

        response = self.client.get("/core/onboarding/suggestions/")
        titles = [s["title"] for s in response.data["sample_test_series"]]
        self.assertEqual(titles, ["Free Published"])

    def test_sample_test_series_limited_to_three(self):
        from testseries.models import TestSeries

        for i in range(5):
            TestSeries.objects.create(
                creator=self.student, source=TestSeries.Source.INDIVIDUAL, title=f"Series {i}",
                status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
            )
        response = self.client.get("/core/onboarding/suggestions/")
        self.assertLessEqual(len(response.data["sample_test_series"]), 3)

    def test_sample_test_series_higher_rated_first(self):
        """Ordering uses `.with_rating()` (Task G9) — a review's `attempt`
        must itself belong to that (series, student) pair and be CHECKED,
        per `TestSeriesReview.clean()`, so the fixture builds real
        `TestAttempt` rows rather than reviews in isolation."""
        from testseries.models import TestAttempt, TestSeries, TestSeriesReview

        low = TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.INDIVIDUAL, title="Low Rated",
            status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
        )
        high = TestSeries.objects.create(
            creator=self.student, source=TestSeries.Source.INDIVIDUAL, title="High Rated",
            status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
        )
        reviewer1 = User.objects.create_user(
            username="reviewer1", password="pass12345", email="reviewer1@example.com",
        )
        reviewer2 = User.objects.create_user(
            username="reviewer2", password="pass12345", email="reviewer2@example.com",
        )

        def _checked_attempt(series, student):
            return TestAttempt.objects.create(
                series=series, student=student, status=TestAttempt.Status.CHECKED,
            )

        TestSeriesReview.objects.create(
            series=low, student=reviewer1, attempt=_checked_attempt(low, reviewer1), rating=2,
        )
        TestSeriesReview.objects.create(
            series=high, student=reviewer1, attempt=_checked_attempt(high, reviewer1), rating=5,
        )
        TestSeriesReview.objects.create(
            series=high, student=reviewer2, attempt=_checked_attempt(high, reviewer2), rating=5,
        )

        response = self.client.get("/core/onboarding/suggestions/")
        titles = [s["title"] for s in response.data["sample_test_series"]]
        self.assertEqual(titles[0], "High Rated")


class OnboardingCompleteViewTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_requires_auth(self):
        anon = APIClient()
        response = anon.get("/core/onboarding/complete/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_before_any_progress_is_neither_completed_nor_skipped(self):
        response = self.client.get("/core/onboarding/complete/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"completed": False, "skipped": False})

    def test_post_default_marks_completed_not_skipped(self):
        response = self.client.post("/core/onboarding/complete/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"completed": True, "skipped": False})

        progress = OnboardingProgress.objects.get(user=self.student)
        self.assertTrue(progress.completed)
        self.assertFalse(progress.skipped)
        self.assertIsNotNone(progress.completed_at)

    def test_post_skipped_true_marks_skipped_not_completed(self):
        response = self.client.post("/core/onboarding/complete/", {"skipped": True}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"completed": False, "skipped": True})

        progress = OnboardingProgress.objects.get(user=self.student)
        self.assertFalse(progress.completed)
        self.assertTrue(progress.skipped)

    def test_get_reflects_prior_post(self):
        self.client.post("/core/onboarding/complete/", {"skipped": True}, format="json")
        response = self.client.get("/core/onboarding/complete/")
        self.assertEqual(response.data, {"completed": False, "skipped": True})

    def test_post_is_idempotent_per_user(self):
        """Calling complete twice (e.g. a retried request) shouldn't create
        a second `OnboardingProgress` row — `get_or_create` in the view
        keyed on the OneToOneField is what this pins down."""
        self.client.post("/core/onboarding/complete/", {}, format="json")
        self.client.post("/core/onboarding/complete/", {}, format="json")
        self.assertEqual(OnboardingProgress.objects.filter(user=self.student).count(), 1)
