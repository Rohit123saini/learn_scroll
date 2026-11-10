"""
S13 - Settings QA, notification side (backend). NOT RUN by the author (read-only task, no test / migration / run):
    python manage.py test core.tests_settings_qa

user_profile/tests_settings_qa.py ka hi plan, NotificationPreference (/core/notification-preferences/me/) ke liye:
  1. FIELD MATRIX   har writable field ke valid values save + persist (naya client + GET = "app band -> dobara kholo"),
                    har galat value 400 aur KUCH save nahi (valid field ke saath bheji ho tab bhi - sab ya kuch nahi).
  2. COVERAGE GUARD serializer me naya field aaya aur yahan case nahi -> test fail.
  3. NO SIDE EFFECT ek field badalne se baaki fields nahi badalte.
  4. READ-ONLY      updated_at / dnd_until / derived flags client nahi badal sakta.
  5. ISOLATION      user A ka PATCH B ko nahi lagta (body me "user" / "id" bhejne par bhi); bina login 401/403.
  6. DND / quiet hours / sms-whatsapp opt-in / channel_overrides / sound_prefs ke rules.

Kuch assumptions maine code padh kar likhe hain (error-shape, tolerance) - pehli run par chhoti galti aa sakti hai.
"""
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from login.models import User
from message.push_channels import CHANNEL_CALLS, CHANNEL_CLASSES, CHANNEL_DEFAULT, CHANNEL_SOCIAL

from .channel_policy import record_consent
from .models import Notification, NotificationPreference
from .notification_center import history_days
from .notification_delivery import DND_PRESET_MINUTES
from .serializers import NotificationPreferenceSerializer
from .tests import CoreTestBase

NT = Notification.NotifType
URL = "/core/notification-preferences/me/"
TZ_URL = "/core/notification-preferences/timezone/"
DIGEST_URL = "/core/notification-preferences/digest-preview/"

# Fields the client may never change (server owns them) and derived (method) fields.
READ_ONLY_TIMESTAMPS = {
    "updated_at": "2099-01-01T00:00:00Z",
    "last_digest_sent_at": "2099-01-01T00:00:00Z",
    "last_content_digest_at": "2099-01-01T00:00:00Z",
    "timezone_synced_at": "2099-01-01T00:00:00Z",
    "last_rollup_at": "2099-01-01T00:00:00Z",
    "email_verified_at": "2099-01-01T00:00:00Z",
    "dnd_until": "2099-01-01T00:00:00Z",
}
DERIVED_FIELDS = {
    "email_verified": True,
    "focus_active": True,
    "groups": [],
    "locked_types": [],
    "type_labels": {},
    "sound_channels": [],
    "history_days": 9999,
}

# field -> (valid values, invalid values). Plain fields only; special ones (quiet hours, dnd, overrides, sound, sms)
# have their own test classes below.
SIMPLE_MATRIX = {
    "push_enabled": ([True, False], ["maybe", None, [1]]),
    "email_enabled": ([False, True], ["maybe", None]),
    "focus_priority_only": ([False, True], ["maybe", None]),
    "low_priority_rollup": ([True, False], ["maybe", None]),
    "content_digest_notes": ([False, True], ["maybe", None]),
    "content_digest_topics": ([False, True], ["maybe", None]),
    "digest_frequency": (["daily", "weekly", "off"], ["hourly", "", None, 3]),
    "content_digest_frequency": (["off", "daily", "weekly"], ["monthly", "", None, 3]),
    "timezone_mode": (["manual", "auto"], ["sometimes", "", None]),
    "content_digest_hour": ([0, 7, 18, 23], [24, -1, 99, "abc", None]),
    "timezone": (["Asia/Kolkata", "America/New_York", "UTC"], ["Mars/Phobos", "Not a zone", "", "x" * 65, None]),
    "muted_types": (
        [[], [NT.POST_LIKED.value], [NT.POST_LIKED.value, NT.POST_COMMENTED.value]],
        [["not_a_type"], NT.POST_LIKED.value, {"post_liked": True}, [NT.POST_LIKED.value, "nope"], None],
    ),
}

# Every serializer field must appear in one of these buckets (coverage guard).
COVERED_FIELDS = (
    set(SIMPLE_MATRIX)
    | set(READ_ONLY_TIMESTAMPS)
    | set(DERIVED_FIELDS)
    | {
        "sms_enabled", "whatsapp_enabled",                                   # SmsWhatsappOptInTests
        "quiet_start", "quiet_end",                                          # QuietHoursTests
        "dnd_for_minutes", "dnd_preset", "dnd_custom_until",                 # DndTests
        "channel_overrides", "group_overrides", "reset_channel_overrides",   # ChannelOverrideTests
        "sound_prefs",                                                       # SoundPrefTests
    }
)


class PrefQABase(CoreTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()  # throttles live in the cache; a long matrix must not trip one
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    # -- helpers --------------------------------------------------------------------------------------------
    def patch(self, data, client=None):
        return (client or self.client).patch(URL, data, format="json")

    def get(self, client=None):
        return (client or self.client).get(URL)

    def fresh_client(self, user=None):
        """\"App band karke dobara kholo\" = bilkul naya client, sirf login."""
        c = APIClient()
        c.force_authenticate(user or self.student)
        return c

    def row(self, user=None):
        return NotificationPreference.for_user(user or self.student)

    def snapshot(self, user=None):
        """Saari stored settings (updated_at ke bina) - 'kuch nahi badla' check karne ke liye."""
        data = dict(self.get(self.fresh_client(user)).data)
        data.pop("updated_at", None)
        return data


# ======================================================================================================
# 1 + 2 + 3: field matrix, coverage guard, no side effects
# ======================================================================================================
class DefaultsTests(PrefQABase):
    def test_fresh_user_gets_the_documented_defaults(self):
        d = self.get().data
        self.assertTrue(d["push_enabled"])
        self.assertTrue(d["email_enabled"])
        self.assertFalse(d["sms_enabled"])
        self.assertFalse(d["whatsapp_enabled"])
        self.assertEqual(d["muted_types"], [])
        self.assertEqual(d["digest_frequency"], "off")
        self.assertIsNone(d["quiet_start"])
        self.assertIsNone(d["quiet_end"])
        self.assertEqual(d["timezone"], "UTC")
        self.assertEqual(d["timezone_mode"], "auto")
        self.assertIsNone(d["dnd_until"])
        self.assertTrue(d["focus_priority_only"])
        self.assertFalse(d["low_priority_rollup"])
        self.assertEqual(d["content_digest_frequency"], "weekly")
        self.assertEqual(d["content_digest_hour"], 18)
        self.assertTrue(d["content_digest_notes"])
        self.assertTrue(d["content_digest_topics"])
        self.assertEqual(d["channel_overrides"], {})
        self.assertEqual(d["sound_prefs"], {})
        self.assertEqual(d["history_days"], history_days())

    def test_empty_patch_changes_nothing(self):
        before = self.snapshot()
        self.assertEqual(self.patch({}).status_code, 200)
        self.assertEqual(self.snapshot(), before)


class CoverageGuardTests(PrefQABase):
    def test_every_serializer_field_has_a_qa_case(self):
        names = set(NotificationPreferenceSerializer().fields)
        missing = sorted(names - COVERED_FIELDS)
        self.assertEqual(
            missing, [],
            f"Naya notification-preference field {missing} aaya - core/tests_settings_qa.py me iska case likho "
            f"(SIMPLE_MATRIX ya alag test) aur COVERED_FIELDS me jodo.",
        )

    def test_no_stale_names_in_the_guard(self):
        names = set(NotificationPreferenceSerializer().fields)
        self.assertEqual(sorted(COVERED_FIELDS - names), [], "COVERED_FIELDS me aisa field jo serializer me nahi raha.")


class FieldMatrixTests(PrefQABase):
    def test_every_valid_value_saves_and_survives_an_app_restart(self):
        for field, (valid, _invalid) in SIMPLE_MATRIX.items():
            for value in valid:
                with self.subTest(field=field, value=value):
                    r = self.patch({field: value})
                    self.assertEqual(r.status_code, 200, r.data)
                    self.assertEqual(r.data[field], value)
                    # "app restart": naya client, sirf GET
                    self.assertEqual(self.get(self.fresh_client()).data[field], value)
                    self.assertEqual(getattr(self.row(), field), value)

    def test_every_invalid_value_is_400_and_saves_nothing(self):
        before = self.snapshot()
        for field, (_valid, invalid) in SIMPLE_MATRIX.items():
            for value in invalid:
                with self.subTest(field=field, value=value):
                    r = self.patch({field: value})
                    self.assertEqual(r.status_code, 400, f"{field}={value!r} -> {r.status_code}")
                    self.assertIn(field, r.data.get("errors") or r.data)
                    self.assertEqual(self.snapshot(), before)

    def test_one_bad_field_rejects_the_whole_request(self):
        before = self.snapshot()
        r = self.patch({"push_enabled": False, "timezone": "Mars/Phobos"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.row().push_enabled)

    def test_changing_one_field_does_not_touch_the_others(self):
        # Baseline: har field ko non-default par rakho, phir ek-ek ko badlo.
        self.assertEqual(self.patch({
            "push_enabled": False, "email_enabled": False, "digest_frequency": "daily",
            "content_digest_frequency": "off", "content_digest_hour": 6, "content_digest_notes": False,
            "content_digest_topics": False, "focus_priority_only": False, "low_priority_rollup": True,
            "timezone": "Asia/Kolkata", "timezone_mode": "manual", "muted_types": [NT.POST_LIKED.value],
            "quiet_start": "22:00", "quiet_end": "07:00",
            "channel_overrides": {NT.POST_COMMENTED.value: {"email": False}},
            "sound_prefs": {CHANNEL_SOCIAL: {"sound": "gentle"}},
        }).status_code, 200)
        base = self.snapshot()
        for field, (valid, _invalid) in SIMPLE_MATRIX.items():
            new = next(v for v in valid if v != base[field])
            with self.subTest(field=field):
                self.assertEqual(self.patch({field: new}).status_code, 200)
                after = self.snapshot()
                changed = {k for k in after if after[k] != base[k]}
                self.assertEqual(changed, {field}, f"{field} badalne se {changed - {field}} bhi badla")
                self.patch({field: base[field]})  # wapas baseline par
                self.assertEqual(self.snapshot(), base)


class ReadOnlyFieldTests(PrefQABase):
    def test_client_cannot_set_server_owned_fields(self):
        before = self.snapshot()
        r = self.patch(READ_ONLY_TIMESTAMPS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        row = self.row()
        for field in READ_ONLY_TIMESTAMPS:
            if field == "updated_at":
                continue  # auto_now, har save par badalta hai
            self.assertIsNone(getattr(row, field), field)

    def test_client_cannot_set_derived_fields(self):
        before = self.snapshot()
        r = self.patch(DERIVED_FIELDS)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(r.data["email_verified"])
        self.assertEqual(r.data["history_days"], history_days())

    def test_unknown_fields_are_ignored_not_stored(self):
        before = self.snapshot()
        r = self.patch({"is_staff": True, "user": self.other_student.id, "id": 9999, "nonsense": 1})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.snapshot(), before)
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_staff)

    def test_persisted_mute_really_changes_delivery(self):
        self.patch({"muted_types": [NT.POST_LIKED.value]})
        pref = self.row()
        self.assertEqual(pref.allowed_channels_for(NT.POST_LIKED.value), [])
        self.assertEqual(pref.allowed_channels_for(NT.POST_COMMENTED.value), ["push", "email"])

    def test_master_push_off_really_changes_delivery(self):
        self.patch({"push_enabled": False})
        self.assertNotIn("push", self.row().allowed_channels_for(NT.POST_LIKED.value))


# ======================================================================================================
# Quiet hours
# ======================================================================================================
class QuietHoursTests(PrefQABase):
    def test_pair_saves_and_persists_including_overnight_window(self):
        for start, end in (("22:00", "07:00"), ("09:00", "17:30"), ("00:00", "06:00")):
            with self.subTest(start=start, end=end):
                r = self.patch({"quiet_start": start, "quiet_end": end})
                self.assertEqual(r.status_code, 200, r.data)
                d = self.get(self.fresh_client()).data
                self.assertTrue(d["quiet_start"].startswith(start))
                self.assertTrue(d["quiet_end"].startswith(end))

    def test_only_one_of_the_pair_is_refused(self):
        for body in ({"quiet_start": "22:00"}, {"quiet_end": "07:00"}):
            with self.subTest(body=body):
                before = self.snapshot()
                self.assertEqual(self.patch(body).status_code, 400)
                self.assertEqual(self.snapshot(), before)

    def test_clearing_just_one_of_an_existing_pair_is_refused(self):
        self.patch({"quiet_start": "22:00", "quiet_end": "07:00"})
        before = self.snapshot()
        self.assertEqual(self.patch({"quiet_start": None}).status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_start_equal_to_end_is_refused(self):
        self.assertEqual(self.patch({"quiet_start": "08:00", "quiet_end": "08:00"}).status_code, 400)
        self.assertIsNone(self.row().quiet_start)

    def test_clearing_both_turns_quiet_hours_off(self):
        self.patch({"quiet_start": "22:00", "quiet_end": "07:00"})
        r = self.patch({"quiet_start": None, "quiet_end": None})
        self.assertEqual(r.status_code, 200, r.data)
        row = self.row()
        self.assertIsNone(row.quiet_start)
        self.assertIsNone(row.quiet_end)
        self.assertFalse(row.is_quiet_now())

    def test_garbage_times_are_refused(self):
        for bad in ("25:00", "abc", "7pm", 12):
            with self.subTest(bad=bad):
                before = self.snapshot()
                self.assertEqual(self.patch({"quiet_start": bad, "quiet_end": "07:00"}).status_code, 400)
                self.assertEqual(self.snapshot(), before)


# ======================================================================================================
# Do-not-disturb
# ======================================================================================================
class DndTests(PrefQABase):
    def assert_until(self, expected_minutes, slack=2):
        until = self.row().dnd_until
        self.assertIsNotNone(until)
        delta = (until - timezone.now()).total_seconds() / 60
        self.assertAlmostEqual(delta, expected_minutes, delta=slack)

    def test_dnd_for_minutes_uses_the_server_clock_and_zero_clears(self):
        r = self.patch({"dnd_for_minutes": 30})
        self.assertEqual(r.status_code, 200, r.data)
        self.assert_until(30)
        self.assertTrue(self.row().is_quiet_now())
        self.assertEqual(self.patch({"dnd_for_minutes": 0}).status_code, 200)
        self.assertIsNone(self.row().dnd_until)
        self.assertNotIn("dnd_for_minutes", self.get().data)  # write-only

    def test_dnd_for_minutes_bounds(self):
        before = self.snapshot()
        for bad in (-1, 7 * 24 * 60 + 1, "abc", None):
            with self.subTest(bad=bad):
                self.assertEqual(self.patch({"dnd_for_minutes": bad}).status_code, 400)
                self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.patch({"dnd_for_minutes": 7 * 24 * 60}).status_code, 200)

    def test_presets(self):
        for preset, minutes in DND_PRESET_MINUTES.items():
            with self.subTest(preset=preset):
                self.assertEqual(self.patch({"dnd_preset": preset}).status_code, 200)
                self.assert_until(minutes)
        r = self.patch({"dnd_preset": "tomorrow"})
        self.assertEqual(r.status_code, 200, r.data)
        until = self.row().dnd_until
        self.assertGreater(until, timezone.now())
        self.assertLess(until, timezone.now() + timedelta(hours=26))
        self.assertEqual(self.patch({"dnd_preset": "off"}).status_code, 200)
        self.assertIsNone(self.row().dnd_until)

    def test_custom_preset(self):
        ok = (timezone.now() + timedelta(hours=2)).isoformat()
        r = self.patch({"dnd_preset": "custom", "dnd_custom_until": ok})
        self.assertEqual(r.status_code, 200, r.data)
        self.assert_until(120)

    def test_custom_preset_refusals(self):
        self.patch({"dnd_for_minutes": 0})
        before = self.snapshot()
        now = timezone.now()
        cases = {
            "past": {"dnd_preset": "custom", "dnd_custom_until": (now - timedelta(hours=1)).isoformat()},
            "too far": {"dnd_preset": "custom", "dnd_custom_until": (now + timedelta(days=8)).isoformat()},
            "missing value": {"dnd_preset": "custom"},
            "value alone": {"dnd_custom_until": (now + timedelta(hours=2)).isoformat()},
            "unknown preset": {"dnd_preset": "forever"},
            "preset + minutes": {"dnd_preset": "1h", "dnd_for_minutes": 30},
        }
        for label, body in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.patch(body).status_code, 400, body)
                self.assertEqual(self.snapshot(), before)

    def test_dnd_until_cannot_be_posted_directly(self):
        self.patch({"dnd_until": "2099-01-01T00:00:00Z"})
        self.assertIsNone(self.row().dnd_until)


# ======================================================================================================
# SMS / WhatsApp opt-in (N8): ON needs verified phone + consent, OFF always allowed
# ======================================================================================================
class SmsWhatsappOptInTests(PrefQABase):
    FLAGS = (("sms", "sms_enabled"), ("whatsapp", "whatsapp_enabled"))

    def set_phone(self, user, number, verified):
        User.objects.filter(pk=user.pk).update(phone=number, phone_verified=verified)
        user.refresh_from_db()

    def test_on_without_a_phone_is_refused_with_a_code(self):
        for _channel, flag in self.FLAGS:
            with self.subTest(flag=flag):
                r = self.patch({flag: True})
                self.assertEqual(r.status_code, 400)
                self.assertEqual((r.data.get("errors") or r.data)[flag]["code"], "no_address")
                self.assertFalse(getattr(self.row(), flag))

    def test_on_with_unverified_phone_is_refused(self):
        self.set_phone(self.student, "+919876500001", verified=False)
        for _channel, flag in self.FLAGS:
            with self.subTest(flag=flag):
                r = self.patch({flag: True})
                self.assertEqual(r.status_code, 400)
                self.assertEqual((r.data.get("errors") or r.data)[flag]["code"], "phone_unverified")
                self.assertFalse(getattr(self.row(), flag))

    def test_on_with_verified_phone_but_no_consent_is_refused(self):
        self.set_phone(self.student, "+919876500002", verified=True)
        for _channel, flag in self.FLAGS:
            with self.subTest(flag=flag):
                r = self.patch({flag: True})
                self.assertEqual(r.status_code, 400)
                self.assertEqual((r.data.get("errors") or r.data)[flag]["code"], "no_consent")
                self.assertFalse(getattr(self.row(), flag))

    def test_on_with_verified_phone_and_consent_works_and_persists(self):
        self.set_phone(self.student, "+919876500003", verified=True)
        for channel, flag in self.FLAGS:
            with self.subTest(flag=flag):
                record_consent(self.student, channel, True)
                r = self.patch({flag: True})
                self.assertEqual(r.status_code, 200, r.data)
                self.assertTrue(self.get(self.fresh_client()).data[flag])

    def test_off_is_always_allowed(self):
        NotificationPreference.objects.filter(user=self.student).update(sms_enabled=True, whatsapp_enabled=True)
        for _channel, flag in self.FLAGS:
            with self.subTest(flag=flag):
                self.assertEqual(self.patch({flag: False}).status_code, 200)
                self.assertFalse(getattr(self.row(), flag))

    def test_refused_sms_does_not_save_the_other_fields_in_the_same_request(self):
        r = self.patch({"sms_enabled": True, "push_enabled": False})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(self.row().push_enabled)

    def test_non_bool_values_are_refused(self):
        for _channel, flag in self.FLAGS:
            for bad in ("maybe", None):
                with self.subTest(flag=flag, bad=bad):
                    self.assertEqual(self.patch({flag: bad}).status_code, 400)


# ======================================================================================================
# channel_overrides / group_overrides / reset (N1)
# ======================================================================================================
class ChannelOverrideTests(PrefQABase):
    def locked_type(self):
        locked = sorted(Notification.locked_types())
        self.assertTrue(locked, "locked types khali - N1 ka lock hi nahi bacha")
        return locked[0]

    def test_override_saves_merges_and_persists(self):
        t = NT.POST_LIKED.value
        self.assertEqual(self.patch({"channel_overrides": {t: {"push": False}}}).status_code, 200)
        self.assertEqual(self.patch({"channel_overrides": {t: {"email": False}}}).status_code, 200)
        d = self.get(self.fresh_client()).data
        self.assertEqual(d["channel_overrides"], {t: {"push": False, "email": False}})

    def test_null_and_true_remove_the_override(self):
        t = NT.POST_LIKED.value
        self.patch({"channel_overrides": {t: {"push": False, "email": False}}})
        self.patch({"channel_overrides": {t: {"push": None}}})
        self.assertEqual(self.get().data["channel_overrides"], {t: {"email": False}})
        self.patch({"channel_overrides": {t: {"email": True}}})  # True == default, deviation nahi
        self.assertEqual(self.get().data["channel_overrides"], {})

    def test_one_type_does_not_leak_into_another(self):
        self.patch({"channel_overrides": {NT.POST_LIKED.value: {"email": False}}})
        pref = self.row()
        self.assertEqual(pref.allowed_channels_for(NT.POST_LIKED.value), ["push"])
        self.assertEqual(pref.allowed_channels_for(NT.POST_COMMENTED.value), ["push", "email"])

    def test_bad_shapes_are_refused_and_nothing_is_saved(self):
        before = self.snapshot()
        bad = [
            {"channel_overrides": {"not_a_type": {"push": False}}},
            {"channel_overrides": {NT.POST_LIKED.value: {"fax": False}}},
            {"channel_overrides": {NT.POST_LIKED.value: {"push": "yes"}}},
            {"channel_overrides": [NT.POST_LIKED.value]},
            {"channel_overrides": "post_liked"},
            {"group_overrides": {"no_such_group": {"push": False}}},
            {"group_overrides": {"social": {"fax": False}}},
            {"group_overrides": {"social": {"push": "maybe"}}},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.patch(body).status_code, 400, body)
                self.assertEqual(self.snapshot(), before)

    def test_locked_type_cannot_lose_push_or_in_app_but_can_lose_email(self):
        t = self.locked_type()
        r = self.patch({"channel_overrides": {t: {"push": False, "in_app": False, "email": False}}})
        self.assertEqual(r.status_code, 200, r.data)
        stored = self.get().data["channel_overrides"].get(t, {})
        self.assertNotIn("push", stored)
        self.assertNotIn("in_app", stored)
        self.assertEqual(stored.get("email"), False)
        self.assertIn("push", self.row().allowed_channels_for(t))

    def test_locked_type_cannot_be_muted_away(self):
        t = self.locked_type()
        self.patch({"muted_types": [t]})
        self.assertIn("push", self.row().allowed_channels_for(t))

    def test_group_override_expands_to_every_type_but_skips_locks(self):
        groups = Notification.preference_groups()
        locked = Notification.locked_types()
        key = next(k for k, types in groups.items() if any(x not in locked for x in types))
        r = self.patch({"group_overrides": {key: {"push": False}}})
        self.assertEqual(r.status_code, 200, r.data)
        stored = self.get(self.fresh_client()).data["channel_overrides"]
        for t in groups[key]:
            with self.subTest(type=t):
                if t in locked:
                    self.assertNotIn("push", stored.get(t, {}))
                else:
                    self.assertEqual(stored[t], {"push": False})

    def test_reset_wipes_every_override_and_only_overrides(self):
        self.patch({"push_enabled": False, "channel_overrides": {NT.POST_LIKED.value: {"email": False}}})
        r = self.patch({"reset_channel_overrides": True})
        self.assertEqual(r.status_code, 200, r.data)
        d = self.get(self.fresh_client()).data
        self.assertEqual(d["channel_overrides"], {})
        self.assertFalse(d["push_enabled"])  # reset sirf overrides ko chhoota hai
        self.assertNotIn("reset_channel_overrides", d)  # write-only

    def test_groups_and_locked_types_are_exposed_for_the_matrix_ui(self):
        d = self.get().data
        flat = [t for g in d["groups"] for t in g["types"]]
        self.assertEqual(sorted(flat), sorted(NT.values))
        self.assertEqual(d["locked_types"], sorted(Notification.locked_types()))


# ======================================================================================================
# sound_prefs (N9)
# ======================================================================================================
class SoundPrefTests(PrefQABase):
    def test_valid_prefs_save_merge_and_persist(self):
        r = self.patch({"sound_prefs": {CHANNEL_SOCIAL: {"sound": "gentle", "vibrate": False}}})
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.patch({"sound_prefs": {CHANNEL_CLASSES: {"sound": "silent"}}}).status_code, 200)
        d = self.get(self.fresh_client()).data["sound_prefs"]
        self.assertEqual(d, {CHANNEL_SOCIAL: {"sound": "gentle", "vibrate": False}, CHANNEL_CLASSES: {"sound": "silent"}})

    def test_null_and_defaults_remove_the_entry(self):
        self.patch({"sound_prefs": {CHANNEL_SOCIAL: {"sound": "gentle"}, CHANNEL_CLASSES: {"vibrate": False}}})
        self.patch({"sound_prefs": {CHANNEL_SOCIAL: None}})
        self.assertEqual(self.get().data["sound_prefs"], {CHANNEL_CLASSES: {"vibrate": False}})
        self.patch({"sound_prefs": {CHANNEL_CLASSES: {"vibrate": True}}})  # default value = deviation nahi
        self.assertEqual(self.get().data["sound_prefs"], {})

    def test_bad_values_are_refused_and_nothing_is_saved(self):
        before = self.snapshot()
        bad = [
            {"sound_prefs": {"no_such_channel": {"sound": "gentle"}}},
            {"sound_prefs": {CHANNEL_SOCIAL: {"sound": "loud"}}},
            {"sound_prefs": {CHANNEL_SOCIAL: {"vibrate": "yes"}}},
            {"sound_prefs": {CHANNEL_SOCIAL: {"colour": "red"}}},
            {"sound_prefs": {CHANNEL_SOCIAL: "silent"}},
            {"sound_prefs": ["social"]},
        ]
        for body in bad:
            with self.subTest(body=body):
                self.assertEqual(self.patch(body).status_code, 400, body)
                self.assertEqual(self.snapshot(), before)

    def test_calls_and_safety_alerts_can_never_be_silent(self):
        before = self.snapshot()
        for channel in (CHANNEL_CALLS, CHANNEL_DEFAULT):
            with self.subTest(channel=channel):
                self.assertEqual(self.patch({"sound_prefs": {channel: {"sound": "silent"}}}).status_code, 400)
                self.assertEqual(self.snapshot(), before)
        # lekin vibrate band / gentle chalta hai
        self.assertEqual(self.patch({"sound_prefs": {CHANNEL_CALLS: {"vibrate": False}}}).status_code, 200)

    def test_sound_channels_list_marks_which_can_be_silenced(self):
        by_channel = {c["channel"]: c["can_silence"] for c in self.get().data["sound_channels"]}
        self.assertFalse(by_channel[CHANNEL_CALLS])
        self.assertFalse(by_channel[CHANNEL_DEFAULT])
        self.assertTrue(by_channel[CHANNEL_SOCIAL])


# ======================================================================================================
# Timezone sync endpoint (login / app open) + digest preview
# ======================================================================================================
class TimezoneSyncTests(PrefQABase):
    def test_auto_mode_follows_the_phone(self):
        r = self.client.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data["applied"])
        self.assertEqual(self.row().timezone, "Asia/Kolkata")
        self.assertEqual(self.get(self.fresh_client()).data["timezone"], "Asia/Kolkata")

    def test_manual_mode_keeps_the_users_pick(self):
        self.patch({"timezone": "America/New_York", "timezone_mode": "manual"})
        r = self.client.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["applied"])
        self.assertEqual(r.data["reason"], "manual")
        self.assertEqual(self.row().timezone, "America/New_York")

    def test_bad_zone_is_400_and_changes_nothing(self):
        before = self.snapshot()
        for bad in ("Mars/Phobos", "", None, 5):
            with self.subTest(bad=bad):
                self.assertEqual(self.client.post(TZ_URL, {"timezone": bad}, format="json").status_code, 400)
                self.assertEqual(self.snapshot(), before)

    def test_sync_touches_only_the_callers_row(self):
        other_before = self.snapshot(self.other_student)
        self.client.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")
        self.assertEqual(self.snapshot(self.other_student), other_before)

    def test_digest_preview_is_readable_and_read_only(self):
        r = self.client.get(DIGEST_URL)
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.client.post(DIGEST_URL, {}, format="json").status_code, (404, 405))


# ======================================================================================================
# 5: isolation + auth
# ======================================================================================================
class IsolationTests(PrefQABase):
    def test_patch_never_touches_another_users_row(self):
        other_before = self.snapshot(self.other_student)
        r = self.patch({
            "push_enabled": False, "muted_types": [NT.POST_LIKED.value], "timezone": "Asia/Kolkata",
            "quiet_start": "22:00", "quiet_end": "07:00", "dnd_for_minutes": 60,
            "channel_overrides": {NT.POST_LIKED.value: {"email": False}},
            "sound_prefs": {CHANNEL_SOCIAL: {"sound": "gentle"}},
            "user": self.other_student.id, "id": self.other_student.id, "user_id": self.other_student.id,
        })
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.snapshot(self.other_student), other_before)
        mine = self.snapshot()
        self.assertFalse(mine["push_enabled"])
        self.assertEqual(mine["timezone"], "Asia/Kolkata")

    def test_each_user_reads_only_their_own_values(self):
        self.patch({"timezone": "Asia/Kolkata", "muted_types": [NT.POST_LIKED.value]})
        other = self.fresh_client(self.other_student)
        self.patch({"timezone": "America/New_York"}, client=other)
        self.assertEqual(self.get(self.fresh_client()).data["timezone"], "Asia/Kolkata")
        self.assertEqual(self.get(other).data["muted_types"], [])
        self.assertEqual(self.get(other).data["timezone"], "America/New_York")

    def test_there_is_exactly_one_row_per_user(self):
        for _ in range(3):
            self.get()
            self.patch({"push_enabled": False})
        self.assertEqual(NotificationPreference.objects.filter(user=self.student).count(), 1)

    def test_no_url_exposes_another_users_id(self):
        # URL me koi id nahi hai - doosre ka row maangne ka tareeka hi nahi
        self.assertEqual(self.client.get("/core/notification-preferences/%d/" % self.other_student.id).status_code, 404)

    def test_logged_out_requests_are_refused(self):
        anon = APIClient()
        for label, call in (
            ("get", lambda: anon.get(URL)),
            ("patch", lambda: anon.patch(URL, {"push_enabled": False}, format="json")),
            ("tz", lambda: anon.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")),
            ("digest", lambda: anon.get(DIGEST_URL)),
        ):
            with self.subTest(call=label):
                self.assertIn(call().status_code, (401, 403))
        self.assertTrue(self.row().push_enabled)
