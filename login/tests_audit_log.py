# login/tests_audit_log.py
"""
L1 gap 19 -- audit log of login / OTP / reset / session events
(written, not yet run -- run with `python manage.py test login.tests_audit_log`).

Everything goes through the real URLs, like login/tests_auth_hardening.py. The abuse
counters and the audit de-dupe window live in the cache, so every test starts from
``cache.clear()``.
"""
import json
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import audit
from .audit import Event
from .models import AuthAuditLog, AuthToken, User
from .tasks import purge_auth_audit_logs

OLD_PW = "0ld!Passw0rd"
NEW_PW = "N3w!Passw0rd"
STAFF_PW = "Tr0ub4dor&3-Horse-Staple-9!"


def _code_from_outbox():
    body = mail.outbox[-1].body
    return next(t for t in body.split() if t.rstrip(".").isdigit()).rstrip(".")


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


def _device(device_id, name="Pixel 7", platform="android"):
    return {"HTTP_X_DEVICE_ID": device_id, "HTTP_X_DEVICE_NAME": name, "HTTP_X_PLATFORM": platform}


def _text_of(row):
    """Every free-text part of a row (not timestamps / ids, whose digits could clash with a 6-digit code)."""
    return " ".join([row.reason, row.identifier_masked, row.user_agent, json.dumps(row.metadata)])


def _rows(event=None, **filters):
    qs = AuthAuditLog.objects.filter(**filters)
    if event:
        qs = qs.filter(event=event)
    return qs


class AuditTestBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="rita", email="rita@example.com", password=OLD_PW)

    def login(self, password=OLD_PW, username="rita", **extra):
        return self.client.post("/login/", {"username": username, "password": password}, format="json", **extra)


# ---------------------------------------------------------------------------
# helpers (no DB view involved)
# ---------------------------------------------------------------------------
class MaskingTests(SimpleTestCase):
    def test_email_phone_username_are_masked(self):
        self.assertEqual(audit.mask_identifier("Rita.Sharma@Gmail.com"), "r***@gmail.com")
        self.assertEqual(audit.mask_identifier("+919876543210"), "+91********10")
        self.assertEqual(audit.mask_identifier("rita_sharma"), "ri***")
        self.assertEqual(audit.mask_identifier(""), "")

    def test_kind_detection(self):
        self.assertEqual(audit.kind_of("a@b.com"), "email")
        self.assertEqual(audit.kind_of("+91 98765-43210"), "phone")
        self.assertEqual(audit.kind_of("rita"), "username")

    def test_hash_is_stable_normalised_and_not_the_plain_value(self):
        a = audit.identifier_hash("Rita@Example.com ")
        b = audit.identifier_hash("rita@example.com")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)
        self.assertNotIn("rita", a)
        self.assertEqual(audit.identifier_hash("+91 98765-43210"), audit.identifier_hash("+919876543210"))
        self.assertNotEqual(audit.identifier_hash("x@y.com"), audit.identifier_hash("z@y.com"))
        self.assertEqual(audit.identifier_hash(""), "")


# ---------------------------------------------------------------------------
# the writer itself
# ---------------------------------------------------------------------------
class RecordTests(AuditTestBase):
    def test_secret_looking_meta_keys_are_dropped_and_only_primitives_kept(self):
        row = audit.record(
            "x_event", None, password="hunter2", otp_code="123456", refresh_token="abc",
            captcha_answer="QWERT", attempts_left=2, note="ok", nested={"a": 1}, nothing=None,
        )
        self.assertEqual(row.metadata, {"attempts_left": 2, "note": "ok"})

    def test_rows_are_append_only(self):
        row = audit.record(Event.LOGIN_SUCCESS, None, user=self.user)
        row.reason = "tampered"
        with self.assertRaises(ValueError):
            row.save()

    def test_a_failing_write_never_raises_or_breaks_the_caller(self):
        with mock.patch.object(AuthAuditLog.objects, "create", side_effect=RuntimeError("db down")):
            self.assertIsNone(audit.record(Event.LOGIN_SUCCESS, None, user=self.user))
            self.assertEqual(self.login().status_code, 200)  # login still works

    @override_settings(AUTH_AUDIT_ENABLED=False)
    def test_can_be_switched_off(self):
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(AuthAuditLog.objects.count(), 0)

    def test_noisy_events_are_written_once_per_window(self):
        for _ in range(4):
            audit.record(Event.CAPTCHA_REQUIRED, None, outcome=audit.BLOCKED, identifier="a@b.com")
        self.assertEqual(_rows(Event.CAPTCHA_REQUIRED).count(), 1)
        # a different identifier is a different key
        audit.record(Event.CAPTCHA_REQUIRED, None, outcome=audit.BLOCKED, identifier="c@d.com")
        self.assertEqual(_rows(Event.CAPTCHA_REQUIRED).count(), 2)

    @override_settings(AUTH_AUDIT_NOISE_DEDUPE_SECONDS=0)
    def test_dedupe_can_be_switched_off(self):
        for _ in range(3):
            audit.record(Event.CAPTCHA_REQUIRED, None, outcome=audit.BLOCKED, identifier="a@b.com")
        self.assertEqual(_rows(Event.CAPTCHA_REQUIRED).count(), 3)

    def test_non_noisy_events_are_never_deduped(self):
        for _ in range(3):
            audit.record(Event.LOGIN_FAILED, None, outcome=audit.FAILURE, identifier="a@b.com")
        self.assertEqual(_rows(Event.LOGIN_FAILED).count(), 3)


# ---------------------------------------------------------------------------
# password login
# ---------------------------------------------------------------------------
class LoginAuditTests(AuditTestBase):
    def test_success_row_has_user_ip_and_device(self):
        res = self.login(**_device("dev-abcdef123"))
        self.assertEqual(res.status_code, 200, res.data)
        row = _rows(Event.LOGIN_SUCCESS).get()
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.outcome, "success")
        self.assertEqual(row.ip_address, "127.0.0.1")
        self.assertEqual(row.device_id, "dev-abcdef123")
        self.assertEqual(row.platform, "android")
        self.assertEqual(row.identifier_masked, "ri***")
        self.assertEqual(row.metadata.get("method"), "password")

    def test_login_by_email_is_stored_masked_and_hashed(self):
        self.login(username="rita@example.com")
        row = _rows(Event.LOGIN_SUCCESS).get()
        self.assertEqual(row.identifier_kind, "email")
        self.assertEqual(row.identifier_masked, "r***@example.com")
        self.assertEqual(row.identifier_hash, audit.identifier_hash("rita@example.com"))

    def test_wrong_password_is_logged_with_attempts_left_and_never_the_password(self):
        res = self.login(password="Wr0ng!Passw0rd")
        self.assertEqual(res.status_code, 401)
        row = _rows(Event.LOGIN_FAILED).get()
        self.assertEqual(row.outcome, "failure")
        self.assertEqual(row.user, self.user)  # support can see WHO is being guessed at
        self.assertEqual(row.reason, "invalid_credentials")
        self.assertEqual(row.metadata["attempts_left"], 4)
        self.assertNotIn("Wr0ng", _text_of(row))

    def test_unknown_account_is_logged_without_a_user(self):
        self.login(username="nobody", password="Wr0ng!Passw0rd")
        row = _rows(Event.LOGIN_FAILED).get()
        self.assertIsNone(row.user)
        self.assertEqual(row.identifier_masked, "no***")

    def test_lockout_is_logged_once_and_repeats_are_deduped(self):
        for _ in range(5):
            self.login(password="Wr0ng!Passw0rd")
        self.assertEqual(_rows(Event.LOGIN_FAILED).count(), 5)
        self.assertEqual(_rows(Event.LOGIN_LOCKED).count(), 1)
        tripped = _rows(Event.LOGIN_LOCKED).get()
        self.assertEqual(tripped.outcome, "blocked")
        self.assertTrue(tripped.metadata.get("tripped_now"))

        for _ in range(3):  # already locked: refused before the password is looked at
            self.assertEqual(self.login(password=OLD_PW).status_code, 429)
        self.assertEqual(_rows(Event.LOGIN_LOCKED).count(), 1)  # not 4: attacker can't flood the table
        self.assertEqual(_rows(Event.LOGIN_FAILED).count(), 5)

    @override_settings(AUTH_AUDIT_NOISE_DEDUPE_SECONDS=0)
    def test_locked_attempts_each_logged_when_dedupe_is_off(self):
        for _ in range(5):
            self.login(password="Wr0ng!Passw0rd")
        for _ in range(3):
            self.login(password=OLD_PW)
        self.assertEqual(_rows(Event.LOGIN_LOCKED).count(), 4)  # 1 that tripped + 3 refused


# ---------------------------------------------------------------------------
# OTP
# ---------------------------------------------------------------------------
class OtpAuditTests(AuditTestBase):
    def _send(self, target):
        return self.client.post("/login/auth/send-otp/", {"email_or_phone": target}, format="json")

    def _verify(self, target, otp):
        return self.client.post("/login/auth/verify-otp/", {"email_or_phone": target, "otp": otp}, format="json")

    def test_send_wrong_code_then_otp_login(self):
        self.assertEqual(self._send("rita@example.com").status_code, 200)
        sent = _rows(Event.OTP_SENT).get()
        self.assertEqual(sent.user, self.user)
        self.assertEqual(sent.metadata["existing_account"], True)
        self.assertEqual(sent.metadata["purpose"], "verify")

        real = _code_from_outbox()
        wrong = "000000" if real != "000000" else "111111"
        self.assertEqual(self._verify("rita@example.com", wrong).status_code, 400)
        bad = _rows(Event.OTP_FAILED).get()
        self.assertEqual(bad.user, self.user)
        self.assertEqual(bad.reason, "otp_invalid")
        self.assertEqual(bad.metadata["attempts_left"], 4)

        res = self._verify("rita@example.com", real)
        self.assertEqual(res.status_code, 200, res.data)
        ok = _rows(Event.OTP_LOGIN).get()
        self.assertEqual(ok.user, self.user)
        self.assertEqual(ok.metadata["method"], "otp")

        # the code itself is never stored anywhere in the log
        for row in AuthAuditLog.objects.all():
            self.assertNotIn(real, _text_of(row))
            self.assertNotIn(wrong, _text_of(row))

    def test_signup_branch_logs_otp_verified_without_a_user(self):
        self.assertEqual(self._send("new.person@example.com").status_code, 200)
        self.assertEqual(self._verify("new.person@example.com", _code_from_outbox()).status_code, 200)
        row = _rows(Event.OTP_VERIFIED).get()
        self.assertIsNone(row.user)
        self.assertEqual(row.metadata["purpose"], "signup")
        self.assertEqual(row.identifier_masked, "n***@example.com")

    def test_code_that_was_never_requested(self):
        self.assertEqual(self._verify("ghost@example.com", "123456").status_code, 400)
        row = _rows(Event.OTP_FAILED).get()
        self.assertEqual(row.reason, "otp_not_found")

    def test_five_wrong_codes_then_locked(self):
        self._send("rita@example.com")
        real = _code_from_outbox()
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(5):
            self._verify("rita@example.com", wrong)
        self._verify("rita@example.com", wrong)  # 6th call sees the lock
        self.assertEqual(_rows(Event.OTP_FAILED, reason="otp_invalid").count(), 5)
        locked = _rows(Event.OTP_FAILED, reason="otp_locked").get()
        self.assertEqual(locked.outcome, "blocked")

    def test_resend_inside_cooldown_is_logged_as_blocked(self):
        self._send("rita@example.com")
        self.assertEqual(self._send("rita@example.com").status_code, 429)
        row = _rows(Event.OTP_SEND_BLOCKED).get()
        self.assertEqual(row.reason, "otp_cooldown")
        self.assertEqual(row.outcome, "blocked")
        self.assertEqual(row.user, self.user)
        self.assertIn("retry_after", row.metadata)

    @override_settings(DISPOSABLE_BLOCK_ENABLED=True)
    def test_disposable_email_is_logged_as_blocked(self):
        res = self._send("x@mailinator.com")
        self.assertEqual(res.status_code, 400)
        row = _rows(Event.OTP_SEND_BLOCKED).get()
        self.assertEqual(row.reason, res.data["code"])
        self.assertEqual(_rows(Event.OTP_SENT).count(), 0)


# ---------------------------------------------------------------------------
# forgot / reset / change password
# ---------------------------------------------------------------------------
class ResetAuditTests(AuditTestBase):
    def _forgot(self, target):
        return self.client.post("/login/auth/forgot-password/", {"email_or_phone": target}, format="json")

    def _reset(self, target, otp):
        return self.client.post(
            "/login/auth/reset-password/",
            {"email_or_phone": target, "otp": otp, "new_password": NEW_PW, "confirm_password": NEW_PW},
            format="json",
        )

    def test_unknown_email_is_visible_in_the_log_but_not_in_the_response(self):
        res = self._forgot("nobody@example.com")
        self.assertEqual(res.status_code, 200)  # same generic answer as for a real account
        row = _rows(Event.RESET_REQUESTED).get()
        self.assertEqual(row.outcome, "failure")
        self.assertEqual(row.reason, "no_account")
        self.assertIsNone(row.user)

    def test_full_reset_flow(self):
        self.login()  # one live session that the reset will end
        self.assertEqual(self._forgot("rita@example.com").status_code, 200)
        requested = _rows(Event.RESET_REQUESTED).get()
        self.assertEqual(requested.user, self.user)
        self.assertEqual(requested.outcome, "success")

        real = _code_from_outbox()
        wrong = "000000" if real != "000000" else "111111"
        self.assertEqual(self._reset("rita@example.com", wrong).status_code, 400)
        failed = _rows(Event.RESET_FAILED).get()
        self.assertEqual(failed.reason, "otp_invalid")
        self.assertEqual(failed.user, self.user)
        self.assertEqual(failed.metadata["attempts_left"], 4)

        res = self._reset("rita@example.com", real)
        self.assertEqual(res.status_code, 200, res.data)
        done = _rows(Event.RESET_SUCCESS).get()
        self.assertEqual(done.user, self.user)
        self.assertEqual(done.metadata["sessions_ended"], 1)
        self.assertEqual(done.ip_address, "127.0.0.1")
        for row in AuthAuditLog.objects.all():  # neither the code nor the new password is stored
            self.assertNotIn(real, _text_of(row))
            self.assertNotIn(NEW_PW, _text_of(row))

    def test_change_password_is_logged_with_sessions_ended(self):
        a = self.login(**_device("phone-aaaaaaaa")).data["token"]
        self.login(**_device("tab-bbbbbbbbb"))
        res = self.client.post(
            "/login/auth/change-password/",
            {"current_password": OLD_PW, "new_password": NEW_PW, "confirm_password": NEW_PW},
            format="json",
            **_bearer(a["access"]),
        )
        self.assertEqual(res.status_code, 200, res.data)
        row = _rows(Event.PASSWORD_CHANGED).get()
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.metadata["sessions_ended"], 1)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
class SessionAuditTests(AuditTestBase):
    def test_logout_is_logged_but_an_unknown_session_is_not(self):
        tok = self.login().data["token"]
        res = self.client.post("/login/auth/logout/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(_rows(Event.LOGOUT).get().user, self.user)

        self.client.post("/login/auth/logout/", {"refresh": tok["refresh"]}, format="json")  # already dead
        self.client.post("/login/auth/logout/", {}, format="json")  # nothing sent
        self.assertEqual(_rows(Event.LOGOUT).count(), 1)

    def test_sign_out_one_device_and_logout_all(self):
        a = self.login(**_device("phone-aaaaaaaa", "Pixel 7", "android")).data["token"]
        self.login(**_device("tab-bbbbbbbbb", "iPad", "ios"))
        other = AuthToken.objects.get(device_id="tab-bbbbbbbbb")

        res = self.client.delete(f"/login/auth/sessions/{other.pk}/", **_bearer(a["access"]))
        self.assertEqual(res.status_code, 200, res.data)
        row = _rows(Event.SESSION_REVOKED).get()
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.metadata["revoked_device"], "iPad")
        self.assertEqual(row.metadata["was_current"], False)

        res = self.client.post("/login/auth/logout-all/", {"keep_current": True}, format="json", **_bearer(a["access"]))
        self.assertEqual(res.status_code, 200, res.data)
        row = _rows(Event.LOGOUT_ALL).get()
        self.assertEqual(row.metadata["keep_current"], True)
        self.assertEqual(row.metadata["sessions_ended"], 0)

    def test_refresh_with_an_ended_session_is_logged(self):
        tok = self.login().data["token"]
        AuthToken.objects.filter(user=self.user).delete()  # e.g. "sign out everywhere" on another phone
        res = self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json")
        self.assertEqual(res.status_code, 401)
        row = _rows(Event.TOKEN_REFRESH_DENIED).get()
        self.assertEqual(row.user, self.user)
        self.assertEqual(row.reason, "session_ended")
        self.assertEqual(row.outcome, "blocked")

    def test_a_good_refresh_writes_nothing(self):
        tok = self.login().data["token"]
        before = AuthAuditLog.objects.count()
        self.assertEqual(
            self.client.post("/login/auth/token/refresh/", {"refresh": tok["refresh"]}, format="json").status_code, 200
        )
        self.assertEqual(AuthAuditLog.objects.count(), before)


# ---------------------------------------------------------------------------
# account deletion
# ---------------------------------------------------------------------------
@override_settings(ACCOUNT_DELETION_GRACE_DAYS=0)  # S5: immediate erase; the grace path is tested in tests_data_rights.py
class AccountDeletionAuditTests(AuditTestBase):
    def test_deletion_keeps_the_trail_but_blanks_personal_text(self):
        self.login(username="rita@example.com")
        self.assertTrue(_rows(Event.LOGIN_SUCCESS).filter(identifier_masked="r***@example.com").exists())

        api = APIClient()
        api.force_authenticate(self.user)
        wrong = api.post("/login/auth/delete-account/", {"password": "nope", "confirm": "DELETE"}, format="json")
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(_rows(Event.ACCOUNT_DELETE_FAILED).get().reason, "incorrect_password")

        res = api.post("/login/auth/delete-account/", {"password": OLD_PW, "confirm": "DELETE"}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

        self.assertEqual(_rows(Event.ACCOUNT_DELETED).get().user, self.user)
        for row in AuthAuditLog.objects.filter(user=self.user):
            self.assertEqual((row.identifier_masked, row.identifier_hash, row.user_agent), ("", "", ""))
        # the history itself (event / outcome / ip / time) is still there
        self.assertTrue(_rows(Event.LOGIN_SUCCESS, user=self.user).exists())


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------
class RetentionTests(TestCase):
    def _make(self, days_old):
        return AuthAuditLog.objects.create(
            event=Event.LOGIN_SUCCESS, created_at=timezone.now() - timedelta(days=days_old)
        )

    @override_settings(AUTH_AUDIT_RETENTION_DAYS=30)
    def test_old_rows_are_purged_new_ones_kept(self):
        old, recent = self._make(31), self._make(5)
        result = purge_auth_audit_logs()
        self.assertEqual(result["audit_logs_deleted"], 1)
        self.assertFalse(AuthAuditLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(AuthAuditLog.objects.filter(pk=recent.pk).exists())

    @override_settings(AUTH_AUDIT_RETENTION_DAYS=0)
    def test_zero_keeps_everything(self):
        self._make(4000)
        purge_auth_audit_logs()
        self.assertEqual(AuthAuditLog.objects.count(), 1)


# ---------------------------------------------------------------------------
# Django admin
# ---------------------------------------------------------------------------
class AdminLoginAuditTests(TestCase):
    """common/admin_site.py also writes to the table (it only had a logger before)."""

    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_user(
            username="boss", email="boss@example.com", password=STAFF_PW, is_staff=True, is_superuser=True
        )

    def _post(self, username, password):
        return self.client.post(
            "/admin/login/", {"username": username, "password": password, "next": "/admin/"}
        )

    def test_failed_then_successful_admin_login(self):
        self._post("boss", "wrong-password")
        failed = _rows(Event.ADMIN_LOGIN_FAILED).get()
        self.assertEqual(failed.outcome, "failure")
        self.assertEqual(failed.identifier_masked, "bo***")

        res = self._post("boss", STAFF_PW)
        self.assertEqual(res.status_code, 302, getattr(res, "content", b"")[:300])
        ok = _rows(Event.ADMIN_LOGIN).get()
        self.assertEqual(ok.user, self.boss)

    def test_admin_lockout_is_logged(self):
        for _ in range(5):
            self._post("boss", "wrong-password")
        self._post("boss", STAFF_PW)  # locked out even with the right password
        self.assertEqual(_rows(Event.ADMIN_LOGIN_FAILED).count(), 5)
        self.assertEqual(_rows(Event.ADMIN_LOGIN_LOCKED).count(), 1)
        self.assertEqual(_rows(Event.ADMIN_LOGIN).count(), 0)


class AuditAdminPageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.boss = User.objects.create_user(
            username="boss", email="boss@example.com", password=STAFF_PW, is_staff=True, is_superuser=True
        )
        audit.record(Event.LOGIN_FAILED, None, outcome=audit.FAILURE, identifier="rita@example.com", reason="invalid_credentials")
        self.client.force_login(self.boss)

    def test_list_is_visible_and_searchable_by_full_email(self):
        res = self.client.get("/admin/login/authauditlog/")
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "r***@example.com")
        self.assertNotContains(res, "rita@example.com")

        res = self.client.get("/admin/login/authauditlog/", {"q": "rita@example.com"})
        self.assertContains(res, "r***@example.com")  # found through the hash
        res = self.client.get("/admin/login/authauditlog/", {"q": "someone.else@example.com"})
        self.assertNotContains(res, "r***@example.com")

    def test_it_is_read_only(self):
        row = AuthAuditLog.objects.get()
        self.assertEqual(self.client.get("/admin/login/authauditlog/add/").status_code, 403)
        self.assertEqual(self.client.post(f"/admin/login/authauditlog/{row.pk}/delete/", {"post": "yes"}).status_code, 403)
        self.assertTrue(AuthAuditLog.objects.filter(pk=row.pk).exists())
