# campus/tests_settings_rules.py
"""
Pure rules of Campus Settings — no Django, no database, no clock.
Covers `settings_rules.py` (profile, grading request, working days, holidays, timetable slots) and the pure
grade maths in `grading.py` (CBSE / percentage / GPA).

Run: `python -m unittest campus.tests_settings_rules`   (works without any project setup)

Written against the code only — NOT RUN when written.
"""
import importlib.util
import os
import unittest
from datetime import date, time
from types import SimpleNamespace


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(os.path.dirname(__file__), filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load("settings_rules", "settings_rules.py")
grading = _load("grading", "grading.py")

THIS_YEAR = 2026


# ===========================================================================
# 1. School profile
# ===========================================================================
class CleanProfileTests(unittest.TestCase):
    def clean(self, **data):
        return rules.clean_profile(data, this_year=THIS_YEAR)

    def test_only_sent_keys_come_back(self):
        clean, errors = self.clean(city="Pune")
        self.assertEqual(errors, {})
        self.assertEqual(clean, {"city": "Pune"})

    def test_non_object_is_rejected(self):
        clean, errors = rules.clean_profile(["x"], this_year=THIS_YEAR)
        self.assertEqual(clean, {})
        self.assertIn("detail", errors)

    def test_name_is_required_and_squashed(self):
        self.assertIn("name", self.clean(name="   ")[1])
        self.assertIn("name", self.clean(name="x" * (rules.MAX_NAME + 1))[1])
        self.assertEqual(self.clean(name="  Green   Valley \n School ")[0], {"name": "Green Valley School"})

    def test_blank_text_clears_a_field(self):
        clean, errors = self.clean(city="   ", tagline="")
        self.assertEqual(errors, {})
        self.assertEqual(clean, {"city": "", "tagline": ""})

    def test_text_length_limits(self):
        self.assertIn("tagline", self.clean(tagline="x" * 121)[1])
        self.assertEqual(self.clean(tagline="x" * 120)[1], {})
        self.assertIn("principal_name", self.clean(principal_name="x" * 101)[1])
        self.assertIn("affiliation_no", self.clean(affiliation_no="1" * 31)[1])

    def test_board(self):
        self.assertEqual(self.clean(board=" CBSE ")[0], {"board": "cbse"})
        self.assertEqual(self.clean(board="")[0], {"board": ""})
        self.assertIn("board", self.clean(board="nope")[1])
        for b in rules.BOARDS:
            self.assertEqual(self.clean(board=b)[1], {}, b)

    def test_udise_is_eleven_digits(self):
        self.assertEqual(self.clean(udise_code="12345678901")[0], {"udise_code": "12345678901"})
        for bad in ("1234567890", "123456789012", "1234567890A"):
            self.assertIn("udise_code", self.clean(udise_code=bad)[1], bad)
        self.assertEqual(self.clean(udise_code="")[0], {"udise_code": ""})

    def test_established_year(self):
        self.assertEqual(self.clean(established_year=1990)[0], {"established_year": 1990})
        self.assertEqual(self.clean(established_year="1990")[0], {"established_year": 1990})
        self.assertEqual(self.clean(established_year=THIS_YEAR)[0], {"established_year": THIS_YEAR})
        self.assertEqual(self.clean(established_year="")[0], {"established_year": None})
        self.assertEqual(self.clean(established_year=None)[0], {"established_year": None})
        for bad in (1799, THIS_YEAR + 1, "abc", True):
            self.assertIn("established_year", self.clean(established_year=bad)[1], repr(bad))

    def test_pincode(self):
        self.assertEqual(self.clean(pincode="411001")[0], {"pincode": "411001"})
        self.assertEqual(self.clean(pincode="SW1A 1AA")[0], {"pincode": "SW1A 1AA"})
        for bad in ("12", "!!!!!!", "1" * 12):
            self.assertIn("pincode", self.clean(pincode=bad)[1], bad)

    def test_phone(self):
        self.assertEqual(self.clean(phone="+91 98765 43210")[1], {})
        self.assertEqual(self.clean(phone="020-2551234")[1], {})
        for bad in ("abc", "123", "1" * 16, "98765*43210"):
            self.assertIn("phone", self.clean(phone=bad)[1], bad)

    def test_email_is_lowercased_and_checked(self):
        self.assertEqual(self.clean(email=" Office@School.EDU ")[0], {"email": "office@school.edu"})
        for bad in ("nope", "a@b", "a b@c.com"):
            self.assertIn("email", self.clean(email=bad)[1], bad)

    def test_website_gets_https(self):
        self.assertEqual(self.clean(website="school.edu.in")[0], {"website": "https://school.edu.in"})
        self.assertEqual(self.clean(website="http://school.edu.in/about")[0], {"website": "http://school.edu.in/about"})
        for bad in ("localhost", "ftp://school.edu", "https://"):
            self.assertIn("website", self.clean(website=bad)[1], bad)

    def test_all_errors_reported_together(self):
        _, errors = self.clean(phone="x", email="y", board="z")
        self.assertEqual(set(errors), {"phone", "email", "board"})

    def test_profile_fields_cover_every_validated_key(self):
        _, errors = rules.clean_profile({f: "" for f in rules.PROFILE_FIELDS}, this_year=THIS_YEAR)
        self.assertEqual(errors, {})


# ===========================================================================
# 2. Grading request shape
# ===========================================================================
class GradingRequestTests(unittest.TestCase):
    def req(self, data):
        return rules.clean_grading_request(data, systems=grading.SYSTEMS, presets=grading.PRESETS)

    def test_system_only(self):
        plan, err = self.req({"system": "percent"})
        self.assertIsNone(err)
        self.assertEqual(plan, {"system": "percent", "preset": None, "bands": None, "pass_percent": None})

    def test_preset_decides_the_system(self):
        plan, err = self.req({"preset": "gpa10"})
        self.assertIsNone(err)
        self.assertEqual((plan["system"], plan["preset"]), ("gpa", "gpa10"))

    def test_preset_of_other_system_is_rejected(self):
        plan, err = self.req({"preset": "gpa4", "system": "cbse"})
        self.assertIsNone(plan)
        self.assertIn("different grading system", err)

    def test_unknown_preset_and_system(self):
        self.assertIn("Unknown preset", self.req({"preset": "x"})[1])
        self.assertIn("must be one of", self.req({"system": "x"})[1])

    def test_nothing_chosen(self):
        self.assertEqual(self.req({})[1], "Choose a grading system.")

    def test_bands_must_be_a_list(self):
        self.assertIn("list", self.req({"system": "cbse", "grade_scale": "A1"})[1])
        plan, err = self.req({"system": "cbse", "grade_scale": [{"min_percent": 0, "grade": "E"}], "pass_percent": 40})
        self.assertIsNone(err)
        self.assertEqual(plan["pass_percent"], 40)

    def test_not_an_object(self):
        self.assertEqual(self.req("cbse")[1], "Send an object.")


# ===========================================================================
# 3. Working days + calendar
# ===========================================================================
class WeekdayTests(unittest.TestCase):
    def test_clean_weekdays(self):
        self.assertEqual(rules.clean_weekdays(None), [])
        self.assertEqual(rules.clean_weekdays([7, 1, 7]), [1, 7])
        self.assertEqual(rules.clean_weekdays((6,)), [6])

    def test_clean_weekdays_rejects_junk(self):
        for bad in ([0], [8], ["7"], [True], "7", 7, [1.5]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.clean_weekdays(bad)

    def test_validate_working_days(self):
        self.assertEqual(rules.validate_working_days([7], [6]), ([7], [6]))
        self.assertEqual(rules.validate_working_days(None, None), ([], []))

    def test_off_and_half_cannot_overlap(self):
        with self.assertRaises(ValueError) as cm:
            rules.validate_working_days([7], [7])
        self.assertIn("Sunday", str(cm.exception))

    def test_one_full_working_day_must_remain(self):
        with self.assertRaises(ValueError):
            rules.validate_working_days([1, 2, 3, 4, 5, 6], [7])
        with self.assertRaises(ValueError):
            rules.validate_working_days([1, 2, 3, 4, 5, 6, 7], [])
        self.assertEqual(rules.validate_working_days([1, 2, 3, 4, 5, 6], [])[0], [1, 2, 3, 4, 5, 6])

    def test_working_weekdays(self):
        self.assertEqual(rules.working_weekdays([7]), [1, 2, 3, 4, 5, 6])
        self.assertEqual(rules.working_weekdays([]), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(rules.working_weekdays(None), [1, 2, 3, 4, 5, 6, 7])


class DayKindTests(unittest.TestCase):
    # 2026-10-18 is a Sunday, 2026-10-19 Monday, 2026-10-24 Saturday
    SUN, MON, SAT = date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 24)

    def test_plain_kinds(self):
        self.assertEqual(rules.day_kind(self.MON, [7], [6], {}), rules.KIND_WORKING)
        self.assertEqual(rules.day_kind(self.SUN, [7], [6], {}), rules.KIND_OFF)
        self.assertEqual(rules.day_kind(self.SAT, [7], [6], {}), rules.KIND_HALF)

    def test_holiday_wins_over_everything(self):
        for d in (self.MON, self.SUN, self.SAT):
            self.assertEqual(rules.day_kind(d, [7], [6], {d: "X"}), rules.KIND_HOLIDAY)
        self.assertEqual(rules.day_kind(self.MON, [7], [6], {self.MON}), rules.KIND_HOLIDAY)  # a set works too

    def test_off_wins_over_half(self):
        self.assertEqual(rules.day_kind(self.SAT, [6], [6], {}), rules.KIND_OFF)

    def test_none_args_are_fine(self):
        self.assertEqual(rules.day_kind(self.SUN, None, None, {}), rules.KIND_WORKING)

    def test_values(self):
        self.assertEqual(rules.day_value(rules.KIND_WORKING), 1.0)
        self.assertEqual(rules.day_value(rules.KIND_HALF), 0.5)
        self.assertEqual(rules.day_value(rules.KIND_OFF), 0.0)
        self.assertEqual(rules.day_value(rules.KIND_HOLIDAY), 0.0)


class CountWorkingTests(unittest.TestCase):
    def test_week_with_half_day_and_off(self):
        # Mon 19 .. Sun 25 Oct 2026: Mon-Fri 5, Sat half 0.5, Sun off
        self.assertEqual(rules.count_working(date(2026, 10, 19), date(2026, 10, 25), [7], [6], {}), 5.5)

    def test_holiday_removes_a_day(self):
        hol = {date(2026, 10, 21): "Local"}
        self.assertEqual(rules.count_working(date(2026, 10, 19), date(2026, 10, 25), [7], [6], hol), 4.5)

    def test_single_day_and_reversed_range(self):
        self.assertEqual(rules.count_working(date(2026, 10, 19), date(2026, 10, 19), [], [], {}), 1.0)
        self.assertEqual(rules.count_working(date(2026, 10, 20), date(2026, 10, 19), [], [], {}), 0.0)


class MonthCalendarTests(unittest.TestCase):
    def test_october_2026(self):
        # Oct 2026: 31 days. Sundays 4/11/18/25 (4), Saturdays 3/10/17/24/31 (5), Fri 2 Oct = Gandhi Jayanti.
        cal = rules.month_calendar(2026, 10, [7], [6], {date(2026, 10, 2): "Gandhi Jayanti"})
        self.assertEqual((cal["year"], cal["month"]), (2026, 10))
        self.assertEqual(len(cal["days"]), 31)
        self.assertEqual(cal["summary"], {
            "working_days": 21, "half_days": 5, "weekly_offs": 4, "holidays": 1, "working_day_value": 23.5,
        })
        by_date = {d["date"]: d for d in cal["days"]}
        self.assertEqual(by_date["2026-10-02"]["kind"], "holiday")
        self.assertEqual(by_date["2026-10-02"]["holiday"], "Gandhi Jayanti")
        self.assertEqual(by_date["2026-10-02"]["weekday"], 5)
        self.assertEqual(by_date["2026-10-04"]["kind"], "weekly_off")
        self.assertEqual(by_date["2026-10-03"]["kind"], "half_day")
        self.assertNotIn("holiday", by_date["2026-10-05"])

    def test_holiday_on_a_sunday_counts_as_holiday_not_off(self):
        cal = rules.month_calendar(2026, 10, [7], [], {date(2026, 10, 4): "Festival"})
        self.assertEqual(cal["summary"]["holidays"], 1)
        self.assertEqual(cal["summary"]["weekly_offs"], 3)

    def test_leap_february(self):
        self.assertEqual(len(rules.month_calendar(2028, 2, [], [], {})["days"]), 29)
        self.assertEqual(len(rules.month_calendar(2027, 2, [], [], {})["days"]), 28)

    def test_summary_days_add_up(self):
        s = rules.month_calendar(2026, 11, [7], [6], {date(2026, 11, 9): "x"})["summary"]
        self.assertEqual(s["working_days"] + s["half_days"] + s["weekly_offs"] + s["holidays"], 30)


class ParseMonthTests(unittest.TestCase):
    TODAY = date(2026, 10, 20)

    def test_empty_means_this_month(self):
        self.assertEqual(rules.parse_month("", self.TODAY), (2026, 10))
        self.assertEqual(rules.parse_month(None, self.TODAY), (2026, 10))

    def test_valid(self):
        self.assertEqual(rules.parse_month("2027-02", self.TODAY), (2027, 2))
        self.assertEqual(rules.parse_month(" 2027-2 ", self.TODAY), (2027, 2))

    def test_invalid(self):
        for bad in ("abc", "2026-13", "2026-00", "1800-01", "2026/10", "2026"):
            with self.assertRaises(ValueError, msg=bad):
                rules.parse_month(bad, self.TODAY)


# ===========================================================================
# 4. Holidays
# ===========================================================================
class ParseDateTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(rules.parse_date("2026-10-20"), date(2026, 10, 20))
        self.assertEqual(rules.parse_date(date(2026, 10, 20)), date(2026, 10, 20))

    def test_bad(self):
        for bad in ("20-10-2026", "2026-02-30", "", None, "tomorrow"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.parse_date(bad)

    def test_field_name_is_in_the_message(self):
        with self.assertRaises(ValueError) as cm:
            rules.parse_date("x", "date_from")
        self.assertIn("date_from", str(cm.exception))


class HolidayNameTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(rules.clean_holiday_name("  Diwali   break "), "Diwali break")
        with self.assertRaises(ValueError):
            rules.clean_holiday_name("  ")
        with self.assertRaises(ValueError):
            rules.clean_holiday_name("x" * 101)
        self.assertEqual(len(rules.clean_holiday_name("x" * 100)), 100)


class ExpandRangeTests(unittest.TestCase):
    def test_inclusive_range(self):
        out = rules.expand_range("2026-10-20", "2026-10-24", "Diwali break")
        self.assertEqual([d.isoformat() for d, _ in out],
                         ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24"])
        self.assertEqual({n for _, n in out}, {"Diwali break"})

    def test_single_day(self):
        self.assertEqual(len(rules.expand_range("2026-10-20", "2026-10-20", "x")), 1)

    def test_skip_weekly_off(self):
        # Fri 23, Sat 24, Sun 25, Mon 26 — Sunday dropped
        out = rules.expand_range("2026-10-23", "2026-10-26", "Break", skip_weekdays=(7,))
        self.assertEqual([d.day for d, _ in out], [23, 24, 26])

    def test_range_of_only_off_days_is_empty(self):
        self.assertEqual(rules.expand_range("2026-10-25", "2026-10-25", "x", skip_weekdays=[7]), [])

    def test_end_before_start(self):
        with self.assertRaises(ValueError):
            rules.expand_range("2026-10-24", "2026-10-20", "x")

    def test_length_limit(self):
        rules.expand_range("2026-01-01", "2026-03-01", "x")  # exactly 60 days
        with self.assertRaises(ValueError):
            rules.expand_range("2026-01-01", "2026-03-02", "x")  # 61 days

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            rules.expand_range("nope", "2026-10-24", "x")
        with self.assertRaises(ValueError):
            rules.expand_range("2026-10-20", "2026-10-24", "")


class CleanHolidayItemsTests(unittest.TestCase):
    def test_good_rows(self):
        rows, errors = rules.clean_holiday_items([
            {"date": "2026-11-09", "name": "Diwali"}, {"date": "2026-12-25", "name": " Christmas "},
        ])
        self.assertEqual(errors, [])
        self.assertEqual(rows, [(date(2026, 11, 9), "Diwali"), (date(2026, 12, 25), "Christmas")])

    def test_bad_rows_are_reported_by_one_based_index_and_skipped(self):
        rows, errors = rules.clean_holiday_items([
            {"date": "2026-11-09", "name": "Diwali"},
            {"date": "bad", "name": "x"},
            "not an object",
            {"date": "2026-12-25", "name": ""},
        ])
        self.assertEqual(rows, [(date(2026, 11, 9), "Diwali")])
        self.assertEqual([e["index"] for e in errors], [2, 3, 4])

    def test_repeated_date_keeps_first(self):
        rows, errors = rules.clean_holiday_items([
            {"date": "2026-11-09", "name": "First"}, {"date": "2026-11-09", "name": "Second"},
        ])
        self.assertEqual(rows, [(date(2026, 11, 9), "First")])
        self.assertEqual(errors[0]["index"], 2)
        self.assertIn("twice", errors[0]["message"])

    def test_container_rules(self):
        for bad in (None, [], {}, "x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.clean_holiday_items(bad)
        too_many = [{"date": "2026-01-01", "name": "x"}] * (rules.MAX_BULK_ITEMS + 1)
        with self.assertRaises(ValueError):
            rules.clean_holiday_items(too_many)


class NationalAndShiftTests(unittest.TestCase):
    def test_national_fixed(self):
        got = rules.national_fixed_holidays(2026)
        self.assertEqual(got, [
            (date(2026, 1, 26), "Republic Day"),
            (date(2026, 8, 15), "Independence Day"),
            (date(2026, 10, 2), "Gandhi Jayanti"),
        ])

    def test_shift_to_year(self):
        self.assertEqual(rules.shift_to_year(date(2026, 8, 15), 2027), date(2027, 8, 15))
        self.assertEqual(rules.shift_to_year(date(2024, 2, 29), 2025), date(2025, 2, 28))
        self.assertEqual(rules.shift_to_year(date(2024, 2, 29), 2028), date(2028, 2, 29))

    def test_clean_year(self):
        self.assertEqual(rules.clean_year("2026"), 2026)
        self.assertEqual(rules.clean_year(2027), 2027)
        for bad in ("26", True, None, "abc", 1899, 2201):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.clean_year(bad)
        with self.assertRaises(ValueError) as cm:
            rules.clean_year("x", "from_year")
        self.assertIn("from_year", str(cm.exception))


# ===========================================================================
# 5. Timetable slots
# ===========================================================================
class ParseHHMMTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(rules.parse_hhmm("09:00"), time(9, 0))
        self.assertEqual(rules.parse_hhmm("9:05"), time(9, 5))
        self.assertEqual(rules.parse_hhmm("23:59"), time(23, 59))
        self.assertEqual(rules.parse_hhmm("09:00:30"), time(9, 0))  # seconds are dropped

    def test_invalid(self):
        for bad in ("24:00", "09:60", "9", "", None, "ab:cd", "09:00:60"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.parse_hhmm(bad)


class GenerateSlotsTests(unittest.TestCase):
    def times(self, slots):
        return [(s["start"].strftime("%H:%M"), s["end"].strftime("%H:%M")) for s in slots]

    def test_back_to_back_periods(self):
        slots = rules.generate_slots("09:00", 40, 3)
        self.assertEqual(self.times(slots), [("09:00", "09:40"), ("09:40", "10:20"), ("10:20", "11:00")])
        self.assertEqual([s["label"] for s in slots], ["Period 1", "Period 2", "Period 3"])
        self.assertEqual([s["index"] for s in slots], [1, 2, 3])

    def test_break_leaves_a_gap_not_a_slot(self):
        slots = rules.generate_slots("09:00", 40, 3, breaks=[{"after": 2, "minutes": 20}])
        self.assertEqual(self.times(slots), [("09:00", "09:40"), ("09:40", "10:20"), ("10:40", "11:20")])
        self.assertEqual(len(slots), 3)

    def test_two_breaks(self):
        slots = rules.generate_slots("08:00", 30, 4, breaks=[{"after": 1, "minutes": 10}, {"after": 3, "minutes": 30}])
        self.assertEqual(self.times(slots), [("08:00", "08:30"), ("08:40", "09:10"), ("09:10", "09:40"), ("10:10", "10:40")])

    def test_custom_prefix_and_time_object_start(self):
        slots = rules.generate_slots(time(8, 0), 45, 2, label_prefix="Lecture")
        self.assertEqual([s["label"] for s in slots], ["Lecture 1", "Lecture 2"])
        self.assertEqual(self.times(slots), [("08:00", "08:45"), ("08:45", "09:30")])

    def test_limits(self):
        with self.assertRaises(ValueError):
            rules.generate_slots("09:00", 40, 0)
        with self.assertRaises(ValueError):
            rules.generate_slots("09:00", 40, rules.MAX_PERIODS + 1)
        self.assertEqual(len(rules.generate_slots("06:00", 20, rules.MAX_PERIODS)), rules.MAX_PERIODS)
        with self.assertRaises(ValueError):
            rules.generate_slots("09:00", rules.MIN_PERIOD_MINUTES - 1, 2)
        with self.assertRaises(ValueError):
            rules.generate_slots("09:00", rules.MAX_PERIOD_MINUTES + 1, 2)

    def test_numbers_must_be_numbers(self):
        for bad in ("abc", None, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                rules.generate_slots("09:00", bad, 2)
        self.assertEqual(len(rules.generate_slots("09:00", "40", "2")), 2)  # numeric strings are fine

    def test_bad_start(self):
        with self.assertRaises(ValueError):
            rules.generate_slots("25:00", 40, 2)

    def test_bad_breaks(self):
        cases = [
            [{"after": 3, "minutes": 10}],                       # after the last period
            [{"after": 0, "minutes": 10}],                       # before the first
            [{"after": 1, "minutes": 0}],                        # no length
            [{"after": 1, "minutes": rules.MAX_BREAK_MINUTES + 1}],
            [{"after": 1, "minutes": 5}, {"after": 1, "minutes": 5}],  # twice after period 1
            ["lunch"],
        ]
        for breaks in cases:
            with self.assertRaises(ValueError, msg=repr(breaks)):
                rules.generate_slots("09:00", 40, 3, breaks=breaks)

    def test_single_period_cannot_have_a_break(self):
        with self.assertRaises(ValueError):
            rules.generate_slots("09:00", 40, 1, breaks=[{"after": 1, "minutes": 10}])

    def test_midnight_is_refused(self):
        with self.assertRaises(ValueError):
            rules.generate_slots("23:30", 60, 1)
        with self.assertRaises(ValueError):
            rules.generate_slots("22:00", 60, 2)
        with self.assertRaises(ValueError):
            rules.generate_slots("22:00", 60, 2, breaks=[{"after": 1, "minutes": 90}])  # the break crosses midnight
        self.assertEqual(len(rules.generate_slots("23:00", 50, 1)), 1)


class OverlapTests(unittest.TestCase):
    def test_overlaps(self):
        self.assertTrue(rules.overlaps(time(9), time(10), time(9, 30), time(10, 30)))
        self.assertTrue(rules.overlaps(time(9), time(12), time(10), time(11)))  # one inside the other
        self.assertFalse(rules.overlaps(time(9), time(10), time(10), time(11)))  # back to back is fine
        self.assertFalse(rules.overlaps(time(11), time(12), time(9), time(10)))


def _slot(label, start, end):
    return {"label": label, "start": start, "end": end}


def _existing(sid, day, start, end, in_use=False):
    return {"id": sid, "day": day, "start": start, "end": end, "in_use": in_use}


class PlanSlotCreationTests(unittest.TestCase):
    WANTED = [_slot("Period 1", time(9, 0), time(9, 40)), _slot("Period 2", time(9, 40), time(10, 20))]

    def plan(self, days, existing, mode=rules.MODE_ADD_MISSING):
        return rules.plan_slot_creation(days, self.WANTED, existing, mode=mode)

    def test_empty_campus_gets_every_slot_on_every_day(self):
        p = self.plan([1, 2], [])
        self.assertEqual(len(p["create"]), 4)
        self.assertEqual(p["delete"], [])
        self.assertEqual(p["already_there"], 0)
        self.assertEqual(p["conflicts"], [])
        self.assertEqual({c["day"] for c in p["create"]}, {1, 2})

    def test_identical_slot_is_counted_not_duplicated(self):
        p = self.plan([1, 2], [_existing(1, 1, time(9, 0), time(9, 40))])
        self.assertEqual(p["already_there"], 1)
        self.assertEqual(len(p["create"]), 3)
        self.assertEqual([c["label"] for c in p["create"] if c["day"] == 1], ["Period 2"])

    def test_clash_blocks_the_whole_day_but_not_other_days(self):
        p = self.plan([1, 2], [_existing(1, 1, time(9, 30), time(10, 0))])
        self.assertEqual(len(p["conflicts"]), 2)
        self.assertEqual({c["day"] for c in p["conflicts"]}, {1})
        self.assertEqual({c["day"] for c in p["create"]}, {2})
        self.assertEqual(len(p["create"]), 2)
        self.assertIn("(09:30-10:00).", p["conflicts"][0]["message"])
        self.assertNotIn("used in the timetable", p["conflicts"][0]["message"])

    def test_clash_with_a_used_slot_says_so(self):
        p = self.plan([1], [_existing(1, 1, time(9, 30), time(10, 0), in_use=True)])
        self.assertTrue(p["conflicts"][0]["message"].endswith("that is used in the timetable."))

    def test_partial_conflict_still_creates_nothing_for_that_day(self):
        # only Period 2 clashes (09:50-10:10) — Period 1 must not be created either (all or nothing)
        p = self.plan([1], [_existing(1, 1, time(9, 50), time(10, 10))])
        self.assertEqual(len(p["conflicts"]), 1)
        self.assertEqual(p["create"], [])

    def test_replace_unused_deletes_free_slots_then_creates(self):
        p = self.plan([1], [_existing(7, 1, time(9, 30), time(10, 0), in_use=False)], mode=rules.MODE_REPLACE_UNUSED)
        self.assertEqual(p["delete"], [7])
        self.assertEqual(len(p["create"]), 2)
        self.assertEqual(p["conflicts"], [])

    def test_replace_unused_never_deletes_a_used_slot(self):
        p = self.plan([1], [_existing(7, 1, time(9, 30), time(10, 0), in_use=True)], mode=rules.MODE_REPLACE_UNUSED)
        self.assertEqual(p["delete"], [])
        self.assertEqual(p["create"], [])
        self.assertEqual(len(p["conflicts"]), 2)

    def test_replace_unused_recreates_identical_free_slot(self):
        p = self.plan([1], [_existing(7, 1, time(9, 0), time(9, 40))], mode=rules.MODE_REPLACE_UNUSED)
        self.assertEqual(p["delete"], [7])
        self.assertEqual(len(p["create"]), 2)
        self.assertEqual(p["already_there"], 0)

    def test_replace_unused_keeps_used_identical_slot(self):
        p = self.plan([1], [_existing(7, 1, time(9, 0), time(9, 40), in_use=True)], mode=rules.MODE_REPLACE_UNUSED)
        self.assertEqual(p["delete"], [])
        self.assertEqual(p["already_there"], 1)
        self.assertEqual([c["label"] for c in p["create"]], ["Period 2"])

    def test_other_days_slots_are_ignored(self):
        p = self.plan([1], [_existing(7, 2, time(9, 30), time(10, 0))])
        self.assertEqual(len(p["create"]), 2)
        self.assertEqual(p["conflicts"], [])

    def test_add_missing_never_deletes(self):
        p = self.plan([1], [_existing(7, 1, time(12, 0), time(12, 40))])
        self.assertEqual(p["delete"], [])
        self.assertEqual(len(p["create"]), 2)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            self.plan([1], [], mode="wipe")

    def test_no_days_means_empty_plan(self):
        p = self.plan([], [])
        self.assertEqual((p["create"], p["delete"], p["conflicts"], p["already_there"]), ([], [], [], 0))


class SlotSummaryTests(unittest.TestCase):
    def test_counts_per_day(self):
        self.assertEqual(rules.slot_summary([{"day": 1}, {"day": 1}, {"day": 3}]), {1: 2, 3: 1})
        self.assertEqual(rules.slot_summary([]), {})


# ===========================================================================
# Grading maths (campus/grading.py)
# ===========================================================================
class ValidateGradeScaleTests(unittest.TestCase):
    def test_default_scale_is_valid_and_sorted_high_first(self):
        clean, err = grading.validate_grade_scale(list(reversed(grading.DEFAULT_GRADE_SCALE)))
        self.assertIsNone(err)
        self.assertEqual(clean[0]["grade"], "A1")
        self.assertEqual(clean[-1], {"min_percent": 0, "grade": "E"})

    def test_every_preset_is_valid_for_its_own_system(self):
        for key, preset in grading.PRESETS.items():
            clean, err = grading.validate_grade_scale(
                preset["bands"], require_points=preset["system"] == grading.SYSTEM_GPA)
            self.assertIsNone(err, key)
            self.assertEqual(len(clean), len(preset["bands"]), key)
        self.assertEqual({p["system"] for p in grading.PRESETS.values()}, {"cbse", "gpa"})

    def test_must_be_a_non_empty_list(self):
        for bad in (None, [], "A", {}):
            self.assertIsNotNone(grading.validate_grade_scale(bad)[1], repr(bad))

    def test_lowest_band_must_start_at_zero(self):
        _, err = grading.validate_grade_scale([{"min_percent": 40, "grade": "P"}])
        self.assertIn("start at 0", err)

    def test_duplicates_and_ranges(self):
        base = {"min_percent": 0, "grade": "F"}
        self.assertIn("same min_percent", grading.validate_grade_scale([base, {"min_percent": 0, "grade": "G"}])[1])
        self.assertIn("same grade", grading.validate_grade_scale([base, {"min_percent": 50, "grade": "f"}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([base, {"min_percent": 101, "grade": "A"}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([base, {"min_percent": -1, "grade": "A"}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([base, {"min_percent": 50, "grade": ""}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([base, {"min_percent": 50, "grade": "x" * 11}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([base, "A"])[1])

    def test_band_count_limit(self):
        many = [{"min_percent": i, "grade": f"G{i}"} for i in range(grading.MAX_BANDS + 1)]
        self.assertIn("At most", grading.validate_grade_scale(many)[1])

    def test_points_optional_unless_gpa(self):
        scale = [{"min_percent": 50, "grade": "A"}, {"min_percent": 0, "grade": "B"}]
        self.assertIsNone(grading.validate_grade_scale(scale)[1])
        _, err = grading.validate_grade_scale(scale, require_points=True)
        self.assertIn("needs grade points", err)

    def test_points_range_and_order(self):
        low = {"min_percent": 0, "grade": "F", "points": 0}
        self.assertIsNotNone(grading.validate_grade_scale([low, {"min_percent": 50, "grade": "A", "points": 11}])[1])
        self.assertIsNotNone(grading.validate_grade_scale([low, {"min_percent": 50, "grade": "A", "points": -1}])[1])
        _, err = grading.validate_grade_scale(
            [{"min_percent": 0, "grade": "F", "points": 8}, {"min_percent": 50, "grade": "A", "points": 5}],
            require_points=True)
        self.assertIn("fewer grade points", err)
        clean, err = grading.validate_grade_scale([low, {"min_percent": 50, "grade": "A", "points": "9.5"}])
        self.assertIsNone(err)
        self.assertEqual(clean[0]["points"], 9.5)

    def test_numbers_are_plain(self):
        clean, _ = grading.validate_grade_scale([{"min_percent": "33.0", "grade": "D"}, {"min_percent": "0", "grade": "E"}])
        self.assertEqual(clean[0]["min_percent"], 33)
        self.assertIsInstance(clean[0]["min_percent"], int)


class GradeLookupTests(unittest.TestCase):
    def test_boundaries_on_default_scale(self):
        cases = {100: "A1", 91: "A1", 90.99: "A2", 81: "A2", 71: "B1", 61: "B2", 51: "C1", 41: "C2",
                 33: "D", 32.99: "E", 0: "E"}
        for pct, grade in cases.items():
            self.assertEqual(grading.grade_for_percent(pct), grade, pct)

    def test_none_percent_has_no_grade(self):
        self.assertIsNone(grading.grade_for_percent(None))
        self.assertIsNone(grading.grade_for_marks("abc", 50))
        self.assertIsNone(grading.grade_for_marks(10, 0))

    def test_grade_for_marks(self):
        self.assertEqual(grading.grade_for_marks(45, 50), "A2")  # 90 %
        self.assertEqual(grading.grade_for_marks("46", "50"), "A1")  # 92 %

    def test_custom_scale_and_unsorted_input(self):
        scale = [{"min_percent": 0, "grade": "Fail"}, {"min_percent": 50, "grade": "Pass"}]
        self.assertEqual(grading.grade_for_percent(49, scale), "Fail")
        self.assertEqual(grading.grade_for_percent(50, scale), "Pass")

    def test_broken_stored_scale_falls_back_to_default(self):
        self.assertEqual(grading.grade_for_percent(95, [{"nonsense": True}]), "A1")
        self.assertEqual(grading.get_scale(None), grading.DEFAULT_GRADE_SCALE)

    def test_percent_only_never_has_a_grade_or_point(self):
        self.assertIsNone(grading.grade_for_percent(95, grading.PERCENT_ONLY))
        self.assertIsNone(grading.grade_for_marks(45, 50, grading.PERCENT_ONLY))
        self.assertIsNone(grading.grade_point_for_percent(95, grading.PERCENT_ONLY))
        self.assertEqual(grading.get_scale(grading.PERCENT_ONLY), [])

    def test_percent_of(self):
        self.assertEqual(float(grading.percent_of(45, 50)), 90.0)
        self.assertIsNone(grading.percent_of(1, 0))
        self.assertIsNone(grading.percent_of(None, 10))


class CampusScaleTests(unittest.TestCase):
    BANDS = [{"min_percent": 0, "grade": "E"}]

    def test_percent_campus_gets_the_marker(self):
        c = SimpleNamespace(grading_system="percent", grade_scale=self.BANDS)
        self.assertIs(grading.campus_scale(c), grading.PERCENT_ONLY)
        self.assertFalse(grading.uses_points(c))

    def test_cbse_and_gpa_campus_get_their_bands(self):
        for system in ("cbse", "gpa"):
            c = SimpleNamespace(grading_system=system, grade_scale=self.BANDS)
            self.assertEqual(grading.campus_scale(c), self.BANDS)
        self.assertTrue(grading.uses_points(SimpleNamespace(grading_system="gpa")))
        self.assertFalse(grading.uses_points(SimpleNamespace(grading_system="cbse")))

    def test_old_object_without_the_field_is_cbse(self):
        c = SimpleNamespace(grade_scale=self.BANDS)
        self.assertEqual(grading.campus_scale(c), self.BANDS)


class GradePointTests(unittest.TestCase):
    GPA4 = grading.PRESETS["gpa4"]["bands"]
    GPA10 = grading.PRESETS["gpa10"]["bands"]

    def test_band_points_win(self):
        self.assertEqual(grading.grade_point_for_percent(95, self.GPA4), 4)
        self.assertEqual(grading.grade_point_for_percent(85, self.GPA4), 3)
        self.assertEqual(grading.grade_point_for_percent(10, self.GPA4), 0)
        self.assertEqual(grading.grade_point_for_percent(95, self.GPA10), 10)

    def test_cbse_table_is_used_when_a_band_has_no_points(self):
        self.assertEqual(grading.grade_point_for_percent(95), 10)  # default scale, A1
        self.assertEqual(grading.grade_point_for_percent(35), 4)   # D
        self.assertEqual(grading.grade_point_for_percent(5), 0)    # E

    def test_unknown_label_without_points_has_no_point(self):
        scale = [{"min_percent": 0, "grade": "Weird"}]
        self.assertIsNone(grading.grade_point_for_percent(50, scale))

    def test_none_percent(self):
        self.assertIsNone(grading.grade_point_for_percent(None, self.GPA4))

    def test_gpa_is_the_average_of_subject_points(self):
        self.assertEqual(grading.gpa_of([95, 85], self.GPA4), 3.5)
        self.assertEqual(grading.gpa_of([95, 85, None], self.GPA4), 3.5)  # None ignored
        self.assertEqual(grading.gpa_of([95, 85, 75], self.GPA4), 3.0)

    def test_gpa_rounds_to_two_places(self):
        self.assertEqual(grading.gpa_of([95, 85, 85], self.GPA4), 3.33)

    def test_gpa_of_nothing_is_none(self):
        self.assertIsNone(grading.gpa_of([]))
        self.assertIsNone(grading.gpa_of([None, None], self.GPA4))
        self.assertIsNone(grading.gpa_of([90], grading.PERCENT_ONLY))


class IsPassTests(unittest.TestCase):
    def test_default_pass_mark_is_33(self):
        self.assertTrue(grading.is_pass(33))
        self.assertFalse(grading.is_pass(32.9))

    def test_custom_pass_mark(self):
        self.assertFalse(grading.is_pass(39, 40))
        self.assertTrue(grading.is_pass(40, 40))
        self.assertTrue(grading.is_pass(60, "60"))

    def test_none_never_passes(self):
        self.assertFalse(grading.is_pass(None))
        self.assertFalse(grading.is_pass("abc"))


class LogoRulesTests(unittest.TestCase):
    PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 12
    WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 "

    def test_type_comes_from_the_bytes(self):
        self.assertEqual(rules.sniff_image(self.PNG), "png")
        self.assertEqual(rules.sniff_image(self.JPG), "jpg")
        self.assertEqual(rules.sniff_image(self.WEBP), "webp")
        self.assertIsNone(rules.sniff_image(b"GIF89a......"))
        self.assertIsNone(rules.sniff_image(b"%PDF-1.7"))
        self.assertIsNone(rules.sniff_image(b""))
        self.assertIsNone(rules.sniff_image(None))

    def test_riff_that_is_not_webp_is_refused(self):
        self.assertIsNone(rules.sniff_image(b"RIFF\x10\x00\x00\x00WAVEfmt "))

    def test_good_upload(self):
        self.assertEqual(rules.clean_logo_upload(1000, self.PNG), ("png", None))

    def test_empty_and_huge_uploads_are_refused(self):
        self.assertIsNotNone(rules.clean_logo_upload(0, self.PNG)[1])
        self.assertIsNotNone(rules.clean_logo_upload(rules.MAX_LOGO_BYTES + 1, self.PNG)[1])
        self.assertIsNone(rules.clean_logo_upload(rules.MAX_LOGO_BYTES, self.PNG)[1])

    def test_not_an_image_and_bad_size(self):
        ext, err = rules.clean_logo_upload(100, b"<svg xmlns=")
        self.assertIsNone(ext)
        self.assertIn("PNG", err)
        self.assertIsNotNone(rules.clean_logo_upload("abc", self.PNG)[1])
        self.assertIsNotNone(rules.clean_logo_upload(None, self.PNG)[1])


if __name__ == "__main__":
    unittest.main()
