# campus/tests_fee_gateway.py
"""FEE-5 — online fee payment (Razorpay UPI / card), my-summary and the receipt of an online payment.
The Razorpay order call is patched (`campus.fee_gateway.create_razorpay_order`); no network."""
import hashlib
import hmac
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import FeeInvoice, FeePayment, FeeStructure
from .tests import make_full_fixture

KEY_SECRET = "rzp_secret_test"
WH_SECRET = "whsec_fee_test"


def _checkout_sig(order_id, pay_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{pay_id}".encode(), hashlib.sha256).hexdigest()


def _event(name, order_id, amount_paise, currency="INR", pay_id="pay_FEE1", method="upi", status_="captured"):
    ent = {"id": pay_id, "order_id": order_id, "amount": amount_paise, "currency": currency,
           "status": status_, "method": method}
    return json.dumps({"event": name, "payload": {"payment": {"entity": ent}}}).encode()


def _refund_event(pay_id, amount_paise):
    return json.dumps({"event": "refund.processed", "payload": {
        "refund": {"entity": {"id": "rfnd_1", "payment_id": pay_id, "amount": amount_paise}},
        "payment": {"entity": {"id": pay_id}},
    }}).encode()


@override_settings(
    RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET=KEY_SECRET,
    PAYMENT_GATEWAY_WEBHOOK_SECRETS={"razorpay": WH_SECRET},
    PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS={"razorpay": "X-Razorpay-Signature"},
)
class FeeOnlinePaymentTests(APITestCase):
    def setUp(self):
        cache.clear()
        make_full_fixture(self)
        self.campus.fee_module_enabled = True
        self.campus.save(update_fields=["fee_module_enabled"])
        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1 Tuition",
            amount=Decimal("15000"), due_date=date.today() + timedelta(days=30),
        )
        self.invoice = FeeInvoice.objects.create(
            enrollment=self.enrollment, fee_structure=fs, amount_due=Decimal("15000"))
        self.patcher = mock.patch("campus.fee_gateway.dispatch_after_commit")
        self.dispatch = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    # ------------------------------------------------------------ helpers
    def _order(self, user=None, order_id="order_F1", **body):
        self.client.force_authenticate(user=user or self.student_user)
        data = {"invoice": str(self.invoice.id), **body}
        with mock.patch("campus.fee_gateway.create_razorpay_order", return_value=order_id) as m:
            r = self.client.post(reverse("fee-payment-online-order"), data, format="json")
        return r, m

    def _hook(self, body, sig=None):
        sig = hmac.new(WH_SECRET.encode(), body, hashlib.sha256).hexdigest() if sig is None else sig
        return self.client.post(reverse("campus-fee-razorpay-webhook"), data=body,
                                content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=sig)

    # ------------------------------------------------------------ order
    def test_order_creates_pending_online_payment_priced_by_server(self):
        r, m = self._order()
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(r.data["order_id"], "order_F1")
        self.assertEqual(r.data["amount_paise"], 1500000)
        self.assertEqual(r.data["key_id"], "rzp_test_key")
        self.assertEqual(r.data["methods"], ["upi", "card"])
        self.assertEqual(m.call_args.kwargs["amount_paise"], 1500000)
        p = FeePayment.objects.get(gateway_reference="order_F1")
        self.assertEqual(p.status, FeePayment.Status.PENDING)
        self.assertEqual(p.payment_mode, FeePayment.Mode.ONLINE)
        self.assertEqual(p.payer_role, FeePayment.PayerRole.STUDENT)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PENDING)  # nothing paid yet

    def test_partial_amount_with_paise_allowed_but_not_more_than_owed(self):
        r, _ = self._order(amount="2500.50")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["amount_paise"], 250050)
        r2, _ = self._order(order_id="order_F2", amount="15000.01")
        self.assertEqual(r2.status_code, 400)
        self.assertEqual(r2.data["code"], "amount_too_high")

    def test_outsider_cannot_start_payment(self):
        r, m = self._order(user=self.outsider)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
        m.assert_not_called()

    def test_fee_module_off_blocks_order(self):
        self.campus.fee_module_enabled = False
        self.campus.save(update_fields=["fee_module_enabled"])
        r, _ = self._order()
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(CAMPUS_FEE_ONLINE_ENABLED=False)
    def test_online_can_be_switched_off(self):
        r, m = self._order()
        self.assertEqual(r.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        m.assert_not_called()

    def test_gateway_failure_is_502_and_leaves_no_row(self):
        from user_profile.payments import GatewayError
        self.client.force_authenticate(user=self.student_user)
        with mock.patch("campus.fee_gateway.create_razorpay_order", side_effect=GatewayError("down")):
            r = self.client.post(reverse("fee-payment-online-order"), {"invoice": str(self.invoice.id)}, format="json")
        self.assertEqual(r.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(FeePayment.objects.filter(payment_mode="online").exists())

    def test_already_paid_invoice_cannot_be_paid_again(self):
        FeePayment.objects.create(invoice=self.invoice, amount=Decimal("15000"), payer_role="admin",
                                  payment_mode="cash", status="success", recorded_by=self.admin)
        r, _ = self._order()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "already_paid")

    def test_pending_orders_are_capped(self):
        with override_settings(CAMPUS_FEE_ONLINE_MAX_PENDING=2):
            for i in range(2):
                r, _ = self._order(order_id=f"order_C{i}")
                self.assertEqual(r.status_code, 201)
            r, _ = self._order(order_id="order_C9")
            self.assertEqual(r.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # ------------------------------------------------------------ verify (client)
    def test_verify_with_good_signature_settles_once(self):
        self._order()
        body = {"razorpay_order_id": "order_F1", "razorpay_payment_id": "pay_X1",
                "razorpay_signature": _checkout_sig("order_F1", "pay_X1")}
        r = self.client.post(reverse("fee-payment-online-verify"), body, format="json")
        self.assertEqual(r.status_code, status.HTTP_201_CREATED, r.data)
        self.assertEqual(r.data["status"], "success")
        self.assertEqual(r.data["gateway_payment_id"], "pay_X1")
        self.assertIsNotNone(r.data["paid_at"])
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PAID)
        self.assertEqual(self.dispatch.call_count, 2)  # receipt e-mail + fee-paid push
        self.assertEqual(
            {c.args[0].__name__ for c in self.dispatch.call_args_list},
            {"send_fee_receipt_email", "send_fee_paid_notification"},
        )
        # a retry is a no-op
        r2 = self.client.post(reverse("fee-payment-online-verify"), body, format="json")
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(self.dispatch.call_count, 2)  # unchanged: no second email/push

    def test_verify_with_bad_signature_does_not_settle(self):
        self._order()
        r = self.client.post(reverse("fee-payment-online-verify"), {
            "razorpay_order_id": "order_F1", "razorpay_payment_id": "pay_X1", "razorpay_signature": "nope",
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "pending")
        self.dispatch.assert_not_called()

    def test_verify_someone_elses_order_is_404(self):
        self._order()
        self.client.force_authenticate(user=self.outsider)
        r = self.client.post(reverse("fee-payment-online-verify"), {
            "razorpay_order_id": "order_F1", "razorpay_payment_id": "pay_X1",
            "razorpay_signature": _checkout_sig("order_F1", "pay_X1"),
        }, format="json")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    # ------------------------------------------------------------ webhook
    def test_webhook_settles_and_is_idempotent_across_events(self):
        self._order()
        for name in ("payment.captured", "order.paid", "payment.captured"):
            r = self._hook(_event(name, "order_F1", 1500000))
            self.assertEqual(r.status_code, 200)
        p = FeePayment.objects.get(gateway_reference="order_F1")
        self.assertEqual(p.status, "success")
        self.assertEqual(p.gateway_method, "upi")
        self.assertEqual(p.gateway_payment_id, "pay_FEE1")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PAID)
        self.assertEqual(self.invoice.amount_paid, Decimal("15000.00"))
        self.assertEqual(self.dispatch.call_count, 2)  # receipt e-mail + fee-paid push

    def test_webhook_partial_payment_makes_invoice_partial(self):
        self._order(amount="5000")
        self._hook(_event("payment.captured", "order_F1", 500000, method="card"))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PARTIAL)

    def test_webhook_amount_mismatch_does_not_settle(self):
        self._order()
        r = self._hook(_event("payment.captured", "order_F1", 100))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["settled"], False)
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "pending")
        self.dispatch.assert_not_called()

    def test_webhook_bad_signature_401_and_unknown_order_ignored(self):
        self._order()
        self.assertEqual(self._hook(_event("payment.captured", "order_F1", 1500000), sig="bad").status_code, 401)
        r = self._hook(_event("payment.captured", "order_OTHER", 100))
        self.assertEqual(r.status_code, 200)  # not ours (e.g. a coin top-up) -> no retries
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "pending")

    def test_payment_failed_event_is_ignored_so_a_later_success_still_settles(self):
        self._order()
        self._hook(_event("payment.failed", "order_F1", 1500000, status_="failed"))
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "pending")
        self._hook(_event("payment.captured", "order_F1", 1500000))
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "success")

    def test_full_refund_event_marks_refunded_and_reopens_invoice(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        r = self._hook(_refund_event("pay_FEE1", 1500000))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "refunded")
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, FeeInvoice.Status.PENDING)

    def test_partial_refund_event_is_left_for_a_human(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        self._hook(_refund_event("pay_FEE1", 100))
        self.assertEqual(FeePayment.objects.get(gateway_reference="order_F1").status, "success")

    def test_admin_refund_endpoint_explains_online_refunds_use_dashboard(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        p = FeePayment.objects.get(gateway_reference="order_F1")
        self.client.force_authenticate(user=self.admin)
        r = self.client.post(reverse("fee-payment-refund", args=[p.id]))
        self.assertEqual(r.status_code, 400)
        self.assertIn("Razorpay", r.data["detail"])

    # ------------------------------------------------------------ receipt
    def test_receipt_pdf_of_online_payment(self):
        self._order()
        self._hook(_event("payment.captured", "order_F1", 1500000))
        p = FeePayment.objects.get(gateway_reference="order_F1")
        self.client.force_authenticate(user=self.student_user)
        r = self.client.get(reverse("fee-payment-receipt-pdf", args=[p.id]))
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.assertEqual(r.status_code, status.HTTP_501_NOT_IMPLEMENTED)
            return
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))

    def test_pending_online_payment_has_no_receipt(self):
        self._order()
        p = FeePayment.objects.get(gateway_reference="order_F1")
        r = self.client.get(reverse("fee-payment-receipt-pdf", args=[p.id]))
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------ my-summary ("fees dikhe")
    def test_my_summary_for_student_and_totals(self):
        FeePayment.objects.create(invoice=self.invoice, amount=Decimal("5000"), payer_role="admin",
                                  payment_mode="cash", status="success", recorded_by=self.admin)
        self.invoice.recompute_status()
        self._order()  # an unfinished checkout must not show up in the list
        r = self.client.get(reverse("fee-invoice-my-summary"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(r.data["invoices"]), 1)
        inv = r.data["invoices"][0]
        self.assertEqual(inv["amount_paid"], "5000.00")
        self.assertEqual(inv["balance"], "10000.00")
        self.assertEqual(inv["display_status"], "partial")
        self.assertTrue(inv["can_pay_online"])
        self.assertEqual(len(inv["payments"]), 1)
        self.assertTrue(inv["payments"][0]["receipt_url"].endswith("/receipt-pdf/"))
        self.assertEqual(r.data["totals"]["balance"], "10000.00")
        self.assertTrue(r.data["online_enabled"])

    def test_my_summary_marks_unpaid_past_due_as_overdue_live(self):
        fs = self.invoice.fee_structure
        fs.due_date = date.today() - timedelta(days=3)
        fs.save(update_fields=["due_date"])
        self.client.force_authenticate(user=self.student_user)
        r = self.client.get(reverse("fee-invoice-my-summary"))
        self.assertEqual(r.data["invoices"][0]["display_status"], "overdue")
        self.assertEqual(r.data["totals"]["overdue"], "15000.00")

    def test_my_summary_hides_other_students_invoices(self):
        self.client.force_authenticate(user=self.outsider)
        r = self.client.get(reverse("fee-invoice-my-summary"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["invoices"], [])
