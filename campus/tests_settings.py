# campus/tests_settings.py
"""
Campus Settings API tests: overview, school profile, grading (CBSE / percentage / GPA), working days + calendar,
holidays (range / bulk / copy-year / national), timetable slots (generate / copy-day / clear), permissions, audit.

Run: `python manage.py test campus.tests_settings`
(The pure rules have their own DB-free file: `campus/tests_settings_rules.py`.)

Written against the code only — NOT RUN when written (no Django in that environment).
"""
import shutil
import tempfile
from datetime import date, time

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Campus,
    CampusAuditLog,
    CampusHoliday,
    CampusProfile,
    ExamTerm,
    ResultEntry,
    StaffProfile,
    TimeSlot,
    TimetableEntry,
)
from .serializers import ResultEntrySerializer
from .tests_t4_efg import T4Base

User = get_user_model()


class SettingsBase(T4Base):
    """T4Base gives: campus (admin = ADMIN), teacher (subject teacher), students sa1/sa2/sb, parent (linked to sa1)."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.principal = User.objects.create_user(username="principal", password="pass12345")
        StaffProfile.objects.create(campus=self.campus, user=self.principal, role=StaffProfile.Role.PRINCIPAL_HOD)
        self.outsider = User.objects.create_user(username="outsider", password="pass12345")
        self.other_admin = User.objects.create_user(username="other_admin", password="pass12345")
        self.other_campus = Campus.objects.create(name="Other School", created_by=self.other_admin)
        StaffProfile.objects.create(campus=self.other_campus, user=self.other_admin, role=StaffProfile.Role.ADMIN)

    # ---- helpers
    def u(self, name):
        return reverse(f"campus-settings-{name}", args=[str(self.campus.id)])

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def get(self, name, user=None, **params):
        return self.as_(user or self.admin).get(self.u(name), params)

    def put(self, name, data, user=None):
        return self.as_(user or self.admin).put(self.u(name), data, format="json")

    def post(self, name, data, user=None):
        return self.as_(user or self.admin).post(self.u(name), data, format="json")

    def actions(self):
        return set(CampusAuditLog.objects.filter(campus=self.campus).values_list("action", flat=True))

    def refresh(self):
        self.campus.refresh_from_db()
        return self.campus

    def set_working(self, off=(7,), half=()):
        self.campus.weekly_off_days, self.campus.half_day_weekdays = list(off), list(half)
        self.campus.save(update_fields=["weekly_off_days", "half_day_weekdays"])

    def holiday(self, d, name="Holiday", campus=None):
        return CampusHoliday.objects.create(campus=campus or self.campus, date=d, name=name)

    def slot(self, day, start, end, label="Manual"):
        return TimeSlot.objects.create(campus=self.campus, day_of_week=day, start_time=start, end_time=end, label=label)

    def holiday_dates(self):
        return set(CampusHoliday.objects.filter(campus=self.campus).values_list("date", flat=True))


# ===========================================================================
# 1. ACCESS — who can read, who can write
# ===========================================================================
class AccessTests(SettingsBase):
    READ = ("overview", "profile", "grading", "working-days", "calendar", "holidays", "time-slots")

    def test_every_member_can_read_everything(self):
        for user in (self.admin, self.principal, self.teacher, self.sa1, self.parent):
            for name in self.READ:
                self.assertEqual(self.get(name, user).status_code, 200, f"{user.username} {name}")

    def test_outsider_and_other_campus_admin_get_404(self):
        for user in (self.outsider, self.other_admin):
            for name in self.READ:
                self.assertEqual(self.get(name, user).status_code, 404, f"{user.username} {name}")
            self.assertEqual(self.put("profile", {"city": "X"}, user).status_code, 404)

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.u("overview")).status_code, (401, 403))

    def test_unknown_or_malformed_campus_id_is_404(self):
        self.as_(self.admin)
        for bad in ("123e4567-e89b-12d3-a456-426614174000", "not-a-uuid"):
            url = reverse("campus-settings-overview", args=[bad])
            self.assertEqual(self.client.get(url).status_code, 404, bad)

    def test_only_admin_and_principal_can_write(self):
        writes = [
            ("put", "profile", {"city": "Pune"}),
            ("put", "grading", {"system": "percent"}),
            ("put", "working-days", {"weekly_off_days": [7]}),
            ("post", "holidays-range", {"date_from": "2026-10-20", "date_to": "2026-10-21", "name": "x"}),
            ("post", "holidays-bulk", {"items": [{"date": "2026-10-20", "name": "x"}]}),
            ("post", "holidays-copy-year", {"from_year": 2026, "to_year": 2027}),
            ("post", "holidays-national", {"year": 2026}),
            ("post", "slots-generate", {"start_time": "09:00", "period_minutes": 40, "periods": 2}),
            ("post", "slots-copy-day", {"from_day": 1, "to_days": [2]}),
            ("post", "slots-clear", {}),
        ]
        for user in (self.teacher, self.sa1, self.parent):
            for method, name, body in writes:
                resp = getattr(self, method)(name, body, user)
                self.assertEqual(resp.status_code, 403, f"{user.username} {name}")
        self.assertFalse(CampusProfile.objects.filter(campus=self.campus).exists())
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 0)

    def test_principal_can_write(self):
        self.assertEqual(self.put("profile", {"city": "Pune"}, self.principal).status_code, 200)
        self.assertEqual(self.put("grading", {"system": "percent"}, self.principal).status_code, 200)

    def test_overview_tells_the_app_whether_to_show_edit_buttons(self):
        self.assertTrue(self.get("overview", self.admin).data["can_manage"])
        self.assertTrue(self.get("overview", self.principal).data["can_manage"])
        self.assertFalse(self.get("overview", self.teacher).data["can_manage"])
        self.assertFalse(self.get("overview", self.sa1).data["can_manage"])


# ===========================================================================
# 2. OVERVIEW
# ===========================================================================
class OverviewTests(SettingsBase):
    def test_fresh_campus_has_an_empty_checklist(self):
        data = self.get("overview").data
        self.assertEqual(data["campus"]["name"], "GV")
        self.assertEqual([c["key"] for c in data["checklist"]],
                         ["profile", "grading", "working_days", "holidays", "time_slots"])
        self.assertFalse(any(c["done"] for c in data["checklist"]))
        self.assertEqual(data["grading"]["system"], "cbse")
        self.assertTrue(data["grading"]["is_default"])
        self.assertEqual(data["grading"]["bands"], 8)
        self.assertEqual(data["holidays"]["count"], 0)
        self.assertIsNone(data["holidays"]["next"])
        self.assertEqual(data["time_slots"]["total"], 0)

    def test_checklist_fills_up_as_the_admin_sets_things(self):
        self.put("profile", {"board": "cbse", "city": "Pune", "phone": "+91 98765 43210"})
        self.put("grading", {"system": "percent"})
        self.put("working-days", {"weekly_off_days": [7]})
        today = timezone.localdate()
        self.holiday(today, "Today off")
        self.post("slots-generate", {"start_time": "09:00", "period_minutes": 40, "periods": 2})
        data = self.get("overview").data
        self.assertEqual({c["key"]: c["done"] for c in data["checklist"]},
                         {"profile": True, "grading": True, "working_days": True, "holidays": True, "time_slots": True})
        self.assertEqual(data["profile"]["city"], "Pune")
        self.assertEqual(data["holidays"]["next"], {"date": today.isoformat(), "name": "Today off"})
        self.assertEqual(data["time_slots"]["total"], 12)  # 6 working days x 2 periods
        self.assertEqual(data["time_slots"]["per_day"]["1"], 2)
        self.assertNotIn("7", data["time_slots"]["per_day"])

    def test_profile_needs_board_address_and_phone_to_count(self):
        self.put("profile", {"board": "cbse", "city": "Pune"})  # no phone
        done = {c["key"]: c["done"] for c in self.get("overview").data["checklist"]}
        self.assertFalse(done["profile"])

    def test_slots_step_needs_every_working_day(self):
        self.set_working([7])
        self.post("slots-generate", {"start_time": "09:00", "period_minutes": 40, "periods": 2, "days": [1, 2]})
        done = {c["key"]: c["done"] for c in self.get("overview").data["checklist"]}
        self.assertFalse(done["time_slots"])

    def test_other_campus_rows_are_not_counted(self):
        self.holiday(timezone.localdate(), "elsewhere", campus=self.other_campus)
        self.assertEqual(self.get("overview").data["holidays"]["count"], 0)


# ===========================================================================
# 3. SCHOOL PROFILE
# ===========================================================================
class ProfileTests(SettingsBase):
    def test_empty_profile(self):
        data = self.get("profile").data
        self.assertEqual(data["name"], "GV")
        self.assertEqual(data["city"], "")
        self.assertIsNone(data["established_year"])
        self.assertIn("cbse", data["boards"])
        self.assertIn("type", data)

    def test_save_and_read_back(self):
        resp = self.put("profile", {
            "board": "CBSE", "city": "Pune", "state": "Maharashtra", "phone": "+91 98765 43210",
            "principal_name": "Dr. Rao", "established_year": 1990, "website": "school.edu", "email": "Office@School.edu",
            "udise_code": "12345678901", "pincode": "411001", "affiliation_no": "1130123",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["board"], "cbse")
        self.assertEqual(resp.data["website"], "https://school.edu")
        self.assertEqual(resp.data["email"], "office@school.edu")
        again = self.get("profile").data
        self.assertEqual((again["city"], again["established_year"], again["udise_code"]), ("Pune", 1990, "12345678901"))
        self.assertIn("settings.profile.update", self.actions())

    def test_patch_works_and_only_sent_keys_change(self):
        self.put("profile", {"city": "Pune", "state": "MH"})
        resp = self.as_(self.admin).patch(self.u("profile"), {"city": "Mumbai"}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.data["city"], resp.data["state"]), ("Mumbai", "MH"))

    def test_blank_clears_a_field(self):
        self.put("profile", {"tagline": "Learn well"})
        self.assertEqual(self.put("profile", {"tagline": ""}).data["tagline"], "")

    def test_year_can_be_cleared(self):
        self.put("profile", {"established_year": 1990})
        self.assertIsNone(self.put("profile", {"established_year": ""}).data["established_year"])

    def test_school_name_is_changed_on_the_campus_itself(self):
        resp = self.put("profile", {"name": "  Green   Valley  "})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.refresh().name, "Green Valley")
        self.assertEqual(resp.data["name"], "Green Valley")
        self.assertFalse(CampusProfile.objects.filter(campus=self.campus).exists())  # name-only save needs no profile row

    def test_saving_twice_keeps_one_row(self):
        self.put("profile", {"city": "A"})
        self.put("profile", {"city": "B"})
        self.assertEqual(CampusProfile.objects.filter(campus=self.campus).count(), 1)

    def test_validation_errors_name_the_field(self):
        for body, field in (
            ({"phone": "abc"}, "phone"),
            ({"email": "nope"}, "email"),
            ({"board": "x"}, "board"),
            ({"udise_code": "123"}, "udise_code"),
            ({"established_year": 1700}, "established_year"),
            ({"name": "  "}, "name"),
            ({"website": "localhost"}, "website"),
            ({"pincode": "!"}, "pincode"),
        ):
            resp = self.put("profile", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_profile", body)
            self.assertTrue(resp.data["detail"].startswith(f"{field}:"), (body, resp.data["detail"]))
        self.assertFalse(CampusProfile.objects.filter(campus=self.campus).exists())
        self.assertEqual(self.refresh().name, "GV")

    def test_one_bad_field_saves_nothing(self):
        resp = self.put("profile", {"city": "Pune", "phone": "abc"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(CampusProfile.objects.filter(campus=self.campus).exists())

    def test_empty_body_is_refused(self):
        resp = self.put("profile", {})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "nothing_to_save")

    def test_other_campus_profile_is_untouched(self):
        CampusProfile.objects.create(campus=self.other_campus, city="Delhi")
        self.put("profile", {"city": "Pune"})
        self.assertEqual(CampusProfile.objects.get(campus=self.other_campus).city, "Delhi")


# ===========================================================================
# 4. GRADING
# ===========================================================================
class GradingTests(SettingsBase):
    CUSTOM = [{"min_percent": 0, "grade": "Fail"}, {"min_percent": 50, "grade": "Pass"}]

    def test_default_is_cbse(self):
        data = self.get("grading").data
        self.assertEqual(data["system"], "cbse")
        self.assertTrue(data["is_default"])
        self.assertEqual(len(data["grade_scale"]), 8)
        self.assertEqual(data["pass_percent"], 33.0)
        self.assertEqual({p["key"] for p in data["presets"]}, {"cbse", "gpa10", "gpa4"})
        self.assertEqual(set(data["systems"]), {"cbse", "percent", "gpa"})

    def test_percentage_only_keeps_the_stored_bands(self):
        self.put("grading", {"system": "cbse", "grade_scale": self.CUSTOM})
        resp = self.put("grading", {"system": "percent"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["system"], "percent")
        self.assertEqual([b["grade"] for b in resp.data["grade_scale"]], ["Pass", "Fail"])
        back = self.put("grading", {"system": "cbse"})
        self.assertEqual(back.status_code, 200)
        self.assertEqual([b["grade"] for b in back.data["grade_scale"]], ["Pass", "Fail"])

    def test_percentage_only_on_a_default_campus_stores_no_bands(self):
        self.put("grading", {"system": "percent"})
        self.assertEqual(self.refresh().grade_scale, [])
        self.assertTrue(self.get("grading").data["is_default"])

    def test_custom_cbse_bands_are_sorted_high_first(self):
        resp = self.put("grading", {"system": "cbse", "grade_scale": self.CUSTOM, "pass_percent": 50})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["grade_scale"][0], {"min_percent": 50, "grade": "Pass"})
        self.assertEqual(resp.data["pass_percent"], 50.0)
        self.assertFalse(resp.data["is_default"])

    def test_preset_loads_bands_pass_mark_and_system(self):
        resp = self.put("grading", {"preset": "gpa10"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["system"], "gpa")
        self.assertEqual(resp.data["pass_percent"], 40.0)
        self.assertEqual(len(resp.data["grade_scale"]), 7)
        self.assertEqual(resp.data["grade_scale"][0], {"min_percent": 90, "grade": "O", "points": 10})

    def test_preset_with_own_pass_mark_and_bands_on_top(self):
        resp = self.put("grading", {"preset": "gpa4", "pass_percent": 55})
        self.assertEqual(resp.data["pass_percent"], 55.0)
        bands = [{"min_percent": 0, "grade": "F", "points": 0}, {"min_percent": 60, "grade": "P", "points": 2}]
        resp = self.put("grading", {"preset": "gpa4", "grade_scale": bands})
        self.assertEqual([b["grade"] for b in resp.data["grade_scale"]], ["P", "F"])  # sent bands win over the preset

    def test_cbse_preset(self):
        resp = self.put("grading", {"preset": "cbse"})
        self.assertEqual(resp.data["system"], "cbse")
        self.assertEqual(resp.data["pass_percent"], 33.0)
        self.assertEqual(resp.data["grade_scale"][0]["points"], 10)

    def test_gpa_on_a_default_campus_gets_cbse_points(self):
        resp = self.put("grading", {"system": "gpa"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["grade_scale"][0], {"min_percent": 91, "grade": "A1", "points": 10})
        self.assertEqual(resp.data["grade_scale"][-1], {"min_percent": 0, "grade": "E", "points": 0})

    def test_gpa_needs_points_on_every_band(self):
        resp = self.put("grading", {"system": "gpa", "grade_scale": self.CUSTOM})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_grade_scale")
        self.assertEqual(self.refresh().grading_system, "cbse")

    def test_invalid_bands(self):
        for bands in (
            [{"min_percent": 40, "grade": "P"}],                                   # no band at 0
            [{"min_percent": 0, "grade": "A"}, {"min_percent": 0, "grade": "B"}],  # same start
            [{"min_percent": 0, "grade": "A"}, {"min_percent": 50, "grade": "a"}],  # same label
            [],
        ):
            resp = self.put("grading", {"system": "cbse", "grade_scale": bands})
            self.assertEqual(resp.status_code, 400, bands)
            self.assertEqual(resp.data["code"], "invalid_grade_scale", bands)

    def test_bad_requests(self):
        for body, code in (
            ({}, "invalid_grading"),
            ({"system": "x"}, "invalid_grading"),
            ({"preset": "x"}, "invalid_grading"),
            ({"preset": "gpa4", "system": "cbse"}, "invalid_grading"),
            ({"system": "cbse", "grade_scale": "A1"}, "invalid_grading"),
            ({"system": "cbse", "pass_percent": 101}, "invalid_pass_percent"),
            ({"system": "cbse", "pass_percent": -1}, "invalid_pass_percent"),
            ({"system": "cbse", "pass_percent": "abc"}, "invalid_pass_percent"),
        ):
            resp = self.put("grading", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], code, body)

    def test_a_rejected_request_changes_nothing(self):
        self.put("grading", {"system": "percent", "pass_percent": 101})
        campus = self.refresh()
        self.assertEqual(campus.grading_system, "cbse")
        self.assertEqual(float(campus.pass_percent), 33.0)

    def test_audit_row(self):
        self.put("grading", {"preset": "gpa4"})
        row = CampusAuditLog.objects.filter(campus=self.campus, action="settings.grading.update").first()
        self.assertIsNotNone(row)
        self.assertEqual(row.metadata.get("system"), "gpa")
        self.assertEqual(row.metadata.get("preset"), "gpa4")

    # ---- the saved system really changes what a result shows -------------------------------------------------------
    def _entry(self, marks, max_marks=100):
        term = ExamTerm.objects.create(session=self.session, name="Mid", start_date=date(2026, 9, 1), end_date=date(2026, 9, 10))
        return ResultEntry.objects.create(enrollment=self.e_a1, subject=self.math, exam_term=term,
                                          marks_obtained=marks, max_marks=max_marks)

    def _shown(self, entry):
        return ResultEntrySerializer(ResultEntry.objects.get(pk=entry.pk)).data

    def test_result_shows_letter_grade_on_cbse(self):
        shown = self._shown(self._entry(95))
        self.assertEqual(shown["grade"], "A1")
        self.assertIsNone(shown["grade_point"])
        self.assertTrue(shown["passed"])

    def test_result_has_no_letter_grade_on_a_percentage_campus(self):
        entry = self._entry(95)
        self.put("grading", {"system": "percent"})
        shown = self._shown(entry)
        self.assertIsNone(shown["grade"])
        self.assertIsNone(shown["grade_point"])
        self.assertTrue(shown["passed"])  # pass / fail still works on the pass mark

    def test_result_has_a_grade_point_on_a_gpa_campus(self):
        entry = self._entry(85)
        self.put("grading", {"preset": "gpa4"})
        shown = self._shown(entry)
        self.assertEqual(shown["grade"], "B")
        self.assertEqual(shown["grade_point"], 3)

    def test_pass_mark_change_flips_passed(self):
        entry = self._entry(45)
        self.assertTrue(self._shown(entry)["passed"])
        self.put("grading", {"system": "cbse", "pass_percent": 50})
        self.assertFalse(self._shown(entry)["passed"])


# ===========================================================================
# 5. WORKING DAYS + CALENDAR
# ===========================================================================
class WorkingDaysTests(SettingsBase):
    def test_default(self):
        data = self.get("working-days").data
        self.assertEqual(data["weekly_off_days"], [])
        self.assertEqual(data["half_day_weekdays"], [])
        self.assertEqual(data["working_weekdays"], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(len(data["weekday_names"]), 7)

    def test_save(self):
        resp = self.put("working-days", {"weekly_off_days": [7, 7], "half_day_weekdays": [6]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["weekly_off_days"], [7])
        self.assertEqual(resp.data["half_day_weekdays"], [6])
        self.assertEqual(resp.data["working_weekdays"], [1, 2, 3, 4, 5, 6])
        campus = self.refresh()
        self.assertEqual((campus.weekly_off_days, campus.half_day_weekdays), ([7], [6]))
        self.assertIn("settings.working_days.update", self.actions())

    def test_a_key_left_out_is_kept(self):
        self.put("working-days", {"weekly_off_days": [7], "half_day_weekdays": [6]})
        resp = self.put("working-days", {"half_day_weekdays": [5]})
        self.assertEqual((resp.data["weekly_off_days"], resp.data["half_day_weekdays"]), ([7], [5]))
        resp = self.put("working-days", {"weekly_off_days": []})
        self.assertEqual((resp.data["weekly_off_days"], resp.data["half_day_weekdays"]), ([], [5]))

    def test_same_day_cannot_be_off_and_half(self):
        resp = self.put("working-days", {"weekly_off_days": [7], "half_day_weekdays": [7]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_working_days")

    def test_new_off_day_cannot_clash_with_stored_half_day(self):
        self.put("working-days", {"half_day_weekdays": [6]})
        resp = self.put("working-days", {"weekly_off_days": [6]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.refresh().weekly_off_days, [])

    def test_one_full_day_must_remain(self):
        resp = self.put("working-days", {"weekly_off_days": [1, 2, 3, 4, 5, 6], "half_day_weekdays": [7]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_working_days")

    def test_junk_is_refused(self):
        for body in ({"weekly_off_days": [0]}, {"weekly_off_days": [8]}, {"weekly_off_days": "7"},
                     {"half_day_weekdays": ["x"]}, {"weekly_off_days": [True]}):
            resp = self.put("working-days", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_working_days", body)

    def test_not_an_object(self):
        resp = self.as_(self.admin).put(self.u("working-days"), [7], format="json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_working_days")


class CalendarTests(SettingsBase):
    def test_month_with_off_half_and_holiday(self):
        self.set_working([7], [6])
        self.holiday(date(2026, 10, 2), "Gandhi Jayanti")
        resp = self.get("calendar", month="2026-10")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.data["year"], resp.data["month"]), (2026, 10))
        self.assertEqual(len(resp.data["days"]), 31)
        self.assertEqual(resp.data["summary"], {
            "working_days": 21, "half_days": 5, "weekly_offs": 4, "holidays": 1, "working_day_value": 23.5,
        })
        by_date = {d["date"]: d for d in resp.data["days"]}
        self.assertEqual(by_date["2026-10-02"]["kind"], "holiday")
        self.assertEqual(by_date["2026-10-02"]["holiday"], "Gandhi Jayanti")
        self.assertEqual(by_date["2026-10-04"]["kind"], "weekly_off")
        self.assertEqual(by_date["2026-10-03"]["kind"], "half_day")
        self.assertEqual(by_date["2026-10-05"]["kind"], "working")

    def test_defaults_to_this_month(self):
        today = timezone.localdate()
        resp = self.get("calendar")
        self.assertEqual((resp.data["year"], resp.data["month"]), (today.year, today.month))

    def test_bad_month(self):
        for bad in ("abc", "2026-13", "1800-01"):
            resp = self.get("calendar", month=bad)
            self.assertEqual(resp.status_code, 400, bad)
            self.assertEqual(resp.data["code"], "invalid_month", bad)

    def test_only_this_campus_holidays_show(self):
        self.holiday(date(2026, 10, 5), "elsewhere", campus=self.other_campus)
        self.assertEqual(self.get("calendar", month="2026-10").data["summary"]["holidays"], 0)

    def test_a_student_sees_the_same_calendar(self):
        self.set_working([7])
        self.assertEqual(self.get("calendar", self.sa1, month="2026-10").data["summary"]["weekly_offs"], 4)


# ===========================================================================
# 6. HOLIDAYS
# ===========================================================================
class HolidayListTests(SettingsBase):
    def test_year_list_is_sorted_and_scoped(self):
        self.holiday(date(2026, 10, 2), "Gandhi Jayanti")
        self.holiday(date(2026, 1, 26), "Republic Day")
        self.holiday(date(2027, 1, 26), "Next year")
        self.holiday(date(2026, 5, 1), "elsewhere", campus=self.other_campus)
        resp = self.get("holidays", year=2026)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["year"], 2026)
        self.assertEqual([r["name"] for r in resp.data["results"]], ["Republic Day", "Gandhi Jayanti"])
        first = resp.data["results"][0]
        self.assertEqual((first["date"], first["weekday"]), ("2026-01-26", 1))
        self.assertIn("id", first)

    def test_defaults_to_this_year(self):
        self.assertEqual(self.get("holidays").data["year"], timezone.localdate().year)

    def test_bad_year(self):
        for bad in ("abc", "26", "3000"):
            resp = self.get("holidays", year=bad)
            self.assertEqual(resp.status_code, 400, bad)
            self.assertEqual(resp.data["code"], "invalid_year", bad)


class HolidayRangeTests(SettingsBase):
    def body(self, **kw):
        return {"date_from": "2026-10-20", "date_to": "2026-10-24", "name": "Diwali break", **kw}

    def test_adds_every_day(self):
        resp = self.post("holidays-range", self.body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_count"], 5)
        self.assertFalse(resp.data["dry_run"])
        self.assertEqual(self.holiday_dates(), {date(2026, 10, d) for d in range(20, 25)})
        self.assertEqual(set(CampusHoliday.objects.values_list("name", flat=True)), {"Diwali break"})
        self.assertIn("settings.holidays.range", self.actions())

    def test_weekly_off_is_skipped_by_default(self):
        self.set_working([7])
        resp = self.post("holidays-range", self.body(date_from="2026-10-23", date_to="2026-10-26"))  # Fri..Mon
        self.assertEqual(resp.data["created_count"], 3)
        self.assertNotIn(date(2026, 10, 25), self.holiday_dates())

    def test_weekly_off_can_be_kept(self):
        self.set_working([7])
        resp = self.post("holidays-range", self.body(date_from="2026-10-23", date_to="2026-10-26", skip_weekly_off=False))
        self.assertEqual(resp.data["created_count"], 4)
        self.assertIn(date(2026, 10, 25), self.holiday_dates())

    def test_range_of_only_off_days_is_refused(self):
        self.set_working([7])
        resp = self.post("holidays-range", self.body(date_from="2026-10-25", date_to="2026-10-25"))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_holidays")

    def test_dry_run_changes_nothing(self):
        resp = self.post("holidays-range", self.body(dry_run=True))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["dry_run"])
        self.assertEqual(resp.data["created_count"], 5)
        self.assertEqual(len(resp.data["created"]), 5)
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)
        self.assertNotIn("settings.holidays.range", self.actions())

    def test_dry_run_as_a_string(self):
        resp = self.post("holidays-range", self.body(dry_run="true"))
        self.assertTrue(resp.data["dry_run"])
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)

    def test_an_existing_date_is_skipped_never_overwritten(self):
        self.holiday(date(2026, 10, 21), "Old name")
        resp = self.post("holidays-range", self.body())
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_count"], 4)
        self.assertEqual(resp.data["skipped_existing"], [{"date": "2026-10-21", "name": "Diwali break"}])
        self.assertEqual(CampusHoliday.objects.get(campus=self.campus, date=date(2026, 10, 21)).name, "Old name")

    def test_nothing_new_is_a_200(self):
        self.post("holidays-range", self.body())
        again = self.post("holidays-range", self.body())
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["created_count"], 0)
        self.assertEqual(len(again.data["skipped_existing"]), 5)

    def test_bad_input(self):
        for body in (
            self.body(date_to="2026-10-19"),                 # end before start
            self.body(date_from="2026-01-01", date_to="2026-12-31"),  # too long
            self.body(name=""),
            self.body(date_from="nope"),
            {},
        ):
            resp = self.post("holidays-range", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_holidays", body)
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)


class HolidayBulkTests(SettingsBase):
    def test_adds_good_rows_and_reports_bad_ones(self):
        resp = self.post("holidays-bulk", {"items": [
            {"date": "2026-11-09", "name": "Diwali"},
            {"date": "bad", "name": "x"},
            {"date": "2026-12-25", "name": " Christmas "},
            {"date": "2026-11-09", "name": "Dup"},
        ]})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["created_count"], 2)
        self.assertEqual([e["index"] for e in resp.data["errors"]], [2, 4])
        self.assertEqual(CampusHoliday.objects.get(campus=self.campus, date=date(2026, 12, 25)).name, "Christmas")
        self.assertIn("settings.holidays.bulk", self.actions())

    def test_existing_dates_are_skipped(self):
        self.holiday(date(2026, 11, 9), "Kept")
        resp = self.post("holidays-bulk", {"items": [{"date": "2026-11-09", "name": "New"}]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["created_count"], 0)
        self.assertEqual(len(resp.data["skipped_existing"]), 1)
        self.assertEqual(CampusHoliday.objects.get(campus=self.campus).name, "Kept")

    def test_dry_run(self):
        resp = self.post("holidays-bulk", {"items": [{"date": "2026-11-09", "name": "Diwali"}], "dry_run": True})
        self.assertEqual((resp.status_code, resp.data["created_count"]), (200, 1))
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)

    def test_container_errors(self):
        for body in ({}, {"items": []}, {"items": "x"}, {"items": [{"date": "2026-01-01", "name": "x"}] * 101}):
            resp = self.post("holidays-bulk", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_holidays", body)


class HolidayCopyYearTests(SettingsBase):
    def setUp(self):
        super().setUp()
        self.holiday(date(2026, 8, 15), "Independence Day")
        self.holiday(date(2026, 10, 2), "Gandhi Jayanti")

    def test_copies_to_the_new_year(self):
        resp = self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2027})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual({r["date"] for r in resp.data["created"]}, {"2027-08-15", "2027-10-02"})
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus, date__year=2027).count(), 2)
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus, date__year=2026).count(), 2)  # source kept
        self.assertIn("settings.holidays.copy_year", self.actions())

    def test_copying_twice_adds_nothing_the_second_time(self):
        self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2027})
        again = self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2027})
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["created_count"], 0)
        self.assertEqual(len(again.data["skipped_existing"]), 2)

    def test_leap_day_moves_to_28_feb(self):
        self.holiday(date(2024, 2, 29), "Leap day")
        resp = self.post("holidays-copy-year", {"from_year": 2024, "to_year": 2025})
        self.assertEqual([r["date"] for r in resp.data["created"]], ["2025-02-28"])

    def test_dry_run(self):
        resp = self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2027, "dry_run": True})
        self.assertEqual((resp.status_code, resp.data["created_count"]), (200, 2))
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus, date__year=2027).count(), 0)

    def test_same_year_is_refused(self):
        resp = self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2026})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "same_year")

    def test_empty_source_year_is_refused(self):
        resp = self.post("holidays-copy-year", {"from_year": 2020, "to_year": 2027})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["code"], "invalid_holidays")

    def test_bad_years(self):
        for body in ({}, {"from_year": "x", "to_year": 2027}, {"from_year": 2026, "to_year": 99}):
            resp = self.post("holidays-copy-year", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_holidays", body)

    def test_only_this_campus_is_copied(self):
        self.holiday(date(2026, 5, 1), "elsewhere", campus=self.other_campus)
        self.post("holidays-copy-year", {"from_year": 2026, "to_year": 2027})
        self.assertFalse(CampusHoliday.objects.filter(campus=self.campus, date=date(2027, 5, 1)).exists())
        self.assertFalse(CampusHoliday.objects.filter(campus=self.other_campus, date=date(2027, 5, 1)).exists())


class HolidayNationalTests(SettingsBase):
    def test_adds_the_three_fixed_days(self):
        resp = self.post("holidays-national", {"year": 2026})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual({(r["date"], r["name"]) for r in resp.data["created"]}, {
            ("2026-01-26", "Republic Day"), ("2026-08-15", "Independence Day"), ("2026-10-02", "Gandhi Jayanti"),
        })
        self.assertIn("settings.holidays.national", self.actions())

    def test_second_run_adds_nothing(self):
        self.post("holidays-national", {"year": 2026})
        again = self.post("holidays-national", {"year": 2026})
        self.assertEqual((again.status_code, again.data["created_count"]), (200, 0))
        self.assertEqual(len(again.data["skipped_existing"]), 3)

    def test_an_existing_name_is_kept(self):
        self.holiday(date(2026, 10, 2), "Gandhi Jayanti (school function)")
        resp = self.post("holidays-national", {"year": 2026})
        self.assertEqual(resp.data["created_count"], 2)
        self.assertEqual(CampusHoliday.objects.get(campus=self.campus, date=date(2026, 10, 2)).name,
                         "Gandhi Jayanti (school function)")

    def test_dry_run(self):
        resp = self.post("holidays-national", {"year": 2026, "dry_run": True})
        self.assertEqual((resp.status_code, resp.data["created_count"]), (200, 3))
        self.assertEqual(CampusHoliday.objects.filter(campus=self.campus).count(), 0)

    def test_year_is_required(self):
        for body in ({}, {"year": "abc"}, {"year": 1800}):
            resp = self.post("holidays-national", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], "invalid_holidays", body)


# ===========================================================================
# 7. TIMETABLE SLOTS
# ===========================================================================
class SlotListTests(SettingsBase):
    def test_empty_week(self):
        self.set_working([7])
        data = self.get("time-slots").data
        self.assertEqual(data["total"], 0)
        self.assertEqual([d["day"] for d in data["days"]], [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(data["days"][0]["name"], "Monday")
        self.assertTrue(data["days"][6]["is_off"])
        self.assertFalse(data["days"][0]["is_off"])

    def test_slots_are_listed_with_in_use_flag(self):
        used = self.slot(1, time(9, 0), time(9, 40), "Period 1")
        self.slot(1, time(9, 40), time(10, 20), "Period 2")
        TimetableEntry.objects.create(section=self.secA, subject=self.math, staff=self.t_staff,
                                      time_slot=used, session=self.session)
        monday = self.get("time-slots").data["days"][0]["slots"]
        self.assertEqual([(s["label"], s["start_time"], s["end_time"], s["in_use"]) for s in monday],
                         [("Period 1", "09:00", "09:40", True), ("Period 2", "09:40", "10:20", False)])

    def test_only_this_campus(self):
        TimeSlot.objects.create(campus=self.other_campus, day_of_week=1, start_time=time(9), end_time=time(10))
        self.assertEqual(self.get("time-slots").data["total"], 0)


class SlotGenerateTests(SettingsBase):
    BODY = {"start_time": "09:00", "period_minutes": 40, "periods": 3, "breaks": [{"after": 2, "minutes": 20}]}

    def test_fills_every_working_day_by_default(self):
        self.set_working([7])
        resp = self.post("slots-generate", self.BODY)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["create_count"], 18)
        self.assertEqual(resp.data["conflicts"], [])
        self.assertEqual([(t["label"], t["start_time"], t["end_time"]) for t in resp.data["template"]],
                         [("Period 1", "09:00", "09:40"), ("Period 2", "09:40", "10:20"), ("Period 3", "10:40", "11:20")])
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 18)
        self.assertFalse(TimeSlot.objects.filter(campus=self.campus, day_of_week=7).exists())
        self.assertIn("settings.time_slots.generate", self.actions())

    def test_explicit_days(self):
        resp = self.post("slots-generate", {**self.BODY, "days": [1, 2]})
        self.assertEqual(resp.data["create_count"], 6)
        self.assertEqual(set(TimeSlot.objects.values_list("day_of_week", flat=True)), {1, 2})

    def test_days_working_keyword(self):
        self.set_working([6, 7])
        resp = self.post("slots-generate", {**self.BODY, "days": "working"})
        self.assertEqual(resp.data["create_count"], 15)

    def test_custom_label_prefix(self):
        self.post("slots-generate", {**self.BODY, "days": [1], "label_prefix": "Lecture"})
        self.assertTrue(TimeSlot.objects.filter(campus=self.campus, label="Lecture 1").exists())

    def test_dry_run_changes_nothing(self):
        resp = self.post("slots-generate", {**self.BODY, "days": [1], "dry_run": True})
        self.assertEqual((resp.status_code, resp.data["create_count"], resp.data["dry_run"]), (200, 3, True))
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 0)
        self.assertNotIn("settings.time_slots.generate", self.actions())

    def test_running_twice_creates_nothing_new(self):
        self.post("slots-generate", {**self.BODY, "days": [1]})
        again = self.post("slots-generate", {**self.BODY, "days": [1]})
        self.assertEqual(again.status_code, 200)
        self.assertEqual((again.data["create_count"], again.data["already_there"]), (0, 3))
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 3)

    def test_a_clash_blocks_only_that_day(self):
        self.set_working([7])
        self.slot(1, time(9, 30), time(10, 0))
        resp = self.post("slots-generate", self.BODY)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["create_count"], 15)
        self.assertEqual(len(resp.data["conflicts"]), 2)
        self.assertEqual({c["day"] for c in resp.data["conflicts"]}, {1})
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus, day_of_week=1).count(), 1)  # Monday untouched
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 16)

    def test_replace_unused_swaps_a_free_clashing_slot(self):
        self.set_working([7])
        self.slot(1, time(9, 30), time(10, 0), "Old")
        resp = self.post("slots-generate", {**self.BODY, "mode": "replace_unused"})
        self.assertEqual((resp.data["create_count"], resp.data["delete_count"]), (18, 1))
        self.assertFalse(TimeSlot.objects.filter(campus=self.campus, label="Old").exists())
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 18)

    def test_replace_unused_never_removes_a_slot_the_timetable_uses(self):
        self.set_working([7])
        used = self.slot(1, time(9, 30), time(10, 0), "Used")
        TimetableEntry.objects.create(section=self.secA, subject=self.math, staff=self.t_staff,
                                      time_slot=used, session=self.session)
        resp = self.post("slots-generate", {**self.BODY, "mode": "replace_unused"})
        self.assertEqual(resp.data["delete_count"], 0)
        self.assertEqual(len(resp.data["conflicts"]), 2)
        self.assertTrue(TimeSlot.objects.filter(pk=used.pk).exists())
        self.assertTrue(any("used in the timetable" in c["message"] for c in resp.data["conflicts"]))

    def test_bad_input(self):
        for body, code in (
            ({}, "invalid_slots"),
            ({**BODY_BASE, "start_time": "25:00"}, "invalid_slots"),
            ({**BODY_BASE, "periods": 0}, "invalid_slots"),
            ({**BODY_BASE, "periods": 99}, "invalid_slots"),
            ({**BODY_BASE, "period_minutes": 5}, "invalid_slots"),
            ({**BODY_BASE, "breaks": [{"after": 9, "minutes": 10}]}, "invalid_slots"),
            ({**BODY_BASE, "start_time": "23:00", "period_minutes": 60}, "invalid_slots"),
            ({**BODY_BASE, "mode": "wipe"}, "invalid_mode"),
            ({**BODY_BASE, "days": []}, "no_days"),
            ({**BODY_BASE, "days": [9]}, "invalid"),
        ):
            resp = self.post("slots-generate", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], code, body)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 0)

    def test_not_an_object(self):
        resp = self.as_(self.admin).post(self.u("slots-generate"), [1], format="json")
        self.assertEqual(resp.status_code, 400)


BODY_BASE = {"start_time": "09:00", "period_minutes": 40, "periods": 2}


class SlotCopyDayTests(SettingsBase):
    def setUp(self):
        super().setUp()
        self.set_working([7])
        self.post("slots-generate", {"start_time": "09:00", "period_minutes": 40, "periods": 3, "days": [1]})

    def test_copies_one_day_to_others(self):
        resp = self.post("slots-copy-day", {"from_day": 1, "to_days": [2, 3]})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["create_count"], 6)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus, day_of_week=2).count(), 3)
        got = list(TimeSlot.objects.filter(campus=self.campus, day_of_week=3).values_list("start_time", "end_time"))
        want = list(TimeSlot.objects.filter(campus=self.campus, day_of_week=1).values_list("start_time", "end_time"))
        self.assertEqual(got, want)
        self.assertIn("settings.time_slots.copy_day", self.actions())

    def test_default_target_is_every_other_working_day(self):
        resp = self.post("slots-copy-day", {"from_day": 1})
        self.assertEqual(resp.data["create_count"], 15)  # Tue..Sat x 3
        self.assertFalse(TimeSlot.objects.filter(campus=self.campus, day_of_week=7).exists())

    def test_source_day_in_targets_is_ignored(self):
        resp = self.post("slots-copy-day", {"from_day": 1, "to_days": [1, 2]})
        self.assertEqual(resp.data["create_count"], 3)

    def test_dry_run(self):
        resp = self.post("slots-copy-day", {"from_day": 1, "to_days": [2], "dry_run": True})
        self.assertEqual((resp.status_code, resp.data["create_count"]), (200, 3))
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus, day_of_week=2).count(), 0)

    def test_second_copy_adds_nothing(self):
        self.post("slots-copy-day", {"from_day": 1, "to_days": [2]})
        again = self.post("slots-copy-day", {"from_day": 1, "to_days": [2]})
        self.assertEqual((again.status_code, again.data["create_count"], again.data["already_there"]), (200, 0, 3))

    def test_a_clash_on_the_target_day_blocks_it(self):
        self.slot(2, time(9, 30), time(10, 0))
        resp = self.post("slots-copy-day", {"from_day": 1, "to_days": [2, 3]})
        self.assertEqual(resp.data["create_count"], 3)
        self.assertTrue(resp.data["conflicts"])
        self.assertEqual({c["day"] for c in resp.data["conflicts"]}, {2})

    def test_bad_input(self):
        for body, code in (
            ({}, "invalid_slots"),
            ({"from_day": 9}, "invalid_slots"),
            ({"from_day": 4, "to_days": [2]}, "empty_day"),
            ({"from_day": 1, "to_days": [1]}, "no_days"),
            ({"from_day": 1, "to_days": [2], "mode": "wipe"}, "invalid_mode"),
        ):
            resp = self.post("slots-copy-day", body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertEqual(resp.data["code"], code, body)


class SlotClearTests(SettingsBase):
    def setUp(self):
        super().setUp()
        self.set_working([7])
        self.post("slots-generate", {"start_time": "09:00", "period_minutes": 40, "periods": 2, "days": [1, 2]})

    def test_removes_only_unused_slots(self):
        used = TimeSlot.objects.filter(campus=self.campus, day_of_week=1).first()
        TimetableEntry.objects.create(section=self.secA, subject=self.math, staff=self.t_staff,
                                      time_slot=used, session=self.session)
        resp = self.post("slots-clear", {"days": [1, 2]})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual((resp.data["removed"], resp.data["kept_in_use"]), (3, 1))
        self.assertEqual(list(TimeSlot.objects.filter(campus=self.campus).values_list("pk", flat=True)), [used.pk])
        self.assertIn("settings.time_slots.clear", self.actions())

    def test_only_the_chosen_days(self):
        resp = self.post("slots-clear", {"days": [2]})
        self.assertEqual(resp.data["removed"], 2)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus, day_of_week=1).count(), 2)

    def test_default_is_every_working_day(self):
        self.assertEqual(self.post("slots-clear", {}).data["removed"], 4)
        self.assertEqual(TimeSlot.objects.filter(campus=self.campus).count(), 0)

    def test_nothing_to_clear_writes_no_audit_row(self):
        self.post("slots-clear", {})
        before = CampusAuditLog.objects.filter(campus=self.campus, action="settings.time_slots.clear").count()
        resp = self.post("slots-clear", {})
        self.assertEqual((resp.data["removed"], resp.data["kept_in_use"]), (0, 0))
        self.assertEqual(CampusAuditLog.objects.filter(campus=self.campus, action="settings.time_slots.clear").count(), before)

    def test_bad_days(self):
        resp = self.post("slots-clear", {"days": [9]})
        self.assertEqual(resp.status_code, 400)

    def test_other_campus_slots_are_safe(self):
        other = TimeSlot.objects.create(campus=self.other_campus, day_of_week=1, start_time=time(9), end_time=time(10))
        self.post("slots-clear", {})
        self.assertTrue(TimeSlot.objects.filter(pk=other.pk).exists())


# ===========================================================================
# SCHOOL LOGO  (POST / DELETE settings/<campus>/logo/)
# ===========================================================================
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class LogoTests(SettingsBase):
    def setUp(self):
        super().setUp()
        self._media = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._media)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(shutil.rmtree, self._media, True)

    def upload(self, content=PNG_BYTES, name="logo.png", user=None):
        client = self.as_(user or self.admin)
        return client.post(self.u("logo"), {"logo": SimpleUploadedFile(name, content, content_type="image/png")},
                           format="multipart")

    def test_profile_has_no_logo_at_first(self):
        self.assertEqual(self.get("profile").data["logo_url"], "")

    def test_admin_uploads_and_the_profile_shows_it(self):
        resp = self.upload()
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["logo_url"])
        self.assertEqual(self.get("profile").data["logo_url"], resp.data["logo_url"])
        self.assertIn("settings.logo.update", self.actions())

    def test_the_type_is_read_from_the_bytes_not_the_name(self):
        resp = self.upload(content=JPG_BYTES, name="whatever.png")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertTrue(resp.data["logo_url"].endswith(".jpg"))

    def test_not_an_image_is_refused_even_with_a_png_name(self):
        resp = self.upload(content=b"MZ not an image at all", name="logo.png")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_logo")
        self.assertEqual(self.get("profile").data["logo_url"], "")

    def test_too_large_is_refused(self):
        resp = self.upload(content=PNG_BYTES + b"\x00" * (2 * 1024 * 1024))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_logo")

    def test_missing_file_is_refused(self):
        resp = self.as_(self.admin).post(self.u("logo"), {}, format="multipart")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data.get("code"), "invalid_logo")

    def test_replacing_keeps_one_logo(self):
        first = self.upload().data["logo_url"]
        second = self.upload(content=JPG_BYTES, name="b.jpg").data["logo_url"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.get("profile").data["logo_url"], second)
        self.assertEqual(CampusProfile.objects.filter(campus=self.campus).count(), 1)

    def test_remove(self):
        self.upload()
        resp = self.as_(self.admin).delete(self.u("logo"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["logo_url"], "")
        self.assertIn("settings.logo.remove", self.actions())
        again = self.as_(self.admin).delete(self.u("logo"))  # nothing to remove is not an error
        self.assertEqual(again.status_code, 200)

    def test_removing_the_logo_keeps_the_rest_of_the_profile(self):
        self.put("profile", {"board": "cbse", "city": "Pune"})
        self.upload()
        self.as_(self.admin).delete(self.u("logo"))
        data = self.get("profile").data
        self.assertEqual(data["city"], "Pune")
        self.assertEqual(data["logo_url"], "")

    def test_saving_the_profile_does_not_touch_the_logo(self):
        url = self.upload().data["logo_url"]
        self.put("profile", {"city": "Pune"})
        self.assertEqual(self.get("profile").data["logo_url"], url)

    def test_teacher_cannot_change_it(self):
        self.assertEqual(self.upload(user=self.teacher).status_code, 403)
        self.assertEqual(self.as_(self.teacher).delete(self.u("logo")).status_code, 403)

    def test_outsider_gets_404(self):
        self.assertEqual(self.upload(user=self.outsider).status_code, 404)

    def test_principal_can(self):
        self.assertEqual(self.upload(user=self.principal).status_code, 200)

