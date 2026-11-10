# login/tests_disposable_captcha.py
"""
L1 gap 18 -- disposable e-mail / virtual phone block + captcha.

  * login/disposable.py  : domain list (+ subdomains, extra, file, allow-list),
                           fake / virtual phone numbers, optional carrier lookup
  * login/captcha.py     : built-in image captcha, adaptive + always modes, remote
                           providers, login captcha after wrong passwords

Like the other login tests everything goes through the real URLs, and every test
starts from ``cache.clear()`` (the abuse counters live in the cache). The global
test settings run with CAPTCHA_MODE="off", so each captcha test switches it on.
"""
import os
import tempfile
from unittest import mock

import requests
from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from . import disposable
from .models import User

PW = "Str0ng!Passw0rd"
ANSWER = "AAAAA"  # with _ALPHABET patched to "A" every challenge text is this


def _solvable():
    """Challenges whose answer is known (ANSWER) and that skip the image drawing."""
    return (
        mock.patch("login.captcha._ALPHABET", "A"),
        mock.patch("login.captcha._render_png", return_value=b"png"),
    )


class _Solvable:
    def __enter__(self):
        self._patches = _solvable()
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()


def _send(client, target, **extra):
    return client.post("/login/auth/send-otp/", {"email_or_phone": target, **extra}, format="json")


# ---------------------------------------------------------------------------
# disposable.py (pure functions)
# ---------------------------------------------------------------------------
class DisposableEmailUnitTests(SimpleTestCase):
    def test_known_disposable_domains_are_caught(self):
        for email in ("a@mailinator.com", "A@Mailinator.COM", "x@guerrillamail.com", "y@yopmail.com"):
            self.assertTrue(disposable.is_disposable_email(email), email)

    def test_subdomains_of_a_disposable_domain_are_caught(self):
        self.assertTrue(disposable.is_disposable_email("a@inbox.mailinator.com"))

    def test_normal_providers_are_not_blocked(self):
        for email in ("a@gmail.com", "b@outlook.com", "c@example.com", "d@x.com", "e@school.edu.in", "f@mailinator.com.au"):
            self.assertFalse(disposable.is_disposable_email(email), email)

    def test_blank_or_malformed_is_not_judged(self):
        self.assertFalse(disposable.is_disposable_email(""))
        self.assertFalse(disposable.is_disposable_email("no-at-sign"))

    @override_settings(DISPOSABLE_EMAIL_EXTRA_DOMAINS=["burner.test"])
    def test_extra_domains_from_settings(self):
        self.assertTrue(disposable.is_disposable_email("a@burner.test"))
        self.assertTrue(disposable.is_disposable_email("a@sub.burner.test"))

    @override_settings(DISPOSABLE_EMAIL_ALLOWED_DOMAINS=["mailinator.com"])
    def test_allow_list_wins(self):
        self.assertFalse(disposable.is_disposable_email("a@mailinator.com"))

    @override_settings(DISPOSABLE_BLOCK_ENABLED=False)
    def test_master_switch(self):
        self.assertFalse(disposable.is_disposable_email("a@mailinator.com"))
        self.assertIsNone(disposable.phone_problem("+919999999999"))

    def test_domain_list_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write("# comment\nfromfile.test\n\n@Other.TEST  # trailing\n")
            path = handle.name
        try:
            with override_settings(DISPOSABLE_EMAIL_LIST_FILE=path):
                self.assertTrue(disposable.is_disposable_email("a@fromfile.test"))
                self.assertTrue(disposable.is_disposable_email("a@other.test"))
                self.assertFalse(disposable.is_disposable_email("a@gmail.com"))
        finally:
            os.unlink(path)

    def test_missing_list_file_is_ignored(self):
        with override_settings(DISPOSABLE_EMAIL_LIST_FILE="/nonexistent/list.txt"):
            self.assertFalse(disposable.is_disposable_email("a@gmail.com"))


class PhoneUnitTests(SimpleTestCase):
    def test_real_looking_numbers_pass(self):
        for number in ("+919876543210", "919876543210", "+14155552671", "+447911123456"):
            self.assertIsNone(disposable.phone_problem(number), number)

    def test_repeating_numbers_are_fake(self):
        for number in ("+919999999999", "+917777777777", "+919812121212"):
            problem = disposable.phone_problem(number)
            self.assertIsNotNone(problem, number)
            self.assertEqual(problem[0], "phone_invalid_number")

    def test_indian_number_outside_mobile_range_is_invalid(self):
        for number in ("+915876543210", "+911234567890", "915876543210"):
            problem = disposable.phone_problem(number)
            self.assertIsNotNone(problem, number)
            self.assertEqual(problem[0], "phone_invalid_number")

    @override_settings(BLOCKED_PHONE_PREFIXES=["4470", "+1500"])
    def test_blocked_prefixes(self):
        for number in ("+447012345678", "+15005550006"):
            problem = disposable.phone_problem(number)
            self.assertIsNotNone(problem, number)
            self.assertEqual(problem[0], "disposable_phone")
        self.assertIsNone(disposable.phone_problem("+447911123456"))


@override_settings(
    PHONE_LOOKUP_PROVIDER="twilio",
    TWILIO_ACCOUNT_SID="AC123",
    TWILIO_AUTH_TOKEN="tok",
    PHONE_BLOCKED_LINE_TYPES="nonFixedVoip,voip",
)
class PhoneLookupTests(TestCase):
    def setUp(self):
        cache.clear()

    @staticmethod
    def _answer(line_type, status=200):
        response = mock.Mock(status_code=status)
        response.json.return_value = {"line_type_intelligence": {"type": line_type}}
        return response

    def test_voip_number_is_refused(self):
        with mock.patch("login.disposable.requests.get", return_value=self._answer("nonFixedVoip")):
            problem = disposable.phone_lookup_problem("+14155552671")
        self.assertEqual(problem[0], "disposable_phone")

    def test_mobile_number_passes(self):
        with mock.patch("login.disposable.requests.get", return_value=self._answer("mobile")):
            self.assertIsNone(disposable.phone_lookup_problem("+14155552671"))

    def test_result_is_cached(self):
        with mock.patch("login.disposable.requests.get", return_value=self._answer("mobile")) as get:
            disposable.phone_lookup_problem("+14155552671")
            disposable.phone_lookup_problem("+14155552671")
        self.assertEqual(get.call_count, 1)

    def test_provider_outage_fails_open(self):
        with mock.patch("login.disposable.requests.get", side_effect=requests.ConnectionError("down")):
            self.assertIsNone(disposable.phone_lookup_problem("+14155552671"))
        with mock.patch("login.disposable.requests.get", return_value=self._answer("", status=500)):
            self.assertIsNone(disposable.phone_lookup_problem("+14155552672"))

    @override_settings(PHONE_LOOKUP_PROVIDER="")
    def test_off_by_default(self):
        with mock.patch("login.disposable.requests.get") as get:
            self.assertIsNone(disposable.phone_lookup_problem("+14155552671"))
        get.assert_not_called()

    def test_send_otp_refuses_a_voip_number_before_any_sms(self):
        with mock.patch("login.disposable.requests.get", return_value=self._answer("voip")), mock.patch(
            "login.otp_service.send_otp_sms"
        ) as sms:
            res = _send(APIClient(), "+14155552671")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "disposable_phone")
        sms.assert_not_called()


# ---------------------------------------------------------------------------
# send-otp / signup / google through the real URLs
# ---------------------------------------------------------------------------
class DisposableEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_send_otp_refuses_a_disposable_email_and_sends_nothing(self):
        res = _send(self.client, "bot@mailinator.com")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "disposable_email")
        self.assertEqual(res.data["message"], res.data["detail"])
        self.assertEqual(len(mail.outbox), 0)

    def test_refusal_does_not_use_up_the_resend_allowance(self):
        _send(self.client, "bot@mailinator.com")
        self.assertEqual(_send(self.client, "real@example.com").status_code, 200)

    def test_send_otp_still_works_for_a_normal_email(self):
        res = _send(self.client, "real@example.com")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_an_existing_account_on_such_a_domain_can_still_use_otp_login(self):
        User.objects.create_user(username="old", email="old@mailinator.com", password=PW)
        res = _send(self.client, "old@mailinator.com")
        self.assertEqual(res.status_code, 200, res.data)

    def test_send_otp_refuses_fake_phone_numbers(self):
        with mock.patch("login.otp_service.send_otp_sms") as sms:
            for number in ("+919999999999", "+915876543210"):
                cache.clear()
                res = _send(self.client, number)
                self.assertEqual(res.status_code, 400, number)
                self.assertEqual(res.data["code"], "phone_invalid_number")
        sms.assert_not_called()

    def test_send_otp_accepts_a_real_looking_phone(self):
        with mock.patch("login.otp_service.send_otp_sms") as sms:
            res = _send(self.client, "+919876543210")
        self.assertEqual(res.status_code, 200, res.data)
        sms.assert_called_once()

    def test_forgot_password_is_not_blocked_for_such_a_domain(self):
        # No disposable check here: it would reveal nothing useful and could lock a
        # legacy account owner out of recovering it.
        User.objects.create_user(username="old", email="old@mailinator.com", password=PW)
        res = self.client.post("/login/auth/forgot-password/", {"email_or_phone": "old@mailinator.com"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def _signup_payload(self, **over):
        body = {
            "username": "newuser",
            "first_name": "New",
            "last_name": "User",
            "phone": "+919876543210",
            "email": "new@example.com",
            "date_of_birth": "2000-01-01",
            "password": PW,
            "confirm_password": PW,
        }
        body.update(over)
        return body

    def test_signup_names_the_disposable_email_rule(self):
        res = self.client.post("/login/signup/", self._signup_payload(email="x@mailinator.com"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "validation_error")
        self.assertEqual(res.data["error_codes"]["email"], ["email_disposable"])

    def test_signup_names_the_fake_phone_rule(self):
        res = self.client.post("/login/signup/", self._signup_payload(phone="+919999999999"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error_codes"]["phone"], ["phone_invalid_number"])

    @override_settings(BLOCKED_PHONE_PREFIXES=["4470"])
    def test_signup_names_the_virtual_phone_rule(self):
        res = self.client.post("/login/signup/", self._signup_payload(phone="+447012345678"), format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["error_codes"]["phone"], ["phone_disposable"])

    def test_google_does_not_create_an_account_on_a_disposable_domain(self):
        idinfo = {"email": "g@mailinator.com", "email_verified": True, "given_name": "G", "family_name": "H"}
        with override_settings(GOOGLE_CLIENT_ID="cid"), mock.patch(
            "login.views.google_id_token.verify_oauth2_token", return_value=idinfo
        ):
            res = self.client.post("/login/auth/google/", {"id_token": "t"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "disposable_email")
        self.assertFalse(User.objects.filter(email="g@mailinator.com").exists())


# ---------------------------------------------------------------------------
# captcha
# ---------------------------------------------------------------------------
@override_settings(CAPTCHA_MODE="always", CAPTCHA_PROVIDER="builtin")
class BuiltinCaptchaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _challenge(self, target="a@example.com"):
        with _Solvable():
            res = _send(self.client, target)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "captcha_required")
        return res.data["captcha"]

    def test_required_response_carries_a_challenge(self):
        body = self._challenge()
        self.assertEqual(body["provider"], "builtin")
        self.assertTrue(body["image"].startswith("data:image/png;base64,"))
        self.assertTrue(body["captcha_id"])
        self.assertEqual(len(mail.outbox), 0)  # nothing was sent yet

    def test_the_answer_is_never_in_the_response_or_stored_in_clear(self):
        body = self._challenge()
        self.assertNotIn(ANSWER, str(body))
        self.assertNotEqual(cache.get(f"captcha:ch:{body['captcha_id']}"), ANSWER)

    def test_solving_it_lets_the_request_through(self):
        body = self._challenge()
        res = _send(self.client, "a@example.com", captcha_id=body["captcha_id"], captcha_answer=ANSWER.lower())
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_wrong_answer_is_refused_and_the_challenge_is_spent(self):
        body = self._challenge()
        with _Solvable():
            bad = _send(self.client, "a@example.com", captcha_id=body["captcha_id"], captcha_answer="ZZZZZ")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(bad.data["code"], "captcha_invalid")
        # the invalid answer comes with a brand-new challenge
        self.assertNotEqual(bad.data["captcha"]["captcha_id"], body["captcha_id"])
        # the first one can't be retried, even with the right text
        with _Solvable():
            again = _send(self.client, "a@example.com", captcha_id=body["captcha_id"], captcha_answer=ANSWER)
        self.assertEqual(again.data["code"], "captcha_invalid")

    def test_a_solved_challenge_cannot_be_reused(self):
        body = self._challenge()
        ok = _send(self.client, "a@example.com", captcha_id=body["captcha_id"], captcha_answer=ANSWER)
        self.assertEqual(ok.status_code, 200, ok.data)
        with _Solvable():
            again = _send(self.client, "b@example.com", captcha_id=body["captcha_id"], captcha_answer=ANSWER)
        self.assertEqual(again.data["code"], "captcha_invalid")

    def test_an_unknown_challenge_id_is_refused(self):
        with _Solvable():
            res = _send(self.client, "a@example.com", captcha_id="nope", captcha_answer=ANSWER)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "captcha_invalid")

    def test_challenge_endpoint(self):
        with _Solvable():
            res = self.client.get("/login/auth/captcha/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["mode"], "always")
        self.assertEqual(res.data["provider"], "builtin")
        self.assertTrue(res.data["image"].startswith("data:image/png;base64,"))

    @override_settings(CAPTCHA_MODE="off")
    def test_challenge_endpoint_when_off(self):
        res = self.client.get("/login/auth/captcha/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["mode"], "off")
        self.assertNotIn("image", res.data)

    def test_the_real_image_renderer_produces_a_png(self):
        from .captcha import new_builtin_challenge

        body = new_builtin_challenge()
        import base64

        raw = base64.b64decode(body["image"].split(",", 1)[1])
        self.assertTrue(raw.startswith(b"\x89PNG"))

    def test_forgot_password_asks_the_same_for_known_and_unknown_accounts(self):
        User.objects.create_user(username="real", email="real@example.com", password=PW)
        with _Solvable():
            a = self.client.post("/login/auth/forgot-password/", {"email_or_phone": "real@example.com"}, format="json")
            b = self.client.post("/login/auth/forgot-password/", {"email_or_phone": "ghost@example.com"}, format="json")
        self.assertEqual((a.status_code, a.data["code"]), (b.status_code, b.data["code"]))
        self.assertEqual(a.data["code"], "captcha_required")

    def test_forgot_password_goes_through_once_solved(self):
        User.objects.create_user(username="real", email="real@example.com", password=PW)
        with _Solvable():
            first = self.client.post("/login/auth/forgot-password/", {"email_or_phone": "real@example.com"}, format="json")
        cap = first.data["captcha"]
        ok = self.client.post(
            "/login/auth/forgot-password/",
            {"email_or_phone": "real@example.com", "captcha_id": cap["captcha_id"], "captcha_answer": ANSWER},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(len(mail.outbox), 1)


@override_settings(CAPTCHA_MODE="adaptive", CAPTCHA_PROVIDER="builtin", CAPTCHA_SEND_OTP_FREE_REQUESTS=2)
class AdaptiveCaptchaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def test_normal_use_never_sees_a_captcha(self):
        # a signup (send) plus a resend is two calls from one IP
        self.assertEqual(_send(self.client, "a@example.com").status_code, 200)
        self.assertEqual(_send(self.client, "b@example.com").status_code, 200)

    def test_bot_like_volume_is_asked(self):
        _send(self.client, "a@example.com")
        _send(self.client, "b@example.com")
        with _Solvable():
            third = _send(self.client, "c@example.com")
        self.assertEqual(third.status_code, 400)
        self.assertEqual(third.data["code"], "captcha_required")

    @override_settings(CAPTCHA_MODE="off")
    def test_off_never_asks(self):
        for name in "abcd":
            self.assertEqual(_send(self.client, f"{name}@example.com").status_code, 200)

    def test_login_asks_after_repeated_wrong_passwords_and_not_before(self):
        User.objects.create_user(username="rita", email="rita@example.com", password=PW)

        def login(password, **extra):
            return self.client.post("/login/", {"username": "rita", "password": password, **extra}, format="json")

        with override_settings(CAPTCHA_LOGIN_AFTER_FAILURES=3):
            for _ in range(3):
                self.assertEqual(login("wrong").status_code, 401)
            with _Solvable():
                gated = login(PW)  # even the right password is held until the captcha is solved
            self.assertEqual(gated.status_code, 400)
            self.assertEqual(gated.data["code"], "captcha_required")

            cap = gated.data["captcha"]
            ok = login(PW, captcha_id=cap["captcha_id"], captcha_answer=ANSWER)
            self.assertEqual(ok.status_code, 200, ok.data)

    @staticmethod
    def _failures(identifier):
        from rest_framework.request import Request
        from rest_framework.test import APIRequestFactory

        from .login_guard import LoginGuard

        return LoginGuard(Request(APIRequestFactory().post("/login/")), identifier).failure_count()

    def test_a_missing_captcha_does_not_count_as_a_wrong_password(self):
        User.objects.create_user(username="rita", email="rita@example.com", password=PW)
        with override_settings(CAPTCHA_LOGIN_AFTER_FAILURES=1):
            first = self.client.post("/login/", {"username": "rita", "password": "wrong"}, format="json")
            self.assertEqual(first.status_code, 401)
            before = self._failures("rita")
            self.assertEqual(before, 1)
            with _Solvable():
                for _ in range(4):
                    res = self.client.post("/login/", {"username": "rita", "password": "wrong"}, format="json")
                    self.assertEqual(res.data["code"], "captcha_required")
            self.assertEqual(self._failures("rita"), before)


@override_settings(CAPTCHA_MODE="always", CAPTCHA_PROVIDER="turnstile", CAPTCHA_SECRET_KEY="secret", CAPTCHA_SITE_KEY="site")
class RemoteCaptchaTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    @staticmethod
    def _verdict(success):
        response = mock.Mock()
        response.json.return_value = {"success": success}
        return response

    def test_required_names_the_provider_and_site_key(self):
        res = _send(self.client, "a@example.com")
        self.assertEqual(res.data["code"], "captcha_required")
        self.assertEqual(res.data["captcha_provider"], "turnstile")
        self.assertEqual(res.data["captcha"], {"provider": "turnstile", "site_key": "site"})

    def test_valid_token_passes(self):
        with mock.patch("login.captcha.requests.post", return_value=self._verdict(True)) as post:
            res = _send(self.client, "a@example.com", captcha_token="tok")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(post.call_args.kwargs["data"]["secret"], "secret")
        self.assertEqual(post.call_args.kwargs["data"]["response"], "tok")

    def test_rejected_token_fails(self):
        with mock.patch("login.captcha.requests.post", return_value=self._verdict(False)):
            res = _send(self.client, "a@example.com", captcha_token="bad")
        self.assertEqual(res.data["code"], "captcha_invalid")

    def test_provider_outage_fails_open_by_default(self):
        with mock.patch("login.captcha.requests.post", side_effect=requests.ConnectionError("down")):
            res = _send(self.client, "a@example.com", captcha_token="tok")
        self.assertEqual(res.status_code, 200, res.data)

    @override_settings(CAPTCHA_FAIL_OPEN=False)
    def test_provider_outage_can_fail_closed(self):
        with mock.patch("login.captcha.requests.post", side_effect=requests.ConnectionError("down")):
            res = _send(self.client, "a@example.com", captcha_token="tok")
        self.assertEqual(res.data["code"], "captcha_invalid")

    @override_settings(CAPTCHA_SECRET_KEY="")
    def test_missing_secret_falls_back_to_the_builtin_captcha(self):
        with _Solvable():
            res = _send(self.client, "a@example.com")
        self.assertEqual(res.data["captcha_provider"], "builtin")
        self.assertEqual(res.data["captcha"]["provider"], "builtin")
