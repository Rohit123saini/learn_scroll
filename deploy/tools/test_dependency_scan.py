"""X6b -- tests for the pure functions of deploy/tools/dependency_scan.py (no pip-audit, no network).

    python -m unittest deploy.tools.test_dependency_scan     (from backend/backend)   -- written by reading, not run.
"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dependency_scan as ds  # noqa: E402

TODAY = dt.date(2026, 10, 29)

NEW_SHAPE = json.dumps({"dependencies": [
    {"name": "django", "version": "5.0.1", "vulns": [
        {"id": "PYSEC-1", "aliases": ["CVE-2026-1"], "fix_versions": ["5.0.2"], "description": "x" * 500}]},
    {"name": "requests", "version": "2.31.0", "vulns": []},
    {"name": "pillow", "version": "10.0.0", "vulns": [{"id": "GHSA-aaaa-bbbb-cccc"}]},
]})
OLD_SHAPE = json.dumps([{"name": "django", "version": "5.0.1", "vulns": [{"id": "PYSEC-1"}]}])


class ParseTests(unittest.TestCase):
    def test_new_shape(self):
        v = ds.parse_pip_audit(NEW_SHAPE)
        self.assertEqual([x.package for x in v], ["django", "pillow"])
        self.assertEqual(v[0].aliases, ("CVE-2026-1",))
        self.assertEqual(v[0].fix_versions, ("5.0.2",))
        self.assertLessEqual(len(v[0].description), 200)

    def test_old_list_shape(self):
        self.assertEqual(len(ds.parse_pip_audit(OLD_SHAPE)), 1)

    def test_clean_report(self):
        self.assertEqual(ds.parse_pip_audit(json.dumps({"dependencies": []})), [])

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            ds.parse_pip_audit("not json")


class IgnoreFileTests(unittest.TestCase):
    def test_parse(self):
        got = ds.parse_ignore_file("# c\n\nPYSEC-1  2026-12-31  no fix yet\nGHSA-x\nCVE-2  oops no date\n")
        self.assertEqual(got[0].vuln_id, "PYSEC-1")
        self.assertEqual(got[0].expires, dt.date(2026, 12, 31))
        self.assertEqual(got[0].reason, "no fix yet")
        self.assertIsNone(got[1].expires)
        self.assertIsNone(got[2].expires)

    def test_active_until_and_including_expiry_day(self):
        ig = ds.Ignore("A", dt.date(2026, 10, 29))
        self.assertTrue(ig.active(dt.date(2026, 10, 29)))
        self.assertFalse(ig.active(dt.date(2026, 10, 30)))


class ApplyIgnoresTests(unittest.TestCase):
    def setUp(self):
        self.vulns = ds.parse_pip_audit(NEW_SHAPE)

    def test_no_ignores_everything_open(self):
        r = ds.apply_ignores(self.vulns, [], TODAY)
        self.assertEqual(len(r.open), 2)
        self.assertEqual(ds.exit_code(r), 1)

    def test_ignore_by_alias_matches_case_insensitively(self):
        r = ds.apply_ignores(self.vulns, [ds.Ignore("cve-2026-1", dt.date(2027, 1, 1))], TODAY)
        self.assertEqual([v.package for v, _ in r.ignored], ["django"])
        self.assertEqual([v.package for v in r.open], ["pillow"])

    def test_expired_ignore_counts_as_open_again(self):
        r = ds.apply_ignores(self.vulns, [ds.Ignore("PYSEC-1", dt.date(2026, 1, 1))], TODAY)
        self.assertEqual(len(r.expired), 1)
        self.assertIn("django", [v.package for v in r.open])

    def test_all_ignored_exits_zero(self):
        r = ds.apply_ignores(self.vulns, [ds.Ignore("PYSEC-1", None), ds.Ignore("GHSA-aaaa-bbbb-cccc", None)], TODAY)
        self.assertEqual(r.open, [])
        self.assertEqual(ds.exit_code(r), 0)


class ExitCodeTests(unittest.TestCase):
    def test_fail_on_modes(self):
        fixable = ds.Result(open=[ds.Vuln("a", "1", "X", fix_versions=("2",))])
        unfixable = ds.Result(open=[ds.Vuln("a", "1", "X")])
        self.assertEqual(ds.exit_code(fixable, "none"), 0)
        self.assertEqual(ds.exit_code(fixable, "fixable"), 1)
        self.assertEqual(ds.exit_code(unfixable, "fixable"), 0)
        self.assertEqual(ds.exit_code(unfixable, "any"), 1)

    def test_render_mentions_open_packages(self):
        r = ds.apply_ignores(ds.parse_pip_audit(NEW_SHAPE), [], TODAY)
        self.assertIn("django", ds.render(r))


class SaveJsonTests(unittest.TestCase):  # P-B7
    def test_save_json_copies_the_raw_report_and_exit_code_still_reflects_findings(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "in.json", Path(d) / "out.json"
            src.write_text(NEW_SHAPE, encoding="utf-8")
            code = ds.main(["--from-json", str(src), "--save-json", str(out), "--ignore-file", str(Path(d) / "none.txt")])
            self.assertEqual(code, 1)
            self.assertEqual(out.read_text(encoding="utf-8"), NEW_SHAPE)


if __name__ == "__main__":
    unittest.main()
