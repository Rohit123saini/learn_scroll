"""
T2 - "database is locked" hardening (written by reading the code; NOT run yet).
Run: python manage.py test common.tests_t2_db_retry
"""
from unittest import mock

from django.conf import settings
from django.db import OperationalError
from django.test import SimpleTestCase

from common import db_retry


def _locked():
    return OperationalError("database is locked")


class RetryHelperTests(SimpleTestCase):
    @mock.patch("common.db_retry.time.sleep")
    def test_three_retries_then_success(self, sleep):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] < 4:
                raise _locked()
            return "ok"

        self.assertEqual(db_retry.retry_on_db_lock(fn, retries=db_retry.DEFAULT_WRITE_RETRIES), "ok")
        self.assertEqual(calls["n"], 4)
        self.assertEqual(sleep.call_count, 3)

    @mock.patch("common.db_retry.time.sleep")
    def test_backoff_grows(self, sleep):
        def fn():
            raise _locked()

        with self.assertRaises(OperationalError):
            db_retry.retry_on_db_lock(fn, retries=3, delay=0.1)
        waits = [c.args[0] for c in sleep.call_args_list]
        self.assertEqual(len(waits), 3)
        self.assertTrue(waits[0] < waits[1] < waits[2], waits)

    @mock.patch("common.db_retry.time.sleep")
    def test_real_error_is_not_retried(self, sleep):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise OperationalError("no such table: x")

        with self.assertRaises(OperationalError):
            db_retry.retry_on_db_lock(fn, retries=3)
        self.assertEqual(calls["n"], 1)
        sleep.assert_not_called()

    @mock.patch("common.db_retry.time.sleep")
    def test_best_effort_returns_default_after_giving_up(self, sleep):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            raise _locked()

        self.assertIsNone(db_retry.best_effort_write(fn, default=None))
        self.assertEqual(calls["n"], 1 + db_retry.DEFAULT_WRITE_RETRIES)

    @mock.patch("common.db_retry.time.sleep")
    def test_best_effort_still_raises_real_bugs(self, sleep):
        def fn():
            raise OperationalError("no such column: y")

        with self.assertRaises(OperationalError):
            db_retry.best_effort_write(fn, default=None)

    def test_best_effort_passes_the_value_through(self):
        self.assertEqual(db_retry.best_effort_write(lambda: 42, default=None), 42)


class SqliteSettingsTests(SimpleTestCase):
    def test_sqlite_dev_options(self):
        db = settings.DATABASES["default"]
        if "sqlite" not in db["ENGINE"]:
            self.skipTest("Postgres in use")
        import django

        self.assertLessEqual(db["OPTIONS"]["timeout"], 30)
        if django.VERSION >= (5, 1):
            self.assertEqual(db["OPTIONS"].get("transaction_mode"), "IMMEDIATE")
