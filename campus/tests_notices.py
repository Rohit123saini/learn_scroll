# campus/tests_notices.py
"""
Communication: school-wide / class-wise notices, scheduled notices, emergency broadcast.

Run: `python manage.py test campus.tests_notices` (written but NOT run here: Django is not installed in
the authoring sandbox). The pure rules are covered by `tests_notice_rules.py` (no Django needed).

Push is never sent: `campus.notices.bridge.notify` is mocked and the calls inspected.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import notices as notices_svc
from .bridge import NotifTypes
from .models import Notice
from .tests_t4_efg import T4Base, rows

User = get_user_model()


class NoticeBase(T4Base):
    def setUp(self):
        super().setUp()
        cache.clear()

    def body(self, **over):
        data = {"campus": str(self.campus.id), "session": str(self.session.id), "title": "Holiday tomorrow", "body": "School closed."}
        data.update(over)
        return data

    def post(self, user, **over):
        self.client.force_authenticate(user)
        with mock.patch("campus.notices.bridge.notify") as notify:
            resp = self.client.post(reverse("notice-list"), self.body(**over), format="json")
        return resp, notify

    def listing(self, user, **params):
        self.client.force_authenticate(user)
        return rows(self.client.get(reverse("notice-list"), {"campus": str(self.campus.id), **params}))


class ImmediateNoticeTests(NoticeBase):
    def test_school_wide_notice_pushes_students_and_staff(self):
        resp, notify = self.post(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], "published")
        self.assertEqual(resp.data["kind"], "normal")
        self.assertTrue(notify.called)
        kw = notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.NOTICE_POSTED)
        self.assertIn(self.sa1.id, kw["users"])
        self.assertNotIn(self.admin.id, kw["users"])  # the poster is not pushed

    def test_students_only_audience_skips_staff(self):
        resp, notify = self.post(self.admin, audience="students")
        users = set(notify.call_args.kwargs["users"])
        self.assertIn(self.sa1.id, users)
        self.assertNotIn(self.t_staff.user_id, users)

    def test_staff_only_notice_is_hidden_from_students(self):
        self.post(self.admin, audience="staff", title="Staff meeting")
        self.assertEqual([n["title"] for n in self.listing(self.sa1)], [])
        self.assertEqual([n["title"] for n in self.listing(self.admin)], ["Staff meeting"])

    def test_class_wise_notice_only_reaches_that_class(self):
        resp, notify = self.post(self.admin, school_class=str(self.secA.school_class_id), audience="students")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        users = set(notify.call_args.kwargs["users"])
        self.assertIn(self.sa1.id, users)

    def test_kind_and_status_cannot_be_forced_by_the_client(self):
        resp, _ = self.post(self.admin, kind="emergency", status="scheduled")
        self.assertEqual(resp.data["kind"], "normal")
        self.assertEqual(resp.data["status"], "published")


class ScheduledNoticeTests(NoticeBase):
    def when(self, **kw):
        return (timezone.now() + timedelta(**kw)).isoformat()

    def test_scheduled_notice_is_stored_not_pushed_and_hidden_from_students(self):
        resp, notify = self.post(self.admin, scheduled_for=self.when(hours=2))
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["status"], "scheduled")
        self.assertFalse(notify.called)
        self.assertEqual(self.listing(self.sa1, status="all"), [])
        self.assertEqual(self.listing(self.admin), [])                       # default list = published only
        self.assertEqual(len(self.listing(self.admin, status="scheduled")), 1)

    def test_schedule_in_the_past_or_too_far_is_rejected(self):
        for kw in ({"minutes": -5}, {"days": 91}):
            resp, notify = self.post(self.admin, scheduled_for=self.when(**kw))
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertFalse(notify.called)

    def test_publish_due_sends_once(self):
        resp, _ = self.post(self.admin, scheduled_for=self.when(hours=1))
        notice = Notice.objects.get(pk=resp.data["id"])
        with mock.patch("campus.notices.bridge.notify") as notify:
            self.assertEqual(notices_svc.publish_due(now=timezone.now()), 0)           # not yet
            self.assertFalse(notify.called)
            later = timezone.now() + timedelta(hours=2)
            self.assertEqual(notices_svc.publish_due(now=later), 1)
            self.assertEqual(notices_svc.publish_due(now=later), 0)                    # second run: nothing
        self.assertEqual(notify.call_count, 1)
        notice.refresh_from_db()
        self.assertEqual(notice.status, "published")
        self.assertIsNotNone(notice.published_at)
        self.assertGreater(notice.recipient_count, 0)

    def test_cancelled_notice_is_never_sent(self):
        resp, _ = self.post(self.admin, scheduled_for=self.when(hours=1))
        self.client.force_authenticate(self.admin)
        r = self.client.post(reverse("notice-cancel", args=[resp.data["id"]]))
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data["status"], "cancelled")
        with mock.patch("campus.notices.bridge.notify") as notify:
            self.assertEqual(notices_svc.publish_due(now=timezone.now() + timedelta(days=1)), 0)
        self.assertFalse(notify.called)

    def test_send_now_publishes_and_cannot_be_repeated(self):
        resp, _ = self.post(self.admin, scheduled_for=self.when(hours=5))
        self.client.force_authenticate(self.admin)
        with mock.patch("campus.notices.bridge.notify") as notify:
            r1 = self.client.post(reverse("notice-send-now", args=[resp.data["id"]]))
            r2 = self.client.post(reverse("notice-send-now", args=[resp.data["id"]]))
        self.assertEqual(r1.status_code, status.HTTP_200_OK, r1.data)
        self.assertEqual(r1.data["status"], "published")
        self.assertEqual(r2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(notify.call_count, 1)

    def test_a_student_cannot_cancel_or_edit(self):
        resp, _ = self.post(self.admin, scheduled_for=self.when(hours=1))
        self.client.force_authenticate(self.sa1)
        self.assertIn(self.client.post(reverse("notice-cancel", args=[resp.data["id"]])).status_code, (403, 404))
        self.assertIn(self.client.patch(reverse("notice-detail", args=[resp.data["id"]]), {"title": "x"}, format="json").status_code, (403, 404))

    def test_published_notice_cannot_be_edited(self):
        resp, _ = self.post(self.admin)
        self.client.force_authenticate(self.admin)
        r = self.client.patch(reverse("notice-detail", args=[resp.data["id"]]), {"title": "Changed"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_409_CONFLICT)


class EmergencyBroadcastTests(NoticeBase):
    def send(self, user, **over):
        self.client.force_authenticate(user)
        data = {"campus": str(self.campus.id), "title": "Fire drill", "body": "Go to the ground now."}
        data.update(over)
        with mock.patch("campus.notices.bridge.notify") as notify:
            resp = self.client.post(reverse("notice-emergency"), data, format="json")
        return resp, notify

    def test_admin_sends_emergency_to_everyone_with_emergency_type(self):
        resp, notify = self.send(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["kind"], "emergency")
        self.assertIsNotNone(resp.data["pin_until"])
        kw = notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_EMERGENCY)
        self.assertTrue(kw["push"])
        self.assertIn(self.sa1.id, kw["users"])

    def test_teacher_and_student_cannot_send_emergency(self):
        for user in (self.t_staff.user, self.sa1):
            resp, notify = self.send(user)
            self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
            self.assertFalse(notify.called)

    def test_double_tap_is_refused(self):
        self.assertEqual(self.send(self.admin)[0].status_code, status.HTTP_201_CREATED)
        resp, notify = self.send(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(notify.called)

    def test_empty_text_is_rejected(self):
        self.assertEqual(self.send(self.admin, body="")[0].status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.send(self.admin, title="  ")[0].status_code, status.HTTP_400_BAD_REQUEST)

    def test_scope_from_another_campus_is_rejected(self):
        resp, _ = self.send(self.admin, section="00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_emergency_is_audit_logged(self):
        from .models import CampusAuditLog
        self.send(self.admin)
        self.assertTrue(CampusAuditLog.objects.filter(campus=self.campus, action="notice.emergency").exists())
