"""S2 / R14 — appearance + data_storage account sync on /profile/preferences/me/.

NOT RUN. Chalane ke liye: python manage.py test user_profile.tests_pref_sync
(pehle migration 0023 apply karni hogi: python manage.py migrate user_profile)
"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from . import pref_sync
from .models import UserPreference

User = get_user_model()
URL = "/profile/preferences/me/"


class PrefSyncSchemaTests(APITestCase):
    def test_valid_partial_passes_through(self):
        out = pref_sync.normalize_partial({"accent": "teal", "amoled": True}, pref_sync.APPEARANCE_SCHEMA)
        self.assertEqual(out, {"accent": "teal", "amoled": True})

    def test_unknown_key_rejected(self):
        with self.assertRaises(pref_sync.PrefSyncError) as cm:
            pref_sync.normalize_partial({"haptics": False}, pref_sync.APPEARANCE_SCHEMA)  # haptics device-only
        self.assertIn("haptics", cm.exception.errors)

    def test_wrong_type_and_value_rejected_per_key(self):
        with self.assertRaises(pref_sync.PrefSyncError) as cm:
            pref_sync.normalize_partial(
                {"amoled": "yes", "accent": "purple", "hinglish": True}, pref_sync.APPEARANCE_SCHEMA)
        self.assertEqual(set(cm.exception.errors), {"amoled", "accent"})  # hinglish theek tha

    def test_int_is_not_a_bool(self):
        with self.assertRaises(pref_sync.PrefSyncError):
            pref_sync.normalize_partial({"amoled": 1}, pref_sync.APPEARANCE_SCHEMA)

    def test_null_not_allowed(self):
        with self.assertRaises(pref_sync.PrefSyncError):
            pref_sync.normalize_partial({"accent": None}, pref_sync.APPEARANCE_SCHEMA)

    def test_not_a_dict(self):
        with self.assertRaises(pref_sync.PrefSyncError):
            pref_sync.normalize_partial(["accent"], pref_sync.APPEARANCE_SCHEMA)

    def test_photo_rule_has_no_never(self):
        with self.assertRaises(pref_sync.PrefSyncError):
            pref_sync.normalize_partial({"photo_rule": "never"}, pref_sync.DATA_STORAGE_SCHEMA)
        self.assertEqual(
            pref_sync.normalize_partial({"autoplay_rule": "never"}, pref_sync.DATA_STORAGE_SCHEMA),
            {"autoplay_rule": "never"})

    def test_merge_drops_keys_not_in_schema(self):
        merged = pref_sync.merge_stored({"accent": "blue", "old_junk": 1}, {"amoled": True}, pref_sync.APPEARANCE_SCHEMA)
        self.assertEqual(merged, {"accent": "blue", "amoled": True})


class PrefSyncApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="sync_u", password="x")
        self.client.force_authenticate(self.user)

    def patch(self, body, **extra):
        return self.client.patch(URL, body, format="json", **extra)

    def test_defaults_are_empty_dicts(self):
        data = self.client.get(URL).json()["data"]
        self.assertEqual(data["appearance"], {})
        self.assertEqual(data["data_storage"], {})

    def test_patch_saves_and_get_returns(self):
        r = self.patch({"appearance": {"accent": "green", "high_contrast": True}})
        self.assertEqual(r.status_code, 200)
        data = self.client.get(URL).json()["data"]
        self.assertEqual(data["appearance"], {"accent": "green", "high_contrast": True})
        self.assertEqual(UserPreference.for_user(self.user).appearance["accent"], "green")

    def test_patch_merges_instead_of_replacing(self):
        self.patch({"appearance": {"accent": "green"}})
        self.patch({"appearance": {"bold_text": True}})  # doosra device: alag key
        self.assertEqual(
            self.client.get(URL).json()["data"]["appearance"], {"accent": "green", "bold_text": True})

    def test_same_key_last_write_wins(self):
        self.patch({"appearance": {"accent": "green"}})
        self.patch({"appearance": {"accent": "pink"}})
        self.assertEqual(self.client.get(URL).json()["data"]["appearance"], {"accent": "pink"})

    def test_data_storage_roundtrip(self):
        r = self.patch({"data_storage": {"video_quality": "low", "autoplay_rule": "wifi_only", "data_saver": True}})
        self.assertEqual(r.status_code, 200)
        ds = self.client.get(URL).json()["data"]["data_storage"]
        self.assertEqual(ds, {"video_quality": "low", "autoplay_rule": "wifi_only", "data_saver": True})

    def test_invalid_value_gives_nested_400_and_saves_nothing(self):
        before = self.client.get(URL).json()["data"]
        r = self.patch({"theme": "dark", "appearance": {"accent": "purple"}})
        self.assertEqual(r.status_code, 400)
        errs = r.json()["errors"]
        self.assertIn("accent", errs["appearance"])  # per-key error, client sirf wahi key rollback karta hai
        after = self.client.get(URL).json()["data"]
        self.assertEqual(before["theme"], after["theme"])  # poora PATCH reject hua, theme nahi badla
        self.assertEqual(after["appearance"], {})

    def test_if_match_conflict_applies_to_new_fields_too(self):
        etag = self.client.get(URL)["ETag"]
        self.patch({"appearance": {"accent": "blue"}})  # doosre device ne badla -> etag badla
        r = self.patch({"appearance": {"amoled": True}}, HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 412)
        self.assertEqual(r.json()["data"]["appearance"], {"accent": "blue"})
        self.assertEqual(UserPreference.for_user(self.user).appearance, {"accent": "blue"})  # kuch save nahi hua

    def test_other_users_rows_untouched(self):
        other = User.objects.create_user(username="sync_o", password="x")
        self.patch({"appearance": {"accent": "orange"}})
        self.assertEqual(UserPreference.for_user(other).appearance, {})
