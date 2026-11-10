"""
user_profile/tests_profile_edit.py

P7 -- edit profile polish (backend): bio limit (150 / 300 verified), date of birth set-once, birthday visibility
(never for minors, year never exposed), owner-only `edit_info`.            Written, NOT run.
Run:  python manage.py test user_profile.tests_profile_edit
"""
from datetime import date

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from . import profile_header

User = get_user_model()
URL = "/profile/update/"


def _years_ago(n):
    t = date.today()
    try:
        return t.replace(year=t.year - n)
    except ValueError:  # 29 Feb
        return t.replace(year=t.year - n, day=28)


class BioLimitTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="p7_bio", email="p7_bio@example.com", password="pass12345")
        self.client.force_authenticate(self.user)

    def test_limits(self):
        self.assertEqual(profile_header.bio_limit(self.user), 150)
        self.user.is_verified = True
        self.assertEqual(profile_header.bio_limit(self.user), 300)

    def test_151_chars_rejected_for_normal_account(self):
        r = self.client.patch(URL, {"bio": "a" * 151}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("bio", r.json()["errors"])

    def test_150_ok(self):
        r = self.client.patch(URL, {"bio": "a" * 150}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)

    def test_verified_gets_300(self):
        self.user.is_verified = True
        self.user.save(update_fields=["is_verified"])
        self.assertEqual(self.client.patch(URL, {"bio": "a" * 300}, format="json").status_code, 200)
        self.assertEqual(self.client.patch(URL, {"bio": "a" * 301}, format="json").status_code, 400)

    def test_old_long_bio_does_not_block_other_edits(self):
        User.objects.filter(pk=self.user.pk).update(bio="b" * 400)  # saved before the limit existed
        r = self.client.patch(URL, {"pronouns": "she/her", "bio": "b" * 400}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)


class DobAndBirthdayTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="p7_dob", email="p7_dob@example.com", password="pass12345")
        self.client.force_authenticate(self.user)

    def test_dob_can_be_set_once(self):
        d = _years_ago(25).isoformat()
        r = self.client.patch(URL, {"date_of_birth": d}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.date_of_birth.isoformat(), d)
        r2 = self.client.patch(URL, {"date_of_birth": _years_ago(30).isoformat()}, format="json")
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)
        # the same value again is not an error (a form that always sends it)
        self.assertEqual(self.client.patch(URL, {"date_of_birth": d}, format="json").status_code, 200)

    def test_error_body_carries_dob_code(self):
        # the app shows its own EN/HI text per code (dob_future | dob_too_young | dob_locked | dob_invalid)
        r = self.client.patch(URL, {"date_of_birth": _years_ago(5).isoformat()}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_of_birth", r.json()["errors"])
        self.assertTrue(r.json().get("dob_code"))
        self.client.patch(URL, {"date_of_birth": _years_ago(25).isoformat()}, format="json")
        r2 = self.client.patch(URL, {"date_of_birth": _years_ago(30).isoformat()}, format="json")
        self.assertEqual(r2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(r2.json().get("dob_code"), "dob_locked")

    def test_future_and_too_young_rejected(self):
        for bad in [date.today().replace(year=date.today().year + 1).isoformat(), _years_ago(10).isoformat()]:
            self.assertEqual(self.client.patch(URL, {"date_of_birth": bad}, format="json").status_code, 400, bad)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.date_of_birth)

    def test_minor_dob_forces_private_and_hides_birthday(self):
        r = self.client.patch(URL, {"date_of_birth": _years_ago(15).isoformat(), "show_birthday": False}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_private)
        self.assertFalse(self.user.show_birthday)

    def test_birthday_cannot_be_shown_by_a_minor(self):
        User.objects.filter(pk=self.user.pk).update(date_of_birth=_years_ago(16))
        r = self.client.patch(URL, {"show_birthday": True}, format="json")
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_birthday_needs_a_dob(self):
        self.assertEqual(self.client.patch(URL, {"show_birthday": True}, format="json").status_code, 400)

    def test_adult_can_show_birthday_and_year_is_not_exposed(self):
        dob = date(date.today().year - 25, 8, 12)
        User.objects.filter(pk=self.user.pk).update(date_of_birth=dob)
        r = self.client.patch(URL, {"show_birthday": True}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        self.user.refresh_from_db()
        self.assertEqual(profile_header.birthday_label(self.user), "08-12")  # MM-DD only

        other = User.objects.create_user(username="p7_other", email="p7_other@example.com", password="pass12345")
        from .serializers import TargetUserProfileSerializer

        class _Req:
            user = other

        data = TargetUserProfileSerializer(self.user, context={"request": _Req()}).data
        self.assertEqual(data["birthday"], "08-12")
        self.assertNotIn("edit_info", data)  # owner only
        self.assertNotIn("date_of_birth", data)

    def test_birthday_hidden_when_switch_off(self):
        User.objects.filter(pk=self.user.pk).update(date_of_birth=_years_ago(25), show_birthday=False)
        self.user.refresh_from_db()
        self.assertIsNone(profile_header.birthday_label(self.user))


class EditInfoTests(APITestCase):
    def test_owner_gets_edit_info(self):
        u = User.objects.create_user(username="p7_info", email="p7_info@example.com", password="pass12345")
        self.client.force_authenticate(u)
        r = self.client.patch(URL, {"pronouns": "they/them"}, format="json")
        info = r.json()["data"]["edit_info"]
        self.assertEqual(info["bio_limit"], 150)
        self.assertFalse(info["dob_locked"])
        self.assertFalse(info["can_show_birthday"])
