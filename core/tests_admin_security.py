"""Task 14 tests: staff password policy, admin lockout, TOTP two-factor on /admin/, enrol commands."""
import io
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from common.password_policy import staff_password_problems

User = get_user_model()
STRONG = "Tr1cky-Moderator#Pass-2026"
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},  # no manifest in tests
}


def totp_now(device):
    t = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    t.time = time.time()
    return f"{t.token():0{device.digits}d}"


class StaffPasswordPolicyTests(TestCase):
    def test_rules(self):
        self.assertEqual(staff_password_problems(STRONG), [])
        weak = staff_password_problems("short1!")
        self.assertTrue(any("14 characters" in p for p in weak))
        self.assertTrue(any("uppercase" in p for p in staff_password_problems("alllowercase-1234567!")))
        self.assertTrue(any("symbol" in p for p in staff_password_problems("NoSymbolsHere12345678")))
        self.assertTrue(any("digit" in p for p in staff_password_problems("No-Digits-Here-Abcdefgh!")))

    def test_must_not_contain_identity(self):
        u = User(username="moderator_rita", email="rita.k@x.com")
        self.assertTrue(any("username" in p for p in staff_password_problems("Moderator_Rita#2026-ok!", u)))
        self.assertTrue(any("username" in p for p in staff_password_problems("Zz9#rita.kZz9#Long-enough", u)))

    def test_validator_only_hits_staff(self):
        staff = User(username="s1", email="s1@x.com", is_staff=True)
        student = User(username="stu1", email="stu1@x.com")
        with self.assertRaises(ValidationError):
            validate_password("Abcdef12!x", staff)  # 10 chars: fine for a student, not for staff
        validate_password("Abcdef12!x", student)       # still allowed for students
        validate_password(STRONG, staff)

    def test_superuser_counts_as_staff(self):
        with self.assertRaises(ValidationError):
            validate_password("Abcdef12!x", User(username="boss", is_superuser=True))


@override_settings(STORAGES=PLAIN_STATIC, ADMIN_REQUIRE_2FA=False, ADMIN_LOGIN_MAX_FAILURES=3)
class AdminLoginGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = Client()

    def _login(self, username, password):
        return self.c.post("/admin/login/", {"username": username, "password": password, "next": "/admin/"})

    def test_weak_staff_password_is_refused_even_if_correct(self):
        User.objects.create_user("weakmod", "w@x.com", "weakpass1", is_staff=True)
        r = self._login("weakmod", "weakpass1")
        self.assertEqual(r.status_code, 200)  # login page re-rendered with an error, no redirect
        self.assertIn(b"staff security policy", r.content)
        self.assertEqual(self.c.get("/admin/").status_code, 302)

    def test_strong_staff_password_logs_in(self):
        User.objects.create_user("strongmod", "sm@x.com", STRONG, is_staff=True, is_superuser=True)
        r = self._login("strongmod", STRONG)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/admin/").status_code, 200)

    def test_lockout_after_repeated_failures(self):
        User.objects.create_user("lockme", "l@x.com", STRONG, is_staff=True, is_superuser=True)
        for _ in range(3):
            self._login("lockme", "wrong-password")
        r = self._login("lockme", STRONG)  # correct password, but the account is locked
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Too many failed attempts", r.content)
        cache.clear()  # window expired
        self.assertEqual(self._login("lockme", STRONG).status_code, 302)


@override_settings(STORAGES=PLAIN_STATIC, ADMIN_REQUIRE_2FA=True)
class AdminTwoFactorTests(TestCase):
    def setUp(self):
        cache.clear()
        self.c = Client()
        self.mod = User.objects.create_user("mod2fa", "m2@x.com", STRONG, is_staff=True, is_superuser=True)

    def _confirmed_device(self):
        return TOTPDevice.objects.create(user=self.mod, name="authenticator", confirmed=True)

    def _post_login(self, device=None, token=""):
        data = {"username": "mod2fa", "password": STRONG, "next": "/admin/", "otp_token": token}
        if device is not None:
            data["otp_device"] = device.persistent_id
        return self.c.post("/admin/login/", data)

    def test_password_only_is_not_enough(self):
        device = self._confirmed_device()
        r = self._post_login(device, token="")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/admin/").status_code, 302)

    def test_wrong_code_is_rejected(self):
        device = self._confirmed_device()
        r = self._post_login(device, token="000000")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/admin/").status_code, 302)

    def test_correct_code_logs_in(self):
        device = self._confirmed_device()
        r = self._post_login(device, token=totp_now(device))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/admin/").status_code, 200)

    def test_staff_without_a_device_cannot_get_in(self):
        r = self._post_login(None, token="123456")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/admin/").status_code, 302)

    def test_session_login_without_otp_does_not_unlock_admin(self):
        self._confirmed_device()
        self.c.force_login(self.mod)  # password-only session (e.g. stolen cookie of a half-logged-in user)
        self.assertEqual(self.c.get("/admin/").status_code, 302)

    def test_backup_code_works_once(self):
        self._confirmed_device()
        out = io.StringIO()
        dev = StaticDevice.objects.create(user=self.mod, name="backup", confirmed=True)
        dev.token_set.create(token="abcd1234")
        data = {"username": "mod2fa", "password": STRONG, "next": "/admin/",
                "otp_device": dev.persistent_id, "otp_token": "abcd1234"}
        self.assertEqual(self.c.post("/admin/login/", data).status_code, 302)
        c2 = Client()
        self.assertEqual(c2.post("/admin/login/", data).status_code, 200)  # already consumed


class EnrolCommandTests(TestCase):
    def test_enrol_confirm_and_audit(self):
        User.objects.create_user("modA", "a@x.com", STRONG, is_staff=True)
        out = io.StringIO()
        call_command("setup_staff_2fa", "modA", stdout=out)
        self.assertIn("otpauth://totp/", out.getvalue())
        device = TOTPDevice.objects.get(user__username="modA")
        self.assertFalse(device.confirmed)

        with self.assertRaises(CommandError):
            call_command("setup_staff_2fa", "modA", "--confirm", "000000", stdout=io.StringIO())

        out = io.StringIO()
        call_command("setup_staff_2fa", "modA", "--confirm", totp_now(device), stdout=out)
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.assertEqual(StaticDevice.objects.get(user__username="modA").token_set.count(), 8)

    def test_non_staff_cannot_enrol(self):
        User.objects.create_user("student", "s@x.com", STRONG)
        with self.assertRaises(CommandError):
            call_command("setup_staff_2fa", "student", stdout=io.StringIO())

    def test_audit_strict_fails_until_everyone_has_2fa(self):
        User.objects.create_user("modB", "b@x.com", STRONG, is_staff=True)
        with self.assertRaises(CommandError):
            call_command("audit_staff_security", "--strict", stdout=io.StringIO())
        TOTPDevice.objects.create(user=User.objects.get(username="modB"), name="authenticator", confirmed=True)
        call_command("audit_staff_security", "--strict", stdout=io.StringIO())
