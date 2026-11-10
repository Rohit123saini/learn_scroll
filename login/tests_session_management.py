# login/tests_session_management.py
"""
L1 session gaps 8-11 (written, not yet run -- run with `python manage.py test login`).

  gap 8   short access token + rotating refresh token
  gap 9   rotated / logged-out refresh tokens are blacklisted (token_blacklist installed)
  gap 10  POST /login/auth/logout/ ends the session server-side
  gap 11  device info on sessions, device list, sign one device out, sign out everywhere
"""
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AuthToken, User

PW = "Sup3r!Passw0rd"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


def _device(device_id, name="Pixel 7", platform="android"):
    return {"HTTP_X_DEVICE_ID": device_id, "HTTP_X_DEVICE_NAME": name, "HTTP_X_PLATFORM": platform}


class SessionManagementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="dev", email="dev@example.com", password=PW)

    def _login(self, **extra):
        res = self.client.post("/login/", {"username": "dev", "password": PW}, format="json", **extra)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["token"]

    def _sessions(self, access):
        res = self.client.get("/login/auth/sessions/", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["sessions"]

    # ---- gap 8 / 9 -------------------------------------------------------
    def test_settings_short_access_and_rotation(self):
        jwt = settings.SIMPLE_JWT
        self.assertLessEqual(jwt["ACCESS_TOKEN_LIFETIME"], timedelta(hours=1))
        self.assertTrue(jwt["ROTATE_REFRESH_TOKENS"])
        self.assertIn("rest_framework_simplejwt.token_blacklist", settings.INSTALLED_APPS)

    def test_refresh_rotates_and_old_refresh_is_dead(self):
        tok = self._login()
        res = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("refresh", res.data)
        self.assertNotEqual(res.data["refresh"], tok["refresh"])
        # The rotated token still belongs to the same session...
        self.assertEqual(len(self._sessions(res.data["access"])), 1)
        # ...the old one is blacklisted, the new one works.
        again = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(again.status_code, 401)
        ok = self.client.post("/login/auth/token/refresh/", {"refresh": res.data["refresh"]}, format="json")
        self.assertEqual(ok.status_code, 200)

    # ---- gap 10 ----------------------------------------------------------
    def test_logout_ends_session_and_blacklists_refresh(self):
        tok = self._login()
        res = self.client.post("/login/auth/logout/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["sessions_ended"], 1)
        self.assertFalse(AuthToken.objects.filter(user=self.user).exists())

        me = self.client.get("/login/auth/sessions/", **_bearer(tok["access"]))
        self.assertEqual(me.status_code, 401)
        self.assertEqual(me.data["code"], "TOKEN_EXPIRED")
        refreshed = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(refreshed.status_code, 401)

    def test_logout_is_idempotent_and_never_errors(self):
        tok = self._login()
        for _ in range(2):
            res = self.client.post("/login/auth/logout/", {"refresh": tok["refresh"]}, format="json")
            self.assertEqual(res.status_code, 200)
        res = self.client.post("/login/auth/logout/", {"refresh": "not-a-token"}, format="json")
        self.assertEqual(res.status_code, 200)
        res = self.client.post("/login/auth/logout/", {}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_logout_with_only_the_access_header(self):
        tok = self._login()
        res = self.client.post("/login/auth/logout/", {}, format="json", **_bearer(tok["access"]))
        self.assertEqual(res.status_code, 200)
        self.assertFalse(AuthToken.objects.filter(user=self.user).exists())

    # ---- gap 11 ----------------------------------------------------------
    def test_device_info_is_stored_and_listed(self):
        tok = self._login(**_device("device-aaaa-0001", name="Pixel 7"))
        row = AuthToken.objects.get(user=self.user)
        self.assertEqual(row.device_id, "device-aaaa-0001")
        self.assertEqual(row.device_name, "Pixel 7")
        self.assertEqual(row.platform, "android")

        sessions = self._sessions(tok["access"])
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0]["is_current"])
        self.assertEqual(sessions[0]["device_name"], "Pixel 7")
        self.assertNotIn("jti", sessions[0])

    def test_missing_or_bad_device_headers_fall_back_safely(self):
        self._login(HTTP_USER_AGENT="Mozilla/5.0 (Linux; Android 13) Chrome/120", HTTP_X_DEVICE_ID="bad id!!")
        row = AuthToken.objects.get(user=self.user)
        self.assertEqual(row.device_id, "")
        self.assertEqual(row.platform, "android")
        self.assertEqual(row.device_name, "Android device")

    def test_login_again_on_same_device_replaces_the_old_session(self):
        first = self._login(**_device("device-aaaa-0001"))
        second = self._login(**_device("device-aaaa-0001"))
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(first["access"])).status_code, 401)
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(second["access"])).status_code, 200)

    def test_current_device_listed_first(self):
        a = self._login(**_device("device-aaaa-0001", name="Phone A"))
        b = self._login(**_device("device-bbbb-0002", name="Phone B"))
        for tok, expected in ((a, "Phone A"), (b, "Phone B")):
            sessions = self._sessions(tok["access"])
            self.assertEqual(len(sessions), 2)
            self.assertTrue(sessions[0]["is_current"])
            self.assertEqual(sessions[0]["device_name"], expected)
            self.assertFalse(sessions[1]["is_current"])

    def test_sign_out_one_device(self):
        a = self._login(**_device("device-aaaa-0001", name="Phone A"))
        b = self._login(**_device("device-bbbb-0002", name="Phone B"))
        other = next(s for s in self._sessions(a["access"]) if not s["is_current"])

        res = self.client.delete(f"/login/auth/sessions/{other['id']}/", **_bearer(a["access"]))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["was_current"])
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(b["access"])).status_code, 401)
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(a["access"])).status_code, 200)

        gone = self.client.delete(f"/login/auth/sessions/{other['id']}/", **_bearer(a["access"]))
        self.assertEqual(gone.status_code, 404)

    def test_cannot_touch_another_users_session(self):
        mine = self._login(**_device("device-aaaa-0001"))
        User.objects.create_user(username="eve", email="eve@example.com", password=PW)
        eve = self.client.post("/login/", {"username": "eve", "password": PW}, format="json").data["token"]
        my_id = AuthToken.objects.get(user=self.user).id

        res = self.client.delete(f"/login/auth/sessions/{my_id}/", **_bearer(eve["access"]))
        self.assertEqual(res.status_code, 404)
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(mine["access"])).status_code, 200)

    def test_logout_all_other_devices_keeps_this_one(self):
        a = self._login(**_device("device-aaaa-0001"))
        b = self._login(**_device("device-bbbb-0002"))
        c = self._login(**_device("device-cccc-0003"))
        res = self.client.post("/login/auth/logout-all/", {"keep_current": True}, format="json", **_bearer(b["access"]))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["sessions_ended"], 2)
        self.assertTrue(res.data["current_kept"])
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(b["access"])).status_code, 200)
        for tok in (a, c):
            self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(tok["access"])).status_code, 401)

    def test_logout_all_everywhere(self):
        a = self._login(**_device("device-aaaa-0001"))
        b = self._login(**_device("device-bbbb-0002"))
        res = self.client.post("/login/auth/logout-all/", {}, format="json", **_bearer(a["access"]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["sessions_ended"], 2)
        self.assertFalse(res.data["current_kept"])
        self.assertFalse(AuthToken.objects.filter(user=self.user).exists())
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(b["access"])).status_code, 401)

    @override_settings(AUTH_MAX_SESSIONS_PER_USER=2)
    def test_session_cap_evicts_the_least_recently_used(self):
        first = self._login(**_device("device-aaaa-0001"))
        self._login(**_device("device-bbbb-0002"))
        self._login(**_device("device-cccc-0003"))
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 2)
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(first["access"])).status_code, 401)

    def test_session_endpoints_need_authentication(self):
        self.assertEqual(self.client.get("/login/auth/sessions/").status_code, 401)
        self.assertEqual(self.client.delete("/login/auth/sessions/1/").status_code, 401)
        self.assertEqual(self.client.post("/login/auth/logout-all/", {}, format="json").status_code, 401)
