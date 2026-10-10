# campus/tests_t4.py
"""
T4 §E (student visibility), §F (doubts) and §G (online/offline classes +
5-minute reminder). Run: `python manage.py test campus.tests_t4`.
"""
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from . import tasks as campus_tasks
from .models import (
    AcademicSession, Attendance, Campus, CampusDoubt, CampusLiveSession, CampusParentLink, ClassMode,
    ClassTeacherassigments, Department, Notice, SchoolClass, Section, StaffProfile, StudentEnrollment,
    Subject, SubjectTeacherassigments, TimeSlot, TimetableEntry, TimetableReminderLog,
)

User = get_user_model()


def rows(resp):
    data = resp.data
    return data.get("results", data) if isinstance(data, dict) else data


class T4Base(APITestCase):
    def setUp(self):
        mk = lambda n: User.objects.create_user(username=n, password="pass12345")
        self.admin, self.teacher, self.other_teacher = mk("admin"), mk("teacher"), mk("teacher2")
        self.sa1, self.sa2, self.sb, self.parent = mk("sa1"), mk("sa2"), mk("sb"), mk("parent")
        self.campus = Campus.objects.create(name="GV", created_by=self.admin)
        self.session = AcademicSession.objects.create(
            campus=self.campus, name="2026-27", start_date=date(2026, 6, 1), end_date=date(2027, 4, 30), is_current=True
        )
        StaffProfile.objects.create(campus=self.campus, user=self.admin, role=StaffProfile.Role.ADMIN)
        self.t_staff = StaffProfile.objects.create(campus=self.campus, user=self.teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        self.t2_staff = StaffProfile.objects.create(campus=self.campus, user=self.other_teacher, role=StaffProfile.Role.SUBJECT_TEACHER)
        self.dept = Department.objects.create(campus=self.campus, name="Sci")
        self.cls = SchoolClass.objects.create(campus=self.campus, session=self.session, name="10", department=self.dept)
        self.cls2 = SchoolClass.objects.create(campus=self.campus, session=self.session, name="9")
        self.secA = Section.objects.create(school_class=self.cls, name="A")
        self.secB = Section.objects.create(school_class=self.cls2, name="B")
        self.math = Subject.objects.create(campus=self.campus, name="Maths")
        self.phy = Subject.objects.create(campus=self.campus, name="Physics")
        self.sta = SubjectTeacherassigments.objects.create(
            section=self.secA, subject=self.math, staff=self.t_staff, status=SubjectTeacherassigments.Status.APPROVED)
        SubjectTeacherassigments.objects.create(  # pending -> invisible to students
            section=self.secA, subject=self.phy, staff=self.t_staff, status=SubjectTeacherassigments.Status.PENDING)
        SubjectTeacherassigments.objects.create(
            section=self.secB, subject=self.math, staff=self.t2_staff, status=SubjectTeacherassigments.Status.APPROVED)
        enr = lambda u, s: StudentEnrollment.objects.create(student=u, section=s, session=self.session, roll_number="1")
        self.e_a1, self.e_a2, self.e_b = enr(self.sa1, self.secA), enr(self.sa2, self.secA), enr(self.sb, self.secB)
        CampusParentLink.objects.create(campus=self.campus, parent=self.parent, student=self.sa1)


class StudentVisibilityTests(T4Base):
    def test_student_sees_only_own_section_and_class(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("section-list")))}, {str(self.secA.id)})
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("school-class-list")))}, {str(self.cls.id)})
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("department-list")))}, {str(self.dept.id)})

    def test_other_section_detail_is_404(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.get(reverse("section-detail", args=[self.secB.id])).status_code, 404)

    def test_staff_directory_and_rooms_hidden(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("staff-profile-list"))), [])
        self.assertEqual(rows(self.client.get(reverse("room-list"))), [])

    def test_only_own_enrollment(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("student-enrollment-list")))}, {str(self.e_a1.id)})
        self.assertEqual(self.client.get(reverse("student-enrollment-detail", args=[self.e_a2.id])).status_code, 404)

    def test_subjects_only_approved_in_own_section(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("subject-list")))}, {str(self.math.id)})
        ids = {r["id"] for r in rows(self.client.get(reverse("subject-teacher-assigments-list")))}
        self.assertEqual(ids, {str(self.sta.id)})

    def test_attendance_only_own(self):
        for e in (self.e_a1, self.e_a2):
            Attendance.objects.create(enrollment=e, date=date(2026, 10, 1), status="present", marked_by=self.teacher)
        self.client.force_authenticate(self.sa1)
        got = rows(self.client.get(reverse("attendance-list")))
        self.assertEqual({str(r["enrollment"]) for r in got}, {str(self.e_a1.id)})

    def test_notices_scoped_by_audience(self):
        mk = lambda title, **kw: Notice.objects.create(campus=self.campus, session=self.session, posted_by=self.admin, title=title, body="b", **kw)
        mk("all"); mk("secA", section=self.secA); mk("secB", section=self.secB); mk("cls9", school_class=self.cls2)
        self.client.force_authenticate(self.sa1)
        self.assertEqual({r["title"] for r in rows(self.client.get(reverse("notice-list")))}, {"all", "secA"})

    def test_admin_keeps_full_visibility(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(len(rows(self.client.get(reverse("section-list")))), 2)
        self.assertEqual(len(rows(self.client.get(reverse("student-enrollment-list")))), 3)

    def test_parent_sees_childs_section_only(self):
        self.client.force_authenticate(self.parent)
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("section-list")))}, {str(self.secA.id)})

    def test_old_session_enrollment_not_current(self):
        old = AcademicSession.objects.create(campus=self.campus, name="2025-26", start_date=date(2025, 6, 1), end_date=date(2026, 4, 1))
        oc = SchoolClass.objects.create(campus=self.campus, session=old, name="OLD")
        osec = Section.objects.create(school_class=oc, name="Z")
        StudentEnrollment.objects.create(student=self.sa1, section=osec, session=old, roll_number="9")
        self.client.force_authenticate(self.sa1)
        self.assertNotIn(str(osec.id), {r["id"] for r in rows(self.client.get(reverse("section-list")))})

    def test_student_cannot_modify_or_delete_campus(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.patch(reverse("campus-detail", args=[self.campus.id]), {"name": "x"}).status_code, 403)
        self.assertEqual(self.client.delete(reverse("campus-detail", args=[self.campus.id])).status_code, 403)
        self.campus.refresh_from_db(); self.assertEqual(self.campus.name, "GV")

    def test_analytics_hidden_from_students(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual(rows(self.client.get(reverse("campus-analytics-snapshot-list"))), [])


class OnlineOfflineVisibilityTests(T4Base):
    def setUp(self):
        super().setUp()
        self.slot_on = TimeSlot.objects.create(campus=self.campus, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))
        self.slot_off = TimeSlot.objects.create(campus=self.campus, day_of_week=2, start_time=time(11, 0), end_time=time(12, 0))
        mk = lambda slot, mode: TimetableEntry.objects.create(
            section=self.secA, subject=self.math, staff=self.t_staff, time_slot=slot, session=self.session, mode=mode)
        self.e_on, self.e_off = mk(self.slot_on, ClassMode.ONLINE), mk(self.slot_off, ClassMode.OFFLINE)

    def test_student_gets_no_time_for_offline_period(self):
        self.client.force_authenticate(self.sa1)
        by_id = {r["id"]: r for r in rows(self.client.get(reverse("timetable-entry-list")))}
        off, on = by_id[str(self.e_off.id)], by_id[str(self.e_on.id)]
        self.assertIsNone(off["time_slot"]); self.assertTrue(off["time_hidden"]); self.assertEqual(off["label"], "Offline class")
        self.assertEqual(str(on["time_slot"]), str(self.slot_on.id)); self.assertEqual(on["time_slot_detail"]["start_time"], "09:00:00")

    def test_student_cannot_list_offline_time_slot(self):
        self.client.force_authenticate(self.sa1)
        self.assertEqual({r["id"] for r in rows(self.client.get(reverse("time-slot-list")))}, {str(self.slot_on.id)})

    def test_staff_sees_offline_time(self):
        self.client.force_authenticate(self.admin)
        by_id = {r["id"]: r for r in rows(self.client.get(reverse("timetable-entry-list")))}
        self.assertEqual(str(by_id[str(self.e_off.id)]["time_slot"]), str(self.slot_off.id))
        self.assertFalse(by_id[str(self.e_off.id)]["time_hidden"])

    def test_offline_live_session_hides_time_and_has_no_room(self):
        when = timezone.now() + timedelta(days=1)
        self.client.force_authenticate(self.teacher)
        with mock.patch("campus.views.bridge.provision_video_room", return_value="room-1") as prov:
            r = self.client.post(reverse("campus-live-session-list"), {
                "section": str(self.secA.id), "subject": str(self.math.id), "teacher": str(self.t_staff.id),
                "scheduled_at": when.isoformat(), "mode": "offline"}, format="json")
            self.assertEqual(r.status_code, 201, r.data)
            prov.assert_not_called()
        sess = CampusLiveSession.objects.get(pk=r.data["id"])
        self.assertEqual(sess.room_id, "")
        notice = Notice.objects.get(section=self.secA)
        self.assertNotIn(str(when.year), notice.body)
        self.client.force_authenticate(self.sa1)
        got = self.client.get(reverse("campus-live-session-detail", args=[sess.id])).data
        self.assertIsNone(got["scheduled_at"]); self.assertTrue(got["time_hidden"])
        self.assertEqual(self.client.post(reverse("campus-live-session-join", args=[sess.id])).status_code, 400)

    def test_my_classes_cards(self):
        CampusLiveSession.objects.create(section=self.secA, subject=self.math, teacher=self.t_staff,
                                         scheduled_at=timezone.now() + timedelta(hours=1), mode=ClassMode.OFFLINE)
        self.client.force_authenticate(self.sa1)
        cards = self.client.get("/campus/my/classes/").data if False else self.client.get(reverse("campus-my-classes")).data
        self.assertEqual(len(cards), 1)  # only the APPROVED subject-class
        c = cards[0]
        self.assertEqual(c["subject_name"], "Maths")
        self.assertEqual(c["teacher"]["user_id"], self.teacher.id)
        self.assertEqual(c["next_session"]["mode"], "offline")
        self.assertIsNone(c["next_session"]["starts_at"]); self.assertEqual(c["next_session"]["label"], "Offline class")
        self.assertIsNotNone(c["next_online_session"]["starts_at"])  # Monday online weekly slot


class ClassReminderTests(T4Base):
    def _live(self, minutes, **kw):
        kw.setdefault("mode", ClassMode.ONLINE)
        return CampusLiveSession.objects.create(
            section=self.secA, subject=self.math, teacher=self.t_staff,
            scheduled_at=timezone.now() + timedelta(minutes=minutes), **kw)

    def _run(self):
        with mock.patch("campus.tasks.bridge.notify") as n:
            campus_tasks.send_class_start_reminders()
        return n

    def test_sends_once_to_students_and_teacher(self):
        s = self._live(4)
        n = self._run()
        self.assertEqual(n.call_count, 1)
        kw = n.call_args.kwargs
        self.assertEqual(set(kw["users"]), {self.sa1.id, self.sa2.id, self.teacher.id})
        self.assertEqual(kw["notif_type"], "campus_class_starting")
        s.refresh_from_db(); self.assertIsNotNone(s.reminder_sent_at)
        self.assertEqual(self._run().call_count, 0)  # idempotent

    def test_not_sent_for_offline_far_cancelled_past_or_disabled(self):
        self._live(4, mode=ClassMode.OFFLINE)
        self._live(30)
        self._live(3, status=CampusLiveSession.Status.CANCELLED)
        self._live(-2)
        self.assertEqual(self._run().call_count, 0)
        self._live(3)
        self.campus.class_reminders_enabled = False; self.campus.save()
        self.assertEqual(self._run().call_count, 0)

    def test_reschedule_rearms_reminder(self):
        s = self._live(4)
        self._run()
        self.client.force_authenticate(self.teacher)
        r = self.client.patch(reverse("campus-live-session-detail", args=[s.id]),
                              {"scheduled_at": (timezone.now() + timedelta(minutes=3)).isoformat()}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        s.refresh_from_db(); self.assertIsNone(s.reminder_sent_at)
        self.assertEqual(self._run().call_count, 1)

    def _timetable(self, mode):
        # weekly Monday 09:00 slot; "now" = Monday 2026-10-05 08:56 local
        slot = TimeSlot.objects.create(campus=self.campus, day_of_week=1, start_time=time(9, 0), end_time=time(10, 0))
        entry = TimetableEntry.objects.create(section=self.secA, subject=self.math, staff=self.t_staff,
                                              time_slot=slot, session=self.session, mode=mode)
        now = datetime(2026, 10, 5, 8, 56, tzinfo=timezone.get_current_timezone())
        return entry, now

    def test_timetable_online_slot_fires_once_per_day(self):
        entry, now = self._timetable(ClassMode.ONLINE)
        with mock.patch.object(timezone, "now", return_value=now), mock.patch("campus.tasks.bridge.notify") as n:
            campus_tasks.send_class_start_reminders()
            campus_tasks.send_class_start_reminders()
        self.assertEqual(n.call_count, 1)
        self.assertEqual(TimetableReminderLog.objects.filter(entry=entry).count(), 1)
        self.assertIn("09:00 AM", n.call_args.kwargs["body"])

    def test_timetable_offline_slot_never_fires(self):
        _, now = self._timetable(ClassMode.OFFLINE)
        with mock.patch.object(timezone, "now", return_value=now), mock.patch("campus.tasks.bridge.notify") as n:
            campus_tasks.send_class_start_reminders()
        self.assertEqual(n.call_count, 0)

    def test_timetable_slot_with_explicit_live_session_not_doubled(self):
        _, now = self._timetable(ClassMode.ONLINE)
        CampusLiveSession.objects.create(section=self.secA, subject=self.math, teacher=self.t_staff,
                                         scheduled_at=now + timedelta(minutes=4), mode=ClassMode.ONLINE)
        with mock.patch.object(timezone, "now", return_value=now), mock.patch("campus.tasks.bridge.notify") as n:
            campus_tasks.send_class_start_reminders()
        self.assertEqual(n.call_count, 1)  # the live-session one only


class DoubtTests(T4Base):
    def _post(self, user=None, **kw):
        self.client.force_authenticate(user or self.sa1)
        data = {"section": str(self.secA.id), "subject": str(self.math.id), "text": "How to factorise?"}
        data.update(kw)
        with mock.patch("campus.views.bridge.notify") as n:
            r = self.client.post(reverse("campus-doubt-list"), data, format="json")
        return r, n

    def test_student_posts_and_teacher_notified(self):
        r, n = self._post()
        self.assertEqual(r.status_code, 201, r.data)
        d = CampusDoubt.objects.get(pk=r.data["id"])
        self.assertEqual((d.campus_id, d.session_id, d.author_id), (self.campus.id, self.session.id, self.sa1.id))
        self.assertEqual(n.call_args.kwargs["users"], [self.teacher.id])

    def test_cannot_post_for_other_section_or_pending_subject(self):
        self.assertEqual(self._post(section=str(self.secB.id))[0].status_code, 403)
        self.assertEqual(self._post(subject=str(self.phy.id))[0].status_code, 400)
        self.assertEqual(self._post(user=self.teacher)[0].status_code, 403)

    def test_privacy_default(self):
        r, _ = self._post()
        did = r.data["id"]
        seen = lambda u: self.client.force_authenticate(u) or self.client.get(reverse("campus-doubt-detail", args=[did])).status_code
        self.assertEqual(seen(self.sa1), 200)       # author
        self.assertEqual(seen(self.teacher), 200)   # subject teacher
        self.assertEqual(seen(self.admin), 200)     # admin
        self.assertEqual(seen(self.parent), 200)    # linked parent
        self.assertEqual(seen(self.sa2), 404)       # classmate
        self.assertEqual(seen(self.sb), 404)        # other section
        self.assertEqual(seen(self.other_teacher), 404)  # other subject-class teacher

    def test_public_toggle_needs_campus_flag(self):
        r, _ = self._post()
        url = reverse("campus-doubt-set-public", args=[r.data["id"]])
        self.assertEqual(self.client.post(url, {"is_public": True}, format="json").status_code, 400)
        self.campus.doubts_public_allowed = True; self.campus.save()
        self.assertEqual(self.client.post(url, {"is_public": True}, format="json").status_code, 200)
        self.client.force_authenticate(self.sa2)
        self.assertEqual(self.client.get(reverse("campus-doubt-detail", args=[r.data["id"]])).status_code, 200)
        # classmate is read-only
        self.assertEqual(self.client.post(reverse("campus-doubt-reply", args=[r.data["id"]]), {"text": "hi"}, format="json").status_code, 403)

    def test_reply_flow_and_status(self):
        r, _ = self._post()
        did = r.data["id"]
        url = reverse("campus-doubt-reply", args=[did])
        self.client.force_authenticate(self.teacher)
        with mock.patch("campus.views.bridge.notify") as n:
            rr = self.client.post(url, {"text": "Use identity"}, format="json")
        self.assertEqual(rr.status_code, 201); self.assertTrue(rr.data["is_staff_reply"])
        self.assertEqual(n.call_args.kwargs["users"], [self.sa1.id])
        self.assertEqual(CampusDoubt.objects.get(pk=did).status, "answered")
        self.client.force_authenticate(self.sa1)
        with mock.patch("campus.views.bridge.notify"):
            self.client.post(url, {"text": "still unclear"}, format="json")
        self.assertEqual(CampusDoubt.objects.get(pk=did).status, "open")
        self.assertEqual(self.client.post(reverse("campus-doubt-resolve", args=[did])).status_code, 200)
        self.assertEqual(CampusDoubt.objects.get(pk=did).status, "resolved")
        detail = self.client.get(reverse("campus-doubt-detail", args=[did])).data
        self.assertEqual(len(detail["replies"]), 2)

    def test_other_subject_teacher_cannot_reply_and_soft_delete(self):
        r, _ = self._post()
        did = r.data["id"]
        self.client.force_authenticate(self.other_teacher)
        self.assertEqual(self.client.post(reverse("campus-doubt-reply", args=[did]), {"text": "x"}, format="json").status_code, 404)
        self.client.force_authenticate(self.sa1)
        self.assertEqual(self.client.delete(reverse("campus-doubt-detail", args=[did])).status_code, 204)
        self.assertFalse(CampusDoubt.objects.get(pk=did).is_active)
        self.assertEqual(self.client.get(reverse("campus-doubt-detail", args=[did])).status_code, 404)

    def test_doubts_disabled_blocks_posting(self):
        self.campus.doubts_enabled = False; self.campus.save()
        self.assertEqual(self._post()[0].status_code, 403)
