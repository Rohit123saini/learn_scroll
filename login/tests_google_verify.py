"""
T1 - Google ID-token verification helper + the two endpoints that use it (written by reading the code; NOT run yet).
Run: python manage.py test login.tests_google_verify

Google is never called for real: `google.oauth2.id_token.verify_oauth2_token` / `requests.Session.request` are patched.
"""
from types import SimpleNamespace
from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from google.auth import exceptions as google_exceptions
from rest_framework.test import APIClient

from . import connected_accounts as ca
from . import google_verify as gv
from .models import User

GOOD = {"sub": "1234567890", "email": "g.user@example.com", "email_verified": True,
        "given_name": "G", "family_name": "User", "aud": "web-id"}


class ClientIdsTests(SimpleTestCase):
    @override_settings(GOOGLE_CLIENT_IDS=["web-id", "android-id", "ios-id"], GOOGLE_CLIENT_ID="web-id")
    def test_merge_and_dedupe(self):
        self.assertEqual(gv.client_ids(), ["web-id", "android-id", "ios-id"])

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_CLIENT_ID="only-id")
    def test_old_single_id_is_the_fallback(self):
        self.assertEqual(gv.client_ids(), ["only-id"])
        self.assertTrue(gv.is_configured())

    @override_settings(GOOGLE_CLIENT_IDS="a, b ,,c", GOOGLE_CLIENT_ID="")
    def test_comma_string_is_accepted(self):
        self.assertEqual(gv.client_ids(), ["a", "b", "c"])

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_CLIENT_ID="")
    def test_nothing_configured(self):
        self.assertFalse(gv.is_configured())
        with self.assertRaises(gv.GoogleVerifyError) as cm:
            gv.verify_google_token("tok")
        self.assertEqual((cm.exception.code, cm.exception.status), ("google_not_configured", 500))

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_CLIENT_ID="")
    def test_startup_warning_when_empty(self):
        with self.assertLogs("login.google_verify", level="WARNING"):
            gv.check_configuration()


@override_settings(GOOGLE_CLIENT_IDS=["web-id", "android-id"], GOOGLE_CLIENT_ID="web-id")
class VerifyMappingTests(SimpleTestCase):
    def _patch(self, **kw):
        return mock.patch("login.google_verify.google_id_token.verify_oauth2_token", **kw)

    def test_ok_passes_all_audiences_and_clock_skew(self):
        with self._patch(return_value=GOOD) as m:
            self.assertEqual(gv.verify_google_token("  tok  ")["sub"], "1234567890")
        args, kwargs = m.call_args
        self.assertEqual(args[0], "tok")
        self.assertEqual(args[2], ["web-id", "android-id"])
        self.assertEqual(kwargs["clock_skew_in_seconds"], 10)

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_CLIENT_ID="web-id")
    def test_single_audience_is_passed_as_a_string(self):
        with self._patch(return_value=GOOD) as m:
            gv.verify_google_token("tok")
        self.assertEqual(m.call_args.args[2], "web-id")

    def test_value_error_is_401(self):
        with self._patch(side_effect=ValueError("Token expired")):
            with self.assertRaises(gv.GoogleVerifyError) as cm:
                gv.verify_google_token("tok")
        self.assertEqual((cm.exception.code, cm.exception.status), ("google_token_invalid", 401))

    def test_empty_token_is_401(self):
        for bad in (None, "", "   ", 5):
            with self.assertRaises(gv.GoogleVerifyError) as cm:
                gv.verify_google_token(bad)
            self.assertEqual(cm.exception.code, "google_token_invalid")

    def test_network_failures_are_503(self):
        for exc in (google_exceptions.TransportError("down"), requests.Timeout("slow"),
                    requests.ConnectionError("dns"), TimeoutError("t")):
            with self._patch(side_effect=exc):
                with self.assertRaises(gv.GoogleVerifyError) as cm:
                    gv.verify_google_token("tok")
            self.assertEqual((cm.exception.code, cm.exception.status), ("google_unreachable", 503), repr(exc))

    def test_unknown_key_id_clears_the_cache_and_retries_once(self):
        calls = {"n": 0}

        def fn(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("Certificate for key id abc not found.")
            return GOOD

        with self._patch(side_effect=fn), mock.patch.object(gv, "clear_certs_cache") as clear:
            self.assertEqual(gv.verify_google_token("tok")["email"], "g.user@example.com")
        self.assertEqual(calls["n"], 2)
        clear.assert_called_once()


class TimeoutAndCacheTests(SimpleTestCase):
    CERTS = "https://www.googleapis.com/oauth2/v1/certs"

    def setUp(self):
        gv.clear_certs_cache()

    def tearDown(self):
        gv.clear_certs_cache()

    def _resp(self):
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"}, content=b"{}")

    @override_settings(GOOGLE_VERIFY_TIMEOUT=5)
    def test_every_call_has_a_short_timeout(self):
        with mock.patch("requests.Session.request", return_value=self._resp()) as req:
            gv._TimeoutRequest()(self.CERTS)
            gv._TimeoutRequest()("https://example.com/x", timeout=120)
        for call in req.call_args_list:
            self.assertLessEqual(call.kwargs["timeout"], 5)

    @override_settings(GOOGLE_CERTS_CACHE_SECONDS=3600)
    def test_certs_are_fetched_once_per_ttl(self):
        with mock.patch("requests.Session.request", return_value=self._resp()) as req:
            first = gv._TimeoutRequest()(self.CERTS)
            second = gv._TimeoutRequest()(self.CERTS)
        self.assertEqual(req.call_count, 1)
        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(second.data, b"{}")

    @override_settings(GOOGLE_CERTS_CACHE_SECONDS=0)
    def test_cache_can_be_turned_off(self):
        with mock.patch("requests.Session.request", return_value=self._resp()) as req:
            gv._TimeoutRequest()(self.CERTS)
            gv._TimeoutRequest()(self.CERTS)
        self.assertEqual(req.call_count, 2)

    def test_a_failed_fetch_is_not_cached(self):
        bad = SimpleNamespace(status_code=500, headers={}, content=b"x")
        with mock.patch("requests.Session.request", return_value=bad) as req:
            gv._TimeoutRequest()(self.CERTS)
            gv._TimeoutRequest()(self.CERTS)
        self.assertEqual(req.call_count, 2)


@override_settings(GOOGLE_CLIENT_IDS=["web-id"], GOOGLE_CLIENT_ID="web-id")
class LoginEndpointTests(TestCase):
    URL = "/login/auth/google/"

    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def _post(self, **patch):
        with mock.patch("login.google_verify.google_id_token.verify_oauth2_token", **patch):
            return self.client.post(self.URL, {"id_token": "tok"}, format="json")

    def test_timeout_is_a_clean_503_not_a_hang_or_500(self):
        res = self._post(side_effect=requests.Timeout("slow"))
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.data["code"], "google_unreachable")
        self.assertNotIn("Traceback", str(res.data))

    def test_bad_token_is_401(self):
        res = self._post(side_effect=ValueError("bad"))
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data["code"], "google_token_invalid")

    @override_settings(GOOGLE_CLIENT_IDS=[], GOOGLE_CLIENT_ID="")
    def test_not_configured_is_500_with_code(self):
        res = self._post(return_value=GOOD)
        self.assertEqual(res.status_code, 500)
        self.assertEqual(res.data["code"], "google_not_configured")

    def test_new_account_is_created_then_the_same_account_logs_in_again(self):
        first = self._post(return_value=GOOD)
        self.assertIn(first.status_code, (200, 201), first.data)
        self.assertEqual(User.objects.filter(email="g.user@example.com").count(), 1)
        second = self._post(return_value=GOOD)
        self.assertEqual(second.status_code, 200, second.data)
        self.assertEqual(User.objects.filter(email="g.user@example.com").count(), 1)

    def test_android_audience_token_is_accepted(self):
        with override_settings(GOOGLE_CLIENT_IDS=["web-id", "android-id"]):
            with mock.patch("login.google_verify.google_id_token.verify_oauth2_token", return_value=GOOD) as m:
                self.client.post(self.URL, {"id_token": "tok"}, format="json")
        self.assertEqual(m.call_args.args[2], ["web-id", "android-id"])


@override_settings(GOOGLE_CLIENT_IDS=["web-id"], GOOGLE_CLIENT_ID="web-id")
class LinkHelperTests(SimpleTestCase):
    def test_link_path_uses_the_same_helper(self):
        with mock.patch("login.google_verify.google_id_token.verify_oauth2_token", return_value=GOOD):
            profile = ca.verify_google_id_token("tok")
        self.assertEqual(profile["sub"], "1234567890")
        self.assertEqual(profile["email"], "g.user@example.com")

    def test_link_path_503_on_timeout(self):
        with mock.patch("login.google_verify.google_id_token.verify_oauth2_token", side_effect=requests.Timeout()):
            with self.assertRaises(ca.ConnectionError_) as cm:
                ca.verify_google_id_token("tok")
        self.assertEqual((cm.exception.code, cm.exception.status), ("google_unreachable", 503))

    def test_link_path_401_on_bad_token(self):
        with mock.patch("login.google_verify.google_id_token.verify_oauth2_token", side_effect=ValueError()):
            with self.assertRaises(ca.ConnectionError_) as cm:
                ca.verify_google_id_token("tok")
        self.assertEqual((cm.exception.code, cm.exception.status), ("google_token_invalid", 401))
