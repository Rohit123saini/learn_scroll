# campus/tests_report_rules.py — pure (no Django):  python -m unittest campus.tests_report_rules
import unittest
from datetime import date

from . import report_rules as r


class CatalogTests(unittest.TestCase):
    def test_every_report_has_a_group_and_a_title(self):
        for name in r.REPORTS:
            self.assertIn(r.group_of(name), ("attendance", "marks", "fees"))
            self.assertTrue(r.title_of(name))

    def test_unknown_report(self):
        with self.assertRaises(r.ReportRuleError):
            r.group_of("salary")

    def test_fees_reports_map_to_finance_names(self):
        self.assertEqual(set(r.FEES_FINANCE_NAME), set(r.FEES_REPORTS))


class AccessTests(unittest.TestCase):
    def test_attendance_and_marks_roles(self):
        for g in ("attendance", "marks"):
            self.assertTrue(r.can_export_group("admin", g))
            self.assertTrue(r.can_export_group("principal_hod", g))
            self.assertTrue(r.can_export_group("class_teacher", g))
            self.assertFalse(r.can_export_group("subject_teacher", g))
            self.assertFalse(r.can_export_group("non_teaching", g))

    def test_fees_is_accounts_side_only(self):
        self.assertTrue(r.can_export_group("non_teaching", "fees"))
        self.assertTrue(r.can_export_group("admin", "fees"))
        self.assertFalse(r.can_export_group("class_teacher", "fees"))
        self.assertFalse(r.can_export_group("student", "fees"))

    def test_whole_campus(self):
        self.assertTrue(r.sees_whole_campus("admin"))
        self.assertFalse(r.sees_whole_campus("class_teacher"))


class RangeTests(unittest.TestCase):
    today = date(2026, 10, 20)

    def test_default_is_last_30_days(self):
        self.assertEqual(r.resolve_range(None, None, self.today), (date(2026, 9, 20), self.today))

    def test_reversed(self):
        with self.assertRaises(r.ReportRuleError):
            r.resolve_range(date(2026, 10, 5), date(2026, 10, 1), self.today)

    def test_too_long(self):
        with self.assertRaises(r.ReportRuleError):
            r.resolve_range(date(2024, 1, 1), date(2026, 1, 1), self.today)

    def test_exactly_a_year_is_fine(self):
        r.resolve_range(date(2025, 10, 20), self.today, self.today)


class MathTests(unittest.TestCase):
    def test_percent(self):
        self.assertEqual(r.percent(19, 20), 95.0)
        self.assertEqual(r.percent(1, 3), 33.33)
        self.assertIsNone(r.percent(0, 0))
        self.assertIsNone(r.percent("x", 5))

    def test_rank_ties_skip(self):
        ranks = r.rank_by_score({"a": 90, "b": 90, "c": 80, "d": None})
        self.assertEqual(ranks, {"a": 1, "b": 1, "c": 3})

    def test_file_name(self):
        self.assertEqual(r.file_name("marks_sheet", "pdf", date(2026, 10, 20)), "marks_sheet_20261020.pdf")


if __name__ == "__main__":
    unittest.main()
