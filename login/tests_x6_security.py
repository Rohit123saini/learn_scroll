# login/tests_x6_security.py
"""
X6 / X6b security tests (written by reading the code -- NOT run; run with `python manage.py test login.tests_x6_security`).

  * ReauthGuard            wrong-current-password lockout (login/sensitive.py)
  * SensitiveWriteThrottle only writes are counted, a refusal leaves an audit row
  * record_change          audit rows hold field NAMES only, never values
  * WebSocket ticket       one-time, short-lived, bound to the session; the JWT is not needed in the URL
  * JWTAuthMiddleware      ?ticket= works once, ?token= can be switched off (WS_ALLOW_QUERY_TOKEN)
"""
from unittest import mock

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from . import audit, sensitive, ws_ticket
from .models import AuthAuditLog, AuthToken, User
from .token_issuance import issue_tokens_for_user

PW = "Sup3r!Passw0rd"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


# ---------------------------------------------------------------------------------------------------------------
class ReauthGuardTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.user = mock.Mock(pk=4242)

    @override_settings(REAUTH_MAX_FAILURES=3, REAUTH_LOCKOUT_SECONDS=600)
    def test_locks_after_max_failures_and_counts_down_attempts(self):
        g = sensitive.ReauthGuard(self.user)
        self.assertEqual(g.retry_after(), 0)
        self.assertEqual(g.record_failure(), (0, 2))
        self.assertEqual(g.record_failure(), (0, 1))
        retry, left = g.record_failure()
        self.assertEqual((retry, left), (600, 0))
        self.assertGreater(g.retry_after(), 0)
        self.assertLessEqual(g.retry_after(), 600)

    @override_settings(REAUTH_MAX_FAILURES=3)
    def test_correct_password_resets_the_counter(self):
        g = sensitive.ReauthGuard(self.user)
        g.record_failure()
        g.record_failure()
        g.clear()
        self.assertEqual(g.record_failure(), (0, 2))

    @override_settings(REAUTH_MAX_FAILURES=2)
    def test_actions_and_users_are_independent(self):
        sensitive.ReauthGuard(self.user, "password_change").record_failure()
        _, left = sensitive.ReauthGuard(self.user, "email_change").record_failure()
        self.assertEqual(left, 1)  # a different action starts from zero
        _, left = sensitive.ReauthGuard(mock.Mock(pk=7), "password_change").record_failure()
        self.assertEqual(left, 1)  # a different user starts from zero

    @override_settings(REAUTH_MAX_FAILURES=1, REAUTH_LOCKOUT_SECONDS=60)
    def test_lock_expires_by_itself(self):
        g = sensitive.ReauthGuard(self.user)
        g.record_failure()
        with mock.patch("login.sensitive.time.time", return_value=10 ** 10):
            self.assertEqual(g.retry_after(), 0)


# ---------------------------------------------------------------------------------------------------------------
class SensitiveWriteThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="thr", email="thr@example.com", password=PW)
        self.factory = RequestFactory()

    def _req(self, method):
        req = getattr(self.factory, method.lower())("/x/")
        req.user = self.user
        return req

    def test_reads_are_never_throttled_or_counted(self):
        t = sensitive.SensitiveWriteThrottle()
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=False) as base:
            self.assertTrue(t.allow_request(self._req("GET"), None))
            base.assert_not_called()

    def test_refused_write_leaves_a_rate_limited_audit_row(self):
        t = sensitive.SensitiveWriteThrottle()
        t.scope = "password_change"
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=False):
            self.assertFalse(t.allow_request(self._req("POST"), None))
        row = AuthAuditLog.objects.filter(event=audit.Event.RATE_LIMITED).first()
        self.assertIsNotNone(row)
        self.assertEqual(row.outcome, audit.BLOCKED)
        self.assertEqual(row.reason, "password_change")
        self.assertEqual(row.user_id, self.user.pk)

    def test_allowed_write_leaves_no_audit_row(self):
        t = sensitive.SensitiveWriteThrottle()
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=True):
            self.assertTrue(t.allow_request(self._req("POST"), None))
        self.assertFalse(AuthAuditLog.objects.filter(event=audit.Event.RATE_LIMITED).exists())

    def test_audit_failure_never_turns_a_429_into_a_500(self):
        t = sensitive.SensitiveWriteThrottle()
        with mock.patch("rest_framework.throttling.ScopedRateThrottle.allow_request", return_value=False), \
                mock.patch("login.sensitive.audit.record", side_effect=RuntimeError("db down")):
            self.assertFalse(t.allow_request(self._req("PATCH"), None))


# ---------------------------------------------------------------------------------------------------------------
class ChangedFieldsTests(SimpleTestCase):
    def test_only_sent_fields_count(self):
        self.assertEqual(sensitive.changed_fields({"email": "a", "x": 1}, ["email", "phone"]), ["email"])

    def test_resent_same_value_is_not_a_change(self):
        before = {"email": "a@x.com", "phone": "1"}
        after = {"email": "a@x.com", "phone": "2"}
        self.assertEqual(sensitive.changed_fields({"email": "a@x.com", "phone": "2"}, ["email", "phone"], before, after), ["phone"])


class RecordChangeTests(TestCase):
    def test_row_holds_field_names_never_values(self):
        user = User.objects.create_user(username="rc", email="rc@example.com", password=PW)
        req = RequestFactory().post("/x/")
        sensitive.record_change(audit.Event.RATE_LIMITED, req, user, ["email", "phone"], reason="profile")
        row = AuthAuditLog.objects.latest("id")
        self.assertEqual(row.metadata.get("fields"), "email,phone")
        self.assertNotIn("rc@example.com", str(row.metadata))

    def test_never_raises(self):
        with mock.patch("login.sensitive.audit.record", side_effect=RuntimeError("boom")):
            self.assertIsNone(sensitive.record_change("x", None, None, ["a"]))


# ---------------------------------------------------------------------------------------------------------------
class WsTicketModuleTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_ticket_is_single_use(self):
        ticket, ttl = ws_ticket.issue(5, "jti-1")
        self.assertGreaterEqual(ttl, 5)
        self.assertEqual(ws_ticket.redeem(ticket), ("5", "jti-1"))
        self.assertIsNone(ws_ticket.redeem(ticket))  # a ticket copied out of a log is already dead

    def test_unknown_empty_and_oversized_tickets_are_refused(self):
        for bad in ("", None, "nope", "x" * 500, 123):
            self.assertIsNone(ws_ticket.redeem(bad))

    def test_only_a_hash_is_used_as_the_cache_key(self):
        ticket, _ = ws_ticket.issue(5)
        self.assertNotIn(ticket, ws_ticket._key(ticket))
        self.assertIsNone(cache.get("wst:" + ticket))

    @override_settings(WS_TICKET_TTL_SECONDS=9999)
    def test_ttl_is_clamped(self):
        self.assertEqual(ws_ticket.ttl_seconds(), 120)

    @override_settings(WS_TICKET_TTL_SECONDS="abc")
    def test_bad_ttl_setting_falls_back_to_30(self):
        self.assertEqual(ws_ticket.ttl_seconds(), 30)

    def test_cache_outage_means_not_authenticated(self):
        ticket, _ = ws_ticket.issue(5)
        with mock.patch("login.ws_ticket.cache.get", side_effect=RuntimeError("redis down")):
            self.assertIsNone(ws_ticket.redeem(ticket))


class WsTicketEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="wst", email="wst@example.com", password=PW)
        self.access = issue_tokens_for_user(self.user)["access"]

    def test_needs_login(self):
        self.assertIn(self.client.post("/login/auth/ws-ticket/").status_code, (401, 403))

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get("/login/auth/ws-ticket/", **_bearer(self.access)).status_code, 405)

    def test_returns_a_ticket_bound_to_user_and_session(self):
        res = self.client.post("/login/auth/ws-ticket/", **_bearer(self.access))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Cache-Control"], "no-store")
        body = res.json()
        self.assertTrue(body["ticket"])
        self.assertGreaterEqual(body["expires_in"], 5)
        user_id, jti = ws_ticket.redeem(body["ticket"])
        self.assertEqual(user_id, str(self.user.pk))
        self.assertTrue(jti)  # new logins always carry session_jti
        self.assertTrue(AuthToken.objects.filter(jti=jti).exists())

    def test_every_call_gives_a_different_ticket(self):
        a = self.client.post("/login/auth/ws-ticket/", **_bearer(self.access)).json()["ticket"]
        b = self.client.post("/login/auth/ws-ticket/", **_bearer(self.access)).json()["ticket"]
        self.assertNotEqual(a, b)


# ---------------------------------------------------------------------------------------------------------------
class _Capture:
    """Inner ASGI app: remembers the scope['user'] the middleware resolved."""

    def __init__(self):
        self.user = None

    async def __call__(self, scope, receive, send):
        self.user = scope["user"]


def _connect(query):
    from LearnScroll.ws_auth import JWTAuthMiddleware
    inner = _Capture()
    mw = JWTAuthMiddleware(inner)
    scope = {"type": "websocket", "path": "/ws/chat/1/", "query_string": query.encode()}
    async_to_sync(mw)(scope, None, None)
    return inner.user


class WsAuthMiddlewareTests(TransactionTestCase):
    """TransactionTestCase: the middleware reads the DB from a worker thread, which must see committed rows."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="wsm", email="wsm@example.com", password=PW)
        self.tokens = issue_tokens_for_user(self.user)

    def test_ticket_logs_the_user_in(self):
        ticket, _ = ws_ticket.issue(self.user.pk, None)
        self.assertEqual(_connect(f"ticket={ticket}").pk, self.user.pk)

    def test_ticket_works_only_once(self):
        ticket, _ = ws_ticket.issue(self.user.pk, None)
        self.assertTrue(_connect(f"ticket={ticket}").is_authenticated)
        self.assertFalse(_connect(f"ticket={ticket}").is_authenticated)

    def test_unknown_ticket_is_anonymous(self):
        self.assertIsInstance(_connect("ticket=garbage"), AnonymousUser)

    def test_ticket_of_an_ended_session_is_dead(self):
        jti = AuthToken.objects.filter(user=self.user).first().jti
        ticket, _ = ws_ticket.issue(self.user.pk, jti)
        AuthToken.objects.filter(jti=jti).delete()  # logout / "sign this device out"
        self.assertFalse(_connect(f"ticket={ticket}").is_authenticated)

    def test_ticket_of_a_deactivated_user_is_dead(self):
        ticket, _ = ws_ticket.issue(self.user.pk, None)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertFalse(_connect(f"ticket={ticket}").is_authenticated)

    @override_settings(WS_ALLOW_QUERY_TOKEN=True)
    def test_legacy_query_token_still_works_while_allowed(self):
        self.assertEqual(_connect(f"token={self.tokens['access']}").pk, self.user.pk)

    @override_settings(WS_ALLOW_QUERY_TOKEN=False)
    def test_legacy_query_token_is_refused_once_switched_off(self):
        self.assertFalse(_connect(f"token={self.tokens['access']}").is_authenticated)

    @override_settings(WS_ALLOW_QUERY_TOKEN=False)
    def test_ticket_still_works_when_query_token_is_off(self):
        ticket, _ = ws_ticket.issue(self.user.pk, None)
        self.assertTrue(_connect(f"ticket={ticket}").is_authenticated)

    def test_no_credentials_is_anonymous(self):
        self.assertFalse(_connect("").is_authenticated)

    @override_settings(WS_ALLOW_QUERY_TOKEN=True)
    def test_a_bad_ticket_does_not_fall_back_to_the_token(self):
        # ticket present => ticket decides; a stale ticket must not silently authenticate through ?token=.
        self.assertFalse(_connect(f"ticket=bad&token={self.tokens['access']}").is_authenticated)


# ---------------------------------------------------------------------------------------------------------------
class LogRedactionTests(SimpleTestCase):
    """LearnScroll/log_filters.py -- no token / ticket / JWT in a log line."""

    JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoxfQ.abcdefghijk"

    def _emit(self, msg, *args):
        import logging
        from LearnScroll.log_filters import RedactSecretsFilter
        rec = logging.LogRecord("daphne.ws_protocol", logging.INFO, __file__, 1, msg, args, None)
        self.assertTrue(RedactSecretsFilter().filter(rec))
        return rec.getMessage()

    def test_query_token_and_ticket_are_redacted(self):
        out = self._emit("WebSocket HANDSHAKING %s [%s]", f"/ws/chat/1/?token={self.JWT}&x=1", "1.2.3.4")
        self.assertNotIn("eyJ", out)
        self.assertIn("?token=[redacted]&x=1", out)
        self.assertIn("?ticket=[redacted]", self._emit("GET /ws/inbox/?ticket=abc123_-XYZ"))

    def test_a_bare_jwt_is_redacted(self):
        self.assertEqual(self._emit(f"Bearer {self.JWT} rejected"), "Bearer [redacted-jwt] rejected")

    def test_other_lines_and_params_are_untouched(self):
        self.assertEqual(self._emit("GET /feed/?page=2&token_type=x"), "GET /feed/?page=2&token_type=x")

    def test_bad_format_args_never_raise(self):
        import logging
        from LearnScroll.log_filters import RedactSecretsFilter
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "%d items", ("not-a-number",), None)
        self.assertTrue(RedactSecretsFilter().filter(rec))

    def test_console_handler_has_the_filter(self):
        from django.conf import settings
        self.assertIn("redact_secrets", settings.LOGGING["handlers"]["console"]["filters"])
