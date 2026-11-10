"""P-LEFT (P-A2 leftover) - duplicate / crossed deliveries of the campus fee payment. WRITTEN, NOT RUN:
    python manage.py test campus.tests_pleft_fee_duplicates
Extends the existing FeeOnlinePaymentTests fixtures (so that class's own tests run once more under this name - harmless).
Covers the crossings the old tests do not: webhook THEN client verify, client verify THEN webhook, the same refund event twice.
Class-pass / coin purchases are covered by tuitionclass.tests (verify twice = one credit); they are paid in coins, not by this gateway.
"""
from decimal import Decimal

from django.urls import reverse

from .models import FeeInvoice, FeePayment
from .tests_fee_gateway import FeeOnlinePaymentTests, _checkout_sig, _event, _refund_event


class FeeDuplicateDeliveryTests(FeeOnlinePaymentTests):
    def _verify(self, order="order_F1", pay="pay_FEE1"):
        self.client.force_authenticate(user=self.student_user)
        return self.client.post(reverse("fee-payment-online-verify"), {
            "razorpay_order_id": order, "razorpay_payment_id": pay,
            "razorpay_signature": _checkout_sig(order, pay)}, format="json")

    def _assert_paid_once(self):
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PAID)
        self.assertEqual(self.invoice.amount_paid, Decimal("15000.00"))
        self.assertEqual(FeePayment.objects.filter(invoice=self.invoice, status="success").count(), 1)

    def test_webhook_then_client_verify_does_not_pay_twice(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        calls = self.dispatch.call_count
        r = self._verify()
        self.assertEqual(r.status_code, 200, r.data)  # ALREADY settled: 200, not 201
        self._assert_paid_once()
        self.assertEqual(self.dispatch.call_count, calls)  # no second receipt / push

    def test_client_verify_then_webhook_does_not_pay_twice(self):
        self._order()
        r = self._verify()
        self.assertEqual(r.status_code, 201, r.data)
        calls = self.dispatch.call_count
        for name in ("payment.captured", "order.paid"):
            self.assertEqual(self._hook(_event(name, "order_F1", 1500000)).status_code, 200)
        self._assert_paid_once()
        self.assertEqual(self.dispatch.call_count, calls)

    def test_same_refund_event_twice_refunds_once(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        for _ in range(2):
            self.assertEqual(self._hook(_refund_event("pay_FEE1", 1500000)).status_code, 200)
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "refunded")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount_paid, Decimal("0.00"))
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PENDING)
