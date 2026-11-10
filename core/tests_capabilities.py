"""
S12 - GET /core/me/capabilities/. NOT RUN by the author (read-only task):
    python manage.py test core.tests_capabilities

Covers: plain user = student only (no role sections), classroom owner = teacher, onboarding "teacher" hint = teacher,
campus admin vs plain staff vs teacher, parent needs a real parent link (onboarding "parent" alone is not enough), platform
staff = moderator, PENDING campus grants nothing, and that the endpoint needs a login.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from campus.models import Campus, CampusParentLink, StaffProfile
from tuitionclass.models import Classroom

User = get_user_model()
URL = "/core/me/capabilities/"
ADULT = datetime.date(1990, 1, 1)
CHILD = datetime.date(timezone.localdate().year - 12, 6, 1)


def user(name, dob=ADULT, **kw):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob, **kw)


def approved_campus(owner, name="Green Valley"):
    return Campus.objects.create(name=name, created_by=owner, verification_status=Campus.VerificationStatus.APPROVED)


class CapabilitiesTests(TestCase):
    def get(self, u):
        c = APIClient()
        c.force_authenticate(u)
        res = c.get(URL)
        self.assertEqual(res.status_code, 200, res.content)
        return res.json()

    def test_requires_login(self):
        self.assertIn(APIClient().get(URL).status_code, (401, 403))

    def test_plain_user_is_student_only(self):
        data = self.get(user("plain"))
        self.assertEqual(data["roles"], ["student"])
        self.assertEqual(data["primary_role"], "student")
        self.assertEqual(data["sections"], [])
        self.assertFalse(any(data["can"].values()))
        self.assertFalse(data["partial"])

    def test_classroom_owner_is_teacher(self):
        t = user("tina")
        Classroom.objects.create(teacher=t, title="Physics")
        data = self.get(t)
        self.assertIn("teacher", data["roles"])
        self.assertTrue(data["can"]["teacher_live_defaults"])
        self.assertEqual(data["sections"], ["teacher_tools"])

    def test_onboarding_teacher_hint_is_teacher(self):
        data = self.get(user("hint", onboarding_role="teacher"))
        self.assertIn("teacher", data["roles"])
        self.assertTrue(data["can"]["teacher_live_defaults"])

    def test_campus_admin_gets_campus_shortcut(self):
        a = user("asha")
        campus = approved_campus(a)
        StaffProfile.objects.create(campus=campus, user=a, role=StaffProfile.Role.ADMIN)
        data = self.get(a)
        self.assertIn("campus_admin", data["roles"])
        self.assertIn("campus_staff", data["roles"])
        self.assertEqual(data["primary_role"], "campus_admin")
        self.assertEqual(data["campus"]["admin_id"], str(campus.pk))
        self.assertTrue(data["can"]["campus_settings"])
        self.assertIn("campus_admin", data["sections"])

    def test_non_teaching_staff_is_staff_not_teacher_or_admin(self):
        s = user("clerk")
        campus = approved_campus(user("owner1"))
        StaffProfile.objects.create(campus=campus, user=s, role=StaffProfile.Role.NON_TEACHING)
        data = self.get(s)
        self.assertEqual(data["roles"], ["student", "campus_staff"])
        self.assertFalse(data["can"]["campus_settings"])
        self.assertFalse(data["can"]["teacher_live_defaults"])

    def test_campus_class_teacher_is_teacher(self):
        s = user("ct")
        campus = approved_campus(user("owner2"))
        StaffProfile.objects.create(campus=campus, user=s, role=StaffProfile.Role.CLASS_TEACHER)
        data = self.get(s)
        self.assertIn("teacher", data["roles"])
        self.assertEqual(data["campus"]["teacher_id"], str(campus.pk))

    def test_pending_campus_grants_nothing(self):
        a = user("pend")
        campus = Campus.objects.create(name="Pending School", created_by=a)  # PENDING by default
        StaffProfile.objects.create(campus=campus, user=a, role=StaffProfile.Role.ADMIN)
        data = self.get(a)
        self.assertEqual(data["roles"], ["student"])
        self.assertFalse(data["can"]["campus_settings"])

    def test_parent_needs_a_real_link(self):
        self.assertNotIn("parent", self.get(user("hintparent", onboarding_role="parent"))["roles"])
        p, kid = user("pa"), user("kid", dob=CHILD)
        CampusParentLink.objects.create(campus=approved_campus(user("owner3")), student=kid, parent=p)
        data = self.get(p)
        self.assertIn("parent", data["roles"])
        self.assertTrue(data["can"]["child_limits"])
        self.assertEqual(data["sections"], ["parent_controls"])

    def test_platform_staff_is_moderator(self):
        m = user("mod", is_staff=True)
        data = self.get(m)
        self.assertIn("moderator", data["roles"])
        self.assertEqual(data["primary_role"], "moderator")
        self.assertTrue(data["can"]["moderation_queue"])
        self.assertTrue(data["can"]["study_room_reports"])
        self.assertEqual(data["sections"], ["staff_tools"])

    def test_inactive_staff_row_grants_nothing(self):
        a = user("gone")
        campus = approved_campus(user("owner4"))
        StaffProfile.objects.create(campus=campus, user=a, role=StaffProfile.Role.ADMIN, is_active=False)
        self.assertEqual(self.get(a)["roles"], ["student"])
