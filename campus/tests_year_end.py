# campus/tests_year_end.py
"""Year-end: copy structure -> plan -> promote.
Run: `python manage.py test campus.tests_year_end` (NOT RUN when written)."""
from datetime import date

from django.core.cache import cache
from django.urls import reverse

from . import year_end
from .models import (
    AcademicSession,
    ClassTeacherassigments,
    Section,
    SchoolClass,
    StudentEnrollment,
    SubjectTeacherassigments,
)
from .tests_t4_efg import T4Base

S = StudentEnrollment.Status


class NameTests(T4Base):
    def test_next_class_name(self):
        f = year_end.next_class_name
        self.assertEqual(f("Class 9"), "Class 10")
        self.assertEqual(f("9"), "10")
        self.assertEqual(f("B.Sc 2nd Year"), "B.Sc 3rd Year")
        self.assertEqual(f("B.Sc 3rd Year"), "B.Sc 4th Year")
        self.assertEqual(f("Class 12"), "Class 13")
        self.assertIsNone(f("Nursery"))


class YearEndBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.new = AcademicSession.objects.create(
            campus=self.campus, name="2027-28", start_date=date(2027, 6, 1), end_date=date(2028, 4, 30))
        self.client.force_authenticate(self.admin)

    def make_new_structure(self):
        year_end.copy_structure(self.campus, self.admin, self.session, self.new)
        self.n10 = SchoolClass.objects.get(session=self.new, name="10")
        self.n9 = SchoolClass.objects.get(session=self.new, name="9")
        # class 10 -> graduate, class 9 -> class 10
        self.mapping = [
            {"from_class": str(self.cls.id), "action": "graduate"},
            {"from_class": str(self.cls2.id), "action": "promote", "to_class": str(self.n10.id)},
        ]
        Section.objects.get_or_create(school_class=self.n10, name="B")

    def run_promote(self, **kw):
        kw.setdefault("dry_run", False)
        return year_end.promote(self.campus, self.admin, self.session, self.new, self.mapping, **kw)


class CopyStructureTests(YearEndBase):
    def test_copies_classes_sections_and_is_idempotent(self):
        r = year_end.copy_structure(self.campus, self.admin, self.session, self.new)
        self.assertEqual((r["classes_created"], r["sections_created"]), (2, 2))
        r2 = year_end.copy_structure(self.campus, self.admin, self.session, self.new)
        self.assertEqual((r2["classes_created"], r2["sections_created"]), (0, 0))
        self.assertEqual(SchoolClass.objects.get(session=self.new, name="10").department_id, self.dept.id)

    def test_copy_teachers(self):
        ClassTeacherassigments.objects.create(section=self.secA, staff=self.t_staff)
        r = year_end.copy_structure(self.campus, self.admin, self.session, self.new, copy_teachers=True)
        self.assertEqual(r["class_teachers_copied"], 1)
        self.assertEqual(r["subject_teachers_copied"], 2)  # approved math rows of secA + secB
        # pending request is NOT copied
        self.assertFalse(SubjectTeacherassigments.objects.filter(
            section__school_class__session=self.new, subject=self.phy).exists())

    def test_same_or_wrong_order_rejected(self):
        with self.assertRaises(year_end.YearEndError):
            year_end.copy_structure(self.campus, self.admin, self.session, self.session)
        with self.assertRaises(year_end.YearEndError):
            year_end.copy_structure(self.campus, self.admin, self.new, self.session)


class PlanTests(YearEndBase):
    def test_suggestions(self):
        year_end.copy_structure(self.campus, self.admin, self.session, self.new)
        p = year_end.plan(self.campus, self.session, self.new)
        rows = {r["from_class_name"]: r for r in p["classes"]}
        self.assertEqual(rows["9"]["suggested_action"], "promote")
        self.assertEqual(rows["9"]["suggested_to_class"], str(SchoolClass.objects.get(session=self.new, name="10").id))
        self.assertEqual(rows["9"]["missing_sections"], ["B"])  # new class 10 only has section A
        self.assertEqual(rows["10"]["suggested_action"], "graduate")
        self.assertEqual((rows["10"]["students"], rows["9"]["students"]), (2, 1))
        self.assertEqual(p["total_students"], 3)

    def test_empty_new_session_flagged(self):
        p = year_end.plan(self.campus, self.session, self.new)
        self.assertFalse(p["new_session_has_classes"])
        self.assertTrue(all(r["suggested_action"] == "skip" or r["suggested_action"] == "graduate" for r in p["classes"]))


class PromoteTests(YearEndBase):
    def setUp(self):
        super().setUp()
        self.make_new_structure()

    def test_dry_run_writes_nothing(self):
        r = self.run_promote(dry_run=True)
        self.assertEqual((r["summary"]["graduate"], r["summary"]["promote"], r["summary"]["errors"]), (2, 1, 0))
        self.assertEqual(StudentEnrollment.objects.filter(status=S.ACTIVE, session=self.session).count(), 3)
        self.assertFalse(StudentEnrollment.objects.filter(session=self.new).exists())

    def test_real_run(self):
        r = self.run_promote()
        self.assertEqual((r["summary"]["graduate"], r["summary"]["promote"], r["error_count"]), (2, 1, 0))
        self.e_a1.refresh_from_db(); self.e_b.refresh_from_db()
        self.assertEqual(self.e_a1.status, S.GRADUATED)
        self.assertEqual(self.e_b.status, S.PROMOTED)
        self.assertIsNotNone(self.e_b.left_at)
        new = StudentEnrollment.objects.get(student=self.sb, session=self.new)
        self.assertEqual((new.status, new.section.school_class_id, new.section.name), (S.ACTIVE, self.n10.id, "B"))
        self.assertFalse(StudentEnrollment.objects.filter(student=self.sa1, session=self.new).exists())

    def test_rerun_is_safe(self):
        self.run_promote()
        r = self.run_promote()
        self.assertEqual(r["total"], 0)  # nobody ACTIVE in the old session any more
        self.assertEqual(StudentEnrollment.objects.filter(session=self.new).count(), 1)

    def test_already_in_new_session_only_closes_old_row(self):
        from .roster import enroll_student
        enroll_student(section=Section.objects.get(school_class=self.n10, name="B"), student=self.sb, session=self.new)
        r = self.run_promote()
        self.assertEqual(r["summary"]["already_done"], 1)
        self.e_b.refresh_from_db()
        self.assertEqual(self.e_b.status, S.PROMOTED)
        self.assertEqual(StudentEnrollment.objects.filter(student=self.sb, session=self.new).count(), 1)

    def test_section_full_reported_and_student_stays_active(self):
        Section.objects.filter(school_class=self.n10, name="B").update(capacity=0)
        r = self.run_promote()
        self.assertEqual([e["code"] for e in r["errors"]], ["section_full"])
        self.e_b.refresh_from_db()
        self.assertEqual(self.e_b.status, S.ACTIVE)
        r2 = self.run_promote(override_capacity=True)
        self.assertEqual(r2["summary"]["promote"], 1)

    def test_missing_section(self):
        Section.objects.filter(school_class=self.n10, name="B").delete()
        Section.objects.filter(school_class=self.n10).exclude(name="A").delete()
        Section.objects.get_or_create(school_class=self.n10, name="X")
        Section.objects.get_or_create(school_class=self.n10, name="Y")
        r = self.run_promote(dry_run=True)
        self.assertEqual(r["errors"][0]["code"], "no_section")
        r = self.run_promote(create_missing_sections=True)
        self.assertEqual((r["summary"]["promote"], r["error_count"]), (1, 0))
        self.assertTrue(Section.objects.filter(school_class=self.n10, name="B").exists())

    def test_overrides_repeat_withdraw_skip(self):
        r = self.run_promote(overrides=[
            {"student": self.sa1.pk, "action": "repeat"},
            {"student": self.sa2.pk, "action": "withdraw"},
            {"student": self.sb.pk, "action": "skip"},
        ])
        self.assertEqual((r["summary"]["repeat"], r["summary"]["withdraw"], r["summary"]["skipped"]), (1, 1, 1))
        self.e_a1.refresh_from_db(); self.e_a2.refresh_from_db(); self.e_b.refresh_from_db()
        self.assertEqual((self.e_a1.status, self.e_a2.status, self.e_b.status), (S.PROMOTED, S.WITHDRAWN, S.ACTIVE))
        rep = StudentEnrollment.objects.get(student=self.sa1, session=self.new)
        self.assertEqual((rep.section.school_class_id, rep.section.name), (self.n10.id, "A"))

    def test_unmapped_class_is_an_error_not_a_guess(self):
        r = year_end.promote(self.campus, self.admin, self.session, self.new, [], dry_run=False)
        self.assertEqual(r["error_count"], 3)
        self.assertTrue(all(e["code"] == "no_mapping" for e in r["errors"]))
        self.assertEqual(StudentEnrollment.objects.filter(status=S.ACTIVE, session=self.session).count(), 3)

    def test_bad_target_class_rejected(self):
        with self.assertRaises(year_end.YearEndError):
            year_end.promote(self.campus, self.admin, self.session, self.new,
                             [{"from_class": str(self.cls2.id), "action": "promote", "to_class": str(self.cls.id)}])

    def test_set_current(self):
        self.run_promote(set_current=True)
        self.new.refresh_from_db()
        self.assertTrue(self.new.is_current)


class ApiTests(YearEndBase):
    def url(self, name):
        return reverse(name, args=[self.new.id])

    def test_flow_through_api(self):
        r = self.client.post(self.url("academic-session-copy-structure"), {"from_session": str(self.session.id)}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        r = self.client.get(self.url("academic-session-promotion-plan"), {"from_session": str(self.session.id)})
        self.assertEqual(r.status_code, 200, r.data)
        n10 = SchoolClass.objects.get(session=self.new, name="10")
        body = {"from_session": str(self.session.id),
                "mapping": [{"from_class": str(self.cls.id), "action": "graduate"},
                            {"from_class": str(self.cls2.id), "action": "promote", "to_class": str(n10.id)}]}
        r = self.client.post(self.url("academic-session-promote"), body, format="json")  # dry_run defaults to TRUE
        self.assertEqual((r.status_code, r.data["dry_run"]), (200, True), r.data)
        self.assertFalse(StudentEnrollment.objects.filter(session=self.new).exists())
        r = self.client.post(self.url("academic-session-promote"), {**body, "dry_run": False, "create_missing_sections": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["summary"]["promote"], 1)

    def test_teacher_denied_and_bad_from_session(self):
        self.client.force_authenticate(self.teacher)
        r = self.client.post(self.url("academic-session-promote"), {"from_session": str(self.session.id)}, format="json")
        self.assertIn(r.status_code, (403, 404))
        self.client.force_authenticate(self.admin)
        r = self.client.post(self.url("academic-session-promote"), {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_busy_lock(self):
        cache.add(f"campus:year_end:{self.new.pk}", 1, timeout=60)
        r = self.client.post(self.url("academic-session-promote"),
                             {"from_session": str(self.session.id), "dry_run": False}, format="json")
        self.assertEqual(r.status_code, 409)
