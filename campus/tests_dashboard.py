# campus/tests_dashboard.py
"""Campus admin dashboard (attendance % / fees / approvals / people / live classes).
Run: `python manage.py test campus.tests_dashboard` (NOT RUN when written)."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from .models import (
    Attendance,
    CampusLiveParticipant,
    CampusLiveSession,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    StaffLeave,
    StaffProfile,
    SubjectTeacherassigments,
)
from .tests_t4_efg import T4Base


class DashboardTests(T4Base):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.url = reverse("campus-dashboard", args=[self.campus.id])
        self.client.force_authenticate(self.admin)

    def get(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        return r.data

    # ---- permissions -------------------------------------------------
    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_teacher_and_student_denied(self):
        for u in (self.teacher, self.sa1):
            self.client.force_authenticate(u)
            self.assertIn(self.client.get(self.url).status_code, (403, 404))

    # ---- attendance --------------------------------------------------
    def test_attendance_empty(self):
        a = self.get()["attendance"]
        self.assertIsNone(a["percent"])
        self.assertEqual((a["total_students"], a["marked"], a["not_marked"]), (3, 0, 3))

    def test_attendance_percent_late_counts_and_unmarked_excluded(self):
        Attendance.objects.create(enrollment=self.e_a1, date=self.today, status="present", marked_by=self.admin)
        Attendance.objects.create(enrollment=self.e_a2, date=self.today, status="absent", marked_by=self.admin)
        a = self.get()["attendance"]
        self.assertEqual((a["marked"], a["attended"], a["absent"], a["not_marked"]), (2, 1, 1, 1))
        self.assertEqual(a["percent"], 50.0)
        Attendance.objects.filter(enrollment=self.e_a2).update(status="late")
        self.assertEqual(self.get()["attendance"]["percent"], 100.0)

    def test_attendance_ignores_other_days_and_counts_student_once(self):
        Attendance.objects.create(enrollment=self.e_a1, date=self.today - timedelta(days=1), status="absent", marked_by=self.admin)
        Attendance.objects.create(enrollment=self.e_a1, date=self.today, status="present", marked_by=self.admin)
        Attendance.objects.create(enrollment=self.e_a1, date=self.today, subject=self.math, status="present", marked_by=self.admin)
        a = self.get()["attendance"]
        self.assertEqual((a["marked"], a["percent"]), (1, 100.0))

    # ---- fees --------------------------------------------------------
    def _invoice(self, enrollment, amount, *, due_in=10, structure=None):
        fs = structure or FeeStructure.objects.create(
            campus=self.campus, session=self.session, title="Term", amount=amount,
            due_date=self.today + timedelta(days=due_in))
        return FeeInvoice.objects.create(enrollment=enrollment, fee_structure=fs, amount_due=amount)

    def _pay(self, invoice, amount, status=FeePayment.Status.SUCCESS):
        return FeePayment.objects.create(
            invoice=invoice, amount=amount, payer_role="admin", payment_mode="cash",
            status=status, paid_at=timezone.now() if status == FeePayment.Status.SUCCESS else None)

    def test_fees_empty(self):
        f = self.get()["fees"]
        self.assertEqual((f["collected"], f["pending"], f["collected_percent"]), ("0.00", "0.00", None))

    def test_fees_collected_vs_pending(self):
        i1 = self._invoice(self.e_a1, Decimal("1000"))
        i2 = self._invoice(self.e_a2, Decimal("1000"), structure=i1.fee_structure)
        self._pay(i1, Decimal("400"))
        self._pay(i2, Decimal("1000"))
        self._pay(i1, Decimal("999"), status=FeePayment.Status.FAILED)  # never counted
        f = self.get()["fees"]
        self.assertEqual(f["total_due"], "2000.00")
        self.assertEqual(f["collected"], "1400.00")
        self.assertEqual(f["pending"], "600.00")
        self.assertEqual(f["overdue"], "0.00")
        self.assertEqual(f["collected_today"], "1400.00")
        self.assertEqual(f["collected_percent"], 70.0)
        self.assertEqual((f["invoices_total"], f["invoices_unpaid"]), (2, 1))

    def test_overpayment_does_not_hide_other_dues(self):
        fs = FeeStructure.objects.create(campus=self.campus, session=self.session, title="T",
                                         amount=Decimal("500"), due_date=self.today + timedelta(days=5))
        i1 = self._invoice(self.e_a1, Decimal("500"), structure=fs)
        self._invoice(self.e_a2, Decimal("500"), structure=fs)
        self._pay(i1, Decimal("700"))
        self.assertEqual(self.get()["fees"]["pending"], "500.00")

    def test_overdue_and_waived(self):
        i1 = self._invoice(self.e_a1, Decimal("300"), due_in=-3)
        i2 = self._invoice(self.e_a2, Decimal("900"), due_in=-3)
        FeeInvoice.objects.filter(pk=i2.pk).update(status=FeeInvoice.Status.WAIVED)
        f = self.get()["fees"]
        self.assertEqual((f["pending"], f["overdue"], f["total_due"]), ("300.00", "300.00", "300.00"))
        self.assertEqual(f["invoices_total"], 1)
        self.assertTrue(i1.pk)

    # ---- approvals ---------------------------------------------------
    def test_pending_approvals(self):
        # fixture already has 1 pending subject request (secA / physics)
        StaffLeave.objects.create(
            campus=self.campus, staff=self.t_staff, leave_type="casual",
            date_from=self.today, date_to=self.today, status=StaffLeave.Status.PENDING)
        StaffLeave.objects.create(
            campus=self.campus, staff=self.t2_staff, leave_type="sick",
            date_from=self.today, date_to=self.today, status=StaffLeave.Status.APPROVED)
        ap = self.get()["approvals"]
        self.assertEqual((ap["subject_requests"], ap["staff_leaves"], ap["total"]), (1, 1, 2))

    # ---- people ------------------------------------------------------
    def test_people(self):
        p = self.get()["people"]
        self.assertEqual((p["active_teachers"], p["active_students"]), (2, 3))
        StaffProfile.objects.filter(pk=self.t2_staff.pk).update(is_active=False)
        self.e_b.status = "withdrawn"
        self.e_b.save(update_fields=["status"])
        p = self.get()["people"]
        self.assertEqual((p["active_teachers"], p["active_students"]), (1, 2))

    # ---- live --------------------------------------------------------
    def test_live_now_and_later_today(self):
        now = timezone.now()
        live = CampusLiveSession.objects.create(
            section=self.secA, subject=self.math, teacher=self.t_staff, scheduled_at=now,
            status=CampusLiveSession.Status.LIVE, started_at=now)
        CampusLiveSession.objects.create(  # ended -> not live
            section=self.secB, subject=self.math, teacher=self.t2_staff, scheduled_at=now,
            status=CampusLiveSession.Status.ENDED)
        CampusLiveParticipant.objects.create(session=live, user=self.teacher)  # teacher not counted
        CampusLiveParticipant.objects.create(session=live, user=self.sa1)
        CampusLiveParticipant.objects.create(session=live, user=self.sa2, left_at=now)  # already left
        d = self.get()["live"]
        self.assertEqual(d["live_now"], 1)
        self.assertEqual(len(d["items"]), 1)
        self.assertEqual(d["items"][0]["subject"], "Maths")
        self.assertEqual(d["items"][0]["students_in_room"], 1)

    # ---- scope -------------------------------------------------------
    def test_hod_only_sees_own_department(self):
        hod = self.other_teacher  # re-use a user; make them department-limited principal
        StaffProfile.objects.filter(pk=self.t2_staff.pk).update(
            role=StaffProfile.Role.PRINCIPAL_HOD, department=self.dept)
        self.client.force_authenticate(hod)
        d = self.get()
        self.assertEqual(d["scope_department"], str(self.dept.id))
        self.assertEqual(d["attendance"]["total_students"], 2)  # only class 10 (secA)
        self.assertEqual(d["people"]["active_students"], 2)
