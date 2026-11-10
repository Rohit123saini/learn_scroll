"""Task 13 tests: copyright Celery tasks retry, alert on final failure, leave heartbeats; health command."""
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from . import tasks
from .management.commands.check_copyright_automation import expected_intervals


class TaskAlertingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_success_leaves_a_heartbeat(self):
        self.assertIsNone(tasks.get_heartbeat("copyrights.expire_strikes"))
        res = tasks.expire_strikes.apply()
        self.assertTrue(res.successful(), res.traceback)
        hb = tasks.get_heartbeat("copyrights.expire_strikes")
        self.assertIsNotNone(hb)
        self.assertLess(datetime.now(dt_timezone.utc) - hb["at"], timedelta(minutes=1))

    def test_failure_retries_then_alerts_once(self):
        with mock.patch("copyrights.services.expire_strikes", side_effect=RuntimeError("db down")) as svc, \
                mock.patch("copyrights.tasks.alert_failure") as alert:
            res = tasks.expire_strikes.apply()
        self.assertTrue(res.failed())
        self.assertEqual(svc.call_count, 1 + tasks.CopyrightTask.max_retries)
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], "copyrights.expire_strikes")
        self.assertIsNone(tasks.get_heartbeat("copyrights.expire_strikes"))  # no false "healthy" signal

    def test_alert_failure_logs_error_and_tags_sentry(self):
        exc = RuntimeError("boom")
        with self.assertLogs("copyrights.tasks", level="ERROR") as logs, \
                mock.patch("sentry_sdk.capture_exception") as capture:
            tasks.alert_failure("copyrights.expire_strikes", exc, "tid-1")
        self.assertIn("COPYRIGHT TASK FAILED", logs.output[0])
        capture.assert_called_once_with(exc)

    def test_alerting_never_raises(self):
        with mock.patch("sentry_sdk.capture_exception", side_effect=Exception("sentry down")):
            tasks.alert_failure("copyrights.expire_strikes", RuntimeError("x"))  # must not raise


class BeatScheduleCoverageTests(TestCase):
    def test_every_copyright_task_is_scheduled(self):
        self.assertEqual(set(expected_intervals()), set(tasks.TASK_NAMES))


@override_settings(CELERY_BEAT_SCHEDULE={
    n: {"task": n, "schedule": 3600.0} for n in tasks.TASK_NAMES})
class HealthCommandTests(TestCase):
    def setUp(self):
        cache.clear()

    def run_cmd(self, *extra):
        out = io.StringIO()
        call_command("check_copyright_automation", "--skip-ping", *extra, stdout=out)
        return out.getvalue()

    def test_unhealthy_when_nothing_ever_ran(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_cmd()
        self.assertIn("no successful run recorded", str(ctx.exception))

    def test_healthy_after_all_tasks_ran(self):
        for n in tasks.TASK_NAMES:
            tasks.record_heartbeat(n, "ok")
        self.assertIn("healthy", self.run_cmd())

    def test_stale_heartbeat_is_reported_and_logged(self):
        for n in tasks.TASK_NAMES:
            tasks.record_heartbeat(n, "ok")
        old = (datetime.now(dt_timezone.utc) - timedelta(hours=6)).isoformat()
        cache.set(tasks.HEARTBEAT_KEY.format(name="copyrights.auto_restore_counter_notices"),
                  {"at": old, "result": "ok"}, timeout=None)
        with self.assertLogs("copyrights.management.commands.check_copyright_automation", "ERROR"):
            with self.assertRaises(CommandError) as ctx:
                self.run_cmd("--alert")
        self.assertIn("auto_restore_counter_notices", str(ctx.exception))

    def test_task_missing_from_schedule_is_flagged(self):
        sched = {n: {"task": n, "schedule": 3600.0} for n in tasks.TASK_NAMES[1:]}
        for n in tasks.TASK_NAMES:
            tasks.record_heartbeat(n, "ok")
        with override_settings(CELERY_BEAT_SCHEDULE=sched):
            with self.assertRaises(CommandError) as ctx:
                self.run_cmd()
        self.assertIn("not in CELERY_BEAT_SCHEDULE", str(ctx.exception))
