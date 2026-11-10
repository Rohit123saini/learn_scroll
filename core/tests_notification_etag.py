"""S2 / R14 — ETag / If-Match on core/notification-preferences/me/ (NOT RUN).
python manage.py test core.tests_notification_etag
"""
from rest_framework import status
from rest_framework.test import APIClient

from .models import NotificationPreference
from .tests import CoreTestBase

URL = "/core/notification-preferences/me/"


class NotificationPrefEtagTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()  # CoreTestBase plain TestCase hai
        self.client.force_authenticate(self.student)

    def test_get_has_etag_and_body_has_no_envelope(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertTrue(r["ETag"].startswith('W/"'))
        self.assertIn("push_enabled", r.json())  # seedha serializer data, {"data": ...} nahi

    def test_if_none_match_304(self):
        etag = self.client.get(URL)["ETag"]
        self.assertEqual(self.client.get(URL, HTTP_IF_NONE_MATCH=etag).status_code, 304)

    def test_patch_without_if_match_unchanged_behaviour(self):
        r = self.client.patch(URL, {"push_enabled": False}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r["ETag"])
        self.assertFalse(NotificationPreference.for_user(self.student).push_enabled)

    def test_patch_with_current_etag_ok_and_new_etag_returned(self):
        etag = self.client.get(URL)["ETag"]
        r = self.client.patch(URL, {"email_enabled": False}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r["ETag"], etag)

    def test_stale_if_match_gives_412_with_current_prefs_and_saves_nothing(self):
        etag = self.client.get(URL)["ETag"]
        self.client.patch(URL, {"push_enabled": False}, format="json")  # doosre device ne badla
        r = self.client.patch(URL, {"email_enabled": False}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 412)
        self.assertFalse(r.json()["push_enabled"])  # current server state body me
        self.assertTrue(NotificationPreference.for_user(self.student).email_enabled)  # save nahi hua

    def test_validation_error_is_still_400_with_if_match(self):
        etag = self.client.get(URL)["ETag"]
        r = self.client.patch(URL, {"digest_frequency": "every_second"}, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(r.status_code, 400)
