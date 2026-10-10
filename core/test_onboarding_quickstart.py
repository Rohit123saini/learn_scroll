"""Onboarding quick-start: class + exam + <=3 interests in one call.
Run: python manage.py test core.test_onboarding_quickstart"""
from django.test import TestCase
from rest_framework.test import APIClient

from login.models import User
from post.models import UserInterest

URL = "/core/onboarding/quick-start/"


class QuickStartTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="qs_user", password="x", email="qs@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, **body):
        base = {"study_class": "class_11", "target_exam": "jee", "interests": ["education", "tech", "sports"]}
        base.update(body)
        return self.client.post(URL, base, format="json")

    def test_requires_auth(self):
        self.assertIn(APIClient().post(URL, {}, format="json").status_code, (401, 403))
        self.assertIn(APIClient().get("/core/onboarding/options/").status_code, (401, 403))

    def test_saves_everything_in_one_call(self):
        r = self._post()
        self.assertEqual(r.status_code, 200, r.data)
        self.user.refresh_from_db()
        self.assertEqual((self.user.study_class, self.user.target_exam), ("class_11", "jee"))
        self.assertEqual(
            set(UserInterest.objects.filter(user=self.user).values_list("category", flat=True)),
            {"education", "tech", "sports"},
        )
        for key in ("suggested_users", "suggested_campuses", "sample_test_series", "interests"):
            self.assertIn(key, r.data)

    def test_more_than_three_interests_rejected_and_nothing_saved(self):
        r = self._post(interests=["education", "tech", "sports", "news"])
        self.assertEqual(r.status_code, 400)
        self.assertIn("interests", r.data)
        self.assertEqual(UserInterest.objects.filter(user=self.user).count(), 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.study_class, "")

    def test_zero_interests_rejected(self):
        self.assertEqual(self._post(interests=[]).status_code, 400)

    def test_unknown_values_rejected(self):
        self.assertEqual(self._post(interests=["nonsense"]).status_code, 400)
        self.assertEqual(self._post(study_class="class_99").status_code, 400)
        self.assertEqual(self._post(target_exam="xyz").status_code, 400)

    def test_duplicates_collapse_and_count_toward_limit_once(self):
        r = self._post(interests=["tech", "tech", "news"])
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["interests"], ["tech", "news"])

    def test_exam_is_optional(self):
        r = self.client.post(URL, {"study_class": "undergrad", "interests": ["tech"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.target_exam, "")

    def test_rerun_replaces_instead_of_accumulating(self):
        self._post(interests=["education", "tech", "sports"])
        self._post(interests=["news"], study_class="class_12", target_exam="neet")
        self.assertEqual(
            list(UserInterest.objects.filter(user=self.user).values_list("category", flat=True)), ["news"]
        )
        self.user.refresh_from_db()
        self.assertEqual((self.user.study_class, self.user.target_exam), ("class_12", "neet"))

    def test_does_not_mark_onboarding_completed(self):
        from core.models import OnboardingProgress

        self._post()
        self.assertFalse(OnboardingProgress.objects.filter(user=self.user, completed=True).exists())

    def test_get_returns_saved_values(self):
        self._post()
        r = self.client.get(URL)
        self.assertEqual(r.data["study_class"], "class_11")
        self.assertEqual(set(r.data["interests"]), {"education", "tech", "sports"})

    def test_options_lists_pickers_without_vague_categories(self):
        r = self.client.get("/core/onboarding/options/")
        self.assertEqual(r.data["max_interests"], 3)
        keys = {i["key"] for i in r.data["interests"]}
        self.assertNotIn("general", keys)
        self.assertNotIn("other", keys)
        self.assertIn("education", keys)
        self.assertIn("jee", {e["key"] for e in r.data["target_exams"]})

    def test_feed_cache_is_invalidated(self):
        from unittest import mock

        with mock.patch("post.feed_cache.invalidate") as inv:
            self._post()
        inv.assert_called_once_with(self.user.pk)


class ExamTunedSampleTestsTests(TestCase):
    def setUp(self):
        from testseries.models import TestSeries

        self.u = User.objects.create_user(username="qs_t", password="x", email="t@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.u)
        mk = lambda title, subject="": TestSeries.objects.create(
            creator=self.u, source=TestSeries.Source.INDIVIDUAL, title=title, subject=subject,
            status=TestSeries.Status.PUBLISHED, is_paid=False, price_coins=0,
        )
        self.generic = mk("General Knowledge Quiz")
        self.jee = mk("Physics Mock 1", subject="JEE Main")
        self.neet = mk("Biology Mock", subject="NEET")

    def _titles(self):
        r = self.client.get("/core/onboarding/suggestions/")
        return [s["title"] for s in r.data["sample_test_series"]]

    def test_matching_exam_comes_first(self):
        self.u.target_exam = "jee"
        self.u.save(update_fields=["target_exam"])
        self.assertEqual(self._titles()[0], "Physics Mock 1")

    def test_no_exam_keeps_old_behaviour(self):
        self.assertEqual(set(self._titles()), {"General Knowledge Quiz", "Physics Mock 1", "Biology Mock"})

    def test_exam_with_no_matches_still_fills_up(self):
        self.u.target_exam = "upsc"
        self.u.save(update_fields=["target_exam"])
        self.assertEqual(len(self._titles()), 3)
