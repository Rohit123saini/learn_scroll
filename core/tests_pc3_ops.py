"""P-C3 -- tests for Sentry sampling env, Celery ops (retry base, failure alert, single-beat guard), release_check rules, settings profiles.
Written by reading + a sandbox run of the pure parts (no Django / Celery there):

    python -m unittest core.tests_pc3_ops                    # pure rules always run; celery / django ones skip if not installed
    python manage.py test core.tests_pc3_ops                 # everything
"""
import importlib.util
import os
import pathlib
import subprocess
import sys
import unittest

from LearnScroll import beat_guard, celery_ops
from LearnScroll.ops_settings import env_bool, env_float, env_int
from core.release_check import ERROR, WARN, check_env
from core.tests_release_check import GOOD

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
HAVE_CELERY = importlib.util.find_spec("celery") is not None
HAVE_DJANGO = importlib.util.find_spec("django") is not None


class OpsSettingsTests(unittest.TestCase):
    def test_float_falls_back_on_garbage_and_clamps(self):
        self.assertEqual(env_float("X", 0.1, env={"X": "O.1"}), 0.1)
        self.assertEqual(env_float("X", 0.1, env={"X": "nan"}), 0.1)
        self.assertEqual(env_float("X", 0.1, lo=0, hi=1, env={"X": "7"}), 1.0)
        self.assertEqual(env_float("X", 0.1, lo=0, hi=1, env={"X": "-3"}), 0.0)
        self.assertEqual(env_float("X", 0.1, env={}), 0.1)

    def test_int_and_bool(self):
        self.assertEqual(env_int("X", 60, lo=0, env={"X": "abc"}), 60)
        self.assertEqual(env_int("X", 60, lo=0, env={"X": "-5"}), 0)
        self.assertEqual(env_int("X", 60, env={"X": " 15 "}), 15)
        self.assertTrue(env_bool("X", False, env={"X": "True"}))
        self.assertFalse(env_bool("X", True, env={"X": "no"}))
        self.assertTrue(env_bool("X", True, env={"X": ""}))


class FakeRedis:
    def __init__(self):
        self.d = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    def get(self, k):
        return self.d.get(k)

    def expire(self, k, t):
        pass

    def delete(self, k):
        self.d.pop(k, None)


class BeatGuardTests(unittest.TestCase):
    def test_first_beat_runs_second_exits_with_code_3(self):
        r, codes = FakeRedis(), []
        self.assertIsNotNone(beat_guard.start_guard(r, exit_fn=codes.append, start_thread=False))
        self.assertEqual(codes, [])
        beat_guard.start_guard(r, exit_fn=codes.append, start_thread=False)
        self.assertEqual(codes, [3])

    def test_renew_only_while_we_own_the_lock(self):
        r = FakeRedis()
        mine, other = beat_guard.BeatLock(r, token="a"), beat_guard.BeatLock(r, token="b")
        self.assertTrue(mine.acquire())
        self.assertFalse(other.acquire())
        self.assertTrue(mine.renew())
        self.assertFalse(other.renew())
        mine.release()
        self.assertTrue(other.acquire())

    def test_expired_lock_is_retaken_by_renew(self):
        r = FakeRedis()
        lock = beat_guard.BeatLock(r, token="a")
        self.assertTrue(lock.renew())  # key missing -> taken

    def test_redis_down_is_fail_open(self):
        class Boom(FakeRedis):
            def set(self, *a, **k):
                raise ConnectionError("down")
        codes = []
        self.assertIsNone(beat_guard.start_guard(Boom(), exit_fn=codes.append, start_thread=False))
        self.assertEqual(codes, [])


class FakeCache:
    def __init__(self):
        self.d = {}

    def add(self, k, v, timeout=None):
        self.d.setdefault(k, v)

    def incr(self, k):
        self.d[k] += 1
        return self.d[k]


class CeleryOpsTests(unittest.TestCase):
    def test_alert_fires_exactly_once_at_threshold(self):
        calls = []
        orig = celery_ops.send_alert
        celery_ops.send_alert = lambda *a, **k: calls.append(a[:2])
        try:
            cache = FakeCache()
            counts = [celery_ops.record_failure("t.x", "id", ValueError("secret"), cache=cache, now=1000) for _ in range(7)]
        finally:
            celery_ops.send_alert = orig
        self.assertEqual(counts, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(calls, [("t.x", 5)])

    def test_new_window_counts_again_and_tasks_are_separate(self):
        cache = FakeCache()
        a = celery_ops.record_failure("t.a", None, ValueError(), cache=cache, now=0)
        b = celery_ops.record_failure("t.b", None, ValueError(), cache=cache, now=0)
        c = celery_ops.record_failure("t.a", None, ValueError(), cache=cache, now=10_000)
        self.assertEqual((a, b, c), (1, 1, 1))

    def test_cache_down_never_raises(self):
        class Dead:
            def add(self, *a, **k):
                raise RuntimeError("redis down")
        self.assertIsNone(celery_ops.record_failure("t.x", None, ValueError(), cache=Dead()))

    def test_exception_text_is_not_logged(self):  # exceptions can carry user data
        with self.assertLogs("celery.ops", level="ERROR") as cm:
            celery_ops.record_failure("t.x", "1", ValueError("phone 9999999999"), cache=FakeCache())
        self.assertNotIn("9999999999", "\n".join(cm.output))


@unittest.skipUnless(HAVE_CELERY, "celery not installed")
class RetryingTaskTests(unittest.TestCase):
    def test_retry_only_transient_errors(self):
        from LearnScroll.celery_base import RetryingTask
        self.assertIn(ConnectionError, RetryingTask.autoretry_for)
        self.assertIn(TimeoutError, RetryingTask.autoretry_for)
        self.assertNotIn(ValueError, RetryingTask.autoretry_for)
        self.assertNotIn(KeyError, RetryingTask.autoretry_for)
        self.assertEqual(RetryingTask.max_retries, 3)
        self.assertTrue(RetryingTask.retry_jitter)

    def test_shared_task_inherits_the_policy(self):
        from celery import Celery
        app = Celery("t", task_cls="LearnScroll.celery_base:RetryingTask")

        @app.task
        def sample():
            return 1
        self.assertIn(ConnectionError, sample.autoretry_for)
        self.assertEqual(sample.retry_backoff_max, 600)


def _env(**over):
    e = dict(GOOD)
    e.update(over)
    return e


def _keys(findings, level):
    return {f.key for f in findings if f.level == level}


class ReleaseCheckRuleTests(unittest.TestCase):
    def test_sentry_rate(self):
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", _keys(check_env(_env(SENTRY_TRACES_SAMPLE_RATE="O.1")), ERROR))
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", _keys(check_env(_env(SENTRY_TRACES_SAMPLE_RATE="2")), ERROR))
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", _keys(check_env(_env(SENTRY_TRACES_SAMPLE_RATE="1.0")), WARN))
        f = check_env(_env(SENTRY_TRACES_SAMPLE_RATE="0.1"))
        self.assertNotIn("SENTRY_TRACES_SAMPLE_RATE", _keys(f, ERROR) | _keys(f, WARN))

    def test_whole_number_settings(self):
        self.assertIn("DB_CONN_MAX_AGE", _keys(check_env(_env(DB_CONN_MAX_AGE="sixty")), ERROR))
        self.assertIn("CELERY_TASK_TIME_LIMIT", _keys(check_env(_env(CELERY_TASK_TIME_LIMIT="-1")), ERROR))
        self.assertNotIn("DB_CONN_MAX_AGE", _keys(check_env(_env(DB_CONN_MAX_AGE="60")), ERROR))

    def test_pgbouncer_port_without_cursor_flag_warns(self):
        url = "postgres://u:p@127.0.0.1:6432/db"
        self.assertIn("DB_DISABLE_SERVER_SIDE_CURSORS", _keys(check_env(_env(DATABASE_URL=url)), WARN))
        self.assertNotIn("DB_DISABLE_SERVER_SIDE_CURSORS",
                         _keys(check_env(_env(DATABASE_URL=url, DB_DISABLE_SERVER_SIDE_CURSORS="true")), WARN))

    def test_dev_settings_module_and_beat_guard_off(self):
        self.assertIn("DJANGO_SETTINGS_MODULE", _keys(check_env(_env(DJANGO_SETTINGS_MODULE="LearnScroll.settings_dev")), ERROR))
        self.assertIn("BEAT_SINGLE_INSTANCE_GUARD", _keys(check_env(_env(BEAT_SINGLE_INSTANCE_GUARD="false")), WARN))
        self.assertNotIn("BEAT_SINGLE_INSTANCE_GUARD", _keys(check_env(_env(BEAT_SINGLE_INSTANCE_GUARD="")), WARN))


def _probe(module, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DEBUG", "DJANGO_ENV", "CELERY_TASK_ALWAYS_EAGER")}
    base.update(SECRET_KEY="ci-only-Zp2vQ7xLm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd",
                JWT_SIGNING_KEY="ci-only-jwt-key-Lm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd0Ey",
                ALLOWED_HOSTS="ci.example.com", LIVEKIT_WS_URL="", SENTRY_DSN="")
    base.update(env)
    code = (f"import os; os.environ['DJANGO_SETTINGS_MODULE']={module!r}; from django.conf import settings; "
            "print('DEBUG=%s EAGER=%s BEAT=%s' % (settings.DEBUG, settings.CELERY_TASK_ALWAYS_EAGER, settings.BEAT_SINGLE_INSTANCE_GUARD))")
    return subprocess.run([sys.executable, "-c", code], cwd=str(BASE_DIR), env=base, capture_output=True, text=True, timeout=120)


@unittest.skipUnless(HAVE_DJANGO, "django not installed")
class SettingsProfileTests(unittest.TestCase):
    PG = "postgres://ls:ls@127.0.0.1:5432/ls"

    def test_dev_profile_defaults(self):
        r = _probe("LearnScroll.settings_dev")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertIn("DEBUG=True EAGER=True BEAT=False", r.stdout)

    def test_dev_profile_env_still_wins(self):
        r = _probe("LearnScroll.settings_dev", DEBUG="False", DATABASE_URL=self.PG, REDIS_URL="redis://127.0.0.1:6379/0")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertIn("DEBUG=False", r.stdout)

    def test_prod_profile_refuses_debug_and_eager(self):
        for bad in ({"DEBUG": "True"}, {"CELERY_TASK_ALWAYS_EAGER": "true"}):
            r = _probe("LearnScroll.settings_prod", DATABASE_URL=self.PG, REDIS_URL="redis://127.0.0.1:6379/0", **bad)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ImproperlyConfigured", r.stderr)

    def test_prod_profile_boots_with_prod_env(self):
        r = _probe("LearnScroll.settings_prod", DATABASE_URL=self.PG, REDIS_URL="redis://127.0.0.1:6379/0")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])
        self.assertIn("DEBUG=False EAGER=False BEAT=True", r.stdout)


if __name__ == "__main__":
    unittest.main()
