# login/tests.py
"""
Phone number is OPTIONAL on signup.

Covers, end to end through the real URLs (send-otp -> verify-otp -> signup ->
login), that an account can be created with no phone at all, that several
phone-less accounts can coexist (the unique constraint on User.phone only ever
sees NULL, never ''), and that a phone which *is* supplied is still fully
validated (format + uniqueness).
"""
from django.core import mail
from django.test import TestCase
from rest_framework.test import APIClient

from .models import OTPVerification, User
from .serializers import CompleteProfileSerializer, SignupSerializer

PASSWORD = "Str0ng!Pass"


def _verified_otp(target):
    """A verified, unexpired OTP row — what VerifyOTPView leaves behind."""
    return OTPVerification.objects.update_or_create(
        target=target, defaults={"is_verified": True}
    )[0]


def _payload(**overrides):
    data = {
        "username": "alice",
        "email": "alice@example.com",
        "first_name": "Alice",
        "last_name": "Doe",
        "password": PASSWORD,
        "confirm_password": PASSWORD,
    }
    data.update(overrides)
    # `phone=_OMIT` drops the key entirely (client that doesn't send it).
    return {k: v for k, v in data.items() if v is not _OMIT}


_OMIT = object()


class SignupPhoneOptionalTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _signup(self, **overrides):
        email = overrides.get("email", "alice@example.com")
        _verified_otp(email)
        return self.client.post("/login/signup/", _payload(**overrides), format="json")

    # ---- no phone ---------------------------------------------------------
    def test_signup_without_phone_key(self):
        res = self._signup()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(res.data["user"]["phone"])
        self.assertIsNone(User.objects.get(username="alice").phone)

    def test_signup_with_empty_string_phone(self):
        res = self._signup(phone="")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(User.objects.get(username="alice").phone)

    def test_signup_with_whitespace_only_phone(self):
        res = self._signup(phone="   ")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(User.objects.get(username="alice").phone)

    def test_signup_with_null_phone(self):
        res = self._signup(phone=None)
        self.assertEqual(res.status_code, 201, res.data)
        self.assertIsNone(User.objects.get(username="alice").phone)

    def test_many_phoneless_accounts_do_not_collide_on_unique(self):
        for i, phone in enumerate([_OMIT, "", None, "  "]):
            res = self._signup(
                username=f"user{i}", email=f"user{i}@example.com", phone=phone
            )
            self.assertEqual(res.status_code, 201, (i, res.data))
        self.assertEqual(User.objects.filter(phone__isnull=True).count(), 4)

    # ---- phone still validated when given ---------------------------------
    def test_signup_with_valid_phone_saves_it(self):
        res = self._signup(phone="919876543210")
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(User.objects.get(username="alice").phone, "919876543210")

    def test_signup_with_bad_phone_format_rejected(self):
        res = self._signup(phone="123")
        self.assertEqual(res.status_code, 400)
        self.assertIn("phone", res.data)
        self.assertFalse(User.objects.filter(username="alice").exists())

    def test_signup_with_duplicate_phone_rejected(self):
        self.assertEqual(self._signup(phone="919876543210").status_code, 201)
        res = self._signup(
            username="bob", email="bob@example.com", phone="919876543210"
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("phone", res.data)

    # ---- OTP gate is unchanged -------------------------------------------
    def test_signup_still_requires_verified_otp_without_phone(self):
        res = self.client.post("/login/signup/", _payload(), format="json")
        self.assertEqual(res.status_code, 400)

    def test_phone_only_otp_still_authorises_signup(self):
        """Verified via phone OTP (not email) is still a valid signup path."""
        _verified_otp("919876543210")
        res = self.client.post(
            "/login/signup/", _payload(phone="919876543210"), format="json"
        )
        self.assertEqual(res.status_code, 201, res.data)

    def test_omitting_phone_does_not_let_a_null_otp_target_authorise(self):
        """No phone => must not match OTP rows by a NULL/blank target."""
        _verified_otp("someone-else@example.com")
        res = self.client.post("/login/signup/", _payload(), format="json")
        self.assertEqual(res.status_code, 400)


class FullFlowWithoutPhoneTests(TestCase):
    """send-otp -> verify-otp -> signup -> login, exactly as the app does it."""

    def test_email_otp_signup_then_login_without_phone(self):
        c = APIClient()
        email = "carol@example.com"

        r = c.post("/login/auth/send-otp/", {"email_or_phone": email}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        code = next(t for t in mail.outbox[-1].body.split() if t.rstrip(".").isdigit())
        code = code.rstrip(".")

        r = c.post(
            "/login/auth/verify-otp/",
            {"email_or_phone": email, "otp": code},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(r.data["user_exists"])

        # Same body the Flutter app now sends when the phone box is empty:
        # no "phone" key at all.
        r = c.post(
            "/login/signup/",
            _payload(username="carol", email=email),
            format="json",
        )
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIn("access", r.data["token"])

        r = c.post(
            "/login/",
            {"username": "carol", "password": PASSWORD},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["status"])
        self.assertIn("access", r.data["token"])

        # email login works too
        r = c.post(
            "/login/", {"username": email, "password": PASSWORD}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.data)


class CompleteProfileStillRequiresPhoneTests(TestCase):
    """/complete-profile/ exists *to add* a phone, so it stays strict."""

    def test_serializer_rejects_blank_phone(self):
        self.assertFalse(CompleteProfileSerializer(data={"phone": ""}).is_valid())
        self.assertFalse(CompleteProfileSerializer(data={}).is_valid())

    def test_add_phone_later_for_phoneless_user(self):
        user = User.objects.create_user(
            username="dave", email="dave@example.com", password=PASSWORD
        )
        self.assertIsNone(user.phone)
        c = APIClient()
        c.force_authenticate(user)
        r = c.post(
            "/login/auth/complete-profile/", {"phone": "919876543210"}, format="json"
        )
        self.assertEqual(r.status_code, 200, r.data)
        user.refresh_from_db()
        self.assertEqual(user.phone, "919876543210")


class SignupSerializerUnitTests(TestCase):
    def test_phone_field_flags(self):
        f = SignupSerializer().fields["phone"]
        self.assertFalse(f.required)
        self.assertTrue(f.allow_blank)
        self.assertTrue(f.allow_null)


class ModelPhoneNormalisationTests(TestCase):
    """models.py behaviour we must NOT break: '' is stored as NULL."""

    def test_blank_phone_saved_as_null(self):
        a = User.objects.create_user(username="a", email="a@x.com", phone="")
        b = User.objects.create_user(username="b", email="b@x.com", phone="")
        self.assertIsNone(a.phone)
        self.assertIsNone(b.phone)
