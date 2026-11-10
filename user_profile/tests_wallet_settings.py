"""
S9 - Wallet / payments settings. NOT RUN by the author (read-only task):
    python manage.py test user_profile.tests_wallet_settings

Covers: spend limit enforcement in CoinLedger.record_transaction, minor caps, settings API (PATCH validation, cross-user
isolation), ledger filters + totals, payments history + refund webhook + masked method, PDF receipt access rules,
withdrawal serializer extras.
"""
import datetime
import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import wallet_settings as ws
from .models import (
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    SpendLimitExceeded,
    WalletSettings,
)
from .payments import _method_from_event
from .wallet_receipt_pdf import mask_account, mask_upi, payout_summary, purchase_receipt_no
from .wallet_unify import TYPE_GROUPS, WalletFilters, wallet_history, wallet_totals

User = get_user_model()
SETTINGS_URL = "/profile/wallet/settings/"
SUMMARY_URL = "/profile/wallet/summary/"
PAYMENTS_URL = "/profile/wallet/payments/"
HISTORY_URL = "/profile/wallet/history/"
ADULT_DOB = datetime.date(1995, 1, 1)
MINOR_DOB = datetime.date(timezone.localdate().year - 14, 6, 1)


def make_user(name, coin=0, dob=ADULT_DOB):
    user = User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob)
    if coin:
        CoinLedger.objects.record_transaction(
            user=user, transaction_type=CoinLedger.TransactionType.PURCHASE, amount=coin, reference=f"seed:{name}",
        )
        user.refresh_from_db()
    return user


class MaskingTests(SimpleTestCase):
    def test_upi_and_account_masks(self):
        self.assertEqual(mask_upi("rahul.k@okhdfcbank"), "ra***@okhdfcbank")
        self.assertEqual(mask_upi(""), "")
        self.assertEqual(mask_account("123456789012"), "XXXX9012")
        self.assertEqual(payout_summary("upi", {"upi_id": "abc@ybl"}), "UPI ab***@ybl")
        self.assertEqual(payout_summary("bank_transfer", {"account_number": "000011112222"}), "Bank XXXX2222")
        self.assertNotIn("000011112222", payout_summary("bank_transfer", {"account_number": "000011112222"}))

    def test_receipt_no(self):
        self.assertEqual(purchase_receipt_no(42, datetime.datetime(2026, 10, 3)), "LS-R-202610-000042")

    def test_method_from_event_is_masked(self):
        ev = {"payload": {"payment": {"entity": {"method": "upi", "vpa": "someone@okaxis"}}}}
        self.assertEqual(_method_from_event(ev), ("upi", "so***@okaxis"))
        card = {"payload": {"payment": {"entity": {"method": "card", "card": {"last4": "4242", "network": "Visa"}}}}}
        self.assertEqual(_method_from_event(card), ("card", "Visa XX4242"))
        self.assertEqual(_method_from_event({}), ("", ""))


class LimitValidationTests(TestCase):
    def test_validate_patch_rules(self):
        user = make_user("adult")
        self.assertEqual(ws.validate_patch(user.pk, {"daily_spend_limit": 100}), {"daily_spend_limit": 100})
        self.assertEqual(ws.validate_patch(user.pk, {"daily_spend_limit": None}), {"daily_spend_limit": None})
        with self.assertRaises(ValueError):
            ws.validate_patch(user.pk, {"daily_spend_limit": 0})
        with self.assertRaises(ValueError):
            ws.validate_patch(user.pk, {"daily_spend_limit": "abc"})
        with self.assertRaises(ValueError):
            ws.validate_patch(user.pk, {"daily_spend_limit": 500, "monthly_spend_limit": 100})
        with self.assertRaises(ValueError):
            ws.validate_patch(user.pk, {"confirm_purchases": "maybe"})

    @override_settings(WALLET_MINOR_DAILY_SPEND_LIMIT=50, WALLET_MINOR_MONTHLY_SPEND_LIMIT=200)
    def test_minor_cannot_raise_remove_or_disable_confirm(self):
        kid = make_user("kid", dob=MINOR_DOB)
        self.assertEqual(ws.validate_patch(kid.pk, {"daily_spend_limit": 30}), {"daily_spend_limit": 30})
        with self.assertRaises(ValueError):
            ws.validate_patch(kid.pk, {"daily_spend_limit": 51})
        with self.assertRaises(ValueError):
            ws.validate_patch(kid.pk, {"monthly_spend_limit": None})
        with self.assertRaises(ValueError):
            ws.validate_patch(kid.pk, {"confirm_purchases": False})

    @override_settings(WALLET_MINOR_DAILY_SPEND_LIMIT=50, WALLET_MINOR_MONTHLY_SPEND_LIMIT=200)
    def test_effective_limits_take_the_lower(self):
        kid = make_user("kid2", dob=MINOR_DOB)
        self.assertEqual(ws.effective_limits(kid.pk)["daily"], 50)
        WalletSettings.objects.create(user=kid, daily_spend_limit=20)
        self.assertEqual(ws.effective_limits(kid.pk)["daily"], 20)
        adult = make_user("grown")
        self.assertIsNone(ws.effective_limits(adult.pk)["daily"])


class EnforcementTests(TestCase):
    def spend(self, user, coins, ttype=CoinLedger.TransactionType.SPEND, **kw):
        return CoinLedger.objects.record_transaction(user=user, transaction_type=ttype, amount=-coins, **kw)

    def test_no_limit_means_no_block(self):
        user = make_user("free", coin=1000)
        self.spend(user, 900)
        user.refresh_from_db()
        self.assertEqual(user.coin, 100)

    def test_daily_limit_blocks_and_keeps_balance(self):
        user = make_user("capped", coin=1000)
        WalletSettings.objects.create(user=user, daily_spend_limit=100)
        self.spend(user, 60, reference="a")
        with self.assertRaises(SpendLimitExceeded) as ctx:
            self.spend(user, 50, reference="b")
        self.assertEqual(ctx.exception.code, "daily")
        user.refresh_from_db()
        self.assertEqual(user.coin, 940)  # nothing debited by the blocked call
        self.assertTrue(isinstance(ctx.exception, ValueError))  # old `except ValueError` handlers still work

    def test_monthly_limit(self):
        user = make_user("monthly", coin=1000)
        WalletSettings.objects.create(user=user, monthly_spend_limit=100)
        self.spend(user, 100, reference="m1")
        with self.assertRaises(SpendLimitExceeded) as ctx:
            self.spend(user, 1, reference="m2")
        self.assertEqual(ctx.exception.code, "monthly")

    def test_retry_of_applied_reference_is_not_blocked(self):
        user = make_user("retry", coin=500)
        WalletSettings.objects.create(user=user, daily_spend_limit=100)
        first = self.spend(user, 100, reference="same")
        again = self.spend(user, 100, reference="same")  # idempotent replay, would be over the limit if re-applied
        self.assertEqual(first.pk, again.pk)

    def test_only_limited_types_and_exempt_flag(self):
        user = make_user("types", coin=1000)
        WalletSettings.objects.create(user=user, daily_spend_limit=10)
        # withdrawal is not a "spend"
        CoinLedger.objects.record_transaction(
            user=user, transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REQUESTED, amount=-500, reference="w1",
        )
        # fee payment style call opts out
        self.spend(user, 300, reference="fee-1", enforce_spend_limit=False)
        with self.assertRaises(SpendLimitExceeded):
            self.spend(user, 11, ttype=CoinLedger.TransactionType.GIFT_SENT, reference="g1")

    @override_settings(WALLET_MINOR_DAILY_SPEND_LIMIT=30)
    def test_minor_cap_applies_without_any_row(self):
        kid = make_user("kid3", coin=500, dob=MINOR_DOB)
        with self.assertRaises(SpendLimitExceeded):
            self.spend(kid, 31, reference="k1")
        self.spend(kid, 30, reference="k2")

    @override_settings(WALLET_MINOR_MONTHLY_TOPUP_INR=500)
    def test_minor_topup_cap(self):
        kid = make_user("kid4", dob=MINOR_DOB)
        self.assertIsNone(ws.topup_block_reason(kid.pk, 500))
        self.assertIsNotNone(ws.topup_block_reason(kid.pk, 501))
        self.assertIsNone(ws.topup_block_reason(make_user("adult4").pk, 100000))


class SettingsApiTests(TestCase):
    def setUp(self):
        self.user = make_user("api", coin=300)
        self.other = make_user("other")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_requires_auth(self):
        self.assertEqual(APIClient().get(SETTINGS_URL).status_code, 401)

    def test_get_defaults_then_patch_persists(self):
        data = self.api.get(SETTINGS_URL).json()["data"]
        self.assertTrue(data["confirm_purchases"])
        self.assertIsNone(data["daily_spend_limit"])
        res = self.api.patch(SETTINGS_URL, {"confirm_purchases": False, "daily_spend_limit": 80, "monthly_spend_limit": 400}, format="json")
        self.assertEqual(res.status_code, 200)
        row = WalletSettings.objects.get(user=self.user)
        self.assertFalse(row.confirm_purchases)
        self.assertEqual((row.daily_spend_limit, row.monthly_spend_limit), (80, 400))
        self.assertEqual(res.json()["data"]["effective_daily_limit"], 80)

    def test_bad_patch_is_400_and_saves_nothing(self):
        res = self.api.patch(SETTINGS_URL, {"daily_spend_limit": 500, "monthly_spend_limit": 100}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["status"])
        self.assertFalse(WalletSettings.objects.filter(user=self.user, daily_spend_limit=500).exists())

    def test_cross_user_isolation(self):
        self.api.patch(SETTINGS_URL, {"daily_spend_limit": 5}, format="json")
        other_api = APIClient()
        other_api.force_authenticate(self.other)
        self.assertIsNone(other_api.get(SETTINGS_URL).json()["data"]["daily_spend_limit"])

    @override_settings(WALLET_MINOR_DAILY_SPEND_LIMIT=40)
    def test_minor_view_shows_lock_and_cap(self):
        kid = make_user("kidapi", dob=MINOR_DOB)
        api = APIClient()
        api.force_authenticate(kid)
        data = api.get(SETTINGS_URL).json()["data"]
        self.assertTrue(data["is_minor"])
        self.assertTrue(data["limits_locked"])
        self.assertEqual(data["effective_daily_limit"], 40)
        self.assertEqual(api.patch(SETTINGS_URL, {"confirm_purchases": False}, format="json").status_code, 400)

    def test_summary(self):
        data = self.api.get(SUMMARY_URL).json()["data"]
        self.assertEqual(data["balance"], 300)
        self.assertIn("withdrawable", data)
        self.assertIn("limits", data)
        self.assertEqual(data["min_withdrawal"], CoinWithdrawalRequest.MIN_WITHDRAWAL_COINS)


class LedgerFilterTests(TestCase):
    def setUp(self):
        self.user = make_user("ledger", coin=1000)  # +1000 purchase
        T = CoinLedger.TransactionType
        CoinLedger.objects.record_transaction(user=self.user, transaction_type=T.SPEND, amount=-100, reference="s1", description="Streak freeze")
        CoinLedger.objects.record_transaction(user=self.user, transaction_type=T.REFUND, amount=40, reference="r1", description="Refund")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_group_and_direction(self):
        self.assertEqual(wallet_history(self.user, 50, 0, WalletFilters(group="spends"))[0], 1)
        self.assertEqual(wallet_history(self.user, 50, 0, WalletFilters(direction="in"))[0], 2)
        self.assertEqual(wallet_history(self.user, 50, 0, WalletFilters(group="refunds", direction="in"))[0], 1)
        self.assertEqual(wallet_history(self.user, 50, 0)[0], 3)

    def test_unknown_group_is_ignored(self):
        self.assertFalse(WalletFilters(group="bogus").active)
        self.assertEqual(set(TYPE_GROUPS), {"topups", "spends", "earnings", "withdrawals", "refunds", "other"})

    def test_search_and_totals(self):
        self.assertEqual(wallet_history(self.user, 50, 0, WalletFilters(q="streak"))[0], 1)
        self.assertEqual(wallet_totals(self.user), {"in": 1040, "out": 100, "count": 3})
        self.assertEqual(wallet_totals(self.user, WalletFilters(direction="out"))["out"], 100)

    def test_date_range(self):
        future = timezone.localdate() + datetime.timedelta(days=2)
        flt = WalletFilters.from_query({"date_from": future.isoformat()})
        self.assertEqual(wallet_history(self.user, 50, 0, flt)[0], 0)
        junk = WalletFilters.from_query({"date_from": "not-a-date"})
        self.assertIsNone(junk.date_from)

    def test_endpoint_filters_and_totals_flag(self):
        res = self.api.get(HISTORY_URL, {"group": "spends", "totals": "1"}).json()["data"]
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["totals"]["out"], 100)
        plain = self.api.get(HISTORY_URL).json()["data"]
        self.assertNotIn("totals", plain)  # old response shape unchanged unless asked


class PaymentsAndReceiptTests(TestCase):
    def setUp(self):
        self.user = make_user("payer")
        self.other = make_user("someone")
        self.ok = CoinPurchaseRequest.objects.create(
            user=self.user, gateway="razorpay", gateway_reference="order_OK", gateway_payment_id="pay_OK",
            amount=Decimal("100.00"), coins=100, status=CoinPurchaseRequest.Status.SUCCESS,
            payment_method="upi", payment_method_detail="ab***@ybl",
        )
        self.pending = CoinPurchaseRequest.objects.create(
            user=self.user, gateway="razorpay", gateway_reference="order_P", amount=Decimal("50.00"), coins=50,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_list_and_status_filter(self):
        data = self.api.get(PAYMENTS_URL).json()["data"]
        self.assertEqual(data["count"], 2)
        done = self.api.get(PAYMENTS_URL, {"status": "success"}).json()["data"]["results"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["method_text"], "UPI - ab***@ybl")
        self.assertTrue(done[0]["receipt_available"])
        self.assertNotIn("signature", json.dumps(done[0]).lower())

    def test_only_own_payments(self):
        other_api = APIClient()
        other_api.force_authenticate(self.other)
        self.assertEqual(other_api.get(PAYMENTS_URL).json()["data"]["count"], 0)
        self.assertEqual(other_api.get(f"{PAYMENTS_URL}{self.ok.pk}/receipt/").status_code, 404)

    def test_receipt_rules(self):
        self.assertEqual(self.api.get(f"{PAYMENTS_URL}{self.pending.pk}/receipt/").status_code, 409)
        with mock.patch("user_profile.wallet_settings_views.render_purchase_receipt", return_value=b"%PDF-1.4 fake"):
            res = self.api.get(f"{PAYMENTS_URL}{self.ok.pk}/receipt/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("LS-R-", res["Content-Disposition"])

    def test_pdf_unavailable_is_501(self):
        from .wallet_receipt_pdf import PdfUnavailable

        with mock.patch("user_profile.wallet_settings_views.render_purchase_receipt", side_effect=PdfUnavailable("no reportlab")):
            self.assertEqual(self.api.get(f"{PAYMENTS_URL}{self.ok.pk}/receipt/").status_code, 501)

    def test_withdrawal_slip_only_when_success(self):
        w = CoinWithdrawalRequest.objects.create(
            user=self.user, coins=100, amount_inr=Decimal("100.00"), payout_method="upi",
            payout_details={"upi_id": "me@ybl"}, status=CoinWithdrawalRequest.Status.PENDING,
        )
        self.assertEqual(self.api.get(f"/profile/wallet/withdrawals/{w.pk}/receipt/").status_code, 409)
        w.status = CoinWithdrawalRequest.Status.SUCCESS
        w.save()
        with mock.patch("user_profile.wallet_settings_views.render_withdrawal_slip", return_value=b"%PDF fake") as fake:
            res = self.api.get(f"/profile/wallet/withdrawals/{w.pk}/receipt/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(fake.call_args.kwargs["payout_text"], "UPI me***@ybl")  # masked, never the raw id


class RefundWebhookTests(TestCase):
    def setUp(self):
        from . import payments

        self.payments = payments
        self.user = make_user("refunded")
        self.p = CoinPurchaseRequest.objects.create(
            user=self.user, gateway="razorpay", gateway_reference="order_R", gateway_payment_id="pay_R",
            amount=Decimal("100.00"), coins=100, status=CoinPurchaseRequest.Status.SUCCESS,
        )

    def event(self, name, status="created", amount=10000):
        return {"event": name, "payload": {"refund": {"entity": {"id": "rfnd_1", "payment_id": "pay_R", "amount": amount, "status": status}}}}

    def test_refund_progress_is_recorded_and_never_goes_backwards(self):
        self.payments._handle_refund(self.event("refund.created"), "refund.created")
        self.p.refresh_from_db()
        self.assertEqual(self.p.refund_status, "initiated")
        self.payments._handle_refund(self.event("refund.processed"), "refund.processed")
        self.p.refresh_from_db()
        self.assertEqual((self.p.refund_status, self.p.refund_amount), ("processed", Decimal("100.00")))
        self.assertIsNotNone(self.p.refunded_at)
        self.payments._handle_refund(self.event("refund.created"), "refund.created")  # late, older event
        self.p.refresh_from_db()
        self.assertEqual(self.p.refund_status, "processed")

    def test_refund_does_not_touch_coins_and_unknown_payment_is_ignored(self):
        before = User.objects.get(pk=self.user.pk).coin
        self.payments._handle_refund(self.event("refund.processed"), "refund.processed")
        self.assertEqual(User.objects.get(pk=self.user.pk).coin, before)
        stray = self.event("refund.processed")
        stray["payload"]["refund"]["entity"]["payment_id"] = "pay_UNKNOWN"
        self.assertEqual(self.payments._handle_refund(stray, "refund.processed").status_code, 200)

    def test_refund_status_shows_in_payments_api(self):
        self.payments._handle_refund(self.event("refund.processed"), "refund.processed")
        api = APIClient()
        api.force_authenticate(self.user)
        row = api.get(PAYMENTS_URL).json()["data"]["results"][0]
        self.assertEqual(row["refund"]["status"], "processed")


@override_settings(COIN_CASHOUT_ENABLED=True)  # P-G1: these tests exercise the cash-out path itself
class WithdrawalSerializerTests(TestCase):
    def test_extra_fields_are_read_only_and_masked(self):
        user = make_user("wd", coin=500)
        api = APIClient()
        api.force_authenticate(user)
        with mock.patch("user_profile.fraud.is_withdrawal_eligible", return_value=(True, "")):
            res = api.post(
                "/profile/coin-withdrawals/",
                {"coins": 100, "payout_method": "upi", "payout_details": {"upi_id": "me@ybl"}, "amount_inr": "99999", "status": "success"},
                format="json",
            )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()["data"]
        self.assertEqual(body["status"], "pending")  # client cannot set status
        self.assertEqual(Decimal(body["amount_inr"]), Decimal("100.00"))  # nor the INR snapshot
        self.assertEqual(body["payout_summary"], "UPI me***@ybl")
        self.assertFalse(body["receipt_available"])
