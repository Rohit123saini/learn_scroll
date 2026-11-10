"""P-C1 -- PostgreSQL is compulsory in production. Written by reading the code only - NOT run.

    python manage.py test core.tests_pc1_postgres_required
    python -m unittest core.tests_pc1_postgres_required.CheckerRuleTests     # pure rules, no Django / DB

settings.py is evaluated once per process, so the boot rules are tested the honest way: a fresh `python -c` process loads
the settings with a chosen environment and we look at what it says (nothing connects to a database).
`DATABASE_URL=""` (set but empty) is used for "unset" so a developer's local .env cannot fill it in (load_dotenv never
overrides an existing variable).
"""
import os
import pathlib
import subprocess
import sys
import unittest

from core.release_check import ERROR, check_env
from core.security_settings_check import CSRF_MW, SECURITY_MW, check_settings

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
PG_URL = "postgres://ls:ls@127.0.0.1:5432/ls"
PROBE = (
    "import os; os.environ['DJANGO_SETTINGS_MODULE']='LearnScroll.settings'; "
    "from django.conf import settings; print('ENGINE=' + settings.DATABASES['default']['ENGINE'])"
)


def load_settings(argv_tail=None, **env):
    """Run a throw-away interpreter that imports settings. `argv_tail` becomes sys.argv[1:] (e.g. ['collectstatic'])."""
    base = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DEBUG", "DJANGO_ENV")}
    base.update(
        SECRET_KEY="ci-only-Zp2vQ7xLm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd",
        JWT_SIGNING_KEY="ci-only-jwt-key-Lm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd0Ey",
        ALLOWED_HOSTS="ci.example.com",
        LIVEKIT_WS_URL="",
    )
    base.update(env)
    code = PROBE if not argv_tail else f"import sys; sys.argv=['manage.py']+{list(argv_tail)!r}; " + PROBE
    return subprocess.run([sys.executable, "-c", code], cwd=str(BASE_DIR), env=base, capture_output=True, text=True, timeout=120)


class SettingsBootTests(unittest.TestCase):
    def test_production_without_database_url_refuses_to_boot(self):
        for empty in ("",):  # unset == empty after strip()
            with self.subTest(value=repr(empty)):
                r = load_settings(DEBUG="False", DATABASE_URL=empty)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("ImproperlyConfigured", r.stderr)
                self.assertIn("DATABASE_URL is not set", r.stderr)

    def test_whitespace_only_database_url_counts_as_unset(self):
        r = load_settings(DEBUG="False", DATABASE_URL="   ")
        self.assertIn("DATABASE_URL is not set", r.stderr)

    def test_production_with_postgres_url_boots_on_postgres(self):
        for url in (PG_URL, "postgresql://ls:ls@db.internal:5432/ls"):
            with self.subTest(url=url):
                r = load_settings(DEBUG="False", DATABASE_URL=url)
                self.assertNotIn("DATABASE_URL", r.stderr, r.stderr[-600:])
                self.assertIn("ENGINE=django.db.backends.postgresql", r.stdout, r.stderr[-600:])

    def test_non_postgres_url_is_refused_and_never_echoed(self):
        for url in ("sqlite:///db.sqlite3", "mysql://u:secretpw@h:3306/d"):
            for debug in ("False", "True"):
                with self.subTest(url=url.split(":")[0], debug=debug):
                    r = load_settings(DEBUG=debug, DATABASE_URL=url)
                    self.assertNotEqual(r.returncode, 0)
                    self.assertIn("must be a PostgreSQL URL", r.stderr)
                    self.assertNotIn("secretpw", r.stderr + r.stdout)  # the URL holds the password: never printed

    def test_local_dev_without_url_still_uses_sqlite(self):
        r = load_settings(DEBUG="True", DATABASE_URL="")
        self.assertNotIn("DATABASE_URL is not set", r.stderr)
        self.assertIn("ENGINE=django.db.backends.sqlite3", r.stdout, r.stderr[-600:])

    def test_test_runner_and_collectstatic_are_exempt(self):
        for cmd in (["test"], ["collectstatic", "--noinput"]):
            with self.subTest(cmd=cmd[0]):
                r = load_settings(argv_tail=cmd, DEBUG="False", DATABASE_URL="")
                self.assertNotIn("DATABASE_URL is not set", r.stderr, r.stderr[-600:])

    def test_other_commands_are_not_exempt(self):
        for cmd in (["migrate"], ["runserver"], ["shell"], ["check", "--deploy"]):
            with self.subTest(cmd=cmd[0]):
                r = load_settings(argv_tail=cmd, DEBUG="False", DATABASE_URL="")
                self.assertIn("DATABASE_URL is not set", r.stderr)


# ---------------------------------------------------------------------------------------------------------------------
GOOD_SETTINGS = {
    "DEBUG": False,
    "ALLOWED_HOSTS": ["api.learnscroll.app"],
    "CSRF_TRUSTED_ORIGINS": ["https://api.learnscroll.app"],
    "CSRF_COOKIE_SECURE": True,
    "SESSION_COOKIE_SECURE": True,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "CSRF_COOKIE_SAMESITE": "Lax",
    "SECURE_SSL_REDIRECT": True,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_HSTS_SECONDS": 60 * 60 * 24 * 30,
    "SECURE_CONTENT_TYPE_NOSNIFF": True,
    "SECURE_REFERRER_POLICY": "same-origin",
    "X_FRAME_OPTIONS": "DENY",
    "CORS_ALLOW_ALL_ORIGINS": False,
    "CORS_ALLOWED_ORIGINS": [],
    "MIDDLEWARE": [
        "corsheaders.middleware.CorsMiddleware",
        SECURITY_MW,
        CSRF_MW,
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    "REST_FRAMEWORK": {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]},
}


def _keys(findings, level=None):
    return {f.key for f in findings if level is None or f.level == level}


class CheckerRuleTests(unittest.TestCase):
    PG = {"default": {"ENGINE": "django.db.backends.postgresql"}}
    LITE = {"default": {"ENGINE": "django.db.backends.sqlite3"}}

    def test_sqlite_engine_is_an_error_in_production(self):
        self.assertIn("DATABASES", _keys(check_settings({**GOOD_SETTINGS, "DATABASES": self.LITE}), ERROR))

    def test_postgres_engine_is_clean(self):
        self.assertNotIn("DATABASES", _keys(check_settings({**GOOD_SETTINGS, "DATABASES": self.PG})))

    def test_a_second_sqlite_alias_is_caught_too(self):
        dbs = {**self.PG, "scratch": {"ENGINE": "django.db.backends.sqlite3"}}
        self.assertIn("DATABASES", _keys(check_settings({**GOOD_SETTINGS, "DATABASES": dbs}), ERROR))

    def test_sqlite_is_fine_on_a_dev_laptop(self):
        cfg = {**GOOD_SETTINGS, "DEBUG": True, "DATABASES": self.LITE}
        self.assertNotIn("DATABASES", _keys(check_settings(cfg)))

    def test_snapshot_without_databases_is_not_judged(self):
        self.assertNotIn("DATABASES", _keys(check_settings(dict(GOOD_SETTINGS))))

    def test_env_file_without_or_with_non_postgres_database_url_is_an_error(self):
        from core.tests_release_check import GOOD as GOOD_ENV
        for patch in ({"DATABASE_URL": ""}, {"DATABASE_URL": "sqlite:///db.sqlite3"}, {"DATABASE_URL": "mysql://u:p@h/d"}):
            with self.subTest(patch=patch):
                env = {**GOOD_ENV, **patch}
                self.assertIn("DATABASE_URL", _keys(check_env(env, file_exists=lambda p: True), ERROR))
        env = dict(GOOD_ENV)
        env.pop("DATABASE_URL")
        self.assertIn("DATABASE_URL", _keys(check_env(env, file_exists=lambda p: True), ERROR))


class CiScriptsStillLoadProductionSettingsTests(unittest.TestCase):
    """The two CI scripts load settings with DEBUG=False; without a Postgres URL they would now crash at import."""

    def test_scripts_export_a_throwaway_postgres_url(self):
        for name in ("ci_release_gate.sh", "ci_config_drift.sh"):
            with self.subTest(script=name):
                text = (BASE_DIR / "scripts" / name).read_text(encoding="utf-8")
                self.assertIn("export DATABASE_URL=", text)
                self.assertIn("postgres://", text)
                self.assertNotIn("unset DATABASE_URL", text)

    def test_env_example_documents_the_requirement(self):
        text = (BASE_DIR / "deploy" / "env.production.example").read_text(encoding="utf-8")
        self.assertIn("REFUSES TO BOOT", text)
        self.assertIn("DATABASE_URL=postgres://", text)


if __name__ == "__main__":
    unittest.main()
