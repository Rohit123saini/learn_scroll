"""T0.8 tests.   python manage.py test login.tests_account_deletion"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from login.account_deletion import blocking_reasons, delete_account, is_deleted
from login.models import AuthToken

User = get_user_model()
URL = "/login/auth/delete-account/"   # adjust the prefix if login.urls is mounted elsewhere


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=0)  # S5: these are the old "erase at once" tests; grace is in tests_data_rights.py
class DeleteAccountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw12345!", email="a@x.com", first_name="Alice", bio="hi")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_requires_confirm_word(self):
        r = self.api.post(URL, {"password": "pw12345!"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_active)

    def test_wrong_password_rejected(self):
        r = self.api.post(URL, {"password": "nope", "confirm": "DELETE"}, format="json")
        self.assertEqual(r.status_code, 403)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_active)

    def test_login_required(self):
        self.assertIn(APIClient().post(URL, {"confirm": "DELETE"}, format="json").status_code, (401, 403))

    def test_delete_anonymises_and_deactivates(self):
        r = self.api.post(URL, {"password": "pw12345!", "confirm": "delete"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        u = User.objects.get(pk=self.user.pk)          # row kept (FK-linked financial records survive)
        self.assertTrue(is_deleted(u))
        self.assertEqual((u.username, u.email, u.first_name, u.bio), (f"deleted_{u.pk}", None, "", ""))
        self.assertFalse(u.has_usable_password())
        self.assertFalse(User.objects.filter(email="a@x.com").exists())

    def test_sessions_are_revoked_and_old_username_reusable(self):
        AuthToken.objects.create(user=self.user, jti="j1", last_used_at=self.user.date_joined)
        delete_account(self.user)
        self.assertEqual(AuthToken.objects.filter(user_id=self.user.pk).count(), 0)
        User.objects.create_user(username="alice", password="x")  # username freed

    def test_idempotent(self):
        delete_account(self.user)
        self.assertTrue(delete_account(User.objects.get(pk=self.user.pk))["already_deleted"])

    def test_google_only_account_needs_only_confirm(self):
        g = User.objects.create_user(username="goog", password=None)
        g.set_unusable_password(); g.save()
        c = APIClient(); c.force_authenticate(g)
        self.assertEqual(c.post(URL, {"confirm": "DELETE"}, format="json").status_code, 200)

    def test_preflight_reports_deletable_when_nothing_blocks(self):
        self.assertEqual(blocking_reasons(self.user), [])
        r = self.api.get(URL).json()
        self.assertTrue(r["can_delete"])
        self.assertTrue(r["requires_password"])

    def test_pending_withdrawal_blocks_deletion(self):
        from user_profile.models import CoinWithdrawalRequest
        self.user.coin = 500; self.user.save()
        CoinWithdrawalRequest.objects.create(user=self.user, coins=500, amount_inr="500.00", status=CoinWithdrawalRequest.Status.PENDING)
        r = self.api.post(URL, {"password": "pw12345!", "confirm": "DELETE"}, format="json")
        self.assertEqual(r.status_code, 409)
        self.user.refresh_from_db(); self.assertTrue(self.user.is_active)

    def test_legal_pages_are_public(self):
        c = APIClient()
        for path in ("/legal/privacy/", "/legal/terms/", "/legal/delete-account/"):
            r = c.get(path)
            self.assertEqual(r.status_code, 200, path)
        self.assertIn(b"Settings", c.get("/legal/delete-account/").content)
