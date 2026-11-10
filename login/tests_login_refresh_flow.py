"""P-A2 — login -> authenticated call -> expired access -> refresh -> rotated refresh, as ONE flow.
Run: python manage.py test login.tests_login_refresh_flow   (written by reading the code; NOT run)
Single-step cases already live in tests_session_management.py / tests_auth_hardening.py; this chains them like the app does.
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from .models import User

PW = "Sup3r!Passw0rd"
PROBE = "/login/auth/sessions/"          # any authenticated GET
REFRESH = "/login/auth/token/refresh/"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


class LoginRefreshFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="flow", email="flow@example.com", password=PW)

    def _login(self):
        res = self.client.post("/login/", {"username": "flow", "password": PW}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["token"]

    def test_full_session_lifecycle(self):
        tok = self._login()
        self.assertEqual(self.client.get(PROBE, **_bearer(tok["access"])).status_code, 200)

        r1 = self.client.post(REFRESH, {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(r1.status_code, 200, r1.data)
        self.assertEqual(self.client.get(PROBE, **_bearer(r1.data["access"])).status_code, 200)

        # chain: the rotated refresh works once more, and the first one stays dead
        r2 = self.client.post(REFRESH, {"refresh": r1.data["refresh"]}, format="json")
        self.assertEqual(r2.status_code, 200, r2.data)
        self.assertEqual(self.client.post(REFRESH, {"refresh": tok["refresh"]}, format="json").status_code, 401)

        # still ONE device/session after two rotations
        self.assertEqual(len(self.client.get(PROBE, **_bearer(r2.data["access"])).data["sessions"]), 1)

    def test_expired_access_is_401_and_refresh_recovers(self):
        tok = self._login()
        old = AccessToken(tok["access"])
        old.set_exp(from_time=timezone.now() - timedelta(hours=3), lifetime=timedelta(minutes=1))
        expired = self.client.get(PROBE, **_bearer(str(old)))
        self.assertEqual(expired.status_code, 401)

        r = self.client.post(REFRESH, {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.client.get(PROBE, **_bearer(r.data["access"])).status_code, 200)

    def test_garbage_and_missing_refresh_are_refused(self):
        self._login()
        self.assertIn(self.client.post(REFRESH, {"refresh": "not-a-token"}, format="json").status_code, (400, 401))
        self.assertIn(self.client.post(REFRESH, {}, format="json").status_code, (400, 401))

    def test_logout_then_refresh_then_probe_all_refused(self):
        tok = self._login()
        self.assertEqual(self.client.post("/login/auth/logout/", {"refresh": tok["refresh"]}, format="json").status_code, 200)
        self.assertEqual(self.client.post(REFRESH, {"refresh": tok["refresh"]}, format="json").status_code, 401)
        self.assertEqual(self.client.get(PROBE, **_bearer(tok["access"])).status_code, 401)
