# campus/tests_parent_dashboard.py
"""C3 — parent home dashboard + "fee payment received" push.
Run: `python manage.py test campus.tests_parent_dashboard` (NOT RUN when written)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from .bridge import NotifTypes
from .fee_paid_push import notify_fee_paid
from .models import Attendance, CampusParentLink, FeeInvoice, FeePayment, FeeStructure
from .tests_t4_efg import T4Base

User = get_user_model()


class ParentDashboardTests(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.today = timezone.localdate()
        self.url = reverse("campus-parent-dashboard")
        self.client.force_authenticate(self.parent)

    def _child(self, resp, student):
        rows = [c for c in resp.data["children"] if c["student_id"] == str(student.pk)]
        self.assertEqual(len(rows), 1, resp.data)
        return rows[0]

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_only_my_linked_children(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual([c["student_id"] for c in r.data["children"]], [str(self.sa1.pk)])
        self.assertEqual(r.data["totals"]["children"], 1)

    def test_other_user_sees_nothing(self):
        self.client.force_authenticate(self.sa2)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["children"], [])

    def test_multiple_children(self):
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sa2)
        r = self.client.get(self.url)
        self.assertEqual({c["student_id"] for c in r.data["children"]}, {str(self.sa1.pk), str(self.sa2.pk)})

    def test_not_marked_today(self):
        c = self._child(self.client.get(self.url), self.sa1)
        self.assertEqual(c["attendance_today"], "not_marked")
        self.assertIsNone(c["attendance_month_percent"])
        self.assertEqual(c["section"], "10 A")

    def test_absent_wins_over_present_across_registers(self):
        Attendance.objects.create(enrollment=self.e_a1, date=self.today, status="present", marked_by=self.teacher)
        Attendance.objects.create(enrollment=self.e_a1, date=self.today, subject=self.math, status="absent", marked_by=self.teacher)
        r = self.client.get(self.url)
        c = self._child(r, self.sa1)
        self.assertEqual(c["attendance_today"], "absent")
        self.assertEqual(r.data["totals"]["absent_today"], 1)

    def test_yesterday_is_not_today(self):
        Attendance.objects.create(enrollment=self.e_a1, date=self.today - timedelta(days=1), status="absent", marked_by=self.teacher)
        c = self._child(self.client.get(self.url), self.sa1)
        self.assertEqual(c["attendance_today"], "not_marked")

    def test_fees_due_is_balance_after_successful_payments(self):
        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="T1", amount=Decimal("1000"),
            due_date=self.today + timedelta(days=5),
        )
        inv = FeeInvoice.objects.create(enrollment=self.e_a1, fee_structure=fs, amount_due=Decimal("1000"))
        FeePayment.objects.create(
            invoice=inv, amount=Decimal("400"), payer_role="parent", payment_mode="cash",
            status=FeePayment.Status.SUCCESS, paid_by=self.parent,
        )
        FeePayment.objects.create(  # a PENDING online attempt must not reduce the balance
            invoice=inv, amount=Decimal("600"), payer_role="parent", payment_mode="online",
            status=FeePayment.Status.PENDING, gateway_reference="order_X",
        )
        inv.recompute_status()
        c = self._child(self.client.get(self.url), self.sa1)
        self.assertEqual(c["fees_due"], "600.00")
        self.assertEqual(c["fees_open_invoices"], 1)

    def test_paid_invoice_not_counted(self):
        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="T1", amount=Decimal("500"),
            due_date=self.today + timedelta(days=5),
        )
        inv = FeeInvoice.objects.create(enrollment=self.e_a1, fee_structure=fs, amount_due=Decimal("500"))
        FeePayment.objects.create(
            invoice=inv, amount=Decimal("500"), payer_role="parent", payment_mode="cash",
            status=FeePayment.Status.SUCCESS, paid_by=self.parent,
        )
        inv.recompute_status()
        c = self._child(self.client.get(self.url), self.sa1)
        self.assertEqual(c["fees_due"], "0.00")
        self.assertEqual(c["fees_open_invoices"], 0)

    def test_invalid_campus_param(self):
        self.assertEqual(self.client.get(self.url, {"campus": "nope"}).status_code, 400)

    def test_campus_filter(self):
        r = self.client.get(self.url, {"campus": str(self.campus.id)})
        self.assertEqual(len(r.data["children"]), 1)


class FeePaidPushTests(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term 1", amount=Decimal("1000"),
            due_date=date.today() + timedelta(days=10),
        )
        self.inv = FeeInvoice.objects.create(enrollment=self.e_a1, fee_structure=fs, amount_due=Decimal("1000"))

    def _pay(self, amount, status_=FeePayment.Status.SUCCESS, mode="online", ref=""):
        p = FeePayment.objects.create(
            invoice=self.inv, amount=Decimal(amount), payer_role="parent", payment_mode=mode,
            status=FeePayment.Status.PENDING, paid_by=self.parent, gateway_reference=ref,
        )
        if status_ == FeePayment.Status.SUCCESS:
            p.mark_success()
        return p

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_student_and_parent_pushed_once(self, notify, devices):
        p = self._pay("400", ref="order_A")
        self.assertEqual(notify_fee_paid(p.pk), 2)
        kw = notify.call_args.kwargs
        self.assertEqual(set(kw["users"]), {self.sa1.pk, self.parent.pk})
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_FEE_PAID)
        self.assertTrue(kw["push"])
        self.assertEqual(kw["data"]["student_id"], str(self.sa1.pk))
        self.assertIn("Balance", kw["body"])
        # webhook + client-verify both settle -> second call must not push again
        self.assertEqual(notify_fee_paid(p.pk), 0)
        notify.assert_called_once()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_pending_payment_not_pushed(self, notify, devices):
        p = self._pay("400", status_=FeePayment.Status.PENDING, ref="order_B")
        self.assertEqual(notify_fee_paid(p.pk), 0)
        notify.assert_not_called()

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify")
    def test_fully_paid_body(self, notify, devices):
        p = self._pay("1000", ref="order_C")
        notify_fee_paid(p.pk)
        self.assertIn("Fully paid", notify.call_args.kwargs["body"])

    @mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
    @mock.patch("campus.bridge.notify", side_effect=RuntimeError("boom"))
    def test_failure_never_raises_and_retries(self, notify, devices):
        p = self._pay("400", ref="order_D")
        self.assertEqual(notify_fee_paid(p.pk), 0)  # swallowed
        notify.side_effect = None
        self.assertEqual(notify_fee_paid(p.pk), 2)  # marker was rolled back -> retried
