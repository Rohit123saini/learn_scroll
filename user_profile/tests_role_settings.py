"""
S12 - role-aware settings API. NOT RUN by the author (read-only task):
    python manage.py test user_profile.tests_role_settings

Covers: teacher defaults (GET lazily creates, PATCH validation, auto-record needs recording, per-user isolation, new classroom
takes the attendance defaults, existing classroom untouched); parent child-limit (list, set, remove, not-linked = 404, adult /
unknown age = 403, bad numbers = 400, other parent cannot touch the child, child pulled DOWN to the parent limit, child cannot
raise / clear it but can go lower, removing the parent limit hands control back).
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from campus.models import Campus, CampusParentLink
from tuitionclass.models import Classroom

from .models import ParentChildLimit, TeacherLiveDefaults, UserPreference

User = get_user_model()
DEFAULTS_URL = "/profile/teacher/live-defaults/"
CHILDREN_URL = "/profile/parent/children/"
ACTIVITY_URL = "/profile/activity/"
ADULT = datetime.date(1990, 1, 1)
CHILD = datetime.date(timezone.localdate().year - 12, 6, 1)


def make_user(name, dob=ADULT):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob)


def client_for(u):
    c = APIClient()
    c.force_authenticate(u)
    return c


def limit_url(child):
    return f"/profile/parent/children/{child.pk}/limit/"


class TeacherDefaultsTests(TestCase):
    def setUp(self):
        self.teacher = make_user("tina")
        self.c = client_for(self.teacher)

    def test_get_creates_row_with_model_defaults(self):
        res = self.c.get(DEFAULTS_URL)
        self.assertEqual(res.status_code, 200)
        d = res.json()["data"]
        self.assertTrue(d["chat_enabled"])
        self.assertTrue(d["recording_enabled"])
        self.assertFalse(d["recording_auto"])
        self.assertEqual(d["attendance_min_percent"], 50)
        self.assertEqual(d["attendance_edit_days"], 7)
        self.assertEqual(TeacherLiveDefaults.objects.filter(user=self.teacher).count(), 1)

    def test_patch_saves_and_isolated_per_user(self):
        res = self.c.patch(DEFAULTS_URL, {"recording_auto": True, "attendance_min_percent": 75, "parents_enabled": True}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()["data"]["recording_auto"])
        other = client_for(make_user("other"))
        self.assertEqual(other.get(DEFAULTS_URL).json()["data"]["attendance_min_percent"], 50)

    def test_recording_off_switches_auto_off(self):
        self.c.patch(DEFAULTS_URL, {"recording_auto": True}, format="json")
        res = self.c.patch(DEFAULTS_URL, {"recording_enabled": False}, format="json")
        d = res.json()["data"]
        self.assertFalse(d["recording_enabled"])
        self.assertFalse(d["recording_auto"])

    def test_validation_saves_nothing(self):
        for bad in ({"attendance_min_percent": 0}, {"attendance_min_percent": 101}, {"attendance_edit_days": 61},
                    {"chat_enabled": "yes"}, {"attendance_min_percent": True}, {"attendance_min_percent": "abc"}):
            res = self.c.patch(DEFAULTS_URL, bad, format="json")
            self.assertEqual(res.status_code, 400, bad)
        self.assertEqual(self.c.get(DEFAULTS_URL).json()["data"]["attendance_min_percent"], 50)

    def test_login_required(self):
        self.assertIn(APIClient().get(DEFAULTS_URL).status_code, (401, 403))

    def test_new_classroom_takes_attendance_defaults_old_one_does_not(self):
        old = Classroom.objects.create(teacher=self.teacher, title="Old")
        self.c.patch(DEFAULTS_URL, {"attendance_min_percent": 80, "attendance_edit_days": 3}, format="json")
        new = Classroom.objects.create(teacher=self.teacher, title="New")
        new.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((new.attendance_min_percent, new.attendance_edit_days), (80, 3))
        self.assertEqual((old.attendance_min_percent, old.attendance_edit_days), (50, 7))

    def test_explicit_attendance_on_create_wins(self):
        self.c.patch(DEFAULTS_URL, {"attendance_min_percent": 80}, format="json")
        c = Classroom.objects.create(teacher=self.teacher, title="Explicit", attendance_min_percent=60)
        c.refresh_from_db()
        self.assertEqual(c.attendance_min_percent, 60)


class ChildLimitTests(TestCase):
    def setUp(self):
        self.parent = make_user("pa")
        self.child = make_user("kid", dob=CHILD)
        self.campus = Campus.objects.create(name="Green Valley", created_by=make_user("owner"),
                                            verification_status=Campus.VerificationStatus.APPROVED)
        CampusParentLink.objects.create(campus=self.campus, student=self.child, parent=self.parent)
        self.p = client_for(self.parent)
        self.k = client_for(self.child)

    def test_list_children(self):
        d = self.p.get(CHILDREN_URL).json()["data"]
        self.assertEqual(len(d["children"]), 1)
        row = d["children"][0]
        self.assertEqual(row["id"], self.child.pk)
        self.assertTrue(row["can_set_limit"])
        self.assertIsNone(row["my_limit_minutes"])
        self.assertEqual(row["today_minutes"], 0)

    def test_list_empty_for_non_parent(self):
        self.assertEqual(client_for(make_user("rando")).get(CHILDREN_URL).json()["data"]["children"], [])

    def test_set_limit_pulls_child_down(self):
        res = self.p.patch(limit_url(self.child), {"daily_limit_minutes": 90}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["data"]["my_limit_minutes"], 90)
        self.assertEqual(UserPreference.for_user(self.child).daily_limit_minutes, 90)

    def test_child_own_lower_limit_is_kept(self):
        UserPreference.for_user(self.child)
        self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": 45}, format="json")
        self.p.patch(limit_url(self.child), {"daily_limit_minutes": 90}, format="json")
        self.assertEqual(UserPreference.for_user(self.child).daily_limit_minutes, 45)

    def test_child_cannot_raise_or_clear_but_can_lower(self):
        self.p.patch(limit_url(self.child), {"daily_limit_minutes": 90}, format="json")
        for value in (120, None, 0):
            res = self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": value}, format="json")
            self.assertEqual(res.status_code, 403, value)
            self.assertEqual(res.json()["code"], "limit_set_by_parent")
        self.assertEqual(self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": 60}, format="json").status_code, 200)
        self.assertEqual(self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": 90}, format="json").status_code, 200)

    def test_activity_get_shows_parent_limit(self):
        self.p.patch(limit_url(self.child), {"daily_limit_minutes": 90}, format="json")
        self.assertEqual(self.k.get(ACTIVITY_URL).json()["data"]["parent_limit_minutes"], 90)

    def test_remove_limit_hands_control_back(self):
        self.p.patch(limit_url(self.child), {"daily_limit_minutes": 90}, format="json")
        res = self.p.patch(limit_url(self.child), {"daily_limit_minutes": None}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(ParentChildLimit.objects.filter(child=self.child).exists())
        self.assertEqual(self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": None}, format="json").status_code, 200)

    def test_two_parents_smallest_wins(self):
        mom = make_user("mom")
        CampusParentLink.objects.create(campus=self.campus, student=self.child, parent=mom)
        self.p.patch(limit_url(self.child), {"daily_limit_minutes": 120}, format="json")
        client_for(mom).patch(limit_url(self.child), {"daily_limit_minutes": 60}, format="json")
        self.assertEqual(UserPreference.for_user(self.child).daily_limit_minutes, 60)
        self.assertEqual(self.k.patch(ACTIVITY_URL, {"daily_limit_minutes": 100}, format="json").status_code, 403)

    def test_not_linked_is_404(self):
        res = client_for(make_user("stranger")).patch(limit_url(self.child), {"daily_limit_minutes": 60}, format="json")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["code"], "not_linked")
        self.assertFalse(ParentChildLimit.objects.exists())

    def test_adult_child_is_403(self):
        adult = make_user("grown", dob=ADULT)
        CampusParentLink.objects.create(campus=self.campus, student=adult, parent=self.parent)
        res = self.p.patch(limit_url(adult), {"daily_limit_minutes": 60}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "child_not_minor")

    def test_unknown_age_is_403(self):
        unknown = User.objects.create_user(username="nodob", email="nodob@example.com", password="pass12345")
        CampusParentLink.objects.create(campus=self.campus, student=unknown, parent=self.parent)
        self.assertEqual(self.p.patch(limit_url(unknown), {"daily_limit_minutes": 60}, format="json").status_code, 403)

    def test_bad_numbers_are_400(self):
        for bad in (4, 1441, -5, "abc", True):
            res = self.p.patch(limit_url(self.child), {"daily_limit_minutes": bad}, format="json")
            self.assertEqual(res.status_code, 400, bad)
        self.assertEqual(self.p.patch(limit_url(self.child), {}, format="json").status_code, 400)
