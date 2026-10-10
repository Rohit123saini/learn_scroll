"""Minor safety: age rules, signup DOB, private lock, adult->minor DM gate, discovery."""
from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from login import age
from login.models import OTPVerification
from login.serializers import SignupSerializer
from message.minor_safety import dm_blocked_for_minor
from user_profile.discovery import suggested_users_queryset
from user_profile.models import Follow

User = get_user_model()


def years_ago(n, extra_days=0):
    t = timezone.localdate()
    try:
        d = t.replace(year=t.year - n)
    except ValueError:
        d = t.replace(year=t.year - n, day=28)
    return d - timedelta(days=extra_days)


class AgeHelperTests(SimpleTestCase):
    def test_age_boundaries(self):
        today = date(2026, 10, 9)
        self.assertEqual(age.age_on(date(2008, 10, 9), today), 18)   # birthday today
        self.assertEqual(age.age_on(date(2008, 10, 10), today), 17)  # tomorrow
        self.assertTrue(age.is_minor_dob(date(2008, 10, 10), today))
        self.assertFalse(age.is_minor_dob(date(2008, 10, 9), today))

    def test_unknown_dob_is_neither_minor_nor_adult(self):
        self.assertFalse(age.is_minor_dob(None))
        self.assertFalse(age.is_adult_dob(None))

    def test_dob_errors(self):
        today = date(2026, 10, 9)
        self.assertIsNotNone(age.dob_error(date(2026, 10, 10), today))   # future
        self.assertIsNotNone(age.dob_error(date(1900, 1, 1), today))     # absurd
        self.assertIsNotNone(age.dob_error(date(2016, 1, 1), today))     # 10 y/o < 13
        self.assertIsNone(age.dob_error(date(2012, 10, 9), today))       # exactly 14
        self.assertIsNone(age.dob_error(date(2013, 10, 9), today))       # exactly 13

    def test_cutoff_feb29(self):
        self.assertEqual(age.minor_cutoff_date(date(2028, 2, 29)), date(2010, 2, 28))
        self.assertEqual(age.minor_cutoff_date(date(2026, 10, 9)), date(2008, 10, 9))


class SignupDobTests(TestCase):
    def _data(self, **kw):
        d = dict(username="kid1", email="kid1@example.com", first_name="K", last_name="Id",
                 password="Str0ng!Pass#1", confirm_password="Str0ng!Pass#1")
        d.update(kw)
        return d

    def _verified(self, email):
        OTPVerification.objects.create(target=email, otp_hash="x", is_verified=True)

    def test_dob_required(self):
        s = SignupSerializer(data=self._data())
        self.assertFalse(s.is_valid())
        self.assertIn("date_of_birth", s.errors)

    def test_under_13_rejected(self):
        s = SignupSerializer(data=self._data(date_of_birth=years_ago(12).isoformat()))
        self.assertFalse(s.is_valid())
        self.assertIn("date_of_birth", s.errors)

    def test_teen_account_created_private(self):
        self._verified("kid1@example.com")
        s = SignupSerializer(data=self._data(date_of_birth=years_ago(15).isoformat()))
        self.assertTrue(s.is_valid(), s.errors)
        u = s.save()
        self.assertTrue(u.is_private)
        self.assertTrue(u.is_minor)

    def test_adult_account_stays_public(self):
        self._verified("kid1@example.com")
        s = SignupSerializer(data=self._data(date_of_birth=years_ago(25).isoformat()))
        self.assertTrue(s.is_valid(), s.errors)
        u = s.save()
        self.assertFalse(u.is_private)
        self.assertTrue(u.is_verified_adult)


class SetDobEndpointTests(APITestCase):
    URL = "/login/auth/set-dob/"

    def setUp(self):
        self.u = User.objects.create_user(username="nodob", password="x")
        self.client.force_authenticate(self.u)

    def _post(self, dob):
        return self.client.post(self.URL, {"date_of_birth": dob.isoformat()}, format="json")

    def test_sets_once_and_locks_private_for_minor(self):
        r = self._post(years_ago(16))
        self.assertEqual(r.status_code, 200, r.content)
        self.u.refresh_from_db()
        self.assertTrue(self.u.is_private)
        self.assertEqual(self._post(years_ago(30)).status_code, 409)   # can't re-enter an adult DOB

    def test_under_13_rejected_and_nothing_saved(self):
        self.assertEqual(self._post(years_ago(10)).status_code, 400)
        self.u.refresh_from_db()
        self.assertIsNone(self.u.date_of_birth)


class PrivateLockTests(APITestCase):
    def test_minor_cannot_go_public(self):
        u = User.objects.create_user(username="teen", password="x", date_of_birth=years_ago(15), is_private=True)
        self.client.force_authenticate(u)
        r = self.client.patch("/profile/update/", {"is_private": "false"}, format="multipart")
        self.assertEqual(r.status_code, 400, r.content)
        u.refresh_from_db()
        self.assertTrue(u.is_private)


class DmGateTests(TestCase):
    def setUp(self):
        self.adult = User.objects.create_user(username="adult", password="x", date_of_birth=years_ago(30))
        self.teen = User.objects.create_user(username="teen2", password="x", date_of_birth=years_ago(15))
        self.teen_b = User.objects.create_user(username="teen3", password="x", date_of_birth=years_ago(16))
        self.unknown = User.objects.create_user(username="unk", password="x")

    def test_adult_to_unfollowing_minor_blocked(self):
        self.assertTrue(dm_blocked_for_minor(self.adult, self.teen))

    def test_minor_who_follows_adult_unblocks(self):
        Follow.objects.create(follower=self.teen, following=self.adult, status=Follow.Status.ACCEPTED)
        self.assertFalse(dm_blocked_for_minor(self.adult, self.teen))

    def test_pending_follow_does_not_unblock(self):
        Follow.objects.create(follower=self.teen, following=self.adult, status=Follow.Status.PENDING)
        self.assertTrue(dm_blocked_for_minor(self.adult, self.teen))

    def test_other_pairs_unaffected(self):
        self.assertFalse(dm_blocked_for_minor(self.teen, self.adult))        # minor -> adult
        self.assertFalse(dm_blocked_for_minor(self.teen, self.teen_b))       # minor <-> minor
        self.assertFalse(dm_blocked_for_minor(self.unknown, self.teen))      # unknown DOB sender
        self.assertFalse(dm_blocked_for_minor(self.adult, self.unknown))     # unknown DOB receiver


class DiscoveryTests(TestCase):
    def test_adult_viewer_not_suggested_minors(self):
        adult = User.objects.create_user(username="v_adult", password="x", date_of_birth=years_ago(30))
        teen = User.objects.create_user(username="s_teen", password="x", date_of_birth=years_ago(15))
        grown = User.objects.create_user(username="s_grown", password="x", date_of_birth=years_ago(40))
        unknown = User.objects.create_user(username="s_unknown", password="x")
        ids = set(suggested_users_queryset(adult).values_list("id", flat=True))
        self.assertNotIn(teen.id, ids)
        self.assertIn(grown.id, ids)
        self.assertIn(unknown.id, ids)

    def test_minor_viewer_still_sees_other_minors(self):
        teen = User.objects.create_user(username="v_teen", password="x", date_of_birth=years_ago(15))
        other = User.objects.create_user(username="s_teen2", password="x", date_of_birth=years_ago(16))
        ids = set(suggested_users_queryset(teen).values_list("id", flat=True))
        self.assertIn(other.id, ids)
