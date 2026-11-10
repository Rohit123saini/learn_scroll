# testseries/tests_deadline_reminders.py
"""N6 - "test closing soon" reminders (24h + 1h) for IN_PROGRESS attempts.
(NOT RUN.)  python manage.py test testseries.tests_deadline_reminders

Attempts are created through the ORM with explicit started_at / deadline_at (TestAttempt.save only stamps those on a
brand-new row when started_at is None), so no API / coin fixtures are needed."""
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import Notification, NotificationPreference

from . import tasks
from .models import TestAttempt, TestSeries

User = get_user_model()
NT = Notification.NotifType.TESTSERIES_DUE_SOON


def _user(prefix):
    return User.objects.create_user(username=f"{prefix}_{uuid.uuid4().hex[:8]}", password="testpass123")


class DeadlineReminderTests(TestCase):
    def setUp(self):
        self.teacher = _user("teacher")
        self.student = _user("student")
        self.series = TestSeries.objects.create(
            source=TestSeries.Source.INDIVIDUAL, creator=self.teacher, title="Mock test",
            is_paid=False, price_coins=0, duration_minutes=30,
        )

    def attempt(self, *, started_ago, remaining, status=TestAttempt.Status.IN_PROGRESS, **extra):
        now = timezone.now()
        return TestAttempt.objects.create(
            series=self.series, student=self.student, status=status,
            started_at=now - started_ago, deadline_at=now + remaining, **extra,
        )

    def run_task(self):
        with mock.patch("core.tasks.push_for_notification_row") as push:
            summary = tasks.send_test_deadline_reminders()
        return summary, push

    def rows(self):
        return Notification.objects.filter(recipient=self.student, notif_type=NT)

    # ---- tiers -------------------------------------------------------------------------------------------------
    def test_24h_tier(self):
        a = self.attempt(started_ago=timedelta(hours=5), remaining=timedelta(hours=23))  # 28h window
        summary, push = self.run_task()
        row = self.rows().get()
        self.assertEqual(row.data["lead"], "24h")
        self.assertEqual(row.data["attempt_id"], str(a.pk))
        self.assertEqual(row.data["series_id"], str(self.series.pk))
        self.assertEqual(row.data["push_state"], "sent")
        self.assertEqual((summary["created"], summary["sent"]), (1, 1))
        push.assert_called_once()

    def test_1h_tier_only_not_both(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        self.run_task()
        self.assertEqual([r.data["lead"] for r in self.rows()], ["1h"])

    def test_more_than_24h_left_is_too_early(self):
        self.attempt(started_ago=timedelta(hours=1), remaining=timedelta(hours=30))
        summary, push = self.run_task()
        self.assertEqual(self.rows().count(), 0)
        push.assert_not_called()

    def test_deadline_already_passed_gets_nothing(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=-1))
        self.run_task()
        self.assertEqual(self.rows().count(), 0)

    # ---- who is skipped ----------------------------------------------------------------------------------------
    def test_short_timed_test_does_not_announce_one_hour_left(self):
        # 30-minute test, started 10 minutes ago: the student chose that limit.
        self.attempt(started_ago=timedelta(minutes=10), remaining=timedelta(minutes=20))
        self.run_task()
        self.assertEqual(self.rows().count(), 0)

    def test_submitted_attempt_is_ignored(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40),
                     status=TestAttempt.Status.SUBMITTED)
        self.run_task()
        self.assertEqual(self.rows().count(), 0)

    def test_student_who_just_saved_an_answer_is_left_alone(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40),
                     draft_saved_at=timezone.now() - timedelta(minutes=2))
        summary, push = self.run_task()
        self.assertEqual(self.rows().count(), 0)
        self.assertEqual(summary["skipped_active"], 1)
        push.assert_not_called()

    def test_stale_draft_does_not_count_as_active(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40),
                     draft_saved_at=timezone.now() - timedelta(hours=1))
        self.run_task()
        self.assertEqual(self.rows().count(), 1)

    def test_untimed_legacy_attempt_is_never_touched(self):
        TestAttempt.objects.create(series=self.series, student=self.student, started_at=timezone.now(), deadline_at=None)
        self.run_task()
        self.assertEqual(self.rows().count(), 0)

    # ---- idempotence + delivery state --------------------------------------------------------------------------
    def test_second_run_does_not_duplicate_row_or_push(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        _, first = self.run_task()
        _, second = self.run_task()
        self.assertEqual(self.rows().count(), 1)
        first.assert_called_once()
        second.assert_not_called()

    def test_quiet_hours_keep_it_pending_then_retry_pushes(self):
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        with mock.patch("core.tasks._push_state_for", return_value="defer"), \
                mock.patch("core.tasks.push_for_notification_row") as push:
            summary = tasks.send_test_deadline_reminders()
        push.assert_not_called()
        self.assertEqual(summary["deferred"], 1)
        self.assertEqual(self.rows().get().data["push_state"], "pending")

        summary, push = self.run_task()  # window over: now it goes out, still ONE row
        push.assert_called_once()
        self.assertEqual(self.rows().count(), 1)
        self.assertEqual(self.rows().get().data["push_state"], "sent")

    def test_user_turned_push_off_for_the_type(self):
        pref = NotificationPreference.for_user(self.student)
        pref.channel_overrides = {NT.value: {"push": False}}
        pref.save()
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        summary, push = self.run_task()
        push.assert_not_called()
        self.assertEqual(summary["skipped_prefs"], 1)
        row = self.rows().get()  # bell row still written
        self.assertEqual(row.data["push_state"], "skipped")

    def test_in_app_chip_off_means_no_row_and_no_push(self):
        pref = NotificationPreference.for_user(self.student)
        pref.channel_overrides = {NT.value: {"in_app": False}}
        pref.save()
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        summary, push = self.run_task()
        self.assertEqual(self.rows().count(), 0)
        push.assert_not_called()

    def test_one_bad_attempt_does_not_stop_the_rest(self):
        other = _user("student")
        self.attempt(started_ago=timedelta(hours=3), remaining=timedelta(minutes=40))
        now = timezone.now()
        TestAttempt.objects.create(series=self.series, student=other, started_at=now - timedelta(hours=3),
                                   deadline_at=now + timedelta(minutes=30))
        calls = {"n": 0}
        from core import services as core_services
        original = core_services.create_notification

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return original(*args, **kwargs)

        with mock.patch("core.services.create_notification", side_effect=flaky), \
                mock.patch("core.tasks.push_for_notification_row"):
            tasks.send_test_deadline_reminders()
        self.assertEqual(Notification.objects.filter(notif_type=NT).count(), 1)

    def test_helper_remaining_text(self):
        self.assertEqual(tasks._deadline_remaining_text(timedelta(minutes=40)), "40 min")
        self.assertEqual(tasks._deadline_remaining_text(timedelta(minutes=75)), "about 1 hour")
        self.assertEqual(tasks._deadline_remaining_text(timedelta(hours=23, minutes=50)), "24 hours")
