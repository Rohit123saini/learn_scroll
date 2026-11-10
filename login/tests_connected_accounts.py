# login/tests_connected_accounts.py
"""
S10 -- Connected accounts & devices (written, NOT yet run -- `python manage.py test login.tests_connected_accounts`).

Covers
  * overview shape + re-auth (password -> reauth_token, wrong password refused)
  * Google: link (ID token check mocked), second account cannot take the same Google, unlink, last-way-in guard
  * writes need re-auth (no proof -> 400 invalid_password), a token from another session is refused
  * phone remove (needs another way in)
  * contact change FULL flow (S10 final): send-code -> OTP to the NEW address -> confirm; re-auth first (a wrong password
    never burns the OTP), wrong / expired / other-address OTP, e-mail + phone add / replace, old e-mail told, audit rows,
    account with no password re-authing by code (REAUTH_OTP path)
  * QR sign-in: create (no auth) -> lookup (never shows the match code) -> wrong / right match code -> poll hands the
    session over ONCE -> "Linked apps" lists it -> sign it out; deny; 3 wrong codes kill the request; wrong secret;
    QR turned off
Google is never called for real: `connected_accounts.verify_google_id_token` is patched.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import connected_accounts as ca
from .audit import Event
from .models import AuthAuditLog, LinkedIdentity, OTPVerification, QRLoginRequest, User

PW = "Sup3r!Passw0rd"
BASE = "/login/auth"


def _bearer(access):
    return {"HTTP_AUTHORIZATION": f"Bearer {access}"}


def _google(sub="g-111", email="me@gmail.com", name="Me"):
    return {"sub": sub, "email": email, "name": name}


class ConnBase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(username="conn", email="conn@example.com", password=PW)

    def _login(self, username="conn", password=PW):
        res = self.client.post("/login/", {"username": username, "password": password}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["token"]["access"]

    def _post(self, access, path, body=None):
        return self.client.post(f"{BASE}/{path}", body or {}, format="json", **_bearer(access))

    def _reauth_token(self, access):
        res = self._post(access, "connections/reauth/", {"password": PW})
        self.assertEqual(res.status_code, 200, res.data)
        return res.data["reauth_token"]


# ============================================================================== overview + re-auth
class OverviewAndReauthTests(ConnBase):
    def test_overview_shape(self):
        access = self._login()
        res = self.client.get(f"{BASE}/connections/", **_bearer(access))
        self.assertEqual(res.status_code, 200, res.data)
        for key in ("google", "email", "phone", "has_password", "two_factor_enabled", "reauth", "linked_apps", "qr_login"):
            self.assertIn(key, res.data)
        self.assertTrue(res.data["has_password"])
        self.assertFalse(res.data["google"]["linked"])
        self.assertIn("password", res.data["reauth"]["required"])

    def test_overview_needs_login(self):
        self.assertEqual(self.client.get(f"{BASE}/connections/").status_code, 401)

    def test_reauth_with_password_gives_token(self):
        access = self._login()
        res = self._post(access, "connections/reauth/", {"password": PW})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["reauth_token"])
        self.assertGreater(res.data["expires_in"], 0)

    def test_reauth_wrong_password_refused(self):
        access = self._login()
        res = self._post(access, "connections/reauth/", {"password": "Wr0ng!Passw0rd"})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "invalid_password")

    def test_reauth_token_from_another_session_is_refused(self):
        a1 = self._login()
        a2 = self._login()
        token = self._reauth_token(a1)
        with mock.patch.object(ca, "verify_google_id_token", return_value=_google()):
            res = self._post(a2, "connections/google/link/", {"id_token": "x", "reauth_token": token})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "invalid_password")


# ============================================================================== Google
class GoogleLinkTests(ConnBase):
    def _link(self, access, **extra):
        with mock.patch.object(ca, "verify_google_id_token", return_value=_google(**extra)):
            return self._post(access, "connections/google/link/", {"id_token": "tok", "password": PW})

    def test_link_needs_reauth(self):
        access = self._login()
        with mock.patch.object(ca, "verify_google_id_token", return_value=_google()):
            res = self._post(access, "connections/google/link/", {"id_token": "tok"})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "invalid_password")
        self.assertFalse(LinkedIdentity.objects.filter(user=self.user).exists())

    def test_link_then_overview_shows_it(self):
        access = self._login()
        res = self._link(access)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertTrue(res.data["google"]["linked"])
        self.assertTrue(LinkedIdentity.objects.filter(user=self.user, subject="g-111", revoked_at__isnull=True).exists())

    def test_link_twice_with_same_google_is_fine(self):
        access = self._login()
        self.assertEqual(self._link(access).status_code, 200)
        self.assertEqual(self._link(access).status_code, 200)
        self.assertEqual(LinkedIdentity.objects.filter(user=self.user).count(), 1)

    def test_other_account_cannot_take_the_same_google(self):
        self._link(self._login())
        other = User.objects.create_user(username="other", email="other@example.com", password=PW)
        access2 = self._login("other")
        res = self._link(access2, email="third@gmail.com")
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "google_already_linked")
        self.assertFalse(LinkedIdentity.objects.filter(user=other).exists())

    def test_google_email_of_another_account_is_refused(self):
        User.objects.create_user(username="owner", email="taken@gmail.com", password=PW)
        res = self._link(self._login(), sub="g-222", email="taken@gmail.com")
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "google_email_other_account")

    def test_different_google_while_one_is_linked_is_refused(self):
        access = self._login()
        self._link(access)
        res = self._link(access, sub="g-999", email="else@gmail.com")
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "google_other_linked")

    def test_unlink_marks_revoked(self):
        access = self._login()
        self._link(access)
        res = self._post(access, "connections/google/unlink/", {"password": PW})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertFalse(res.data["google"]["linked"])
        row = LinkedIdentity.objects.get(user=self.user)
        self.assertIsNotNone(row.revoked_at)

    def test_unlink_without_link_is_404(self):
        res = self._post(self._login(), "connections/google/unlink/", {"password": PW})
        self.assertEqual(res.status_code, 404, res.data)
        self.assertEqual(res.data["code"], "google_not_linked")

    def test_unlink_refused_when_it_is_the_last_way_in(self):
        access = self._login()
        self._link(access)
        with mock.patch.object(ca, "has_other_way_in", return_value=False):
            res = self._post(access, "connections/google/unlink/", {"password": PW})
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "last_sign_in_method")
        self.assertIsNone(LinkedIdentity.objects.get(user=self.user).revoked_at)

    def test_bad_google_token_is_401(self):
        access = self._login()
        err = ca.ConnectionError_("google_token_invalid", "bad token", 401)
        with mock.patch.object(ca, "verify_google_id_token", side_effect=err):
            res = self._post(access, "connections/google/link/", {"id_token": "bad", "password": PW})
        self.assertEqual(res.status_code, 401, res.data)
        self.assertEqual(res.data["code"], "google_token_invalid")


# ============================================================================== phone
class PhoneRemoveTests(ConnBase):
    def setUp(self):
        super().setUp()
        self.user.phone = "+919876543210"
        self.user.save(update_fields=["phone"])

    def test_remove_phone_needs_reauth(self):
        res = self._post(self._login(), "connections/phone/remove/")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "invalid_password")
        self.user.refresh_from_db()
        self.assertTrue(self.user.phone)

    def test_remove_phone_with_password(self):
        res = self._post(self._login(), "connections/phone/remove/", {"password": PW})
        self.assertEqual(res.status_code, 200, res.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.phone)

    def test_remove_phone_refused_when_last_way_in(self):
        with mock.patch.object(ca, "has_other_way_in", return_value=False):
            res = self._post(self._login(), "connections/phone/remove/", {"password": PW})
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "last_sign_in_method")

    def test_remove_phone_when_none_is_404(self):
        self.user.phone = None
        self.user.save(update_fields=["phone"])
        res = self._post(self._login(), "connections/phone/remove/", {"password": PW})
        self.assertEqual(res.status_code, 404, res.data)
        self.assertEqual(res.data["code"], "phone_not_set")


# ============================================================================== contact change (input rules only)
class ContactSendCodeTests(ConnBase):
    def test_bad_kind_is_400(self):
        res = self._post(self._login(), "connections/contact/send-code/", {"kind": "fax", "value": "x"})
        self.assertEqual(res.status_code, 400, res.data)

    def test_same_email_is_refused(self):
        res = self._post(self._login(), "connections/contact/send-code/", {"kind": "email", "value": "conn@example.com"})
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "same_as_current")

    def test_email_of_another_account_is_refused(self):
        User.objects.create_user(username="x", email="x@example.com", password=PW)
        res = self._post(self._login(), "connections/contact/send-code/", {"kind": "email", "value": "x@example.com"})
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "email_exists")


# ============================================================================== QR sign-in
class QrSignInTests(ConnBase):
    def _create(self):
        res = self.client.post(f"{BASE}/qr/create/", {}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        return res.data

    def _poll(self, qr, secret=None):
        return self.client.post(
            f"{BASE}/qr/poll/", {"code": qr["code"], "secret": secret or qr["secret"]}, format="json"
        )

    def test_create_returns_everything_the_computer_needs(self):
        qr = self._create()
        for key in ("code", "secret", "match_code", "qr_payload", "expires_in", "poll_interval"):
            self.assertIn(key, qr)
        self.assertEqual(len(qr["match_code"]), 4)
        self.assertIn(qr["code"], qr["qr_payload"])
        self.assertNotIn(qr["secret"], qr["qr_payload"])

    def test_secret_and_match_code_are_not_stored_in_clear(self):
        qr = self._create()
        row = QRLoginRequest.objects.get()
        self.assertNotEqual(row.secret_hash, qr["secret"])
        self.assertNotEqual(row.code_hash, qr["code"])

    def test_pending_poll(self):
        qr = self._create()
        res = self._poll(qr)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["state"], "pending")
        self.assertNotIn("token", res.data)

    def test_poll_with_wrong_secret_is_404(self):
        qr = self._create()
        res = self._poll(qr, secret="not-the-secret")
        self.assertEqual(res.status_code, 404, res.data)
        self.assertEqual(res.data["code"], "qr_not_found")

    def test_lookup_never_shows_the_match_code(self):
        qr = self._create()
        access = self._login()
        res = self._post(access, "qr/lookup/", {"code": qr["code"]})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertNotIn("match_code", res.data)
        self.assertNotIn(qr["match_code"], str(res.data))

    def test_phone_approves_with_right_match_code_and_computer_gets_session_once(self):
        qr = self._create()
        phone = self._login()
        res = self._post(phone, "qr/answer/", {"code": qr["code"], "approve": True, "match_code": qr["match_code"]})
        self.assertEqual(res.status_code, 200, res.data)

        first = self._poll(qr)
        self.assertEqual(first.data["state"], "approved", first.data)
        self.assertTrue(first.data["token"]["access"])
        self.assertEqual(first.data["user"]["id"], self.user.id)

        again = self._poll(qr)
        self.assertEqual(again.data["state"], "consumed")
        self.assertNotIn("token", again.data)

    def test_new_session_shows_up_in_linked_apps_and_can_be_signed_out(self):
        qr = self._create()
        phone = self._login()
        self._post(phone, "qr/answer/", {"code": qr["code"], "approve": True, "match_code": qr["match_code"]})
        computer_access = self._poll(qr).data["token"]["access"]

        apps = self.client.get(f"{BASE}/connections/apps/", **_bearer(phone))
        self.assertEqual(apps.status_code, 200, apps.data)
        self.assertEqual(len(apps.data["apps"]), 1)
        app_id = apps.data["apps"][0]["id"]

        gone = self.client.delete(f"{BASE}/connections/apps/{app_id}/", **_bearer(phone))
        self.assertEqual(gone.status_code, 200, gone.data)
        self.assertEqual(self.client.get(f"{BASE}/connections/apps/", **_bearer(phone)).data["apps"], [])
        # the computer's session is dead now
        self.assertEqual(self.client.get(f"{BASE}/connections/", **_bearer(computer_access)).status_code, 401)

        again = self.client.delete(f"{BASE}/connections/apps/{app_id}/", **_bearer(phone))
        self.assertEqual(again.status_code, 404, again.data)
        self.assertEqual(again.data["code"], "linked_app_not_found")

    def test_wrong_match_code_counts_down_then_locks(self):
        qr = self._create()
        phone = self._login()
        wrong = "0000" if qr["match_code"] != "0000" else "1111"
        body = {"code": qr["code"], "approve": True, "match_code": wrong}

        r1 = self._post(phone, "qr/answer/", body)
        self.assertEqual((r1.status_code, r1.data["code"]), (400, "qr_match_invalid"))
        r2 = self._post(phone, "qr/answer/", body)
        self.assertEqual((r2.status_code, r2.data["code"]), (400, "qr_match_invalid"))
        r3 = self._post(phone, "qr/answer/", body)
        self.assertEqual((r3.status_code, r3.data["code"]), (429, "qr_match_locked"))

        # the right code no longer helps, and the computer is told it was denied
        late = self._post(phone, "qr/answer/", {"code": qr["code"], "approve": True, "match_code": qr["match_code"]})
        self.assertEqual(late.status_code, 409, late.data)
        self.assertEqual(self._poll(qr).data["state"], "denied")

    def test_decline_tells_the_computer(self):
        qr = self._create()
        phone = self._login()
        res = self._post(phone, "qr/answer/", {"code": qr["code"], "approve": False})
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(self._poll(qr).data["state"], "denied")

    def test_answer_needs_login(self):
        qr = self._create()
        res = self.client.post(
            f"{BASE}/qr/answer/", {"code": qr["code"], "approve": True, "match_code": qr["match_code"]}, format="json"
        )
        self.assertEqual(res.status_code, 401)

    def test_unknown_code_is_404(self):
        phone = self._login()
        res = self._post(phone, "qr/lookup/", {"code": "x" * 24})
        self.assertEqual(res.status_code, 404, res.data)
        self.assertEqual(res.data["code"], "qr_not_found")

    def test_expired_code(self):
        from datetime import timedelta

        from django.utils import timezone

        qr = self._create()
        QRLoginRequest.objects.update(expires_at=timezone.now() - timedelta(seconds=5))
        self.assertEqual(self._poll(qr).data["state"], "expired")
        res = self._post(self._login(), "qr/lookup/", {"code": qr["code"]})
        self.assertEqual((res.status_code, res.data["code"]), (410, "qr_expired"))

    def test_inactive_user_gets_no_session(self):
        qr = self._create()
        phone = self._login()
        self._post(phone, "qr/answer/", {"code": qr["code"], "approve": True, "match_code": qr["match_code"]})
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        res = self._poll(qr)
        self.assertEqual(res.data["state"], "denied")
        self.assertNotIn("token", res.data)

    @override_settings(QR_LOGIN_ENABLED=False)
    def test_qr_can_be_turned_off(self):
        res = self.client.post(f"{BASE}/qr/create/", {}, format="json")
        self.assertEqual(res.status_code, 403, res.data)
        self.assertEqual(res.data["code"], "qr_disabled")


# ============================================================================== contact change -- FULL flow (OTP)
def _code_from_outbox(to=None):
    """6-digit code from the newest mail (optionally the newest one sent to ``to``)."""
    msgs = [m for m in mail.outbox if to is None or to in m.to]
    body = msgs[-1].body
    return next(t for t in body.split() if t.rstrip(".").isdigit()).rstrip(".")


def _wrong(code):
    return "000000" if code != "000000" else "111111"


@override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
class ContactChangeFlowTests(ConnBase):
    NEW_EMAIL = "fresh@example.com"
    NEW_PHONE = "+919876543210"

    def setUp(self):
        super().setUp()
        mail.outbox = []
        # the paid carrier lookup is optional + fail-open; keep the tests off the network
        p = mock.patch("login.connected_views.phone_lookup_problem", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def _send(self, access, kind, value):
        return self._post(access, "connections/contact/send-code/", {"kind": kind, "value": value})

    def _confirm(self, access, kind, value, otp, **reauth):
        body = {"kind": kind, "value": value, "otp": otp, **reauth}
        return self._post(access, "connections/contact/confirm/", body)

    # ---------------------------------------------------------------- e-mail
    def test_email_change_full_flow(self):
        access = self._login()
        sent = self._send(access, "email", self.NEW_EMAIL)
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertTrue(sent.data["sent_to"].endswith("@example.com"))
        self.assertNotIn("fresh", sent.data["sent_to"])  # masked
        code = _code_from_outbox(to=self.NEW_EMAIL)  # the code goes to the NEW address, not the old one
        self.assertFalse([m for m in mail.outbox if "conn@example.com" in m.to])

        res = self._confirm(access, "email", self.NEW_EMAIL, code, password=PW)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["message"], "E-mail updated.")
        self.assertEqual(res.data["email"]["value"], self.NEW_EMAIL)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, self.NEW_EMAIL)

        # old address is told afterwards (and the mail does not show the new address in full)
        told = [m for m in mail.outbox if "conn@example.com" in m.to]
        self.assertEqual(len(told), 1)
        self.assertNotIn(self.NEW_EMAIL, told[0].body)
        # the code is spent
        self.assertFalse(OTPVerification.objects.filter(target=self.NEW_EMAIL).exists())
        # audit row
        row = AuthAuditLog.objects.filter(event=Event.CONTACT_CHANGED, user=self.user).get()
        self.assertEqual(row.identifier_kind, "email")
        self.assertTrue(row.metadata.get("replaced"))

    def test_email_change_marks_notification_email_verified(self):
        from core.models import NotificationPreference

        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        self.assertEqual(self._confirm(access, "email", self.NEW_EMAIL, code, password=PW).status_code, 200)
        pref = NotificationPreference.for_user(self.user)
        self.assertEqual(pref.email_verified_address, self.NEW_EMAIL)
        self.assertIsNotNone(pref.email_verified_at)

    def test_wrong_password_does_not_burn_the_otp(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        bad = self._confirm(access, "email", self.NEW_EMAIL, code, password="Wr0ng!Passw0rd")
        self.assertEqual(bad.status_code, 400, bad.data)
        self.assertEqual(bad.data["code"], "invalid_password")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "conn@example.com")
        # the e-mailed code is still there: same code + right password now works
        self.assertTrue(OTPVerification.objects.filter(target=self.NEW_EMAIL).exists())
        self.assertEqual(self._confirm(access, "email", self.NEW_EMAIL, code, password=PW).status_code, 200)

    def test_wrong_otp_reports_attempts_and_changes_nothing(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        res = self._confirm(access, "email", self.NEW_EMAIL, _wrong(code), password=PW)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "otp_invalid")
        self.assertEqual(res.data["attempts_left"], OTPVerification.MAX_ATTEMPTS - 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "conn@example.com")
        self.assertTrue(AuthAuditLog.objects.filter(event=Event.OTP_FAILED, user=self.user).exists())

    def test_too_many_wrong_otps_lock_the_code(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        last = None
        for _ in range(OTPVerification.MAX_ATTEMPTS + 1):
            last = self._confirm(access, "email", self.NEW_EMAIL, _wrong(code), password=PW)
        self.assertEqual(last.status_code, 429, last.data)
        self.assertEqual(last.data["code"], "otp_locked")
        # even the right code is dead now
        again = self._confirm(access, "email", self.NEW_EMAIL, code, password=PW)
        self.assertEqual(again.status_code, 400, again.data)
        self.assertEqual(again.data["code"], "otp_not_found")

    def test_expired_otp(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        OTPVerification.objects.filter(target=self.NEW_EMAIL).update(
            created_at=timezone.now() - timedelta(minutes=OTPVerification.EXPIRY_MINUTES + 1)
        )
        res = self._confirm(access, "email", self.NEW_EMAIL, code, password=PW)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "otp_expired")

    def test_otp_is_bound_to_the_address_it_was_sent_to(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        res = self._confirm(access, "email", "someone.else@example.com", code, password=PW)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "otp_not_found")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "conn@example.com")

    def test_confirm_without_otp_is_refused(self):
        access = self._login()
        res = self._confirm(access, "email", self.NEW_EMAIL, "", password=PW)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "otp_required")

    def test_code_cannot_be_replayed(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        self.assertEqual(self._confirm(access, "email", self.NEW_EMAIL, code, password=PW).status_code, 200)
        # change away again to a third address, then try the spent code for the second address
        self._send(access, "email", "third@example.com")
        res = self._confirm(access, "email", self.NEW_EMAIL, code, password=PW)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertIn(res.data["code"], ("otp_not_found", "same_as_current"))

    def test_reauth_token_replaces_password_on_confirm(self):
        access = self._login()
        token = self._reauth_token(access)
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        res = self._confirm(access, "email", self.NEW_EMAIL, code, reauth_token=token)
        self.assertEqual(res.status_code, 200, res.data)

    def test_email_taken_between_send_and_confirm_is_409(self):
        access = self._login()
        self._send(access, "email", self.NEW_EMAIL)
        code = _code_from_outbox(to=self.NEW_EMAIL)
        User.objects.create_user(username="sneaky", email=self.NEW_EMAIL, password=PW)
        res = self._confirm(access, "email", self.NEW_EMAIL, code, password=PW)
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "email_exists")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "conn@example.com")

    def test_bad_email_format_is_refused_at_send(self):
        res = self._send(self._login(), "email", "not-an-email")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "email_invalid_format")
        self.assertEqual(len(mail.outbox), 0)

    # ---------------------------------------------------------------- phone
    def _send_phone(self, access, value):
        """Send a phone code with the SMS provider mocked; returns (response, code)."""
        with mock.patch("login.otp_service.send_otp_sms") as sms:
            res = self._send(access, "phone", value)
        code = sms.call_args[0][1] if sms.called else None
        return res, code

    def test_phone_add_full_flow(self):
        access = self._login()
        res, code = self._send_phone(access, self.NEW_PHONE)
        self.assertEqual(res.status_code, 200, res.data)
        self.assertIsNotNone(code)
        ok = self._confirm(access, "phone", self.NEW_PHONE, code, password=PW)
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data["message"], "Phone number added.")
        self.assertTrue(ok.data["phone"]["verified"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, self.NEW_PHONE)
        self.assertTrue(self.user.phone_verified)
        self.assertTrue(AuthAuditLog.objects.filter(event=Event.PHONE_VERIFIED, user=self.user).exists())
        row = AuthAuditLog.objects.filter(event=Event.CONTACT_CHANGED, user=self.user).get()
        self.assertEqual(row.identifier_kind, "phone")
        self.assertFalse(row.metadata.get("replaced"))

    def test_phone_replace_full_flow(self):
        self.user.phone, self.user.phone_verified = "+919000000001", True
        self.user.save(update_fields=["phone", "phone_verified"])
        access = self._login()
        res, code = self._send_phone(access, self.NEW_PHONE)
        self.assertEqual(res.status_code, 200, res.data)
        ok = self._confirm(access, "phone", self.NEW_PHONE, code, password=PW)
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(ok.data["message"], "Phone number updated.")
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, self.NEW_PHONE)

    def test_phone_wrong_otp_changes_nothing(self):
        access = self._login()
        _res, code = self._send_phone(access, self.NEW_PHONE)
        bad = self._confirm(access, "phone", self.NEW_PHONE, _wrong(code), password=PW)
        self.assertEqual(bad.status_code, 400, bad.data)
        self.assertEqual(bad.data["code"], "otp_invalid")
        self.user.refresh_from_db()
        self.assertFalse(self.user.phone)
        self.assertFalse(self.user.phone_verified)

    def test_phone_of_another_account_is_refused_at_send(self):
        other = User.objects.create_user(username="ph", email="ph@example.com", password=PW)
        other.phone = self.NEW_PHONE
        other.save(update_fields=["phone"])
        with mock.patch("login.otp_service.send_otp_sms") as sms:
            res = self._send(self._login(), "phone", self.NEW_PHONE)
        self.assertEqual(res.status_code, 409, res.data)
        self.assertEqual(res.data["code"], "phone_exists")
        sms.assert_not_called()

    def test_virtual_phone_is_refused_by_the_carrier_lookup(self):
        with mock.patch("login.connected_views.phone_lookup_problem", return_value=("disposable_phone", "Virtual numbers are not allowed.")), \
                mock.patch("login.otp_service.send_otp_sms") as sms:
            res = self._send(self._login(), "phone", self.NEW_PHONE)
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "disposable_phone")
        sms.assert_not_called()

    def test_bad_phone_format_is_refused_at_send(self):
        res = self._send(self._login(), "phone", "12345")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "phone_invalid_format")

    def test_sms_failure_is_503_and_refunds_the_send(self):
        from .otp_service import OTPDeliveryError

        access = self._login()
        with mock.patch("login.connected_views.deliver_code", side_effect=OTPDeliveryError("down")):
            res = self._send(access, "phone", self.NEW_PHONE)
        self.assertEqual(res.status_code, 503, res.data)
        self.assertEqual(res.data["code"], "otp_delivery_failed")
        # not locked out by the failed attempt: the next send goes through
        res2, code = self._send_phone(access, self.NEW_PHONE)
        self.assertEqual(res2.status_code, 200, res2.data)

    def test_contact_endpoints_need_login(self):
        for path in ("connections/contact/send-code/", "connections/contact/confirm/"):
            res = self.client.post(f"{BASE}/{path}", {"kind": "email", "value": self.NEW_EMAIL}, format="json")
            self.assertEqual(res.status_code, 401, path)


# ============================================================================== re-auth by CODE (account with no password)
@override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
class OtpReauthContactTests(TestCase):
    """Google-only / OTP-only accounts have no password: they prove themselves with a code to their CURRENT e-mail."""

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.client = APIClient()
        self.user = User.objects.create_user(username="otponly", email="otp@example.com", password=None)
        self.assertFalse(self.user.has_usable_password())
        self.client.force_authenticate(self.user)

    def test_requirements_say_otp(self):
        res = self.client.get(f"{BASE}/connections/")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["reauth"]["required"], ["otp"])
        self.assertTrue(res.data["reauth"]["otp_target_masked"])

    def test_send_code_goes_to_current_email_then_token_then_change(self):
        sent = self.client.post(f"{BASE}/connections/reauth/send-code/", {}, format="json")
        self.assertEqual(sent.status_code, 200, sent.data)
        self.assertTrue([m for m in mail.outbox if "otp@example.com" in m.to])
        reauth_code = _code_from_outbox(to="otp@example.com")

        # wrong code first
        bad = self.client.post(f"{BASE}/connections/reauth/", {"reauth_code": _wrong(reauth_code)}, format="json")
        self.assertEqual(bad.status_code, 400, bad.data)
        self.assertEqual(bad.data["code"], "otp_invalid")

        ok = self.client.post(f"{BASE}/connections/reauth/", {"reauth_code": reauth_code}, format="json")
        self.assertEqual(ok.status_code, 200, ok.data)
        token = ok.data["reauth_token"]

        # now the contact change itself (code to the NEW address)
        new = "moved@example.com"
        self.assertEqual(
            self.client.post(f"{BASE}/connections/contact/send-code/", {"kind": "email", "value": new}, format="json").status_code,
            200,
        )
        new_code = _code_from_outbox(to=new)
        res = self.client.post(
            f"{BASE}/connections/contact/confirm/",
            {"kind": "email", "value": new, "otp": new_code, "reauth_token": token},
            format="json",
        )
        self.assertEqual(res.status_code, 200, res.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, new)

    def test_confirm_without_any_reauth_is_refused(self):
        new = "moved@example.com"
        self.client.post(f"{BASE}/connections/contact/send-code/", {"kind": "email", "value": new}, format="json")
        new_code = _code_from_outbox(to=new)
        res = self.client.post(
            f"{BASE}/connections/contact/confirm/", {"kind": "email", "value": new, "otp": new_code}, format="json"
        )
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "reauth_code_required")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "otp@example.com")

    def test_reauth_send_code_refused_for_password_accounts(self):
        pw_user = User.objects.create_user(username="haspw", email="haspw@example.com", password=PW)
        self.client.force_authenticate(pw_user)
        res = self.client.post(f"{BASE}/connections/reauth/send-code/", {}, format="json")
        self.assertEqual(res.status_code, 400, res.data)
        self.assertEqual(res.data["code"], "reauth_not_otp")
