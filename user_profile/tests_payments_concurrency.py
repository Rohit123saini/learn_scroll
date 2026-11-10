"""P-A2 — the SAME signed Razorpay webhook delivered by several workers at once credits coins exactly once.
Postgres only (SQLite cannot run real concurrent writers).   python manage.py test user_profile.tests_payments_concurrency
Sequential duplicates are already covered in tests_payments.py. Written by reading the code; NOT run.
"""
import threading
import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection, connections
from django.test import Client, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from .models import CoinLedger, CoinPurchaseRequest
from .tests_payments import HOOK, ORDER, SECRET, _event, _sign

User = get_user_model()


@unittest.skipUnless(connection.vendor == "postgresql", "needs real concurrent transactions (Postgres)")
@override_settings(
    RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="secret",
    PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": SECRET},
    PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Razorpay-Signature"},
    COIN_PURCHASE_PACKS="1,100,500", COIN_PURCHASE_INR_PER_COIN="1",
)
class ConcurrentWebhookTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="racer", password="x")
        api = APIClient()
        api.force_authenticate(self.user)
        with mock.patch("user_profile.payments.create_razorpay_order", return_value="order_RACE"):
            self.assertEqual(api.post(ORDER, {"coins": 100}, format="json").status_code, 201)

    def test_parallel_duplicate_deliveries_credit_once(self):
        body = _event("payment.captured", "order_RACE", 10000, pay_id="pay_RACE1")
        sig = _sign(body)
        codes, errors, barrier = [], [], threading.Barrier(6)

        def deliver():
            try:
                barrier.wait(timeout=10)
                c = Client()
                r = c.post(HOOK, data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)
                codes.append(r.status_code)
            except Exception as exc:  # noqa: BLE001 - surfaced below
                errors.append(repr(exc))
            finally:
                connections.close_all()

        threads = [threading.Thread(target=deliver) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(errors, [])
        self.assertEqual(len(codes), 6)
        self.assertTrue(all(c == 200 for c in codes), codes)
        self.user.refresh_from_db()
        self.assertEqual(self.user.coin, 100)
        self.assertEqual(CoinLedger.objects.filter(user=self.user, transaction_type="purchase").count(), 1)
        self.assertEqual(CoinPurchaseRequest.objects.get(gateway_reference="order_RACE").status, "success")
