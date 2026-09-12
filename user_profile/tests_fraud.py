# user_profile/tests_fraud.py
"""
TASK 5 — tests for the fraud/anti-abuse layer (fraud.py +
CoinLedgerManager.record_transaction()'s hooks into it).

NOTE: this file wasn't placed in an existing `user_profile/tests/`
package because none was part of this upload — if this app already
has one, move this module in as `tests/test_fraud.py` instead of
leaving it as a top-level sibling of models.py/views.py, and drop this
note.

NOTE on user creation: the exact required fields for
`settings.AUTH_USER_MODEL` (`login.User`, per models.py's comments)
weren't part of this upload either, so `_make_user()` below only sets
`username` + `password` via `create_user`. If that model requires more
than that (e.g. a mandatory phone/email field with no default), adjust
`_make_user()` accordingly — the assertions themselves don't depend on
anything about the user beyond its `coin` field and its pk.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import fraud
from .models import CoinLedger

User = get_user_model()


def _make_user(username):
    return User.objects.create_user(username=username, password="testpass123")


class WithdrawalEligibilityTests(TestCase):
    def test_earn_only_balance_not_withdrawable(self):
        """
        A user whose entire balance came from EARN/CAMPUS_REWARD has
        zero withdrawal-eligible balance, and a withdrawal attempt gets
        a clean rejection with no balance change.
        """
        user = _make_user("earn_only_user")

        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=150,
            reference="earn-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.CAMPUS_REWARD,
            amount=50,
            reference="campus-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 200)

        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 0)

        is_eligible, reason = fraud.is_withdrawal_eligible(user, coins=50)
        self.assertFalse(is_eligible)
        self.assertTrue(reason)  # a human-readable rejection message

        # Balance must be untouched — the check ran before any debit.
        user.refresh_from_db()
        self.assertEqual(user.coin, 200)

        # Note: `CoinWithdrawalRequestManager.request_withdrawal()` on
        # its own does NOT enforce eligibility (it only checks total
        # balance, which 200 covers) — it's `CoinWithdrawalRequestView`
        # calling `fraud.is_withdrawal_eligible()` first that provides
        # the guarantee this test is actually checking. That's a view-
        # layer responsibility, so it isn't re-asserted here.

    def test_mixed_balance_only_purchased_portion_withdrawable(self):
        """
        A balance made up of both earned and purchased coins is only
        withdrawal-eligible up to the purchased/gifted portion — not
        the full balance.
        """
        user = _make_user("mixed_balance_user")

        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=100,
            reference="earn-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.PURCHASE,
            amount=200,
            reference="purchase-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 300)

        # Only the 200 purchased coins are eligible, not the full 300.
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 200)

        ok_small, _ = fraud.is_withdrawal_eligible(user, coins=150)
        self.assertTrue(ok_small)

        ok_full_balance, reason = fraud.is_withdrawal_eligible(user, coins=300)
        self.assertFalse(ok_full_balance)
        self.assertIn("200", reason)

        ok_exact, _ = fraud.is_withdrawal_eligible(user, coins=200)
        self.assertTrue(ok_exact)

    def test_gift_received_is_withdrawal_eligible(self):
        """Gifted coins are eligible the same way purchased coins are."""
        user = _make_user("gift_user")
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.GIFT_RECEIVED,
            amount=75,
            reference="gift-1",
        )
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 75)

    def test_spend_overflow_eats_into_eligible_balance(self):
        """
        Spending more than the non-eligible (earned) balance should
        reduce the eligible (purchased) balance by the overflow amount
        — not leave it untouched.
        """
        user = _make_user("overflow_user")
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.PURCHASE,
            amount=100,
            reference="purchase-1",
        )
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.EARN,
            amount=50,
            reference="earn-1",
        )
        # Spend 80: consumes the 50 earned coins, then 30 more must come
        # out of the 100 purchased coins.
        CoinLedger.objects.record_transaction(
            user=user,
            transaction_type=CoinLedger.TransactionType.SPEND,
            amount=-80,
            reference="spend-1",
        )
        user.refresh_from_db()
        self.assertEqual(user.coin, 70)
        self.assertEqual(fraud.get_withdrawal_eligible_balance(user), 70)


class EarnRateLimitTests(TestCase):
    def test_earn_rate_limit_blocks_burst(self):
        """
        Once a user crosses the earn-rate limit, a further EARN credit
        is rejected with EarnRateLimitExceeded — and the caller (e.g.
        campus tasks, referral bonus) can catch that specifically.
        """
        user = _make_user("burst_farmer")

        # Patch the limit low so this test is fast and deterministic
        # rather than depending on fraud.py's production defaults.
        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 3), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 10_000):
            for i in range(3):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=10,
                    reference=f"earn-{i}",
                )

            with self.assertRaises(fraud.EarnRateLimitExceeded):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=10,
                    reference="earn-over-limit",
                )

        # The rejected transaction must not have moved the balance.
        user.refresh_from_db()
        self.assertEqual(user.coin, 30)

    def test_earn_rate_limit_by_total_coins(self):
        """The coin-total cap trips independently of the count cap."""
        user = _make_user("big_earn_farmer")

        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 100), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 50):
            CoinLedger.objects.record_transaction(
                user=user,
                transaction_type=CoinLedger.TransactionType.EARN,
                amount=50,
                reference="earn-1",
            )
            with self.assertRaises(fraud.EarnRateLimitExceeded):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.EARN,
                    amount=1,
                    reference="earn-2",
                )

    def test_earn_rate_limit_does_not_apply_to_purchase(self):
        """
        PURCHASE (and every non-EARN/CAMPUS_REWARD type) is never
        rate-limited, even well past the earn caps.
        """
        user = _make_user("frequent_buyer")

        with patch.object(fraud, "EARN_RATE_LIMIT_MAX_TRANSACTIONS", 1), \
             patch.object(fraud, "EARN_RATE_LIMIT_MAX_COINS", 1):
            for i in range(5):
                CoinLedger.objects.record_transaction(
                    user=user,
                    transaction_type=CoinLedger.TransactionType.PURCHASE,
                    amount=100,
                    reference=f"purchase-{i}",
                )

        user.refresh_from_db()
        self.assertEqual(user.coin, 500)