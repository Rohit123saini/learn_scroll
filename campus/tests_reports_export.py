# campus/tests_reports_export.py
"""
REPORTS / EXPORT — API tests for `reports/catalog/` and `reports/export/`.

Run: `python manage.py test campus.tests_reports_export`

What is covered
* catalog: who sees which report group, fee module gate, which formats the server can write
* export access: admin / principal / class teacher (own section only) / accounts clerk /
  department-limited HOD / subject teacher / student / outsider
* attendance_summary + attendance_daily: numbers, section scope, date-range rules, subject filter
* marks_sheet + marks_detail: exam_term required, drafts hidden unless asked, total / % / grade /
  result / rank, rank resets per section
* fees_*: fee.view + fee module gate, a real collection row
* CSV / XLSX / PDF bodies (XLSX / PDF are skipped when openpyxl / reportlab are not installed),
  501 when the server lacks a library, formula-injection guard
* audit log row per export, `Cache-Control: no-store`, file name, throttle (60/hour scope)

Nothing here touches the network, Celery or FCM.
"""
import csv
import importlib.util
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from . import reports_export as rx
from .models import (
    AcademicSession,
    Attendance,
    CampusAuditLog,
    ClassTeacherassigments,
    Department,
    ExamTerm,
    FeeInvoice,
    FeePayment,
    FeeStructure,
    ResultEntry,
    SchoolClass,
    Section,
    StaffProfile,
    StudentEnrollment,
    Subject,
)
from .throttles import CampusReportExportThrottle

User = get_user_model()

HAS_XLSX = importlib.util.find_spec("openpyxl") is not None
HAS_PDF = importlib.util.find_spec("reportlab") is not None

TODAY = timezone.localdate()


def unittest_skip_unless(condition, reason):
    import unittest
    return unittest.skipUnless(condition, reason)


def parse_csv(response):
    """CSV body -> list of rows (header first). The file starts with a UTF-8 BOM."""
    text = response.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def as_dicts(response):
    rows = parse_csv(response)
    header, body = rows[0], rows[1:]
    return [dict(zip(header, r)) for r in body]


class ReportsBase(APITestCase):
    """One campus, two sections (10-A and 9-B), a few students, one exam, and one user per role."""

    def setUp(self):
        cache.clear()
        mk = lambda name, **kw: User.objects.create_user(username=name, password="pass12345", **kw)
        self.admin = mk("admin")
        self.principal = mk("principal")
        self.hod = mk("hod")
        self.ct_a = mk("ct_a")            # class teacher of 10-A
        self.ct_b = mk("ct_b")            # class teacher of 9-B
        self.subj_teacher = mk("subj")
        self.clerk = mk("clerk")
        self.outsider = mk("outsider")
        self.s1 = mk("stu1", first_name="Asha", last_name="Verma")
        self.s2 = mk("stu2", first_name="Bilal", last_name="Khan")
        self.s3 = mk("stu3", first_name="Chitra", last_name="Rao")
        self.s4 = mk("stu4", first_name="Dev", last_name="Singh")     # in 9-B

        from .models import Campus
        self.campus = Campus.objects.create(name="Green Valley", created_by=self.admin)
        self.session = AcademicSession.objects.create(
            campus=self.campus, name="2026-27", start_date=date(2026, 6, 1), end_date=date(2027, 4, 30),
            is_current=True)

        Role = StaffProfile.Role
        sp = lambda user, role, **kw: StaffProfile.objects.create(campus=self.campus, user=user, role=role, **kw)
        sp(self.admin, Role.ADMIN)
        sp(self.principal, Role.PRINCIPAL_HOD)
        self.dept = Department.objects.create(campus=self.campus, name="Science")
        self.dept2 = Department.objects.create(campus=self.campus, name="Arts")
        self.hod_staff = sp(self.hod, Role.PRINCIPAL_HOD, department=self.dept)
        self.ct_a_staff = sp(self.ct_a, Role.CLASS_TEACHER)
        self.ct_b_staff = sp(self.ct_b, Role.CLASS_TEACHER)
        sp(self.subj_teacher, Role.SUBJECT_TEACHER)
        sp(self.clerk, Role.NON_TEACHING)

        self.cls10 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="10",
                                                department=self.dept)
        self.cls9 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="9",
                                               department=self.dept2)
        self.sec_a = Section.objects.create(school_class=self.cls10, name="A")
        self.sec_b = Section.objects.create(school_class=self.cls9, name="B")
        ClassTeacherassigments.objects.create(section=self.sec_a, staff=self.ct_a_staff)
        ClassTeacherassigments.objects.create(section=self.sec_b, staff=self.ct_b_staff)

        self.math = Subject.objects.create(campus=self.campus, name="Maths")
        self.sci = Subject.objects.create(campus=self.campus, name="Science")

        enr = lambda user, sec, roll: StudentEnrollment.objects.create(
            student=user, section=sec, session=self.session, roll_number=roll)
        self.e1, self.e2, self.e3 = enr(self.s1, self.sec_a, "1"), enr(self.s2, self.sec_a, "2"), enr(self.s3, self.sec_a, "3")
        self.e4 = enr(self.s4, self.sec_b, "1")

        self.term = ExamTerm.objects.create(session=self.session, name="Mid-Term",
                                            start_date=date(2026, 9, 1), end_date=date(2026, 9, 10))

    # ------------------------------------------------------------------ factories
    def att(self, enrollment, day, status_="present", subject=None):
        return Attendance.objects.create(enrollment=enrollment, date=day, status=status_,
                                         subject=subject, marked_by=self.ct_a)

    def mark(self, enrollment, subject, got, top=100, status_=ResultEntry.Status.PUBLISHED):
        return ResultEntry.objects.create(enrollment=enrollment, subject=subject, exam_term=self.term,
                                          marks_obtained=Decimal(str(got)), max_marks=Decimal(str(top)),
                                          status=status_)

    def enable_fees(self):
        self.campus.fee_module_enabled = True
        self.campus.save(update_fields=["fee_module_enabled"])

    # ------------------------------------------------------------------ requests
    def catalog(self, user, **params):
        self.client.force_authenticate(user)
        params.setdefault("campus", str(self.campus.id))
        return self.client.get(reverse("campus-report-catalog"), params)

    def export(self, user, report, fmt="csv", **params):
        self.client.force_authenticate(user)
        query = {"campus": str(self.campus.id), "report": report, "format": fmt}
        query.update({k: v for k, v in params.items() if v is not None})
        return self.client.get(reverse("campus-report-export"), query)


# ====================================================================== catalog
class ReportCatalogTests(ReportsBase):
    def keys(self, response):
        return {r["key"] for r in response.data["reports"]}

    def test_admin_sees_attendance_and_marks_but_fees_only_when_module_on(self):
        r = self.catalog(self.admin)
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(self.keys(r), {"attendance_summary", "attendance_daily", "marks_sheet", "marks_detail"})
        self.enable_fees()
        r = self.catalog(self.admin)
        self.assertEqual(self.keys(r), {
            "attendance_summary", "attendance_daily", "marks_sheet", "marks_detail",
            "fees_collection", "fees_dues", "fees_concessions", "fees_daybook"})

    def test_catalog_shape(self):
        r = self.catalog(self.admin)
        self.assertEqual(r.data["campus"], str(self.campus.id))
        self.assertEqual(r.data["role"], "admin")
        self.assertTrue(r.data["whole_campus"])
        self.assertTrue(r.data["formats"]["csv"])
        self.assertEqual(set(r.data["formats"]), {"csv", "xlsx", "pdf"})
        first = r.data["reports"][0]
        self.assertEqual(set(first), {"key", "group", "title"})

    def test_formats_reflect_installed_libraries(self):
        with mock.patch("campus.report_views.importlib.util.find_spec", return_value=None):
            r = self.catalog(self.admin)
        self.assertEqual(r.data["formats"], {"csv": True, "xlsx": False, "pdf": False})

    def test_class_teacher_gets_attendance_and_marks_not_fees(self):
        self.enable_fees()
        r = self.catalog(self.ct_a)
        self.assertEqual(self.keys(r), {"attendance_summary", "attendance_daily", "marks_sheet", "marks_detail"})
        self.assertFalse(r.data["whole_campus"])

    def test_accounts_clerk_gets_fees_only(self):
        self.enable_fees()
        r = self.catalog(self.clerk)
        self.assertEqual(self.keys(r), {"fees_collection", "fees_dues", "fees_concessions", "fees_daybook"})

    def test_department_hod_never_sees_fees_and_is_not_whole_campus(self):
        self.enable_fees()
        r = self.catalog(self.hod)
        self.assertEqual(self.keys(r), {"attendance_summary", "attendance_daily", "marks_sheet", "marks_detail"})
        self.assertFalse(r.data["whole_campus"])

    def test_principal_without_department_is_whole_campus(self):
        r = self.catalog(self.principal)
        self.assertTrue(r.data["whole_campus"])

    def test_subject_teacher_gets_an_empty_menu(self):
        r = self.catalog(self.subj_teacher)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["reports"], [])

    def test_student_and_outsider_are_forbidden(self):
        self.assertEqual(self.catalog(self.s1).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.catalog(self.outsider).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("campus-report-catalog"), {"campus": str(self.campus.id)})
        self.assertIn(r.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_campus_is_required_and_must_exist(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(reverse("campus-report-catalog")).status_code, 400)
        self.assertEqual(self.client.get(reverse("campus-report-catalog"), {"campus": "not-a-uuid"}).status_code, 400)
        self.assertEqual(
            self.client.get(reverse("campus-report-catalog"), {"campus": str(uuid.uuid4())}).status_code, 404)


# ====================================================================== attendance
class AttendanceExportTests(ReportsBase):
    def setUp(self):
        super().setUp()
        d = TODAY - timedelta(days=3)
        # Asha: 3 present + 1 late (+ 1 absent) -> marked 5, attended 4 -> 80 %
        for i in range(3):
            self.att(self.e1, d - timedelta(days=i), "present")
        self.att(self.e1, d - timedelta(days=3), "late")
        self.att(self.e1, d - timedelta(days=4), "absent")
        # Bilal: 1 absent, 1 leave -> 0 %
        self.att(self.e2, d, "absent")
        self.att(self.e2, d - timedelta(days=1), "leave")
        # Chitra: nothing marked -> blank %
        # Dev (9-B): 2 present
        self.att(self.e4, d, "present")
        self.att(self.e4, d - timedelta(days=1), "present")

    def test_summary_numbers_per_student(self):
        r = self.export(self.admin, "attendance_summary")
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        rows = {x["Student"]: x for x in as_dicts(r)}
        asha = rows["Asha Verma"]
        self.assertEqual((asha["Marked"], asha["Present"], asha["Absent"], asha["Late"], asha["Leave"]),
                         ("5", "3", "1", "1", "0"))
        self.assertEqual(float(asha["Attendance %"]), 80.0)
        self.assertEqual(float(rows["Bilal Khan"]["Attendance %"]), 0.0)
        self.assertEqual(rows["Chitra Rao"]["Marked"], "0")
        self.assertEqual(rows["Chitra Rao"]["Attendance %"], "")        # nothing marked: blank, not 0 %
        self.assertIn("Dev Singh", rows)                                  # admin sees every section

    def test_summary_respects_date_range(self):
        d = TODAY - timedelta(days=3)
        r = self.export(self.admin, "attendance_summary", **{"from": d.isoformat(), "to": d.isoformat()})
        rows = {x["Student"]: x for x in as_dicts(r)}
        self.assertEqual(rows["Asha Verma"]["Marked"], "1")

    def test_summary_subject_filter(self):
        self.att(self.e1, TODAY - timedelta(days=1), "present", subject=self.math)
        r = self.export(self.admin, "attendance_summary", subject=str(self.math.id))
        rows = {x["Student"]: x for x in as_dicts(r)}
        self.assertEqual(rows["Asha Verma"]["Marked"], "1")
        self.assertEqual(rows["Bilal Khan"]["Marked"], "0")

    def test_daily_register_lists_every_mark(self):
        r = self.export(self.admin, "attendance_daily")
        self.assertEqual(r.status_code, 200)
        body = as_dicts(r)
        self.assertEqual(len(body), 9)                                    # 5 + 2 + 2
        self.assertEqual({x["Subject"] for x in body}, {"Daily"})
        self.assertEqual(body[0]["Date"] <= body[-1]["Date"], True)       # oldest first
        self.assertIn(body[0]["Source"], ("Marked by a teacher", "Automatic (live class)"))

    def test_class_teacher_gets_only_own_section(self):
        r = self.export(self.ct_a, "attendance_summary")
        names = {x["Student"] for x in as_dicts(r)}
        self.assertEqual(names, {"Asha Verma", "Bilal Khan", "Chitra Rao"})
        r = self.export(self.ct_b, "attendance_daily")
        self.assertEqual({x["Student"] for x in as_dicts(r)}, {"Dev Singh"})

    def test_class_teacher_cannot_ask_for_someone_elses_section(self):
        r = self.export(self.ct_a, "attendance_summary", section=str(self.sec_b.id))
        self.assertEqual(r.status_code, 403)

    def test_class_teacher_can_narrow_to_own_section(self):
        r = self.export(self.ct_a, "attendance_summary", section=str(self.sec_a.id))
        self.assertEqual(r.status_code, 200)

    def test_class_teacher_without_a_section_is_forbidden(self):
        ClassTeacherassigments.objects.filter(staff=self.ct_a_staff).delete()
        self.assertEqual(self.export(self.ct_a, "attendance_summary").status_code, 403)

    def test_department_hod_sees_only_own_department(self):
        r = self.export(self.hod, "attendance_summary")
        self.assertEqual({x["Student"] for x in as_dicts(r)}, {"Asha Verma", "Bilal Khan", "Chitra Rao"})
        self.assertEqual(self.export(self.hod, "attendance_summary", section=str(self.sec_b.id)).status_code, 403)

    def test_principal_without_department_sees_everything(self):
        r = self.export(self.principal, "attendance_summary")
        self.assertIn("Dev Singh", {x["Student"] for x in as_dicts(r)})

    def test_class_filter_narrows_for_admin(self):
        r = self.export(self.admin, "attendance_summary", **{"class": str(self.cls9.id)})
        self.assertEqual({x["Student"] for x in as_dicts(r)}, {"Dev Singh"})

    def test_section_from_another_campus_is_not_found(self):
        from .models import Campus
        other = Campus.objects.create(name="Other", created_by=self.admin)
        osess = AcademicSession.objects.create(campus=other, name="x", start_date=date(2026, 6, 1),
                                               end_date=date(2027, 4, 1), is_current=True)
        osec = Section.objects.create(school_class=SchoolClass.objects.create(campus=other, session=osess, name="1"),
                                      name="Z")
        self.assertEqual(self.export(self.admin, "attendance_summary", section=str(osec.id)).status_code, 400)

    def test_withdrawn_student_with_nothing_in_range_is_left_out(self):
        self.e3.status = StudentEnrollment.Status.TRANSFERRED
        self.e3.save(update_fields=["status"])
        r = self.export(self.admin, "attendance_summary")
        self.assertNotIn("Chitra Rao", {x["Student"] for x in as_dicts(r)})

    # ---- validation
    def test_reversed_range_is_400(self):
        r = self.export(self.admin, "attendance_summary",
                        **{"from": TODAY.isoformat(), "to": (TODAY - timedelta(days=2)).isoformat()})
        self.assertEqual(r.status_code, 400)
        self.assertIn("from", r.data.get("errors", r.data))

    def test_range_longer_than_366_days_is_400(self):
        r = self.export(self.admin, "attendance_summary",
                        **{"from": (TODAY - timedelta(days=400)).isoformat(), "to": TODAY.isoformat()})
        self.assertEqual(r.status_code, 400)

    def test_bad_date_text_is_400(self):
        self.assertEqual(self.export(self.admin, "attendance_summary", **{"from": "yesterday"}).status_code, 400)

    def test_unknown_report_and_format_are_400(self):
        self.assertEqual(self.export(self.admin, "nonsense").status_code, 400)
        self.assertEqual(self.export(self.admin, "attendance_summary", fmt="docx").status_code, 400)

    def test_missing_report_is_400(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("campus-report-export"), {"campus": str(self.campus.id)})
        self.assertEqual(r.status_code, 400)

    def test_invalid_ids_are_400(self):
        self.assertEqual(self.export(self.admin, "attendance_summary", section="nope").status_code, 400)
        self.assertEqual(self.export(self.admin, "attendance_summary", subject="nope").status_code, 400)
        self.assertEqual(self.export(self.admin, "attendance_summary", subject=str(uuid.uuid4())).status_code, 400)

    # ---- who may call it at all
    def test_subject_teacher_student_outsider_and_clerk_are_forbidden(self):
        self.enable_fees()
        for user in (self.subj_teacher, self.clerk, self.s1, self.outsider):
            with self.subTest(user=user.username):
                self.assertEqual(self.export(user, "attendance_summary").status_code, 403)

    def test_anonymous_is_rejected(self):
        self.client.force_authenticate(None)
        r = self.client.get(reverse("campus-report-export"),
                            {"campus": str(self.campus.id), "report": "attendance_summary"})
        self.assertIn(r.status_code, (401, 403))


# ====================================================================== marks
class MarksExportTests(ReportsBase):
    def setUp(self):
        super().setUp()
        # 10-A: Asha 90+80 = 170/200 (85 %), Bilal 90+80 -> tie on 85 %, Chitra 20+10 = 15 % (fail)
        for e, m, s in ((self.e1, 90, 80), (self.e2, 90, 80), (self.e3, 20, 10)):
            self.mark(e, self.math, m)
            self.mark(e, self.sci, s)
        # 9-B: Dev alone -> rank 1 inside his own section
        self.mark(self.e4, self.math, 50)
        self.mark(self.e4, self.sci, 50)

    def sheet(self, user=None, **params):
        params.setdefault("exam_term", str(self.term.id))
        return self.export(user or self.admin, "marks_sheet", **params)

    def test_exam_term_is_required(self):
        r = self.export(self.admin, "marks_sheet")
        self.assertEqual(r.status_code, 400)
        self.assertIn("exam_term", r.data.get("errors", r.data))

    def test_exam_term_must_belong_to_the_session(self):
        other_session = AcademicSession.objects.create(
            campus=self.campus, name="2025-26", start_date=date(2025, 6, 1), end_date=date(2026, 4, 1))
        foreign = ExamTerm.objects.create(session=other_session, name="Old", start_date=date(2025, 9, 1),
                                          end_date=date(2025, 9, 5))
        self.assertEqual(self.sheet(exam_term=str(foreign.id)).status_code, 404)

    def test_sheet_layout_and_maths(self):
        r = self.sheet()
        self.assertEqual(r.status_code, 200, getattr(r, "data", None))
        header = parse_csv(r)[0]
        self.assertEqual(header[:4], ["Student", "Roll No", "Class", "Section"])
        self.assertEqual(header[4:6], ["Maths", "Science"])               # subject columns, A-Z
        self.assertEqual(header[6:], ["Total", "Max", "Percent", "Grade", "Result", "Rank"])
        rows = {x["Student"]: x for x in as_dicts(r)}
        asha = rows["Asha Verma"]
        self.assertEqual((asha["Maths"], asha["Science"], asha["Total"], asha["Max"]), ("90", "80", "170", "200"))
        self.assertEqual(float(asha["Percent"]), 85.0)
        self.assertEqual(asha["Result"], "Pass")
        self.assertEqual(rows["Chitra Rao"]["Result"], "Fail")
        self.assertTrue(asha["Grade"])

    def test_equal_scores_share_a_rank_and_the_next_skips(self):
        rows = {x["Student"]: x for x in as_dicts(self.sheet())}
        self.assertEqual(rows["Asha Verma"]["Rank"], "1")
        self.assertEqual(rows["Bilal Khan"]["Rank"], "1")
        self.assertEqual(rows["Chitra Rao"]["Rank"], "3")

    def test_rank_restarts_in_every_section(self):
        rows = {x["Student"]: x for x in as_dicts(self.sheet())}
        self.assertEqual(rows["Dev Singh"]["Rank"], "1")

    def test_drafts_hidden_unless_asked(self):
        ResultEntry.objects.filter(enrollment=self.e3).update(status=ResultEntry.Status.DRAFT)
        names = {x["Student"] for x in as_dicts(self.sheet())}
        self.assertNotIn("Chitra Rao", names)
        names = {x["Student"] for x in as_dicts(self.sheet(include_drafts="1"))}
        self.assertIn("Chitra Rao", names)

    def test_missing_subject_stays_blank_and_is_not_counted(self):
        ResultEntry.objects.filter(enrollment=self.e3, subject=self.sci).delete()
        rows = {x["Student"]: x for x in as_dicts(self.sheet())}
        chitra = rows["Chitra Rao"]
        self.assertEqual((chitra["Science"], chitra["Total"], chitra["Max"]), ("", "20", "100"))

    def test_class_teacher_sheet_is_own_section_only(self):
        names = {x["Student"] for x in as_dicts(self.sheet(self.ct_a))}
        self.assertEqual(names, {"Asha Verma", "Bilal Khan", "Chitra Rao"})
        self.assertEqual(self.sheet(self.ct_a, section=str(self.sec_b.id)).status_code, 403)

    def test_detail_is_one_row_per_student_per_subject(self):
        r = self.export(self.admin, "marks_detail", exam_term=str(self.term.id))
        body = as_dicts(r)
        self.assertEqual(len(body), 8)
        self.assertEqual(set(body[0]), {"Student", "Roll No", "Class", "Section", "Subject", "Marks", "Max",
                                        "Percent", "Grade", "Result", "Status", "Remarks"})
        asha_math = next(x for x in body if x["Student"] == "Asha Verma" and x["Subject"] == "Maths")
        self.assertEqual((asha_math["Marks"], asha_math["Max"], asha_math["Result"], asha_math["Status"]),
                         ("90", "100", "Pass", "Published"))

    def test_detail_subject_filter(self):
        r = self.export(self.admin, "marks_detail", exam_term=str(self.term.id), subject=str(self.sci.id))
        self.assertEqual({x["Subject"] for x in as_dicts(r)}, {"Science"})

    def test_decimal_marks_are_written_cleanly(self):
        ResultEntry.objects.filter(enrollment=self.e1, subject=self.math).update(marks_obtained=Decimal("87.50"))
        rows = {x["Student"]: x for x in as_dicts(self.sheet())}
        self.assertEqual(rows["Asha Verma"]["Maths"], "87.5")

    def test_formula_injection_is_neutralised(self):
        self.s1.first_name, self.s1.last_name = "=HYPERLINK(\"http://x\")", "Verma"
        self.s1.save()
        r = self.sheet()
        names = [x["Student"] for x in as_dicts(r)]
        self.assertTrue(any(n.startswith("'=") for n in names), names)
        self.assertFalse(any(n.startswith("=") for n in names))

    def test_marks_forbidden_for_clerk_and_subject_teacher(self):
        for user in (self.clerk, self.subj_teacher, self.s1):
            self.assertEqual(self.sheet(user).status_code, 403)

    def test_hindi_names_survive_in_csv(self):
        self.s2.first_name, self.s2.last_name = "आशा", "शर्मा"
        self.s2.save()
        names = {x["Student"] for x in as_dicts(self.sheet())}
        self.assertIn("आशा शर्मा", names)


# ====================================================================== fees
class FeeExportTests(ReportsBase):
    def setUp(self):
        super().setUp()
        self.enable_fees()
        fs = FeeStructure.objects.create(campus=self.campus, session=self.session, title="Term 1",
                                         amount=Decimal("1000"), due_date=TODAY - timedelta(days=5))
        self.invoice = FeeInvoice.objects.create(enrollment=self.e1, fee_structure=fs, amount_due=Decimal("1000"))
        self.payment = FeePayment.objects.create(
            invoice=self.invoice, amount=Decimal("400"), payment_mode=FeePayment.Mode.CASH,
            status=FeePayment.Status.SUCCESS, paid_at=timezone.now(), recorded_by=self.clerk,
            payer_role=FeePayment.PayerRole.ADMIN)

    def test_fee_module_off_is_403(self):
        self.campus.fee_module_enabled = False
        self.campus.save(update_fields=["fee_module_enabled"])
        self.assertEqual(self.export(self.admin, "fees_collection").status_code, 403)

    def test_admin_principal_and_clerk_may_export_fees(self):
        for user in (self.admin, self.principal, self.clerk):
            with self.subTest(user=user.username):
                self.assertEqual(self.export(user, "fees_collection").status_code, 200)

    def test_class_teacher_department_hod_and_subject_teacher_may_not(self):
        for user in (self.ct_a, self.hod, self.subj_teacher, self.s1, self.outsider):
            with self.subTest(user=user.username):
                self.assertEqual(self.export(user, "fees_collection").status_code, 403)

    def test_collection_has_the_payment_row(self):
        r = self.export(self.admin, "fees_collection")
        body = as_dicts(r)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["Student"], "Asha Verma")
        self.assertEqual(body[0]["Amount"], "400.00")
        self.assertEqual(body[0]["Mode"], "cash")

    def test_dues_shows_the_balance(self):
        r = self.export(self.clerk, "fees_dues")
        body = as_dicts(r)
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["Balance"], "600.00")

    def test_dues_can_be_narrowed_to_another_class_and_comes_back_empty(self):
        r = self.export(self.clerk, "fees_dues", **{"class": str(self.cls9.id)})
        self.assertEqual(as_dicts(r), [])

    def test_concessions_and_daybook_render(self):
        for report in ("fees_concessions", "fees_daybook"):
            with self.subTest(report=report):
                self.assertEqual(self.export(self.admin, report).status_code, 200)

    def test_fee_date_range_reversed_is_400(self):
        r = self.export(self.admin, "fees_collection",
                        **{"from": TODAY.isoformat(), "to": (TODAY - timedelta(days=1)).isoformat()})
        self.assertEqual(r.status_code, 400)

    def test_same_numbers_as_the_old_finance_export(self):
        self.client.force_authenticate(self.admin)
        old = self.client.get(reverse("campus-finance-export"), {
            "campus": str(self.campus.id), "report": "collection", "format": "csv"})
        new = self.export(self.admin, "fees_collection")
        self.assertEqual(old.status_code, 200)
        self.assertEqual(parse_csv(old), parse_csv(new))


# ====================================================================== formats, headers, audit, throttle
class ExportFormatTests(ReportsBase):
    def setUp(self):
        super().setUp()
        self.att(self.e1, TODAY - timedelta(days=1), "present")

    def test_csv_headers(self):
        r = self.export(self.admin, "attendance_summary")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["Content-Type"].startswith("text/csv"))
        self.assertEqual(r["Cache-Control"], "no-store")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["Content-Disposition"],
                         f'attachment; filename="attendance_summary_{TODAY:%Y%m%d}.csv"')
        self.assertTrue(r.content.startswith(b"\xef\xbb\xbf"))          # BOM: Excel opens Hindi correctly

    def test_format_defaults_to_csv_and_is_case_insensitive(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(reverse("campus-report-export"),
                            {"campus": str(self.campus.id), "report": "attendance_summary"})
        self.assertEqual(r.status_code, 200)
        r = self.export(self.admin, "attendance_summary", fmt="CSV")
        self.assertEqual(r.status_code, 200)

    @unittest_skip_unless(HAS_XLSX, "openpyxl not installed")
    def test_xlsx_is_a_real_workbook(self):
        r = self.export(self.admin, "attendance_summary", fmt="xlsx")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:2], b"PK")
        self.assertIn("spreadsheetml", r["Content-Type"])
        self.assertTrue(r["Content-Disposition"].endswith('.xlsx"'))

        from openpyxl import load_workbook
        sheet = load_workbook(io.BytesIO(r.content)).active
        self.assertEqual(sheet.cell(1, 1).value, "Student")
        self.assertEqual(sheet.cell(2, 1).value, "Asha Verma")

    @unittest_skip_unless(HAS_PDF, "reportlab not installed")
    def test_pdf_is_a_real_pdf(self):
        r = self.export(self.admin, "attendance_summary", fmt="pdf")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:5], b"%PDF-")
        self.assertEqual(r["Content-Type"], "application/pdf")
        self.assertTrue(r["Content-Disposition"].endswith('.pdf"'))

    @unittest_skip_unless(HAS_PDF, "reportlab not installed")
    def test_empty_report_still_gives_a_pdf(self):
        r = self.export(self.admin, "attendance_daily",
                        **{"from": "2020-01-01", "to": "2020-01-31"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content[:5], b"%PDF-")

    def test_missing_library_is_501_not_500(self):
        with mock.patch("campus.report_views.rx.render", side_effect=rx.ExportUnavailable("Install openpyxl")):
            r = self.export(self.admin, "attendance_summary", fmt="xlsx")
        self.assertEqual(r.status_code, status.HTTP_501_NOT_IMPLEMENTED)
        self.assertIn("openpyxl", r.data["detail"])
        with mock.patch("campus.report_views.rx.render", side_effect=rx.PdfUnavailable("Install reportlab")):
            r = self.export(self.admin, "attendance_summary", fmt="pdf")
        self.assertEqual(r.status_code, status.HTTP_501_NOT_IMPLEMENTED)

    def test_too_many_rows_is_400(self):
        with mock.patch("campus.report_views.rx.render", side_effect=rx.TooManyRows("too many")):
            r = self.export(self.admin, "attendance_summary")
        self.assertEqual(r.status_code, 400)
        with mock.patch("campus.report_views.rx.render", side_effect=rx.PdfTooLarge("too big")):
            r = self.export(self.admin, "attendance_summary", fmt="pdf")
        self.assertEqual(r.status_code, 400)

    def test_failed_request_writes_no_audit_row(self):
        self.export(self.admin, "attendance_summary", fmt="docx")
        self.export(self.subj_teacher, "attendance_summary")
        self.assertFalse(CampusAuditLog.objects.filter(action="report.export").exists())


class ExportAuditAndThrottleTests(ReportsBase):
    def test_every_successful_export_is_audit_logged(self):
        self.export(self.admin, "attendance_summary")
        self.export(self.ct_a, "attendance_daily")
        logs = list(CampusAuditLog.objects.filter(action="report.export").order_by("created_at"))
        self.assertEqual(len(logs), 2)
        first = logs[0]
        self.assertEqual(first.campus_id, self.campus.id)
        self.assertEqual(first.actor_id, self.admin.id)
        self.assertEqual(first.target_type, "Report")
        self.assertEqual(first.target_id, "attendance_summary")
        self.assertEqual(first.metadata["fmt"], "csv")
        self.assertIn("Session: 2026-27", first.metadata["scope"])
        self.assertEqual(logs[1].actor_id, self.ct_a.id)

    def test_audit_scope_records_what_the_class_teacher_exported(self):
        self.export(self.ct_a, "attendance_summary")
        log = CampusAuditLog.objects.get(action="report.export")
        self.assertIn("10-A", log.metadata["scope"])
        self.assertNotIn("9-B", log.metadata["scope"])

    def test_throttle_stops_the_nth_plus_one_export(self):
        rates = {"campus_report_export": "3/hour"}
        with mock.patch.object(CampusReportExportThrottle, "THROTTLE_RATES", rates):
            cache.clear()
            codes = [self.export(self.admin, "attendance_summary").status_code for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 429, 429])

    def test_throttle_is_per_user(self):
        rates = {"campus_report_export": "1/hour"}
        with mock.patch.object(CampusReportExportThrottle, "THROTTLE_RATES", rates):
            cache.clear()
            self.assertEqual(self.export(self.admin, "attendance_summary").status_code, 200)
            self.assertEqual(self.export(self.admin, "attendance_summary").status_code, 429)
            self.assertEqual(self.export(self.principal, "attendance_summary").status_code, 200)

    def test_throttle_rate_is_configured(self):
        from django.conf import settings
        self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["campus_report_export"], "60/hour")

