"""
tuitionclass/test_attendance.py

NEW FEATURE B — Student Attendance (TUITION_CLASS_TASK.md §5).

Covers:
    1. Auto build from SessionParticipant join/leave (present / late / absent),
       min-percent rule, idempotency, manual rows never overwritten, a session
       that never ran marks nobody absent, trial + not-yet-enrolled students
       are ignored.
    2. Manual edit API: permissions, edit window, validation, audit fields.
    3. Summary + roster API (manager sees all, student sees only self).
    4. compute_attendance_percent_bulk / my-progress read the new table.
    5. Signal hook: COMPLETED -> attendance built.
    6. Daily escrow release (D9) is INDEPENDENT of present/absent.

Run: python manage.py test tuitionclass.test_attendance
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from login.models import User

from .attendance import (
    LATE_GRACE_MINUTES,
    apply_manual_edits,
    attendance_summary,
    build_session_attendance,
)
from .models import (
    ClassPass,
    ClassSession,
    PassDailyCharge,
    PassPurchase,
    SessionAttendance,
    SessionParticipant,
    compute_attendance_percent_bulk,
)
from .tests import TuitionClassTestBase


class AttendanceBase(TuitionClassTestBase):
    def setUp(self):
        super().setUp()
        self.s2 = User.objects.create_user(username="student2", password="pass12345", email="s2@example.com")
        self.s3 = User.objects.create_user(username="student3", password="pass12345", email="s3@example.com")
        for s in (self.student, self.s2, self.s3):
            self.enroll(s)
        self.client = APIClient()

    # -- fixtures ---------------------------------------------------------
    def enroll(self, student, days_ago=5, coins=100):
        p = PassPurchase.objects.create(
            student=student, class_pass=self.class_pass, amount_paid=Decimal(coins), coins_spent=coins,
            status=PassPurchase.Status.SUCCESS, is_active=True,
            expires_at=timezone.now() + timedelta(days=10),
        )
        PassPurchase.objects.filter(pk=p.pk).update(purchased_at=timezone.now() - timedelta(days=days_ago))
        return p

    def completed_session(self, hours_ago=2, minutes=60, **kw):
        start = timezone.now() - timedelta(hours=hours_ago)
        end = start + timedelta(minutes=minutes)
        return self.make_session(
            status_=ClassSession.Status.COMPLETED,
            scheduled_start=start, scheduled_end=end, actual_start=start, actual_end=end, **kw,
        )

    def join(self, session, user, offset_min=0, stay_min=60, is_trial=False):
        start = session.actual_start or session.scheduled_start
        joined = start + timedelta(minutes=offset_min)
        p = SessionParticipant.objects.create(
            session=session, user=user, role=SessionParticipant.Role.STUDENT,
            left_at=joined + timedelta(minutes=stay_min), is_trial=is_trial,
        )
        SessionParticipant.objects.filter(pk=p.pk).update(joined_at=joined)
        return p

    def status_of(self, session, user):
        row = SessionAttendance.objects.filter(session=session, student=user).first()
        return row.status if row else None


class AutoBuildTests(AttendanceBase):
    def test_present_late_absent(self):
        s = self.completed_session()
        self.join(s, self.student, offset_min=1, stay_min=58)                       # full class
        self.join(s, self.s2, offset_min=LATE_GRACE_MINUTES + 5, stay_min=45)       # late but enough
        # s3 never joins
        res = build_session_attendance(s)
        self.assertTrue(res["ran"])
        self.assertEqual(self.status_of(s, self.student), "present")
        self.assertEqual(self.status_of(s, self.s2), "late")
        self.assertEqual(self.status_of(s, self.s3), "absent")

    def test_below_min_percent_is_absent(self):
        s = self.completed_session()
        self.join(s, self.student, offset_min=0, stay_min=20)     # 33% < default 50%
        build_session_attendance(s)
        self.assertEqual(self.status_of(s, self.student), "absent")

    def test_min_percent_is_configurable(self):
        self.classroom.attendance_min_percent = 30
        self.classroom.save()
        s = self.completed_session()
        self.join(s, self.student, offset_min=0, stay_min=20)
        build_session_attendance(s)
        self.assertEqual(self.status_of(s, self.student), "present")

    def test_rejoin_overlap_not_double_counted(self):
        s = self.completed_session()
        self.join(s, self.student, offset_min=0, stay_min=20)
        # second row overlaps the first entirely -> still 20 min, not 40
        self.join(s, self.student, offset_min=0.5, stay_min=20)
        build_session_attendance(s)
        row = SessionAttendance.objects.get(session=s, student=self.student)
        self.assertLessEqual(row.minutes_present, 21)
        self.assertEqual(row.status, "absent")

    def test_idempotent(self):
        s = self.completed_session()
        self.join(s, self.student)
        first = build_session_attendance(s)
        second = build_session_attendance(s)
        self.assertEqual(first["created"], 3)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(SessionAttendance.objects.filter(session=s).count(), 3)

    def test_manual_row_never_overwritten(self):
        s = self.completed_session()
        build_session_attendance(s)                                   # all absent
        apply_manual_edits(s, [{"student": self.s3.id, "status": "excused", "note": "sick"}], self.teacher)
        self.join(s, self.s3)                                         # log changes afterwards
        res = build_session_attendance(s)
        self.assertEqual(res["skipped_manual"], 1)
        row = SessionAttendance.objects.get(session=s, student=self.s3)
        self.assertEqual((row.status, row.source, row.marked_by_id), ("excused", "manual", self.teacher.id))

    def test_session_that_never_ran_marks_nobody(self):
        s = self.make_session(status_=ClassSession.Status.COMPLETED)   # actual_start None, no participants
        res = build_session_attendance(s)
        self.assertFalse(res["ran"])
        self.assertEqual(SessionAttendance.objects.filter(session=s).count(), 0)

    def test_non_completed_session_ignored(self):
        s = self.make_session(status_=ClassSession.Status.LIVE)
        self.assertFalse(build_session_attendance(s)["ran"])

    def test_trial_and_not_yet_enrolled_are_ignored(self):
        late_joiner = User.objects.create_user(username="latecomer", password="pass12345", email="l@example.com")
        self.enroll(late_joiner, days_ago=0)             # bought the pass just now, after the session
        trial_user = User.objects.create_user(username="trialer", password="pass12345", email="t@example.com")
        s = self.completed_session()
        self.join(s, trial_user, is_trial=True)
        build_session_attendance(s)
        self.assertIsNone(self.status_of(s, late_joiner))
        self.assertIsNone(self.status_of(s, trial_user))

    def test_teacher_never_in_roster(self):
        s = self.completed_session()
        build_session_attendance(s)
        self.assertFalse(SessionAttendance.objects.filter(session=s, student=self.teacher).exists())


class SignalHookTests(AttendanceBase):
    def test_completing_a_session_builds_attendance(self):
        start = timezone.now() - timedelta(hours=1)
        s = self.make_session(
            status_=ClassSession.Status.LIVE,
            scheduled_start=start, scheduled_end=start + timedelta(hours=1), actual_start=start,
        )
        p = SessionParticipant.objects.create(session=s, user=self.student, role=SessionParticipant.Role.STUDENT)
        SessionParticipant.objects.filter(pk=p.pk).update(joined_at=start + timedelta(minutes=1))
        with patch("tuitionclass.signals.end_room"), patch("tuitionclass.tasks.build_engagement_report.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                s.status = ClassSession.Status.COMPLETED
                s.save()
        self.assertEqual(self.status_of(s, self.student), "present")   # open row was force-checked-out
        self.assertEqual(self.status_of(s, self.s2), "absent")


class EscrowIndependenceTests(AttendanceBase):
    def test_daily_release_happens_even_when_absent(self):
        """D9: the class-day charge is released whether the student was present or absent."""
        s = self.completed_session(hours_ago=1)
        build_session_attendance(s)
        self.assertEqual(self.status_of(s, self.s3), "absent")
        purchase = PassPurchase.objects.get(student=self.s3)
        charge = purchase.charge_for_session(s)
        self.assertIsNotNone(charge)
        self.assertEqual(PassDailyCharge.objects.filter(purchase=purchase).count(), 1)
        # and a manual edit afterwards doesn't touch the ledger
        before = PassDailyCharge.objects.count()
        apply_manual_edits(s, [{"student": self.s3.id, "status": "present"}], self.teacher)
        self.assertEqual(PassDailyCharge.objects.count(), before)


class AttendanceApiTests(AttendanceBase):
    def url(self, s):
        return f"/tuitionclass/sessions/{s.id}/attendance/"

    def test_manager_sees_full_roster_student_sees_self(self):
        s = self.completed_session()
        self.join(s, self.student)
        build_session_attendance(s)

        self.client.force_authenticate(self.teacher)
        r = self.client.get(self.url(s))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["records"]), 3)
        self.assertTrue(r.data["edit_open"])

        self.client.force_authenticate(self.student)
        r = self.client.get(self.url(s))
        self.assertEqual([x["student"] for x in r.data["records"]], [self.student.id])
        self.assertNotIn("unmarked", r.data)

    def test_outsider_forbidden(self):
        s = self.completed_session()
        self.client.force_authenticate(self.other_teacher)
        self.assertEqual(self.client.get(self.url(s)).status_code, 403)
        self.assertEqual(self.client.patch(self.url(s), {"records": []}, format="json").status_code, 403)

    def test_manager_lists_unmarked_students_before_build(self):
        s = self.completed_session()
        self.client.force_authenticate(self.teacher)
        r = self.client.get(self.url(s))
        self.assertEqual(len(r.data["unmarked"]), 3)

    def test_manual_edit_sets_audit_fields(self):
        s = self.completed_session()
        build_session_attendance(s)
        self.client.force_authenticate(self.teacher)
        r = self.client.patch(self.url(s), {"records": [
            {"student": self.student.id, "status": "present", "note": "joined by phone"},
            {"student": self.s2.id, "status": "excused"},
        ]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["updated"], 2)
        row = SessionAttendance.objects.get(session=s, student=self.student)
        self.assertEqual((row.status, row.source, row.marked_by_id, row.note),
                         ("present", "manual", self.teacher.id, "joined by phone"))

    def test_student_cannot_edit(self):
        s = self.completed_session()
        self.client.force_authenticate(self.student)
        r = self.client.patch(self.url(s), {"records": [{"student": self.student.id, "status": "present"}]}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_edit_window_closed(self):
        s = self.completed_session(hours_ago=24 * 9)          # default window = 7 days
        self.client.force_authenticate(self.teacher)
        r = self.client.patch(self.url(s), {"records": [{"student": self.student.id, "status": "present"}]}, format="json")
        self.assertEqual(r.status_code, 403)
        self.classroom.attendance_edit_days = 14
        self.classroom.save()
        r = self.client.patch(self.url(s), {"records": [{"student": self.student.id, "status": "present"}]}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_validation(self):
        s = self.completed_session()
        self.client.force_authenticate(self.teacher)
        bad_status = self.client.patch(self.url(s), {"records": [{"student": self.student.id, "status": "sleeping"}]}, format="json")
        self.assertEqual(bad_status.status_code, 400)
        empty = self.client.patch(self.url(s), {"records": []}, format="json")
        self.assertEqual(empty.status_code, 400)
        stranger = User.objects.create_user(username="nobody", password="pass12345", email="n@example.com")
        not_enrolled = self.client.patch(self.url(s), {"records": [{"student": stranger.id, "status": "present"}]}, format="json")
        self.assertEqual(not_enrolled.status_code, 400)
        live = self.make_session(status_=ClassSession.Status.LIVE)
        not_done = self.client.patch(self.url(live), {"records": [{"student": self.student.id, "status": "present"}]}, format="json")
        self.assertEqual(not_done.status_code, 400)

    def test_summary_manager_and_student(self):
        s1, s2_, s3_ = self.completed_session(hours_ago=5), self.completed_session(hours_ago=4), self.completed_session(hours_ago=3)
        for s in (s1, s2_, s3_):
            self.join(s, self.student)
        self.join(s1, self.s2)                       # s2 attends 1 of 3
        for s in (s1, s2_, s3_):
            build_session_attendance(s)
        apply_manual_edits(s3_, [{"student": self.s2.id, "status": "excused"}], self.teacher)   # 1 of 2 counted

        self.client.force_authenticate(self.teacher)
        r = self.client.get(f"/tuitionclass/classrooms/{self.classroom.id}/attendance/summary/")
        self.assertEqual(r.status_code, 200)
        by = {x["student"]: x for x in r.data["students"]}
        self.assertEqual(by[self.student.id]["attendance_percent"], 100.0)
        self.assertEqual(by[self.s2.id]["attendance_percent"], 50.0)
        self.assertEqual(by[self.s2.id]["excused"], 1)
        self.assertEqual(r.data["sessions_completed"], 3)

        self.client.force_authenticate(self.s2)
        r = self.client.get(f"/tuitionclass/classrooms/{self.classroom.id}/attendance/summary/")
        self.assertEqual([x["student"] for x in r.data["students"]], [self.s2.id])

        stranger = User.objects.create_user(username="outsider", password="pass12345", email="o@example.com")
        self.client.force_authenticate(stranger)
        self.assertEqual(self.client.get(f"/tuitionclass/classrooms/{self.classroom.id}/attendance/summary/").status_code, 403)


class PercentAndProgressTests(AttendanceBase):
    def test_percent_reads_table_and_falls_back_to_join_log(self):
        s1, s2_ = self.completed_session(hours_ago=5), self.completed_session(hours_ago=4)
        self.join(s1, self.student)
        self.join(s2_, self.student)
        # no rows built yet -> fallback to participants
        self.assertEqual(compute_attendance_percent_bulk([self.classroom.id], self.student)[self.classroom.id], 100.0)
        build_session_attendance(s1); build_session_attendance(s2_)
        apply_manual_edits(s2_, [{"student": self.student.id, "status": "absent"}], self.teacher)
        self.assertEqual(compute_attendance_percent_bulk([self.classroom.id], self.student)[self.classroom.id], 50.0)

    def test_percent_zero_when_no_completed_sessions(self):
        self.assertEqual(compute_attendance_percent_bulk([self.classroom.id], self.student), {self.classroom.id: 0})

    def test_my_progress_includes_attendance_and_streak_follows_manual_edit(self):
        s1 = self.completed_session(hours_ago=2)
        self.join(s1, self.student)
        build_session_attendance(s1)
        c = APIClient(); c.force_authenticate(self.student)
        r = c.get("/tuitionclass/my-progress/")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["attendance_percent"], 100.0)
        self.assertEqual(r.data["sessions_attended"], 1)
        self.assertGreaterEqual(r.data["current_streak_days"], 1)

        apply_manual_edits(s1, [{"student": self.student.id, "status": "absent"}], self.teacher)
        r = c.get("/tuitionclass/my-progress/")
        self.assertEqual(r.data["attendance_percent"], 0)
        self.assertEqual(r.data["current_streak_days"], 0)
