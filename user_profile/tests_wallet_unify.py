"""T3.10 tests: merged wallet history + reconcile_wallets."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User
from tuitionclass.models import CoinTransaction

from .models import CoinLedger
from .wallet_unify import wallet_history

T = CoinLedger.TransactionType


def _user(name="wally", coin=0):
    u = User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")
    User.objects.filter(pk=u.pk).update(coin=coin)
    u.refresh_from_db()
    return u


def _ledger(user, amount, balance, ago_min, t=T.EARN, **kw):
    e = CoinLedger.objects.create(user=user, transaction_type=t, amount=amount, balance_after=balance, **kw)
    CoinLedger.objects.filter(pk=e.pk).update(created_at=timezone.now() - timedelta(minutes=ago_min))
    return e


def _legacy(user, kind, reason, amount, balance, ago_min, ref=""):
    x = CoinTransaction.objects.create(user=user, txn_type=kind, reason=reason, amount=amount, balance_after=balance, reference_id=ref)
    CoinTransaction.objects.filter(pk=x.pk).update(created_at=timezone.now() - timedelta(minutes=ago_min))
    return x


class WalletHistoryTests(TestCase):
    def test_merges_newest_first_with_signed_amounts(self):
        u = _user(coin=70)
        _ledger(u, 100, 100, ago_min=30, t=T.PURCHASE)
        _legacy(u, "debit", "pass_purchase", 30, 70, ago_min=10)
        total, rows = wallet_history(u)
        self.assertEqual(total, 2)
        self.assertEqual([r["source"] for r in rows], ["legacy", "ledger"])
        self.assertEqual([r["amount"] for r in rows], [-30, 100])
        self.assertLess(rows[0]["id"], 0)
        self.assertEqual(rows[0]["transaction_type"], "spend")
        self.assertEqual(rows[0]["metadata"]["legacy_reason"], "pass_purchase")

    def test_legacy_rows_already_migrated_are_not_duplicated(self):
        u = _user(coin=100)
        old = _legacy(u, "credit", "topup", 100, 100, ago_min=60, ref="coinpurchase:1")
        _ledger(u, 100, 100, ago_min=60, t=T.PURCHASE, metadata={"source_coin_transaction_id": old.pk})
        total, rows = wallet_history(u)
        self.assertEqual((total, len(rows)), (1, 1))
        self.assertEqual(rows[0]["source"], "ledger")

    def test_unmigrated_legacy_topup_still_shown(self):
        u = _user(coin=50)
        _legacy(u, "credit", "topup", 50, 50, ago_min=5, ref="coinpurchase:9")
        _, rows = wallet_history(u)
        self.assertEqual(rows[0]["transaction_type"], "purchase")

    def test_pagination_across_both_sources(self):
        u = _user(coin=0)
        for i in range(5):
            _ledger(u, 1, i + 1, ago_min=100 - i * 10)          # minutes ago: 100,90,80,70,60
            _legacy(u, "credit", "class_earning", 1, i + 1, ago_min=95 - i * 10)  # 95,85,75,65,55
        _, page1 = wallet_history(u, limit=4, offset=0)
        _, page2 = wallet_history(u, limit=4, offset=4)
        _, page3 = wallet_history(u, limit=4, offset=8)
        ids = [r["id"] for r in page1 + page2 + page3]
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)
        stamps = [r["created_at"] for r in page1 + page2 + page3]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_endpoint_requires_auth_and_only_returns_own_rows(self):
        a, b = _user("a1", 10), _user("b1", 10)
        _ledger(a, 10, 10, ago_min=1)
        _ledger(b, 10, 10, ago_min=1)
        c = APIClient()
        self.assertEqual(c.get("/profile/wallet/history/").status_code, 401)
        c.force_authenticate(a)
        r = c.get("/profile/wallet/history/?limit=10")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["count"], 1)
        self.assertEqual(c.get("/profile/wallet/history/?limit=abc&offset=-5").status_code, 200)


class ReconcileWalletsTests(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command("reconcile_wallets", *args, stdout=out)
        return out.getvalue()

    def test_clean_wallet_reports_no_drift(self):
        u = _user(coin=70)
        _ledger(u, 100, 100, ago_min=30, t=T.PURCHASE)
        _legacy(u, "debit", "pass_purchase", 30, 70, ago_min=10)
        self.assertIn("drift: 0", self.run_cmd("--user-id", str(u.pk)))

    def test_detects_drift_and_fails_when_asked(self):
        u = _user(coin=999)
        _ledger(u, 100, 100, ago_min=5, t=T.PURCHASE)
        out = self.run_cmd("--user-id", str(u.pk))
        self.assertIn("drift: 1", out)
        self.assertIn("User.coin=999", out)
        with self.assertRaises(CommandError):
            self.run_cmd("--user-id", str(u.pk), "--fail-on-drift")

    def test_unexplained_balance_is_reported_not_counted_as_drift(self):
        u = _user(coin=40)
        out = self.run_cmd("--user-id", str(u.pk))
        self.assertIn("unexplained balance: 1", out)
        self.assertIn("drift: 0", out)

    def test_newest_row_wins_across_ledgers(self):
        u = _user(coin=100)
        _legacy(u, "credit", "class_earning", 80, 80, ago_min=50)   # older, stale balance
        _ledger(u, 20, 100, ago_min=5)                               # newest -> authoritative
        self.assertIn("drift: 0", self.run_cmd("--user-id", str(u.pk)))
