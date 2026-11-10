"""S2 — ETag / If-Match on /profile/preferences/me/. Run: python manage.py test user_profile.tests_pref_etag"""
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from .models import UserPreference
from .pref_etag import if_match_state, if_none_match_hits, make_etag

User = get_user_model()
URL = "/profile/preferences/me/"


class PrefEtagHelperTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="etag_h", password="x")
        self.pref = UserPreference.for_user(self.user)

    def test_etag_is_weak_and_stable(self):
        e = make_etag(self.pref)
        self.assertTrue(e.startswith('W/"') and e.endswith('"'))
        self.assertEqual(e, make_etag(self.pref))

    def test_if_match_states(self):
        e = make_etag(self.pref)
        self.assertEqual(if_match_state(None, e), "absent")
        self.assertEqual(if_match_state("", e), "absent")
        self.assertEqual(if_match_state(e, e), "ok")
        self.assertEqual(if_match_state(e.replace("W/", ""), e), "ok")  # strong form of same value
        self.assertEqual(if_match_state("*", e), "ok")
        self.assertEqual(if_match_state('W/"1"', e), "stale")

    def test_if_none_match(self):
        e = make_etag(self.pref)
        self.assertTrue(if_none_match_hits(e, e))
        self.assertTrue(if_none_match_hits(f'W/"1", {e}', e))
        self.assertFalse(if_none_match_hits('W/"1"', e))
        self.assertFalse(if_none_match_hits(None, e))


class PreferencesEtagApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="etag_u", password="x")
        self.other = User.objects.create_user(username="etag_o", password="x")
        self.client.force_authenticate(self.user)

    def test_get_returns_etag_header(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["ETag"].startswith('W/"'))

    def test_get_if_none_match_gives_304(self):
        etag = self.client.get(URL)["ETag"]
        r = self.client.get(URL, HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(r.status_code, 304)

    def test_get_if_none_match_stale_gives_200(self):
        r = self.client.get(URL, HTTP_IF_NONE_MATCH='W/"1"')
        self.assertEqual(r.status_code, 200)

    def test_patch_without_if_match_still_works(self):
        r = self.client.patch(URL, {"theme": "dark"}, format="json")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r["ETag"])

    def test_patch_with_current_etag_saves_and_returns_new_etag(self):
        etag = self.client.get(URL)["ETag"]
        r = self.client.patch(URL, {"theme": "dark"}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotEqual(r["ETag"], etag)  # updated_at badla => naya etag
        self.assertEqual(UserPreference.for_user(self.user).theme, "dark")

    def test_patch_with_stale_etag_gives_412_and_saves_nothing(self):
        stale = self.client.get(URL)["ETag"]
        # "doosra device" pehle save kar deta hai
        self.client.patch(URL, {"theme": "light"}, format="json")
        r = self.client.patch(URL, {"theme": "dark"}, format="json", HTTP_IF_MATCH=stale)
        self.assertEqual(r.status_code, 412)
        self.assertFalse(r.data["status"])
        self.assertEqual(r.data["code"], "preferences_conflict")
        self.assertEqual(r.data["data"]["theme"], "light")  # current server value wapas
        self.assertEqual(r["ETag"], make_etag(UserPreference.for_user(self.user)))
        self.assertEqual(UserPreference.for_user(self.user).theme, "light")

    def test_retry_after_412_with_fresh_etag_succeeds(self):
        stale = self.client.get(URL)["ETag"]
        self.client.patch(URL, {"theme": "light"}, format="json")
        r1 = self.client.patch(URL, {"font_scale": "large"}, format="json", HTTP_IF_MATCH=stale)
        self.assertEqual(r1.status_code, 412)
        r2 = self.client.patch(URL, {"font_scale": "large"}, format="json", HTTP_IF_MATCH=r1["ETag"])
        self.assertEqual(r2.status_code, 200, r2.data)
        pref = UserPreference.for_user(self.user)
        self.assertEqual((pref.theme, pref.font_scale), ("light", "large"))

    def test_validation_error_with_valid_etag_is_400_not_412(self):
        etag = self.client.get(URL)["ETag"]
        r = self.client.patch(URL, {"font_scale": "huge"}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 400)
        self.assertIn("font_scale", r.data["errors"])

    def test_etag_is_per_user(self):
        mine = self.client.get(URL)["ETag"]
        self.client.force_authenticate(self.other)
        self.client.patch(URL, {"theme": "dark"}, format="json")
        self.client.force_authenticate(self.user)
        r = self.client.patch(URL, {"theme": "light"}, format="json", HTTP_IF_MATCH=mine)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(UserPreference.for_user(self.other).theme, "dark")

    def test_anonymous_cannot_read_or_write(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(URL).status_code, (401, 403))
        self.assertIn(self.client.patch(URL, {"theme": "dark"}, format="json").status_code, (401, 403))
