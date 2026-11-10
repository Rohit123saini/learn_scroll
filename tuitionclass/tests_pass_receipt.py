"""R9 / N6b - pass purchase receipt (the paid pass accept path + the receipt row). (NOT RUN.)
python manage.py test tuitionclass.tests_pass_receipt"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.utils import timezone

from core import notification_receipts as receipts
from core.models import Notification

from .models import PassPurchase
from .tests import TuitionClassTestBase

NT = Notification.NotifType


class PassReceiptTests(TuitionClassTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def _purchase(self, coins=100, status=PassPurchase.Status.SUCCESS):
        return PassPurchase.objects.create(
            student=self.student, class_pass=self.class_pass, amount_paid=Decimal(coins), coins_spent=coins,
            status=status, is_active=True, expires_at=timezone.now() + timedelta(days=10),
        )

    def test_paid_pass_gets_one_receipt_row(self):
        purchase = self._purchase()
        with patch("core.tasks.push_for_notification_row") as push:
            self.assertTrue(receipts.notify_pass_purchase(purchase.pk))
            receipts.notify_pass_purchase(purchase.pk)  # double call
        rows = Notification.objects.filter(recipient=self.student, notif_type=NT.PURCHASE_RECEIPT)
        self.assertEqual(rows.count(), 1)
        row = rows.get()
        self.assertEqual(row.title, "Receipt: pass purchased")
        self.assertIn("DSA Batch", row.message)
        self.assertTrue(row.message.endswith(f"Receipt {row.data['receipt_no']}."))
        self.assertEqual(row.data["ref"], f"pass_purchase:{purchase.pk}")
        self.assertEqual(row.data["pass_purchase_id"], str(purchase.pk))
        push.assert_called_once()

    def test_free_pass_has_nothing_to_receipt(self):
        purchase = self._purchase(coins=0)
        with patch("core.tasks.push_for_notification_row") as push:
            self.assertTrue(receipts.notify_pass_purchase(purchase.pk))
        self.assertFalse(Notification.objects.filter(notif_type=NT.PURCHASE_RECEIPT).exists())
        push.assert_not_called()

    def test_refunded_or_pending_purchase_gets_no_receipt(self):
        purchase = self._purchase(status=PassPurchase.Status.PENDING)
        self.assertTrue(receipts.notify_pass_purchase(purchase.pk))
        self.assertFalse(Notification.objects.filter(notif_type=NT.PURCHASE_RECEIPT).exists())

    def test_charge_and_create_purchase_queues_the_receipt_for_paid_passes_only(self):
        from .views.passes import _charge_and_create_purchase

        with patch("core.notification_receipts.queue_receipt") as queued:
            paid = _charge_and_create_purchase(self.student, self.class_pass, None)
        queued.assert_called_once_with(receipts.KIND_PASS, paid.pk)

        self.class_pass.price = Decimal("0")
        self.class_pass.save(update_fields=["price"])
        with patch("core.notification_receipts.queue_receipt") as queued:
            _charge_and_create_purchase(self.student, self.class_pass, None)
        queued.assert_not_called()

    def test_a_receipt_failure_never_fails_the_accept(self):
        from .views.passes import _charge_and_create_purchase

        with patch("core.notification_receipts.queue_receipt", side_effect=RuntimeError("down")):
            purchase = _charge_and_create_purchase(self.student, self.class_pass, None)
        self.assertEqual(purchase.status, PassPurchase.Status.SUCCESS)
