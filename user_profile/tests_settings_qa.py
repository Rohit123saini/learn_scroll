"""
S13 - Settings QA (backend). NOT RUN by the author (read-only task, no test / migration / run):
    python manage.py test user_profile.tests_settings_qa

Kya check hota hai (har settings endpoint ke liye):
  1. FIELD MATRIX   har preference field ke valid values save + persist hote hain ("app band -> dobara kholo" = naya client + GET),
                    har galat value 400 deti hai aur KUCH save nahi hota (baaki fields bhi nahi).
  2. COVERAGE GUARD serializer me koi NAYA field aaya aur yahan case nahi likha -> test fail (kisi field ka test chhoot nahi sakta).
  3. NO SIDE EFFECT ek field PATCH karne se baaki fields nahi badalte ("theme badla to font_scale reset" jaisa bug).
  4. READ-ONLY      client updated_at / derived flags nahi badal sakta.
  5. ISOLATION      user A ki settings user B ko kabhi nahi dikhti / B ka PATCH A ko nahi lagta (body me "user"/"id" bhejne par bhi).
  6. AUTH           bina login har settings endpoint 401/403.

Endpoints: /profile/preferences/me/, /profile/privacy/, /profile/activity/ (daily limit), /profile/wallet/settings/,
/profile/teacher/live-defaults/. Notification preferences ka same matrix: core/tests_settings_qa.py.
"""
import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import privacy as privacy_rules
from . import role_settings as role_rules
from .models import PrivacySettings, UserPreference, WalletSettings
from .serializers import PrivacySettingsSerializer, UserPreferenceSerializer

User = get_user_model()

PREF_URL = "/profile/preferences/me/"
PRIVACY_URL = "/profile/privacy/"
ACTIVITY_URL = "/profile/activity/"
WALLET_URL = "/profile/wallet/settings/"
TEACHER_URL = "/profile/teacher/live-defaults/"
LEARNING_URL = "/profile/learning/"

ADULT_DOB = datetime.date(1990, 1, 1)
_TODAY = timezone.localdate()
TOMORROW = (_TODAY + datetime.timedelta(days=1)).isoformat()
NEXT_MONTH = (_TODAY + datetime.timedelta(days=40)).isoformat()
YESTERDAY = (_TODAY - datetime.timedelta(days=1)).isoformat()


def make_user(name, dob=ADULT_DOB):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", date_of_birth=dob)


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


WINDOW = {"days": [0, 1], "start": "22:00", "end": "06:00"}  # raat paar wali window

# ----------------------------------------------------------------------------------------------------------------
# UserPreference field matrix.  field -> (valid [(sent, expected-in-response)], invalid [sent])
# ----------------------------------------------------------------------------------------------------------------
_BOOL_OK = [(True, True), (False, False)]
_BOOL_BAD = ["maybe", None]

PREF_CASES = {
    "theme": (
        [("light", "light"), ("dark", "dark"), ("system", "system")],
        ["blue", "", None, "DARK"],
    ),
    "language": (
        [("en", "en"), ("hi", "hi"), ("bn", "bn"), ("EN", "en"), ("en-us", "en-US")],
        ["xx", "english", "en_US", "", None, "e"],
    ),
    "font_scale": (
        [("small", "small"), ("normal", "normal"), ("large", "large"), ("xlarge", "xlarge")],
        ["huge", "", "LARGE", "1.2", None],
    ),
    "exam_target": (
        [("jee", "jee"), ("neet", "neet"), ("board", "board"), ("upsc", "upsc"), ("other", "other"), ("", "")],
        ["gate", "JEE", 5],
    ),
    "class_level": (
        [("6", "6"), ("12", "12"), ("dropper", "dropper"), ("graduate", "graduate"), ("other", "other"), ("", "")],
        ["5", "13", "school"],
    ),
    "focus_subjects": (
        [
            (["Physics", "Chemistry"], ["Physics", "Chemistry"]),
            (["  Organic   Chemistry "], ["Organic Chemistry"]),  # extra spaces saaf
            (["physics", "PHYSICS"], ["physics"]),  # case-insensitive de-dupe
            ([], []),
        ],
        [["x"] * 11, [""], "Physics", ["a" * 41]],
    ),
    "exam_date": (
        [(TOMORROW, TOMORROW), (NEXT_MONTH, NEXT_MONTH), (_TODAY.isoformat(), _TODAY.isoformat()), (None, None)],
        [YESTERDAY, "2026-13-45", "kal", "31-12-2099"],
    ),
    "exam_mode": (_BOOL_OK, _BOOL_BAD),
    "login_alerts": (_BOOL_OK, _BOOL_BAD),
    "study_reminder_enabled": (_BOOL_OK, _BOOL_BAD),
    "study_reminder_time": (
        [("07:30", "07:30"), ("18:05:30", "18:05"), ("00:00", "00:00"), ("23:59", "23:59")],
        ["25:00", "7am", "", None, "12:60"],
    ),
    "study_reminder_days": (
        [([], []), ([4, 0, 2], [0, 2, 4]), ([0, 0, 1], [0, 1]), ([0, 1, 2, 3, 4, 5, 6], [0, 1, 2, 3, 4, 5, 6])],
        [[7], [-1], ["a"], [0, 1, 2, 3, 4, 5, 6, 0], "0,1"],
    ),
    # Baseline me schedule ON + ek window hai, isliye True/False dono valid.
    "focus_schedule_enabled": (_BOOL_OK, _BOOL_BAD),
    "focus_schedule": (
        [
            ([WINDOW], [WINDOW]),
            ([{"days": [2, 0], "start": "09:00", "end": "10:30"}], [{"days": [0, 2], "start": "09:00", "end": "10:30"}]),
        ],
        [
            "nope",
            [{"days": [], "start": "09:00", "end": "09:00"}],  # zero length
            [{"days": [9], "start": "09:00", "end": "10:00"}],
            [{"days": [], "start": "9am", "end": "10:00"}],
            [{"days": [], "start": f"0{i}:00", "end": f"0{i}:30"} for i in range(4)],  # max 3 windows
            [],  # schedule ON hai (baseline) -> khaali nahi ho sakta
        ],
    ),
    "content_level": (
        [("beginner", "beginner"), ("intermediate", "intermediate"), ("advanced", "advanced"), ("", "")],
        ["expert", "ADVANCED", 3],
    ),
    "content_languages": (
        [(["hi", "en"], ["hi", "en"]), ([], []), (["HI"], ["hi"]), (["en-US", "hi"], ["en", "hi"])],
        [["xx"], ["en", "hi", "bn", "ta"], "hi"],
    ),
}

# Serializer ke read-only / derived fields: client inhe set nahi kar sakta.
PREF_READ_ONLY = ("updated_at", "exam_mode_active", "focus_schedule_active", "focus_now")
# Snapshot me time-dependent derived values nahi lete (window ke kinare par flaky ho jaate).
_SNAPSHOT_SKIP = {"updated_at", "focus_schedule_active", "focus_now"}

# Har field non-default value par: "ek field badla to baaki nahi badle" + "reset nahi hua" ke liye.
PREF_BASELINE = {
    "theme": "dark",
    "language": "hi",
    "font_scale": "large",
    "exam_target": "neet",
    "class_level": "12",
    "focus_subjects": ["Physics"],
    "exam_date": NEXT_MONTH,
    "exam_mode": True,
    "login_alerts": False,
    "study_reminder_enabled": True,
    "study_reminder_time": "06:45",
    "study_reminder_days": [0, 1, 2],
    "focus_schedule_enabled": True,
    "focus_schedule": [WINDOW],
    "content_level": "advanced",
    "content_languages": ["hi", "en"],
}


class _PrefBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("qa_pref")
        self.client = client_for(self.user)

    def data(self, client=None):
        return (client or self.client).get(PREF_URL).data["data"]

    def snapshot(self, client=None):
        return {k: v for k, v in self.data(client).items() if k not in _SNAPSHOT_SKIP}

    def baseline(self):
        r = self.client.patch(PREF_URL, PREF_BASELINE, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        return self.snapshot()


# ================================================================================================================
# 1. field matrix
# ================================================================================================================
class PreferenceFieldMatrixTests(_PrefBase):
    def setUp(self):
        super().setUp()
        self.before = self.baseline()

    def test_coverage_guard_every_serializer_field_has_a_case(self):
        """Serializer me naya field jodo to yahan PREF_CASES (ya PREF_READ_ONLY) me bhi jodna padega."""
        declared = set(UserPreferenceSerializer.Meta.fields)
        covered = set(PREF_CASES) | set(PREF_READ_ONLY)
        self.assertEqual(
            declared, covered,
            f"missing a QA case: {sorted(declared - covered)}; stale case: {sorted(covered - declared)}",
        )
        self.assertEqual(set(UserPreferenceSerializer.Meta.read_only_fields), set(PREF_READ_ONLY))
        self.assertEqual(set(PREF_BASELINE), set(PREF_CASES))  # baseline har writable field ko non-default par rakhta hai

    def test_valid_values_save_and_survive_an_app_restart(self):
        for field, (valid, _bad) in PREF_CASES.items():
            for sent, expected in valid:
                with self.subTest(field=field, sent=sent):
                    cache.clear()  # throttle
                    r = self.client.patch(PREF_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertTrue(r.data["status"])
                    self.assertEqual(r.data["data"][field], expected)
                    # "app kill + reopen": bilkul naya client, server se padho.
                    reopened = client_for(self.user)
                    self.assertEqual(self.data(reopened)[field], expected)

    def test_invalid_values_are_rejected_and_nothing_is_saved(self):
        for field, (_ok, bad) in PREF_CASES.items():
            for sent in bad:
                with self.subTest(field=field, sent=sent):
                    cache.clear()
                    r = self.client.patch(PREF_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 400, r.data)
                    self.assertFalse(r.data["status"])
                    self.assertIn(field, r.data["errors"])
                    self.assertEqual(self.snapshot(), self.before)  # kuch bhi nahi badla

    def test_one_bad_field_rejects_the_whole_request(self):
        r = self.client.patch(PREF_URL, {"theme": "light", "font_scale": "huge"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.snapshot(), self.before)  # theme bhi light nahi hua

    def test_changing_one_field_never_changes_the_others(self):
        """'theme badla aur font_scale / exam target reset ho gaya' jaisa bug dobara na aaye."""
        alt = {
            "theme": "light", "language": "en", "font_scale": "small", "exam_target": "jee", "class_level": "11",
            "focus_subjects": ["Maths"], "exam_date": TOMORROW, "exam_mode": False, "login_alerts": True,
            "study_reminder_enabled": False, "study_reminder_time": "09:15", "study_reminder_days": [5],
            "focus_schedule_enabled": False, "focus_schedule": [{"days": [3], "start": "10:00", "end": "11:00"}],
            "content_level": "beginner", "content_languages": ["en"],
        }
        self.assertEqual(set(alt), set(PREF_CASES))
        for field, value in alt.items():
            with self.subTest(field=field):
                cache.clear()
                before = self.snapshot()
                r = self.client.patch(PREF_URL, {field: value}, format="json")
                self.assertEqual(r.status_code, 200, r.data)
                after = self.snapshot()
                self.assertNotEqual(after[field], before[field], "test value baseline se alag hona chahiye")
                for other in PREF_CASES:
                    if other != field:
                        self.assertEqual(after[other], before[other], f"{field} badalne se {other} badal gaya")

    def test_empty_patch_and_unknown_fields_change_nothing(self):
        for body in ({}, {"bogus": 1}, {"daily_limit_minutes": 90}, {"user": 999, "id": 999}):
            with self.subTest(body=body):
                cache.clear()
                r = self.client.patch(PREF_URL, body, format="json")
                self.assertEqual(r.status_code, 200, r.data)
                self.assertEqual(self.snapshot(), self.before)
        # daily limit sirf /profile/activity/ se badalta hai, preferences endpoint se nahi
        self.assertIsNone(UserPreference.for_user(self.user).daily_limit_minutes)

    def test_non_object_body_is_a_400_not_a_500(self):
        for body in ([1, 2], "theme", 5):
            with self.subTest(body=body):
                cache.clear()
                r = self.client.patch(PREF_URL, body, format="json")
                self.assertEqual(r.status_code, 400, r.data)
        self.assertEqual(self.snapshot(), self.before)


class PreferenceReadOnlyAndRulesTests(_PrefBase):
    def test_get_on_a_new_account_gives_defaults_without_a_404(self):
        d = self.data()
        self.assertEqual((d["theme"], d["language"], d["font_scale"]), ("system", "en", "normal"))
        self.assertFalse(d["exam_mode"])
        self.assertTrue(d["login_alerts"])
        self.assertFalse(d["study_reminder_enabled"])
        self.assertEqual(d["focus_schedule"], [])
        self.assertEqual(d["content_languages"], [])

    def test_client_cannot_set_read_only_or_derived_fields(self):
        before = self.data()
        self.assertFalse(before["exam_mode_active"])
        r = self.client.patch(
            PREF_URL,
            {
                "updated_at": "2000-01-01T00:00:00Z",
                "exam_mode_active": True,
                "focus_schedule_active": True,
                "focus_now": True,
            },
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        after = self.data()
        self.assertFalse(after["exam_mode_active"])
        self.assertFalse(after["focus_now"])
        self.assertFalse(after["focus_schedule_active"])
        self.assertNotIn("2000-01-01", after["updated_at"])

    def test_exam_mode_active_follows_exam_mode_and_date(self):
        self.client.patch(PREF_URL, {"exam_mode": True, "exam_date": NEXT_MONTH}, format="json")
        self.assertTrue(self.data()["exam_mode_active"])
        self.client.patch(PREF_URL, {"exam_mode": False}, format="json")
        self.assertFalse(self.data()["exam_mode_active"])

    def test_focus_schedule_cannot_be_switched_on_without_a_window(self):
        r = self.client.patch(PREF_URL, {"focus_schedule_enabled": True}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("focus_schedule", r.data["errors"])
        self.assertFalse(self.data()["focus_schedule_enabled"])
        # window ke saath ek hi request me OK
        r = self.client.patch(PREF_URL, {"focus_schedule_enabled": True, "focus_schedule": [WINDOW]}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        # band karna windows nahi mitata
        self.client.patch(PREF_URL, {"focus_schedule_enabled": False}, format="json")
        self.assertEqual(self.data()["focus_schedule"], [WINDOW])

    def test_login_alerts_can_be_turned_off_and_stays_off(self):
        r = self.client.patch(PREF_URL, {"login_alerts": False}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertFalse(UserPreference.for_user(self.user).login_alerts)

    def test_learning_summary_reflects_what_was_saved(self):
        """GET /profile/learning/ (read-only summary) wahi dikhata hai jo preferences/me/ me save hua."""
        self.client.patch(PREF_URL, {"exam_target": "neet", "class_level": "12", "exam_mode": True,
                                     "exam_date": NEXT_MONTH}, format="json")
        r = self.client.get(LEARNING_URL)
        self.assertEqual(r.status_code, 200, r.data)
        prof = r.data["data"]["profile"]
        self.assertEqual(prof["exam_target"], "neet")
        self.assertEqual(prof["class_level"], "12")
        self.assertEqual(prof["exam_date"], NEXT_MONTH)
        self.assertTrue(prof["exam_mode"])
        self.assertTrue(prof["exam_mode_active"])

    def test_etag_changes_after_every_successful_patch_and_not_after_a_failed_one(self):
        e0 = self.client.get(PREF_URL)["ETag"]
        bad = self.client.patch(PREF_URL, {"theme": "blue"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(PREF_URL)["ETag"], e0)  # fail par row chhua hi nahi
        ok = self.client.patch(PREF_URL, {"theme": "dark"}, format="json")
        self.assertNotEqual(ok["ETag"], e0)


# ================================================================================================================
# Privacy matrix (S4)
# ================================================================================================================
PRIVACY_AUDIENCE_OK = [("everyone", "everyone"), ("connections", "connections"), ("nobody", "nobody")]
PRIVACY_AUDIENCE_BAD = ["friends", "", "EVERYONE", None, True, 3]
# S4b -- defaults for new posts / stories: each choice field has its own levels ("nobody" is NOT one of them).
PRIVACY_CONTENT_BAD = ["nobody", "friends", "", "EVERYONE", None, True, 3]
PRIVACY_CASES = {
    **{name: (PRIVACY_AUDIENCE_OK, PRIVACY_AUDIENCE_BAD) for name in privacy_rules.AUDIENCE_FIELDS},
    **{name: (_BOOL_OK, _BOOL_BAD) for name in privacy_rules.BOOLEAN_FIELDS},
    **{name: ([(level, level) for level in levels], PRIVACY_CONTENT_BAD)
       for name, levels in privacy_rules.CONTENT_CHOICE_FIELDS.items()},
    **{name: (_BOOL_OK, _BOOL_BAD) for name in privacy_rules.CONTENT_BOOLEAN_FIELDS},
}


class PrivacyFieldMatrixTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("qa_priv")
        self.client = client_for(self.user)

    def data(self, client=None):
        return (client or self.client).get(PRIVACY_URL).data["data"]

    def test_coverage_guard_every_privacy_field_has_a_case(self):
        declared = set(PrivacySettingsSerializer.Meta.fields) - {"updated_at"}
        self.assertEqual(declared, set(PRIVACY_CASES), "privacy serializer aur PRIVACY_CASES match nahi karte")
        self.assertEqual(set(privacy_rules.ALL_FIELDS), set(PRIVACY_CASES))

    def test_defaults_without_a_row_and_get_writes_nothing(self):
        d = self.data()
        for name, value in privacy_rules.DEFAULTS.items():
            self.assertEqual(d[name], value, name)
        self.assertFalse(PrivacySettings.objects.filter(user=self.user).exists())

    def test_valid_values_save_and_survive_an_app_restart(self):
        for field, (valid, _bad) in PRIVACY_CASES.items():
            for sent, expected in valid:
                with self.subTest(field=field, sent=sent):
                    cache.clear()
                    r = self.client.patch(PRIVACY_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertEqual(r.data["data"][field], expected)
                    self.assertEqual(self.data(client_for(self.user))[field], expected)

    def test_invalid_values_are_rejected_and_nothing_is_saved(self):
        PrivacySettings.for_user(self.user)
        before = self.data()
        for field, (_ok, bad) in PRIVACY_CASES.items():
            for sent in bad:
                with self.subTest(field=field, sent=sent):
                    cache.clear()
                    r = self.client.patch(PRIVACY_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 400, r.data)
                    self.assertIn(field, r.data["errors"])
                    after = self.data()
                    after.pop("updated_at", None)
                    expected = dict(before)
                    expected.pop("updated_at", None)
                    self.assertEqual(after, expected)

    def test_changing_one_control_never_changes_the_others(self):
        self.client.patch(PRIVACY_URL, {name: "nobody" for name in privacy_rules.AUDIENCE_FIELDS}, format="json")
        self.client.patch(PRIVACY_URL, {"hide_from_search": True, "find_by_phone": False, "find_by_email": False},
                          format="json")
        base = self.data()
        for field in privacy_rules.AUDIENCE_FIELDS:
            with self.subTest(field=field):
                cache.clear()
                self.client.patch(PRIVACY_URL, {field: "connections"}, format="json")
                after = self.data()
                for other in PRIVACY_CASES:
                    expected = "connections" if other == field else base[other]
                    self.assertEqual(after[other], expected, f"{field} se {other} badla")
                self.client.patch(PRIVACY_URL, {field: "nobody"}, format="json")

    def test_private_account_flag_and_read_only_fields_are_not_writable_here(self):
        self.assertFalse(self.user.is_private)
        r = self.client.patch(
            PRIVACY_URL, {"is_private": True, "updated_at": "2000-01-01T00:00:00Z", "is_minor": True,
                          "locked_fields": ["message_from"]},
            format="json",
        )
        self.assertEqual(r.status_code, 200, r.data)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_private)  # private account /profile/ se badalta hai
        self.assertFalse(self.data()["is_minor"])
        self.assertEqual(self.data()["locked_fields"], [])

    def test_two_people_never_see_each_others_privacy(self):
        other = make_user("qa_priv_b")
        self.client.patch(PRIVACY_URL, {"message_from": "nobody", "hide_from_search": True}, format="json")
        b = client_for(other)
        self.assertEqual(self.data(b)["message_from"], "everyone")
        self.assertFalse(self.data(b)["hide_from_search"])
        b.patch(PRIVACY_URL, {"message_from": "connections", "user": self.user.pk}, format="json")
        self.assertEqual(self.data()["message_from"], "nobody")  # A par asar nahi
        self.assertEqual(PrivacySettings.objects.filter(user=self.user).count(), 1)


# ================================================================================================================
# Daily limit (PATCH /profile/activity/)
# ================================================================================================================
class DailyLimitFieldTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("qa_limit")
        self.client = client_for(self.user)

    def stored(self):
        return UserPreference.objects.filter(user=self.user).values_list("daily_limit_minutes", flat=True).first()

    def test_valid_values_save_and_come_back_on_get(self):
        for sent, expected in [(5, 5), (60, 60), (1440, 1440), (None, None), (0, None)]:
            with self.subTest(sent=sent):
                cache.clear()
                r = self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": sent}, format="json")
                self.assertEqual(r.status_code, 200, r.data)
                self.assertEqual(r.data["data"]["daily_limit_minutes"], expected)
                self.assertEqual(self.stored(), expected)
                got = client_for(self.user).get(ACTIVITY_URL)
                self.assertEqual(got.status_code, 200)
                self.assertEqual(got.data["data"]["daily_limit_minutes"], expected)

    def test_invalid_values_are_rejected_and_the_old_limit_stays(self):
        self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": 45}, format="json")
        for sent in (1, 4, 1441, -1, "abc", 2.5, [], {}):
            with self.subTest(sent=sent):
                cache.clear()
                r = self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": sent}, format="json")
                self.assertEqual(r.status_code, 400, r.data)
                self.assertIn("daily_limit_minutes", r.data["errors"])
                self.assertEqual(self.stored(), 45)

    def test_missing_field_is_a_400(self):
        r = self.client.patch(ACTIVITY_URL, {}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_setting_the_limit_does_not_touch_other_preferences(self):
        self.client.patch(PREF_URL, PREF_BASELINE, format="json")
        before = self.client.get(PREF_URL).data["data"]
        self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": 90}, format="json")
        after = self.client.get(PREF_URL).data["data"]
        for key in PREF_CASES:
            self.assertEqual(after[key], before[key], key)

    def test_saving_preferences_does_not_clear_the_daily_limit(self):
        self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": 90}, format="json")
        self.client.patch(PREF_URL, PREF_BASELINE, format="json")
        self.assertEqual(self.stored(), 90)

    def test_limit_is_per_user(self):
        other = make_user("qa_limit_b")
        self.client.patch(ACTIVITY_URL, {"daily_limit_minutes": 30}, format="json")
        got = client_for(other).get(ACTIVITY_URL)
        self.assertIsNone(got.data["data"]["daily_limit_minutes"])


# ================================================================================================================
# Wallet settings (S9)
# ================================================================================================================
class WalletSettingsFieldTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("qa_wallet")
        self.client = client_for(self.user)

    def data(self, client=None):
        return (client or self.client).get(WALLET_URL).data["data"]

    def test_confirm_purchases_matrix(self):
        for sent, expected in [(False, False), (True, True), ("false", False), ("true", True)]:
            with self.subTest(sent=sent):
                r = self.client.patch(WALLET_URL, {"confirm_purchases": sent}, format="json")
                self.assertEqual(r.status_code, 200, r.data)
                self.assertEqual(self.data(client_for(self.user))["confirm_purchases"], expected)
        self.client.patch(WALLET_URL, {"confirm_purchases": False}, format="json")
        for bad in ("maybe", 1, None, [], "yes"):
            with self.subTest(bad=bad):
                r = self.client.patch(WALLET_URL, {"confirm_purchases": bad}, format="json")
                self.assertEqual(r.status_code, 400, r.data)
                self.assertFalse(self.data()["confirm_purchases"])  # purani value

    def test_spend_limit_matrix(self):
        for key in ("daily_spend_limit", "monthly_spend_limit"):
            for sent, expected in [(50, 50), ("75", 75), (None, None), ("", None), ("off", None), (1_000_000, 1_000_000)]:
                with self.subTest(key=key, sent=sent):
                    r = self.client.patch(WALLET_URL, {key: sent}, format="json")
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertEqual(self.data(client_for(self.user))[key], expected)
            self.client.patch(WALLET_URL, {key: 40}, format="json")
            for bad in (0, -5, "abc", True, 1_000_001):
                with self.subTest(key=key, bad=bad):
                    r = self.client.patch(WALLET_URL, {key: bad}, format="json")
                    self.assertEqual(r.status_code, 400, r.data)
                    self.assertEqual(self.data()[key], 40)
            self.client.patch(WALLET_URL, {key: None}, format="json")

    def test_daily_cannot_be_above_monthly_and_nothing_is_saved(self):
        self.client.patch(WALLET_URL, {"daily_spend_limit": 100, "monthly_spend_limit": 1000}, format="json")
        r = self.client.patch(WALLET_URL, {"monthly_spend_limit": 50}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.patch(WALLET_URL, {"daily_spend_limit": 2000, "confirm_purchases": False}, format="json")
        self.assertEqual(r.status_code, 400)
        d = self.data()
        self.assertEqual((d["daily_spend_limit"], d["monthly_spend_limit"], d["confirm_purchases"]), (100, 1000, True))

    def test_one_field_does_not_reset_the_others(self):
        self.client.patch(WALLET_URL, {"daily_spend_limit": 100, "monthly_spend_limit": 1000,
                                       "confirm_purchases": False}, format="json")
        self.client.patch(WALLET_URL, {"daily_spend_limit": 80}, format="json")
        d = self.data()
        self.assertEqual((d["daily_spend_limit"], d["monthly_spend_limit"], d["confirm_purchases"]), (80, 1000, False))

    def test_read_only_numbers_cannot_be_set(self):
        r = self.client.patch(WALLET_URL, {"spent_today": 5, "spent_month": 5, "is_minor": True, "effective_daily_limit": 1,
                                           "limits_locked": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        d = self.data()
        self.assertEqual((d["spent_today"], d["spent_month"]), (0, 0))
        self.assertFalse(d["is_minor"])
        self.assertIsNone(d["effective_daily_limit"])

    def test_non_object_body_is_a_400(self):
        r = self.client.patch(WALLET_URL, [1], format="json")
        self.assertEqual(r.status_code, 400)

    def test_isolation(self):
        other = make_user("qa_wallet_b")
        self.client.patch(WALLET_URL, {"daily_spend_limit": 10, "confirm_purchases": False}, format="json")
        b = client_for(other)
        self.assertIsNone(self.data(b)["daily_spend_limit"])
        self.assertTrue(self.data(b)["confirm_purchases"])
        b.patch(WALLET_URL, {"daily_spend_limit": 99, "user": self.user.pk}, format="json")
        self.assertEqual(WalletSettings.objects.get(user=self.user).daily_spend_limit, 10)


# ================================================================================================================
# Teacher live-class defaults (S12)
# ================================================================================================================
TEACHER_INT_CASES = {
    "attendance_min_percent": ([1, 50, 75, 100], [0, 101, "x", True, None, -1]),
    "attendance_edit_days": ([0, 7, 60], [-1, 61, "x", True, None]),
}


class TeacherDefaultsFieldTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("qa_teacher")
        self.client = client_for(self.user)

    def data(self, client=None):
        return (client or self.client).get(TEACHER_URL).data["data"]

    def test_coverage_guard(self):
        served = set(self.data())
        self.assertEqual(served, set(role_rules.TEACHER_BOOL_FIELDS) | set(TEACHER_INT_CASES))

    def test_bool_fields_matrix(self):
        for field in role_rules.TEACHER_BOOL_FIELDS:
            if field == "recording_auto":
                continue  # recording_enabled par depend karta hai - alag test neeche
            for sent in (True, False):
                with self.subTest(field=field, sent=sent):
                    cache.clear()
                    r = self.client.patch(TEACHER_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertEqual(self.data(client_for(self.user))[field], sent)
            before = self.data()
            for bad in ("yes", 1, 0, None, "true", []):
                with self.subTest(field=field, bad=bad):
                    cache.clear()
                    r = self.client.patch(TEACHER_URL, {field: bad}, format="json")
                    self.assertEqual(r.status_code, 400, r.data)
                    self.assertIn(field, r.data["errors"])
                    self.assertEqual(self.data(), before)

    def test_recording_auto_needs_recording(self):
        r = self.client.patch(TEACHER_URL, {"recording_enabled": True, "recording_auto": True}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(self.data()["recording_auto"])
        self.client.patch(TEACHER_URL, {"recording_enabled": False}, format="json")
        d = self.data()
        self.assertFalse(d["recording_enabled"])
        self.assertFalse(d["recording_auto"])  # recording band -> auto bhi band
        for bad in ("yes", 1, None):
            self.assertEqual(self.client.patch(TEACHER_URL, {"recording_auto": bad}, format="json").status_code, 400)

    def test_number_fields_matrix(self):
        for field, (valid, bad) in TEACHER_INT_CASES.items():
            for sent in valid:
                with self.subTest(field=field, sent=sent):
                    cache.clear()
                    r = self.client.patch(TEACHER_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertEqual(self.data(client_for(self.user))[field], sent)
            keep = self.data()[field]
            for sent in bad:
                with self.subTest(field=field, bad=sent):
                    cache.clear()
                    r = self.client.patch(TEACHER_URL, {field: sent}, format="json")
                    self.assertEqual(r.status_code, 400, r.data)
                    self.assertEqual(self.data()[field], keep)

    def test_one_bad_field_saves_nothing_and_one_field_does_not_reset_others(self):
        self.client.patch(TEACHER_URL, {"chat_enabled": False, "attendance_min_percent": 80}, format="json")
        before = self.data()
        r = self.client.patch(TEACHER_URL, {"whiteboard_enabled": False, "attendance_edit_days": 999}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.data(), before)
        self.client.patch(TEACHER_URL, {"parents_enabled": False}, format="json")
        after = self.data()
        self.assertFalse(after["chat_enabled"])
        self.assertEqual(after["attendance_min_percent"], 80)

    def test_non_object_body_is_a_400(self):
        self.assertEqual(self.client.patch(TEACHER_URL, [1], format="json").status_code, 400)

    def test_isolation(self):
        other = make_user("qa_teacher_b")
        self.client.patch(TEACHER_URL, {"chat_enabled": False, "attendance_min_percent": 90}, format="json")
        b = client_for(other)
        self.assertTrue(self.data(b)["chat_enabled"])
        self.assertEqual(self.data(b)["attendance_min_percent"], 50)
        b.patch(TEACHER_URL, {"chat_enabled": True, "user": self.user.pk, "id": 1}, format="json")
        self.assertFalse(self.data()["chat_enabled"])


# ================================================================================================================
# 5 + 6. cross-user isolation (preferences) and login-required (every endpoint)
# ================================================================================================================
class PreferenceIsolationTests(_PrefBase):
    def test_other_users_settings_are_never_visible_or_writable(self):
        other = make_user("qa_pref_b")
        b = client_for(other)
        self.baseline()  # A: sab kuch non-default

        d = self.data(b)  # B abhi tak defaults par
        self.assertEqual((d["theme"], d["language"], d["font_scale"]), ("system", "en", "normal"))
        self.assertFalse(d["exam_mode"])
        self.assertEqual(d["focus_subjects"], [])

        # query-string / body se doosre ka row maangna kaam nahi karta
        got = b.get(PREF_URL, {"user": self.user.pk, "user_id": self.user.pk, "id": self.user.pk})
        self.assertEqual(got.data["data"]["theme"], "system")

        r = b.patch(PREF_URL, {"theme": "light", "user": self.user.pk, "id": self.user.pk, "user_id": self.user.pk},
                    format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.data()["theme"], "dark")  # A ki theme nahi badli
        self.assertEqual(self.data(b)["theme"], "light")
        self.assertEqual(UserPreference.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserPreference.objects.filter(user=other).count(), 1)

    def test_every_field_patched_by_b_leaves_a_untouched(self):
        other = make_user("qa_pref_b2")
        b = client_for(other)
        mine = self.baseline()
        for field, (valid, _bad) in PREF_CASES.items():
            sent = valid[-1][0] if field != "focus_schedule_enabled" else False
            with self.subTest(field=field):
                cache.clear()
                b.patch(PREF_URL, {field: sent}, format="json")
                self.assertEqual(self.snapshot(), mine)

    def test_etag_of_another_user_does_not_unlock_my_write(self):
        other = make_user("qa_pref_b3")
        b = client_for(other)
        b_etag = b.get(PREF_URL)["ETag"]
        self.client.patch(PREF_URL, {"theme": "dark"}, format="json")  # meri row ka etag ab alag
        r = self.client.patch(PREF_URL, {"theme": "light"}, format="json", HTTP_IF_MATCH=b_etag)
        self.assertEqual(r.status_code, 412)
        self.assertEqual(self.data()["theme"], "dark")


class SettingsRequireLoginTests(TestCase):
    def test_every_settings_endpoint_refuses_anonymous_users(self):
        anon = APIClient()
        endpoints = [
            ("get", PREF_URL), ("patch", PREF_URL),
            ("get", PRIVACY_URL), ("patch", PRIVACY_URL),
            ("get", ACTIVITY_URL), ("patch", ACTIVITY_URL),
            ("get", WALLET_URL), ("patch", WALLET_URL),
            ("get", TEACHER_URL), ("patch", TEACHER_URL),
            ("get", LEARNING_URL),
        ]
        for method, url in endpoints:
            with self.subTest(method=method, url=url):
                cache.clear()
                if method == "get":
                    res = anon.get(url)
                else:
                    res = anon.patch(url, {"theme": "dark"}, format="json")
                self.assertIn(res.status_code, (401, 403))
