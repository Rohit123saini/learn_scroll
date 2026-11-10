"""X12 L-C standalone Go Live - pure rules (NOT RUN). No DB: plain SimpleTestCase.
    python manage.py test tuitionclass.test_standalone_live
"""
from datetime import datetime, timedelta, timezone as dt_tz
from types import SimpleNamespace

from django.test import SimpleTestCase

from tuitionclass import standalone_live as sl

NOW = datetime(2026, 10, 25, 10, 0, 0, tzinfo=dt_tz.utc)


class CleanTests(SimpleTestCase):
    def test_title(self):
        self.assertEqual(sl.clean_title("  My   first  live "), "My first live")
        for bad in ("", "  ", "ab", "x" * 151):
            with self.assertRaises(sl.StandaloneLiveError):
                sl.clean_title(bad)

    def test_topic(self):
        self.assertEqual(sl.clean_topic(None), "")
        with self.assertRaises(sl.StandaloneLiveError) as cm:
            sl.clean_topic("t" * 121)
        self.assertEqual(cm.exception.code, "topic_too_long")

    def test_duration(self):
        self.assertEqual(sl.clean_duration(None), 60)
        self.assertEqual(sl.clean_duration("90"), 90)
        for bad in ("abc", 5, 241):
            with self.assertRaises(sl.StandaloneLiveError) as cm:
                sl.clean_duration(bad)
            self.assertEqual(cm.exception.code, "invalid_duration")

    def test_timezone(self):
        self.assertEqual(sl.clean_timezone(""), "Asia/Kolkata")
        self.assertEqual(sl.clean_timezone("Europe/London"), "Europe/London")
        with self.assertRaises(sl.StandaloneLiveError):
            sl.clean_timezone("Mars/Base")


class ParseStartTests(SimpleTestCase):
    def test_empty_is_go_now(self):
        self.assertIsNone(sl.parse_start("", "Asia/Kolkata", now=NOW))
        self.assertIsNone(sl.parse_start(None, "Asia/Kolkata", now=NOW))

    def test_naive_is_read_in_host_zone(self):
        got = sl.parse_start("2026-10-26T18:00:00", "Asia/Kolkata", now=NOW)
        self.assertEqual(got.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(got.astimezone(dt_tz.utc), datetime(2026, 10, 26, 12, 30, tzinfo=dt_tz.utc))

    def test_offset_and_z_are_kept(self):
        got = sl.parse_start("2026-10-26T12:30:00Z", "Asia/Kolkata", now=NOW)
        self.assertEqual(got, datetime(2026, 10, 26, 12, 30, tzinfo=dt_tz.utc))

    def test_too_soon_and_too_far(self):
        soon = (NOW + timedelta(seconds=30)).isoformat()
        with self.assertRaises(sl.StandaloneLiveError) as cm:
            sl.parse_start(soon, "UTC", now=NOW)
        self.assertEqual(cm.exception.code, "schedule_too_soon")
        far = (NOW + timedelta(days=31)).isoformat()
        with self.assertRaises(sl.StandaloneLiveError) as cm:
            sl.parse_start(far, "UTC", now=NOW)
        self.assertEqual(cm.exception.code, "schedule_too_far")

    def test_garbage(self):
        with self.assertRaises(sl.StandaloneLiveError) as cm:
            sl.parse_start("tomorrow evening", "UTC", now=NOW)
        self.assertEqual(cm.exception.code, "invalid_start")


class AudienceTests(SimpleTestCase):
    def test_default_depends_on_minor(self):
        self.assertEqual(sl.resolve_audience(None, host_is_minor_blocked=False), "followers")
        self.assertEqual(sl.resolve_audience("", host_is_minor_blocked=True), "pass")

    def test_all_known_values_for_adults(self):
        for a in ("class", "pass", "followers", "public", "PUBLIC"):
            self.assertEqual(sl.resolve_audience(a, host_is_minor_blocked=False), a.lower())

    def test_unknown_audience(self):
        with self.assertRaises(sl.StandaloneLiveError) as cm:
            sl.resolve_audience("friends", host_is_minor_blocked=False)
        self.assertEqual(cm.exception.code, "invalid_audience")

    def test_minor_cannot_go_public_or_followers(self):
        for a in ("public", "followers"):
            with self.assertRaises(sl.StandaloneLiveError) as cm:
                sl.resolve_audience(a, host_is_minor_blocked=True)
            self.assertEqual(cm.exception.code, "public_live_blocked_minors")
            self.assertEqual(cm.exception.status, 403)
        self.assertEqual(sl.resolve_audience("class", host_is_minor_blocked=True), "class")


class ReminderWindowTests(SimpleTestCase):
    def test_window(self):
        self.assertTrue(sl.reminder_due(NOW + timedelta(minutes=15), NOW))
        self.assertTrue(sl.reminder_due(NOW + timedelta(minutes=1), NOW))
        self.assertTrue(sl.reminder_due(NOW - timedelta(minutes=5), NOW))
        self.assertFalse(sl.reminder_due(NOW + timedelta(minutes=16), NOW))
        self.assertFalse(sl.reminder_due(NOW - timedelta(minutes=6), NOW))

    def test_minutes_until(self):
        self.assertEqual(sl.minutes_until(NOW + timedelta(minutes=14, seconds=40), NOW), 14)
        self.assertEqual(sl.minutes_until(NOW - timedelta(minutes=2), NOW), 0)


class ErrorPayloadTests(SimpleTestCase):
    def test_payload_has_code_detail_and_extras(self):
        e = sl.StandaloneLiveError("already_live", "You are already live.", 409, {"session_id": 5})
        self.assertEqual(e.payload(), {"detail": "You are already live.", "code": "already_live", "session_id": 5})

    def test_display_title_falls_back_to_classroom(self):
        s = SimpleNamespace(live_title=" ", classroom=SimpleNamespace(title="Ravi - Live"))
        self.assertEqual(sl.display_title(s), "Ravi - Live")
        s.live_title = "Algebra Q&A"
        self.assertEqual(sl.display_title(s), "Algebra Q&A")
