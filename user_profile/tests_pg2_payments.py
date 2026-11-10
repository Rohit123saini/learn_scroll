"""P-G2 tests (NOT RUN):  python manage.py test user_profile.tests_pg2_payments
Reconcile job for lost webhooks (coin top-ups), receipts list, emailed receipt (once), refund policy page.
The gateway lookup is injected (`fetch=`), so no network."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from common import payment_reconcile as pr

from .models import CoinLedger, CoinPurchaseRequest

User = get_user_model()


def _pay(order, amount_paise, status="captured", currency="INR", pay_id="pay_R1", method="upi"):
    return {"id": pay_id, "order_id": order, "amount": amount_paise, "currency": currency, "status": status, "method": method,
            "vpa": "abcd@okbank"}


@override_settings(COIN_PURCHASE_PACKS="1,100,500", COIN_PURCHASE_INR_PER_COIN="1",
                   PAYMENT_RECONCILE_MIN_AGE_MINUTES=10, PAYMENT_RECONCILE_MAX_AGE_DAYS=7, PAYMENT_RECONCILE_EXPIRE_HOURS=24,
                   COIN_RECEIPT_EMAIL_ENABLED=True)
class CoinReconcileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="payer", password="x", email="payer@example.com")

    def _pending(self, order="order_R1", coins=100, age_minutes=30):
        p, _ = CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference=order, amount=Decimal(coins), coins=coins, gateway="razorpay")
        CoinPurchaseRequest.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
        return p

    def _coins(self):
        self.user.refresh_from_db()
        return self.user.coin

    def test_lost_webhook_is_settled_exactly_once(self):
        p = self._pending()
        fetch = mock.Mock(return_value=[_pay("order_R1", 10000)])
        with mock.patch("core.notification_receipts.queue_receipt"):
            s1 = pr.reconcile_coin_purchases(fetch=fetch)
            s2 = pr.reconcile_coin_purchases(fetch=fetch)  # second run: row is SUCCESS now -> not even looked at
        p.refresh_from_db()
        self.assertEqual((s1["settled"], s2["checked"]), (1, 0))
        self.assertEqual(p.status, "success")
        self.assertEqual(p.gateway_payment_id, "pay_R1")
        self.assertEqual(p.payment_method, "upi")
        self.assertEqual(self._coins(), 100)
        self.assertEqual(CoinLedger.objects.filter(user=self.user, transaction_type="purchase").count(), 1)

    def test_young_row_is_left_to_the_webhook(self):
        self._pending(age_minutes=2)
        fetch = mock.Mock()
        s = pr.reconcile_coin_purchases(fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(s["checked"], 0)

    def test_amount_mismatch_is_not_credited_and_not_failed(self):
        p = self._pending()
        with self.assertLogs("common.payment_reconcile", level="ERROR"):
            s = pr.reconcile_coin_purchases(fetch=lambda o: [_pay(o, 5000)])
        p.refresh_from_db()
        self.assertEqual((s["mismatch"], s["settled"], p.status, self._coins()), (1, 0, "pending", 0))

    def test_wrong_currency_is_not_credited(self):
        self._pending()
        with self.assertLogs("common.payment_reconcile", level="ERROR"):
            s = pr.reconcile_coin_purchases(fetch=lambda o: [_pay(o, 10000, currency="USD")])
        self.assertEqual((s["mismatch"], self._coins()), (1, 0))

    def test_failed_attempt_only_stays_pending_until_expiry_then_nothing_is_failed(self):
        p = self._pending(age_minutes=60 * 30)  # older than EXPIRE_HOURS=24, but the order HAS an attempt
        s = pr.reconcile_coin_purchases(fetch=lambda o: [_pay(o, 10000, status="failed")])
        p.refresh_from_db()
        self.assertEqual((s["still_pending"], s["expired"], p.status), (1, 0, "pending"))

    def test_no_attempt_and_old_enough_is_expired(self):
        p = self._pending(age_minutes=60 * 30)
        s = pr.reconcile_coin_purchases(fetch=lambda o: [])
        p.refresh_from_db()
        self.assertEqual((s["expired"], p.status), (1, "failed"))
        self.assertIn("expired_by_reconcile", p.failure_reason)

    def test_no_attempt_but_not_old_enough_stays_pending(self):
        p = self._pending(age_minutes=60)
        s = pr.reconcile_coin_purchases(fetch=lambda o: [])
        p.refresh_from_db()
        self.assertEqual((s["still_pending"], p.status), (1, "pending"))

    def test_dry_run_changes_nothing(self):
        p = self._pending()
        s = pr.reconcile_coin_purchases(fetch=lambda o: [_pay(o, 10000)], dry_run=True)
        p.refresh_from_db()
        self.assertEqual((s["settled"], p.status, self._coins()), (1, "pending", 0))

    def test_gateway_error_on_one_row_does_not_stop_the_others(self):
        self._pending("order_BAD", coins=100)
        self._pending("order_OK", coins=500)

        def fetch(order):
            if order == "order_BAD":
                raise pr.ReconcileGatewayError("boom")
            return [_pay(order, 50000, pay_id="pay_OK")]

        with mock.patch("core.notification_receipts.queue_receipt"):
            s = pr.reconcile_coin_purchases(fetch=fetch)
        self.assertEqual((s["gateway_errors"], s["settled"], self._coins()), (1, 1, 500))

    def test_rows_older_than_max_age_are_only_counted(self):
        self._pending(age_minutes=60 * 24 * 9)
        fetch = mock.Mock()
        s = pr.reconcile_coin_purchases(fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual((s["too_old"], s["checked"]), (1, 0))

    @override_settings(PAYMENT_RECONCILE_ENABLED=False)
    def test_task_respects_the_switch(self):
        from .tasks import reconcile_payments
        self.assertIn("skipped", reconcile_payments())

    def test_run_all_survives_one_reconciler_crashing(self):
        with mock.patch.object(pr, "reconcile_coin_purchases", side_effect=RuntimeError("x")), \
                mock.patch.object(pr, "reconcile_fee_payments", return_value={"kind": "campus_fee"}):
            out = pr.run_all(fetch=lambda o: [])
        self.assertIn("error", out["coin_purchase"])
        self.assertEqual(out["campus_fee"]["kind"], "campus_fee")


class ClassifyTests(TestCase):
    def test_classify(self):
        self.assertEqual(pr.classify([])[0], "none")
        self.assertEqual(pr.classify([{"status": "failed"}])[0], "attempted")
        self.assertEqual(pr.classify([{"status": "failed"}, {"status": "captured", "id": "p"}])[0], "captured")


class ReceiptsListTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="rcpt", password="x", email="rcpt@example.com")
        self.other = User.objects.create_user(username="other", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _success(self, user, order, coins):
        CoinPurchaseRequest.objects.start_purchase(
            user=user, gateway_reference=order, amount=Decimal(coins), coins=coins, gateway="razorpay")
        with mock.patch("core.notification_receipts.queue_receipt"):
            return CoinPurchaseRequest.objects.confirm_success(
                gateway_reference=order, gateway_payment_id="pay_" + order, gateway="razorpay")

    def test_list_has_only_my_paid_rows(self):
        mine = self._success(self.user, "o1", 100)
        self._success(self.other, "o2", 500)
        CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference="o3", amount=Decimal(100), coins=100, gateway="razorpay")  # pending: no receipt
        r = self.api.get("/profile/wallet/receipts/")
        self.assertEqual(r.status_code, 200, r.data)
        rows = r.data["data"]["results"]
        self.assertEqual([x["id"] for x in rows], [str(mine.pk)])
        self.assertEqual(rows[0]["kind"], "coins")
        self.assertTrue(rows[0]["receipt_no"].startswith("LS-R-"))
        self.assertEqual(rows[0]["pdf_path"], f"/profile/wallet/payments/{mine.pk}/receipt/")

    def test_requires_login(self):
        self.assertIn(APIClient().get("/profile/wallet/receipts/").status_code, (401, 403))

    def test_kind_filter_and_paging_are_bounded(self):
        for i in range(3):
            self._success(self.user, f"op{i}", 100)
        r = self.api.get("/profile/wallet/receipts/?kind=coins&limit=2&offset=1")
        self.assertEqual((r.data["data"]["count"], len(r.data["data"]["results"])), (3, 2))
        r = self.api.get("/profile/wallet/receipts/?kind=pass")
        self.assertEqual(r.data["data"]["results"], [])

    def test_pass_pdf_of_someone_elses_purchase_is_404(self):
        self.assertEqual(self.api.get("/profile/wallet/receipts/pass/999999/pdf/").status_code, 404)


@override_settings(COIN_RECEIPT_EMAIL_ENABLED=True, DEFAULT_FROM_EMAIL="noreply@example.com",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ReceiptEmailTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="mailme", password="x", email="mailme@example.com")
        CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference="om", amount=Decimal(100), coins=100, gateway="razorpay")
        with mock.patch("core.notification_receipts.queue_receipt"):
            self.p = CoinPurchaseRequest.objects.confirm_success(gateway_reference="om", gateway="razorpay")

    def test_sent_once_with_pdf_even_if_task_runs_twice(self):
        from .receipt_email import send_coin_receipt
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        self.assertTrue(send_coin_receipt(self.p.pk))
        self.assertTrue(send_coin_receipt(self.p.pk))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["mailme@example.com"])
        self.assertTrue(mail.outbox[0].attachments[0][0].endswith(".pdf"))

    @override_settings(COIN_RECEIPT_EMAIL_ENABLED=False)
    def test_switch_off_sends_nothing(self):
        from .receipt_email import send_coin_receipt
        self.assertTrue(send_coin_receipt(self.p.pk))
        self.assertEqual(len(mail.outbox), 0)

    def test_no_address_is_not_an_error(self):
        from .receipt_email import send_coin_receipt
        User.objects.filter(pk=self.user.pk).update(email="")
        self.assertTrue(send_coin_receipt(self.p.pk))
        self.assertEqual(len(mail.outbox), 0)

    def test_smtp_failure_asks_for_retry_and_releases_the_mark(self):
        from .receipt_email import send_coin_receipt
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        with mock.patch("django.core.mail.EmailMessage.send", side_effect=OSError("smtp down")):
            self.assertFalse(send_coin_receipt(self.p.pk))
        self.assertTrue(send_coin_receipt(self.p.pk))  # retry works (mark was released)
        self.assertEqual(len(mail.outbox), 1)


class RefundPolicyPageTests(TestCase):
    def test_page_is_public_and_mentions_each_purchase_kind(self):
        r = self.client.get("/legal/refund-policy/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for word in ("Coin purchases", "Class passes", "Test series", "Campus", "How to ask for a refund"):
            self.assertIn(word, html)

    def test_hindi_summary(self):
        self.assertIn("सारांश", self.client.get("/legal/refund-policy/?lang=hi").content.decode())

    @override_settings(REFUND_POLICY_URL="https://example.com/refunds")
    def test_external_url_redirects(self):
        r = self.client.get("/legal/refund-policy/")
        self.assertEqual((r.status_code, r["Location"]), (302, "https://example.com/refunds"))

    def test_terms_links_to_it(self):
        self.assertIn("/legal/refund-policy/", self.client.get("/legal/terms/").content.decode())
