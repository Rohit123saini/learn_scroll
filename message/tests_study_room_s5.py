# message/tests_study_room_s5.py
"""
S5 — study room presence + attendance minutes: LiveKit webhook (join / leave / room_finished) -> `StudyRoomPresence`
-> `StudyRoomAttendance.total_seconds`; streak "kam se kam N minute"; sweeper; study-time leaderboard.

LiveKit network mock hai (webhook events `SimpleNamespace` se bante hain) — koi network nahi.

    python manage.py test message.tests_study_room_s5
"""
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import study_room_presence as presence
from .attendance_utils import compute_attendance_stats
from .livekit_webhook import handle_message_event
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    StudyRoomAttendance,
    StudyRoomPresence,
)

User = get_user_model()


def _uid():
    return uuid.uuid4().hex[:8]


def _at(day, hour, minute=0):
    return timezone.make_aware(datetime(2026, 10, day, hour, minute))


def _epoch(dt):
    return int(dt.timestamp())


class RoomNameTests(SimpleTestCase):
    def test_parse_room_name(self):
        cid = str(uuid.uuid4())
        self.assertEqual(presence.parse_room_name(f"study_{cid}"), cid)
        self.assertEqual(presence.parse_room_name(f"study_{cid}_abc123def456"), cid)
        self.assertIsNone(presence.parse_room_name("call_abcdef"))
        self.assertIsNone(presence.parse_room_name("study_not-a-uuid"))
        self.assertIsNone(presence.parse_room_name(""))

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_qualifies(self):
        self.assertTrue(presence.qualifies(600, True))
        self.assertFalse(presence.qualifies(599, True))
        self.assertTrue(presence.qualifies(0, False))            # purani row — join kaafi
        self.assertTrue(presence.qualifies(300, True, live_seconds=300))

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=0)
    def test_qualifies_threshold_zero(self):
        self.assertTrue(presence.qualifies(0, True))


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.a = User.objects.create_user(username=f"a{_uid()}", password="x")
        self.b = User.objects.create_user(username=f"b{_uid()}", password="x")
        self.outsider = User.objects.create_user(username=f"o{_uid()}", password="x")
        self.conv = Conversation.objects.create(type=ConversationType.GROUP)
        for u in (self.a, self.b):
            ConversationParticipant.objects.create(conversation=self.conv, user=u)
        self.room = f"study_{self.conv.id}_{uuid.uuid4().hex[:12]}"

    def ev(self, name, user, joined, created=None, room=None, identity=None):
        """LiveKit webhook event jaisa object. `joined` = participant.joined_at, `created` = event time."""
        return SimpleNamespace(
            event=name,
            room=SimpleNamespace(name=room or self.room),
            participant=SimpleNamespace(
                identity=identity if identity is not None else str(user.id),
                joined_at=_epoch(joined) if joined else 0,
            ),
            created_at=_epoch(created) if created else 0,
        )

    def attendance(self, user, day):
        return StudyRoomAttendance.objects.get(conversation=self.conv, user=user, attended_date=day)


class PresenceWebhookTests(_Base):
    def test_join_then_leave_credits_minutes(self):
        start, end = _at(20, 10), _at(20, 10, 25)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        row = StudyRoomPresence.objects.get(user=self.a)
        self.assertIsNone(row.left_at)

        handle_message_event(self.ev('participant_left', self.a, start, created=end))
        row.refresh_from_db()
        self.assertEqual(row.left_at, end)
        self.assertEqual(row.duration_seconds, 25 * 60)
        att = self.attendance(self.a, start.date())
        self.assertEqual(att.total_seconds, 25 * 60)
        self.assertEqual(att.join_count, 1)
        self.assertTrue(att.presence_tracked)
        self.assertEqual(att.first_joined_at, start)
        self.assertEqual(att.last_left_at, end)

    def test_duplicate_leave_is_idempotent(self):
        start, end = _at(20, 10), _at(20, 10, 25)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        for _ in range(3):
            handle_message_event(self.ev('participant_left', self.a, start, created=end))
        self.assertEqual(self.attendance(self.a, start.date()).total_seconds, 25 * 60)
        self.assertEqual(StudyRoomPresence.objects.filter(user=self.a).count(), 1)

    def test_leave_before_join_out_of_order(self):
        start, end = _at(20, 10), _at(20, 10, 30)
        handle_message_event(self.ev('participant_left', self.a, start, created=end))      # pehle left
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))  # baad me joined
        row = StudyRoomPresence.objects.get(user=self.a)
        self.assertEqual(row.left_at, end)                      # reopen nahi hua
        self.assertEqual(self.attendance(self.a, start.date()).total_seconds, 30 * 60)

    def test_leave_without_joined_at_closes_latest_open_row(self):
        start, end = _at(20, 10), _at(20, 10, 12)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        handle_message_event(self.ev('participant_left', self.a, None, created=end))
        self.assertEqual(self.attendance(self.a, start.date()).total_seconds, 12 * 60)

    def test_reconnect_adds_both_intervals_same_day(self):
        s1, e1 = _at(20, 10), _at(20, 10, 10)
        s2, e2 = _at(20, 11), _at(20, 11, 20)
        for s, e in ((s1, e1), (s2, e2)):
            handle_message_event(self.ev('participant_joined', self.a, s, created=s))
            handle_message_event(self.ev('participant_left', self.a, s, created=e))
        att = self.attendance(self.a, s1.date())
        self.assertEqual(att.total_seconds, 30 * 60)
        self.assertEqual(att.join_count, 2)
        self.assertEqual(StudyRoomAttendance.objects.filter(conversation=self.conv, user=self.a).count(), 1)

    def test_midnight_split_between_days(self):
        start, end = _at(20, 23, 50), _at(21, 0, 20)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        handle_message_event(self.ev('participant_left', self.a, start, created=end))
        self.assertEqual(self.attendance(self.a, start.date()).total_seconds, 10 * 60)
        self.assertEqual(self.attendance(self.a, end.date()).total_seconds, 20 * 60)

    @override_settings(LIVEKIT_STUDY_ROOM_MAX_SECONDS=3600)
    def test_credit_capped_at_max_duration(self):
        start, end = _at(20, 10), _at(20, 13)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        handle_message_event(self.ev('participant_left', self.a, start, created=end))
        self.assertEqual(self.attendance(self.a, start.date()).total_seconds, 3600)

    def test_room_finished_closes_all_open_rows(self):
        start, end = _at(20, 10), _at(20, 10, 40)
        for u in (self.a, self.b):
            handle_message_event(self.ev('participant_joined', u, start, created=start))
        handle_message_event(SimpleNamespace(
            event='room_finished', room=SimpleNamespace(name=self.room), participant=None, created_at=_epoch(end),
        ))
        self.assertFalse(StudyRoomPresence.objects.filter(left_at__isnull=True).exists())
        for u in (self.a, self.b):
            self.assertEqual(self.attendance(u, start.date()).total_seconds, 40 * 60)

    def test_ignored_cases(self):
        start = _at(20, 10)
        handle_message_event(self.ev('participant_joined', self.outsider, start, created=start))       # member nahi
        handle_message_event(self.ev('participant_joined', self.a, start, created=start, identity='EG_bot1'))   # egress bot
        handle_message_event(self.ev('participant_joined', self.a, start, created=start, room='call_deadbeef'))  # call room
        handle_message_event(self.ev('participant_joined', self.a, start, created=start, identity='not-a-user'))
        self.assertEqual(StudyRoomPresence.objects.count(), 0)

    def test_left_member_not_credited(self):
        ConversationParticipant.objects.filter(conversation=self.conv, user=self.b).update(left_at=timezone.now())
        start = _at(20, 10)
        handle_message_event(self.ev('participant_joined', self.b, start, created=start))
        self.assertEqual(StudyRoomPresence.objects.count(), 0)

    @override_settings(STUDY_ROOM_PRESENCE_ENABLED=False)
    def test_presence_disabled_no_rows(self):
        start = _at(20, 10)
        handle_message_event(self.ev('participant_joined', self.a, start, created=start))
        self.assertEqual(StudyRoomPresence.objects.count(), 0)


class StreakMinutesTests(_Base):
    def _row(self, user, days_ago, seconds, tracked=True):
        return StudyRoomAttendance.objects.create(
            conversation=self.conv, user=user, attended_date=timezone.localdate() - timedelta(days=days_ago),
            total_seconds=seconds, presence_tracked=tracked,
        )

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_short_day_does_not_count(self):
        self._row(self.a, 0, 15 * 60)    # aaj 15 min — ginta hai
        self._row(self.a, 1, 5 * 60)     # kal 5 min — nahi
        self._row(self.a, 2, 40 * 60)    # parso — ginta hai (par kal toot gaya)
        stats = compute_attendance_stats(self.conv, self.a)
        self.assertEqual(stats["current_streak"], 1)
        self.assertEqual(stats["longest_streak"], 1)
        self.assertEqual(stats["total_classes_attended"], 2)
        self.assertEqual(stats["today_minutes"], 15)
        self.assertEqual(stats["total_study_minutes"], 60)
        self.assertEqual(stats["streak_min_minutes"], 10)
        self.assertTrue(stats["today_counted"])

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_consecutive_qualifying_days_build_streak(self):
        for d in range(3):
            self._row(self.a, d, 20 * 60)
        stats = compute_attendance_stats(self.conv, self.a)
        self.assertEqual(stats["current_streak"], 3)
        self.assertEqual(stats["longest_streak"], 3)

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_legacy_rows_still_count(self):
        for d in range(3):
            self._row(self.a, d, 0, tracked=False)    # S5 se pehle ki rows (sirf join)
        self.assertEqual(compute_attendance_stats(self.conv, self.a)["current_streak"], 3)

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=0)
    def test_threshold_zero_join_is_enough(self):
        self._row(self.a, 0, 0, tracked=True)
        self.assertEqual(compute_attendance_stats(self.conv, self.a)["current_streak"], 1)

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_open_interval_counts_today(self):
        if timezone.localtime().hour == 0:
            self.skipTest("midnight ke aas-paas live-minutes today-start se clamp hote hain")
        self._row(self.a, 0, 0, tracked=True)
        StudyRoomPresence.objects.create(
            conversation=self.conv, user=self.a, room_name=self.room,
            joined_at=timezone.now() - timedelta(minutes=15),
        )
        stats = compute_attendance_stats(self.conv, self.a)
        self.assertEqual(stats["current_streak"], 1)
        self.assertGreaterEqual(stats["today_minutes"], 14)

    @override_settings(STUDY_ROOM_STREAK_MIN_MINUTES=10)
    def test_streak_endpoint_returns_new_keys(self):
        self._row(self.a, 0, 20 * 60)
        client = APIClient()
        client.force_authenticate(self.a)
        resp = client.get(f"/message/study-room/{self.conv.id}/streak/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        data = data.get("data", data)
        self.assertEqual(data["current_streak"], 1)
        self.assertIn("today_minutes", data)
        self.assertIn("streak_min_minutes", data)


class SweeperTests(_Base):
    def _open(self, user, hours_ago):
        return StudyRoomPresence.objects.create(
            conversation=self.conv, user=user, room_name=self.room,
            joined_at=timezone.now() - timedelta(hours=hours_ago),
        )

    @override_settings(STUDY_ROOM_PRESENCE_STALE_CREDIT_SECONDS=3600)
    def test_missing_leave_gets_capped_stale_credit(self):
        row = self._open(self.a, 5)
        with mock.patch('message.livekit_utils.list_room_identities', return_value=set()):
            summary = presence.sweep_stale()
        row.refresh_from_db()
        self.assertEqual(summary["closed"], 1)
        self.assertIsNotNone(row.left_at)
        self.assertEqual(row.end_reason, presence.REASON_SWEPT)
        self.assertEqual(row.duration_seconds, 3600)

    def test_still_in_room_is_left_alone(self):
        row = self._open(self.a, 5)
        with mock.patch('message.livekit_utils.list_room_identities', return_value={str(self.a.id)}):
            summary = presence.sweep_stale()
        row.refresh_from_db()
        self.assertEqual(summary["closed"], 0)
        self.assertIsNone(row.left_at)

    def test_livekit_unreachable_retries_later(self):
        row = self._open(self.a, 5)
        with mock.patch('message.livekit_utils.list_room_identities', return_value=None):
            presence.sweep_stale()
        row.refresh_from_db()
        self.assertIsNone(row.left_at)

    def test_fresh_rows_not_touched(self):
        row = self._open(self.a, 0.5)
        with mock.patch('message.livekit_utils.list_room_identities', return_value=set()) as m:
            presence.sweep_stale()
        row.refresh_from_db()
        self.assertIsNone(row.left_at)
        m.assert_not_called()


class StudyTimeLeaderboardTests(_Base):
    def setUp(self):
        super().setUp()
        from leaderboard import services

        self.services = services
        self.week = services.current_week_key()
        today = timezone.localdate()
        StudyRoomAttendance.objects.create(
            conversation=self.conv, user=self.a, attended_date=today, total_seconds=90 * 60, join_count=2,
        )
        StudyRoomAttendance.objects.create(
            conversation=self.conv, user=self.b, attended_date=today, total_seconds=30,   # 1 min se kam — board par nahi
        )

    def test_compute_scores_weekly(self):
        rows = self.services.compute_study_time_scores(self.conv.id, "weekly", self.week)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], self.a.id)
        self.assertEqual(rows[0]["score"], 90.0)
        self.assertEqual(rows[0]["metadata"]["minutes"], 90)
        self.assertEqual(rows[0]["metadata"]["sessions"], 2)

    def test_old_week_excluded_from_weekly_but_in_all_time(self):
        StudyRoomAttendance.objects.create(
            conversation=self.conv, user=self.b, attended_date=timezone.localdate() - timedelta(days=60),
            total_seconds=120 * 60,
        )
        weekly = {r["user_id"] for r in self.services.compute_study_time_scores(self.conv.id, "weekly", self.week)}
        all_time = {r["user_id"]: r["score"] for r in self.services.compute_study_time_scores(self.conv.id, "all_time", "all")}
        self.assertNotIn(self.b.id, weekly)
        self.assertEqual(all_time[self.b.id], 120.0)

    def test_member_who_left_is_hidden(self):
        ConversationParticipant.objects.filter(conversation=self.conv, user=self.a).update(left_at=timezone.now())
        self.assertEqual(self.services.compute_study_time_scores(self.conv.id, "weekly", self.week), [])

    def test_board_endpoint_members_only(self):
        from leaderboard import tasks

        tasks.recompute_study_time_board(self.conv.id, "weekly", self.week)
        params = {"scope_type": "study_time", "scope_id": str(self.conv.id), "period": "weekly"}

        member = APIClient()
        member.force_authenticate(self.a)
        resp = member.get("/leaderboard/board/", params)
        self.assertEqual(resp.status_code, 200)

        stranger = APIClient()
        stranger.force_authenticate(self.outsider)
        self.assertEqual(stranger.get("/leaderboard/board/", params).status_code, 403)

    def test_global_board_off_by_default_and_bad_id_400(self):
        client = APIClient()
        client.force_authenticate(self.a)
        self.assertEqual(client.get("/leaderboard/board/", {"scope_type": "study_time"}).status_code, 403)
        self.assertEqual(
            client.get("/leaderboard/board/", {"scope_type": "study_time", "scope_id": "nope"}).status_code, 400,
        )
        with override_settings(STUDY_TIME_GLOBAL_BOARD_ENABLED=True):
            self.assertEqual(client.get("/leaderboard/board/", {"scope_type": "study_time"}).status_code, 200)
