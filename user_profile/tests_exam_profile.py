# user_profile/tests_exam_profile.py
"""
Study profile on UserPreference (exam target, class, focus subjects, exam date)
+ Exam Mode flag, via the existing PATCH/GET /profile/preferences/me/.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .models import UserPreference

User = get_user_model()
URL = "/profile/preferences/me/"


class ExamProfileApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="stud", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def patch(self, body):
        return self.api.patch(URL, body, format="json")

    def test_defaults_on_get(self):
        d = self.api.get(URL).data["data"]
        self.assertEqual((d["exam_target"], d["class_level"], d["focus_subjects"]), ("", "", []))
        self.assertIsNone(d["exam_date"])
        self.assertFalse(d["exam_mode"])
        self.assertFalse(d["exam_mode_active"])

    def test_save_full_profile(self):
        future = (timezone.localdate() + timedelta(days=30)).isoformat()
        r = self.patch({
            "exam_target": "jee", "class_level": "12",
            "focus_subjects": ["Physics", "Organic Chemistry"],
            "exam_date": future, "exam_mode": True,
        })
        self.assertEqual(r.status_code, 200)
        d = r.data["data"]
        self.assertEqual((d["exam_target"], d["class_level"]), ("jee", "12"))
        self.assertEqual(d["focus_subjects"], ["Physics", "Organic Chemistry"])
        self.assertTrue(d["exam_mode"])
        self.assertTrue(d["exam_mode_active"])

    def test_subjects_are_trimmed_and_deduped_case_insensitively(self):
        r = self.patch({"focus_subjects": ["  Physics ", "physics", "Chem   istry", "PHYSICS"]})
        self.assertEqual(r.data["data"]["focus_subjects"], ["Physics", "Chem istry"])

    def test_validation(self):
        self.assertEqual(self.patch({"exam_target": "mba"}).status_code, 400)
        self.assertEqual(self.patch({"class_level": "13"}).status_code, 400)
        self.assertEqual(self.patch({"focus_subjects": [f"s{i}" for i in range(11)]}).status_code, 400)
        self.assertEqual(self.patch({"focus_subjects": ["x" * 41]}).status_code, 400)
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.patch({"exam_date": past}).status_code, 400)

    def test_can_clear_fields(self):
        self.patch({"exam_target": "neet", "class_level": "11", "focus_subjects": ["Biology"]})
        r = self.patch({"exam_target": "", "class_level": "", "focus_subjects": [], "exam_date": None})
        self.assertEqual(r.status_code, 200)
        d = r.data["data"]
        self.assertEqual((d["exam_target"], d["class_level"], d["focus_subjects"], d["exam_date"]), ("", "", [], None))

    def test_exam_mode_without_date_stays_active(self):
        self.patch({"exam_mode": True})
        self.assertTrue(UserPreference.for_user(self.user).exam_mode_active)

    def test_exam_mode_expires_after_exam_date(self):
        pref = UserPreference.for_user(self.user)
        pref.exam_mode = True
        pref.exam_date = timezone.localdate() - timedelta(days=1)
        pref.save()
        self.assertFalse(pref.exam_mode_active)
        pref.exam_date = timezone.localdate()  # exam din tak active
        self.assertTrue(pref.exam_mode_active)

    def test_exam_mode_off_is_never_active(self):
        pref = UserPreference.for_user(self.user)
        pref.exam_date = timezone.localdate() + timedelta(days=5)
        self.assertFalse(pref.exam_mode_active)

    def test_study_profile_change_invalidates_feed_cache(self):
        with mock.patch("post.feed_cache.invalidate") as inv:
            self.patch({"exam_mode": True})
        inv.assert_called_once_with(self.user.pk)

    def test_theme_only_patch_does_not_invalidate_feed_cache(self):
        with mock.patch("post.feed_cache.invalidate") as inv:
            r = self.patch({"theme": "dark"})
        self.assertEqual(r.status_code, 200)
        inv.assert_not_called()

    def test_cache_failure_does_not_fail_the_request(self):
        with mock.patch("post.feed_cache.invalidate", side_effect=RuntimeError("redis down")):
            self.assertEqual(self.patch({"exam_mode": True}).status_code, 200)

    def test_requires_auth(self):
        self.assertIn(APIClient().patch(URL, {"exam_mode": True}, format="json").status_code, (401, 403))
