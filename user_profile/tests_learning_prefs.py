"""
S8 - Learning preferences. NOT RUN by the author (read-only task):
    python manage.py test user_profile.tests_learning_prefs

Pure logic (learning_prefs.py) + the API: PATCH /profile/preferences/me/ (writes) and GET /profile/learning/ (summary).
2026-10-26 is a Monday (weekday 0).
"""
import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from . import learning_prefs as lp
from .models import Streak, UserPreference

User = get_user_model()
IST = ZoneInfo("Asia/Kolkata")
PREF_URL = "/profile/preferences/me/"
LEARNING_URL = "/profile/learning/"
MON = datetime.date(2026, 10, 26)


def at(day_offset, hour, minute=0):
    """Aware IST datetime: Monday 2026-10-26 + day_offset days."""
    return datetime.datetime.combine(MON + datetime.timedelta(days=day_offset), datetime.time(hour, minute), tzinfo=IST)


# ---------------------------------------------------------------------------------------------------------------------
class ValidationTests(SimpleTestCase):
    def test_parse_hhmm(self):
        self.assertEqual(lp.parse_hhmm("07:30"), datetime.time(7, 30))
        self.assertEqual(lp.parse_hhmm("18:05:59"), datetime.time(18, 5))  # seconds dropped
        self.assertEqual(lp.parse_hhmm(datetime.time(9, 15, 30)), datetime.time(9, 15))
        for bad in ("24:00", "12:60", "7", "ab:cd", "", None, 930, "12:3x"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                lp.parse_hhmm(bad)

    def test_fmt_hhmm(self):
        self.assertEqual(lp.fmt_hhmm(datetime.time(6, 5)), "06:05")

    def test_normalize_days(self):
        self.assertEqual(lp.normalize_days([4, 0, 4, 2]), [0, 2, 4])
        self.assertEqual(lp.normalize_days([]), [])
        self.assertEqual(lp.normalize_days(None), [])
        for bad in ([7], [-1], ["1"], [True], "0,1", [1.5]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                lp.normalize_days(bad)

    def test_normalize_focus_windows(self):
        out = lp.normalize_focus_windows([{"days": [2, 0], "start": "22:00", "end": "06:00"}])
        self.assertEqual(out, [{"days": [0, 2], "start": "22:00", "end": "06:00"}])  # night-crossing is valid
        self.assertEqual(lp.normalize_focus_windows(None), [])
        # duplicate window collapses
        w = {"days": [], "start": "09:00", "end": "10:00"}
        self.assertEqual(len(lp.normalize_focus_windows([w, dict(w)])), 1)

    def test_normalize_focus_windows_rejects_bad_input(self):
        w = {"days": [], "start": "09:00", "end": "10:00"}
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows("nope")
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows(["x"])
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows([{"days": [], "start": "09:00", "end": "09:00"}])  # zero length
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows([{"days": [9], "start": "09:00", "end": "10:00"}])
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows([{"days": [], "start": "9am", "end": "10:00"}])
        too_many = [{**w, "start": f"0{i}:00", "end": f"0{i}:30"} for i in range(lp.MAX_FOCUS_WINDOWS + 1)]
        with self.assertRaises(ValueError):
            lp.normalize_focus_windows(too_many)

    def test_normalize_languages(self):
        ok = ("en", "hi", "bn", "ta")
        self.assertEqual(lp.normalize_languages(["HI", "en-US", "hi"], ok), ["hi", "en"])
        self.assertEqual(lp.normalize_languages(None, ok), [])
        with self.assertRaises(ValueError):
            lp.normalize_languages(["xx"], ok)
        with self.assertRaises(ValueError):
            lp.normalize_languages(["en", "hi", "bn", "ta"], ok)  # 4 > MAX_CONTENT_LANGUAGES
        with self.assertRaises(ValueError):
            lp.normalize_languages("en", ok)


# ---------------------------------------------------------------------------------------------------------------------
class FocusWindowTests(SimpleTestCase):
    NIGHT_MON = [{"days": [0], "start": "22:00", "end": "06:00"}]

    def test_same_day_window(self):
        w = [{"days": [], "start": "09:00", "end": "11:00"}]
        self.assertTrue(lp.focus_window_active(w, at(0, 10, 0), IST))
        self.assertTrue(lp.focus_window_active(w, at(0, 9, 0), IST))      # start inclusive
        self.assertFalse(lp.focus_window_active(w, at(0, 11, 0), IST))    # end exclusive
        self.assertFalse(lp.focus_window_active(w, at(0, 8, 59), IST))

    def test_days_filter(self):
        w = [{"days": [1], "start": "09:00", "end": "11:00"}]  # Tuesday only
        self.assertFalse(lp.focus_window_active(w, at(0, 10), IST))
        self.assertTrue(lp.focus_window_active(w, at(1, 10), IST))

    def test_night_window_crosses_midnight(self):
        self.assertTrue(lp.focus_window_active(self.NIGHT_MON, at(0, 23, 0), IST))   # Monday night - start side
        self.assertTrue(lp.focus_window_active(self.NIGHT_MON, at(1, 5, 30), IST))   # Tuesday morning - belongs to Monday
        self.assertFalse(lp.focus_window_active(self.NIGHT_MON, at(1, 6, 0), IST))   # end exclusive
        self.assertFalse(lp.focus_window_active(self.NIGHT_MON, at(0, 5, 30), IST))  # Monday morning: Sunday's window
        self.assertFalse(lp.focus_window_active(self.NIGHT_MON, at(1, 23, 0), IST))  # Tuesday night: not in days

    def test_every_day_night_window_morning_part(self):
        w = [{"days": [], "start": "22:00", "end": "06:00"}]
        self.assertTrue(lp.focus_window_active(w, at(0, 2, 0), IST))
        self.assertFalse(lp.focus_window_active(w, at(0, 12, 0), IST))

    def test_timezone_is_the_users_not_the_servers(self):
        w = [{"days": [], "start": "09:00", "end": "11:00"}]
        utc_moment = datetime.datetime(2026, 10, 26, 4, 30, tzinfo=datetime.timezone.utc)  # 10:00 IST
        self.assertTrue(lp.focus_window_active(w, utc_moment, IST))
        self.assertFalse(lp.focus_window_active(w, utc_moment, ZoneInfo("America/New_York")))

    def test_broken_windows_are_skipped_not_raised(self):
        junk = [None, "x", {"start": "bad", "end": "10:00"}, {"days": "?", "start": "09:00", "end": "10:00"},
                {"days": [], "start": "09:00", "end": "09:00"}]
        self.assertFalse(lp.focus_window_active(junk, at(0, 9, 30), IST))
        self.assertFalse(lp.focus_window_active([], at(0, 9, 30), IST))
        self.assertFalse(lp.focus_window_active(None, at(0, 9, 30), IST))

    def test_tz_helpers(self):
        self.assertIsNone(lp.tz_from_name("UTC"))   # untouched default => server tz
        self.assertIsNone(lp.tz_from_name(""))
        self.assertIsNone(lp.tz_from_name("Not/AZone"))
        self.assertEqual(str(lp.tz_from_name("Asia/Kolkata")), "Asia/Kolkata")


# ---------------------------------------------------------------------------------------------------------------------
class ReminderDueTests(SimpleTestCase):
    T = datetime.time(18, 0)

    def due(self, local, **kw):
        args = dict(enabled=True, reminder_time=self.T, days=[], last_on=None, local=local)
        args.update(kw)
        return lp.study_reminder_due(args["enabled"], args["reminder_time"], args["days"], args["last_on"], args["local"])

    def test_window_edges(self):
        self.assertFalse(self.due(at(0, 17, 59)))
        self.assertTrue(self.due(at(0, 18, 0)))
        self.assertTrue(self.due(at(0, 21, 0)))      # exactly the 180 min grace
        self.assertFalse(self.due(at(0, 21, 1)))     # too late - never a night-time "study now"

    def test_once_per_local_day(self):
        self.assertFalse(self.due(at(0, 18, 30), last_on=MON))
        self.assertTrue(self.due(at(1, 18, 30), last_on=MON))

    def test_disabled_or_no_time(self):
        self.assertFalse(self.due(at(0, 18, 30), enabled=False))
        self.assertFalse(self.due(at(0, 18, 30), reminder_time=None))

    def test_weekdays(self):
        self.assertFalse(self.due(at(0, 18, 30), days=[1, 2]))   # Monday not chosen
        self.assertTrue(self.due(at(1, 18, 30), days=[1, 2]))
        self.assertTrue(self.due(at(0, 18, 30), days=[]))        # empty = every day

    def test_bad_stored_days_mean_every_day(self):
        self.assertTrue(self.due(at(0, 18, 30), days=["junk"]))


# ---------------------------------------------------------------------------------------------------------------------
class FeedTagTests(SimpleTestCase):
    def test_level_and_language_tags(self):
        self.assertIn("basics", lp.level_tags("beginner"))
        self.assertEqual(lp.level_tags(""), [])
        self.assertEqual(lp.level_tags("nonsense"), [])
        self.assertIn("hinglish", lp.language_tags(["hi"]))
        self.assertIn("english", lp.language_tags(["en-US"]))
        self.assertEqual(lp.language_tags(None), [])

    def test_preference_tags_unique_and_combined(self):
        tags = lp.preference_tags("advanced", ["en", "hi"])
        self.assertIn("olympiad", tags)
        self.assertIn("hindi", tags)
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(lp.preference_tags("", []), [])


# ---------------------------------------------------------------------------------------------------------------------
class SummaryTests(TestCase):
    def test_completeness(self):
        self.assertEqual(lp.completeness(UserPreference())["percent"], 0)
        pref = UserPreference(exam_target="jee", class_level="12", focus_subjects=["Physics"],
                              exam_date=MON, content_level="advanced", content_languages=["en"],
                              study_reminder_enabled=True)
        out = lp.completeness(pref)
        self.assertEqual((out["percent"], out["missing"]), (100, []))
        part = lp.completeness(UserPreference(exam_target="jee"))
        self.assertEqual(part["percent"], 14)  # 1 of 7
        self.assertIn("exam_date", part["missing"])
        self.assertNotIn("exam_target", part["missing"])

    def test_drives_codes(self):
        pref = UserPreference(exam_target="neet", content_level="beginner", content_languages=["hi"],
                              study_reminder_enabled=True, focus_schedule_enabled=True,
                              focus_schedule=[{"days": [], "start": "09:00", "end": "10:00"}])
        d = lp.drives(pref)
        self.assertEqual(d["feed"], ["exam_target", "content_level", "content_languages", "focus_schedule"])
        self.assertEqual(d["notifications"], ["focus_schedule_priority_only", "study_reminder"])
        self.assertEqual(lp.drives(UserPreference()), {"feed": [], "notifications": []})

    def test_build_summary_shape_and_defaults(self):
        s = lp.build_summary(UserPreference(), None, now=at(0, 12), tz=IST)
        self.assertEqual(s["daily_goal"]["minutes"], 10)  # no Streak row => default
        self.assertEqual(s["study_reminder"], {"enabled": False, "time": "18:00", "days": []})
        self.assertFalse(s["focus_now"])
        self.assertEqual(s["content"]["levels"], list(lp.LEVELS))
        self.assertEqual(s["focus_schedule"]["max_windows"], lp.MAX_FOCUS_WINDOWS)
        self.assertIn("hi", s["content"]["language_options"])
        self.assertIsNone(s["profile"]["days_to_exam"])

    def test_build_summary_days_to_exam_and_focus(self):
        pref = UserPreference(exam_date=MON + datetime.timedelta(days=12), focus_schedule_enabled=True,
                              focus_schedule=[{"days": [], "start": "09:00", "end": "11:00"}])
        s = lp.build_summary(pref, Streak(daily_goal_minutes=30), now=at(0, 10), tz=IST)
        self.assertEqual(s["profile"]["days_to_exam"], 12)
        self.assertEqual(s["daily_goal"]["minutes"], 30)
        self.assertTrue(s["focus_schedule"]["active_now"])
        self.assertTrue(s["focus_now"])
        past = UserPreference(exam_date=MON - datetime.timedelta(days=1))
        self.assertIsNone(lp.build_summary(past, None, now=at(0, 10), tz=IST)["profile"]["days_to_exam"])


# ---------------------------------------------------------------------------------------------------------------------
class LearningApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="learner", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def patch(self, body, client=None):
        return (client or self.api).patch(PREF_URL, body, format="json")

    def summary(self, client=None):
        return (client or self.api).get(LEARNING_URL).data["data"]

    # ---- reads
    def test_get_defaults_creates_no_row(self):
        s = self.summary()
        self.assertEqual(s["completeness"]["percent"], 0)
        self.assertFalse(s["study_reminder"]["enabled"])
        self.assertEqual(s["daily_goal"]["minutes"], 10)
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())  # GET has no side effects

    def test_preference_get_has_new_fields_and_hides_server_marker(self):
        d = self.api.get(PREF_URL).data["data"]
        for key in ("study_reminder_enabled", "study_reminder_time", "study_reminder_days", "focus_schedule_enabled",
                    "focus_schedule", "focus_schedule_active", "focus_now", "content_level", "content_languages"):
            self.assertIn(key, d)
        self.assertNotIn("last_study_reminder_on", d)
        self.assertEqual(d["study_reminder_time"], "18:00")

    # ---- writes
    def test_save_all_learning_fields(self):
        r = self.patch({
            "exam_target": "jee", "class_level": "12", "focus_subjects": ["Physics"],
            "study_reminder_enabled": True, "study_reminder_time": "07:30", "study_reminder_days": [4, 0, 2],
            "focus_schedule_enabled": True,
            "focus_schedule": [{"days": [0, 1], "start": "22:00", "end": "06:00"}],
            "content_level": "advanced", "content_languages": ["HI", "en"],
        })
        self.assertEqual(r.status_code, 200, r.data)
        d = r.data["data"]
        self.assertEqual((d["study_reminder_time"], d["study_reminder_days"]), ("07:30", [0, 2, 4]))
        self.assertEqual(d["content_languages"], ["hi", "en"])
        self.assertEqual(d["content_level"], "advanced")
        s = self.summary()
        self.assertTrue(s["study_reminder"]["enabled"])
        self.assertEqual(s["study_reminder"]["time"], "07:30")
        self.assertEqual(s["focus_schedule"]["windows"], [{"days": [0, 1], "start": "22:00", "end": "06:00"}])
        self.assertIn("study_reminder", s["drives"]["notifications"])
        self.assertIn("content_level", s["drives"]["feed"])
        self.assertGreaterEqual(s["completeness"]["percent"], 80)

    def test_seconds_in_time_are_accepted_and_trimmed(self):
        r = self.patch({"study_reminder_time": "06:45:00"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["data"]["study_reminder_time"], "06:45")

    def test_validation_errors(self):
        bad_bodies = [
            {"study_reminder_time": "25:00"},
            {"study_reminder_time": "soon"},
            {"study_reminder_days": [7]},
            {"study_reminder_days": ["mon"]},
            {"content_level": "expert"},
            {"content_languages": ["xx"]},
            {"content_languages": ["en", "hi", "bn", "ta"]},
            {"focus_schedule": [{"days": [], "start": "09:00", "end": "09:00"}]},
            {"focus_schedule": "always"},
            {"focus_schedule": [{"days": [], "start": "0%d:00" % i, "end": "0%d:30" % i} for i in range(4)]},
        ]
        for body in bad_bodies:
            self.assertEqual(self.patch(body).status_code, 400, body)

    def test_schedule_on_needs_a_window(self):
        self.assertEqual(self.patch({"focus_schedule_enabled": True}).status_code, 400)
        self.assertEqual(self.patch({"focus_schedule_enabled": True, "focus_schedule": []}).status_code, 400)
        ok = self.patch({"focus_schedule_enabled": True, "focus_schedule": [{"days": [], "start": "20:00", "end": "21:00"}]})
        self.assertEqual(ok.status_code, 200)
        # later PATCH of only the flag uses the stored windows
        self.assertEqual(self.patch({"focus_schedule_enabled": False}).status_code, 200)
        self.assertEqual(self.patch({"focus_schedule_enabled": True}).status_code, 200)

    def test_clearing_everything(self):
        self.patch({"content_level": "beginner", "content_languages": ["en"], "study_reminder_days": [1]})
        r = self.patch({"content_level": "", "content_languages": [], "study_reminder_days": []})
        self.assertEqual(r.status_code, 200)
        d = r.data["data"]
        self.assertEqual((d["content_level"], d["content_languages"], d["study_reminder_days"]), ("", [], []))

    def test_server_marker_is_not_client_writable(self):
        self.patch({"study_reminder_enabled": True, "last_study_reminder_on": "2020-01-01"})
        self.assertIsNone(UserPreference.objects.get(user=self.user).last_study_reminder_on)

    def test_read_only_fields_ignored(self):
        self.patch({"focus_now": True, "focus_schedule_active": True})
        d = self.api.get(PREF_URL).data["data"]
        self.assertFalse(d["focus_now"])
        self.assertFalse(d["focus_schedule_active"])

    # ---- feed cache
    def test_feed_cache_invalidated_for_level_language_and_schedule(self):
        for body in ({"content_level": "beginner"}, {"content_languages": ["hi"]},
                     {"focus_schedule_enabled": False}, {"focus_schedule": []}):
            with mock.patch("post.feed_cache.invalidate") as inv:
                self.assertEqual(self.patch(body).status_code, 200, body)
            inv.assert_called_once_with(self.user.pk)

    def test_reminder_only_patch_does_not_touch_feed_cache(self):
        with mock.patch("post.feed_cache.invalidate") as inv:
            self.assertEqual(self.patch({"study_reminder_enabled": True, "study_reminder_time": "08:00"}).status_code, 200)
        inv.assert_not_called()

    # ---- isolation + auth
    def test_cross_user_isolation(self):
        self.patch({"study_reminder_enabled": True, "content_level": "advanced", "content_languages": ["hi"]})
        other_api = APIClient()
        other_api.force_authenticate(self.other)
        s = self.summary(other_api)
        self.assertFalse(s["study_reminder"]["enabled"])
        self.assertEqual(s["content"]["level"], "")
        self.assertEqual(s["content"]["languages"], [])
        self.patch({"content_level": "beginner"}, other_api)
        self.assertEqual(UserPreference.objects.get(user=self.user).content_level, "advanced")

    def test_summary_reads_daily_goal_from_streak(self):
        Streak.objects.create(user=self.user, daily_goal_minutes=45)
        self.assertEqual(self.summary()["daily_goal"]["minutes"], 45)

    def test_requires_auth(self):
        anon = APIClient()
        self.assertIn(anon.get(LEARNING_URL).status_code, (401, 403))
        self.assertIn(anon.patch(PREF_URL, {"content_level": "beginner"}, format="json").status_code, (401, 403))


# ---------------------------------------------------------------------------------------------------------------------
class ModelHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="m", password="x")

    def test_focus_schedule_active_needs_flag_and_window(self):
        w = [{"days": [], "start": "09:00", "end": "11:00"}]
        off = UserPreference(user=self.user, focus_schedule_enabled=False, focus_schedule=w)
        on = UserPreference(user=self.user, focus_schedule_enabled=True, focus_schedule=w)
        self.assertFalse(off.focus_schedule_active(now=at(0, 10), tz=IST))
        self.assertTrue(on.focus_schedule_active(now=at(0, 10), tz=IST))
        self.assertFalse(on.focus_schedule_active(now=at(0, 12), tz=IST))

    def test_focus_now_is_exam_mode_or_window(self):
        w = [{"days": [], "start": "09:00", "end": "11:00"}]
        pref = UserPreference(user=self.user, exam_mode=True)
        self.assertTrue(pref.focus_now(now=at(0, 15), tz=IST))  # exam mode alone is enough (no date => active)
        pref = UserPreference(user=self.user, focus_schedule_enabled=True, focus_schedule=w)
        self.assertTrue(pref.focus_now(now=at(0, 10), tz=IST))
        self.assertFalse(pref.focus_now(now=at(0, 15), tz=IST))
