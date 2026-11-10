"""R9 / N6b - purchase receipts (coin top-up) + shared receipt copy + the type's rules (locked, uncapped, route).
(NOT RUN.)  python manage.py test core.tests_notification_receipts"""
from datetime import datetime, timezone as dt_tz
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import override_settings

from . import notification_priority as prio
from . import notification_receipts as receipts
from . import notification_reminders as rem
from .models import Notification, NotificationPreference
from .notification_routes import route_for
from .tests import CoreTestBase

NT = Notification.NotifType
WHEN = datetime(2026, 10, 26, 12, 0, tzinfo=dt_tz.utc)


class ReceiptCopyTests(CoreTestBase):
    def test_amount_formatting(self):
        self.assertEqual(receipts.fmt_amount(500), "500")
        self.assertEqual(receipts.fmt_amount(Decimal("499.50")), "499.50")
        self.assertEqual(receipts.fmt_amount(Decimal("500.00")), "500")

    def test_every_kind_uses_the_same_receipt_format(self):
        c_title, c_body = receipts.receipt_copy(receipts.KIND_COINS, number="LS-R-1", amount=Decimal("99"), coins=100)
        p_title, p_body = receipts.receipt_copy(
            receipts.KIND_PASS, number="LS-P-1", coins=50, what="DSA Batch", valid_until=WHEN,
        )
        f_title, f_body = receipts.receipt_copy(
            receipts.KIND_FEE, number="RCPT-9", amount=Decimal("1500.00"), what="Term 1", who="Asha", balance=0,
        )
        for title in (c_title, p_title, f_title):
            self.assertTrue(title.startswith("Receipt: "), title)
        for body, number in ((c_body, "LS-R-1"), (p_body, "LS-P-1"), (f_body, "RCPT-9")):
            self.assertTrue(body.endswith(f"Receipt {number}."), body)
        self.assertIn("100 coins", c_title)
        self.assertIn("Rs. 99", c_body)
        self.assertIn("DSA Batch", p_body)
        self.assertIn("26 Oct 2026", p_body)
        self.assertIn("Rs. 1500 received towards 'Term 1' for Asha.", f_body)
        self.assertIn("Fully paid.", f_body)

    def test_fee_copy_shows_balance_when_unpaid(self):
        _, body = receipts.receipt_copy(
            receipts.KIND_FEE, number="RCPT-1", amount=500, what="Term 1", who="Asha", balance=Decimal("250"),
        )
        self.assertIn("Balance: Rs. 250.", body)
        self.assertNotIn("Fully paid", body)

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            receipts.receipt_copy("nope", number="x")

    def test_refs_are_distinct_per_kind(self):
        self.assertEqual(receipts.ref_for(receipts.KIND_COINS, 7), "coin_purchase_request:7")
        self.assertEqual(receipts.ref_for(receipts.KIND_PASS, 7), "pass_purchase:7")
        self.assertEqual(receipts.ref_for(receipts.KIND_FEE, 7), "campus_fee_payment:7")


class ReceiptTypeRulesTests(CoreTestBase):
    def test_type_is_locked_urgent_and_in_payments_group(self):
        value = NT.PURCHASE_RECEIPT.value
        self.assertIn(value, Notification.locked_types())
        self.assertIn(value, NotificationPreference.NEVER_MUTE_TYPES)
        self.assertEqual(prio.priority_for(value), prio.URGENT)
        self.assertIn(value, Notification.preference_groups()["payments"])

    def test_receipt_is_never_a_reminder_never_capped_never_snoozable(self):
        value = NT.PURCHASE_RECEIPT.value
        self.assertNotIn(value, rem.reminder_types())
        self.assertNotIn(value, rem.gated_types())
        with override_settings(NOTIF_REMINDER_DAILY_CAP=1, NOTIF_REMINDER_COLLAPSE_MINUTES=240):
            cache.clear()
            self.addCleanup(cache.clear)
            self.assertEqual([rem.claim_slot(self.student.id, value) for _ in range(5)], [None] * 5)

    def test_route_is_wallet(self):
        self.assertEqual(route_for(NT.PURCHASE_RECEIPT.value, {}).route, "wallet")

    def test_why_am_i_seeing_this_is_bilingual_and_locked(self):
        from . import notification_center as center

        why = center.why_for(Notification(recipient=self.student, notif_type=NT.PURCHASE_RECEIPT.value, title="t"))
        self.assertEqual(why["code"], "receipt")
        self.assertTrue(why["en"].strip() and why["hi"].strip())
        self.assertEqual((why["group"], why["locked"], why["control"]), ("payments", True, "locked"))


class CoinReceiptTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def _confirmed(self, ref="gw-1"):
        from user_profile.models import CoinPurchaseRequest

        CoinPurchaseRequest.objects.start_purchase(
            user=self.student, gateway_reference=ref, amount=Decimal("99.00"), coins=100, gateway="razorpay",
        )
        with patch("core.notification_receipts.queue_receipt"):
            return CoinPurchaseRequest.objects.confirm_success(gateway_reference=ref, gateway="razorpay")

    def test_confirm_success_queues_one_receipt_and_a_retry_queues_none(self):
        from user_profile.models import CoinPurchaseRequest

        CoinPurchaseRequest.objects.start_purchase(
            user=self.student, gateway_reference="gw-q", amount=Decimal("99.00"), coins=100, gateway="razorpay",
        )
        with patch("core.notification_receipts.queue_receipt") as queued:
            first = CoinPurchaseRequest.objects.confirm_success(gateway_reference="gw-q", gateway="razorpay")
            CoinPurchaseRequest.objects.confirm_success(gateway_reference="gw-q", gateway="razorpay")  # webhook retry
        queued.assert_called_once_with(receipts.KIND_COINS, first.pk)

    def test_a_receipt_failure_never_fails_the_payment(self):
        from user_profile.models import CoinPurchaseRequest

        CoinPurchaseRequest.objects.start_purchase(
            user=self.student, gateway_reference="gw-boom", amount=Decimal("10.00"), coins=10, gateway="razorpay",
        )
        with patch("core.notification_receipts.queue_receipt", side_effect=RuntimeError("broker down")):
            purchase = CoinPurchaseRequest.objects.confirm_success(gateway_reference="gw-boom", gateway="razorpay")
        self.assertEqual(purchase.status, CoinPurchaseRequest.Status.SUCCESS)

    def test_notify_creates_one_locked_row_with_receipt_data_and_pushes(self):
        purchase = self._confirmed()
        with patch("core.tasks.push_for_notification_row") as push:
            self.assertTrue(receipts.notify_coin_purchase(purchase.pk))
        row = Notification.objects.get(recipient=self.student, notif_type=NT.PURCHASE_RECEIPT)
        self.assertTrue(row.title.startswith("Receipt: 100 coins"))
        self.assertEqual(row.data["ref"], f"coin_purchase_request:{purchase.pk}")
        self.assertEqual(row.data["kind"], "coins")
        self.assertEqual(row.data["coins"], 100)
        self.assertTrue(row.data["receipt_no"].startswith("LS-R-"))
        push.assert_called_once()

    def test_notify_is_idempotent_even_after_the_cache_is_flushed(self):
        purchase = self._confirmed("gw-2")
        with patch("core.tasks.push_for_notification_row") as push:
            receipts.notify_coin_purchase(purchase.pk)
            receipts.notify_coin_purchase(purchase.pk)
            cache.clear()  # redis flush: the DB row is still the source of truth
            receipts.notify_coin_purchase(purchase.pk)
        self.assertEqual(Notification.objects.filter(notif_type=NT.PURCHASE_RECEIPT).count(), 1)
        self.assertEqual(push.call_count, 1)

    def test_pending_or_failed_purchase_gets_no_receipt(self):
        from user_profile.models import CoinPurchaseRequest

        pending, _ = CoinPurchaseRequest.objects.start_purchase(
            user=self.student, gateway_reference="gw-p", amount=Decimal("5.00"), coins=5, gateway="razorpay",
        )
        with patch("core.tasks.push_for_notification_row") as push:
            self.assertTrue(receipts.notify_coin_purchase(pending.pk))
        self.assertFalse(Notification.objects.filter(notif_type=NT.PURCHASE_RECEIPT).exists())
        push.assert_not_called()

    def test_missing_purchase_is_not_retried(self):
        self.assertTrue(receipts.notify_coin_purchase(999999))

    def test_failed_row_creation_releases_the_marker_so_the_task_can_retry(self):
        purchase = self._confirmed("gw-3")
        with patch("core.tasks.create_notification_rows", return_value=[self.student.id]):
            self.assertFalse(receipts.notify_coin_purchase(purchase.pk))
        with patch("core.tasks.push_for_notification_row"):
            self.assertTrue(receipts.notify_coin_purchase(purchase.pk))
        self.assertEqual(Notification.objects.filter(notif_type=NT.PURCHASE_RECEIPT).count(), 1)

    def test_deliver_dispatches_by_kind_and_ignores_unknown(self):
        # `_LOADERS` holds the function objects bound at import, so patching the module name alone is not seen
        coins = MagicMock(return_value=True)
        with patch.dict(receipts._LOADERS, {receipts.KIND_COINS: coins}):
            self.assertTrue(receipts.deliver(receipts.KIND_COINS, 5))
        coins.assert_called_once_with(5)
        self.assertTrue(receipts.deliver("unknown", 5))

    def test_receipt_row_cannot_be_muted_or_snoozed(self):
        row = Notification.objects.create(
            recipient=self.student, notif_type=NT.PURCHASE_RECEIPT, title="t", message="m", data={"ref": "x"},
        )
        with self.assertRaises(rem.ReminderActionError):
            rem.snooze_reminder(row)
        with self.assertRaises(rem.ReminderActionError) as ctx:
            rem.turn_off_type(self.student, NT.PURCHASE_RECEIPT.value)
        self.assertEqual(ctx.exception.code, "locked")


class QueueReceiptTests(CoreTestBase):
    def test_queue_uses_after_commit_dispatch_and_never_raises(self):
        with patch("core.async_utils.dispatch_after_commit") as dispatch:
            receipts.queue_receipt(receipts.KIND_COINS, 3)
        # P-G2: a coin receipt queues TWO tasks - the bell/push receipt and the e-mailed PDF receipt
        self.assertEqual(dispatch.call_count, 2)
        self.assertEqual(dispatch.call_args_list[0].args[1:], (receipts.KIND_COINS, 3))
        self.assertEqual(dispatch.call_args_list[1].args[1:], (3,))
        with patch("core.async_utils.dispatch_after_commit") as dispatch_pass:
            receipts.queue_receipt(receipts.KIND_PASS, 4)  # only coins also send the e-mail
        dispatch_pass.assert_called_once()
        with patch("core.async_utils.dispatch_after_commit", side_effect=RuntimeError("x")):
            receipts.queue_receipt(receipts.KIND_COINS, 3)  # must not raise
