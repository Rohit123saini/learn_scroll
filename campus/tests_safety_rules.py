# campus/tests_safety_rules.py
"""
Pure rules of the Safety module — no Django, no database, no clock.

Run: `python -m unittest campus.tests_safety_rules`   (works without any project setup)
"""
import importlib.util
import os
import unittest
from datetime import datetime, timedelta

_spec = importlib.util.spec_from_file_location("safety_rules", os.path.join(os.path.dirname(__file__), "safety_rules.py"))
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)


class AuditBoundTests(unittest.TestCase):
    def test_date_since_is_start_of_day(self):
        self.assertEqual(rules.parse_audit_bound("2026-10-20"), datetime(2026, 10, 20, 0, 0, 0))

    def test_date_until_is_end_of_that_day(self):
        got = rules.parse_audit_bound("2026-10-20", end=True)
        self.assertEqual(got.date().isoformat(), "2026-10-20")
        self.assertEqual((got.hour, got.minute, got.second), (23, 59, 59))
        self.assertGreater(got, datetime(2026, 10, 20, 23, 59, 58))

    def test_datetime_used_as_is_and_timezone_dropped(self):
        self.assertEqual(rules.parse_audit_bound("2026-10-20T14:30:00"), datetime(2026, 10, 20, 14, 30))
        self.assertEqual(rules.parse_audit_bound("2026-10-20T14:30:00Z"), datetime(2026, 10, 20, 14, 30))

    def test_empty_is_none_junk_raises(self):
        self.assertIsNone(rules.parse_audit_bound(""))
        self.assertIsNone(rules.parse_audit_bound(None))
        with self.assertRaises(ValueError):
            rules.parse_audit_bound("yesterday")

    def test_range_check(self):
        rules.check_range(datetime(2026, 1, 1), datetime(2026, 1, 2))
        rules.check_range(None, datetime(2026, 1, 2))
        with self.assertRaises(ValueError):
            rules.check_range(datetime(2026, 1, 3), datetime(2026, 1, 2))

    def test_category_search_uuid(self):
        self.assertEqual(rules.audit_category("section.create"), "section")
        self.assertEqual(rules.audit_category("fine_waive"), "fine_waive")
        self.assertEqual(rules.clean_search("  ravi   kumar \n"), "ravi kumar")
        self.assertEqual(len(rules.clean_search("x" * 500)), rules.AUDIT_SEARCH_MAX_LEN)
        self.assertTrue(rules.is_uuid("123e4567-e89b-12d3-a456-426614174000"))
        self.assertFalse(rules.is_uuid("12"))

    def test_csv_formula_guard(self):
        for bad in ("=1+1", "+cmd", "-2", "@SUM(A1)"):
            self.assertTrue(rules.safe_cell(bad).startswith("'"), bad)
        self.assertEqual(rules.safe_cell("plain"), "plain")
        self.assertEqual(rules.safe_cell(None), "")


class RestoreOptionTests(unittest.TestCase):
    def check(self, groups=None, mode="missing_only", available=("setup",), confirm="", dry=True):
        return rules.validate_restore_options(groups, mode, available, confirm, dry)

    def test_defaults_to_setup_and_missing_only(self):
        self.assertEqual(self.check(), (["setup"], "missing_only"))

    def test_dry_run_needs_no_confirmation_real_run_does(self):
        self.check(dry=True)
        with self.assertRaises(ValueError) as ctx:
            self.check(dry=False)
        self.assertEqual(str(ctx.exception), "confirm_required")
        self.assertEqual(self.check(dry=False, confirm=" restore ")[1], "missing_only")

    def test_group_must_exist_in_backup(self):
        with self.assertRaises(ValueError) as ctx:
            self.check(groups=["records"])
        self.assertEqual(str(ctx.exception), "group_not_in_backup")
        self.assertEqual(self.check(groups=["records", "setup"], available=("setup", "records"))[0], ["setup", "records"])

    def test_bad_group_and_mode(self):
        with self.assertRaises(ValueError) as ctx:
            self.check(groups=["everything"])
        self.assertEqual(str(ctx.exception), "invalid_group")
        with self.assertRaises(ValueError) as ctx:
            self.check(mode="delete_all")
        self.assertEqual(str(ctx.exception), "invalid_mode")


class DeletionRuleTests(unittest.TestCase):
    S = rules.DeletionStatus

    def test_allowed_transitions_only(self):
        ok = [("pending", "approved"), ("pending", "rejected"), ("pending", "cancelled"), ("approved", "completed")]
        for a, b in ok:
            self.assertTrue(rules.can_transition(a, b), (a, b))
        for a, b in [("approved", "rejected"), ("approved", "cancelled"), ("rejected", "approved"),
                     ("completed", "approved"), ("pending", "completed"), ("cancelled", "pending")]:
            self.assertFalse(rules.can_transition(a, b), (a, b))

    def test_due_and_overdue(self):
        created = datetime(2026, 10, 1)
        due = rules.due_at(created)
        self.assertEqual(due, created + timedelta(days=30))
        self.assertTrue(rules.is_overdue("pending", due, due + timedelta(seconds=1)))
        self.assertTrue(rules.is_overdue("approved", due, due + timedelta(days=1)))
        self.assertFalse(rules.is_overdue("pending", due, due))
        self.assertFalse(rules.is_overdue("completed", due, due + timedelta(days=9)))
        self.assertFalse(rules.is_overdue("rejected", due, due + timedelta(days=9)))

    def test_clean_text(self):
        self.assertEqual(rules.clean_text("  too   many   spaces ", minimum=3, maximum=50, field="note"), "too many spaces")
        with self.assertRaises(ValueError) as ctx:
            rules.clean_text("  a ", minimum=3, maximum=50, field="reason")
        self.assertEqual(str(ctx.exception), "reason_too_short")
        self.assertEqual(len(rules.clean_text("x" * 900, minimum=3, maximum=500, field="reason")), 500)

    def test_records_the_school_must_keep_are_listed(self):
        keys = {k for k, _ in rules.RETAINED_RECORDS}
        self.assertTrue({"attendance", "results", "fees", "audit_log", "consent_log"} <= keys)


class ConsentRuleTests(unittest.TestCase):
    def test_scopes(self):
        self.assertEqual(rules.validate_consent("terms_privacy", True, False, None, "v1"), ("granted", "v1"))
        self.assertEqual(rules.validate_consent("communications", False, False, "v0", "v1"), ("withdrawn", "v0"))
        self.assertEqual(rules.validate_consent("child_data", True, True, None, "v1")[0], "granted")
        self.assertEqual(rules.validate_consent("photo_media", True, True, None, "v1")[0], "granted")  # either
        self.assertEqual(rules.validate_consent("photo_media", True, False, None, "v1")[0], "granted")

    def test_rejections(self):
        for args, code in [
            (("nope", True, False, None, "v1"), "invalid_purpose"),
            (("terms_privacy", "yes", False, None, "v1"), "granted_must_be_boolean"),
            (("terms_privacy", 1, False, None, "v1"), "granted_must_be_boolean"),
            (("child_data", True, False, None, "v1"), "student_required"),
            (("terms_privacy", True, True, None, "v1"), "student_not_allowed"),
        ]:
            with self.assertRaises(ValueError) as ctx:
                rules.validate_consent(*args)
            self.assertEqual(str(ctx.exception), code, args)

    def test_version_is_trimmed_and_capped(self):
        self.assertEqual(len(rules.validate_consent("terms_privacy", True, False, "v" * 99, "d")[1]), rules.POLICY_VERSION_MAX)
        self.assertEqual(rules.validate_consent("terms_privacy", True, False, "   ", "d")[1], "d")

    def test_needs_reconsent(self):
        self.assertTrue(rules.needs_reconsent("granted", "2025-01", "2026-10"))
        self.assertFalse(rules.needs_reconsent("granted", "2026-10", "2026-10"))
        self.assertFalse(rules.needs_reconsent("withdrawn", "2025-01", "2026-10"))

    def test_latest_per_key_picks_newest_and_ties_keep_later(self):
        t = datetime(2026, 10, 1)
        rows = [
            {"subject": 1, "student": None, "purpose": "p", "status": "granted", "created_at": t},
            {"subject": 1, "student": None, "purpose": "p", "status": "withdrawn", "created_at": t + timedelta(days=1)},
            {"subject": 1, "student": 7, "purpose": "p", "status": "granted", "created_at": t},
            {"subject": 2, "student": None, "purpose": "p", "status": "granted", "created_at": t},
            {"subject": 2, "student": None, "purpose": "p", "status": "withdrawn", "created_at": t},  # tie -> later item
        ]
        got = rules.latest_per_key(reversed(rows[:2]))  # order does not matter
        self.assertEqual(got[(1, None, "p")]["status"], "withdrawn")
        got = rules.latest_per_key(rows)
        self.assertEqual(got[(1, 7, "p")]["status"], "granted")
        self.assertEqual(got[(2, None, "p")]["status"], "withdrawn")
        self.assertEqual(len(got), 3)

    def test_every_purpose_has_a_known_scope(self):
        for key, spec in rules.CONSENT_PURPOSES.items():
            self.assertIn(spec["scope"], ("self", "child", "either"), key)
            self.assertIsInstance(spec["required"], bool)


if __name__ == "__main__":
    unittest.main()
