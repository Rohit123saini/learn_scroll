# core/tests_streak_reminders.py
"""
T2.9 — streak-at-risk + class-reminder push scheduling with quiet hours.
"""
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase

from user_profile.models import Streak

from .models import Notification, NotificationPreference
from .tasks import create_notification_rows, send_streak_at_risk_reminders

User = get_user_model()
IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2026, 10, 13)
NT = Notification.NotifType.STREAK_AT_RISK


def at(hour, minute=0):
    return datetime.combine(TODAY, time(hour, minute), tzinfo=IST)


def run_at(when):
    with mock.patch("django.utils.timezone.now", return_value=when), \
            mock.patch("core.tasks.push_for_notification_row") as push:
        result = send_streak_at_risk_reminders()
    return result, push


def make_user(name, *, streak=5, last_active=TODAY - timedelta(days=1)):
    user = User.objects.create_user(username=name, password="x")
    Streak.objects.create(user=user, current_streak=streak, longest_streak=streak, last_active_date=last_active)
    return user


def row_for(user):
    return Notification.objects.filter(recipient=user, notif_type=NT).first()


class StreakAtRiskScheduleTests(TestCase):
    def test_nothing_before_the_reminder_hour(self):
        make_user("early")
        result, push = run_at(at(20, 0))
        self.assertEqual(result["skipped"], "before_window")
        self.assertEqual(Notification.objects.filter(notif_type=NT).count(), 0)
        push.assert_not_called()

    def test_creates_row_and_pushes_once_per_day(self):
        user = make_user("a")
        result, push = run_at(at(22, 0))
        self.assertEqual((result["created"], result["sent"]), (1, 1))
        push.assert_called_once()
        self.assertEqual(row_for(user).data["push_state"], "sent")
        self.assertEqual(row_for(user).data["streak_days"], 5)

        result, push = run_at(at(22, 30))  # next beat tick: nothing new
        self.assertEqual((result["created"], result["sent"]), (0, 0))
        push.assert_not_called()
        self.assertEqual(Notification.objects.filter(notif_type=NT, recipient=user).count(), 1)

    def test_already_checked_in_today_or_no_streak_is_ignored(self):
        make_user("done", last_active=TODAY)
        make_user("none", streak=0)
        make_user("old", last_active=TODAY - timedelta(days=3))
        result, push = run_at(at(22, 0))
        self.assertEqual(result["created"], 0)
        push.assert_not_called()

    def test_quiet_hours_defer_the_push_but_keep_the_bell_row(self):
        user = make_user("quiet")
        NotificationPreference.objects.create(
            user=user, quiet_start=time(22, 0), quiet_end=time(22, 45), timezone="Asia/Kolkata",
        )
        result, push = run_at(at(22, 15))
        self.assertEqual((result["created"], result["sent"], result["deferred"]), (1, 0, 1))
        push.assert_not_called()
        self.assertEqual(row_for(user).data["push_state"], "pending")

        result, push = run_at(at(23, 0))  # quiet window over -> the retry delivers it
        self.assertEqual((result["created"], result["sent"]), (0, 1))
        push.assert_called_once()
        self.assertEqual(row_for(user).data["push_state"], "sent")

    def test_quiet_hours_use_the_users_own_timezone(self):
        user = make_user("tz")
        # 22:15 IST == 16:45 UTC -> NOT quiet for a UTC user with 22:00-07:00.
        NotificationPreference.objects.create(
            user=user, quiet_start=time(22, 0), quiet_end=time(7, 0), timezone="UTC",
        )
        result, push = run_at(at(22, 15))
        self.assertEqual(result["sent"], 1)

    def test_dnd_defers(self):
        user = make_user("dnd")
        NotificationPreference.objects.create(user=user, dnd_until=at(23, 0))
        result, _ = run_at(at(22, 0))
        self.assertEqual(result["deferred"], 1)

    def test_muted_type_or_push_off_never_pushes_but_row_exists(self):
        muted = make_user("muted")
        NotificationPreference.objects.create(user=muted, muted_types=[NT])
        off = make_user("off")
        NotificationPreference.objects.create(user=off, push_enabled=False)
        result, push = run_at(at(22, 0))
        self.assertEqual((result["created"], result["sent"], result["skipped_prefs"]), (2, 0, 2))
        push.assert_not_called()
        self.assertEqual(row_for(muted).data["push_state"], "skipped")
        self.assertEqual(row_for(off).data["push_state"], "skipped")

    def test_legacy_user_task_delegates_and_reports_notified(self):
        from user_profile.tasks import send_streak_risk_reminders

        make_user("legacy")
        with mock.patch("django.utils.timezone.now", return_value=at(22, 0)), \
                mock.patch("core.tasks.push_for_notification_row"):
            result = send_streak_risk_reminders()
        self.assertEqual(result["notified"], 1)


class ClassReminderPushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stu", password="x")

    def test_rows_only_by_default(self):
        with mock.patch("core.tasks.push_for_notification_row") as push:
            failed = create_notification_rows([self.user.pk], "campus_class_starting", "t", "m")
        self.assertEqual(failed, [])
        push.assert_not_called()

    def test_push_flag_pushes_each_created_row(self):
        with mock.patch("core.tasks.push_for_notification_row") as push:
            create_notification_rows([self.user.pk], "campus_class_starting", "t", "m", {"a": 1}, push=True)
        push.assert_called_once()
        self.assertEqual(push.call_args.args[0].recipient_id, self.user.pk)

    def test_class_starting_reminder_asks_for_a_push(self):
        from campus import tasks as campus_tasks

        now = at(9, 0)
        with mock.patch.object(campus_tasks.bridge, "notify") as notify:
            campus_tasks._send_class_starting(
                recipients=[self.user.pk], subject_name="Maths", section_label="10-A",
                starts_at=now + timedelta(minutes=5), now=now, data={},
            )
        self.assertTrue(notify.call_args.kwargs["push"])

    def test_push_respects_quiet_hours_via_the_push_layer(self):
        NotificationPreference.objects.create(
            user=self.user, quiet_start=time(22, 0), quiet_end=time(7, 0), timezone="Asia/Kolkata",
        )
        row = Notification.objects.create(recipient=self.user, notif_type="campus_class_starting", title="t")
        with mock.patch("message.push_utils.send_rich_push") as send, \
                mock.patch("django.utils.timezone.now", return_value=at(23, 0)):
            from message.push_utils import send_push_for_notification

            send_push_for_notification(row)
        send.assert_not_called()
