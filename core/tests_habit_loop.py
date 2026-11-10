"""P-F3_F4 habit loop tests (written, NOT run). Run: python manage.py test core.tests_habit_loop"""
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from login.models import User
from post.models import Post, PostSave
from post.study_tools import REVISION_COLLECTION
from user_profile.models import DailyUsage, Streak, WeeklyRecap

from . import habit_loop as hl
from .models import Notification, NotificationPreference

IST = ZoneInfo("Asia/Kolkata")
NT = Notification.NotifType.REVISION_DECK_REMINDER
DAY = date(2026, 11, 4)  # a Wednesday


def at(hour, minute=0, day=DAY):
    return datetime.combine(day, time(hour, minute), tzinfo=IST)


def _user(name):
    return User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")


def _deck(user, n, age_days=5, now=None):
    """n posts in the user's Revision deck, saved `age_days` ago."""
    now = now or at(12)
    author = User.objects.get_or_create(username="hl_author", defaults={"email": "a@example.com"})[0]
    for _ in range(n):
        p = Post.objects.create(user=author, content="x", visibility="public", moderation_status="approved")
        s = PostSave.objects.create(user=user, post=p, collection_name=REVISION_COLLECTION)
        PostSave.objects.filter(pk=s.pk).update(created_at=now - timedelta(days=age_days))


class PureRulesTests(SimpleTestCase):
    def test_nudge_hour_clamps_into_the_window(self):
        self.assertEqual(hl.nudge_hour(23, 20), 22)   # 23 - 1 lead => 22 (== latest)
        self.assertEqual(hl.nudge_hour(21, 20), 20)   # 21 - 1
        self.assertEqual(hl.nudge_hour(9, 20), 18)    # a morning learner is not warned at 8
        self.assertEqual(hl.nudge_hour(None, 20), 20)  # no history => the fixed hour

    @override_settings(HABIT_LOOP={"smart_timing": False})
    def test_smart_timing_off_uses_the_fixed_hour(self):
        self.assertEqual(hl.nudge_hour(21, 20), 20)
        self.assertEqual(hl.nudge_hours_for([1, 2], 20), {1: 20, 2: 20})

    def test_next_action_priority(self):
        s = lambda **k: {"streak": {"at_risk": k.get("risk", False)}, "goal": {"completed": k.get("goal", True)},
                         "revision": {"due": k.get("due", 0)}, "weekly_report": k.get("weekly")}
        self.assertEqual(hl._next_action(s(risk=True, goal=False, due=9)), "streak")
        self.assertEqual(hl._next_action(s(goal=False, due=9)), "goal")
        self.assertEqual(hl._next_action(s(due=9)), "revision")
        self.assertEqual(hl._next_action(s(due=1, weekly={"x": 1})), "weekly_report")  # 1 < revision_min_items
        self.assertEqual(hl._next_action(s()), "none")

    def test_compose_revision_en_hi_plural_and_old_item(self):
        t, m = hl.compose_revision(1, 0, "en")
        self.assertIn("1 item", m)
        self.assertIn("is waiting", m)
        _, m = hl.compose_revision(4, 9, "en")
        self.assertIn("4 items", m)
        self.assertIn("9 days old", m)
        t, m = hl.compose_revision(4, 0, "hi")
        self.assertIn("4", m)
        self.assertNotEqual(t, hl.compose_revision(4, 0, "en")[0])

    def test_compose_weekly_report_lines(self):
        n = {"study_minutes": 95, "active_days": 4, "tests_attempted": 2, "classes_attended": 1, "posts_liked_received": 7,
             "streak_days": 4}
        subject, lines = hl.compose_weekly_report(n, deck_due=3, language="en")
        self.assertIn("weekly", subject.lower())
        self.assertTrue(any("95 min" in l and "4/7" in l for l in lines))
        self.assertTrue(any("3 items" in l for l in lines))
        _, lines0 = hl.compose_weekly_report(n, deck_due=0, language="en")
        self.assertEqual(len(lines0), len(lines) - 1)  # no deck line when nothing is due


class SmartTimingTests(TestCase):
    def test_typical_hour_is_the_median_of_last_heartbeats(self):
        u = _user("hl_t1")
        today = timezone.localdate()
        for i, hour in enumerate([21, 21, 22, 9], start=1):
            d = today - timedelta(days=i)
            ts = timezone.make_aware(datetime.combine(d, time(hour, 30)))
            DailyUsage.objects.create(user=u, date=d, seconds=600, last_heartbeat_at=ts)
        self.assertEqual(hl.typical_hours([u.pk], today).get(u.pk), 21)

    def test_too_little_history_is_absent(self):
        u = _user("hl_t2")
        today = timezone.localdate()
        for i in (1, 2):
            d = today - timedelta(days=i)
            DailyUsage.objects.create(user=u, date=d, seconds=600, last_heartbeat_at=timezone.make_aware(datetime.combine(d, time(20))))
        self.assertNotIn(u.pk, hl.typical_hours([u.pk], today))


class SlotTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_slot_is_taken_once_per_user_per_day(self):
        self.assertFalse(hl.slot_taken(7, DAY))
        hl.take_slot(7, DAY, "streak")
        self.assertTrue(hl.slot_taken(7, DAY))
        self.assertFalse(hl.slot_taken(7, DAY + timedelta(days=1)))
        self.assertFalse(hl.slot_taken(8, DAY))


class RevisionReminderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = _user("hl_rev")
        NotificationPreference.objects.create(user=self.u, timezone="Asia/Kolkata")
        _deck(self.u, 4)

    def run_at(self, when):
        with mock.patch("core.content_digest.push_pending_row", return_value="sent") as push:
            summary = hl.run_revision_reminders(when)
        return summary, push

    def test_creates_one_reminder_in_the_window(self):
        summary, push = self.run_at(at(18, 10))
        self.assertEqual(summary["created"], 1)
        row = Notification.objects.get(recipient=self.u, notif_type=NT)
        self.assertEqual(row.data["deck_size"], 4)
        push.assert_called_once()

    def test_nothing_outside_the_window(self):
        summary, _ = self.run_at(at(11, 0))
        self.assertEqual(summary["created"], 0)
        summary, _ = self.run_at(at(22, 0))  # 18:00 + 3 h grace has passed
        self.assertEqual(summary["created"], 0)

    def test_same_run_twice_is_idempotent(self):
        self.run_at(at(18, 0))
        summary, _ = self.run_at(at(18, 15))
        self.assertEqual(summary["created"], 0)
        self.assertEqual(Notification.objects.filter(recipient=self.u, notif_type=NT).count(), 1)

    def test_not_again_within_cadence_but_again_after(self):
        self.run_at(at(18, 0))
        # created_at is the real clock; the test runs on a simulated one, so stamp the row with the simulated time
        Notification.objects.filter(recipient=self.u, notif_type=NT).update(created_at=at(18, 0))
        s, _ = self.run_at(at(18, 0, DAY + timedelta(days=1)))
        self.assertEqual(s["recent"], 1)
        s, _ = self.run_at(at(18, 0, DAY + timedelta(days=4)))
        self.assertEqual(s["created"], 1)

    def test_skipped_when_already_studied_today(self):
        Streak.objects.create(user=self.u, current_streak=3, longest_streak=3, last_active_date=DAY)
        summary, _ = self.run_at(at(18, 0))
        self.assertEqual(summary["active_today"], 1)
        self.assertEqual(summary["created"], 0)

    def test_skipped_when_a_streak_warning_took_the_slot(self):
        Notification.objects.create(recipient=self.u, notif_type=Notification.NotifType.STREAK_AT_RISK, title="t", message="m")
        summary, _ = self.run_at(timezone.now().astimezone(IST).replace(hour=18, minute=5))
        self.assertEqual(summary["created"], 0)

    def test_small_or_fresh_deck_is_not_nagged(self):
        small = _user("hl_small")
        NotificationPreference.objects.create(user=small, timezone="Asia/Kolkata")
        _deck(small, 2)
        fresh = _user("hl_fresh")
        NotificationPreference.objects.create(user=fresh, timezone="Asia/Kolkata")
        _deck(fresh, 5, age_days=0)
        self.run_at(at(18, 0))
        self.assertFalse(Notification.objects.filter(recipient__in=[small, fresh], notif_type=NT).exists())

    def test_muted_type_creates_no_row(self):
        pref = NotificationPreference.objects.get(user=self.u)
        pref.muted_types = [NT.value]
        pref.save()
        summary, _ = self.run_at(at(18, 0))
        self.assertEqual(summary["muted"], 1)
        self.assertFalse(Notification.objects.filter(recipient=self.u, notif_type=NT).exists())

    def test_hindi_user_gets_hindi_copy(self):
        from user_profile.models import UserPreference
        up = UserPreference.for_user(self.u)
        up.language = "hi"
        up.save()
        self.run_at(at(18, 0))
        row = Notification.objects.get(recipient=self.u, notif_type=NT)
        self.assertEqual(row.title, hl.compose_revision(4, 5, "hi")[0])


class TodaySnapshotTests(TestCase):
    def test_new_user_has_goal_and_no_streak_risk(self):
        u = _user("hl_snap0")
        snap = hl.today_snapshot(u, at(10))
        self.assertEqual(snap["errors"], [])
        self.assertFalse(snap["streak"]["at_risk"])
        self.assertEqual(snap["goal"]["minutes"], 10)
        self.assertEqual(snap["goal"]["percent"], 0)
        self.assertIsNone(snap["revision"])
        self.assertEqual(snap["next_action"], "goal")

    def test_streak_at_risk_wins_over_everything(self):
        u = _user("hl_snap1")
        today = timezone.localdate(at(10))
        Streak.objects.create(user=u, current_streak=6, longest_streak=9, last_active_date=today - timedelta(days=1))
        _deck(u, 5)
        snap = hl.today_snapshot(u, at(10))
        self.assertTrue(snap["streak"]["at_risk"])
        self.assertEqual(snap["next_action"], "streak")
        self.assertEqual(snap["revision"]["due"], 5)

    def test_goal_progress_and_completion(self):
        u = _user("hl_snap2")
        today = timezone.localdate(at(10))
        Streak.objects.create(user=u, current_streak=2, longest_streak=2, last_active_date=today, daily_goal_minutes=10)
        DailyUsage.objects.create(user=u, date=today, seconds=300)
        g = hl.today_snapshot(u, at(10))["goal"]
        self.assertEqual((g["done_minutes"], g["percent"], g["completed"], g["remaining_minutes"]), (5, 50, False, 5))
        DailyUsage.objects.filter(user=u, date=today).update(seconds=700)
        g = hl.today_snapshot(u, at(10))["goal"]
        self.assertTrue(g["completed"])
        self.assertEqual(g["percent"], 100)

    def test_revision_is_next_once_goal_done(self):
        u = _user("hl_snap3")
        today = timezone.localdate(at(10))
        Streak.objects.create(user=u, current_streak=2, longest_streak=2, last_active_date=today, goal_completed_date=today)
        _deck(u, 4)
        self.assertEqual(hl.today_snapshot(u, at(10))["next_action"], "revision")

    def test_weekly_report_only_in_first_days_of_new_week(self):
        from user_profile.recap import week_bounds
        u = _user("hl_snap4")
        today = timezone.localdate(at(10))
        this_monday, _ = week_bounds(today)
        last_monday = this_monday - timedelta(days=7)
        # today_snapshot sees "today" = Tuesday of this week (patched below), so the streak must be active THAT day,
        # otherwise it is "at risk" and `streak` wins over `weekly_report`
        fake_today = this_monday + timedelta(days=1)
        Streak.objects.create(user=u, current_streak=1, longest_streak=1, last_active_date=fake_today,
                              goal_completed_date=fake_today)
        WeeklyRecap.objects.create(user=u, week_start=last_monday, week_end=last_monday + timedelta(days=6),
                                   tests_attempted=2, classes_attended=1, streak_days=3)
        DailyUsage.objects.create(user=u, date=last_monday, seconds=1800)
        with mock.patch("django.utils.timezone.localdate", return_value=this_monday + timedelta(days=1)):
            snap = hl.today_snapshot(u, at(10))
        self.assertEqual(snap["weekly_report"]["study_minutes"], 30)
        self.assertEqual(snap["weekly_report"]["active_days"], 1)
        self.assertEqual(snap["next_action"], "weekly_report")
        with mock.patch("django.utils.timezone.localdate", return_value=this_monday + timedelta(days=5)):
            self.assertIsNone(hl.today_snapshot(u, at(10))["weekly_report"])


class WeeklyEmailTests(TestCase):
    def setUp(self):
        self.u = _user("hl_mail")
        self.recap = WeeklyRecap.objects.create(
            user=self.u, week_start=DAY - timedelta(days=9), week_end=DAY - timedelta(days=3), tests_attempted=1,
            classes_attended=0, streak_days=2,
        )

    @override_settings(HABIT_LOOP={"weekly_email": False})
    def test_switch_off(self):
        self.assertEqual(hl.send_weekly_report_email(self.u.pk, self.recap.pk), "off")

    def test_unknown_recap_or_other_users_recap(self):
        self.assertEqual(hl.send_weekly_report_email(self.u.pk, 99999), "no_recap")
        other = _user("hl_mail2")
        self.assertEqual(hl.send_weekly_report_email(other.pk, self.recap.pk), "no_recap")

    def test_blocked_when_channel_policy_says_no(self):
        decision = mock.Mock(allowed=False, reason="no_verified_email")
        with mock.patch("core.channel_policy.decide", return_value=decision):
            self.assertEqual(hl.send_weekly_report_email(self.u.pk, self.recap.pk), "blocked:no_verified_email")

    def test_sent_marks_the_bell_row_emailed(self):
        row = Notification.objects.create(
            recipient=self.u, notif_type=Notification.NotifType.WEEKLY_RECAP_READY, title="t", message="m",
            data={"recap_id": self.recap.pk},
        )
        decision = mock.Mock(allowed=True, reason="", address="hl_mail@example.com")
        with mock.patch("core.channel_policy.decide", return_value=decision), \
                mock.patch("core.channel_policy.unsubscribe_url", return_value="https://x/u"), \
                mock.patch("core.channel_policy.email_headers", return_value={}), \
                mock.patch("core.channel_policy.record_send") as rec, \
                mock.patch("core.channel_templates.render_event_email", return_value=("s", "text", "<p>html</p>")), \
                mock.patch("django.core.mail.EmailMultiAlternatives.send") as send:
            self.assertEqual(hl.send_weekly_report_email(self.u.pk, self.recap.pk), "sent")
        send.assert_called_once()
        rec.assert_called_once()
        row.refresh_from_db()
        self.assertTrue(row.data.get("emailed"))


class PerTypeToggleTests(TestCase):
    """Per-type toggle (N1): the new type is in the settings 'learning' group and obeys mute / master switch."""

    def test_type_is_a_reminder_with_snooze_and_turn_off(self):
        from . import notification_reminders as nr
        self.assertIn("REVISION_DECK_REMINDER", nr.REMINDER_TYPE_NAMES)

    def test_muted_type_is_not_in_app_allowed(self):
        u = _user("hl_tog")
        pref = NotificationPreference.for_user(u)
        pref.muted_types = [NT.value]
        pref.save()
        # by design a mute switches off push / email / sms / whatsapp; the bell row (in_app) has its own per-type chip
        self.assertFalse(pref.channel_allowed(NT, "push"))
        self.assertFalse(pref.channel_allowed(NT, "email"))
        self.assertTrue(pref.in_app_allowed(NT))
        pref.muted_types = []
        pref.channel_overrides = {NT.value: {"in_app": False}}
        pref.save()
        self.assertFalse(pref.in_app_allowed(NT))
        pref.channel_overrides = {}
        pref.save()
        self.assertTrue(pref.in_app_allowed(NT))
        self.assertTrue(pref.channel_allowed(NT, "push"))

    def test_type_is_in_the_learning_settings_group(self):
        from . import notification_center as nc
        self.assertEqual(nc._group_of("revision_deck_reminder"), "learning")
        # preference_groups() lists notif_type VALUES (lower-case), not enum member names
        self.assertIn(NT.value, Notification.preference_groups()["learning"])
