"""P-B2 -- WebSocket auth is ticket-only; `?token=<JWT>` is closed by default. Written by reading the code only - NOT run.

    python manage.py test login.tests_pb2_ws_ticket_only
    python -m unittest login.tests_pb2_ws_ticket_only.CheckerRuleTests      # pure rules, needs no DB

Covers the four P-B2 points:
  1. (Flutter side: test/services/ws_ticket_only_test.dart)
  2. backend default false, env can turn it back on          -> DefaultTests, MiddlewareTests
  3. release_check / check_security_settings hard FAIL on true -> CheckerRuleTests
  4. ?token= / ?ticket= / JWT never printed in server logs    -> LogRedactionTests, SentryScrubTests
"""
import logging
import unittest

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from core.release_check import ERROR, check_env
from core.security_settings_check import CSRF_MW, SECURITY_MW, check_settings
from LearnScroll import log_filters, ws_auth
from login import ws_ticket
from login.models import User
from login.token_issuance import issue_tokens_for_user
from login.tests_x6_security import _connect  # builds the ASGI scope + runs JWTAuthMiddleware

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoxfQ.abcdefghijk"


# ---------------------------------------------------------------------------------------------------------------------
# 2. default is closed
# ---------------------------------------------------------------------------------------------------------------------
class DefaultTests(SimpleTestCase):
    def test_setting_is_false_unless_the_environment_turns_it_on(self):
        # CI / `make test-backend` never export WS_ALLOW_QUERY_TOKEN. If this fails, a shell / .env has it set.
        self.assertIs(settings.WS_ALLOW_QUERY_TOKEN, False)

    def test_middleware_fails_closed_when_the_setting_is_missing(self):
        with self.settings():
            del settings.WS_ALLOW_QUERY_TOKEN
            self.assertFalse(ws_auth._query_token_allowed())

    @override_settings(WS_ALLOW_QUERY_TOKEN=True)
    def test_env_switch_still_reopens_it_for_old_builds(self):
        self.assertTrue(ws_auth._query_token_allowed())


class MiddlewareTests(TransactionTestCase):
    """TransactionTestCase: the middleware reads the DB from a worker thread (same reason as login.tests_x6_security)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="pb2ws", email="pb2ws@example.com", password="Sup3r!Passw0rd")
        self.access = issue_tokens_for_user(self.user)["access"]

    def test_query_token_is_refused_with_the_default_settings(self):
        self.assertIsInstance(_connect(f"token={self.access}"), AnonymousUser)

    def test_ticket_works_with_the_default_settings(self):
        ticket, _ = ws_ticket.issue(self.user.pk, None)
        self.assertEqual(_connect(f"ticket={ticket}").pk, self.user.pk)

    def test_token_next_to_no_ticket_does_not_authenticate_either_param_order(self):
        self.assertFalse(_connect(f"x=1&token={self.access}").is_authenticated)


# ---------------------------------------------------------------------------------------------------------------------
# 3. the gates
# ---------------------------------------------------------------------------------------------------------------------
GOOD_SETTINGS = {
    "DEBUG": False,
    "ALLOWED_HOSTS": ["api.learnscroll.app"],
    "CSRF_TRUSTED_ORIGINS": ["https://api.learnscroll.app"],
    "CSRF_COOKIE_SECURE": True,
    "SESSION_COOKIE_SECURE": True,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "CSRF_COOKIE_SAMESITE": "Lax",
    "SECURE_SSL_REDIRECT": True,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_HSTS_SECONDS": 60 * 60 * 24 * 30,
    "SECURE_CONTENT_TYPE_NOSNIFF": True,
    "SECURE_REFERRER_POLICY": "same-origin",
    "X_FRAME_OPTIONS": "DENY",
    "CORS_ALLOW_ALL_ORIGINS": False,
    "CORS_ALLOWED_ORIGINS": [],
    "MIDDLEWARE": [
        "corsheaders.middleware.CorsMiddleware",
        SECURITY_MW,
        CSRF_MW,
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    "REST_FRAMEWORK": {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]},
}


def _keys(findings, level=None):
    return {f.key for f in findings if level is None or f.level == level}


class CheckerRuleTests(unittest.TestCase):
    def test_live_settings_with_query_token_on_is_an_error_in_production(self):
        cfg = {**GOOD_SETTINGS, "WS_ALLOW_QUERY_TOKEN": True}
        self.assertIn("WS_ALLOW_QUERY_TOKEN", _keys(check_settings(cfg), ERROR))

    def test_live_settings_with_query_token_off_is_clean(self):
        cfg = {**GOOD_SETTINGS, "WS_ALLOW_QUERY_TOKEN": False}
        self.assertNotIn("WS_ALLOW_QUERY_TOKEN", _keys(check_settings(cfg)))

    def test_snapshot_without_the_key_is_not_judged(self):
        self.assertNotIn("WS_ALLOW_QUERY_TOKEN", _keys(check_settings(dict(GOOD_SETTINGS))))

    def test_env_file_with_query_token_on_is_an_error(self):
        from core.tests_release_check import GOOD as GOOD_ENV
        for val in ("true", "1", "yes", "on", "TRUE"):
            with self.subTest(val=val):
                env = {**GOOD_ENV, "WS_ALLOW_QUERY_TOKEN": val}
                self.assertIn("WS_ALLOW_QUERY_TOKEN", _keys(check_env(env, file_exists=lambda p: True), ERROR))

    def test_env_file_with_false_or_missing_is_fine(self):
        from core.tests_release_check import GOOD as GOOD_ENV
        for env in ({**GOOD_ENV, "WS_ALLOW_QUERY_TOKEN": "false"}, dict(GOOD_ENV)):
            self.assertNotIn("WS_ALLOW_QUERY_TOKEN", _keys(check_env(env, file_exists=lambda p: True)))


# ---------------------------------------------------------------------------------------------------------------------
# 4. logs
# ---------------------------------------------------------------------------------------------------------------------
class LogRedactionTests(SimpleTestCase):
    def _line(self, logger_name, msg, *args):
        rec = logging.LogRecord(logger_name, logging.INFO, __file__, 1, msg, args, None)
        self.assertTrue(log_filters.RedactSecretsFilter().filter(rec))
        return rec.getMessage()

    def test_server_style_lines_are_redacted(self):
        # uvicorn.access passes the path as an argument, daphne formats it into the message
        out = self._line("uvicorn.access", '%s - "%s %s HTTP/%s" %d', "1.2.3.4:5", "GET", f"/ws/chat/9/?token={JWT}", "1.1", 101)
        self.assertNotIn("eyJ", out)
        self.assertIn("/ws/chat/9/?token=[redacted]", out)
        self.assertIn("?ticket=[redacted]", self._line("daphne.server", "WebSocket CONNECT /ws/inbox/?ticket=AbC123_-xyz"))

    def test_filter_is_installed_on_the_server_loggers_once(self):
        names = ("pb2.test.uvicorn.access", "pb2.test.daphne.ws_protocol")
        try:
            self.assertEqual(log_filters.install_on_server_loggers(names), 2)
            self.assertEqual(log_filters.install_on_server_loggers(names), 0)  # idempotent
            for n in names:
                self.assertTrue(any(isinstance(f, log_filters.RedactSecretsFilter) for f in logging.getLogger(n).filters))
        finally:
            for n in names:
                logging.getLogger(n).filters.clear()

    def test_server_logger_list_covers_the_common_asgi_servers(self):
        for n in ("uvicorn.access", "daphne.ws_protocol", "hypercorn.access", "gunicorn.access"):
            self.assertIn(n, log_filters.SERVER_LOGGERS)

    def test_asgi_installs_the_filter(self):
        import pathlib
        src = (pathlib.Path(settings.BASE_DIR) / "LearnScroll" / "asgi.py").read_text(encoding="utf-8")
        self.assertIn("install_on_server_loggers()", src)

    def test_nginx_snippet_exists_and_redacts(self):
        import pathlib
        p = pathlib.Path(settings.BASE_DIR) / "deploy" / "nginx" / "ws-log-redaction.conf.example"
        text = p.read_text(encoding="utf-8")
        self.assertIn("ticket", text)
        self.assertIn("[redacted]", text)
        self.assertIn("log_format learnscroll", text)


class SentryScrubTests(SimpleTestCase):
    def test_request_url_and_query_string_are_scrubbed(self):
        event = {
            "request": {"url": f"https://api.x/ws/chat/1/?token={JWT}&x=1", "query_string": f"token={JWT}&x=1"},
            "message": "handshake failed for /ws/inbox/?ticket=abc123",
            "breadcrumbs": {"values": [{"data": {"url": "wss://api.x/ws/inbox/?ticket=zzz999"}}]},
        }
        out = log_filters.scrub_sentry_event(event)
        flat = repr(out)
        self.assertNotIn("eyJ", flat)
        self.assertNotIn("abc123", flat)
        self.assertNotIn("zzz999", flat)
        self.assertEqual(out["request"]["query_string"], "token=[redacted]&x=1")

    def test_pair_and_dict_query_strings_are_scrubbed(self):
        for qs in ([["token", JWT], ["x", "1"]], {"token": JWT, "x": "1"}):
            with self.subTest(kind=type(qs).__name__):
                out = log_filters.scrub_sentry_event({"request": {"query_string": qs}})
                self.assertNotIn("eyJ", repr(out))
                self.assertIn("1", repr(out))  # unrelated params survive

    def test_scrubber_never_raises_and_always_returns_the_event(self):
        weird = {"request": {"query_string": object()}}
        self.assertIs(log_filters.scrub_sentry_event(weird), weird)

    def test_settings_wire_the_hooks(self):
        # the init block only runs with SENTRY_DSN, so check the source wires them
        import pathlib
        src = (pathlib.Path(settings.BASE_DIR) / "LearnScroll" / "settings.py").read_text(encoding="utf-8")
        self.assertIn("before_send=_sentry_before_send", src)
        self.assertIn("before_breadcrumb=_sentry_before_breadcrumb", src)


if __name__ == "__main__":
    unittest.main()
