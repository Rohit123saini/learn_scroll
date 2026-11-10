# campus/tests_perf.py
"""[PERF] N+1 regressions for 1000+ student campuses.

Every test measures the SQL query count of a list endpoint with a SMALL and a LARGE data set and asserts the
two are EQUAL — i.e. the cost no longer grows with the number of rows. Plus correctness checks for the
values that moved from per-row queries to annotations / prefetches.

Run:  python manage.py test campus.tests_perf        (NOT RUN when written — no Django in the authoring pass)
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import bridge
from .models import (
    CampusParentLink, FeeConcession, FeeConcessionGrant, FeeInvoice, FeePayment, FeeStructure, StudentEnrollment,
)
from .tests_t4_efg import T4Base, rows

User = get_user_model()


class PerfBase(T4Base):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.admin)
        self._n = 0

    def more_students(self, n, section=None):
        out = []
        for _ in range(n):
            self._n += 1
            u = User.objects.create_user(username=f"perf_s{self._n}", password="pass12345")
            e = StudentEnrollment.objects.create(
                student=u, section=section or self.secA, session=self.session, roll_number=str(100 + self._n))
            out.append((u, e))
        return out

    def measure(self, url, **params):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(url, params)
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        return len(ctx), r


class FeeInvoiceListTests(PerfBase):
    def setUp(self):
        super().setUp()
        self.fs = FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term", amount=Decimal("1000"),
            due_date=timezone.localdate() + timedelta(days=10))

    def _invoice(self, enrollment, paid=("500",), pending=()):
        inv = FeeInvoice.objects.create(enrollment=enrollment, fee_structure=self.fs, amount_due=Decimal("1000"))
        for a in paid:
            FeePayment.objects.create(invoice=inv, amount=Decimal(a), payer_role="admin", payment_mode="cash",
                                      status=FeePayment.Status.SUCCESS, paid_at=timezone.now())
        for a in pending:
            FeePayment.objects.create(invoice=inv, amount=Decimal(a), payer_role="admin", payment_mode="cash",
                                      status=FeePayment.Status.PENDING)
        return inv

    def test_query_count_does_not_grow_with_rows(self):
        self._invoice(self.e_a1, paid=("500", "100"), pending=("300",))
        self._invoice(self.e_a2)
        url = reverse("fee-invoice-list")
        small, _ = self.measure(url, page_size=100)
        for _u, e in self.more_students(15):
            self._invoice(e, paid=("250",), pending=("50",))
        large, r = self.measure(url, page_size=100)
        self.assertEqual(len(rows(r)), 17)
        self.assertEqual(small, large, f"fee-invoice list is O(n): {small} -> {large} queries")

    def test_amount_paid_counts_only_successful_payments(self):
        inv = self._invoice(self.e_a1, paid=("500", "100"), pending=("300",))
        _, r = self.measure(reverse("fee-invoice-list"), page_size=100)
        row = next(x for x in rows(r) if str(x["id"]) == str(inv.id))
        self.assertEqual(Decimal(str(row["amount_paid"])), Decimal("600"))
        self.assertEqual(Decimal(str(row["balance"])), Decimal("400"))

    def test_invoice_without_payments_still_zero(self):
        inv = FeeInvoice.objects.create(enrollment=self.e_a1, fee_structure=self.fs, amount_due=Decimal("1000"))
        _, r = self.measure(reverse("fee-invoice-list"), page_size=100)
        row = next(x for x in rows(r) if str(x["id"]) == str(inv.id))
        self.assertEqual(Decimal(str(row["amount_paid"])), Decimal("0"))
        self.assertEqual(Decimal(str(row["balance"])), Decimal("1000"))

    def test_property_is_not_cached_outside_list(self):
        """recompute / payment flows read the property on freshly loaded rows: never the list annotation."""
        inv = self._invoice(self.e_a1, paid=("500",))
        fresh = FeeInvoice.objects.get(pk=inv.pk)
        self.assertEqual(fresh.amount_paid, Decimal("500"))
        FeePayment.objects.create(invoice=inv, amount=Decimal("200"), payer_role="admin", payment_mode="cash",
                                  status=FeePayment.Status.SUCCESS, paid_at=timezone.now())
        self.assertEqual(fresh.amount_paid, Decimal("700"))  # no stale cache on a normal instance


class EnrollmentAndParentLinkTests(PerfBase):
    def test_enrollment_list_constant_queries(self):
        url = reverse("student-enrollment-list")
        small, _ = self.measure(url, page_size=100)
        self.more_students(20)
        large, r = self.measure(url, page_size=100)
        self.assertEqual(len(rows(r)), 23)
        self.assertEqual(small, large, f"enrollment list is O(n): {small} -> {large}")

    def test_parent_links_constant_queries_and_active_enrollment(self):
        url = reverse("campus-parent-link-list")
        small, r0 = self.measure(url, page_size=100)
        link = next(x for x in rows(r0) if str(x["student"]) == str(self.sa1.id))
        self.assertEqual(str(link["active_enrollment"]["section_id"]), str(self.secA.id))
        for i, (u, _e) in enumerate(self.more_students(12)):
            p = User.objects.create_user(username=f"perf_p{i}", password="pass12345")
            CampusParentLink.objects.create(campus=self.campus, parent=p, student=u)
        large, r = self.measure(url, page_size=100)
        self.assertEqual(len(rows(r)), 13)
        self.assertTrue(all(x["active_enrollment"] for x in rows(r)))
        self.assertEqual(small, large, f"parent-link list is O(n): {small} -> {large}")


class ConcessionListTests(PerfBase):
    def test_grants_count_correct_and_constant_queries(self):
        def mk(name, grants):
            c = FeeConcession.objects.create(campus=self.campus, name=name, kind="percent", value=Decimal("10"))
            for _u, e in self.more_students(grants):
                FeeConcessionGrant.objects.create(concession=c, enrollment=e, granted_by=self.admin)
            return c

        a = mk("Sibling", 3)
        url = reverse("fee-concession-list")
        small, _ = self.measure(url, page_size=100)
        for i in range(6):
            mk(f"C{i}", 1)
        FeeConcessionGrant.objects.filter(concession=a).first().delete()  # 3 -> 2 active
        large, r = self.measure(url, page_size=100)
        by = {x["name"]: x["grants_count"] for x in rows(r)}
        self.assertEqual(by["Sibling"], 2)
        self.assertEqual(by["C0"], 1)
        self.assertEqual(small, large, f"concession list is O(n): {small} -> {large}")


class AssignmentListTests(PerfBase):
    def _assignment(self, i):
        return bridge.create_assigments(
            section=self.secA, subject=self.math, posted_by=self.teacher, title=f"HW {i}",
            due_date=timezone.localdate() + timedelta(days=i + 1))

    def test_assignment_list_constant_queries_and_legacy_bare_list(self):
        self._assignment(0)
        url = reverse("assigments-list")
        small, r0 = self.measure(url)
        self.assertIsInstance(r0.data, list)  # unchanged contract for the shipped app
        for i in range(1, 9):
            self._assignment(i)
        large, r = self.measure(url)
        self.assertEqual(len(r.data), 9)
        self.assertEqual(small, large, f"assignment list is O(n): {small} -> {large}")

    def test_assignment_list_opt_in_pagination(self):
        for i in range(5):
            self._assignment(i)
        _, r = self.measure(reverse("assigments-list"), page=1, page_size=2)
        self.assertEqual(r.data["count"], 5)
        self.assertEqual(len(r.data["results"]), 2)
        self.assertTrue(r.data["next"])

    def test_submission_visibility_still_scoped(self):
        self._assignment(0)
        self.client.force_authenticate(self.sa1)
        r = self.client.get(reverse("assigments-submission-list"))
        self.assertEqual(r.status_code, 200)
        data = rows(r)
        self.assertTrue(data)
        self.assertTrue(all(str(x["student"]) == str(self.sa1.id) for x in data))
        self.client.force_authenticate(self.admin)  # staff of the campus: every student's row
        staff_rows = rows(self.client.get(reverse("assigments-submission-list")))
        self.assertGreater(len(staff_rows), len(data))
