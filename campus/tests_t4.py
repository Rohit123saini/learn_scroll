# campus/tests_t4.py
"""[T4 §A-§D] participants, control panel, moderator, roster/capacity, audit, bulk import."""
import io
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APITestCase

from .models import (
    AcademicSession, Campus, CampusAuditLog, CampusParentLink, ClassTeacherassigments, Department,
    SchoolClass, Section, StaffProfile, StudentEnrollment, Subject, SubjectTeacherassigments,
)
from .permissions import ALL_ROLES, ROLE_ACTIONS, SCOPED_ACTIONS, role_can, user_can

User = get_user_model()
R = StaffProfile.Role


class T4Base(APITestCase):
    def setUp(self):
        mk = lambda n: User.objects.create_user(username=n, password="pass12345")
        self.admin, self.hod, self.ct, self.ct2, self.sub, self.mod = (
            mk("admin"), mk("hod"), mk("ct"), mk("ct2"), mk("sub"), mk("mod"))
        self.campus = Campus.objects.create(name="C", created_by=self.admin,
                                            verification_status=Campus.VerificationStatus.APPROVED)
        self.session = AcademicSession.objects.create(campus=self.campus, name="26", start_date=date(2026, 6, 1),
                                                      end_date=date(2027, 4, 30), is_current=True)
        self.dept = Department.objects.create(campus=self.campus, name="Science")
        self.dept2 = Department.objects.create(campus=self.campus, name="Arts")
        self.cls = SchoolClass.objects.create(campus=self.campus, session=self.session, name="10", department=self.dept)
        self.cls2 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="11", department=self.dept2)
        self.secA = Section.objects.create(school_class=self.cls, name="A", capacity=2)
        self.secB = Section.objects.create(school_class=self.cls, name="B")
        self.secC = Section.objects.create(school_class=self.cls2, name="A")
        sp = lambda u, r, **k: StaffProfile.objects.create(campus=self.campus, user=u, role=r, **k)
        self.p_admin = sp(self.admin, R.ADMIN)
        self.p_hod = sp(self.hod, R.PRINCIPAL_HOD, department=self.dept)
        self.p_ct = sp(self.ct, R.CLASS_TEACHER)
        self.p_ct2 = sp(self.ct2, R.CLASS_TEACHER)
        self.p_sub = sp(self.sub, R.SUBJECT_TEACHER)
        self.p_mod = sp(self.mod, R.MODERATOR)
        ClassTeacherassigments.objects.create(section=self.secA, staff=self.p_ct)
        ClassTeacherassigments.objects.create(section=self.secC, staff=self.p_ct2)
        self.maths = Subject.objects.create(campus=self.campus, name="Maths")
        self.students = [mk(f"s{i}") for i in range(4)]
        self.parent = mk("par")

    def enroll(self, user, section, **kw):
        return StudentEnrollment.objects.create(student=user, section=section, session=self.session, **kw)

    def url(self, name, **kw):
        return reverse(name, kwargs=kw)

    def as_(self, user):
        self.client.force_authenticate(user=user)


class PermissionMatrixTests(APITestCase):
    def test_every_action_has_known_roles_only(self):
        for action, roles in ROLE_ACTIONS.items():
            self.assertTrue(set(roles) <= set(ALL_ROLES), action)

    def test_student_parent_non_teaching_have_no_management_action(self):
        for role in ("student", "parent", R.NON_TEACHING.value):
            for action in ROLE_ACTIONS:
                self.assertFalse(role_can(role, action), (role, action))

    def test_admin_and_principal_can_everything(self):
        for action in ROLE_ACTIONS:
            self.assertTrue(role_can(R.ADMIN.value, action))
            self.assertTrue(role_can(R.PRINCIPAL_HOD.value, action))

    def test_only_admin_principal_for_campus_wide_actions(self):
        for action in ("campus.structure.manage", "staff.manage", "bulk_import", "audit.view", "capacity.override",
                       "assignments.direct_assign", "panel.overview"):
            self.assertEqual(set(ROLE_ACTIONS[action]), {R.ADMIN.value, R.PRINCIPAL_HOD.value})
            self.assertNotIn(action, SCOPED_ACTIONS)

    def test_moderator_vs_subject_teacher_difference(self):
        self.assertTrue(role_can(R.MODERATOR.value, "attendance.mark_daily"))
        self.assertFalse(role_can(R.SUBJECT_TEACHER.value, "attendance.mark_daily"))
        self.assertTrue(role_can(R.MODERATOR.value, "notice.post_section"))
        self.assertFalse(role_can(R.SUBJECT_TEACHER.value, "notice.post_section"))


class ModeratorScopeTests(T4Base):
    def test_moderator_scoped_to_assigned_sections_only(self):
        SubjectTeacherassigments.objects.create(section=self.secA, subject=self.maths, staff=self.p_mod,
                                                status=SubjectTeacherassigments.Status.APPROVED)
        c = self.campus.id
        self.assertTrue(user_can(self.mod, c, "attendance.mark_daily", section_id=self.secA.id))
        self.assertTrue(user_can(self.mod, c, "notice.post_section", section_id=self.secA.id))
        self.assertFalse(user_can(self.mod, c, "attendance.mark_daily", section_id=self.secB.id))
        self.assertFalse(user_can(self.mod, c, "campus.structure.manage"))

    def test_pending_assignment_gives_no_scope(self):
        SubjectTeacherassigments.objects.create(section=self.secA, subject=self.maths, staff=self.p_mod)
        self.assertFalse(user_can(self.mod, self.campus.id, "attendance.mark_daily", section_id=self.secA.id))

    def test_direct_assign_by_admin_is_approved_immediately(self):
        self.as_(self.admin)
        with mock.patch("campus.bridge.notify"):
            r = self.client.post(self.url("subject-teacher-assigments-list"),
                                 {"section": self.secB.id, "subject": self.maths.id, "staff": self.p_mod.id, "direct": True})
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "approved")
        self.assertTrue(CampusAuditLog.objects.filter(action="subject_teacher.direct_assign").exists())

    def test_direct_flag_ignored_for_non_admin(self):
        self.as_(self.sub)
        r = self.client.post(self.url("subject-teacher-assigments-list"),
                             {"section": self.secB.id, "subject": self.maths.id, "staff": self.p_sub.id, "direct": True})
        if r.status_code == 201:
            self.assertEqual(r.data["status"], "pending")


class ParticipantsTests(T4Base):
    def setUp(self):
        super().setUp()
        self.e = [self.enroll(self.students[0], self.secA, roll_number="1"),
                  self.enroll(self.students[1], self.secA, roll_number="2"),
                  self.enroll(self.students[2], self.secC, roll_number="1")]
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.students[0])

    def get(self, user, **params):
        self.as_(user)
        return self.client.get(self.url("campus-participants", pk=self.campus.id), params)

    def test_admin_sees_everyone(self):
        r = self.get(self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 6 + 3 + 1)

    def test_category_filter_and_pagination(self):
        r = self.get(self.admin, category="student", page_size=2)
        self.assertEqual((r.data["count"], len(r.data["results"])), (3, 2))
        self.assertIsNotNone(r.data["next"])
        r2 = self.get(self.admin, category="student", page_size=2, page=2)
        self.assertEqual(len(r2.data["results"]), 1)

    def test_concat_page_crosses_sources(self):
        r = self.get(self.admin, page_size=7, page=1)
        cats = [x["category"] for x in r.data["results"]]
        self.assertEqual(len(cats), 7)
        self.assertIn("student", cats)

    def test_hod_limited_to_department(self):
        r = self.get(self.hod, category="student")
        self.assertEqual(r.data["count"], 2)  # secA only (Science); secC is Arts
        r = self.get(self.hod, category="parent")
        self.assertEqual(r.data["count"], 1)

    def test_class_teacher_sees_only_own_section(self):
        r = self.get(self.ct, category="student")
        self.assertEqual(r.data["count"], 2)
        r = self.get(self.ct2, category="student")
        self.assertEqual(r.data["count"], 1)
        names = {x["user"]["username"] for x in self.get(self.ct, category="subject_teacher").data["results"]}
        self.assertNotIn("sub", names)  # not attached to ct's section

    def test_others_forbidden(self):
        for u in (self.sub, self.mod, self.students[0], self.parent):
            self.assertEqual(self.get(u).status_code, 403, u.username)

    def test_search_and_bad_category(self):
        r = self.get(self.admin, category="student", q="s1")
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(self.get(self.admin, category="nope").status_code, 400)

    def test_summary_counts(self):
        self.as_(self.admin)
        r = self.client.get(self.url("campus-participants-summary", pk=self.campus.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["categories"]["student"], 3)
        self.assertEqual(r.data["categories"]["parent"], 1)
        sec = next(s for c in r.data["classes"] for s in c["sections"] if s["section"] == str(self.secA.id))
        self.assertEqual((sec["students"], sec["capacity"], sec["is_full"]), (2, 2, True))


class RosterTests(T4Base):
    def post_enroll(self, user, student, section, **extra):
        self.as_(user)
        with mock.patch("campus.bridge.sync_section_group"):
            return self.client.post(self.url("student-enrollment-list"),
                                    {"student": student.id, "section": section.id, "session": self.session.id, **extra})

    def test_roll_number_auto_assigned_sequentially(self):
        r1 = self.post_enroll(self.admin, self.students[0], self.secB)
        r2 = self.post_enroll(self.admin, self.students[1], self.secB)
        self.assertEqual((r1.data["roll_number"], r2.data["roll_number"]), ("1", "2"))

    def test_capacity_blocks_then_admin_override(self):
        self.post_enroll(self.admin, self.students[0], self.secA)
        self.post_enroll(self.admin, self.students[1], self.secA)
        r = self.post_enroll(self.admin, self.students[2], self.secA)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.data["code"], "section_full")
        r = self.post_enroll(self.admin, self.students[2], self.secA, override_capacity=True)
        self.assertEqual(r.status_code, 201, r.data)

    def test_class_teacher_cannot_override_capacity(self):
        self.post_enroll(self.ct, self.students[0], self.secA)
        self.post_enroll(self.ct, self.students[1], self.secA)
        r = self.post_enroll(self.ct, self.students[2], self.secA, override_capacity=True)
        self.assertEqual(r.status_code, 400)

    def test_class_teacher_only_own_section(self):
        self.assertEqual(self.post_enroll(self.ct, self.students[0], self.secA).status_code, 201)
        self.assertEqual(self.post_enroll(self.ct, self.students[1], self.secC).status_code, 403)

    def test_duplicate_active_enroll_rejected_cleanly(self):
        self.post_enroll(self.admin, self.students[0], self.secB)
        r = self.post_enroll(self.admin, self.students[0], self.secB)
        self.assertEqual((r.status_code, r.data["code"]), (400, "already_enrolled"))
        self.assertEqual(StudentEnrollment.objects.filter(student=self.students[0], section=self.secB).count(), 1)

    def test_transfer_moves_row_and_checks_target_capacity(self):
        en = self.enroll(self.students[0], self.secB, roll_number="1")
        self.enroll(self.students[1], self.secA, roll_number="1")
        self.enroll(self.students[2], self.secA, roll_number="2")  # secA now full
        self.as_(self.admin)
        with mock.patch("campus.bridge.sync_section_group"):
            r = self.client.post(self.url("student-enrollment-transfer", pk=en.id), {"section": self.secA.id})
            self.assertEqual((r.status_code, r.data["code"]), (400, "section_full"))
            r = self.client.post(self.url("student-enrollment-transfer", pk=en.id),
                                 {"section": self.secA.id, "override_capacity": True})
        self.assertEqual(r.status_code, 200, r.data)
        en.refresh_from_db()
        self.assertEqual(en.status, StudentEnrollment.Status.TRANSFERRED)
        self.assertIsNotNone(en.left_at)
        self.assertTrue(StudentEnrollment.objects.filter(student=self.students[0], section=self.secA,
                                                         status="active", roll_number="3").exists())
        self.assertTrue(CampusAuditLog.objects.filter(action="enrollment.transfer").exists())

    def test_class_teacher_transfer_only_from_own_section(self):
        en = self.enroll(self.students[0], self.secC, roll_number="1")
        self.as_(self.ct)
        r = self.client.post(self.url("student-enrollment-transfer", pk=en.id), {"section": self.secB.id})
        self.assertEqual(r.status_code, 403)

    def test_delete_is_soft_withdraw(self):
        en = self.enroll(self.students[0], self.secB, roll_number="1")
        self.as_(self.admin)
        with mock.patch("campus.bridge.sync_section_group"):
            r = self.client.delete(self.url("student-enrollment-detail", pk=en.id))
        self.assertEqual(r.status_code, 204)
        en.refresh_from_db()
        self.assertEqual(en.status, StudentEnrollment.Status.WITHDRAWN)

    def test_withdrawn_student_can_be_reenrolled(self):
        en = self.enroll(self.students[0], self.secB, roll_number="1", status="withdrawn")
        r = self.post_enroll(self.admin, self.students[0], self.secB)
        self.assertEqual(r.status_code, 201, r.data)
        en.refresh_from_db()
        self.assertEqual(en.status, "active")

    def test_dashboard_permissions_and_payload(self):
        self.enroll(self.students[0], self.secA, roll_number="1")
        self.as_(self.ct)
        r = self.client.get(self.url("section-dashboard", pk=self.secA.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"]["enrolled"], 1)
        self.assertEqual(r.data["count"]["capacity"], 2)
        self.assertEqual(self.client.get(self.url("section-dashboard", pk=self.secC.id)).status_code, 403)
        self.as_(self.students[0])
        self.assertEqual(self.client.get(self.url("section-dashboard", pk=self.secA.id)).status_code, 403)

    def test_section_roster_paginated(self):
        self.enroll(self.students[0], self.secA, roll_number="1")
        self.as_(self.ct)
        r = self.client.get(self.url("section-roster", pk=self.secA.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["capacity"]["seats_left"], 1)

    def test_timetable_permission_for_moderator_scope(self):
        SubjectTeacherassigments.objects.create(section=self.secA, subject=self.maths, staff=self.p_mod,
                                                status=SubjectTeacherassigments.Status.APPROVED)
        self.assertTrue(user_can(self.mod, self.campus.id, "timetable.manage", section_id=self.secA.id))
        self.assertFalse(user_can(self.mod, self.campus.id, "timetable.manage", section_id=self.secB.id))


class PanelTests(T4Base):
    def test_overview_and_setup_status_admin_only(self):
        self.as_(self.admin)
        r = self.client.get(self.url("campus-control-panel-overview", pk=self.campus.id))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["counts"]["sections"], 3)
        self.assertEqual(r.data["attention"]["sections_without_class_teacher"], 1)
        self.assertIn("setup", r.data)
        r = self.client.get(self.url("campus-setup-status", pk=self.campus.id))
        keys = [s["key"] for s in r.data["steps"]]
        self.assertEqual(keys[:3], ["session", "departments", "classes"])
        self.as_(self.ct)
        self.assertEqual(self.client.get(self.url("campus-control-panel-overview", pk=self.campus.id)).status_code, 403)

    def test_my_permissions(self):
        self.as_(self.mod)
        r = self.client.get(self.url("campus-my-permissions", pk=self.campus.id))
        self.assertEqual(r.data["role"], "moderator")
        self.assertFalse(r.data["actions"]["bulk_import"])

    def test_matrix_and_bulk_assign_with_conflicts(self):
        self.as_(self.admin)
        r = self.client.get(self.url("campus-assignment-matrix", pk=self.campus.id))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(row["staff"] == str(self.p_ct.id) for row in r.data["rows"]))
        body = {"assignments": [
            {"staff": str(self.p_sub.id), "section": str(self.secA.id), "kind": "subject", "subject": str(self.maths.id)},
            {"staff": str(self.p_mod.id), "section": str(self.secA.id), "kind": "subject", "subject": str(self.maths.id)},
            {"staff": str(self.p_ct.id), "section": str(self.secB.id), "kind": "class_teacher"},
            {"staff": str(self.p_ct2.id), "section": str(self.secA.id), "kind": "class_teacher"},
        ]}
        dry = self.client.post(self.url("campus-bulk-assign", pk=self.campus.id), {**body, "dry_run": True}, format="json")
        self.assertEqual(dry.data["summary"]["conflict"], 2)
        self.assertFalse(SubjectTeacherassigments.objects.exists())
        with mock.patch("campus.bridge.notify"), mock.patch("campus.bridge.sync_section_group"), \
                mock.patch("campus.bridge.create_section_group"):
            r = self.client.post(self.url("campus-bulk-assign", pk=self.campus.id), body, format="json")
        self.assertEqual(r.data["summary"]["created"], 2)
        self.assertEqual(r.data["summary"]["conflict"], 2)
        self.assertEqual(SubjectTeacherassigments.objects.filter(status="approved").count(), 1)
        self.assertTrue(any("class_teacher_of_multiple_sections" in x["warnings"] for x in r.data["results"]))
        self.assertTrue(CampusAuditLog.objects.filter(action="assignment.bulk").exists())

    def test_hod_cannot_assign_outside_department(self):
        self.as_(self.hod)
        r = self.client.post(self.url("campus-bulk-assign", pk=self.campus.id), {"assignments": [
            {"staff": str(self.p_sub.id), "section": str(self.secC.id), "kind": "subject", "subject": str(self.maths.id)}]},
            format="json")
        self.assertEqual(r.data["summary"]["conflict"], 1)

    def test_bulk_assign_forbidden_for_teacher(self):
        self.as_(self.ct)
        r = self.client.post(self.url("campus-bulk-assign", pk=self.campus.id), {"assignments": [{}]}, format="json")
        self.assertEqual(r.status_code, 403)


class BulkImportAuditTests(T4Base):
    def upload(self, kind, text, dry_run="true", user=None):
        self.as_(user or self.admin)
        f = SimpleUploadedFile("x.csv", text.encode(), content_type="text/csv")
        with mock.patch("campus.bridge.sync_section_group"):
            return self.client.post(self.url("campus-bulk-import", pk=self.campus.id),
                                    {"kind": kind, "file": f, "dry_run": dry_run}, format="multipart")

    def test_subjects_dry_run_then_apply(self):
        csv_text = "name,code\nPhysics,PHY\nMaths,MAT\n,X\n"
        r = self.upload("subjects", csv_text)
        self.assertEqual((r.data["created"], r.data["skipped"], r.data["error_count"]), (1, 1, 1))
        self.assertFalse(Subject.objects.filter(name="Physics").exists())
        r = self.upload("subjects", csv_text, dry_run="false")
        self.assertTrue(Subject.objects.filter(name="Physics", code="PHY").exists())
        self.assertTrue(CampusAuditLog.objects.filter(action="import.subjects").exists())

    def test_students_import_respects_capacity_and_reports_rows(self):
        text = "username,class,section,roll_number\ns0,10,A,\ns1,10,A,\ns2,10,A,\nghost,10,A,\ns3,99,Z,\n"
        r = self.upload("students", text)
        errs = {e["row"]: e["error"] for e in r.data["errors"]}
        self.assertEqual(r.data["created"], 2)
        self.assertIn("full", errs[4])
        self.assertIn("not found", errs[5])
        self.assertIn("not found", errs[6])
        self.assertEqual(StudentEnrollment.objects.count(), 0)
        r = self.upload("students", text, dry_run="false")
        self.assertEqual(StudentEnrollment.objects.filter(section=self.secA).count(), 2)

    def test_staff_import_and_missing_column(self):
        extra = User.objects.create_user(username="newt", password="x12345678")
        r = self.upload("staff", "username,role\nnewt,moderator\n", dry_run="false")
        self.assertEqual(r.data["created"], 1)
        self.assertTrue(StaffProfile.objects.filter(user=extra, role="moderator").exists())
        self.assertEqual(self.upload("staff", "foo\nbar\n").status_code, 400)

    def test_enrollments_kind_moves_students(self):
        self.enroll(self.students[0], self.secB, roll_number="1")
        r = self.upload("enrollments", "username,class,section\ns0,10,A\n", dry_run="false")
        self.assertEqual(r.data["created"], 1)
        self.assertTrue(StudentEnrollment.objects.filter(student=self.students[0], section=self.secA, status="active").exists())

    def test_import_admin_only_and_pending_campus_blocked(self):
        self.assertEqual(self.upload("subjects", "name\nX\n", user=self.ct).status_code, 403)
        self.campus.verification_status = Campus.VerificationStatus.PENDING
        self.campus.save()
        self.assertEqual(self.upload("students", "username,class,section\ns0,10,A\n").status_code, 403)

    def test_audit_log_endpoint_filters_and_permissions(self):
        self.as_(self.admin)
        r = self.client.post(self.url("department-list"), {"campus": self.campus.id, "name": "Math Dept"})
        self.assertEqual(r.status_code, 201, r.data)
        r = self.client.get(self.url("campus-audit-log", pk=self.campus.id), {"action": "department."})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 1)
        self.assertEqual(r.data["results"][0]["actor"]["username"], "admin")
        self.as_(self.ct)
        self.assertEqual(self.client.get(self.url("campus-audit-log", pk=self.campus.id)).status_code, 403)


class SectionGroupReconcileTests(T4Base):
    def test_expected_members(self):
        from core.classroom_chat_bridge import expected_section_members
        self.enroll(self.students[0], self.secA, roll_number="1")
        self.enroll(self.students[1], self.secA, roll_number="2", status="withdrawn")
        SubjectTeacherassigments.objects.create(section=self.secA, subject=self.maths, staff=self.p_sub,
                                                status=SubjectTeacherassigments.Status.APPROVED)
        m = expected_section_members(self.secA)
        self.assertEqual(m["teacher"], self.ct.id)
        self.assertEqual(m["staff"], {self.sub.id})
        self.assertEqual(m["students"], {self.students[0].id})

    def test_reconcile_skips_disabled_and_no_teacher(self):
        from core.classroom_chat_bridge import reconcile_section_group
        self.assertEqual(reconcile_section_group(self.secA)["skipped"], "disabled")
        self.secB.chat_group_enabled = True
        self.secB.save()
        self.assertEqual(reconcile_section_group(self.secB)["skipped"], "no_class_teacher")

    def test_dry_run_reports_missing_group(self):
        from core.classroom_chat_bridge import reconcile_section_group
        self.secA.chat_group_enabled = True
        self.secA.save()
        self.enroll(self.students[0], self.secA, roll_number="1")
        r = reconcile_section_group(self.secA, dry_run=True)
        self.assertTrue(r["group_missing"])
        self.assertEqual(r["expected_count"], 2)
