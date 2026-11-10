# campus/tests_notice_rules.py — pure logic, no Django:  python -m unittest campus.tests_notice_rules
import unittest
from datetime import datetime, timedelta, timezone

from campus import notice_rules as r

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc)


class AudienceTests(unittest.TestCase):
    def test_flags(self):
        self.assertEqual(r.audience_flags("everyone"), (True, True, True))
        self.assertEqual(r.audience_flags("students"), (True, False, False))
        self.assertEqual(r.audience_flags("parents"), (False, True, False))
        self.assertEqual(r.audience_flags("staff"), (False, False, True))

    def test_unknown_audience_never_means_nobody(self):
        self.assertEqual(r.audience_flags("???"), (True, True, True))
        self.assertEqual(r.audience_flags(""), (True, True, True))

    def test_only_staff_notices_are_hidden_from_members(self):
        self.assertTrue(r.hidden_from_non_staff("staff"))
        for a in ("everyone", "students", "parents"):
            self.assertFalse(r.hidden_from_non_staff(a))


class ScheduleTests(unittest.TestCase):
    def test_past_and_too_soon_rejected(self):
        for delta in (timedelta(days=-1), timedelta(0), timedelta(seconds=30)):
            with self.subTest(delta=delta), self.assertRaises(r.NoticeRuleError) as cm:
                r.validate_schedule(NOW + delta, NOW)
            self.assertEqual(cm.exception.code, "schedule_in_past")

    def test_boundaries(self):
        r.validate_schedule(NOW + timedelta(minutes=1), NOW)         # exactly 1 minute: ok
        r.validate_schedule(NOW + timedelta(days=90), NOW)           # exactly 90 days: ok
        with self.assertRaises(r.NoticeRuleError) as cm:
            r.validate_schedule(NOW + timedelta(days=90, seconds=1), NOW)
        self.assertEqual(cm.exception.code, "schedule_too_far")

    def test_is_due(self):
        self.assertTrue(r.is_due("scheduled", NOW, NOW))
        self.assertTrue(r.is_due("scheduled", NOW - timedelta(hours=3), NOW))   # catch-up after downtime
        self.assertFalse(r.is_due("scheduled", NOW + timedelta(seconds=1), NOW))
        self.assertFalse(r.is_due("scheduled", None, NOW))
        for st in ("published", "cancelled"):
            self.assertFalse(r.is_due(st, NOW - timedelta(days=1), NOW))

    def test_only_scheduled_can_be_cancelled_or_edited(self):
        self.assertTrue(r.can_cancel("scheduled") and r.can_edit("scheduled"))
        for st in ("published", "cancelled"):
            self.assertFalse(r.can_cancel(st))
            self.assertFalse(r.can_edit(st))


class EmergencyTests(unittest.TestCase):
    def test_valid_is_trimmed(self):
        self.assertEqual(r.validate_emergency("  Fire drill  ", "  Go to the ground now. "),
                         ("Fire drill", "Go to the ground now."))

    def test_rejects_missing_or_long_text(self):
        cases = [("", "Evacuate now", "emergency_title_required"),
                 ("   ", "Evacuate now", "emergency_title_required"),
                 (None, "Evacuate now", "emergency_title_required"),
                 ("x" * 121, "Evacuate now", "emergency_title_long"),
                 ("Fire", "", "emergency_body_required"),
                 ("Fire", "ok", "emergency_body_required")]
        for title, body, code in cases:
            with self.subTest(title=title, body=body), self.assertRaises(r.NoticeRuleError) as cm:
                r.validate_emergency(title, body)
            self.assertEqual(cm.exception.code, code)

    def test_title_length_boundary(self):
        r.validate_emergency("x" * 120, "Evacuate now")

    def test_duplicate_window(self):
        self.assertFalse(r.is_duplicate_emergency(None, NOW))
        self.assertTrue(r.is_duplicate_emergency(NOW - timedelta(seconds=119), NOW))
        self.assertFalse(r.is_duplicate_emergency(NOW - timedelta(minutes=2), NOW))

    def test_pin(self):
        self.assertEqual(r.emergency_pin_until(NOW), NOW + timedelta(hours=24))


class PushTextTests(unittest.TestCase):
    def test_title(self):
        self.assertEqual(r.push_title("normal", "PTM Friday"), "PTM Friday")
        self.assertTrue(r.push_title("emergency", "Fire drill").startswith("🚨 "))

    def test_body_falls_back_to_title_and_truncates(self):
        self.assertEqual(r.push_body("Holiday", ""), "Holiday")
        self.assertEqual(r.push_body("Holiday", "   "), "Holiday")
        self.assertEqual(len(r.push_body("t", "x" * 500)), 200)


class ChunkTests(unittest.TestCase):
    def test_chunks(self):
        self.assertEqual(list(r.chunked([])), [])
        chunks = list(r.chunked(range(1203)))
        self.assertEqual([len(c) for c in chunks], [500, 500, 203])
        self.assertEqual(sum(chunks, []), list(range(1203)))


if __name__ == "__main__":
    unittest.main()
