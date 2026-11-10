"""
T0.4 (B4) tests.   python manage.py test user_profile.tests_payments

"Done when: Rs 1 test purchase credits exactly once" — automated here with a signed, Razorpay-shaped
webhook delivered several times (payment.captured + order.paid + a plain retry). The REAL Rs 1 purchase
(Razorpay test/live keys + dashboard webhook) still has to be run once by a human; see deploy notes.
"""
import hashlib
import hmac
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import CoinLedger, CoinPurchaseRequest

User = get_user_model()
SECRET = "whsec_test_123"
HOOK = "/profile/buy-coin/webhook/razorpay/"
ORDER = "/profile/buy-coin/order/"


def _sign(body: bytes, secret=SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event(name, order_id, amount_paise, currency="INR", pay_id="pay_ABC123", status="captured"):
    ent = {"id": pay_id, "order_id": order_id, "amount": amount_paise, "currency": currency, "status": status}
    payload = {"payment": {"entity": ent}}
    if name == "order.paid":
        payload["order"] = {"entity": {"id": order_id, "amount_paid": amount_paise, "currency": currency}}
    return json.dumps({"entity": "event", "event": name, "payload": payload}).encode()


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret",
    PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": SECRET},
    PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Razorpay-Signature"},
    COIN_PURCHASE_PACKS="1,100,500", COIN_PURCHASE_INR_PER_COIN="1",
)
class CoinPaymentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="payer", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def _order(self, coins, order_id="order_TEST1"):
        with mock.patch("user_profile.payments.create_razorpay_order", return_value=order_id) as m:
            r = self.api.post(ORDER, {"coins": coins}, format="json")
        return r, m

    def _hook(self, body, sig=None):
        sig = _sign(body) if sig is None else sig
        return self.client.post(HOOK, data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)

    def _coins(self):
        self.user.refresh_from_db()
        return self.user.coin

    # ---- order creation -------------------------------------------------------------------
    def test_order_is_priced_by_server_and_creates_pending_row(self):
        r, m = self._order(500)
        self.assertEqual(r.status_code, 201, r.content)
        d = r.json()["data"]
        self.assertEqual((d["order_id"], d["amount_paise"], d["currency"], d["coins"]), ("order_TEST1", 50000, "INR", 500))
        self.assertEqual(m.call_args.kwargs["amount_paise"], 50000)
        p = CoinPurchaseRequest.objects.get(gateway="razorpay", gateway_reference="order_TEST1")
        self.assertEqual((p.user_id, p.coins, str(p.amount), p.status), (self.user.id, 500, "500.00", "pending"))
        self.assertEqual(self._coins(), 0)  # pending never credits

    def test_pack_not_in_list_rejected_and_no_gateway_call(self):
        r, m = self._order(999999)
        self.assertEqual(r.status_code, 400)
        m.assert_not_called()

    def test_order_requires_login(self):
        self.assertIn(APIClient().post(ORDER, {"coins": 100}, format="json").status_code, (401, 403))

    def test_gateway_down_is_502_and_leaves_no_row(self):
        from .payments import GatewayError
        with mock.patch("user_profile.payments.create_razorpay_order", side_effect=GatewayError("x")):
            r = self.api.post(ORDER, {"coins": 100}, format="json")
        self.assertEqual(r.status_code, 502)
        self.assertEqual(CoinPurchaseRequest.objects.count(), 0)

    # ---- webhook: the Rs 1 / exactly-once contract ------------------------------------------
    def test_one_rupee_purchase_credits_exactly_once(self):
        self._order(1, "order_ONE")
        body = _event("payment.captured", "order_ONE", 100)
        self.assertEqual(self._hook(body).status_code, 200)
        self.assertEqual(self._coins(), 1)
        # Razorpay re-delivers, and also sends order.paid for the same payment:
        self.assertEqual(self._hook(body).status_code, 200)
        self.assertEqual(self._hook(_event("order.paid", "order_ONE", 100)).status_code, 200)
        self.assertEqual(self._coins(), 1)
        self.assertEqual(CoinLedger.objects.filter(user=self.user, transaction_type="purchase").count(), 1)
        p = CoinPurchaseRequest.objects.get(gateway_reference="order_ONE")
        self.assertEqual((p.status, p.gateway_payment_id), ("success", "pay_ABC123"))

    def test_bad_or_missing_signature_is_401_and_credits_nothing(self):
        self._order(100)
        body = _event("payment.captured", "order_TEST1", 10000)
        self.assertEqual(self._hook(body, sig="deadbeef").status_code, 401)
        self.assertEqual(self._hook(body, sig=_sign(body, "wrong-secret")).status_code, 401)
        r = self.client.post(HOOK, data=body, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self._coins(), 0)

    def test_tampered_body_after_signing_rejected(self):
        self._order(100)
        good = _event("payment.captured", "order_TEST1", 10000)
        sig = _sign(good)
        self.assertEqual(self._hook(_event("payment.captured", "order_TEST1", 10000, pay_id="pay_X"), sig).status_code, 401)

    def test_underpayment_is_not_credited(self):
        self._order(500)
        r = self._hook(_event("payment.captured", "order_TEST1", 100))  # paid Rs 1 for a Rs 500 order
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["credited"])
        self.assertEqual(self._coins(), 0)
        self.assertEqual(CoinPurchaseRequest.objects.get(gateway_reference="order_TEST1").status, "pending")

    def test_wrong_currency_is_not_credited(self):
        self._order(100)
        self._hook(_event("payment.captured", "order_TEST1", 10000, currency="USD"))
        self.assertEqual(self._coins(), 0)

    def test_client_started_row_with_cheap_price_cannot_cheat(self):
        # Legacy client-start endpoint: ask for 1,000,000 coins at Rs 1, then pay Rs 1.
        CoinPurchaseRequest.objects.start_purchase(
            user=self.user, gateway_reference="order_CHEAT", amount="1.00", coins=1_000_000, gateway="razorpay")
        self._hook(_event("payment.captured", "order_CHEAT", 100))
        self.assertEqual(self._coins(), 0)  # amount != coins * server rate

    def test_payment_failed_does_not_kill_a_later_successful_attempt(self):
        self._order(100)
        r = self._hook(_event("payment.failed", "order_TEST1", 10000, status="failed"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CoinPurchaseRequest.objects.get(gateway_reference="order_TEST1").status, "pending")
        self._hook(_event("payment.captured", "order_TEST1", 10000))
        self.assertEqual(self._coins(), 100)

    def test_unknown_order_is_200_ignored(self):
        r = self._hook(_event("payment.captured", "order_NOT_OURS", 100))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._coins(), 0)

    @override_settings(PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": ""})
    def test_missing_server_secret_fails_closed_503(self):
        self._order(100)
        self.assertEqual(self._hook(_event("payment.captured", "order_TEST1", 10000)).status_code, 503)
        self.assertEqual(self._coins(), 0)
