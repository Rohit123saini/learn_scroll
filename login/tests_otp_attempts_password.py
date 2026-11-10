# login/tests_otp_attempts_password.py
"""
L1 gaps 14 + 15 from the A1 audit.

  gap 14  the OTP endpoints tell the app how many wrong tries are left
          (``attempts_left``) and give every failure a machine-matchable ``code``
          -- the signup OTP screen shows "3 attempts left" from it.
  gap 15  signup / change-password / reset-password call Django's
          ``validate_password()`` (common-password list, numeric-only,
          similarity to the person's own details) on top of the composition rules.

Everything goes through the real URLs, like login/tests.py. Abuse counters live
in the cache, so each test starts from ``cache.clear()``.
"""
from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import OTPVerification, User

GOOD_PW = "Str0ng!Pass#9"
PHONE = "+919876543210"
SEND = "/login/auth/send-otp/"
VERIFY = "/login/auth/verify-otp/"
SIGNUP = "/login/signup/"
RESET = "/login/auth/reset-password/"
FORGOT = "/login/auth/forgot-password/"
CHANGE = "/login/auth/change-password/"


def _code_from_outbox():
    body = mail.outbox[-1].body
    return next(t for t in body.split() if t.rstrip(".").isdigit()).rstrip(".")


def _wrong(code):
    """A 6-digit code that is certainly not ``code``."""
    return "000000" if code != "000000" else "111111"


class OtpAttemptsLeftTests(TestCase):
    """gap 14"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.email = "sam@example.com"
        res = self.client.post(SEND, {"email_or_phone": self.email}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.send_res = res
        self.code = _code_from_outbox()

    def _verify(self, otp):
        return self.client.post(VERIFY, {"email_or_phone": self.email, "otp": otp}, format="json")

    def test_send_otp_reports_the_rules_of_the_code(self):
        self.assertEqual(self.send_res.data["max_attempts"], OTPVerification.MAX_ATTEMPTS)
        self.assertEqual(self.send_res.data["expires_in"], OTPVerification.EXPIRY_MINUTES * 60)

    def test_each_wrong_code_counts_down_attempts_left(self):
        lefts = []
        for _ in range(OTPVerification.MAX_ATTEMPTS):
            res = self._verify(_wrong(self.code))
            self.assertEqual(res.status_code, 400, res.data)
            self.assertEqual(res.data["code"], "otp_invalid")
            lefts.append(res.data["attempts_left"])
        self.assertEqual(lefts, [4, 3, 2, 1, 0])
        # the message itself says it too (older app builds only show `message`)
        self.assertIn("No attempts left", res.data["message"])

    def test_message_names_the_remaining_attempts(self):
        res = self._verify(_wrong(self.code))
        self.assertEqual(res.data["message"], "Invalid OTP. 4 attempts left.")

    def test_after_the_last_wrong_try_the_code_is_locked_then_gone(self):
        for _ in range(OTPVerification.MAX_ATTEMPTS):
            self._verify(_wrong(self.code))
        # even the RIGHT code is refused once locked
        locked = self._verify(self.code)
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.data["code"], "otp_locked")
        self.assertEqual(locked.data["attempts_left"], 0)
        gone = self._verify(self.code)
        self.assertEqual(gone.status_code, 400)
        self.assertEqual(gone.data["code"], "otp_not_found")

    def test_correct_code_after_some_wrong_ones_still_works(self):
        self._verify(_wrong(self.code))
        res = self._verify(self.code)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["user_exists"])

    def test_a_fresh_code_resets_the_attempts(self):
        for _ in range(3):
            self._verify(_wrong(self.code))
        self.assertEqual(OTPVerification.objects.get(target=self.email).attempts_left(), 2)
        with override_settings(OTP_RESEND_COOLDOWN_SECONDS=0):
            self.client.post(SEND, {"email_or_phone": self.email}, format="json")
        res = self._verify(_wrong(_code_from_outbox()))
        self.assertEqual(res.data["attempts_left"], OTPVerification.MAX_ATTEMPTS - 1)

    def test_expired_code_has_its_own_code(self):
        OTPVerification.objects.filter(target=self.email).update(
            created_at=timezone.now() - timedelta(minutes=OTPVerification.EXPIRY_MINUTES + 1)
        )
        res = self._verify(self.code)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "otp_expired")

    def test_no_request_has_its_own_code(self):
        res = self.client.post(VERIFY, {"email_or_phone": "nobody@example.com", "otp": "123456"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "otp_not_found")

    def test_register_failed_attempt_is_counted_exactly(self):
        row = OTPVerification.objects.get(target=self.email)
        stale = OTPVerification.objects.get(target=self.email)  # a second copy, like a parallel request
        row.register_failed_attempt()
        stale.register_failed_attempt()
        self.assertEqual(OTPVerification.objects.get(target=self.email).attempts, 2)
        self.assertEqual(stale.attempts, 2)

    def test_reset_password_wrong_code_also_reports_attempts_left(self):
        User.objects.create_user(username="real", email="real@example.com", password=GOOD_PW)
        self.client.post(FORGOT, {"email_or_phone": "real@example.com"}, format="json")
        code = _code_from_outbox()
        res = self.client.post(
            RESET,
            {
                "email_or_phone": "real@example.com",
                "otp": _wrong(code),
                "new_password": "N3w!Passw0rd#1",
                "confirm_password": "N3w!Passw0rd#1",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "otp_invalid")
        self.assertEqual(res.data["attempts_left"], OTPVerification.MAX_ATTEMPTS - 1)
        self.assertIn("reset code", res.data["message"])


class SignupPasswordValidationTests(TestCase):
    """gap 15 -- signup"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        OTPVerification.objects.update_or_create(target=PHONE, defaults={"is_verified": True})

    def _signup(self, **overrides):
        pw = overrides.pop("password", GOOD_PW)
        data = {
            "username": "newbie",
            "first_name": "Nina",
            "last_name": "Rao",
            "phone": PHONE,
            "date_of_birth": "1995-06-15",
            "password": pw,
            "confirm_password": overrides.pop("confirm_password", pw),
        }
        data.update(overrides)
        return self.client.post(SIGNUP, data, format="json")

    def test_a_strong_password_is_accepted(self):
        res = self._signup()
        self.assertEqual(res.status_code, 201, res.data)

    def test_common_password_is_refused_even_though_it_has_every_character_class(self):
        # "P@ssw0rd" has upper + lower + digit + symbol and 8 chars, so only
        # Django's CommonPasswordValidator catches it.
        res = self._signup(password="P@ssw0rd")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("password", res.data["errors"])
        self.assertTrue(any("common" in m for m in res.data["errors"]["password"]))

    def test_password_built_from_the_username_is_refused(self):
        res = self._signup(username="rahulsharma", password="Rahulsharma@1")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("password", res.data["errors"])

    def test_password_containing_the_phone_number_is_refused(self):
        res = self._signup(password="Xy@9876543210")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("phone number", " ".join(res.data["errors"]["password"]))

    def test_any_symbol_counts_as_special(self):
        for pw in ("Gr33n_Lantern", "Gr33n-Lantern", "Gr33n~Lantern"):
            OTPVerification.objects.update_or_create(target=PHONE, defaults={"is_verified": True})
            User.objects.filter(username="newbie").delete()
            res = self._signup(password=pw)
            self.assertEqual(res.status_code, 201, (pw, res.data))

    def test_a_space_is_not_a_special_character(self):
        res = self._signup(password="Green lantern 12")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertTrue(any("special" in m for m in res.data["errors"]["password"]))

    def test_rejected_password_does_not_burn_the_verified_phone(self):
        self._signup(password="P@ssw0rd")
        self.assertTrue(OTPVerification.objects.filter(target=PHONE, is_verified=True).exists())
        self.assertEqual(self._signup().status_code, 201)


class ChangeAndResetPasswordValidationTests(TestCase):
    """gap 15 -- change / reset"""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="lenasharma", email="lena@example.com", first_name="Lena", password=GOOD_PW
        )

    def _login_token(self):
        res = self.client.post("/login/", {"username": "lenasharma", "password": GOOD_PW}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["token"]["access"]

    def test_change_password_refuses_a_common_password(self):
        access = self._login_token()
        res = self.client.post(
            CHANGE,
            {"current_password": GOOD_PW, "new_password": "P@ssw0rd", "confirm_password": "P@ssw0rd"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("new_password", res.data)

    def test_change_password_refuses_one_too_similar_to_the_account(self):
        access = self._login_token()
        res = self.client.post(
            CHANGE,
            {"current_password": GOOD_PW, "new_password": "Lenasharma@1", "confirm_password": "Lenasharma@1"},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn("new_password", res.data)

    def test_reset_password_refuses_a_weak_password_without_spending_the_code(self):
        self.client.post(FORGOT, {"email_or_phone": "lena@example.com"}, format="json")
        code = _code_from_outbox()
        body = {"email_or_phone": "lena@example.com", "otp": code}

        weak = self.client.post(
            RESET, {**body, "new_password": "Lenasharma@1", "confirm_password": "Lenasharma@1"}, format="json"
        )
        self.assertEqual(weak.status_code, 400, weak.data)
        self.assertEqual(weak.data["code"], "weak_password")
        self.assertIn("new_password", weak.data["errors"])
        # the code is still good -> the user just picks a better password
        self.assertTrue(OTPVerification.objects.filter(target="lena@example.com").exists())

        ok = self.client.post(
            RESET, {**body, "new_password": "N3w!Passw0rd#1", "confirm_password": "N3w!Passw0rd#1"}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_reset_password_refuses_a_common_password_at_the_serializer(self):
        self.client.post(FORGOT, {"email_or_phone": "lena@example.com"}, format="json")
        res = self.client.post(
            RESET,
            {
                "email_or_phone": "lena@example.com",
                "otp": _code_from_outbox(),
                "new_password": "P@ssw0rd",
                "confirm_password": "P@ssw0rd",
            },
            format="json",
        )
        self.assertEqual(res.status_code, 400, res.data)
