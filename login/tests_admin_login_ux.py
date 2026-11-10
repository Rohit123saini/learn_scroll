# login/tests_admin_login_ux.py
"""L1 gap 20 (written, not yet run): admin lockout time left, login-page hints, public 2FA guide."""
from django.core.cache import cache
from django.test import TestCase

from .models import User

PW = "Tr0ub4dor&3-Horse-Staple-9!"


class AdminLoginUxTests(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.create_user(username="boss", email="boss@example.com", password=PW, is_staff=True, is_superuser=True)

    def _post(self, password):
        return self.client.post("/admin/login/", {"username": "boss", "password": password, "next": "/admin/"})

    def test_lockout_message_says_how_long_is_left(self):
        for _ in range(5):
            self._post("wrong-password")
        res = self._post(PW)  # locked even with the right password
        self.assertContains(res, "Try again in 15 minutes")

    def test_login_page_shows_the_staff_password_rules(self):
        res = self.client.get("/admin/login/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Staff password rules")
        self.assertContains(res, "14 characters")

    def test_2fa_guide_is_public_and_names_the_command(self):
        res = self.client.get("/admin/2fa-guide/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "setup_staff_2fa")
        self.assertContains(res, "&lt;your-username&gt;")

    def test_2fa_guide_fills_in_the_signed_in_username(self):
        self.client.force_login(User.objects.get(username="boss"))
        res = self.client.get("/admin/2fa-guide/")
        self.assertContains(res, "setup_staff_2fa boss")
        self.assertContains(res, "do <strong>not</strong> have 2FA")
