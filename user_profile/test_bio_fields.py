"""
user_profile/test_bio_fields.py

P6-BE — pronouns / category_label / links on the profile.
Run:  python manage.py test user_profile.test_bio_fields

⚠️ UPDATE_PROFILE_URL: urls.py wasn't part of the upload — set it to the real route of
UpdateProfileView (PATCH). Everything else is URL-independent.
"""
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.test import APITestCase

from login.models import clean_profile_links
from .serializers import ProfileUpdateSerializer, TargetUserProfileSerializer, UserProfileSerializer

User = get_user_model()

UPDATE_PROFILE_URL = "/profile/update/"  # <-- adjust to your urls.py

OK_LINK = {"title": "Website", "url": "https://example.com"}


class CleanLinksTests(APITestCase):
    def test_valid_links_are_normalised(self):
        out = clean_profile_links([{"title": "  Site ", "url": " https://example.com/a ", "junk": 1}])
        self.assertEqual(out, [{"title": "Site", "url": "https://example.com/a"}])

    def test_rejects_non_http_schemes_and_garbage(self):
        for bad in ["javascript:alert(1)", "ftp://example.com", "data:text/html,hi",
                    "example.com", "http://", "not a url", ""]:
            with self.assertRaises(ValidationError, msg=bad):
                clean_profile_links([{"title": "x", "url": bad}])

    def test_rejects_fourth_link_and_bad_shapes(self):
        with self.assertRaises(ValidationError):
            clean_profile_links([OK_LINK] * 4)
        for bad in ["str", {"title": "a", "url": "https://a.com"}, [1], [{"title": "", "url": "https://a.com"}],
                    [{"title": "a"}], [{"url": "https://a.com"}], [{"title": 1, "url": "https://a.com"}]]:
            with self.assertRaises(ValidationError, msg=str(bad)):
                clean_profile_links(bad)

    def test_three_links_ok_and_empty_ok(self):
        self.assertEqual(len(clean_profile_links([OK_LINK] * 3)), 3)
        self.assertEqual(clean_profile_links([]), [])


class ProfileUpdateApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bio_u", email="bio_u@example.com", password="pass12345")
        self.client.force_authenticate(self.user)

    def _patch(self, data, fmt="json"):
        return self.client.patch(UPDATE_PROFILE_URL, data, format=fmt)

    def test_defaults(self):
        self.assertEqual((self.user.pronouns, self.user.category_label, self.user.links), ("", "", []))

    def test_save_all_three_fields_json(self):
        r = self._patch({"pronouns": "she/her", "category_label": "JEE Aspirant", "links": [OK_LINK]})
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pronouns, "she/her")
        self.assertEqual(self.user.category_label, "JEE Aspirant")
        self.assertEqual(self.user.links, [OK_LINK])
        self.assertEqual(r.json()["data"]["links"], [OK_LINK])

    def test_links_over_multipart_as_json_string(self):
        import json
        r = self._patch({"links": json.dumps([OK_LINK])}, fmt="multipart")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.links, [OK_LINK])

    def test_invalid_url_is_400(self):
        r = self._patch({"links": [{"title": "x", "url": "javascript:alert(1)"}]})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("links", r.json()["errors"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.links, [])

    def test_fourth_link_is_400(self):
        r = self._patch({"links": [OK_LINK] * 4})
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("links", r.json()["errors"])

    def test_broken_json_string_is_400(self):
        r = self._patch({"links": "[{oops"}, fmt="multipart")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_length_limits(self):
        self.assertEqual(self._patch({"pronouns": "x" * 31}).status_code, 400)
        self.assertEqual(self._patch({"category_label": "x" * 41}).status_code, 400)
        self.assertEqual(self._patch({"pronouns": "x" * 30, "category_label": "y" * 40}).status_code, 200)

    def test_omitting_links_keeps_them_and_empty_list_clears(self):
        self._patch({"links": [OK_LINK]})
        self._patch({"bio": "hi"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.links, [OK_LINK])
        self._patch({"links": []})
        self.user.refresh_from_db()
        self.assertEqual(self.user.links, [])


class ProfileReadTests(APITestCase):
    def test_new_fields_exposed_on_own_and_target_serializers(self):
        u = User.objects.create_user(username="bio_r", email="bio_r@example.com", password="pass12345")
        User.objects.filter(pk=u.pk).update(pronouns="he/him", category_label="Teacher", links=[OK_LINK])
        u.refresh_from_db()
        for ser in (UserProfileSerializer, TargetUserProfileSerializer):
            d = ser(u).data
            self.assertEqual((d["pronouns"], d["category_label"], d["links"]), ("he/him", "Teacher", [OK_LINK]))
