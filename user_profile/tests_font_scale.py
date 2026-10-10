"""Accessibility: font_scale preference. Run: python manage.py test user_profile.tests_font_scale"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import UserPreference

User = get_user_model()
URL = "/profile/preferences/me/"


class FontScalePreferenceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fs_user", password="x")
        self.client.force_authenticate(self.user)

    def test_default_is_normal(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["font_scale"], "normal")

    def test_each_step_saves_and_persists(self):
        for step in ("small", "large", "xlarge", "normal"):
            r = self.client.patch(URL, {"font_scale": step}, format="json")
            self.assertEqual(r.status_code, 200, r.data)
            self.assertEqual(UserPreference.for_user(self.user).font_scale, step)

    def test_arbitrary_value_rejected_and_not_saved(self):
        self.client.patch(URL, {"font_scale": "large"}, format="json")
        for bad in ("huge", "2.5", "", "LARGE"):
            r = self.client.patch(URL, {"font_scale": bad}, format="json")
            self.assertEqual(r.status_code, 400, bad)
        self.assertEqual(UserPreference.for_user(self.user).font_scale, "large")

    def test_patching_theme_does_not_reset_font_scale(self):
        self.client.patch(URL, {"font_scale": "xlarge"}, format="json")
        self.client.patch(URL, {"theme": "dark"}, format="json")
        self.assertEqual(UserPreference.for_user(self.user).font_scale, "xlarge")
