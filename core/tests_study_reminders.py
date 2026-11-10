"""
S8 - the student's own study reminder (core/study_reminders.py). NOT RUN by the author (read-only task):
    python manage.py test core.tests_study_reminders

2026-10-26 is a Monday. All times are IST (the test users get NotificationPreference.timezone = Asia/Kolkata).
"""
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase

from user_profile.models import Streak, UserPreference

from . import notification_priority as prio
from . import notification_reminders as rem
from . import notification_routes as nr
from . import study_reminders as sr
from .models import Notification, NotificationPreference
from .tasks import send_study_reminders

User = get_user_model()
IST = ZoneInfo("Asia/Kolkata")
MON = datetime.date(2026, 10, 26)
NT = Notification.NotifType.STUDY_REMINDER


def at(day_offset, hour, minute=0):
    return datetime.datetime.combine(MON + datetime.timedelta(days=day_offset), datetime.time(hour, minute), tzinfo=IST)


def make_user(name="stud", *, enabled=True, time=datetime.time(18, 0), days=None, language="en", tz="Asia/Kolkata",
              notif_pref=True, **pref_extra):
    user = User.objects.create_user(username=name, password="x")
    UserPreference.objects.create(
        user=user, study_reminder_enabled=enabled, study_reminder_time=time, study_reminder_days=days or [],
        language=language, **pref_extra,
    )
    if notif_pref:
        # a post_save signal on UserPreference already creates this row (core/signals.py) -> update it, never create twice
        NotificationPreference.objects.update_or_create(user=user, defaults={"timezone": tz})
    return user


def rows(user):
    return list(Notification.objects.filter(recipient=user, notif_type=NT))


def run_at(when):
    with mock.patch("core.tasks.push_for_notification_row") as push:
        result = sr.run(now=when)
    return result, push


# ---------------------------------------------------------------------------------------------------------------------
class RegistrationTests(SimpleTestCase):
    """STUDY_REMINDER must be wired everywhere a NotifType has to be (these also guard the other route / center tests)."""

    def test_is_urgent_so_focus_mode_cannot_eat_it(self):
        self.assertEqual(prio.priority_for(NT), prio.URGENT)

    def test_has_snooze_and_turn_off_but_is_never_cap_gated(self):
        self.assertIn(NT.value, rem.reminder_types())
        self.assertNotIn(NT.value, rem.gated_types())

    def test_routes_to_home_and_has_a_spec(self):
        self.assertIn(NT.value, nr.SPECS)
        self.assertEqual(nr.route_for(NT, {}).route, "home")
        self.assertEqual(nr.route_for(NT, {"kind": "study_reminder"}).route, "home")

    def test_is_in_the_learning_settings_group(self):
        self.assertIn(NT.value, Notification.preference_groups()["learning"])

    def test_not_a_locked_type(self):
        self.assertNotIn(NT.value, Notification.locked_types())

    def test_why_line_is_bilingual(self):
        from . import notification_center as center

        why = center.why_for(Notification(notif_type=NT, title="t"))
        self.assertEqual(why["control"], "type_settings")
        self.assertTrue(why["en"].strip() and why["hi"].strip())


class ComposeTests(SimpleTestCase):
    def test_english(self):
        title, msg = sr.compose(20, None, "en")
        self.assertIn("study", title.lower())
        self.assertIn("20-minute", msg)
        self.assertNotIn("exam", msg.lower())

    def test_days_to_exam(self):
        self.assertIn("5 days to your exam", sr.compose(20, 5, "en")[1])
        self.assertIn("1 day to your exam", sr.compose(20, 1, "en")[1])
        self.assertIn("exam is today", sr.compose(20, 0, "en")[1].lower())

    def test_hindi(self):
        title, msg = sr.compose(15, 3, "hi")
        self.assertIn("पढ़ाई", title)
        self.assertIn("15", msg)
        self.assertIn("3 दिन", msg)

    def test_default_goal_when_missing(self):
        self.assertIn("10-minute", sr.compose(0, None, "en")[1])


# ---------------------------------------------------------------------------------------------------------------------
class RunTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_creates_row_pushes_and_marks_the_day(self):
        user = make_user()
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_called_once()
        (row,) = rows(user)
        self.assertEqual(row.data["push_state"], "sent")
        self.assertEqual(row.data["kind"], "study_reminder")
        self.assertEqual(row.data["goal_minutes"], 10)
        self.assertEqual(UserPreference.objects.get(user=user).last_study_reminder_on, MON)

    def test_once_per_local_day(self):
        user = make_user()
        run_at(at(0, 18, 5))
        result, push = run_at(at(0, 18, 20))  # next beat tick
        self.assertEqual((result["created"], result["not_due"]), (0, 1))
        push.assert_not_called()
        self.assertEqual(len(rows(user)), 1)
        result, _ = run_at(at(1, 18, 5))  # next day: again
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(rows(user)), 2)

    def test_not_before_the_time_and_not_after_the_grace_window(self):
        user = make_user()
        self.assertEqual(run_at(at(0, 17, 55))[0]["created"], 0)
        self.assertEqual(run_at(at(0, 21, 30))[0]["created"], 0)  # 3 h 30 min late: skipped, never a late-night nudge
        self.assertEqual(rows(user), [])

    def test_chosen_weekdays_only(self):
        user = make_user(days=[1, 3])  # Tue, Thu
        self.assertEqual(run_at(at(0, 18, 5))[0]["created"], 0)  # Monday
        self.assertEqual(run_at(at(1, 18, 5))[0]["created"], 1)  # Tuesday
        self.assertEqual(len(rows(user)), 1)

    def test_uses_the_users_own_timezone(self):
        user = make_user(tz="America/New_York")
        # 18:05 IST is 08:35 in New York - not yet 18:00 there
        self.assertEqual(run_at(at(0, 18, 5))[0]["created"], 0)
        ny_evening = datetime.datetime(2026, 10, 26, 18, 5, tzinfo=ZoneInfo("America/New_York"))
        self.assertEqual(run_at(ny_evening)[0]["created"], 1)
        self.assertEqual(len(rows(user)), 1)

    def test_disabled_and_inactive_users_are_ignored(self):
        off = make_user("off", enabled=False)
        gone = make_user("gone")
        User.objects.filter(pk=gone.pk).update(is_active=False)
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["checked"], 0)
        self.assertEqual((rows(off), rows(gone)), ([], []))
        push.assert_not_called()

    def test_goal_already_done_today_means_no_reminder(self):
        user = make_user()
        Streak.objects.create(user=user, daily_goal_minutes=20, goal_completed_date=MON)
        result, push = run_at(at(0, 18, 5))
        self.assertEqual((result["created"], result["goal_done"]), (0, 1))
        push.assert_not_called()
        self.assertEqual(rows(user), [])
        self.assertEqual(UserPreference.objects.get(user=user).last_study_reminder_on, MON)  # not re-checked every 15 min

    def test_goal_minutes_and_exam_countdown_in_the_row(self):
        user = make_user(exam_date=MON + datetime.timedelta(days=9))
        Streak.objects.create(user=user, daily_goal_minutes=45)
        run_at(at(0, 18, 5))
        (row,) = rows(user)
        self.assertEqual(row.data["goal_minutes"], 45)
        self.assertEqual(row.data["days_to_exam"], 9)
        self.assertIn("45", row.message)
        self.assertIn("9 days", row.message)

    def test_hindi_user_gets_hindi_text(self):
        user = make_user(language="hi")
        run_at(at(0, 18, 5))
        (row,) = rows(user)
        self.assertIn("पढ़ाई", row.title)

    def test_user_without_a_notification_preference_row_still_gets_it(self):
        user = make_user(notif_pref=False)  # server timezone (TIME_ZONE = Asia/Kolkata), defaults: push on
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_called_once()
        self.assertEqual(len(rows(user)), 1)

    def test_updates_do_not_touch_the_settings_etag(self):
        user = make_user()
        before = UserPreference.objects.get(user=user).updated_at
        run_at(at(0, 18, 5))
        self.assertEqual(UserPreference.objects.get(user=user).updated_at, before)  # .update(), no auto_now

    def test_one_broken_user_does_not_stop_the_rest(self):
        a = make_user("a")
        b = make_user("b")
        real = sr.compose
        calls = {"n": 0}

        def flaky(*args, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real(*args, **kw)

        with mock.patch.object(sr, "compose", side_effect=flaky):
            result, _ = run_at(at(0, 18, 5))
        self.assertEqual((result["failed"], result["created"]), (1, 1))
        self.assertEqual(len(rows(a)) + len(rows(b)), 1)


# ---------------------------------------------------------------------------------------------------------------------
class PushRulesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_push_off_keeps_the_bell_row_but_no_push(self):
        user = make_user()
        NotificationPreference.objects.filter(user=user).update(push_enabled=False)
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_not_called()
        self.assertEqual(rows(user)[0].data["push_state"], "skipped")

    def test_muted_type_means_no_row_at_all(self):
        user = make_user()
        NotificationPreference.objects.filter(user=user).update(muted_types=[NT.value])
        result, push = run_at(at(0, 18, 5))
        self.assertEqual((result["created"], result["muted"]), (0, 1))
        push.assert_not_called()
        self.assertEqual(rows(user), [])

    def test_turn_off_this_type_via_the_matrix_stops_only_the_push(self):
        user = make_user()
        rem.turn_off_type(user, NT.value)  # the N6 "Turn off" button
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_not_called()
        self.assertEqual(len(rows(user)), 1)  # bell row still comes (in-app chip untouched)

    def test_focus_mode_does_not_block_it(self):
        user = make_user(exam_mode=True)
        NotificationPreference.objects.filter(user=user).update(focus_priority_only=True)
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_called_once()  # urgent tier: the user asked for it

    def test_daily_reminder_cap_does_not_eat_it(self):
        user = make_user()
        with mock.patch.object(rem, "daily_cap", return_value=1):
            rem.claim_slot(user.pk, Notification.NotifType.STREAK_AT_RISK.value)  # cap already used up
            result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_called_once()

    def test_quiet_hours_hold_the_push_then_release_it(self):
        user = make_user()
        NotificationPreference.objects.filter(user=user).update(
            quiet_start=datetime.time(17, 0), quiet_end=datetime.time(19, 0),
        )
        result, push = run_at(at(0, 18, 5))
        self.assertEqual(result["created"], 1)
        push.assert_not_called()
        (row,) = rows(user)
        self.assertEqual(row.data["push_state"], "pending")
        Notification.objects.filter(pk=row.pk).update(created_at=at(0, 18, 5))  # pin to the mocked clock

        result, push = run_at(at(0, 18, 30))  # still quiet
        push.assert_not_called()
        self.assertEqual(result["flush"]["still_pending"], 1)

        result, push = run_at(at(0, 19, 5))  # quiet window over, still inside the 3 h grace
        push.assert_called_once()
        self.assertEqual(result["flush"]["sent"], 1)
        row.refresh_from_db()
        self.assertEqual(row.data["push_state"], "sent")

    def test_pending_reminder_expires_after_the_grace_window(self):
        user = make_user()
        NotificationPreference.objects.filter(user=user).update(
            quiet_start=datetime.time(17, 0), quiet_end=datetime.time(23, 0),
        )
        run_at(at(0, 18, 5))
        (row,) = rows(user)
        Notification.objects.filter(pk=row.pk).update(created_at=at(0, 18, 5))  # pin to the mocked clock
        result, push = run_at(at(0, 22, 0))  # 3 h 55 min later - too late to say "study now"
        push.assert_not_called()
        self.assertEqual(result["flush"]["expired"], 1)
        row.refresh_from_db()
        self.assertEqual(row.data["push_state"], "expired")

    def test_snooze_works_on_a_study_reminder_row(self):
        user = make_user()
        run_at(at(0, 18, 5))
        (row,) = rows(user)
        with mock.patch("core.notification_reminders._schedule_delivery", return_value=True) as sched:
            out = rem.snooze_reminder(row, 30, now=at(0, 18, 10))
        self.assertEqual(out["minutes"], 30)
        sched.assert_called_once()


# ---------------------------------------------------------------------------------------------------------------------
class TaskAndSignalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_celery_task_wraps_run(self):
        with mock.patch("core.study_reminders.run", return_value={"created": 3}) as run:
            self.assertEqual(send_study_reminders(), {"created": 3})
        run.assert_called_once_with()

    def test_celery_task_never_raises(self):
        with mock.patch("core.study_reminders.run", side_effect=RuntimeError("db down")):
            self.assertEqual(send_study_reminders(), {"error": True})

    def test_beat_entry_exists_and_points_to_a_real_task(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["core-send-study-reminders"]
        self.assertEqual(entry["task"], "core.send_study_reminders")
        self.assertEqual(send_study_reminders.name, entry["task"])

    def test_enabling_the_reminder_or_schedule_creates_the_notification_preference_row(self):
        user = User.objects.create_user(username="sig", password="x")
        self.assertFalse(NotificationPreference.objects.filter(user=user).exists())
        UserPreference.objects.create(user=user, study_reminder_enabled=True)
        self.assertTrue(NotificationPreference.objects.filter(user=user).exists())

        user2 = User.objects.create_user(username="sig2", password="x")
        UserPreference.objects.create(
            user=user2, focus_schedule_enabled=True, focus_schedule=[{"days": [], "start": "09:00", "end": "10:00"}],
        )
        self.assertTrue(NotificationPreference.objects.filter(user=user2).exists())

    def test_plain_preference_save_creates_no_row(self):
        user = User.objects.create_user(username="sig3", password="x")
        UserPreference.objects.create(user=user, theme="dark")
        self.assertFalse(NotificationPreference.objects.filter(user=user).exists())


# ---------------------------------------------------------------------------------------------------------------------
class FocusScheduleDeliveryTests(TestCase):
    """The focus schedule is the notifications half of S8: inside a window only urgent types push."""

    def setUp(self):
        self.user = make_user("focus", enabled=False)
        UserPreference.objects.filter(user=self.user).update(
            focus_schedule_enabled=True, focus_schedule=[{"days": [], "start": "09:00", "end": "11:00"}],
        )
        self.pref = NotificationPreference.objects.get(user=self.user)

    def reason(self, notif_type, when):
        self.pref = NotificationPreference.objects.get(user=self.user)  # drop the per-instance focus cache
        with mock.patch("django.utils.timezone.now", return_value=when):
            return self.pref.push_block_reason(notif_type.value, when)

    def test_inside_the_window_normal_types_are_held_back_but_urgent_ones_go(self):
        self.assertEqual(self.reason(Notification.NotifType.POST_COMMENTED, at(0, 10, 0)), "focus")
        self.assertIsNone(self.reason(Notification.NotifType.SESSION_LIVE, at(0, 10, 0)))
        self.assertIsNone(self.reason(NT, at(0, 10, 0)))  # the study reminder itself

    def test_outside_the_window_everything_goes(self):
        self.assertIsNone(self.reason(Notification.NotifType.POST_COMMENTED, at(0, 12, 0)))

    def test_prime_focus_batches_the_schedule_too(self):
        NotificationPreference.prime_focus([self.pref])
        self.assertIn(self.pref._focus_active_cache, (True, False))  # filled without a per-row query
