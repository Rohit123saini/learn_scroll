"""
tuitionclass/test_parent_optional.py

NEW FEATURE A — optional Parent (TUITION_CLASS_TASK.md §4).

Covers: parent_status none/skipped/invited/linked, teacher-only permissions,
parents_enabled toggle, invite (+ phone share URLs), skip (reversible, never
downgrades linked), bulk link only to "none" students, push only to linked
parents, and the core rule: a parent is NEVER a gate for join / pass
purchase / attendance.

Run: python manage.py test tuitionclass.test_parent_optional
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User
from message.models import ParentAccessCode, ParentToken

from .attendance import (
    build_session_attendance,
    notify_linked_parents,
    parent_status_map,
)
from .models import ClassSession, PassPurchase, SessionAttendance
from .test_attendance import AttendanceBase


class ParentBase(AttendanceBase):
    def setUp(self):
        super().setUp()
        self.classroom.parents_enabled = True
        self.classroom.save()

    def link_parent(self, student, fcm="fcm-1", approved=True):
        code = ParentAccessCode.generate_for(student, label="Mom")
        ParentToken.objects.create(
            parent_access_code=code, token=ParentToken.generate_token(),
            status=ParentToken.Status.APPROVED if approved else ParentToken.Status.PENDING,
            fcm_token=fcm,
        )
        return code

    def as_teacher(self):
        self.client.force_authenticate(self.teacher)

    def base(self):
        return f"/tuitionclass/classrooms/{self.classroom.id}"


class StatusTests(ParentBase):
    def test_default_none_then_skipped_invited_linked(self):
        ids = [self.student.id, self.s2.id, self.s3.id]
        self.assertEqual(set(parent_status_map(self.classroom, ids).values()), {"none"})

        self.as_teacher()
        self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/")
        self.client.post(f"{self.base()}/students/{self.s3.id}/parent/invite/")
        self.link_parent(self.student)
        m = parent_status_map(self.classroom, ids)
        self.assertEqual(m, {self.student.id: "linked", self.s2.id: "skipped", self.s3.id: "invited"})

    def test_pending_device_is_not_linked(self):
        self.link_parent(self.student, approved=False)
        self.assertEqual(parent_status_map(self.classroom, [self.student.id])[self.student.id], "none")

    def test_revoked_code_stops_being_linked(self):
        code = self.link_parent(self.student)
        code.is_active = False
        code.save()
        self.assertEqual(parent_status_map(self.classroom, [self.student.id])[self.student.id], "none")


class EndpointTests(ParentBase):
    def test_list_counts(self):
        self.link_parent(self.student)
        self.as_teacher()
        self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/")
        r = self.client.get(f"{self.base()}/parents/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["counts"], {"none": 1, "skipped": 1, "invited": 0, "linked": 1})
        self.assertEqual(r.data["pending_count"], 1)
        self.assertEqual(len(r.data["students"]), 3)

    def test_permissions(self):
        for who in (self.student, self.other_teacher):
            self.client.force_authenticate(who)
            self.assertEqual(self.client.get(f"{self.base()}/parents/").status_code, 403)
            self.assertEqual(self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/").status_code, 403)
            self.assertEqual(self.client.post(f"{self.base()}/students/{self.s2.id}/parent/invite/").status_code, 403)

    def test_disabled_classroom_rejects_invite_skip_bulk(self):
        self.classroom.parents_enabled = False
        self.classroom.save()
        self.as_teacher()
        for path in (f"/students/{self.s2.id}/parent/invite/", f"/students/{self.s2.id}/parent/skip/", "/parent-codes/bulk/"):
            r = self.client.post(self.base() + path)
            self.assertEqual(r.status_code, 400, path)
            self.assertEqual(r.data["code"], "parents_disabled")

    def test_invite_returns_link_and_phone_urls(self):
        self.as_teacher()
        r = self.client.post(f"{self.base()}/students/{self.s2.id}/parent/invite/", {"phone": "+91 98765-43210"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["parent_status"], "invited")
        self.assertTrue(r.data["code"] and r.data["link"])
        self.assertTrue(r.data["sms_url"].startswith("sms:+919876543210?body="))
        self.assertIn("wa.me/919876543210", r.data["whatsapp_url"])

    def test_invite_reuses_active_code(self):
        self.as_teacher()
        a = self.client.post(f"{self.base()}/students/{self.s2.id}/parent/invite/").data["code"]
        b = self.client.post(f"{self.base()}/students/{self.s2.id}/parent/invite/").data["code"]
        self.assertEqual(a, b)
        self.assertEqual(ParentAccessCode.objects.filter(student=self.s2).count(), 1)

    def test_invite_or_skip_unenrolled_user_404(self):
        stranger = User.objects.create_user(username="x", password="pass12345", email="x@example.com")
        self.as_teacher()
        self.assertEqual(self.client.post(f"{self.base()}/students/{stranger.id}/parent/invite/").status_code, 404)
        self.assertEqual(self.client.post(f"{self.base()}/students/{stranger.id}/parent/skip/").status_code, 404)

    def test_skip_is_reversible_and_never_downgrades_linked(self):
        self.as_teacher()
        self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/")
        r = self.client.post(f"{self.base()}/students/{self.s2.id}/parent/invite/")
        self.assertEqual(r.data["parent_status"], "invited")

        self.link_parent(self.student)
        r = self.client.post(f"{self.base()}/students/{self.student.id}/parent/skip/")
        self.assertEqual(r.data["parent_status"], "linked")
        r = self.client.post(f"{self.base()}/students/{self.student.id}/parent/invite/")
        self.assertEqual(r.data["parent_status"], "linked")


class BulkTests(ParentBase):
    def test_bulk_only_reaches_none_students_and_marks_them_invited(self):
        self.link_parent(self.student)                                            # linked
        self.as_teacher()
        self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/")     # skipped
        with patch("tuitionclass.parent_link_views.create_bell_rows_for_push") as bell:
            r = self.client.post(f"{self.base()}/parent-codes/bulk/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual([x["student_id"] for x in r.data["sent"]], [self.s3.id])
        self.assertEqual(r.data["already_handled_count"], 2)
        self.assertEqual(bell.call_count, 1)
        m = parent_status_map(self.classroom, [self.student.id, self.s2.id, self.s3.id])
        self.assertEqual(m[self.s3.id], "invited")
        self.assertEqual(m[self.s2.id], "skipped")

        # second click: nothing pending any more
        with patch("tuitionclass.parent_link_views.create_bell_rows_for_push") as bell:
            r = self.client.post(f"{self.base()}/parent-codes/bulk/")
        self.assertEqual(r.data["sent_count"], 0)
        bell.assert_not_called()


class NotificationTests(ParentBase):
    PUSH = "message.push_utils.send_parent_push"

    def test_only_linked_parent_gets_push(self):
        self.link_parent(self.student, fcm="tok-linked")
        self.as_teacher()
        self.client.post(f"{self.base()}/students/{self.s2.id}/parent/skip/")
        with patch(self.PUSH) as push:
            for s in (self.student, self.s2, self.s3):
                notify_linked_parents(self.classroom, s.id, title="t", body="b")
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.kwargs["fcm_token"], "tok-linked")

    def test_no_push_when_parents_disabled(self):
        self.link_parent(self.student)
        self.classroom.parents_enabled = False
        self.classroom.save()
        with patch(self.PUSH) as push:
            n = notify_linked_parents(self.classroom, self.student.id, title="t", body="b")
        self.assertEqual(n, 0)
        push.assert_not_called()

    def test_device_without_fcm_token_is_skipped(self):
        self.link_parent(self.student, fcm="")
        with patch(self.PUSH) as push:
            notify_linked_parents(self.classroom, self.student.id, title="t", body="b")
        push.assert_not_called()

    def test_absent_push_fires_once_per_build_and_only_when_linked(self):
        self.link_parent(self.s3, fcm="tok-s3")
        s = self.completed_session()
        self.join(s, self.student)
        self.join(s, self.s2)
        with patch(self.PUSH) as push:
            build_session_attendance(s)
            build_session_attendance(s)          # double-fire
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.kwargs["fcm_token"], "tok-s3")
        self.assertIn("absent", push.call_args.kwargs["title"].lower())

    def test_low_attendance_push_on_threshold_crossing_only(self):
        self.link_parent(self.s3, fcm="tok-s3")
        sessions = [self.completed_session(hours_ago=10 - i) for i in range(4)]
        titles = []
        with patch(self.PUSH, side_effect=lambda **k: titles.append(k["title"])):
            for i, s in enumerate(sessions):
                self.join(s, self.student)
                if i < 3:
                    self.join(s, self.s3)        # s3 attends 3 in a row, then misses the 4th -> 75%
                build_session_attendance(s)
        self.assertEqual([t for t in titles if "Low attendance" in t], [])   # 75% is not < 75%
        s5 = self.completed_session(hours_ago=1)
        self.join(s5, self.student)
        with patch(self.PUSH, side_effect=lambda **k: titles.append(k["title"])):
            build_session_attendance(s5)         # 3/5 = 60% -> crosses
        self.assertEqual(len([t for t in titles if "Low attendance" in t]), 1)


class ParentNeverBlocksTests(ParentBase):
    def test_enroll_join_attendance_work_with_parents_on_and_nobody_linked(self):
        """parents_enabled=True with zero parents / all skipped must change nothing."""
        self.as_teacher()
        for s in (self.student, self.s2, self.s3):
            self.client.post(f"{self.base()}/students/{s.id}/parent/skip/")
        self.assertTrue(self.classroom.has_access(self.student))
        s = self.completed_session()
        self.join(s, self.student)
        build_session_attendance(s)
        self.assertEqual(self.status_of(s, self.student), "present")
        self.assertEqual(self.status_of(s, self.s3), "absent")

    def test_flag_default_off_and_settable_via_api(self):
        from .models import Classroom
        self.assertFalse(Classroom.objects.create(teacher=self.teacher, title="New").parents_enabled)
        self.as_teacher()
        r = self.client.patch(f"/tuitionclass/classrooms/{self.classroom.id}/",
                              {"parents_enabled": False, "attendance_min_percent": 60, "attendance_edit_days": 3}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.classroom.refresh_from_db()
        self.assertEqual((self.classroom.parents_enabled, self.classroom.attendance_min_percent, self.classroom.attendance_edit_days),
                         (False, 60, 3))
        self.client.force_authenticate(self.other_teacher)
        r = self.client.patch(f"/tuitionclass/classrooms/{self.classroom.id}/", {"parents_enabled": True}, format="json")
        self.assertIn(r.status_code, (403, 404))
        r = self.as_teacher() or self.client.patch(f"/tuitionclass/classrooms/{self.classroom.id}/", {"attendance_min_percent": 0}, format="json")
        self.assertEqual(r.status_code, 400)
