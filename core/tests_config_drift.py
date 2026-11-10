"""Integration tests for `manage.py check_config_drift` project-level checks (T3.5)."""
import json
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

QUIET = dict(CONFIG_DRIFT_APPS=[])  # skip per-app checks; we test the project-level ones


def run(**opts):
    out = StringIO()
    opts.setdefault("skip_stale_comments", True)
    call_command("check_config_drift", stdout=out, **opts)
    return out.getvalue()


@override_settings(**QUIET)
class AllowedHostsTests(SimpleTestCase):
    @override_settings(DEBUG=False, ALLOWED_HOSTS=["*"])
    def test_wildcard_in_prod_is_error_and_fails_strict(self):
        with self.assertRaises(CommandError):
            run(strict=True, skip_security=False)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["api.example.com"])
    def test_explicit_hosts_pass(self):
        out = run(strict=True, fail_on="error")
        self.assertNotIn("wildcard", out.lower().replace("no config drift", ""))

    @override_settings(DEBUG=True, ALLOWED_HOSTS=["*"])
    def test_wildcard_with_debug_is_only_a_warning(self):
        run(strict=True, fail_on="error")  # must not raise
        with self.assertRaises(CommandError):
            run(strict=True, fail_on="error", production=True)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=[])
    def test_empty_hosts_in_prod_warns(self):
        out = run()
        self.assertIn("ALLOWED_HOSTS is empty", out)


@override_settings(**QUIET)
class DebugTests(SimpleTestCase):
    @override_settings(DEBUG=True, ALLOWED_HOSTS=["a.com"])
    def test_debug_warning_vs_production_error(self):
        out = run(format="json")
        data = json.loads(out)
        self.assertEqual((data["errors"], data["warnings"]), (0, 1))
        data = json.loads(run(format="json", production=True))
        self.assertEqual(data["errors"], 1)

    @override_settings(DEBUG=False, ALLOWED_HOSTS=["a.com"])
    def test_debug_off_is_clean(self):
        data = json.loads(run(format="json"))
        self.assertEqual(data["findings"], [])


@override_settings(**QUIET, DEBUG=False, ALLOWED_HOSTS=["a.com"])
class RealSettingsSourceTests(SimpleTestCase):
    def test_project_settings_source_has_no_unconditional_wildcard(self):
        """The R10 regression guard: LearnScroll/settings.py must never again assign
        ALLOWED_HOSTS = ['*'] at module level."""
        from importlib.util import find_spec
        from pathlib import Path

        from core.drift_scan import scan_settings_source

        origin = Path(find_spec("LearnScroll.settings").origin)
        self.assertEqual([f for f in scan_settings_source(origin) if f.severity == "error"], [])
