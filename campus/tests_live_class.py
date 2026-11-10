# campus/tests_live_class.py
"""
Live class — ONE-TAP go-live from the timetable + AUTOMATIC attendance.

Run: `python manage.py test campus.tests_live_class`

(Written against `campus/live_class.py`, `CampusLiveSessionViewSet` and
`TimetableEntryViewSet.go_live`. NOT RUN by the author of this file — if one
fails, read the assertion first: most of them pin a rule written in the module
docstring of `live_class.py`.)

How the suite stays deterministic and offline
  * The clock is FROZEN (`self.now`, patched into `django.utils.timezone.now`) on
    a fixed Monday, `DAY`, so "10:00 slot", "15 min early window" and the
    "attendance dated today" push rule never depend on when the suite runs. Move
    it with `self.now = self.at(10, 30)`.
  * Service functions also take an explicit `now=`; presence / attendance tests
    mostly use those and build participant rows with exact timestamps.
  * LiveKit / push / Celery are never touched: `campus.bridge.notify`,
    `campus.bridge.provision_video_room`, `core.classroom_chat_bridge.
    generate_campus_session_token` and (by default) the two parent-push helpers
    are mocked. `ParentPushTests` un-mocks the helpers and mocks only `bridge`.

The one thing NOT covered here is the true two-devices-at-the-same-millisecond
race (`IntegrityError` branch of `go_live_from_entry`) — it needs real DB
concurrency. The DB constraint behind it IS tested (`UniquenessTests`).
"""
import os
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from . import live_class
from . import tasks as campus_tasks
from .bridge import NotifTypes
from .live_class import LiveClassError
from .models import (
    AcademicSession,
    Attendance,
    CampusLiveParticipant,
    CampusLiveSession,
    ClassMode,
    StaffProfile,
    StudentEnrollment,
    SubstituteAssignment,
    TimeSlot,
    TimetableEntry,
)
from .tests_t4_efg import T4Base, rows

User = get_user_model()
S = CampusLiveSession.Status


class LiveBase(T4Base):
    """Fixture: Monday `DAY`, an ONLINE Maths period for 10-A at 10:00-10:45 taught
    by `self.teacher`, students sa1 + sa2 (parent of sa1 is `self.parent`)."""

    DAY = date(2026, 10, 19)  # a Monday (isoweekday 1), inside the fixture session
    PATCH_PUSH = True  # False -> let the real parent-push helpers run (ParentPushTests)

    # ---- time -----------------------------------------------------------
    def at(self, h, m=0, s=0, days=0):
        d = self.DAY + timedelta(days=days)
        return datetime.combine(d, time(h, m, s), tzinfo=timezone.get_current_timezone())

    # ---- setup ----------------------------------------------------------
    def setUp(self):
        super().setUp()
        cache.clear()
        self.now = self.at(10, 0)
        self._start_patch(mock.patch("django.utils.timezone.now", side_effect=lambda: self.now))
        self.provision = self._start_patch(
            mock.patch("campus.bridge.provision_video_room", return_value="room-1")
        )
        self.notify = self._start_patch(mock.patch("campus.bridge.notify"))
        self.token_mock = self._start_patch(
            mock.patch(
                "core.classroom_chat_bridge.generate_campus_session_token",
                side_effect=lambda room, user: f"tok-{user.username}-{room}",
            )
        )
        if self.PATCH_PUSH:
            self.absent_push = self._start_patch(
                mock.patch("campus.attendance_push.notify_parents_of_absences", return_value=0)
            )
            self.fix_push = self._start_patch(
                mock.patch("campus.attendance_push.notify_parents_of_corrections", return_value=0)
            )
        else:
            self.device_push = self._start_patch(
                mock.patch("campus.bridge.push_to_parent_devices", return_value=0)
            )

        self.slot = TimeSlot.objects.create(
            campus=self.campus, day_of_week=self.DAY.isoweekday(),
            start_time=time(10, 0), end_time=time(10, 45), label="Period 3",
        )
        self.entry = TimetableEntry.objects.create(
            section=self.secA, subject=self.math, staff=self.t_staff,
            time_slot=self.slot, session=self.session, mode=ClassMode.ONLINE,
        )

    def _start_patch(self, patcher):
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    # ---- builders -------------------------------------------------------
    def add_student(self, name, section=None, status_=StudentEnrollment.Status.ACTIVE):
        user = User.objects.create_user(username=name, password="pass12345")
        enr = StudentEnrollment.objects.create(
            student=user, section=section or self.secA, session=self.session,
            roll_number=name[-1], status=status_,
        )
        return user, enr

    def make_live(self, start=None, status_=S.LIVE, end=None, **kw):
        start = start or self.at(10, 0)
        fields = dict(
            section=self.secA, subject=self.math, teacher=self.t_staff,
            scheduled_at=start, status=status_, mode=ClassMode.ONLINE, room_id="room-1",
            started_at=None if status_ == S.SCHEDULED else start,
            ended_at=end,
        )
        fields.update(kw)
        return CampusLiveSession.objects.create(**fields)

    def stay(self, live, user, joined, left=None, seen=None):
        return CampusLiveParticipant.objects.create(
            session=live, user=user, joined_at=joined, last_seen_at=seen or left or joined, left_at=left,
        )

    def end(self, live, at):
        self.now = at
        return live_class.end_session(live, now=at)

    def mark(self, enrollment, subject=None):
        return Attendance.objects.get(enrollment=enrollment, date=self.DAY, subject=subject or self.math)

    # ---- API helpers ----------------------------------------------------
    def go_live(self, user, entry=None):
        self.client.force_authenticate(user)
        return self.client.post(reverse("timetable-entry-go-live", args=[(entry or self.entry).id]), format="json")

    def post_live(self, name, user, live, body=None):
        self.client.force_authenticate(user)
        return self.client.post(reverse(f"campus-live-session-{name}", args=[live.id]), body or {}, format="json")

    def get_attendance_report(self, user, live):
        self.client.force_authenticate(user)
        return self.client.get(reverse("campus-live-session-attendance", args=[live.id]))


# ==========================================================================
# 1. ONE TAP: go live
# ==========================================================================

class GoLiveTests(LiveBase):
    def test_teacher_one_tap_creates_live_session_with_room_token_and_push(self):
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertTrue(resp.data["created"])
        self.assertFalse(resp.data["resumed"])
        self.assertFalse(resp.data["reopened"])
        self.assertEqual(resp.data["room_name"], "room-1")
        self.assertEqual(resp.data["token"], "tok-teacher-room-1")
        self.assertIn("livekit_url", resp.data)
        self.assertEqual(resp.data["session"]["status"], "live")

        live = CampusLiveSession.objects.get()
        self.assertEqual(live.status, S.LIVE)
        self.assertEqual(live.timetable_entry_id, self.entry.id)
        self.assertEqual(live.occurrence_date, self.DAY)
        self.assertEqual(live.teacher_id, self.t_staff.id)
        self.assertEqual(live.started_by_id, self.teacher.id)
        self.assertEqual(live.started_at, self.now)
        self.assertEqual(live.live_heartbeat_at, self.now)  # arms the silence sweeper
        self.assertEqual(live.reminder_sent_at, self.now)  # no "starts in 5 min" reminder after this
        self.assertEqual(live.scheduled_at, self.at(10, 0))
        self.provision.assert_called_once()

        # the teacher is "in the room" straight away
        self.assertTrue(CampusLiveParticipant.objects.filter(session=live, user=self.teacher).exists())

        # exactly one "class is live" push, to the ACTIVE students of the section only
        self.notify.assert_called_once()
        kw = self.notify.call_args.kwargs
        self.assertEqual(kw["notif_type"], NotifTypes.CAMPUS_SESSION_LIVE)
        self.assertEqual(set(kw["users"]), {self.sa1.id, self.sa2.id})
        self.assertTrue(kw["push"])
        self.assertEqual(kw["data"]["live_session_id"], str(live.pk))

    def test_second_tap_resumes_same_room_without_new_push(self):
        first = self.go_live(self.teacher)
        second = self.go_live(self.teacher)
        self.assertEqual(second.status_code, status.HTTP_200_OK, second.data)
        self.assertTrue(second.data["resumed"])
        self.assertFalse(second.data["created"])
        self.assertEqual(second.data["session"]["id"], first.data["session"]["id"])
        self.assertEqual(second.data["room_name"], first.data["room_name"])
        self.assertEqual(CampusLiveSession.objects.count(), 1)
        self.notify.assert_called_once()  # not announced twice
        self.provision.assert_called_once()
        # a re-tap refreshes the same open stay, it does not stack a second one
        self.assertEqual(CampusLiveParticipant.objects.filter(user=self.teacher).count(), 1)

    def test_tap_from_another_device_by_a_manager_resumes_too(self):
        self.go_live(self.teacher)
        resp = self.go_live(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["resumed"])
        self.assertEqual(CampusLiveSession.objects.count(), 1)

    def test_admin_can_take_a_period_live(self):
        resp = self.go_live(self.admin)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)

    def test_adopts_a_session_the_teacher_scheduled_by_hand(self):
        pre = self.make_live(start=self.at(10, 10), status_=S.SCHEDULED, room_id="room-pre")
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)  # not created
        self.assertEqual(resp.data["session"]["id"], str(pre.id))
        self.assertEqual(resp.data["room_name"], "room-pre")
        self.assertEqual(CampusLiveSession.objects.count(), 1)
        pre.refresh_from_db()
        self.assertEqual(pre.status, S.LIVE)
        self.assertEqual(pre.timetable_entry_id, self.entry.id)
        self.assertEqual(pre.occurrence_date, self.DAY)
        self.provision.assert_not_called()  # it already had a room
        self.notify.assert_called_once()  # but its students were never told it is live

    def test_does_not_adopt_a_session_scheduled_far_from_the_slot(self):
        self.make_live(start=self.at(12, 0), status_=S.SCHEDULED)
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(CampusLiveSession.objects.count(), 2)

    def test_does_not_adopt_another_subjects_session(self):
        self.make_live(start=self.at(10, 0), status_=S.SCHEDULED, subject=self.phy)
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(CampusLiveSession.objects.count(), 2)

    def test_cancelled_session_does_not_block_a_fresh_one(self):
        CampusLiveSession.objects.create(
            section=self.secA, subject=self.math, teacher=self.t_staff, scheduled_at=self.at(10, 0),
            status=S.CANCELLED, mode=ClassMode.ONLINE, timetable_entry=self.entry, occurrence_date=self.DAY,
        )
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(CampusLiveSession.objects.exclude(status=S.CANCELLED).count(), 1)

    def test_recently_ended_class_is_reopened_in_the_same_room(self):
        first = self.go_live(self.teacher)
        live = CampusLiveSession.objects.get()
        self.end(live, self.at(10, 20))
        self.now = self.at(10, 40)  # 20 min after it ended (reopen window is 60)
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertTrue(resp.data["reopened"])
        self.assertEqual(resp.data["session"]["id"], first.data["session"]["id"])
        live.refresh_from_db()
        self.assertEqual(live.status, S.LIVE)
        self.assertIsNone(live.ended_at)
        self.assertEqual(live.started_at, self.at(10, 0))  # the real start is kept
        self.notify.assert_called_once()  # still only the original announcement

    @override_settings(CAMPUS_LIVE_REOPEN_MINUTES=10)
    def test_ended_class_cannot_be_reopened_after_the_window(self):
        self.go_live(self.teacher)
        self.end(CampusLiveSession.objects.get(), self.at(10, 20))
        self.now = self.at(10, 40)
        resp = self.go_live(self.teacher)
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(resp.data["code"], "already_ended")


class GoLiveRuleTests(LiveBase):
    """Every refusal is `{detail, code}` and leaves NO session, NO push behind."""

    def assertRefused(self, resp, http, code):
        self.assertEqual(resp.status_code, http, resp.data)
        self.assertEqual(resp.data["code"], code)
        self.assertTrue(resp.data["detail"])
        self.assertEqual(CampusLiveSession.objects.count(), 0)
        self.notify.assert_not_called()

    def test_a_teacher_who_does_not_teach_it_is_refused(self):
        self.assertRefused(self.go_live(self.other_teacher), 403, "not_allowed")

    def test_a_student_cannot_go_live(self):
        resp = self.go_live(self.sa1)
        self.assertIn(resp.status_code, (403, 404), resp.data)  # hidden or forbidden, never allowed
        self.assertEqual(CampusLiveSession.objects.count(), 0)

    def test_a_parent_cannot_go_live(self):
        resp = self.go_live(self.parent)
        self.assertIn(resp.status_code, (403, 404), resp.data)
        self.assertEqual(CampusLiveSession.objects.count(), 0)

    def test_offline_period_has_no_live_room(self):
        self.entry.mode = ClassMode.OFFLINE
        self.entry.save()
        self.assertRefused(self.go_live(self.teacher), 400, "offline_class")

    def test_period_of_another_weekday_is_not_today(self):
        self.now = self.at(10, 0, days=1)  # Tuesday
        self.assertRefused(self.go_live(self.teacher), 400, "not_today")

    def test_outside_the_academic_session(self):
        AcademicSession.objects.filter(pk=self.session.pk).update(end_date=self.DAY - timedelta(days=1))
        self.assertRefused(self.go_live(self.teacher), 400, "outside_session")

    def test_too_early(self):
        self.now = self.at(9, 30)
        self.assertRefused(self.go_live(self.teacher), 400, "too_early")

    def test_slot_is_over(self):
        self.now = self.at(11, 30)
        self.assertRefused(self.go_live(self.teacher), 400, "slot_over")

    def test_video_not_configured_rolls_the_whole_start_back(self):
        self.token_mock.side_effect = RuntimeError("LiveKit not configured")
        self.assertRefused(self.go_live(self.teacher), 503, "video_unavailable")

    def test_no_video_room_rolls_the_whole_start_back(self):
        for label, setup in (
            ("provisioning returns nothing", lambda: setattr(self.provision, "return_value", "")),
            ("provisioning blows up", lambda: setattr(self.provision, "side_effect", RuntimeError("down"))),
        ):
            with self.subTest(label):
                CampusLiveSession.objects.all().delete()
                setup()
                resp = self.go_live(self.teacher)
                self.assertEqual(resp.status_code, 503, resp.data)
                self.assertEqual(resp.data["code"], "no_room")
                self.assertEqual(CampusLiveSession.objects.count(), 0)
                self.notify.assert_not_called()

    def test_inactive_teacher_row_of_the_period_does_not_grant_access(self):
        StaffProfile.objects.filter(pk=self.t_staff.pk).update(is_active=False)
        resp = self.go_live(self.teacher)
        self.assertIn(resp.status_code, (403, 404), resp.data)
        self.assertEqual(CampusLiveSession.objects.count(), 0)


class GoLiveWindowTests(LiveBase):
    """Exact boundaries of the time window (service level, explicit `now`)."""

    def check(self, now, user=None):
        return live_class.check_can_go_live(user or self.teacher, self.entry, now)

    def test_opens_exactly_15_minutes_before_the_period(self):
        self.assertEqual(self.check(self.at(9, 45)), self.DAY)
        with self.assertRaises(LiveClassError) as cm:
            self.check(self.at(9, 44, 59))
        self.assertEqual(cm.exception.code, "too_early")

    def test_closes_15_minutes_after_the_period_ends(self):
        self.assertEqual(self.check(self.at(11, 0)), self.DAY)
        with self.assertRaises(LiveClassError) as cm:
            self.check(self.at(11, 0, 1))
        self.assertEqual(cm.exception.code, "slot_over")

    @override_settings(CAMPUS_GO_LIVE_EARLY_MINUTES=60, CAMPUS_GO_LIVE_LATE_GRACE_MINUTES=0)
    def test_window_is_configurable(self):
        self.assertEqual(self.check(self.at(9, 0)), self.DAY)
        self.assertEqual(self.check(self.at(10, 45)), self.DAY)
        with self.assertRaises(LiveClassError) as cm:
            self.check(self.at(10, 45, 1))
        self.assertEqual(cm.exception.code, "slot_over")

    def test_the_date_is_the_local_one_not_utc(self):
        # 00:10 IST on Monday is still Sunday in UTC — the period must be judged on IST.
        late_slot = TimeSlot.objects.create(
            campus=self.campus, day_of_week=1, start_time=time(0, 15), end_time=time(1, 0), label="Early bird",
        )
        entry = TimetableEntry.objects.create(
            section=self.secB, subject=self.math, staff=self.t2_staff,
            time_slot=late_slot, session=self.session, mode=ClassMode.ONLINE,
        )
        self.assertEqual(live_class.check_can_go_live(self.other_teacher, entry, self.at(0, 10)), self.DAY)


class UniquenessTests(LiveBase):
    def test_db_blocks_a_second_live_row_for_the_same_period_and_day(self):
        kw = dict(
            section=self.secA, subject=self.math, teacher=self.t_staff, scheduled_at=self.at(10, 0),
            mode=ClassMode.ONLINE, timetable_entry=self.entry, occurrence_date=self.DAY,
        )
        CampusLiveSession.objects.create(**kw)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CampusLiveSession.objects.create(**kw)

    def test_a_cancelled_row_and_another_day_do_not_collide(self):
        kw = dict(
            section=self.secA, subject=self.math, teacher=self.t_staff, scheduled_at=self.at(10, 0),
            mode=ClassMode.ONLINE, timetable_entry=self.entry,
        )
        CampusLiveSession.objects.create(occurrence_date=self.DAY, status=S.CANCELLED, **kw)
        CampusLiveSession.objects.create(occurrence_date=self.DAY, **kw)
        CampusLiveSession.objects.create(occurrence_date=self.DAY + timedelta(days=7), **kw)
        self.assertEqual(CampusLiveSession.objects.count(), 3)


# ==========================================================================
# 2. Substitute teacher
# ==========================================================================

class SubstituteGoLiveTests(LiveBase):
    def substitute(self, who=None, on=None, status_=SubstituteAssignment.Status.ACTIVE):
        return SubstituteAssignment.objects.create(
            campus=self.campus, entry=self.entry, date=on or self.DAY,
            substitute=who or self.t2_staff, original_staff=self.t_staff,
            assigned_by=self.admin, status=status_,
        )

    def test_substitute_can_go_live_and_becomes_the_sessions_teacher(self):
        self.substitute()
        resp = self.go_live(self.other_teacher)
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        live = CampusLiveSession.objects.get()
        self.assertEqual(live.teacher_id, self.t2_staff.id)
        self.assertEqual(live.started_by_id, self.other_teacher.id)

    def test_substitute_can_heartbeat_and_end_even_though_not_an_approved_subject_teacher(self):
        self.substitute()
        self.go_live(self.other_teacher)
        live = CampusLiveSession.objects.get()
        self.assertTrue(live_class.can_run_session(self.other_teacher, live))
        self.assertEqual(self.post_live("heartbeat", self.other_teacher, live).status_code, 200)
        self.now = self.at(10, 30)
        end = self.post_live("end", self.other_teacher, live)
        self.assertEqual(end.status_code, 200, end.data)
        self.assertEqual(end.data["status"], "ended")

    def test_substitute_sees_the_attendance_report(self):
        self.substitute()
        self.go_live(self.other_teacher)
        live = CampusLiveSession.objects.get()
        self.assertEqual(self.get_attendance_report(self.other_teacher, live).status_code, 200)

    def test_original_teacher_keeps_access_on_a_substituted_day(self):
        self.substitute()
        self.assertEqual(self.go_live(self.teacher).status_code, 201)

    def test_cancelled_substitution_grants_nothing(self):
        self.substitute(status_=SubstituteAssignment.Status.CANCELLED)
        self.assertEqual(self.go_live(self.other_teacher).status_code, 403)

    def test_substitution_for_another_date_grants_nothing(self):
        self.substitute(on=self.DAY + timedelta(days=7))
        self.assertEqual(self.go_live(self.other_teacher).status_code, 403)


# ==========================================================================
# 3. Presence
# ==========================================================================

class PresenceTests(LiveBase):
    def setUp(self):
        super().setUp()
        self.live = self.make_live()

    def test_join_creates_a_stay(self):
        row = live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        self.assertEqual((row.joined_at, row.last_seen_at, row.left_at), (self.at(10, 1), self.at(10, 1), None))

    def test_refetching_a_token_is_not_a_second_join(self):
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1, 30))  # within the 90 s ttl
        stays = CampusLiveParticipant.objects.filter(session=self.live, user=self.sa1)
        self.assertEqual(stays.count(), 1)
        self.assertEqual(stays.get().last_seen_at, self.at(10, 1, 30))

    def test_a_stale_stay_is_closed_at_its_last_sign_of_life_and_a_new_one_starts(self):
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        live_class.record_join(self.live, self.sa1, now=self.at(10, 20))  # silent for 19 min
        stays = list(CampusLiveParticipant.objects.filter(session=self.live, user=self.sa1).order_by("joined_at"))
        self.assertEqual(len(stays), 2)
        self.assertEqual(stays[0].left_at, self.at(10, 1))
        self.assertEqual((stays[1].joined_at, stays[1].left_at), (self.at(10, 20), None))

    def test_heartbeat_keeps_the_stay_fresh(self):
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        out = live_class.heartbeat(self.live, self.sa1, now=self.at(10, 1, 30))
        self.assertEqual(out, {"live": True, "status": "live", "recording": False})
        self.assertEqual(CampusLiveParticipant.objects.get(user=self.sa1).last_seen_at, self.at(10, 1, 30))

    def test_heartbeat_after_a_dropout_starts_a_new_stay(self):
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        live_class.heartbeat(self.live, self.sa1, now=self.at(10, 10))  # app was dead for 9 min
        self.assertEqual(CampusLiveParticipant.objects.filter(user=self.sa1).count(), 2)

    def test_student_heartbeat_does_not_keep_the_session_alive_only_the_teachers_does(self):
        CampusLiveSession.objects.filter(pk=self.live.pk).update(live_heartbeat_at=self.at(10, 0))
        live_class.heartbeat(self.live, self.sa1, now=self.at(10, 3))
        self.live.refresh_from_db()
        self.assertEqual(self.live.live_heartbeat_at, self.at(10, 0))
        live_class.heartbeat(self.live, self.teacher, now=self.at(10, 4))
        self.live.refresh_from_db()
        self.assertEqual(self.live.live_heartbeat_at, self.at(10, 4))

    def test_heartbeat_tells_the_app_when_the_class_has_ended(self):
        CampusLiveSession.objects.filter(pk=self.live.pk).update(status=S.ENDED)
        self.live.refresh_from_db()
        self.assertEqual(live_class.heartbeat(self.live, self.sa1), {"live": False, "status": "ended"})
        self.assertEqual(CampusLiveParticipant.objects.count(), 0)  # nothing recorded after the end

    def test_leave_closes_the_open_stay_and_is_idempotent(self):
        live_class.record_join(self.live, self.sa1, now=self.at(10, 1))
        self.assertEqual(live_class.record_leave(self.live, self.sa1, now=self.at(10, 30)), 1)
        self.assertEqual(live_class.record_leave(self.live, self.sa1, now=self.at(10, 31)), 0)
        self.assertEqual(CampusLiveParticipant.objects.get(user=self.sa1).left_at, self.at(10, 30))


class PresenceApiTests(LiveBase):
    def setUp(self):
        super().setUp()
        self.live = self.make_live()

    def test_student_join_returns_room_token_and_records_the_stay(self):
        with override_settings(LIVEKIT_WS_URL="wss://lk.test"):
            resp = self.post_live("join", self.sa1, self.live)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["room_name"], "room-1")
        self.assertEqual(resp.data["token"], "tok-sa1-room-1")
        self.assertEqual(resp.data["livekit_url"], "wss://lk.test")
        stay = CampusLiveParticipant.objects.get(user=self.sa1)
        self.assertEqual((stay.joined_at, stay.left_at), (self.now, None))

    def test_join_before_the_class_is_live_is_refused(self):
        CampusLiveSession.objects.filter(pk=self.live.pk).update(status=S.SCHEDULED)
        resp = self.post_live("join", self.sa1, self.live)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(CampusLiveParticipant.objects.count(), 0)

    def test_a_student_of_another_section_cannot_join(self):
        resp = self.post_live("join", self.sb, self.live)
        self.assertIn(resp.status_code, (403, 404), resp.data)
        self.assertEqual(CampusLiveParticipant.objects.count(), 0)

    def test_heartbeat_and_leave_over_http(self):
        self.post_live("join", self.sa1, self.live)
        self.now = self.at(10, 0, 30)
        beat = self.post_live("heartbeat", self.sa1, self.live)
        self.assertEqual(beat.status_code, 200)
        self.assertEqual(beat.data, {"live": True, "status": "live", "recording": False})
        self.now = self.at(10, 5)
        left = self.post_live("leave", self.sa1, self.live)
        self.assertEqual(left.status_code, 200)
        self.assertEqual(left.data, {"left": True})
        self.assertEqual(CampusLiveParticipant.objects.get(user=self.sa1).left_at, self.at(10, 5))

    def test_heartbeat_from_an_outsider_is_refused(self):
        resp = self.post_live("heartbeat", self.sb, self.live)
        self.assertIn(resp.status_code, (403, 404), resp.data)

    def test_student_app_learns_the_teacher_ended_the_class(self):
        self.post_live("end", self.teacher, self.live)
        beat = self.post_live("heartbeat", self.sa1, self.live)
        self.assertEqual(beat.status_code, 200)
        self.assertEqual(beat.data, {"live": False, "status": "ended"})

    def test_livekit_url_setting_and_env_alias(self):
        with override_settings(LIVEKIT_WS_URL="wss://from-settings"):
            self.assertEqual(live_class.livekit_url(), "wss://from-settings")
        with mock.patch.dict(os.environ):
            os.environ.pop("LIVEKIT_WS_URL", None)
            os.environ["LIVEKIT_URL"] = "wss://alias"
            with override_settings(LIVEKIT_WS_URL=None):
                self.assertEqual(live_class.livekit_url(), "wss://alias")


# ==========================================================================
# 4. Ending a class
# ==========================================================================

class EndSessionTests(LiveBase):
    def test_teacher_ends_the_class_and_attendance_is_built(self):
        self.go_live(self.teacher)
        live = CampusLiveSession.objects.get()
        self.stay(live, self.sa1, self.at(10, 1), self.at(10, 45))
        self.now = self.at(10, 45)
        resp = self.post_live("end", self.teacher, live)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "ended")
        live.refresh_from_db()
        self.assertEqual(live.ended_at, self.at(10, 45))
        self.assertIsNotNone(live.attendance_built_at)
        self.assertEqual(self.mark(self.e_a1).status, "present")
        self.assertEqual(self.mark(self.e_a2).status, "absent")  # teacher was there, sa2 was not
        # the teacher's own open stay was closed too
        self.assertFalse(CampusLiveParticipant.objects.filter(session=live, left_at__isnull=True).exists())

    def test_ending_twice_is_harmless(self):
        self.go_live(self.teacher)
        live = CampusLiveSession.objects.get()
        self.now = self.at(10, 45)
        self.post_live("end", self.teacher, live)
        stamp = CampusLiveSession.objects.get().ended_at
        self.now = self.at(10, 50)
        again = self.post_live("end", self.teacher, live)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(CampusLiveSession.objects.get().ended_at, stamp)  # not moved
        self.assertEqual(live_class.end_session(live)[1], False)

    def test_a_student_cannot_end_the_class(self):
        live = self.make_live()
        resp = self.post_live("end", self.sa1, live)
        self.assertIn(resp.status_code, (403, 404), resp.data)
        live.refresh_from_db()
        self.assertEqual(live.status, S.LIVE)

    def test_another_teacher_cannot_end_it(self):
        live = self.make_live()
        resp = self.post_live("end", self.other_teacher, live)
        self.assertEqual(resp.status_code, 403)

    def test_a_never_started_session_ends_without_marking_anybody(self):
        live = self.make_live(status_=S.SCHEDULED)
        self.now = self.at(10, 45)
        resp = self.post_live("end", self.teacher, live)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_ending_never_fails_because_attendance_failed_and_the_sweeper_can_retry(self):
        live = self.make_live()
        self.stay(live, self.sa1, self.at(10, 1), self.at(10, 45))
        with mock.patch("campus.live_class.build_attendance", side_effect=RuntimeError("db hiccup")):
            _live, changed = self.end(live, self.at(10, 45))
        self.assertTrue(changed)
        live.refresh_from_db()
        self.assertEqual(live.status, S.ENDED)
        self.assertIsNone(live.attendance_built_at)
        self.assertEqual(Attendance.objects.count(), 0)
        # ...and the catch-up sweep fixes it
        out = live_class.sweep_live_sessions(now=self.at(10, 50))
        self.assertEqual(out["attendance_rebuilt"], 1)
        self.assertEqual(self.mark(self.e_a1).status, "present")


# ==========================================================================
# 5. Automatic attendance
# ==========================================================================

class AutoAttendanceTests(LiveBase):
    """A 45-minute class (10:00-10:45): present needs >= 50 % = 22.5 min, 'late'
    means first join after 10:10."""

    def setUp(self):
        super().setUp()
        self.live = self.make_live()

    def run_class(self, end=None):
        return self.end(self.live, end or self.at(10, 45))

    # ---- the three outcomes ---------------------------------------------
    def test_present_late_and_absent(self):
        sa3, e3 = self.add_student("sa3")
        sa4, e4 = self.add_student("sa4")
        self.stay(self.live, self.sa1, self.at(10, 1), self.at(10, 45))    # whole class
        self.stay(self.live, self.sa2, self.at(10, 20), self.at(10, 45))   # 25 min, joined late
        self.stay(self.live, sa3, self.at(10, 30), self.at(10, 45))        # 15 min < 22.5
        # sa4 never came
        self.run_class()

        a1 = self.mark(self.e_a1)
        self.assertEqual((a1.status, a1.source, a1.minutes_present), ("present", "auto", 44))
        self.assertEqual(a1.live_session_id, self.live.id)
        self.assertIsNone(a1.marked_by)
        self.assertEqual(a1.date, self.DAY)
        self.assertEqual(a1.subject_id, self.math.id)  # subject-wise, not the daily register
        self.assertEqual(self.mark(self.e_a2).status, "late")
        self.assertEqual(self.mark(e3).status, "absent")
        self.assertEqual(self.mark(e3).minutes_present, 15)
        self.assertEqual(self.mark(e4).status, "absent")
        self.assertEqual(self.mark(e4).minutes_present, 0)
        self.live.refresh_from_db()
        self.assertIsNotNone(self.live.attendance_built_at)

    def test_exactly_at_the_minimum_counts_as_present_just_under_does_not(self):
        sa3, e3 = self.add_student("sa3")
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 22, 30))  # exactly 50 %
        self.stay(self.live, sa3, self.at(10, 0), self.at(10, 22, 29))       # 1 s short
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 45))      # someone else keeps it a real class
        self.run_class()
        self.assertEqual(self.mark(self.e_a1).status, "present")
        self.assertEqual(self.mark(e3).status, "absent")

    def test_late_boundary_is_the_grace_minutes_after_the_start(self):
        sa3, e3 = self.add_student("sa3")
        self.stay(self.live, self.sa1, self.at(10, 10, 0), self.at(10, 45))  # exactly on the grace line
        self.stay(self.live, self.sa2, self.at(10, 10, 1), self.at(10, 45))  # one second after
        self.stay(self.live, sa3, self.at(10, 5), self.at(10, 45))
        self.run_class()
        self.assertEqual(self.mark(self.e_a1).status, "present")
        self.assertEqual(self.mark(self.e_a2).status, "late")
        self.assertEqual(self.mark(e3).status, "present")

    def test_several_stays_add_up_and_overlaps_count_once(self):
        self.stay(self.live, self.sa1, self.at(10, 2), self.at(10, 20))   # 18 min
        self.stay(self.live, self.sa1, self.at(10, 30), self.at(10, 45))  # + 15 min (rejoined)
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 15))
        self.stay(self.live, self.sa2, self.at(10, 10), self.at(10, 40))  # overlaps the first: 40 min, not 45
        self.run_class()
        self.assertEqual(self.mark(self.e_a1).minutes_present, 33)
        self.assertEqual(self.mark(self.e_a1).status, "present")  # first join 10:02, inside the grace
        self.assertEqual(self.mark(self.e_a2).minutes_present, 40)

    def test_merged_seconds_helper(self):
        t = self.at
        self.assertEqual(live_class._merged_seconds([]), 0)
        self.assertEqual(live_class._merged_seconds([(t(10, 0), t(10, 10)), (t(10, 5), t(10, 30))]), 30 * 60)
        self.assertEqual(live_class._merged_seconds([(t(10, 20), t(10, 30)), (t(10, 0), t(10, 10))]), 20 * 60)
        self.assertEqual(live_class._merged_seconds([(t(10, 0), t(10, 30)), (t(10, 10), t(10, 20))]), 30 * 60)

    def test_time_before_the_class_started_does_not_count(self):
        self.stay(self.live, self.sa1, self.at(9, 50), self.at(10, 45))  # waited in the room 10 min early
        self.run_class()
        self.assertEqual(self.mark(self.e_a1).minutes_present, 45)  # not 55

    def test_a_student_whose_app_died_is_closed_at_last_sign_of_life_plus_ttl(self):
        # never left, last heartbeat 10:20 -> counted until 10:21:30 only
        self.stay(self.live, self.sa1, self.at(10, 0), left=None, seen=self.at(10, 20))
        self.stay(self.live, self.sa2, self.at(10, 0), left=None, seen=self.at(10, 44, 50))  # alive to the end
        self.run_class()
        p1 = CampusLiveParticipant.objects.get(user=self.sa1)
        self.assertEqual(p1.left_at, self.at(10, 21, 30))
        self.assertEqual(self.mark(self.e_a1).status, "absent")  # 21.5 / 45 = 48 %
        self.assertEqual(self.mark(self.e_a2).status, "present")

    def test_only_active_students_are_marked(self):
        _u, gone = self.add_student("sa5", status_=StudentEnrollment.Status.WITHDRAWN)
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertFalse(Attendance.objects.filter(enrollment=gone).exists())
        self.assertEqual(Attendance.objects.count(), 2)  # sa1 + sa2

    def test_a_student_of_another_section_in_the_room_is_ignored(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.stay(self.live, self.sb, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertFalse(Attendance.objects.filter(enrollment=self.e_b).exists())

    def test_only_the_teacher_in_the_room_means_everyone_is_absent(self):
        # the class HAPPENED (a teacher taught an empty room) — unlike a class nobody joined at all
        self.stay(self.live, self.teacher, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertEqual(set(Attendance.objects.values_list("status", flat=True)), {"absent"})
        self.assertEqual(Attendance.objects.count(), 2)

    # ---- classes that did not "happen" ----------------------------------
    def assertSkipped(self, reason):
        out = live_class.build_attendance(self.live, now=self.at(11, 0))
        self.assertFalse(out["ran"])
        self.assertEqual(out["reason"], reason)
        self.assertEqual(Attendance.objects.count(), 0)
        self.live.refresh_from_db()
        return self.live

    def test_class_nobody_joined_marks_nobody_absent_and_is_not_retried(self):
        self.end(self.live, self.at(10, 45))
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertIsNotNone(CampusLiveSession.objects.get().attendance_built_at)  # stamped: no endless retries

    def test_class_shorter_than_five_minutes_is_skipped(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 3))
        self.end(self.live, self.at(10, 3))
        self.live.refresh_from_db()
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(live_class.build_attendance(self.live)["reason"], "too_short")

    def test_offline_session_is_skipped(self):
        CampusLiveSession.objects.filter(pk=self.live.pk).update(mode=ClassMode.OFFLINE, status=S.ENDED,
                                                                 ended_at=self.at(10, 45))
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.assertSkipped("offline")

    def test_switched_off_per_campus(self):
        type(self.campus).objects.filter(pk=self.campus.pk).update(live_attendance_auto=False)
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.end(self.live, self.at(10, 45))
        self.assertEqual(Attendance.objects.count(), 0)
        self.assertEqual(live_class.build_attendance(self.live)["reason"], "disabled")

    def test_a_session_that_has_not_ended_is_left_alone(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 30))
        out = live_class.build_attendance(self.live, now=self.at(10, 30))
        self.assertEqual((out["ran"], out["reason"]), (False, "not_ended"))
        self.assertEqual(Attendance.objects.count(), 0)
        self.live.refresh_from_db()
        self.assertIsNone(self.live.attendance_built_at)  # not stamped — it will be built when it ends

    def test_unknown_session(self):
        self.assertEqual(live_class.build_attendance("00000000-0000-0000-0000-000000000000")["reason"], "not_found")

    # ---- campus knobs ----------------------------------------------------
    def test_campus_thresholds_are_respected(self):
        type(self.campus).objects.filter(pk=self.campus.pk).update(
            live_attendance_min_percent=80, live_attendance_late_minutes=0
        )
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))     # 100 %, on time
        self.stay(self.live, self.sa2, self.at(10, 5), self.at(10, 45))     # 40 min = 89 %, but late
        sa3, e3 = self.add_student("sa3")
        self.stay(self.live, sa3, self.at(10, 0), self.at(10, 30))          # 30 min = 67 % < 80
        self.run_class()
        self.assertEqual(self.mark(self.e_a1).status, "present")
        self.assertEqual(self.mark(self.e_a2).status, "late")
        self.assertEqual(self.mark(e3).status, "absent")

    # ---- idempotency + the teacher's word wins ---------------------------
    def test_building_twice_changes_nothing(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        before = list(Attendance.objects.order_by("enrollment_id").values_list("enrollment_id", "status", "minutes_present"))
        again = live_class.build_attendance(self.live, now=self.at(11, 0))
        self.assertTrue(again["ran"])
        self.assertEqual((again["created"], again["updated"]), (0, 0))
        after = list(Attendance.objects.order_by("enrollment_id").values_list("enrollment_id", "status", "minutes_present"))
        self.assertEqual(before, after)

    def test_a_row_the_teacher_marked_by_hand_is_never_overwritten(self):
        Attendance.objects.create(enrollment=self.e_a1, date=self.DAY, subject=self.math,
                                  status="absent", marked_by=self.teacher)  # source defaults to manual
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 45))
        self.run_class()
        row = self.mark(self.e_a1)
        self.assertEqual((row.status, row.source), ("absent", "manual"))
        self.assertEqual(self.mark(self.e_a2).source, "auto")  # the others are still automatic
        self.assertEqual(live_class.build_attendance(self.live)["skipped_manual"], 1)

    def test_an_approved_leave_is_never_turned_into_absent(self):
        Attendance.objects.create(enrollment=self.e_a2, date=self.DAY, subject=self.math,
                                  status="leave", marked_by=self.teacher)
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertEqual(self.mark(self.e_a2).status, "leave")

    def test_teacher_correction_through_the_api_sticks_across_a_rebuild(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertEqual(self.mark(self.e_a2).status, "absent")  # sa2 was ill, joined nothing

        row = self.mark(self.e_a2)
        self.client.force_authenticate(self.teacher)
        resp = self.client.patch(
            reverse("attendance-detail", args=[row.id]),
            {"status": "present", "reason": "Joined by phone call, teacher confirmed"}, format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        row.refresh_from_db()
        self.assertEqual((row.status, row.source, row.edit_count), ("present", "manual", 1))

        # the teacher reopens, sa2 still never shows up, the class ends again -> rebuild
        CampusLiveSession.objects.filter(pk=self.live.pk).update(attendance_built_at=None)
        out = live_class.build_attendance(self.live, now=self.at(11, 0))
        self.assertEqual(out["skipped_manual"], 1)
        row.refresh_from_db()
        self.assertEqual((row.status, row.source), ("present", "manual"))

    def test_rebuild_after_a_late_join_record_lifts_absent_to_present(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        self.assertEqual(self.mark(self.e_a2).status, "absent")
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 45))  # presence data arrived late
        out = live_class.build_attendance(self.live, now=self.at(11, 0))
        self.assertEqual(out["updated"], 1)
        row = self.mark(self.e_a2)
        self.assertEqual((row.status, row.source, row.minutes_present), ("present", "auto", 45))
        self.fix_push.assert_called_once()
        self.assertEqual(list(self.fix_push.call_args.kwargs["enrollment_ids"]), [self.e_a2.id])

    # ---- two live classes of the same subject on one day ------------------
    def test_two_live_classes_of_one_subject_share_one_row(self):
        first = self.live
        self.stay(first, self.sa1, self.at(10, 0), self.at(10, 30))            # sa1 full first class
        self.end(first, self.at(10, 30))                                       # sa2 absent in the first
        self.assertEqual(self.mark(self.e_a2).status, "absent")

        second = self.make_live(start=self.at(11, 0))
        self.stay(second, self.sa2, self.at(11, 0), self.at(11, 30))           # sa2 comes to the second
        self.end(second, self.at(11, 30))                                      # sa1 misses the second

        a1, a2 = self.mark(self.e_a1), self.mark(self.e_a2)
        self.assertEqual(Attendance.objects.filter(subject=self.math).count(), 2)  # still ONE row each
        self.assertEqual((a1.status, a1.minutes_present), ("present", 30))   # better status wins, minutes add up
        self.assertEqual((a2.status, a2.minutes_present), ("present", 30))   # absent -> present
        self.assertEqual(a2.live_session_id, second.id)
        self.fix_push.assert_called_once()  # sa2's parents hear that the absence was corrected
        self.assertEqual(list(self.fix_push.call_args.kwargs["enrollment_ids"]), [self.e_a2.id])

    def test_another_subjects_class_is_a_separate_row(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.run_class()
        phy = self.make_live(start=self.at(12, 0), subject=self.phy)
        self.stay(phy, self.sa1, self.at(12, 0), self.at(12, 45))
        self.end(phy, self.at(12, 45))
        self.assertEqual(Attendance.objects.filter(enrollment=self.e_a1).count(), 2)

    def test_attendance_date_is_the_day_the_class_started(self):
        yesterday = self.make_live(start=self.at(10, 0, days=-1), status_=S.LIVE)
        self.stay(yesterday, self.sa1, self.at(10, 0, days=-1), self.at(10, 45, days=-1))
        live_class.end_session(yesterday, now=self.at(10, 45, days=-1))
        self.assertTrue(
            Attendance.objects.filter(enrollment=self.e_a1, date=self.DAY - timedelta(days=1)).exists()
        )


class AutoAttendancePushTests(LiveBase):
    """Which build outcomes buzz which parents (helpers mocked)."""

    def setUp(self):
        super().setUp()
        self.live = self.make_live()
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))  # sa2 stays away

    def test_absent_children_are_pushed_once_with_the_class_subject(self):
        self.end(self.live, self.at(10, 45))
        self.absent_push.assert_called_once()
        kw = self.absent_push.call_args.kwargs
        self.assertEqual(list(kw["enrollment_ids"]), [self.e_a2.id])
        self.assertEqual(kw["date"], self.DAY)
        self.assertEqual(kw["subject"], self.math)
        self.fix_push.assert_not_called()

    def test_rebuilding_does_not_push_again(self):
        self.end(self.live, self.at(10, 45))
        live_class.build_attendance(self.live, now=self.at(11, 0))
        self.assertEqual(self.absent_push.call_count, 1)

    def test_nobody_absent_means_no_push(self):
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 45))
        self.end(self.live, self.at(10, 45))
        self.absent_push.assert_not_called()


class ParentPushTests(LiveBase):
    """The REAL push helpers with only `campus.bridge` mocked."""

    PATCH_PUSH = False

    def setUp(self):
        super().setUp()
        self.live = self.make_live()
        self.stay(self.live, self.sa2, self.at(10, 0), self.at(10, 45))  # sa1 (has a parent) stays away

    def absent_calls(self):
        return [c for c in self.notify.call_args_list if c.kwargs.get("notif_type") == NotifTypes.CAMPUS_STUDENT_ABSENT]

    def test_parent_of_an_absent_child_gets_the_normal_absent_push(self):
        self.end(self.live, self.at(10, 45))
        calls = self.absent_calls()
        self.assertEqual(len(calls), 1)
        kw = calls[0].kwargs
        self.assertEqual(list(kw["users"]), [self.parent])
        self.assertTrue(kw["push"])
        self.assertEqual(kw["data"]["student_id"], str(self.sa1.pk))
        self.assertEqual(kw["data"]["attendance_date"], self.DAY.isoformat())
        self.assertIn("Maths", kw["body"])

    def test_a_correction_follows_when_a_rebuild_turns_absent_into_present(self):
        self.end(self.live, self.at(10, 45))
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        live_class.build_attendance(self.live, now=self.at(11, 0))
        calls = self.absent_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].kwargs["data"].get("corrected"), "1")

    def test_no_push_for_a_class_of_a_previous_day(self):
        old = self.make_live(start=self.at(10, 0, days=-1))
        self.stay(old, self.sa2, self.at(10, 0, days=-1), self.at(10, 45, days=-1))
        live_class.end_session(old, now=self.at(10, 45, days=-1))  # clock (DAY 10:00) is "today"
        self.assertTrue(Attendance.objects.filter(enrollment=self.e_a1, date=self.DAY - timedelta(days=1)).exists())
        self.assertEqual(self.absent_calls(), [])


# ==========================================================================
# 6. Teacher's "who came" screen
# ==========================================================================

class AttendanceReportTests(LiveBase):
    def setUp(self):
        super().setUp()
        self.live = self.make_live()

    def test_report_before_the_class_ends(self):
        self.stay(self.live, self.sa1, self.at(10, 1), None, seen=self.at(10, 5))
        resp = self.get_attendance_report(self.teacher, self.live)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(resp.data["status"], "live")
        self.assertFalse(resp.data["built"])
        self.assertEqual(resp.data["date"], self.DAY.isoformat())
        self.assertEqual(resp.data["counts"],
                         {"present": 0, "late": 0, "absent": 0, "leave": 0, "unmarked": 2})
        self.assertEqual(len(resp.data["students"]), 2)
        s1 = next(s for s in resp.data["students"] if s["student"] == self.sa1.id)
        self.assertIsNone(s1["status"])
        self.assertEqual(s1["joins"], 1)
        self.assertIsNotNone(s1["first_joined_at"])

    def test_report_after_the_class_ends(self):
        self.stay(self.live, self.sa1, self.at(10, 1), self.at(10, 45))
        self.end(self.live, self.at(10, 45))
        resp = self.get_attendance_report(self.teacher, self.live)
        self.assertTrue(resp.data["built"])
        self.assertEqual(resp.data["counts"]["present"], 1)
        self.assertEqual(resp.data["counts"]["absent"], 1)
        self.assertEqual(resp.data["counts"]["unmarked"], 0)
        by_student = {s["student"]: s for s in resp.data["students"]}
        self.assertEqual(by_student[self.sa1.id]["status"], "present")
        self.assertEqual(by_student[self.sa1.id]["source"], "auto")
        self.assertEqual(by_student[self.sa1.id]["minutes_present"], 44)
        self.assertEqual(by_student[self.sa2.id]["status"], "absent")
        self.assertIsNone(by_student[self.sa2.id]["first_joined_at"])
        self.assertEqual(by_student[self.sa2.id]["joins"], 0)
        self.assertEqual(by_student[self.sa1.id]["name"], "sa1")  # username fallback

    def test_report_shows_a_manual_correction_as_manual(self):
        self.stay(self.live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.end(self.live, self.at(10, 45))
        row = self.mark(self.e_a2)
        self.client.force_authenticate(self.teacher)
        self.client.patch(reverse("attendance-detail", args=[row.id]),
                          {"status": "leave", "reason": "Medical leave letter"}, format="json")
        resp = self.get_attendance_report(self.teacher, self.live)
        s2 = next(s for s in resp.data["students"] if s["student"] == self.sa2.id)
        self.assertEqual((s2["status"], s2["source"]), ("leave", "manual"))
        self.assertEqual(resp.data["counts"]["leave"], 1)

    def test_students_and_outsiders_cannot_see_it(self):
        self.assertEqual(self.get_attendance_report(self.sa1, self.live).status_code, 403)
        self.assertEqual(self.get_attendance_report(self.other_teacher, self.live).status_code, 403)
        self.assertIn(self.get_attendance_report(self.sb, self.live).status_code, (403, 404))

    def test_admin_can_see_it(self):
        self.assertEqual(self.get_attendance_report(self.admin, self.live).status_code, 200)


class SerializerExposureTests(LiveBase):
    def test_live_session_exposes_the_new_fields_read_only(self):
        self.go_live(self.teacher)
        live = CampusLiveSession.objects.get()
        self.client.force_authenticate(self.teacher)
        data = self.client.get(reverse("campus-live-session-detail", args=[live.id])).data
        for key in ("timetable_entry", "occurrence_date", "started_at", "ended_at", "attendance_built_at"):
            self.assertIn(key, data)
        self.assertEqual(data["occurrence_date"], self.DAY.isoformat())
        # status / room can never be forced through a PATCH
        self.client.patch(reverse("campus-live-session-detail", args=[live.id]),
                          {"status": "ended", "started_at": None}, format="json")
        live.refresh_from_db()
        self.assertEqual(live.status, S.LIVE)

    def test_students_attendance_rows_carry_source_and_minutes(self):
        live = self.make_live()
        self.stay(live, self.sa1, self.at(10, 0), self.at(10, 45))
        self.end(live, self.at(10, 45))
        self.client.force_authenticate(self.sa1)
        got = rows(self.client.get(reverse("attendance-list")))
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["source"], "auto")
        self.assertEqual(got[0]["minutes_present"], 45)
        self.assertEqual(got[0]["live_session"], str(live.id))

    def test_a_client_cannot_mark_a_row_as_auto(self):
        self.client.force_authenticate(self.teacher)
        resp = self.client.post(
            reverse("attendance-list"),
            {"enrollment": str(self.e_a1.id), "date": self.DAY.isoformat(), "subject": str(self.math.id),
             "status": "present", "source": "auto", "minutes_present": 999},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.data)
        row = Attendance.objects.get(enrollment=self.e_a1)
        self.assertEqual(row.source, "manual")
        self.assertIsNone(row.minutes_present)


# ==========================================================================
# 7. Sweeper
# ==========================================================================

class SweeperTests(LiveBase):
    def setUp(self):
        super().setUp()
        self.now = self.at(12, 0)

    def test_silent_teacher_ends_the_class_at_the_last_heartbeat(self):
        live = self.make_live(start=self.at(11, 0), live_heartbeat_at=self.at(11, 54))
        self.stay(live, self.sa1, self.at(11, 0), left=None, seen=self.at(11, 53, 30))
        out = live_class.sweep_live_sessions(now=self.at(12, 0))
        self.assertEqual(out["auto_ended"], 1)
        live.refresh_from_db()
        self.assertEqual((live.status, live.ended_at), (S.ENDED, self.at(11, 54)))
        row = Attendance.objects.get(enrollment=self.e_a1, subject=self.math)
        self.assertEqual((row.status, row.minutes_present), ("present", 54))
        self.assertEqual(Attendance.objects.get(enrollment=self.e_a2).status, "absent")

    def test_a_recent_heartbeat_keeps_the_class_alive(self):
        live = self.make_live(start=self.at(11, 0), live_heartbeat_at=self.at(11, 56))  # 4 min ago
        out = live_class.sweep_live_sessions(now=self.at(12, 0))
        self.assertEqual(out["auto_ended"], 0)
        live.refresh_from_db()
        self.assertEqual(live.status, S.LIVE)

    @override_settings(CAMPUS_LIVE_STALE_MINUTES=2)
    def test_silence_threshold_is_configurable(self):
        live = self.make_live(start=self.at(11, 0), live_heartbeat_at=self.at(11, 56))
        self.assertEqual(live_class.sweep_live_sessions(now=self.at(12, 0))["auto_ended"], 1)
        live.refresh_from_db()
        self.assertEqual(live.status, S.ENDED)

    def test_old_app_without_heartbeats_is_not_ended_for_silence(self):
        live = self.make_live(start=self.at(11, 0), live_heartbeat_at=None)  # started via the classic action
        self.assertEqual(live_class.sweep_live_sessions(now=self.at(12, 0))["auto_ended"], 0)
        live.refresh_from_db()
        self.assertEqual(live.status, S.LIVE)

    def test_hard_cap_ends_a_session_older_than_six_hours(self):
        live = self.make_live(start=self.at(5, 0), live_heartbeat_at=None)
        out = live_class.sweep_live_sessions(now=self.at(12, 0))
        self.assertEqual(out["auto_ended"], 1)
        live.refresh_from_db()
        self.assertEqual((live.status, live.ended_at), (S.ENDED, self.at(11, 0)))  # start + 6 h

    def test_hard_cap_also_applies_when_the_teachers_app_is_still_pinging(self):
        live = self.make_live(start=self.at(5, 0), live_heartbeat_at=self.at(11, 59, 50))
        live_class.sweep_live_sessions(now=self.at(12, 0))
        live.refresh_from_db()
        self.assertEqual((live.status, live.ended_at), (S.ENDED, self.at(11, 0)))

    def test_session_that_never_started_is_capped_by_its_scheduled_time(self):
        live = self.make_live(start=self.at(4, 0), live_heartbeat_at=None)
        CampusLiveSession.objects.filter(pk=live.pk).update(started_at=None)
        live_class.sweep_live_sessions(now=self.at(12, 0))
        live.refresh_from_db()
        self.assertEqual(live.status, S.ENDED)

    def test_catch_up_builds_attendance_an_ended_class_never_got(self):
        live = self.make_live(start=self.at(10, 45), status_=S.ENDED, end=self.at(11, 30))
        self.stay(live, self.sa1, self.at(10, 45), self.at(11, 30))
        out = live_class.sweep_live_sessions(now=self.at(12, 0))
        self.assertEqual(out["attendance_rebuilt"], 1)
        self.assertEqual(Attendance.objects.get(enrollment=self.e_a1).status, "present")
        # ... and once built (or deliberately skipped) it is never picked up again
        self.assertEqual(live_class.sweep_live_sessions(now=self.at(12, 1))["attendance_rebuilt"], 0)

    def test_catch_up_ignores_sessions_ended_more_than_a_day_ago(self):
        live = self.make_live(start=self.at(10, 0, days=-3), status_=S.ENDED, end=self.at(10, 45, days=-3))
        self.stay(live, self.sa1, self.at(10, 0, days=-3), self.at(10, 45, days=-3))
        self.assertEqual(live_class.sweep_live_sessions(now=self.at(12, 0))["attendance_rebuilt"], 0)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_a_deliberately_skipped_class_is_not_retried_every_minute(self):
        live = self.make_live(start=self.at(11, 0), status_=S.ENDED, end=self.at(11, 3))  # too short
        self.stay(live, self.sa1, self.at(11, 0), self.at(11, 3))
        first = live_class.sweep_live_sessions(now=self.at(12, 0))
        second = live_class.sweep_live_sessions(now=self.at(12, 1))
        self.assertEqual((first["attendance_rebuilt"], second["attendance_rebuilt"]), (0, 0))
        live.refresh_from_db()
        self.assertIsNotNone(live.attendance_built_at)

    def test_cancelled_and_scheduled_sessions_are_never_touched(self):
        a = self.make_live(start=self.at(5, 0), status_=S.SCHEDULED)
        b = self.make_live(start=self.at(5, 0), status_=S.CANCELLED)
        out = live_class.sweep_live_sessions(now=self.at(12, 0))
        self.assertEqual(out, {"auto_ended": 0, "attendance_rebuilt": 0})
        a.refresh_from_db(), b.refresh_from_db()
        self.assertEqual((a.status, b.status), (S.SCHEDULED, S.CANCELLED))

    def test_celery_wrappers(self):
        self.assertEqual(campus_tasks.sweep_live_sessions(), {"auto_ended": 0, "attendance_rebuilt": 0})
        live = self.make_live(start=self.at(11, 0), status_=S.ENDED, end=self.at(11, 45))
        self.stay(live, self.sa1, self.at(11, 0), self.at(11, 45))
        out = campus_tasks.build_live_session_attendance(str(live.pk))
        self.assertTrue(out["ran"])
        self.assertEqual(Attendance.objects.get(enrollment=self.e_a1).status, "present")
