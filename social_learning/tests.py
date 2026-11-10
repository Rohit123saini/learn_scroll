"""
P-F5 - social_learning tests.  NOT RUN by the author (read-only task): run

    python manage.py test social_learning

Pomodoro / accountability / season services take an explicit `now`, so no test sleeps or freezes the clock.
"""
import uuid
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from message.models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    StudyRoomAttendance,
    StudyRoomPresence,
    StudyRoomSession,
)
from user_profile.models import BlockUser, Follow, Streak, UserBadge

from . import accountability as acc
from . import pomodoro, seasons
from .models import (
    AccountabilityMilestone,
    AccountabilityNudge,
    AccountabilityPair,
    ManualStudyLog,
    PomodoroCompletion,
    PomodoroRound,
    PomodoroTimer,
    SeasonReward,
    SeasonStanding,
    SeasonUserTotal,
    SocialPrefs,
    StudySeason,
)

User = get_user_model()
T0 = timezone.make_aware(datetime(2026, 11, 3, 10, 0))      # a Tuesday, 10:00 IST


def _mk(prefix, dob=None):
    return User.objects.create_user(username=f"{prefix}{uuid.uuid4().hex[:8]}", password="x", date_of_birth=dob)


def _years_ago(n):
    today = date.today()
    return today.replace(year=today.year - n, day=min(today.day, 28))


class _RoomBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.outsider = _mk("a"), _mk("b"), _mk("o")
        self.conv = Conversation.objects.create(type=ConversationType.PRIVATE)
        for u in (self.a, self.b):
            ConversationParticipant.objects.create(conversation=self.conv, user=u)
        self.room_name = f"study_{self.conv.id}"
        self.session = StudyRoomSession.objects.create(conversation=self.conv, room_name=self.room_name, started_by=self.a)

    def timer(self):
        return PomodoroTimer.objects.get(conversation=self.conv)

    def roll(self, now):
        t = self.timer()
        changed = pomodoro.advance_if_due(t, now)
        if changed:
            t.save()
        return self.timer()


# ======================================================================================================================
# Pomodoro
# ======================================================================================================================
class PomodoroStateMachineTests(_RoomBase):
    def test_start_makes_a_focus_round(self):
        t = pomodoro.start(self.conv.id, self.a, {}, now=T0)
        self.assertEqual(t.phase, "focus")
        self.assertTrue(t.is_running)
        self.assertEqual(t.phase_ends_at, T0 + timedelta(minutes=25))
        self.assertEqual(PomodoroRound.objects.filter(timer=t, kind="focus").count(), 1)

    def test_start_twice_is_a_conflict(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        with self.assertRaises(pomodoro.PomodoroError) as ctx:
            pomodoro.start(self.conv.id, self.b, {}, now=T0 + timedelta(minutes=1))
        self.assertEqual(ctx.exception.code, "already_running")

    def test_settings_are_clamped(self):
        t = pomodoro.start(self.conv.id, self.a, {"focus_minutes": 999, "short_break_minutes": 0, "cycles_before_long": 99}, now=T0)
        self.assertEqual(t.focus_minutes, pomodoro.MAX_FOCUS)
        self.assertEqual(t.short_break_minutes, pomodoro.MIN_BREAK)
        self.assertEqual(t.cycles_before_long, pomodoro.MAX_CYCLES)

    def test_due_phase_rolls_forward_without_gap(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        t = self.roll(T0 + timedelta(minutes=26))
        self.assertEqual(t.phase, "short_break")
        self.assertEqual(t.phase_started_at, T0 + timedelta(minutes=25))      # continuous, not "now"
        first = PomodoroRound.objects.get(timer=t, kind="focus")
        self.assertTrue(first.completed)
        self.assertEqual(first.ended_at, T0 + timedelta(minutes=25))

    def test_long_break_after_n_focus_rounds(self):
        pomodoro.start(self.conv.id, self.a, {"focus_minutes": 5, "short_break_minutes": 1, "long_break_minutes": 10, "cycles_before_long": 2}, now=T0)
        self.assertEqual(self.roll(T0 + timedelta(minutes=5, seconds=1)).phase, "short_break")
        self.assertEqual(self.roll(T0 + timedelta(minutes=6, seconds=1)).phase, "focus")
        t = self.roll(T0 + timedelta(minutes=11, seconds=1))
        self.assertEqual(t.phase, "long_break")
        self.assertEqual(t.cycle_index, 0)

    def test_pause_keeps_remaining_and_resume_continues(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        t = pomodoro.pause(self.conv.id, self.a, now=T0 + timedelta(minutes=10))
        self.assertFalse(t.is_running)
        self.assertEqual(t.paused_remaining_seconds, 15 * 60)
        t = pomodoro.resume(self.conv.id, self.a, now=T0 + timedelta(minutes=20))
        self.assertTrue(t.is_running)
        self.assertEqual(t.phase_ends_at, T0 + timedelta(minutes=35))

    def test_skip_focus_is_not_a_finished_round(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        t = pomodoro.skip(self.conv.id, self.a, now=T0 + timedelta(minutes=3))
        self.assertEqual(t.phase, "short_break")
        self.assertFalse(PomodoroRound.objects.get(timer=t, kind="focus").completed)
        self.assertEqual(t.cycle_index, 0)

    def test_stop_goes_idle(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        t = pomodoro.stop(self.conv.id, self.a, now=T0 + timedelta(minutes=1))
        self.assertEqual(t.phase, "idle")
        self.assertFalse(t.is_running)

    def test_abandoned_room_is_stopped_not_fabricated(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        t = self.roll(T0 + timedelta(days=5))
        self.assertEqual(t.phase, "idle")
        self.assertLessEqual(PomodoroRound.objects.filter(timer=t).count(), pomodoro.MAX_CATCHUP + 1)

    def test_payload_keeps_old_client_keys(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        body = pomodoro.payload(self.timer(), now=T0 + timedelta(minutes=1))
        for key in ("isRunning", "isBreak", "focusMinutes", "breakMinutes", "endAt"):
            self.assertIn(key, body)
        self.assertEqual(body["remainingSeconds"], 24 * 60)
        self.assertEqual(body["phase"], "focus")

    def test_sweep_stops_empty_room(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        PomodoroTimer.objects.filter(conversation=self.conv).update(updated_at=T0)
        self.assertEqual(pomodoro.sweep_idle_timers(now=T0 + timedelta(minutes=30)), 1)
        self.assertEqual(self.timer().phase, "idle")

    def test_sweep_keeps_room_with_someone_in_it(self):
        pomodoro.start(self.conv.id, self.a, {}, now=T0)
        PomodoroTimer.objects.filter(conversation=self.conv).update(updated_at=T0)
        StudyRoomPresence.objects.create(conversation=self.conv, user=self.a, room_name=self.room_name, joined_at=T0)
        self.assertEqual(pomodoro.sweep_idle_timers(now=T0 + timedelta(minutes=30)), 0)


class PomodoroClaimTests(_RoomBase):
    def finished_round(self):
        pomodoro.start(self.conv.id, self.a, {"focus_minutes": 5}, now=T0)
        self.roll(T0 + timedelta(minutes=6))
        return PomodoroRound.objects.get(timer=self.timer(), kind="focus")

    def present(self, user, start, end):
        StudyRoomPresence.objects.create(conversation=self.conv, user=user, room_name=self.room_name, joined_at=start, left_at=end)

    def test_credit_once(self):
        rnd = self.finished_round()
        self.present(self.a, T0, T0 + timedelta(minutes=8))
        ok, reason, done = pomodoro.claim_round(self.a, rnd.id, now=T0 + timedelta(minutes=7))
        self.assertTrue(ok)
        self.assertEqual(done.minutes, 5)
        ok2, reason2, _ = pomodoro.claim_round(self.a, rnd.id, now=T0 + timedelta(minutes=8))
        self.assertFalse(ok2)
        self.assertEqual(reason2, "already_claimed")
        self.assertEqual(PomodoroCompletion.objects.filter(user=self.a).count(), 1)

    def test_not_in_room_no_credit(self):
        rnd = self.finished_round()
        ok, reason, _ = pomodoro.claim_round(self.b, rnd.id, now=T0 + timedelta(minutes=7))
        self.assertFalse(ok)
        self.assertEqual(reason, "not_enough_presence")

    def test_joined_late_no_credit(self):
        rnd = self.finished_round()
        self.present(self.b, T0 + timedelta(minutes=3), T0 + timedelta(minutes=8))      # 2 of 5 minutes (< 80 %)
        self.assertFalse(pomodoro.claim_round(self.b, rnd.id, now=T0 + timedelta(minutes=7))[0])

    def test_two_devices_do_not_double_count(self):
        rnd = self.finished_round()
        self.present(self.a, T0, T0 + timedelta(minutes=2))
        self.present(self.a, T0 + timedelta(minutes=1), T0 + timedelta(minutes=3))      # overlaps -> 3 min merged, not 4
        secs = pomodoro.presence_overlap_seconds(self.a.id, self.conv.id, rnd.started_at, rnd.ended_at, now=T0 + timedelta(minutes=7))
        self.assertEqual(secs, 3 * 60)

    def test_unfinished_or_skipped_round_cannot_be_claimed(self):
        pomodoro.start(self.conv.id, self.a, {"focus_minutes": 5}, now=T0)
        pomodoro.skip(self.conv.id, self.a, now=T0 + timedelta(minutes=2))
        rnd = PomodoroRound.objects.get(kind="focus")
        with self.assertRaises(pomodoro.PomodoroError) as ctx:
            pomodoro.claim_round(self.a, rnd.id, now=T0 + timedelta(minutes=3))
        self.assertEqual(ctx.exception.code, "round_not_completed")

    def test_claim_window_expires(self):
        rnd = self.finished_round()
        self.present(self.a, T0, T0 + timedelta(minutes=8))
        with self.assertRaises(pomodoro.PomodoroError) as ctx:
            pomodoro.claim_round(self.a, rnd.id, now=T0 + timedelta(days=2))
        self.assertEqual(ctx.exception.code, "claim_expired")

    @override_settings(SOCIAL_POMODORO_DAILY_CREDIT_CAP=1)
    def test_daily_cap(self):
        rnd = self.finished_round()
        self.present(self.a, T0, T0 + timedelta(minutes=8))
        other_round = PomodoroRound.objects.create(timer=self.timer(), kind="focus", started_at=T0 + timedelta(minutes=10),
                                                   planned_seconds=300, ended_at=T0 + timedelta(minutes=15), completed=True)
        self.present(self.a, T0 + timedelta(minutes=10), T0 + timedelta(minutes=16))
        self.assertTrue(pomodoro.claim_round(self.a, rnd.id, now=T0 + timedelta(minutes=16))[0])
        ok, reason, _ = pomodoro.claim_round(self.a, other_round.id, now=T0 + timedelta(minutes=17))
        self.assertFalse(ok)
        self.assertEqual(reason, "daily_cap")


class PomodoroApiTests(_RoomBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

    def url(self, suffix=""):
        return f"/social/pomodoro/{self.conv.id}/{suffix}"

    def test_member_reads_state_outsider_cannot(self):
        self.client.force_authenticate(self.a)
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["timer"]["phase"], "idle")
        self.client.force_authenticate(self.outsider)
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["code"], "not_in_room")

    def test_start_needs_active_session(self):
        StudyRoomSession.objects.filter(pk=self.session.pk).update(ended_at=timezone.now())
        self.client.force_authenticate(self.a)
        res = self.client.post(self.url("start/"), {}, format="json")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.data["code"], "no_active_session")

    def test_start_pause_resume_stop_over_rest(self):
        self.client.force_authenticate(self.a)
        self.assertEqual(self.client.post(self.url("start/"), {"focus_minutes": 10}, format="json").status_code, 200)
        self.assertEqual(self.client.post(self.url("pause/")).data["timer"]["paused"], True)
        self.assertEqual(self.client.post(self.url("resume/")).data["timer"]["isRunning"], True)
        self.assertEqual(self.client.post(self.url("stop/")).data["timer"]["phase"], "idle")

    def test_unknown_action_404(self):
        self.client.force_authenticate(self.a)
        self.assertEqual(self.client.post(self.url("explode/")).status_code, 404)

    @override_settings(SOCIAL_LEARNING_ENABLED=False)
    def test_kill_switch(self):
        self.client.force_authenticate(self.a)
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["code"], "feature_disabled")

    def test_claim_bad_id(self):
        self.client.force_authenticate(self.a)
        self.assertEqual(self.client.post("/social/pomodoro/claim/", {"round_id": "nope"}, format="json").status_code, 400)


# ======================================================================================================================
# Accountability
# ======================================================================================================================
class PartnerRulesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.c = _mk("a"), _mk("b"), _mk("c")

    def follow(self, x, y):
        Follow.objects.create(follower=x, following=y, status=Follow.Status.ACCEPTED)

    def code(self, requester, target):
        try:
            acc.check_can_pair(requester, target)
        except acc.PartnerError as exc:
            return exc.code
        return None

    def test_needs_a_connection(self):
        self.assertEqual(self.code(self.a, self.b), "not_connected")
        self.follow(self.a, self.b)
        self.assertIsNone(self.code(self.a, self.b))

    def test_private_chat_counts_as_connection(self):
        conv = Conversation.objects.create(type=ConversationType.PRIVATE)
        for u in (self.a, self.b):
            ConversationParticipant.objects.create(conversation=conv, user=u)
        self.assertIsNone(self.code(self.a, self.b))

    def test_not_with_yourself(self):
        self.assertEqual(self.code(self.a, self.a), "self_pair")

    def test_block_looks_like_not_found(self):
        self.follow(self.a, self.b)
        BlockUser.objects.create(blocker=self.b, blocked=self.a)
        self.assertEqual(self.code(self.a, self.b), "user_not_found")

    def test_target_can_switch_requests_off(self):
        self.follow(self.a, self.b)
        SocialPrefs.objects.create(user=self.b, accepts_partner_requests=False)
        self.assertEqual(self.code(self.a, self.b), "not_accepting")

    def test_minor_and_adult_need_a_follow_link(self):
        minor, adult = _mk("m", _years_ago(14)), _mk("d", _years_ago(30))
        conv = Conversation.objects.create(type=ConversationType.PRIVATE)
        for u in (minor, adult):
            ConversationParticipant.objects.create(conversation=conv, user=u)
        self.assertEqual(self.code(adult, minor), "minor_privacy_blocked")      # chat alone is not enough
        self.follow(minor, adult)
        self.assertIsNone(self.code(adult, minor))

    def test_two_minors_may_pair(self):
        m1, m2 = _mk("m", _years_ago(14)), _mk("n", _years_ago(15))
        self.follow(m1, m2)
        self.assertIsNone(self.code(m1, m2))

    def test_duplicate_request_in_either_direction(self):
        self.follow(self.a, self.b)
        acc.request_pair(self.a, self.b)
        self.assertEqual(self.code(self.a, self.b), "pair_exists")
        self.assertEqual(self.code(self.b, self.a), "pair_exists")

    @override_settings(SOCIAL_PARTNER_MAX_PENDING_OUT=1)
    def test_pending_limit(self):
        self.follow(self.a, self.b)
        self.follow(self.a, self.c)
        acc.request_pair(self.a, self.b)
        self.assertEqual(self.code(self.a, self.c), "limit_pending")

    @override_settings(SOCIAL_PARTNER_MAX_ACTIVE=1)
    def test_active_limit(self):
        self.follow(self.a, self.b)
        self.follow(self.a, self.c)
        pair = acc.request_pair(self.a, self.b)
        acc.respond(pair, self.b, True)
        self.assertEqual(self.code(self.a, self.c), "limit_active")

    def test_only_the_invited_person_can_answer(self):
        self.follow(self.a, self.b)
        pair = acc.request_pair(self.a, self.b)
        with self.assertRaises(acc.PartnerError) as ctx:
            acc.respond(pair, self.a, True)
        self.assertEqual(ctx.exception.code, "not_invited")
        self.assertEqual(acc.respond(pair, self.b, True).status, "active")

    def test_declined_pair_can_be_requested_again_later(self):
        self.follow(self.a, self.b)
        pair = acc.request_pair(self.a, self.b)
        acc.respond(pair, self.b, False)
        self.assertEqual(AccountabilityPair.objects.get(pk=pair.pk).status, "declined")
        self.assertIsNone(self.code(self.a, self.b))

    def test_goal_is_clamped(self):
        self.assertEqual(acc.clamp_goal(1), acc.GOAL_MIN)
        self.assertEqual(acc.clamp_goal(9999), acc.GOAL_MAX)
        self.assertEqual(acc.clamp_goal("abc"), acc.DEFAULT_GOAL)

    def test_block_hides_and_ends_the_pair(self):
        self.follow(self.a, self.b)
        pair = acc.request_pair(self.a, self.b)
        acc.respond(pair, self.b, True)
        BlockUser.objects.create(blocker=self.b, blocked=self.a)
        self.assertEqual(acc.pairs_for(self.a), [])
        self.assertEqual(AccountabilityPair.objects.get(pk=pair.pk).status, "ended")


class SharedStreakTests(SimpleTestCase):
    today = date(2026, 11, 8)

    def days(self, *offsets, minutes=40):
        return {self.today - timedelta(days=o): minutes for o in offsets}

    def test_both_every_day(self):
        m = self.days(0, 1, 2)
        self.assertEqual(acc.shared_streak(m, m, 30, self.today), 3)

    def test_today_unfinished_does_not_break(self):
        m = self.days(1, 2, 3)
        self.assertEqual(acc.shared_streak(m, m, 30, self.today), 3)

    def test_one_missing_day_breaks(self):
        both = self.days(0, 1, 3)
        self.assertEqual(acc.shared_streak(both, both, 30, self.today), 2)

    def test_needs_both(self):
        a, b = self.days(0, 1, 2), self.days(0, 1)
        self.assertEqual(acc.shared_streak(a, b, 30, self.today), 2)

    def test_days_before_pair_started_never_count(self):
        m = self.days(0, 1, 2, 3, 4)
        self.assertEqual(acc.shared_streak(m, m, 30, self.today, started_on=self.today - timedelta(days=1)), 2)

    def test_below_goal_does_not_count(self):
        m = self.days(0, 1, minutes=29)
        self.assertEqual(acc.shared_streak(m, m, 30, self.today), 0)


class PartnerActionsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b = _mk("a"), _mk("b")
        Follow.objects.create(follower=self.a, following=self.b, status=Follow.Status.ACCEPTED)
        self.pair = acc.request_pair(self.a, self.b)
        acc.respond(self.pair, self.b, True)
        self.pair.refresh_from_db()

    def test_nudge_limit_and_templates(self):
        acc.send_nudge(self.pair, self.a, "lets_study")
        acc.send_nudge(self.pair, self.a, "keep_going")
        with self.assertRaises(acc.PartnerError) as ctx:
            acc.send_nudge(self.pair, self.a, "keep_going")
        self.assertEqual(ctx.exception.code, "nudge_limit")
        with self.assertRaises(acc.PartnerError) as ctx:
            acc.send_nudge(self.pair, self.b, "free text is not allowed")
        self.assertEqual(ctx.exception.code, "bad_template")

    def test_manual_minutes_capped_and_replace_same_day(self):
        acc.log_manual_minutes(self.a, 30)
        acc.log_manual_minutes(self.a, 45)
        self.assertEqual(ManualStudyLog.objects.filter(user=self.a).count(), 1)
        self.assertEqual(ManualStudyLog.objects.get(user=self.a).minutes, 45)
        with self.assertRaises(acc.PartnerError):
            acc.log_manual_minutes(self.a, 10_000)

    def test_end_by_either_side(self):
        acc.end_pair(self.pair, self.b)
        self.pair.refresh_from_db()
        self.assertEqual((self.pair.status, self.pair.ended_by_id), ("ended", self.b.id))

    def test_milestone_gives_one_freeze_each_exactly_once(self):
        now = T0
        stamp = now - timedelta(days=8)
        AccountabilityPair.objects.filter(pk=self.pair.pk).update(created_at=stamp, responded_at=stamp)
        today = timezone.localtime(now).date()
        for d in range(7):
            for u in (self.a, self.b):
                ManualStudyLog.objects.create(user=u, date=today - timedelta(days=d), minutes=40)
        self.assertEqual(acc.grant_milestones(now=now), 1)
        self.assertEqual(acc.grant_milestones(now=now), 0)
        self.assertEqual(AccountabilityMilestone.objects.filter(pair=self.pair, days=7).count(), 1)
        self.assertEqual(Streak.objects.get(user=self.a).freeze_tokens, 1)
        self.assertEqual(Streak.objects.get(user=self.b).freeze_tokens, 1)

    def test_evening_nudge_only_for_the_one_behind_once_a_day(self):
        now = T0.replace(hour=19, minute=30)
        today = timezone.localtime(now).date()
        ManualStudyLog.objects.create(user=self.a, date=today, minutes=60)      # a is done, b is not
        self.assertEqual(acc.evening_nudges(now=now), 1)
        self.assertEqual(acc.evening_nudges(now=now), 0)
        self.assertEqual(AccountabilityNudge.objects.filter(pair=self.pair, kind="auto").count(), 1)

    def test_evening_nudge_respects_reminder_switch(self):
        now = T0.replace(hour=19, minute=30)
        ManualStudyLog.objects.create(user=self.a, date=timezone.localtime(now).date(), minutes=60)
        SocialPrefs.objects.create(user=self.b, partner_reminders=False)
        self.assertEqual(acc.evening_nudges(now=now), 0)

    def test_nobody_done_or_both_done_means_no_nudge(self):
        now = T0.replace(hour=19, minute=30)
        self.assertEqual(acc.evening_nudges(now=now), 0)
        today = timezone.localtime(now).date()
        for u in (self.a, self.b):
            ManualStudyLog.objects.create(user=u, date=today, minutes=60)
        self.assertEqual(acc.evening_nudges(now=now), 0)


class PartnerApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.stranger = _mk("a"), _mk("b"), _mk("s")
        Follow.objects.create(follower=self.a, following=self.b, status=Follow.Status.ACCEPTED)
        self.client = APIClient()

    def test_request_accept_and_list(self):
        self.client.force_authenticate(self.a)
        res = self.client.post("/social/partners/request/", {"user_id": self.b.id, "daily_goal_minutes": 45}, format="json")
        self.assertEqual(res.status_code, 201)
        pair_id = res.data["id"]
        self.client.force_authenticate(self.b)
        self.assertEqual(len(self.client.get("/social/partners/").data["incoming"]), 1)
        res = self.client.post(f"/social/partners/{pair_id}/respond/", {"accept": True}, format="json")
        self.assertEqual(res.data["status"], "active")
        self.assertEqual(res.data["daily_goal_minutes"], 45)
        self.assertEqual(len(self.client.get("/social/partners/").data["active"]), 1)

    def test_stranger_cannot_open_someone_elses_pair(self):
        pair = acc.request_pair(self.a, self.b)
        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get(f"/social/partners/{pair.id}/").status_code, 404)
        self.assertEqual(self.client.post(f"/social/partners/{pair.id}/end/").status_code, 404)

    def test_request_to_stranger_is_refused(self):
        self.client.force_authenticate(self.a)
        res = self.client.post("/social/partners/request/", {"user_id": self.stranger.id}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.data["code"], "not_connected")

    def test_suggestions_only_connected_people(self):
        self.client.force_authenticate(self.a)
        ids = [r["user_id"] for r in self.client.get("/social/partners/suggestions/").data["results"]]
        self.assertEqual(ids, [self.b.id])


# ======================================================================================================================
# Seasons
# ======================================================================================================================
class _SeasonBase(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.c = _mk("a"), _mk("b"), _mk("c")
        self.conv = Conversation.objects.create(type=ConversationType.GROUP)
        self.start = timezone.make_aware(datetime(2026, 11, 2, 0, 0))              # Monday
        self.season = seasons.create_season(self.start, days=28)
        self.day = date(2026, 11, 3)

    def study(self, user, minutes, day=None):
        StudyRoomAttendance.objects.create(conversation=self.conv, user=user, attended_date=day or self.day,
                                           total_seconds=minutes * 60, presence_tracked=True)

    def pomodoro(self, user, day=None, n=1):
        timer, _ = PomodoroTimer.objects.get_or_create(conversation=self.conv)
        for _ in range(n):
            rnd = PomodoroRound.objects.create(timer=timer, kind="focus", started_at=T0, planned_seconds=1500, ended_at=T0, completed=True)
            PomodoroCompletion.objects.create(user=user, round=rnd, conversation=self.conv, minutes=25, local_date=day or self.day)

    def scores(self):
        return {r["user_id"]: r for r in seasons.compute_scores(self.season)}


class SeasonScoreTests(_SeasonBase):
    def test_daily_cap_and_active_day_bonus(self):
        self.study(self.a, 600)                                   # cap 360 -> 360 min + 1 active day (5)
        s = self.scores()[self.a.id]
        self.assertEqual((s["minutes"], s["active_days"], s["score"]), (360, 1, 365))

    def test_two_rooms_same_day_share_one_cap(self):
        other = Conversation.objects.create(type=ConversationType.GROUP)
        self.study(self.a, 300)
        StudyRoomAttendance.objects.create(conversation=other, user=self.a, attended_date=self.day, total_seconds=300 * 60, presence_tracked=True)
        self.assertEqual(self.scores()[self.a.id]["minutes"], 360)

    def test_consistency_beats_one_binge_of_same_minutes(self):
        self.study(self.a, 60, date(2026, 11, 3))
        self.study(self.a, 60, date(2026, 11, 4))
        self.study(self.b, 120, date(2026, 11, 3))
        s = self.scores()
        self.assertGreater(s[self.a.id]["score"], s[self.b.id]["score"])

    def test_pomodoros_add_points(self):
        self.pomodoro(self.a, n=2)
        self.assertEqual(self.scores()[self.a.id]["score"], 2 * 3)

    def test_activity_outside_the_window_is_the_reset(self):
        self.study(self.a, 100, date(2026, 10, 30))               # before the season
        self.study(self.a, 100, date(2026, 11, 30))               # after the season (ends 11-30 exclusive)
        self.assertNotIn(self.a.id, self.scores())

    def test_opted_out_and_inactive_are_left_out(self):
        self.study(self.a, 60)
        self.study(self.b, 60)
        self.study(self.c, 60)
        SocialPrefs.objects.create(user=self.b, season_opt_out=True)
        User.objects.filter(pk=self.c.pk).update(is_active=False)
        self.assertEqual(set(self.scores()), {self.a.id})

    def test_ranks_are_dense_and_stable(self):
        self.study(self.a, 100)
        self.study(self.b, 100)
        self.study(self.c, 30)
        rows = seasons.compute_scores(self.season)
        self.assertEqual([r["rank"] for r in rows], [1, 2, 3])
        self.assertEqual(rows[2]["user_id"], self.c.id)
        self.assertEqual(rows, seasons.compute_scores(self.season))

    def test_refresh_rewrites_provisional_rows(self):
        now = timezone.make_aware(datetime(2026, 11, 3, 12, 0))   # Nov 3 is "today": still live, not frozen yet
        self.study(self.a, 100)
        self.assertEqual(seasons.refresh_standings(self.season, now=now), 1)
        self.study(self.b, 200)
        self.assertEqual(seasons.refresh_standings(self.season, now=now), 2)
        self.assertEqual(SeasonStanding.objects.get(season=self.season, rank=1).user_id, self.b.id)


class SeasonIncrementalTests(_SeasonBase):
    """The cached (incremental) path must give EXACTLY the full recompute - it is only a faster way to the same numbers."""

    def _both(self, now):
        inc = seasons.compute_scores(self.season, now=now, incremental=True)
        self.assertEqual(inc, seasons.compute_scores(self.season))
        return inc

    def test_incremental_equals_full_over_a_season(self):
        self.study(self.a, 100, date(2026, 11, 3))
        self.study(self.a, 500, date(2026, 11, 4))                  # capped day
        self.study(self.b, 40, date(2026, 11, 5))
        self.pomodoro(self.b, day=date(2026, 11, 4), n=2)
        for hour_offset in (0, 1, 5, 9):                            # successive ticks as days pass
            now = timezone.make_aware(datetime(2026, 11, 3 + hour_offset, 12, 0))
            self._both(now)
        self.season.refresh_from_db()
        self.assertEqual(self.season.rollup_through, date(2026, 11, 11))

    def test_finished_days_are_frozen_and_not_rescanned_but_today_is_live(self):
        now = timezone.make_aware(datetime(2026, 11, 6, 12, 0))     # yesterday = Nov 5 -> days before Nov 5 are frozen
        self.study(self.a, 60, date(2026, 11, 3))
        self._both(now)
        self.season.refresh_from_db()
        self.assertEqual(self.season.rollup_through, date(2026, 11, 5))
        self.assertEqual(SeasonUserTotal.objects.get(season=self.season, user=self.a).minutes, 60)
        self.study(self.b, 30, date(2026, 11, 6))                    # live day: picked up without a rescan of frozen days
        self.assertEqual({r["user_id"] for r in self._both(now)}, {self.a.id, self.b.id})
        self.assertEqual(SeasonUserTotal.objects.get(season=self.season, user=self.a).minutes, 60)   # not added twice

    def test_a_second_refresh_does_not_double_count(self):
        now = timezone.make_aware(datetime(2026, 11, 8, 12, 0))
        self.study(self.a, 50, date(2026, 11, 3))
        first = self._both(now)
        self.assertEqual(self._both(now), first)

    def test_changing_the_scoring_config_rebuilds_the_cache(self):
        now = timezone.make_aware(datetime(2026, 11, 8, 12, 0))
        self.study(self.a, 600, date(2026, 11, 3))
        self.assertEqual(seasons.compute_scores(self.season, now=now, incremental=True)[0]["minutes"], 360)
        self.season.config = {"daily_cap_minutes": 100}
        self.season.save()
        self.season.refresh_from_db()
        self.assertEqual(seasons.compute_scores(self.season, now=now, incremental=True)[0]["minutes"], 100)

    def test_final_standings_ignore_a_stale_cache_and_the_cache_is_dropped_on_close(self):
        now = timezone.make_aware(datetime(2026, 11, 8, 12, 0))
        self.study(self.a, 100, date(2026, 11, 3))
        seasons.compute_scores(self.season, now=now, incremental=True)           # freezes Nov 3
        self.study(self.a, 200, date(2026, 11, 3))                               # late edit of a frozen day
        self.assertEqual(seasons.compute_scores(self.season)[0]["minutes"], 300)  # full recompute sees it
        seasons.finalize_season(self.season)
        self.assertEqual(SeasonStanding.objects.get(season=self.season, user=self.a).minutes, 300)
        self.assertFalse(SeasonUserTotal.objects.filter(season=self.season).exists())


class SeasonConfigTests(SimpleTestCase):
    def test_tiers_never_carry_coins_or_unknown_badges_or_big_freezes(self):
        tiers = seasons._safe_tiers([
            {"tier": "x", "rank_from": 1, "rank_to": 1, "badge": "super_admin", "freezes": 50, "coins": 9999},
            {"rank_from": "bad"},
        ])
        self.assertEqual(len(tiers), 1)
        self.assertEqual(tiers[0]["badge"], "")
        self.assertEqual(tiers[0]["freezes"], seasons.MAX_FREEZES_PER_TIER)
        self.assertNotIn("coins", tiers[0])

    def test_tier_for_rank(self):
        tiers = seasons.DEFAULT_CONFIG["tiers"]
        self.assertEqual(seasons.tier_for_rank(1, tiers)["tier"], "champion")
        self.assertEqual(seasons.tier_for_rank(3, tiers)["tier"], "top3")
        self.assertEqual(seasons.tier_for_rank(10, tiers)["tier"], "top10")
        self.assertEqual(seasons.tier_for_rank(999, tiers)["tier"], "finisher")


class SeasonFinalizeTests(_SeasonBase):
    def test_rewards_are_given_once_and_season_closes(self):
        self.study(self.a, 300)                      # rank 1, well above the reward floor
        self.study(self.b, 5, date(2026, 11, 4))     # below min_score_for_reward -> nothing
        summary = seasons.finalize_season(self.season)
        self.assertEqual(summary["given"], 1)
        self.season.refresh_from_db()
        self.assertEqual(self.season.status, "closed")
        reward = SeasonReward.objects.get(season=self.season)
        self.assertEqual((reward.user_id, reward.tier, reward.badge_code), (self.a.id, "champion", "season_champion"))
        self.assertTrue(UserBadge.objects.filter(user=self.a, badge__code="season_champion").exists())
        self.assertEqual(Streak.objects.get(user=self.a).freeze_tokens, reward.freezes_granted)
        self.assertEqual(seasons.finalize_season(self.season), {"already_closed": True})
        self.assertEqual(SeasonReward.objects.filter(season=self.season).count(), 1)

    def test_freeze_reward_respects_the_holding_cap(self):
        self.study(self.a, 300)
        Streak.objects.create(user=self.a, freeze_tokens=2)
        seasons.finalize_season(self.season)
        reward = SeasonReward.objects.get(season=self.season)
        self.assertEqual(reward.freezes_requested, 2)
        self.assertEqual(reward.freezes_granted, 0)
        self.assertEqual(Streak.objects.get(user=self.a).freeze_tokens, 2)

    def test_closed_season_standings_do_not_change(self):
        self.study(self.a, 300)
        seasons.finalize_season(self.season)
        self.study(self.b, 900, date(2026, 11, 5))
        self.season.refresh_from_db()
        self.assertEqual(seasons.refresh_standings(self.season), 0)
        self.assertEqual(SeasonStanding.objects.filter(season=self.season).count(), 1)

    def test_no_cash_or_coin_field_exists_on_a_reward(self):
        names = {f.name for f in SeasonReward._meta.get_fields()}
        self.assertFalse({"coins", "cash", "amount", "inr"} & names)


class SeasonLifecycleTests(TestCase):
    def test_first_tick_creates_a_running_season(self):
        out = seasons.tick(now=T0)
        self.assertEqual(out["created"], 1)
        s = seasons.current_season(T0)
        self.assertIsNotNone(s)
        self.assertEqual(timezone.localtime(s.starts_at).weekday(), 0)       # Monday
        self.assertEqual(s.ends_at - s.starts_at, timedelta(days=28))

    def test_tick_is_idempotent(self):
        seasons.tick(now=T0)
        n = StudySeason.objects.count()
        seasons.tick(now=T0)
        self.assertEqual(StudySeason.objects.count(), n)

    def test_next_season_is_queued_in_the_last_week_and_starts_where_the_old_one_ends(self):
        seasons.tick(now=T0)
        first = seasons.current_season(T0)
        late = first.ends_at - timedelta(days=2)
        seasons.tick(now=late)
        nxt = seasons.next_season_after(late)
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.starts_at, first.ends_at)
        self.assertEqual(nxt.number, first.number + 1)

    def test_tick_closes_an_ended_season_and_opens_the_next(self):
        seasons.tick(now=T0)
        first = seasons.current_season(T0)
        seasons.tick(now=first.ends_at - timedelta(days=1))
        seasons.tick(now=first.ends_at + timedelta(minutes=1))
        first.refresh_from_db()
        self.assertEqual(first.status, "closed")
        self.assertEqual(seasons.current_season(first.ends_at + timedelta(minutes=1)).number, first.number + 1)

    @override_settings(SOCIAL_SEASON_AUTO_CREATE=False)
    def test_auto_create_can_be_switched_off(self):
        self.assertEqual(seasons.tick(now=T0)["created"], 0)
        self.assertEqual(StudySeason.objects.count(), 0)


class SeasonApiTests(_SeasonBase):
    def setUp(self):
        super().setUp()
        StudySeason.objects.all().delete()
        now = timezone.now()
        self.season = seasons.create_season(now - timedelta(days=1), days=28)
        self.day = timezone.localtime(now).date()
        self.client = APIClient()

    def test_current_season_shows_my_rank_and_rules(self):
        self.study(self.a, 100)
        self.study(self.b, 50)
        seasons.tick()
        self.client.force_authenticate(self.b)
        res = self.client.get("/social/seasons/current/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["me"]["rank"], 2)
        self.assertEqual(res.data["me"]["points_to_pass_next"], res.data["top"][0]["score"] - res.data["top"][1]["score"] + 1)
        self.assertFalse(res.data["season"]["rules"]["cash_rewards"])
        self.assertEqual([r["is_me"] for r in res.data["top"]], [False, True])

    def test_minor_is_masked_for_an_adult_stranger_but_not_for_a_minor(self):
        minor, adult, other_minor = _mk("m", _years_ago(14)), _mk("d", _years_ago(30)), _mk("n", _years_ago(15))
        self.study(minor, 100)
        seasons.tick()
        self.client.force_authenticate(adult)
        row = self.client.get("/social/seasons/current/").data["top"][0]
        self.assertTrue(row["masked"])
        self.assertIsNone(row["user_id"])
        self.assertIsNone(row["photo"])
        self.client.force_authenticate(other_minor)
        self.assertFalse(self.client.get("/social/seasons/current/").data["top"][0]["masked"])

    def test_opt_out_removes_me_at_once(self):
        self.study(self.a, 100)
        seasons.tick()
        self.client.force_authenticate(self.a)
        self.assertEqual(self.client.put("/social/prefs/", {"season_opt_out": True}, format="json").status_code, 200)
        res = self.client.get("/social/seasons/current/")
        self.assertIsNone(res.data["me"])
        self.assertEqual(res.data["top"], [])

    def test_blocked_user_is_not_shown(self):
        self.study(self.a, 100)
        self.study(self.b, 50)
        seasons.tick()
        BlockUser.objects.create(blocker=self.b, blocked=self.a)
        self.client.force_authenticate(self.b)
        self.assertEqual([r["is_me"] for r in self.client.get("/social/seasons/current/").data["top"]], [True])

    def test_rewards_list_and_mark_seen(self):
        self.study(self.a, 300)
        seasons.finalize_season(self.season)
        self.client.force_authenticate(self.a)
        res = self.client.get("/social/seasons/rewards/")
        self.assertEqual((len(res.data["results"]), res.data["unseen"]), (1, 1))
        self.client.post("/social/seasons/rewards/")
        self.assertEqual(self.client.get("/social/seasons/rewards/").data["unseen"], 0)
        hist = self.client.get("/social/seasons/history/").data["results"]
        self.assertEqual(hist[0]["my_rank"], 1)
        self.assertEqual(hist[0]["reward"]["tier"], "champion")


class KillSwitchTests(TestCase):
    """P-F5 follow-up: `conf.enabled()` honours the P-J2 remote switch as well as the env master switch."""

    def test_on_by_default(self):
        from . import conf

        self.assertTrue(conf.enabled())

    def test_env_master_switch_off(self):
        from . import conf

        with override_settings(SOCIAL_LEARNING_ENABLED=False):
            self.assertFalse(conf.enabled())

    def test_feature_flags_off_env_switches_it_off(self):
        from . import conf

        with override_settings(FEATURE_FLAGS_OFF="social_learning"):
            self.assertFalse(conf.enabled())
        with override_settings(FEATURE_FLAGS_OFF="live,ai_features"):
            self.assertTrue(conf.enabled())
