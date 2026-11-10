# login/tests_auth_hardening.py
"""
L1 auth hardening -- gaps 1, 3, 5, 6 from the A1 audit.

  gap 1  password reset / change end the old sessions (and refresh tokens)
  gap 3  server-side OTP resend cooldown, hourly cap, no repeated code
  gap 5  usernames can't hijack an email/phone lookup
  gap 6  wrong-password lockout with a retry_after countdown

Everything goes through the real URLs, like login/tests.py. The abuse counters
live in the cache, so every test starts from ``cache.clear()``.
"""
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AuthToken, OTPVerification, User

OLD_PW = "0ld!Passw0rd"
NEW_PW = "N3w!Passw0rd"


def _code_from_outbox():
    body = mail.outbox[-1].body
    return next(t for t in body.split() if t.rstrip(".").isdigit()).rstrip(".")


def _login(client, username, password, **extra):
    return client.post("/login/", {"username": username, "password": password}, format="json", **extra)


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


def _whoami(client, access):
    """Any authenticated endpoint -- 200 while the session is alive."""
    return client.get("/login/auth/delete-account/", **_bearer(access))


class SessionRevocationTests(TestCase):
    """gap 1"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="rita", email="rita@example.com", password=OLD_PW)

    def _tokens(self):
        res = _login(self.client, "rita", OLD_PW)
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["token"]

    def test_reset_password_ends_every_session(self):
        a, b = self._tokens(), self._tokens()
        self.assertEqual(_whoami(self.client, a["access"]).status_code, 200)
        self.assertEqual(_whoami(self.client, b["access"]).status_code, 200)

        r = self.client.post("/login/auth/forgot-password/", {"email_or_phone": "rita@example.com"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        r = self.client.post(
            "/login/auth/reset-password/",
            {
                "email_or_phone": "rita@example.com",
                "otp": _code_from_outbox(),
                "new_password": NEW_PW,
                "confirm_password": NEW_PW,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)

        for tok in (a, b):
            res = _whoami(self.client, tok["access"])
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.data["code"], "TOKEN_EXPIRED")
            # ...and the refresh token can't mint a new access token either.
            res = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
            self.assertEqual(res.status_code, 401)
        self.assertFalse(AuthToken.objects.filter(user=self.user).exists())

        self.assertEqual(_login(self.client, "rita", OLD_PW).status_code, 401)
        self.assertEqual(_login(self.client, "rita", NEW_PW).status_code, 200)

    def test_change_password_ends_other_sessions_but_keeps_this_one(self):
        a, b = self._tokens(), self._tokens()
        res = self.client.post(
            "/login/auth/change-password/",
            {"current_password": OLD_PW, "new_password": NEW_PW, "confirm_password": NEW_PW},
            format="json",
            **_bearer(a["access"]),
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["other_sessions_logged_out"], 1)

        self.assertEqual(_whoami(self.client, a["access"]).status_code, 200)
        self.assertEqual(_whoami(self.client, b["access"]).status_code, 401)
        self.assertEqual(
            self.client.post("/login/auth/token/refresh/", {"refresh": b["refresh"]}, format="json").status_code, 401
        )
        self.assertEqual(
            self.client.post("/login/auth/token/refresh/", {"refresh": a["refresh"]}, format="json").status_code, 200
        )

    def test_refresh_still_works_for_a_live_session(self):
        tok = self._tokens()
        res = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIn("access", res.data)
        self.assertEqual(_whoami(self.client, res.data["access"]).status_code, 200)


class OtpResendLimitTests(TestCase):
    """gap 3"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _send(self, target="sam@example.com", url="/login/auth/send-otp/"):
        return self.client.post(url, {"email_or_phone": target}, format="json")

    def test_second_send_inside_cooldown_is_refused_with_retry_after(self):
        self.assertEqual(self._send().status_code, 200)
        res = self._send()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "otp_cooldown")
        self.assertTrue(1 <= res.data["retry_after"] <= 30)
        self.assertIn("Retry-After", res)
        self.assertEqual(len(mail.outbox), 1)  # no second email was sent

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_fourth_code_in_an_hour_is_refused(self):
        for _ in range(3):
            self.assertEqual(self._send().status_code, 200)
        res = self._send()
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "otp_hourly_limit")
        self.assertGreater(res.data["retry_after"], 60)
        self.assertEqual(len(mail.outbox), 3)

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_a_new_code_is_never_the_same_as_the_previous_one(self):
        # draws: 1st send -> 100000 ; 2nd send -> 100000 again (rejected) -> 100007
        with mock.patch("login.otp_service.secrets.randbelow", side_effect=[0, 0, 7]):
            self.assertEqual(self._send().status_code, 200)
            self.assertEqual(self._send().status_code, 200)
        row = OTPVerification.objects.get(target="sam@example.com")
        self.assertTrue(row.check_otp("100007"))
        self.assertFalse(row.check_otp("100000"))

    def test_failed_delivery_does_not_start_a_cooldown(self):
        with mock.patch("login.otp_service.send_mail", side_effect=Exception("smtp down")):
            res = self._send()
        self.assertEqual(res.status_code, 503)
        # the user is not punished for OUR failure: retrying works immediately
        self.assertEqual(self._send().status_code, 200)

    def test_forgot_password_limits_apply_whether_or_not_the_account_exists(self):
        User.objects.create_user(username="real", email="real@example.com", password=OLD_PW)
        url = "/login/auth/forgot-password/"
        first = [self._send("real@example.com", url), self._send("ghost@example.com", url)]
        self.assertEqual([r.status_code for r in first], [200, 200])
        second = [self._send("real@example.com", url), self._send("ghost@example.com", url)]
        # identical for both -> the limit can't be used to find out who has an account
        self.assertEqual([r.status_code for r in second], [429, 429])
        self.assertEqual({r.data["code"] for r in second}, {"otp_cooldown"})
        self.assertEqual(len(mail.outbox), 1)  # only the real account was emailed

    def test_target_that_is_neither_email_nor_phone_is_rejected(self):
        res = self._send("just-a-username")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "invalid_identifier")
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
    def test_resending_clears_the_verified_flag(self):
        self._send()
        res = self.client.post(
            "/login/auth/verify-otp/",
            {"email_or_phone": "sam@example.com", "otp": _code_from_outbox()},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(OTPVerification.objects.get(target="sam@example.com").is_verified)
        self._send()
        self.assertFalse(OTPVerification.objects.get(target="sam@example.com").is_verified)


class IdentifierConfusionTests(TestCase):
    """gap 5"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.victim = User.objects.create_user(username="victim", email="victim@example.com", password=OLD_PW)
        # Created through the ORM on purpose: signup now refuses such a username,
        # but accounts like this may already exist in the database.
        self.attacker = User.objects.create_user(
            username="victim@example.com", email="attacker@example.com", password="Att4cker!Pass"
        )

    def test_email_login_resolves_to_the_email_owner_and_does_not_crash(self):
        res = _login(self.client, "victim@example.com", OLD_PW)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["user"]["id"], self.victim.id)

    def test_attacker_cannot_use_the_look_alike_username(self):
        res = _login(self.client, "victim@example.com", "Att4cker!Pass")
        self.assertEqual(res.status_code, 401)

    def test_otp_sent_to_an_email_logs_into_that_emails_owner(self):
        self.client.post("/login/auth/send-otp/", {"email_or_phone": "victim@example.com"}, format="json")
        res = self.client.post(
            "/login/auth/verify-otp/",
            {"email_or_phone": "victim@example.com", "otp": _code_from_outbox()},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["user_exists"])
        self.assertEqual(AuthToken.objects.filter(user=self.victim).count(), 1)
        self.assertEqual(AuthToken.objects.filter(user=self.attacker).count(), 0)

    def _signup(self, **overrides):
        email = overrides.get("email", "new@example.com")
        OTPVerification.objects.update_or_create(target=email, defaults={"is_verified": True})
        data = {
            "username": "newbie",
            "email": email,
            "first_name": "New",
            "last_name": "User",
            "password": OLD_PW,
            "confirm_password": OLD_PW,
            "date_of_birth": "1995-06-15",
        }
        data.update(overrides)
        return self.client.post("/login/signup/", data, format="json")

    def test_signup_refuses_usernames_that_look_like_an_email_or_phone(self):
        for bad in ("someone@example.com", "9876543210", "+919876543210"):
            res = self._signup(username=bad)
            self.assertEqual(res.status_code, 400, (bad, res.data))
            self.assertIn("username", res.data["errors"])

    def test_signup_still_accepts_a_normal_username(self):
        self.assertEqual(self._signup().status_code, 201)


class LoginLockoutTests(TestCase):
    """gap 6"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="lena", email="lena@example.com", password=OLD_PW)

    def test_five_wrong_passwords_lock_login_even_for_the_right_password(self):
        lefts = []
        for _ in range(4):
            res = _login(self.client, "lena", "wrong")
            self.assertEqual(res.status_code, 401)
            lefts.append(res.data["attempts_left"])
        self.assertEqual(lefts, [4, 3, 2, 1])

        res = _login(self.client, "lena", "wrong")  # the 5th wrong try trips the lock
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "login_locked")
        self.assertTrue(0 < res.data["retry_after"] <= 900)

        res = _login(self.client, "lena", OLD_PW)  # correct password, still refused
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "login_locked")
        self.assertIn("Retry-After", res)

    def test_other_accounts_are_not_affected(self):
        User.objects.create_user(username="mo", email="mo@example.com", password=OLD_PW)
        for _ in range(5):
            _login(self.client, "lena", "wrong")
        self.assertEqual(_login(self.client, "mo", OLD_PW).status_code, 200)

    def test_a_successful_login_resets_the_counter(self):
        for _ in range(3):
            _login(self.client, "lena", "wrong")
        self.assertEqual(_login(self.client, "lena", OLD_PW).status_code, 200)
        for _ in range(3):
            self.assertEqual(_login(self.client, "lena", "wrong").status_code, 401)

    def test_unknown_usernames_lock_too_so_nothing_leaks(self):
        for _ in range(5):
            _login(self.client, "ghost", "wrong")
        res = _login(self.client, "ghost", "wrong")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "login_locked")

    @override_settings(LOGIN_MAX_FAILURES=100, LOGIN_ACCOUNT_MAX_FAILURES=3)
    def test_one_account_guessed_from_many_ips_is_locked(self):
        for i in range(3):
            _login(self.client, "lena", "wrong", REMOTE_ADDR=f"10.0.0.{i + 1}")
        res = _login(self.client, "lena", OLD_PW, REMOTE_ADDR="10.0.0.99")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.data["code"], "login_locked")

    def test_password_reset_clears_the_lock(self):
        for _ in range(5):
            _login(self.client, "lena", "wrong")
        self.assertEqual(_login(self.client, "lena", OLD_PW).status_code, 429)

        self.client.post("/login/auth/forgot-password/", {"email_or_phone": "lena@example.com"}, format="json")
        res = self.client.post(
            "/login/auth/reset-password/",
            {
                "email_or_phone": "lena@example.com",
                "otp": _code_from_outbox(),
                "new_password": NEW_PW,
                "confirm_password": NEW_PW,
            },
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(_login(self.client, "lena", NEW_PW).status_code, 200)
