"""T3.7 tests: probe endpoints, backup-freshness logic, management commands."""
import json
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core import ops_health


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(root: Path, rel: str, data: dict):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))


class ProbeEndpointTests(TestCase):
    def test_healthz_is_unauthenticated_and_ok(self):
        r = self.client.get("/healthz/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_readyz_ok_and_leaks_no_details(self):
        r = self.client.get("/readyz/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["checks"], {"database": "ok", "cache": "ok"})

    def test_readyz_503_when_db_down_without_exception_text(self):
        with mock.patch.object(ops_health, "check_database", return_value=(False, "database unreachable")):
            # views call the module-level function by name, so patching the attribute is enough
            r = self.client.get("/readyz/")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["checks"]["database"], "error")

    @override_settings(HEALTHCHECK_TOKEN="")
    def test_deep_is_404_when_token_unset(self):
        self.assertEqual(self.client.get("/healthz/deep/").status_code, 404)

    @override_settings(HEALTHCHECK_TOKEN="s3cret")
    def test_deep_rejects_wrong_token_and_accepts_right_one(self):
        self.assertEqual(self.client.get("/healthz/deep/?token=nope").status_code, 404)
        with mock.patch.object(ops_health, "check_celery", return_value=(True, "1 worker(s) online")), \
                mock.patch.object(ops_health, "check_backups", return_value=(True, "ok")), \
                mock.patch.object(ops_health, "check_disk", return_value=(True, "ok")):  # disk % free of the dev machine must not matter
            r = self.client.get("/healthz/deep/", HTTP_X_HEALTH_TOKEN="s3cret")
        self.assertEqual(r.status_code, 200)
        self.assertIn("backups", r.json()["checks"])


class BackupFreshnessTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 13, 12, 0, tzinfo=dt_timezone.utc)

    def _healthy_markers(self):
        recent = _iso(self.now - timedelta(hours=3))
        _write(self.root, "postgres/LATEST.json", {"status": "ok", "finished_at": recent, "remote_uploaded": True})
        _write(self.root, "media/LATEST.json", {"status": "ok", "finished_at": recent})
        _write(self.root, "drills/LAST_DRILL.json", {"status": "ok", "finished_at": _iso(self.now - timedelta(days=2))})

    def test_healthy(self):
        self._healthy_markers()
        with override_settings(BACKUP_DIR=str(self.root)), mock.patch.dict("os.environ", {"BACKUP_DIR": str(self.root)}):
            ok, detail = ops_health.check_backups(now=self.now)
        self.assertTrue(ok, detail)

    def test_missing_markers_reported(self):
        with mock.patch.dict("os.environ", {"BACKUP_DIR": str(self.root)}):
            ok, detail = ops_health.check_backups(now=self.now)
        self.assertFalse(ok)
        self.assertIn("postgres backup", detail)
        self.assertIn("restore drill", detail)

    def test_stale_backup_and_failed_drill(self):
        self._healthy_markers()
        _write(self.root, "postgres/LATEST.json",
               {"status": "ok", "finished_at": _iso(self.now - timedelta(hours=40)), "remote_uploaded": True})
        _write(self.root, "drills/LAST_DRILL.json",
               {"status": "failed", "finished_at": _iso(self.now - timedelta(days=1))})
        with mock.patch.dict("os.environ", {"BACKUP_DIR": str(self.root)}):
            ok, detail = ops_health.check_backups(now=self.now)
        self.assertFalse(ok)
        self.assertIn("40.0h ago", detail)
        self.assertIn("status='failed'", detail)

    def test_not_uploaded_offsite_is_a_problem(self):
        self._healthy_markers()
        _write(self.root, "postgres/LATEST.json",
               {"status": "ok", "finished_at": _iso(self.now - timedelta(hours=1)), "remote_uploaded": False})
        with mock.patch.dict("os.environ", {"BACKUP_DIR": str(self.root)}):
            ok, detail = ops_health.check_backups(now=self.now)
        self.assertFalse(ok)
        self.assertIn("off-site", detail)

    def test_command_exits_nonzero_when_unhealthy(self):
        with mock.patch.dict("os.environ", {"BACKUP_DIR": str(self.root)}):
            with self.assertRaises(CommandError):
                call_command("check_backups")


class SystemHealthCommandTests(TestCase):
    def test_healthy_run(self):
        with mock.patch.object(ops_health, "check_disk", return_value=(True, "ok")):
            call_command("check_system_health", "--skip-celery", "--skip-backups")

    def test_failure_raises_and_alert_logs_error(self):
        with mock.patch.object(ops_health, "check_cache", return_value=(False, "cache unreachable")), \
                mock.patch.object(ops_health, "check_disk", return_value=(True, "ok")):
            with self.assertLogs("core.management.commands.check_system_health", level="ERROR"):
                with self.assertRaises(CommandError):
                    call_command("check_system_health", "--alert", "--skip-celery", "--skip-backups")
