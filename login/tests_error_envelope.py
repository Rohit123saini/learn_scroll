# login/tests_error_envelope.py
"""
L1 gap 16 from the A1 audit -- every login-app failure has ONE shape:

    {"detail", "code", "message" (legacy alias of detail), "errors"?, ...extras}

Before, the same app answered {"status","message","code"}, raw serializer
errors ({"field": [...]}) and {"detail","code"} depending on which view failed.

Everything goes through the real URLs, like login/tests.py. Abuse counters live
in the cache, so each test starts from ``cache.clear()``.
"""
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from .models import User

PW = "0ld!Passw0rd"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


class ErrorEnvelopeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="eva", email="eva@example.com", password=PW)

    def assertEnvelope(self, res, status_code, code):
        self.assertEqual(res.status_code, status_code, res.data)
        data = res.data
        self.assertEqual(data["code"], code, data)
        self.assertIsInstance(data["detail"], str)
        self.assertTrue(data["detail"])
        # legacy alias: app builds from before the change only read `message`
        self.assertEqual(data["message"], data["detail"])
        # the old hand-built shape is gone from failures
        self.assertNotIn("status", data)

    def test_wrong_password_is_coded_with_attempts_left(self):
        res = self.client.post("/login/", {"username": "eva", "password": "nope"}, format="json")
        self.assertEnvelope(res, 401, "invalid_credentials")
        self.assertEqual(res.data["attempts_left"], 4)

    def test_lockout_carries_retry_after_in_body_and_header(self):
        for _ in range(5):
            res = self.client.post("/login/", {"username": "eva", "password": "nope"}, format="json")
        self.assertEnvelope(res, 429, "login_locked")
        self.assertIsInstance(res.data["retry_after"], int)
        self.assertEqual(res["Retry-After"], str(res.data["retry_after"]))

    def test_serializer_failure_is_wrapped_not_raw(self):
        res = self.client.post("/login/auth/send-otp/", {}, format="json")
        self.assertEnvelope(res, 400, "validation_error")
        self.assertIn("email_or_phone", res.data["errors"])
        # raw `serializer.errors` used to put the field at the TOP level
        self.assertNotIn("email_or_phone", res.data)

    def test_invalid_identifier(self):
        res = self.client.post("/login/auth/send-otp/", {"email_or_phone": "!!"}, format="json")
        self.assertEnvelope(res, 400, "invalid_identifier")

    def test_otp_not_found(self):
        res = self.client.post(
            "/login/auth/verify-otp/",
            {"email_or_phone": "nobody@example.com", "otp": "123456"},
            format="json",
        )
        self.assertEnvelope(res, 400, "otp_not_found")

    def test_verify_otp_serializer_failure_is_wrapped(self):
        res = self.client.post("/login/auth/verify-otp/", {"email_or_phone": "a@example.com"}, format="json")
        self.assertEnvelope(res, 400, "validation_error")
        self.assertIn("otp", res.data["errors"])

    def test_delete_account_failures_are_coded(self):
        login = self.client.post("/login/", {"username": "eva", "password": PW}, format="json")
        auth = _bearer(login.data["token"]["access"])

        res = self.client.post("/login/auth/delete-account/", {}, format="json", **auth)
        self.assertEnvelope(res, 400, "delete_confirm_required")

        res = self.client.post(
            "/login/auth/delete-account/", {"confirm": "DELETE", "password": "wrong"}, format="json", **auth
        )
        self.assertEnvelope(res, 403, "incorrect_password")

    def test_unknown_device_is_coded(self):
        login = self.client.post("/login/", {"username": "eva", "password": PW}, format="json")
        res = self.client.delete(
            "/login/auth/sessions/999999/", **_bearer(login.data["token"]["access"])
        )
        self.assertEnvelope(res, 404, "session_not_found")

    def test_missing_token_still_uses_the_envelope(self):
        res = self.client.get("/login/auth/delete-account/")
        self.assertEqual(res.status_code, 401)
        self.assertIn("code", res.data)
        self.assertIn("detail", res.data)


class FieldErrorCodeTests(TestCase):
    """L1 gap 17 -- field-level failures also say WHICH rule failed (`error_codes`,
    same shape as `errors`) so the app can answer in Hindi/English by code."""

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="ira", email="ira@example.com", password=PW)
        login = self.client.post("/login/", {"username": "ira", "password": PW}, format="json")
        self.auth = _bearer(login.data["token"]["access"])

    def test_short_password_names_its_rule(self):
        res = self.client.post(
            "/login/auth/change-password/",
            {"current_password": PW, "new_password": "Ab1!", "confirm_password": "Ab1!"},
            format="json",
            **self.auth,
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "validation_error")
        self.assertEqual(res.data["error_codes"]["new_password"], ["password_min_length"])

    def test_mismatch_has_its_own_code(self):
        res = self.client.post(
            "/login/auth/change-password/",
            {"current_password": PW, "new_password": "Str0ng!Pass#9", "confirm_password": "Str0ng!Pass#8"},
            format="json",
            **self.auth,
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["error_codes"]["confirm_password"], ["passwords_mismatch"])

    def test_future_dob_has_its_own_code(self):
        res = self.client.post(
            "/login/auth/set-dob/", {"date_of_birth": "2999-01-01"}, format="json", **self.auth
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["error_codes"]["date_of_birth"], ["dob_future"])
