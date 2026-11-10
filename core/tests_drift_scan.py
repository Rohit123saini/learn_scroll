"""Pure tests for core/drift_scan.py (no Django, no DB):
    python manage.py test core.tests_drift_scan     or     python -m unittest core.tests_drift_scan
"""
import tempfile
import textwrap
import unittest
from pathlib import Path

from core import drift_scan as ds


def write(root: Path, rel: str, body: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class SettingsSourceTests(unittest.TestCase):
    def scan(self, body):
        with tempfile.TemporaryDirectory() as d:
            return ds.scan_settings_source(write(Path(d), "settings.py", body))

    def test_unconditional_wildcard_is_error(self):
        f = self.scan("import os\nALLOWED_HOSTS = ['*']\n")
        self.assertEqual([(x.kind, x.severity) for x in f], [("allowed_hosts_wildcard_source", "error")])

    def test_wildcard_overridden_by_later_env_assignment_is_ok(self):
        self.assertEqual(self.scan("ALLOWED_HOSTS = ['*']\nALLOWED_HOSTS = ['a.com']\n"), [])

    def test_env_driven_with_debug_only_wildcard_is_ok(self):
        body = """
            import os
            DEBUG = os.getenv("DEBUG", "False") == "True"
            ALLOWED_HOSTS = [h for h in os.getenv("ALLOWED_HOSTS", "").split(",") if h]
            if DEBUG and not os.getenv("ALLOWED_HOSTS"):
                ALLOWED_HOSTS = ["*"]
        """
        self.assertEqual(self.scan(body), [])

    def test_hardcoded_debug_true(self):
        self.assertEqual([x.kind for x in self.scan("DEBUG = True\n")], ["debug_hardcoded_true"])

    def test_truthy_debug_default(self):
        for default in ("True", "1", "yes", "on"):
            f = self.scan(f'import os\nDEBUG = os.getenv("DEBUG", "{default}") == "True"\n')
            self.assertEqual([x.kind for x in f], ["debug_unsafe_default"], default)

    def test_false_default_is_fine(self):
        self.assertEqual(self.scan('import os\nDEBUG = os.getenv("DEBUG", "False") == "True"\n'), [])

    def test_broken_file_does_not_raise(self):
        self.assertEqual(self.scan("def (:\n"), [])


class StaleCommentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        write(self.root, "core/models.py", """
            class NotifType:
                TESTSERIES_QUERY_ANSWERED = "x"
        """)

    def tearDown(self):
        self._tmp.cleanup()

    def kinds(self):
        return [(f.kind, f.key) for f in ds.scan_stale_comments(self.root)]

    def test_symbol_claimed_missing_but_defined(self):
        write(self.root, "message/services.py", """
            # GAP: `core.models.Notification.NotifType.TESTSERIES_QUERY_ANSWERED`
            # is NOT confirmed to exist yet.
            x = 1
        """)
        self.assertEqual(self.kinds(), [("stale_comment_symbol", "symbol:TESTSERIES_QUERY_ANSWERED")])

    def test_docstring_form_also_detected(self):
        write(self.root, "a/bridge.py", '''
            """`TESTSERIES_QUERY_ANSWERED`, which is NOT confirmed to exist."""
        ''')
        self.assertEqual(len(self.kinds()), 1)

    def test_truly_missing_symbol_is_not_flagged(self):
        write(self.root, "a/x.py", "# `SOME_FUTURE_THING` is NOT confirmed to exist yet.\n")
        self.assertEqual(self.kinds(), [])

    def test_loose_phrases_and_generic_names_ignored(self):
        write(self.root, "a/x.py", "# `conversation_id` is missing here\n# `user` doesn't exist yet\n")
        self.assertEqual(self.kinds(), [])

    def test_ignore_marker(self):
        write(self.root, "a/x.py", "# `TESTSERIES_QUERY_ANSWERED` is NOT confirmed to exist (drift: ignore)\n")
        self.assertEqual(self.kinds(), [])

    def test_dangling_path(self):
        write(self.root, "a/x.py", "# auth is wired via message/Middleware.py\n")
        self.assertEqual(self.kinds(), [("stale_comment_path", "path:message/Middleware.py")])

    def test_existing_path_ok_and_removal_context_ok(self):
        write(self.root, "a/y.py", "x = 1\n")
        write(self.root, "a/x.py", "# helper lives in a/y.py\n# message/Old.py was deleted last release\n")
        self.assertEqual(self.kinds(), [])

    def test_placeholder_paths_ignored(self):
        write(self.root, "a/x.py", "# migration campus/migrations/0xxx_....py and app/sub/file.py\n")
        self.assertEqual(self.kinds(), [])

    def test_skips_migrations_dir(self):
        write(self.root, "a/migrations/0001.py", "# `TESTSERIES_QUERY_ANSWERED` is NOT confirmed to exist\n")
        self.assertEqual(self.kinds(), [])


class BaselineTests(unittest.TestCase):
    def test_warning_baselined_error_never(self):
        w = ds.Finding(kind="stale_comment_path", severity="warning", message="m", file="a.py", key="k")
        e = ds.Finding(kind="allowed_hosts_wildcard", severity="error", message="m", key="k")
        new, old = ds.apply_baseline([w, e], [w.fingerprint, e.fingerprint])
        self.assertEqual(new, [e])
        self.assertEqual(old, [w])

    def test_fingerprint_ignores_line_numbers(self):
        a = ds.Finding(kind="k", severity="warning", message="m", file="f.py", line=1, key="x")
        b = ds.Finding(kind="k", severity="warning", message="m", file="f.py", line=99, key="x")
        self.assertEqual(a.fingerprint, b.fingerprint)


if __name__ == "__main__":
    unittest.main()
