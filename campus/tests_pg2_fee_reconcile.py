"""P-G2 (NOT RUN): python manage.py test campus.tests_pg2_fee_reconcile
Campus fee online payment whose webhook was lost is settled by the reconcile job; mismatch is not; expiry; duplicates."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from common import payment_reconcile as pr

from .models import FeeInvoice, FeePayment, FeeStructure
from .tests import make_full_fixture


@override_settings(PAYMENT_RECONCILE_MIN_AGE_MINUTES=10, PAYMENT_RECONCILE_MAX_AGE_DAYS=7, PAYMENT_RECONCILE_EXPIRE_HOURS=24)
class FeeReconcileTests(APITestCase):
    def setUp(self):
        cache.clear()
        make_full_fixture(self)
        fs = FeeStructure.objects.create(campus=self.campus, session=self.session, title="Term 1",
                                         amount=Decimal("1000"), due_date=date.today() + timedelta(days=30))
        self.invoice = FeeInvoice.objects.create(enrollment=self.enrollment, fee_structure=fs, amount_due=Decimal("1000"))
        p = mock.patch("campus.fee_gateway.dispatch_after_commit")
        p.start()
        self.addCleanup(p.stop)

    def _pending(self, order="order_FR1", age_minutes=30, amount="1000"):
        pay = FeePayment.objects.create(
            invoice=self.invoice, amount=Decimal(amount), paid_by=self.student_user, payer_role="student",
            payment_mode=FeePayment.Mode.ONLINE, status=FeePayment.Status.PENDING, gateway_reference=order)
        FeePayment.objects.filter(pk=pay.pk).update(created_at=timezone.now() - timedelta(minutes=age_minutes))
        return pay

    def test_lost_webhook_settles_once_and_invoice_becomes_paid(self):
        pay = self._pending()
        fetch = lambda o: [{"id": "pay_F", "status": "captured", "amount": 100000, "currency": "INR", "method": "upi"}]  # noqa: E731
        s1 = pr.reconcile_fee_payments(fetch=fetch)
        s2 = pr.reconcile_fee_payments(fetch=fetch)
        pay.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual((s1["settled"], s2["checked"]), (1, 0))
        self.assertEqual(pay.status, FeePayment.Status.SUCCESS)
        self.assertEqual(self.invoice.amount_paid, Decimal("1000"))

    def test_mismatch_is_not_settled(self):
        pay = self._pending()
        with self.assertLogs("campus.fee_gateway", level="ERROR"):
            s = pr.reconcile_fee_payments(fetch=lambda o: [
                {"id": "pay_F", "status": "captured", "amount": 50000, "currency": "INR", "method": "upi"}])
        pay.refresh_from_db()
        self.assertEqual((s["mismatch"], pay.status), (1, FeePayment.Status.PENDING))

    def test_expiry_and_dry_run(self):
        pay = self._pending(age_minutes=60 * 30)
        s = pr.reconcile_fee_payments(fetch=lambda o: [], dry_run=True)
        pay.refresh_from_db()
        self.assertEqual((s["expired"], pay.status), (1, FeePayment.Status.PENDING))
        s = pr.reconcile_fee_payments(fetch=lambda o: [])
        pay.refresh_from_db()
        self.assertEqual((s["expired"], pay.status), (1, FeePayment.Status.FAILED))

    def test_gateway_error_is_counted(self):
        self._pending()

        def boom(o):
            raise pr.ReconcileGatewayError("down")

        s = pr.reconcile_fee_payments(fetch=boom)
        self.assertEqual((s["gateway_errors"], s["settled"]), (1, 0))
