"""
P-G1 - coin CASH-OUT kill switch (settings.COIN_CASHOUT_ENABLED, default OFF). NOT RUN by the author (read-only task):
    python manage.py test user_profile.tests_pg1_cashout_flag

Pins: the flag helper fails closed; user POST /profile/coin-withdrawals/ is 403 {"code": "feature_disabled"} while OFF and
moves no coins / creates no row; staff "processing" / "success" are blocked while OFF but "reject" (a refund) still works;
withdrawal HISTORY and coin spending are untouched; wallet summary + public app config tell the app to hide the UI.
"""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from common.feature_flags import coin_cashout_enabled
from .models import CoinLedger, CoinWithdrawalRequest

User = get_user_model()
WITHDRAW_URL = "/profile/coin-withdrawals/"
SUMMARY_URL = "/profile/wallet/summary/"
PAYLOAD = {"coins": 100, "payout_method": "upi", "payout_details": {"upi_id": "me@ybl"}}


def make_user(name, coin=0, staff=False):
    user = User.objects.create_user(
        username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=datetime.date(1995, 1, 1),
    )
    if staff:
        user.is_staff = True
        user.save(update_fields=["is_staff"])
    if coin:
        CoinLedger.objects.record_transaction(
            user=user, transaction_type=CoinLedger.TransactionType.PURCHASE, amount=coin, reference=f"seed:{name}",
        )
        user.refresh_from_db()
    return user


def api_for(user):
    api = APIClient()
    api.force_authenticate(user)
    return api


class FlagHelperTests(SimpleTestCase):
    def test_only_a_clear_true_switches_it_on(self):
        with override_settings(COIN_CASHOUT_ENABLED=True):
            self.assertTrue(coin_cashout_enabled())
        with override_settings(COIN_CASHOUT_ENABLED="true"):
            self.assertTrue(coin_cashout_enabled())
        for off in (False, None, "", "false", "0", "no", "maybe", 1, 0, [], "enabled?"):
            with override_settings(COIN_CASHOUT_ENABLED=off):
                self.assertFalse(coin_cashout_enabled(), off)

    def test_missing_setting_is_off(self):
        from django.conf import settings

        with override_settings():
            del settings.COIN_CASHOUT_ENABLED
            self.assertFalse(coin_cashout_enabled())


@override_settings(COIN_CASHOUT_ENABLED=False)
class UserWithdrawalOffTests(TestCase):
    def setUp(self):
        self.user = make_user("wdoff", coin=500)
        self.api = api_for(self.user)

    def test_post_is_403_feature_disabled_and_nothing_moves(self):
        with mock.patch("user_profile.fraud.is_withdrawal_eligible", return_value=(True, "")) as eligible:
            res = self.api.post(WITHDRAW_URL, PAYLOAD, format="json")
        self.assertEqual(res.status_code, 403, res.content)
        body = res.json()
        self.assertFalse(body["status"])
        self.assertEqual(body["code"], "feature_disabled")
        self.assertEqual(body["feature"], "coin_cashout")
        eligible.assert_not_called()  # the switch is checked BEFORE the fraud rules
        self.assertFalse(CoinWithdrawalRequest.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 500)

    def test_even_a_bad_body_gets_the_same_403_not_a_400(self):
        res = self.api.post(WITHDRAW_URL, {"coins": "abc"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "feature_disabled")

    def test_anonymous_still_401_not_403(self):
        self.assertEqual(APIClient().post(WITHDRAW_URL, PAYLOAD, format="json").status_code, 401)

    def test_history_list_still_works(self):
        res = self.api.get(WITHDRAW_URL)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["status"])

    def test_spending_and_buying_coins_is_not_affected(self):
        CoinLedger.objects.record_transaction(
            user=self.user, transaction_type=CoinLedger.TransactionType.SPEND, amount=-50, reference="pg1:spend",
        )
        CoinLedger.objects.record_transaction(
            user=self.user, transaction_type=CoinLedger.TransactionType.PURCHASE, amount=20, reference="pg1:buy",
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 470)


@override_settings(COIN_CASHOUT_ENABLED=True)
class UserWithdrawalOnTests(TestCase):
    def test_post_works_when_switched_on(self):
        user = make_user("wdon", coin=500)
        with mock.patch("user_profile.fraud.is_withdrawal_eligible", return_value=(True, "")):
            res = api_for(user).post(WITHDRAW_URL, PAYLOAD, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        self.assertTrue(CoinWithdrawalRequest.objects.filter(user=user).exists())


class StaffActionTests(TestCase):
    def setUp(self):
        self.staff = make_user("pg1staff", staff=True)
        self.payee = make_user("pg1payee", coin=500)
        # Created through the manager (not the API): this is a request made BEFORE the switch was turned OFF.
        self.w = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.payee, coins=200, payout_method="upi", payout_details={"upi_id": "alice@bank"},
        )
        self.url = reverse("coin-withdrawal-admin-action", args=[self.w.pk])
        self.api = api_for(self.staff)

    def act(self, action, **extra):
        return self.api.post(self.url, {"action": action, **extra}, format="json")

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_processing_and_success_blocked_while_off(self):
        for action in ("processing", "success"):
            res = self.act(action)
            self.assertEqual(res.status_code, 403, (action, res.content))
            self.assertEqual(res.json()["code"], "feature_disabled")
        self.w.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.PENDING)

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_reject_still_refunds_while_off(self):
        self.payee.refresh_from_db()
        before = self.payee.coin
        res = self.act("reject", reason="cash-out switched off")
        self.assertEqual(res.status_code, 200, res.content)
        self.w.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.REJECTED)
        self.payee.refresh_from_db()
        self.assertEqual(self.payee.coin, before + 200)

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_bad_action_is_still_400_validation(self):
        self.assertEqual(self.act("nonsense").status_code, 400)

    @override_settings(COIN_CASHOUT_ENABLED=True)
    def test_processing_works_when_on(self):
        res = self.act("processing")
        self.assertEqual(res.status_code, 200, res.content)
        self.w.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.PROCESSING)

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_non_staff_still_403_by_permission(self):
        res = api_for(self.payee).post(self.url, {"action": "reject"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertNotEqual(res.json().get("code"), "feature_disabled")


class WalletSummaryTests(TestCase):
    def setUp(self):
        self.user = make_user("pg1sum", coin=300)
        self.api = api_for(self.user)

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_off_reports_flag_false_and_nothing_withdrawable(self):
        data = self.api.get(SUMMARY_URL).json()["data"]
        self.assertIs(data["cashout_enabled"], False)
        self.assertEqual(data["withdrawable"], 0)
        self.assertEqual(data["balance"], 300)  # balance itself is untouched

    @override_settings(COIN_CASHOUT_ENABLED=True)
    def test_on_reports_flag_true_and_real_withdrawable(self):
        data = self.api.get(SUMMARY_URL).json()["data"]
        self.assertIs(data["cashout_enabled"], True)
        self.assertGreater(data["withdrawable"], 0)  # 300 purchased coins


class AppConfigTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_public_config_says_off(self):
        res = APIClient().get(reverse("app-config"), {"platform": "android"})
        self.assertEqual(res.status_code, 200)
        self.assertIs(res.json()["coin_cashout_enabled"], False)

    @override_settings(COIN_CASHOUT_ENABLED=True)
    def test_public_config_says_on(self):
        cache.clear()
        res = APIClient().get(reverse("app-config"), {"platform": "android"})
        self.assertIs(res.json()["coin_cashout_enabled"], True)
