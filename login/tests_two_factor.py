# login/tests_two_factor.py
"""
S3 -- Security centre (written, NOT yet run -- `python manage.py test login.tests_two_factor`).

Covers
  * 2FA enrolment: re-auth with password, secret/otpauth URL, NOT active until a right code, 10 backup codes once
  * login gate: password login with 2FA -> NO tokens + signed challenge; /2fa/verify/ -> same payload as login
  * backup codes work exactly once; challenge cannot be replayed; expired / forged challenge refused
  * brute force: 5 wrong codes -> locked (429 two_factor_locked)
  * disable / regenerate need password + code
  * security overview + activity feed (own rows only, paging)
  * new-device alert (audit row + e-mail, once per device, `login_alerts` off -> audit only, first login silent)

django-otp keeps its own per-device throttle (a wrong code delays the next try) and a replay guard (`last_t`), so the
tests switch the throttle off and reset `last_t` before computing a code. That is test plumbing only, production
settings are untouched.
"""
from unittest import mock

from django.core import mail, signing
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import two_factor
from .audit import Event
from .models import AuthAuditLog, AuthToken, User

PW = "Sup3r!Passw0rd"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


def _device_headers(device_id, name="Pixel 7", platform="android"):
    return {"HTTP_X_DEVICE_ID": device_id, "HTTP_X_DEVICE_NAME": name, "HTTP_X_PLATFORM": platform}


def _current_code(user):
    """The 6-digit code the authenticator app would show right now (replay guard reset)."""
    from django_otp.oath import totp
    from django_otp.plugins.otp_totp.models import TOTPDevice

    dev = TOTPDevice.objects.get(user=user, name=two_factor.APP_TOTP_NAME)
    dev.last_t = -1
    dev.save(update_fields=["last_t"])
    return f"{totp(dev.bin_key, dev.step, dev.t0, dev.digits, dev.drift):0{dev.digits}d}"


def _wrong_code(user):
    good = _current_code(user)
    return "000000" if good != "000000" else "111111"


@override_settings(OTP_TOTP_THROTTLE_FACTOR=0, OTP_STATIC_THROTTLE_FACTOR=0)
class TwoFactorBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="sec", email="sec@example.com", password=PW)

    # -- helpers ------------------------------------------------------------------
    def _login_plain(self, **extra):
        res = self.client.post("/login/", {"username": "sec", "password": PW}, format="json", **extra)
        self.assertEqual(res.status_code, 200, res.data)
        return res

    def _auth(self):
        """Log in (no 2FA yet) and return the access token."""
        return self._login_plain().data["token"]["access"]

    def _enable_2fa(self):
        """Full enrolment through the API. Returns (access, backup_codes)."""
        access = self._auth()
        res = self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        res = self.client.post(
            "/login/auth/2fa/enable/", {"code": _current_code(self.user)}, format="json", **_bearer(access)
        )
        self.assertEqual(res.status_code, 200, res.data)
        return access, res.data["backup_codes"]

    def _challenge(self):
        res = self.client.post("/login/", {"username": "sec", "password": PW}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data.get("two_factor_required"), res.data)
        return res.data["challenge"]


# =============================================================================== enrolment
class TwoFactorEnrolmentTests(TwoFactorBase):
    def test_setup_needs_login(self):
        res = self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json")
        self.assertIn(res.status_code, (401, 403))

    def test_setup_needs_current_password(self):
        access = self._auth()
        res = self.client.post("/login/auth/2fa/setup/", {}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "invalid_password")
        res = self.client.post("/login/auth/2fa/setup/", {"password": "nope-nope"}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 400)
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_setup_returns_secret_and_url_but_does_not_enable(self):
        access = self._auth()
        res = self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["secret"])
        self.assertTrue(res.data["otpauth_url"].startswith("otpauth://totp/"))
        # half-finished setup must never lock anybody out
        self.assertFalse(two_factor.is_enabled(self.user))
        again = self._login_plain()
        self.assertNotIn("two_factor_required", again.data)
        self.assertIn("access", again.data["token"])

    def test_enable_with_wrong_code_is_refused(self):
        access = self._auth()
        self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        res = self.client.post(
            "/login/auth/2fa/enable/", {"code": _wrong_code(self.user)}, format="json", **_bearer(access)
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "two_factor_invalid_code")
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_enable_without_setup_is_refused(self):
        access = self._auth()
        res = self.client.post("/login/auth/2fa/enable/", {"code": "123456"}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 400)
        self.assertFalse(two_factor.is_enabled(self.user))

    def test_enable_with_right_code_gives_ten_backup_codes(self):
        _access, codes = self._enable_2fa()
        self.assertTrue(two_factor.is_enabled(self.user))
        self.assertEqual(len(codes), two_factor.BACKUP_CODE_COUNT)
        self.assertEqual(len(set(codes)), len(codes))
        self.assertEqual(two_factor.backup_codes_remaining(self.user), 10)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.TWO_FACTOR_ENABLED).exists())

    def test_setup_and_enable_when_already_on_conflict(self):
        access, _codes = self._enable_2fa()
        res = self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "two_factor_already_enabled")
        res = self.client.post(
            "/login/auth/2fa/enable/", {"code": _current_code(self.user)}, format="json", **_bearer(access)
        )
        self.assertEqual(res.status_code, 409)

    def test_enrolment_does_not_touch_other_users(self):
        other = User.objects.create_user(username="other", email="o@example.com", password=PW)
        self._enable_2fa()
        self.assertFalse(two_factor.is_enabled(other))
        res = self.client.post("/login/", {"username": "other", "password": PW}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("two_factor_required", res.data)


# =============================================================================== login step 2
class TwoFactorLoginTests(TwoFactorBase):
    def setUp(self):
        super().setUp()
        _access, self.backup = self._enable_2fa()
        AuthToken.objects.filter(user=self.user).delete()

    def test_password_login_gives_challenge_and_no_tokens(self):
        res = self.client.post("/login/", {"username": "sec", "password": PW}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["two_factor_required"])
        self.assertTrue(res.data["challenge"])
        self.assertNotIn("token", res.data)
        self.assertNotIn("access", res.data)
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 0)

    def test_wrong_password_still_fails_before_the_gate(self):
        res = self.client.post("/login/", {"username": "sec", "password": "wrong-pass-1"}, format="json")
        self.assertEqual(res.status_code, 401)
        self.assertNotIn("challenge", res.data)

    def test_verify_with_authenticator_code_logs_in(self):
        challenge = self._challenge()
        res = self.client.post(
            "/login/auth/2fa/verify/",
            {"challenge": challenge, "code": _current_code(self.user)},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        # same shape the normal login answers with, so the app's session saving works unchanged
        self.assertEqual(res.data["token"]["access"], res.data["access"])
        self.assertEqual(res.data["token"]["refresh"], res.data["refresh"])
        self.assertEqual(res.data["user"]["username"], "sec")
        self.assertIsNone(res.data["backup_codes_remaining"])
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 1)
        # the access token really works
        sessions = self.client.get("/login/auth/sessions/", **_bearer(res.data["access"]))
        self.assertEqual(sessions.status_code, 200)

    def test_code_with_spaces_and_dash_is_accepted(self):
        challenge = self._challenge()
        code = _current_code(self.user)
        spaced = f"{code[:3]} {code[3:]}"
        res = self.client.post("/login/auth/2fa/verify/", {"challenge": challenge, "code": spaced}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_wrong_code_is_refused_and_no_tokens(self):
        challenge = self._challenge()
        res = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": challenge, "code": _wrong_code(self.user)}, format="json"
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "two_factor_invalid_code")
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 0)
        # the same challenge still works with the right code (a typo must not force a new password step)
        ok = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": challenge, "code": _current_code(self.user)}, format="json"
        )
        self.assertEqual(ok.status_code, 200, ok.data)

    def test_backup_code_works_once(self):
        code = self.backup[0]
        first = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": self._challenge(), "code": code}, format="json"
        )
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data["backup_codes_remaining"], 9)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.TWO_FACTOR_BACKUP_USED).exists())
        second = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": self._challenge(), "code": code}, format="json"
        )
        self.assertEqual(second.status_code, 400)
        self.assertEqual(second.data["code"], "two_factor_invalid_code")

    def test_backup_code_is_case_and_dash_tolerant(self):
        code = self.backup[1].upper()
        res = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": self._challenge(), "code": code}, format="json"
        )
        self.assertEqual(res.status_code, 200, res.data)

    def test_challenge_cannot_be_replayed(self):
        challenge = self._challenge()
        first = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": challenge, "code": _current_code(self.user)}, format="json"
        )
        self.assertEqual(first.status_code, 200)
        again = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": challenge, "code": _current_code(self.user)}, format="json"
        )
        self.assertEqual(again.status_code, 401)
        self.assertEqual(again.data["code"], "two_factor_challenge_expired")

    def test_forged_and_missing_challenge_refused(self):
        for bad in ("", "garbage", None):
            res = self.client.post(
                "/login/auth/2fa/verify/", {"challenge": bad, "code": _current_code(self.user)}, format="json"
            )
            self.assertEqual(res.status_code, 401, bad)
            self.assertEqual(res.data["code"], "two_factor_challenge_expired")
        # signed with a different salt (e.g. some other feature's token) is not accepted
        other_salt = signing.dumps({"u": self.user.pk, "n": "abc"}, salt="something.else")
        res = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": other_salt, "code": _current_code(self.user)}, format="json"
        )
        self.assertEqual(res.status_code, 401)

    def test_expired_challenge_refused(self):
        challenge = self._challenge()
        with mock.patch.object(two_factor, "CHALLENGE_MAX_AGE", -1):
            res = self.client.post(
                "/login/auth/2fa/verify/", {"challenge": challenge, "code": _current_code(self.user)}, format="json"
            )
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.data["code"], "two_factor_challenge_expired")

    def test_challenge_for_user_who_turned_2fa_off_is_refused(self):
        challenge = self._challenge()
        two_factor.disable(self.user)
        res = self.client.post("/login/auth/2fa/verify/", {"challenge": challenge, "code": "123456"}, format="json")
        self.assertEqual(res.status_code, 401)

    def test_five_wrong_codes_lock_the_account_for_guessing(self):
        challenge = self._challenge()
        bad = _wrong_code(self.user)
        last = None
        for _ in range(two_factor.MAX_FAILURES):
            last = self.client.post("/login/auth/2fa/verify/", {"challenge": challenge, "code": bad}, format="json")
        self.assertEqual(last.status_code, 429, last.data)
        self.assertEqual(last.data["code"], "two_factor_locked")
        # even the RIGHT code is refused while locked
        locked = self.client.post(
            "/login/auth/2fa/verify/", {"challenge": challenge, "code": _current_code(self.user)}, format="json"
        )
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(AuthToken.objects.filter(user=self.user).count(), 0)

    def test_success_resets_the_failure_counter(self):
        bad = _wrong_code(self.user)
        for _ in range(two_factor.MAX_FAILURES - 1):
            self.client.post("/login/auth/2fa/verify/", {"challenge": self._challenge(), "code": bad}, format="json")
        ok = self.client.post(
            "/login/auth/2fa/verify/",
            {"challenge": self._challenge(), "code": _current_code(self.user)},
            format="json",
        )
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(two_factor.seconds_locked(self.user), 0)


# =============================================================================== disable / regenerate
class TwoFactorManageTests(TwoFactorBase):
    def setUp(self):
        super().setUp()
        self.access, self.backup = self._enable_2fa()

    def test_disable_needs_password_and_code(self):
        no_pw = self.client.post(
            "/login/auth/2fa/disable/", {"code": _current_code(self.user)}, format="json", **_bearer(self.access)
        )
        self.assertEqual(no_pw.status_code, 400)
        self.assertEqual(no_pw.data["code"], "invalid_password")
        bad_code = self.client.post(
            "/login/auth/2fa/disable/",
            {"password": PW, "code": _wrong_code(self.user)},
            format="json",
            **_bearer(self.access),
        )
        self.assertEqual(bad_code.status_code, 400)
        self.assertEqual(bad_code.data["code"], "two_factor_invalid_code")
        self.assertTrue(two_factor.is_enabled(self.user))

    def test_disable_with_backup_code_works_and_login_goes_back_to_normal(self):
        res = self.client.post(
            "/login/auth/2fa/disable/",
            {"password": PW, "code": self.backup[0]},
            format="json",
            **_bearer(self.access),
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(two_factor.is_enabled(self.user))
        self.assertEqual(two_factor.backup_codes_remaining(self.user), 0)
        login = self._login_plain()
        self.assertNotIn("two_factor_required", login.data)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.TWO_FACTOR_DISABLED).exists())

    def test_disable_when_off_conflicts(self):
        two_factor.disable(self.user)
        res = self.client.post(
            "/login/auth/2fa/disable/", {"password": PW, "code": "123456"}, format="json", **_bearer(self.access)
        )
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "two_factor_not_enabled")

    def test_regenerate_backup_codes_kills_the_old_ones(self):
        old = list(self.backup)
        res = self.client.post(
            "/login/auth/2fa/backup-codes/",
            {"password": PW, "code": _current_code(self.user)},
            format="json",
            **_bearer(self.access),
        )
        self.assertEqual(res.status_code, 200, res.data)
        new = res.data["backup_codes"]
        self.assertEqual(len(new), two_factor.BACKUP_CODE_COUNT)
        self.assertFalse(set(new) & set(old))
        # an old code no longer logs in
        challenge = self.client.post("/login/", {"username": "sec", "password": PW}, format="json").data["challenge"]
        res = self.client.post("/login/auth/2fa/verify/", {"challenge": challenge, "code": old[0]}, format="json")
        self.assertEqual(res.status_code, 400)
        # a new one does
        res = self.client.post("/login/auth/2fa/verify/", {"challenge": challenge, "code": new[0]}, format="json")
        self.assertEqual(res.status_code, 200, res.data)

    def test_regenerate_needs_password(self):
        res = self.client.post(
            "/login/auth/2fa/backup-codes/",
            {"code": _current_code(self.user)},
            format="json",
            **_bearer(self.access),
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(two_factor.backup_codes_remaining(self.user), 10)

    def test_google_only_account_has_no_password_check_but_still_needs_the_code(self):
        self.user.set_unusable_password()
        self.user.save()
        res = self.client.post(
            "/login/auth/2fa/disable/", {"code": _wrong_code(self.user)}, format="json", **_bearer(self.access)
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.data["code"], "two_factor_invalid_code")
        res = self.client.post(
            "/login/auth/2fa/disable/", {"code": _current_code(self.user)}, format="json", **_bearer(self.access)
        )
        self.assertEqual(res.status_code, 200, res.data)


# =============================================================================== overview + activity
class SecurityOverviewTests(TwoFactorBase):
    def test_overview_requires_login(self):
        res = self.client.get("/login/auth/security/overview/")
        self.assertIn(res.status_code, (401, 403))

    def test_overview_before_and_after_2fa(self):
        access = self._auth()
        res = self.client.get("/login/auth/security/overview/", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["two_factor"]["enabled"])
        self.assertEqual(res.data["two_factor"]["backup_codes_remaining"], 0)
        self.assertTrue(res.data["has_password"])
        self.assertTrue(res.data["login_alerts"])  # default on
        self.assertGreaterEqual(res.data["sessions_count"], 1)

        self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.client.post(
            "/login/auth/2fa/enable/", {"code": _current_code(self.user)}, format="json", **_bearer(access)
        )
        res = self.client.get("/login/auth/security/overview/", **_bearer(access))
        self.assertTrue(res.data["two_factor"]["enabled"])
        self.assertEqual(res.data["two_factor"]["backup_codes_remaining"], 10)

    def test_overview_masks_contact_details_and_reports_recovery_readiness(self):
        self.user.phone = "+919876543210"
        self.user.phone_verified = True
        self.user.save()
        access = self._auth()
        res = self.client.get("/login/auth/security/overview/", **_bearer(access))
        rec = res.data["recovery"]
        self.assertNotIn("9876543210", rec["phone"])
        self.assertNotIn("sec@example.com", rec["email"])
        self.assertTrue(rec["phone_verified"])
        self.assertTrue(rec["ready"])

    def test_recovery_not_ready_without_any_verified_channel(self):
        self.user.email = ""
        self.user.phone = ""
        self.user.save()
        access = self._auth()
        res = self.client.get("/login/auth/security/overview/", **_bearer(access))
        self.assertFalse(res.data["recovery"]["ready"])


class SecurityActivityTests(TwoFactorBase):
    def test_activity_lists_own_events_newest_first(self):
        access = self._auth()
        self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.client.post(
            "/login/auth/2fa/enable/", {"code": _current_code(self.user)}, format="json", **_bearer(access)
        )
        res = self.client.get("/login/auth/security/activity/", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        events = [i["event"] for i in res.data["items"]]
        self.assertIn(Event.LOGIN_SUCCESS, events)
        self.assertIn(Event.TWO_FACTOR_ENABLED, events)
        self.assertLess(events.index(Event.TWO_FACTOR_ENABLED), events.index(Event.LOGIN_SUCCESS))  # newest first
        for item in res.data["items"]:
            self.assertTrue(item["label"])

    def test_activity_never_shows_another_users_rows(self):
        other = User.objects.create_user(username="other", email="o@example.com", password=PW)
        c2 = APIClient()
        c2.post("/login/", {"username": "other", "password": PW}, format="json")
        access = self._auth()
        res = self.client.get("/login/auth/security/activity/", **_bearer(access))
        ids = set(AuthAuditLog.objects.filter(user=other).values_list("id", flat=True))
        self.assertTrue(ids)
        self.assertFalse(ids & {i["id"] for i in res.data["items"]})

    def test_activity_paging(self):
        access = self._auth()
        for _ in range(4):
            self.client.post("/login/", {"username": "sec", "password": PW}, format="json")
        first = self.client.get("/login/auth/security/activity/?limit=2", **_bearer(access))
        self.assertEqual(len(first.data["items"]), 2)
        self.assertIsNotNone(first.data["next_before"])
        nxt = self.client.get(
            f"/login/auth/security/activity/?limit=2&before={first.data['next_before']}", **_bearer(access)
        )
        self.assertTrue(nxt.data["items"])
        self.assertLess(nxt.data["items"][0]["id"], first.data["items"][-1]["id"])

    def test_activity_limit_is_clamped_and_garbage_ignored(self):
        access = self._auth()
        res = self.client.get("/login/auth/security/activity/?limit=9999&before=abc", **_bearer(access))
        self.assertEqual(res.status_code, 200)
        res = self.client.get("/login/auth/security/activity/?limit=zzz", **_bearer(access))
        self.assertEqual(res.status_code, 200)


# =============================================================================== new device alert
class NewDeviceAlertTests(TwoFactorBase):
    def _login_from(self, device_id):
        return self.client.post(
            "/login/", {"username": "sec", "password": PW}, format="json", **_device_headers(device_id)
        )

    def test_first_ever_login_is_not_an_alert(self):
        self._login_from("device-aaaaaaaa")
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuthAuditLog.objects.filter(user=self.user, event=Event.NEW_DEVICE_LOGIN).exists())

    def test_same_device_again_is_quiet(self):
        self._login_from("device-aaaaaaaa")
        self._login_from("device-aaaaaaaa")
        self.assertEqual(len(mail.outbox), 0)

    def test_new_device_writes_audit_row_and_emails(self):
        self._login_from("device-aaaaaaaa")
        res = self._login_from("device-bbbbbbbb")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.NEW_DEVICE_LOGIN).exists())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["sec@example.com"])
        self.assertIn("new device", mail.outbox[0].body.lower())
        # the new device is now known: no second mail
        self._login_from("device-bbbbbbbb")
        self.assertEqual(len(mail.outbox), 1)

    def test_alerts_off_keeps_audit_row_but_sends_no_mail(self):
        from user_profile.models import UserPreference

        pref = UserPreference.for_user(self.user)
        pref.login_alerts = False
        pref.save()
        self._login_from("device-aaaaaaaa")
        self._login_from("device-bbbbbbbb")
        self.assertEqual(len(mail.outbox), 0)
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.NEW_DEVICE_LOGIN).exists())

    def test_no_email_on_account_means_no_mail_and_no_crash(self):
        self.user.email = ""
        self.user.save()
        self._login_from("device-aaaaaaaa")
        res = self._login_from("device-bbbbbbbb")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_mail_failure_never_breaks_login(self):
        self._login_from("device-aaaaaaaa")
        with mock.patch("login.login_alerts.send_mail", side_effect=RuntimeError("smtp down")):
            res = self._login_from("device-bbbbbbbb")
        self.assertEqual(res.status_code, 200, res.data)

    def test_new_device_after_2fa_login_is_flagged_after_the_second_step(self):
        self._login_from("device-aaaaaaaa")
        access = self._auth()
        self.client.post("/login/auth/2fa/setup/", {"password": PW}, format="json", **_bearer(access))
        self.client.post(
            "/login/auth/2fa/enable/", {"code": _current_code(self.user)}, format="json", **_bearer(access)
        )
        mail.outbox.clear()
        res = self._login_from("device-cccccccc")
        self.assertTrue(res.data["two_factor_required"])
        self.assertEqual(len(mail.outbox), 0)  # nothing yet: this device has not proved anything
        res = self.client.post(
            "/login/auth/2fa/verify/",
            {"challenge": res.data["challenge"], "code": _current_code(self.user)},
            format="json",
            **_device_headers("device-cccccccc"),
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 1)


# =============================================================================== login_alerts preference
class LoginAlertsPreferenceTests(TwoFactorBase):
    def test_toggle_through_preferences_endpoint(self):
        access = self._auth()
        res = self.client.get("/profile/preferences/me/", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["data"]["login_alerts"])
        res = self.client.patch("/profile/preferences/me/", {"login_alerts": False}, format="json", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["data"]["login_alerts"])
        overview = self.client.get("/login/auth/security/overview/", **_bearer(access))
        self.assertFalse(overview.data["login_alerts"])
        # ... and the change shows up in the Security activity list
        activity = self.client.get("/login/auth/security/activity/", **_bearer(access))
        self.assertIn(Event.LOGIN_ALERTS_CHANGED, [i["event"] for i in activity.data["items"]])

    def test_patching_the_same_value_leaves_no_audit_row(self):
        access = self._auth()
        self.client.patch("/profile/preferences/me/", {"login_alerts": True}, format="json", **_bearer(access))
        self.assertFalse(AuthAuditLog.objects.filter(user=self.user, event=Event.LOGIN_ALERTS_CHANGED).exists())


# =============================================================================== change password re-auth
class ChangePasswordReauthTests(TwoFactorBase):
    NEW = "N3w!Passw0rd#7"

    def _change(self, access, **body):
        payload = {"new_password": self.NEW, "confirm_password": self.NEW, **body}
        return self.client.post("/login/auth/change-password/", payload, format="json", **_bearer(access))

    def test_current_password_is_required(self):
        access = self._auth()
        res = self._change(access)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "current_password_required")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PW))

    def test_wrong_current_password_is_refused_and_logged(self):
        access = self._auth()
        res = self._change(access, current_password="Wr0ng!Passw0rd")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "invalid_password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PW))
        self.assertTrue(AuthAuditLog.objects.filter(user=self.user, event=Event.PASSWORD_CHANGE_FAILED).exists())
        activity = self.client.get("/login/auth/security/activity/", **_bearer(access))
        self.assertIn(Event.PASSWORD_CHANGE_FAILED, [i["event"] for i in activity.data["items"]])

    def test_right_current_password_changes_it_and_ends_other_sessions(self):
        other = self.client.post("/login/", {"username": "sec", "password": PW}, format="json").data["token"]
        access = self._auth()
        res = self._change(access, current_password=PW)
        self.assertEqual(res.status_code, 200, res.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW))
        self.assertEqual(self.client.get("/login/auth/sessions/", **_bearer(other["access"])).status_code, 401)

    def test_google_only_account_needs_no_current_password(self):
        access = self._auth()
        self.user.set_unusable_password()
        self.user.save()
        res = self._change(access)
        self.assertEqual(res.status_code, 200, res.data)

    @override_settings(CHANGE_PASSWORD_REQUIRE_CURRENT=False)
    def test_switch_lets_old_app_builds_keep_working(self):
        access = self._auth()
        res = self._change(access)
        self.assertEqual(res.status_code, 200, res.data)

