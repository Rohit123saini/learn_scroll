"""
tuitionclass/test_trial_classes.py

TASK G11 (growth_and_feature_tasks.md) — "Free trial / sample class
booster". Covers the new discovery endpoint added in this pass:
ClassroomViewSet.trial_classes() (GET /classrooms/trial-classes/).

The trial-JOIN flow itself (ClassroomTrialAccess, POST .../trial-join/)
already has coverage elsewhere — this file is scoped to the new
discovery/ranking surface only:
    1. Only is_trial_enabled + trial_password-set classrooms are returned
       (and only one PER SUBJECT — the higher-rated one wins).
    2. is_live_now reflects a genuinely running ClassSession, not just the trial
       flag.
    3. is_recommended_for_you / ranking follows the caller's own
       post.UserInterest selections, text-matched against subject/title.
    4. trial_password never leaks (same write-only contract as every
       other classroom list endpoint).

Run: python manage.py test tuitionclass.test_trial_classes

Same environment note as every other test file added in this project:
no Django/DRF install or manage.py available in this container, so this
was written and reviewed line-by-line against the actual model/
serializer/view code rather than executed here — run it before merging.
"""

from decimal import Decimal

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from .models import ClassSession, Classroom
from .tests import TuitionClassTestBase


class TrialClassesDiscoveryTests(TuitionClassTestBase):
    def setUp(self):
        super().setUp()
        # self.classroom ("DSA Batch", teacher=self.teacher) from the base
        # fixture is NOT trial-enabled by default — leave it that way to
        # double as the "must be excluded" control case.

        self.trial_math = Classroom.objects.create(
            teacher=self.teacher,
            title="Algebra Foundations",
            subject="Mathematics",
            is_trial_enabled=True,
            rating_avg=Decimal("4.2"),
            enrolled_count=50,
        )
        self.trial_math.set_trial_password("mathcode")
        self.trial_math.save()

        # A second, lower-rated Mathematics trial classroom — must be the
        # one EXCLUDED by the one-per-subject rule (trial_math outranks it).
        self.trial_math_lower = Classroom.objects.create(
            teacher=self.other_teacher,
            title="Basic Arithmetic",
            subject="Mathematics",
            is_trial_enabled=True,
            rating_avg=Decimal("3.0"),
            enrolled_count=5,
        )
        self.trial_math_lower.set_trial_password("mathcode2")
        self.trial_math_lower.save()

        self.trial_science = Classroom.objects.create(
            teacher=self.other_teacher,
            title="Physics Basics",
            subject="Science",
            is_trial_enabled=True,
            rating_avg=Decimal("4.0"),
            enrolled_count=20,
        )
        self.trial_science.set_trial_password("scicode")
        self.trial_science.save()

        # Trial switched on but no code set is invalid per
        # ClassroomSerializer.validate() and shouldn't happen via the API,
        # but a row could still reach this state via a data migration/raw
        # .update() — must never surface as a "try this now" card with no
        # way to actually join it.
        self.trial_no_password = Classroom.objects.create(
            teacher=self.teacher,
            title="History 101",
            subject="History",
            is_trial_enabled=True,
        )

    def _trial_classes(self, user, **params):
        client = APIClient()
        client.force_authenticate(user)
        url = reverse("classroom-trial-classes")
        return client.get(url, params)

    def test_only_trial_enabled_with_password_classrooms_returned(self):
        resp = self._trial_classes(self.student)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data}
        self.assertIn(self.trial_math.title, titles)
        self.assertIn(self.trial_science.title, titles)
        # Not trial-enabled at all:
        self.assertNotIn(self.classroom.title, titles)
        # Trial-enabled but no password set — never a joinable card:
        self.assertNotIn(self.trial_no_password.title, titles)

    def test_one_classroom_per_subject_highest_rated_wins(self):
        resp = self._trial_classes(self.student)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {row["title"] for row in resp.data}
        self.assertIn(self.trial_math.title, titles)
        self.assertNotIn(self.trial_math_lower.title, titles)
        subjects = [row["subject"] for row in resp.data]
        self.assertEqual(len(subjects), len(set(subjects)), "expected at most one card per subject")

    def test_trial_password_never_serialized(self):
        resp = self._trial_classes(self.student)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for row in resp.data:
            self.assertNotIn("trial_password", row)

    def test_is_live_now_true_only_for_a_real_live_session(self):
        self.make_session(classroom=self.trial_science, status_=ClassSession.Status.LIVE)
        resp = self._trial_classes(self.student)
        by_title = {row["title"]: row for row in resp.data}
        self.assertTrue(by_title[self.trial_science.title]["is_live_now"])
        self.assertFalse(by_title[self.trial_math.title]["is_live_now"])

    def test_recommended_for_you_matches_selected_interest_category(self):
        # UserInterest.category is one of Post.CATEGORY_CHOICES
        # ("education", "tech", ...) — 'education' is the one whose
        # display label ("Education") is realistically going to appear in
        # a classroom's own subject/title text.
        from post.models import UserInterest

        history_classroom = Classroom.objects.create(
            teacher=self.teacher,
            title="Education for Everyone",
            subject="General Education",
            is_trial_enabled=True,
            rating_avg=Decimal("3.5"),
        )
        history_classroom.set_trial_password("educode")
        history_classroom.save()

        UserInterest.objects.create(user=self.student, category="education")

        resp = self._trial_classes(self.student)
        by_title = {row["title"]: row for row in resp.data}
        self.assertTrue(by_title[history_classroom.title]["is_recommended_for_you"])
        self.assertFalse(by_title[self.trial_math.title]["is_recommended_for_you"])
        # The matched-interest card should be ranked ahead of a
        # non-matching one in the response ordering.
        order = [row["title"] for row in resp.data]
        self.assertLess(order.index(history_classroom.title), order.index(self.trial_math.title))

    def test_no_interests_selected_still_returns_a_ranked_list(self):
        # No UserInterest rows for this student at all — must fall back to
        # the plain rating/enrollment ranking, never an empty response.
        resp = self._trial_classes(self.student)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreater(len(resp.data), 0)
        for row in resp.data:
            self.assertFalse(row["is_recommended_for_you"])

    def test_limit_param_is_respected_and_capped(self):
        resp = self._trial_classes(self.student, limit="1")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
