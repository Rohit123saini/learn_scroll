"""PC2: static checks of the backup / restore-drill tooling (no Postgres, no shell execution needed)."""
import shutil
import subprocess
from pathlib import Path

from django.test import SimpleTestCase

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
BACKUP = DEPLOY / "backup"


def _t(p):
    return Path(p).read_text()


class ScriptSafetyTests(SimpleTestCase):
    def test_all_scripts_pass_bash_syntax_check(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        for f in sorted(BACKUP.glob("*.sh")):
            r = subprocess.run([bash, "-n", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{f.name}: {r.stderr}")

    def test_drill_refuses_unsafe_scratch_db_names(self):
        s = _t(BACKUP / "restore_drill.sh")
        for needle in ("valid_dbname", "reserved_dbname", "*restore_drill*", "equals the LIVE database", "DRILL_DB_SAFE"):
            self.assertIn(needle, s)
        # cleanup may only DROP after the name checks passed
        self.assertIn("(( DRILL_DB_SAFE )) &&", s)

    def test_drill_marks_failed_when_it_dies_midway_and_checks_dump_age(self):
        s = _t(BACKUP / "restore_drill.sh")
        self.assertIn("DRILL_RECORDED", s)
        self.assertIn("aborted before completion", s)
        self.assertIn("DRILL_MAX_DUMP_AGE_HOURS", s)
        self.assertNotIn("need createdb", s)

    def test_restore_requires_confirmation_and_refuses_live_db(self):
        s = _t(BACKUP / "restore_postgres.sh")
        for needle in ("Type the new database name", "--dry-run", "is the LIVE database", "reserved_dbname",
                       "already exists", "--role=", "DROP DATABASE IF EXISTS"):
            self.assertIn(needle, s)

    def test_restore_swap_steps_use_live_db_not_admin_db(self):
        s = _t(BACKUP / "restore_postgres.sh")
        self.assertIn('ALTER DATABASE "$LIVE_DB" RENAME TO', s)
        self.assertNotIn('ALTER DATABASE "$admin_db" RENAME', s)

    def test_failures_alert_through_exit_trap(self):
        lib = _t(BACKUP / "lib.sh")
        self.assertIn("trap _guard_on_exit EXIT", lib)
        self.assertNotIn(" ERR", lib.split("run_guarded()")[1])   # the old ERR trap never saw `die`
        self.assertIn("metrics_publish", lib)

    def test_backup_scripts_pass_metrics_job_and_clean_partials(self):
        self.assertIn("main postgres", _t(BACKUP / "backup_postgres.sh"))
        self.assertIn("main media", _t(BACKUP / "backup_media.sh"))
        self.assertIn("*.partial", _t(BACKUP / "backup_postgres.sh"))
        self.assertIn("shrunk", _t(BACKUP / "backup_postgres.sh"))


class AlertRulesAndDocsTests(SimpleTestCase):
    def test_prometheus_rules_parse_and_cover_failure_modes(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        d = yaml.safe_load(_t(DEPLOY / "monitoring" / "prometheus_backup_rules.yml"))
        names = {r["alert"] for g in d["groups"] for r in g["rules"]}
        for n in ("BackupPostgresLate", "BackupMediaLate", "BackupLastRunFailed", "BackupNotOffsite",
                  "BackupDumpShrunk", "RestoreDrillFailed", "RestoreDrillLate", "BackupMetricsMissing"):
            self.assertIn(n, names)
        for g in d["groups"]:
            for r in g["rules"]:
                self.assertIn("expr", r)
                self.assertIn("severity", r["labels"])

    def test_checklist_has_required_sections(self):
        s = _t(DEPLOY / "RESTORE_DRILL_CHECKLIST.md")
        for needle in ("## When an alert fires", "## Sign-off", "--dry-run", "--from-remote", "GRANT learnscroll"):
            self.assertIn(needle, s)

    def test_env_example_documents_new_keys(self):
        s = _t(DEPLOY / "env.production.example")
        for k in ("DRILL_DB=", "DRILL_MAX_DUMP_AGE_HOURS=", "NODE_EXPORTER_TEXTFILE_DIR="):
            self.assertIn(k, s)
