"""T2.3 - role-based onboarding + Home daily challenge.
Run: python manage.py test core.test_onboarding_roles_daily_challenge"""
from django.test import TestCase
from rest_framework.test import APIClient

from login.models import User
from post.models import UserInterest

QS = "/core/onboarding/quick-start/"
DC = "/core/daily-challenge/"


class RoleOnboardingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="role_user", password="x", email="role@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_options_list_roles(self):
        r = self.client.get("/core/onboarding/options/")
        self.assertEqual({x["key"] for x in r.data["roles"]}, {"student", "teacher", "parent"})

    def test_no_role_behaves_like_student_and_still_requires_class_and_interests(self):
        self.assertEqual(self.client.post(QS, {"interests": ["tech"]}, format="json").status_code, 400)
        self.assertEqual(self.client.post(QS, {"study_class": "class_10", "interests": []}, format="json").status_code, 400)
        r = self.client.post(QS, {"study_class": "class_10", "interests": ["tech"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["role"], "student")
        self.assertIn("daily_challenge", [s["action"] for s in r.data["next_steps"]])

    def test_teacher_needs_no_class_and_gets_teacher_steps(self):
        r = self.client.post(QS, {"role": "teacher", "interests": ["education"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.user.refresh_from_db()
        self.assertEqual((self.user.onboarding_role, self.user.study_class), ("teacher", ""))
        actions = [s["action"] for s in r.data["next_steps"]]
        self.assertIn("create_class", actions)
        self.assertIn("invite_students", actions)

    def test_parent_with_only_a_role(self):
        r = self.client.post(QS, {"role": "parent"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn("parent_code", [s["action"] for s in r.data["next_steps"]])
        self.assertEqual(self.client.get(QS).data["role"], "parent")

    def test_teacher_without_interests_keeps_existing_interests(self):
        UserInterest.objects.create(user=self.user, category="tech")
        self.client.post(QS, {"role": "teacher"}, format="json")
        self.assertTrue(UserInterest.objects.filter(user=self.user, category="tech").exists())

    def test_student_role_only_saves_role_and_keeps_existing_prefs(self):
        self.user.study_class = "class_10"
        self.user.save(update_fields=["study_class"])
        UserInterest.objects.create(user=self.user, category="tech")
        r = self.client.post(QS, {"role": "student"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.user.refresh_from_db()
        self.assertEqual((self.user.onboarding_role, self.user.study_class), ("student", "class_10"))
        self.assertTrue(UserInterest.objects.filter(user=self.user, category="tech").exists())

    def test_student_partial_without_role_key_still_rejected(self):
        self.assertEqual(self.client.post(QS, {"target_exam": "jee"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(QS, {"role": "student", "target_exam": "jee"}, format="json").status_code, 400)

    def test_invalid_role_rejected(self):
        self.assertEqual(self.client.post(QS, {"role": "admin"}, format="json").status_code, 400)


class DailyChallengeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dc_user", password="x", email="dc@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_requires_auth(self):
        self.assertIn(APIClient().get(DC).status_code, (401, 403))

    def test_empty_platform_returns_null_challenge_not_an_error(self):
        r = self.client.get(DC)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.data["challenge"])
        self.assertFalse(r.data["completed_today"])
        self.assertEqual(r.data["streak"], 0)

    def test_parent_gets_no_challenge(self):
        self.user.onboarding_role = "parent"
        self.user.save(update_fields=["onboarding_role"])
        self.assertIsNone(self.client.get(DC).data["challenge"])
