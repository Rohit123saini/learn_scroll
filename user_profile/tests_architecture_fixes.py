# user_profile/tests_architecture_fixes.py
#
# Regression tests for the architecture / business-logic audit items:
#   12 gateway_reference collision    13 withdrawal accounting
#   15 INR rate from settings         16 admin-action serializer
# (10 restrict, 11 admin registration and 14 manual top-up are covered in
# tests_issue_fixes.py.)
import hashlib
import hmac
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from . import fraud
from .models import (
    AmbiguousGatewayReference,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    WithdrawalNotEligible,
)

User = get_user_model()
TT = CoinLedger.TransactionType


def mk(name, **kw):
    return User.objects.create_user(username=name, password="pass12345", **kw)


def ledger(user, ttype, amount, ref=None):
    return CoinLedger.objects.record_transaction(
        user=user, transaction_type=ttype, amount=amount,
        reference=ref or f"{ttype}-{amount}-{CoinLedger.objects.count()}",
    )


WEBHOOK = dict(
    PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": "rzp-secret", "stripe": "stripe-secret"},
    PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={
        "razorpay": "X-Webhook-Signature", "stripe": "X-Webhook-Signature",
    },
)


# ------------------------------------------------------------------- #12
class GatewayReferenceCollisionTests(APITestCase):
    def setUp(self):
        self.u1, self.u2 = mk("u1"), mk("u2")

    def start(self, user, gateway, ref="SAME-ID"):
        return CoinPurchaseRequest.objects.start_purchase(
            user=user, gateway_reference=ref, amount="10.00", coins=10, gateway=gateway,
        )

    def buy_api(self, user, gateway, ref="SAME-ID"):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse("buy-coin"),
            {"gateway_reference": ref, "amount": "10.00", "coins": 10, "gateway": gateway},
            format="json",
        )

    def test_same_reference_on_two_gateways_is_allowed(self):
        self.assertEqual(self.buy_api(self.u1, "razorpay").status_code, 201)
        self.assertEqual(self.buy_api(self.u2, "stripe").status_code, 201)
        self.assertEqual(CoinPurchaseRequest.objects.filter(gateway_reference="SAME-ID").count(), 2)

    def test_same_pair_for_another_user_is_still_409(self):
        self.assertEqual(self.buy_api(self.u1, "razorpay").status_code, 201)
        self.assertEqual(self.buy_api(self.u2, "razorpay").status_code, 409)

    def test_same_pair_for_same_user_is_idempotent_200(self):
        self.assertEqual(self.buy_api(self.u1, "razorpay").status_code, 201)
        self.assertEqual(self.buy_api(self.u1, "razorpay").status_code, 200)
        self.assertEqual(CoinPurchaseRequest.objects.count(), 1)

    def test_gateway_is_normalised_so_case_cannot_split_identity(self):
        self.assertEqual(self.buy_api(self.u1, "Razorpay ").status_code, 201)
        self.assertEqual(CoinPurchaseRequest.objects.get().gateway, "razorpay")
        self.assertEqual(self.buy_api(self.u2, "RAZORPAY").status_code, 409)

    def test_bad_gateway_string_is_rejected(self):
        self.assertEqual(self.buy_api(self.u1, "raz orpay!").status_code, 400)

    def test_database_enforces_the_pair(self):
        self.start(self.u1, "razorpay")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CoinPurchaseRequest.objects.create(
                user=self.u2, gateway="razorpay", gateway_reference="SAME-ID", amount="1.00", coins=1,
            )

    def test_manager_refuses_to_guess_between_gateways(self):
        self.start(self.u1, "razorpay")
        self.start(self.u2, "stripe")
        with self.assertRaises(AmbiguousGatewayReference):
            CoinPurchaseRequest.objects.confirm_success(gateway_reference="SAME-ID")
        self.assertEqual(User.objects.get(pk=self.u1.pk).coin, 0)
        self.assertEqual(User.objects.get(pk=self.u2.pk).coin, 0)

    def _hook(self, url, secret, ref="SAME-ID"):
        body = json.dumps({"gateway_reference": ref, "status": "success"})
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return self.client.post(url, data=body, content_type="application/json", HTTP_X_WEBHOOK_SIGNATURE=sig)

    @override_settings(**WEBHOOK)
    def test_webhook_needs_gateway_when_reference_is_shared(self):
        self.start(self.u1, "razorpay")
        self.start(self.u2, "stripe")
        r = self._hook(reverse("buy-coin-confirm"), "stripe-secret")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(User.objects.get(pk=self.u2.pk).coin, 0)

    @override_settings(**WEBHOOK)
    def test_gateway_url_credits_only_that_gateways_purchase(self):
        self.start(self.u1, "razorpay")
        self.start(self.u2, "stripe")
        r = self._hook(reverse("buy-coin-confirm-gateway", args=["stripe"]), "stripe-secret")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(User.objects.get(pk=self.u2.pk).coin, 10)
        self.assertEqual(User.objects.get(pk=self.u1.pk).coin, 0)
        self.assertEqual(
            CoinPurchaseRequest.objects.get(gateway="razorpay").status, CoinPurchaseRequest.Status.PENDING,
        )

    @override_settings(**WEBHOOK)
    def test_gateway_url_with_other_gateways_secret_is_401(self):
        self.start(self.u1, "razorpay")
        self.start(self.u2, "stripe")
        r = self._hook(reverse("buy-coin-confirm-gateway", args=["stripe"]), "rzp-secret")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(User.objects.get(pk=self.u2.pk).coin, 0)

    @override_settings(**WEBHOOK)
    def test_unique_reference_still_works_on_the_plain_webhook_url(self):
        self.start(self.u1, "razorpay", ref="ONLY-ONE")
        r = self._hook(reverse("buy-coin-confirm"), "rzp-secret", ref="ONLY-ONE")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(User.objects.get(pk=self.u1.pk).coin, 10)

    def test_admin_confirm_only_touches_the_blank_gateway_row(self):
        self.start(self.u1, "razorpay", ref="M1")
        self.start(self.u2, "", ref="M1")
        self.client.force_authenticate(mk("ops", is_staff=True))
        r = self.client.post(
            reverse("buy-coin-admin-confirm"), {"gateway_reference": "M1", "status": "success"}, format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(User.objects.get(pk=self.u2.pk).coin, 10)
        self.assertEqual(User.objects.get(pk=self.u1.pk).coin, 0)

    def test_admin_confirm_on_gateway_only_reference_is_409(self):
        self.start(self.u1, "razorpay", ref="G1")
        self.client.force_authenticate(mk("ops", is_staff=True))
        r = self.client.post(
            reverse("buy-coin-admin-confirm"), {"gateway_reference": "G1", "status": "success"}, format="json",
        )
        self.assertEqual(r.status_code, 409)


# ------------------------------------------------------------------- #13
class WithdrawalAccountingTests(APITestCase):
    def setUp(self):
        self.u = mk("acct")

    def eligible(self):
        return fraud.get_withdrawal_eligible_balance(self.u)

    def test_spend_then_earn_cannot_launder_earned_coins(self):
        # The exploit against the old "final totals" formula: it returned 100
        # here because the later EARN cancelled the negative non-eligible net.
        ledger(self.u, TT.PURCHASE, 100)
        ledger(self.u, TT.SPEND, -100)
        ledger(self.u, TT.EARN, 100)
        self.u.refresh_from_db()
        self.assertEqual(self.u.coin, 100)
        self.assertEqual(self.eligible(), 0)

    def test_earned_coins_are_spent_first(self):
        ledger(self.u, TT.EARN, 50)
        ledger(self.u, TT.PURCHASE, 100)
        ledger(self.u, TT.SPEND, -30)
        self.assertEqual(self.eligible(), 100)

    def test_overflow_spend_eats_eligible(self):
        ledger(self.u, TT.PURCHASE, 100)
        ledger(self.u, TT.EARN, 50)
        ledger(self.u, TT.SPEND, -80)
        self.assertEqual(self.eligible(), 70)

    def test_refund_fails_closed(self):
        ledger(self.u, TT.PURCHASE, 100)
        ledger(self.u, TT.SPEND, -100)
        ledger(self.u, TT.REFUND, 100)
        self.assertEqual(self.eligible(), 0)

    def test_gift_received_and_rejected_withdrawal_are_eligible(self):
        ledger(self.u, TT.GIFT_RECEIVED, 40)
        ledger(self.u, TT.PURCHASE, 100)
        self.assertEqual(self.eligible(), 140)
        w = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.u, coins=100, payout_method="upi", payout_details={"upi_id": "alice@bank"},
        )
        self.assertEqual(self.eligible(), 40)
        CoinWithdrawalRequest.objects.reject(withdrawal_id=w.pk, reason="bad")
        self.assertEqual(self.eligible(), 140)

    def test_never_negative(self):
        ledger(self.u, TT.EARN, 10)
        ledger(self.u, TT.SPEND, -10)
        self.assertEqual(self.eligible(), 0)

    def test_manager_enforces_eligibility_and_leaves_nothing_behind(self):
        ledger(self.u, TT.EARN, 200)
        with self.assertRaises(WithdrawalNotEligible):
            CoinWithdrawalRequest.objects.request_withdrawal(
                user=self.u, coins=150, payout_method="upi", payout_details={"upi_id": "alice@bank"},
            )
        self.u.refresh_from_db()
        self.assertEqual(self.u.coin, 200)
        self.assertEqual(CoinWithdrawalRequest.objects.count(), 0)

    def test_manager_bypass_flag_exists_for_trusted_backfills(self):
        ledger(self.u, TT.EARN, 200)
        w = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.u, coins=150, payout_method="upi", payout_details={"upi_id": "alice@bank"},
            enforce_eligibility=False,
        )
        self.assertEqual(w.coins, 150)

    def test_race_second_request_is_stopped_under_the_lock(self):
        # Simulates two requests that both passed the view's unlocked
        # pre-check: the second one must still be refused by the locked
        # re-check inside request_withdrawal().
        ledger(self.u, TT.PURCHASE, 100)
        ledger(self.u, TT.EARN, 100)
        self.client.force_authenticate(self.u)
        real = fraud.is_withdrawal_eligible
        payload = {
            "coins": 100, "payout_method": "upi", "payout_details": {"upi_id": "alice@bank"},
        }
        first = self.client.post(reverse("coin-withdrawal-requests"), payload, format="json")
        self.assertEqual(first.status_code, 201, first.data)

        calls = {"n": 0}

        def view_precheck_passes_manager_is_real(user, coins=None):
            calls["n"] += 1
            return (True, "") if calls["n"] == 1 else real(user, coins)

        with mock.patch("user_profile.views.fraud.is_withdrawal_eligible", view_precheck_passes_manager_is_real):
            second = self.client.post(reverse("coin-withdrawal-requests"), payload, format="json")
        self.assertEqual(second.status_code, 403, second.data)
        self.assertEqual(CoinWithdrawalRequest.objects.count(), 1)
        self.u.refresh_from_db()
        self.assertEqual(self.u.coin, 100)


# ------------------------------------------------------------------- #15
class CoinToInrRateSettingTests(APITestCase):
    def withdraw(self, coins=100):
        u = mk(f"rate{CoinWithdrawalRequest.objects.count()}{User.objects.count()}")
        ledger(u, TT.PURCHASE, 1000)
        return CoinWithdrawalRequest.objects.request_withdrawal(
            user=u, coins=coins, payout_method="upi", payout_details={"upi_id": "alice@bank"},
        )

    def test_default_rate_is_one(self):
        self.assertEqual(self.withdraw().amount_inr, Decimal("100.00"))

    @override_settings(COIN_TO_INR_RATE="2.5")
    def test_rate_comes_from_settings(self):
        self.assertEqual(self.withdraw().amount_inr, Decimal("250.00"))

    @override_settings(COIN_TO_INR_RATE="0.75")
    def test_fractional_rate_is_rounded_to_paise(self):
        self.assertEqual(self.withdraw(101).amount_inr, Decimal("75.75"))

    def test_bad_values_fall_back_to_default(self):
        for bad in ("abc", "0", "-3", "", None):
            with override_settings(COIN_TO_INR_RATE=bad):
                self.assertEqual(CoinWithdrawalRequest.get_coin_to_inr_rate(), Decimal("1"), bad)

    @override_settings(COIN_TO_INR_RATE="2")
    def test_existing_requests_keep_their_snapshot(self):
        w = self.withdraw()
        with override_settings(COIN_TO_INR_RATE="9"):
            w.refresh_from_db()
            self.assertEqual(w.amount_inr, Decimal("200.00"))


# ------------------------------------------------------------------- #16
class WithdrawalAdminActionSerializerTests(APITestCase):
    def setUp(self):
        self.staff = mk("staff", is_staff=True)
        self.user = mk("payee")
        ledger(self.user, TT.PURCHASE, 500)
        self.w = CoinWithdrawalRequest.objects.request_withdrawal(
            user=self.user, coins=200, payout_method="upi", payout_details={"upi_id": "alice@bank"},
        )
        self.url = reverse("coin-withdrawal-admin-action", args=[self.w.pk])
        self.client.force_authenticate(self.staff)

    def act(self, **body):
        return self.client.post(self.url, body, format="json")

    def test_missing_action_gives_standard_field_error(self):
        r = self.act()
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.data["status"])
        self.assertIn("action", r.data["errors"])

    def test_unknown_action_is_rejected_with_field_error(self):
        r = self.act(action="delete_everything")
        self.assertEqual(r.status_code, 400)
        self.assertIn("action", r.data["errors"])

    def test_too_long_reason_is_400_not_a_db_error(self):
        r = self.act(action="reject", reason="x" * 256)
        self.assertEqual(r.status_code, 400)
        self.assertIn("reason", r.data["errors"])
        self.w.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.PENDING)

    def test_processing_stamps_the_reviewer(self):
        r = self.act(action="processing")
        self.assertEqual(r.status_code, 200, r.data)
        self.w.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.PROCESSING)
        self.assertEqual(self.w.reviewed_by_id, self.staff.id)

    def test_reject_refunds_and_records_reason_and_reviewer(self):
        r = self.act(action="reject", reason="Bad UPI id")
        self.assertEqual(r.status_code, 200, r.data)
        self.w.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.w.status, CoinWithdrawalRequest.Status.REJECTED)
        self.assertEqual(self.w.failure_reason, "Bad UPI id")
        self.assertEqual(self.w.reviewed_by_id, self.staff.id)
        self.assertEqual(self.user.coin, 500)

    def test_success_then_reject_is_409(self):
        self.assertEqual(self.act(action="success").status_code, 200)
        self.assertEqual(self.act(action="reject").status_code, 409)

    def test_non_staff_is_403_and_unknown_id_is_404(self):
        self.client.force_authenticate(self.user)
        self.assertEqual(self.act(action="success").status_code, 403)
        self.client.force_authenticate(self.staff)
        r = self.client.post(
            reverse("coin-withdrawal-admin-action", args=[999999]), {"action": "success"}, format="json",
        )
        self.assertEqual(r.status_code, 404)
