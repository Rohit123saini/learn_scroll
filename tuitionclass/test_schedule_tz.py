"""TASK 10.1 — DST-safe schedule -> instant conversion (no DB needed)."""
from datetime import date, time, timedelta, timezone as dt_tz
import zoneinfo

from django.test import SimpleTestCase

from .models import ClassSchedule


def _sched(tz, start, minutes):
    return ClassSchedule(
        recurrence_type=ClassSchedule.RecurrenceType.DAILY,
        start_date=date(2026, 1, 1), start_time=start,
        duration_minutes=minutes, timezone=tz,
    )


class OccurrenceBoundsTests(SimpleTestCase):
    def test_kolkata_has_no_dst(self):
        s, e = _sched("Asia/Kolkata", time(18, 0), 60).occurrence_bounds(date(2026, 8, 29))
        self.assertEqual(s, __import__("datetime").datetime(2026, 8, 29, 12, 30, tzinfo=dt_tz.utc))
        self.assertEqual(e - s, timedelta(minutes=60))

    def test_duration_is_absolute_across_spring_forward(self):
        # New York clocks jump 02:00 -> 03:00 on 2026-03-08; a 3h class
        # starting 00:30 must really last 3h (not 2h as wall-clock math gives).
        s, e = _sched("America/New_York", time(0, 30), 180).occurrence_bounds(date(2026, 3, 8))
        self.assertEqual(e - s, timedelta(minutes=180))
        ny = zoneinfo.ZoneInfo("America/New_York")
        self.assertEqual(e.astimezone(ny).hour, 4)

    def test_same_wall_time_shifts_in_utc_after_dst(self):
        sch = _sched("America/New_York", time(9, 0), 60)
        winter, _ = sch.occurrence_bounds(date(2026, 3, 7))
        summer, _ = sch.occurrence_bounds(date(2026, 3, 9))
        self.assertEqual(winter.hour, 14)  # EST = UTC-5
        self.assertEqual(summer.hour, 13)  # EDT = UTC-4

    def test_bad_zone_falls_back_instead_of_raising(self):
        s, e = _sched("Nope/Nowhere", time(10, 0), 30).occurrence_bounds(date(2026, 8, 29))
        self.assertEqual(e - s, timedelta(minutes=30))
