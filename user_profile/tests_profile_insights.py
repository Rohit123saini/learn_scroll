"""
user_profile/tests_profile_insights.py  (P9 - profile insights)

Likhe gaye hain, CHALAYE NAHI gaye.  Run:
    python manage.py test user_profile.tests_profile_insights

Covers
  * pure helpers: clamp_days, change_pct, window_dates
  * record_visit: counts once per (viewer, day) + increments `visits`, never for self / blocked / minor / anonymous,
    never raises
  * opening a profile (GET /profile/profile/<username>/) records a visit; your own profile and a blocked viewer do not
  * GET /profile/insights/: shape, windows (current vs previous), unique vs total visits, follower growth (ACCEPTED only,
    PENDING ignored), nothing about WHO visited, other people's data never leaks, auth required, bad `days` -> default
  * prune_old_visits
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile import profile_insights as pi
from user_profile.models import BlockUser, Follow, ProfileVisit

User = get_user_model()


def _user(name, **kw):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", **kw)


def _visit(profile, viewer, days_ago=0, visits=1):
    return ProfileVisit.objects.create(
        profile=profile, viewer=viewer, day=timezone.localdate() - timedelta(days=days_ago), visits=visits
    )


def _follow(follower, following, status=Follow.Status.ACCEPTED, days_ago=0):
    row = Follow.objects.create(follower=follower, following=following, status=status)
    if days_ago:
        Follow.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
    return row


class HelperTests(SimpleTestCase):
    def test_clamp_days(self):
        self.assertEqual(pi.clamp_days(None), 7)
        self.assertEqual(pi.clamp_days("abc"), 7)
        self.assertEqual(pi.clamp_days("30"), 30)
        self.assertEqual(pi.clamp_days(0), 1)
        self.assertEqual(pi.clamp_days(-5), 1)
        self.assertEqual(pi.clamp_days(500), 90)

    def test_change_pct(self):
        self.assertIsNone(pi.change_pct(5, 0))
        self.assertEqual(pi.change_pct(15, 10), 50.0)
        self.assertEqual(pi.change_pct(5, 10), -50.0)
        self.assertEqual(pi.change_pct(10, 10), 0.0)
        self.assertEqual(pi.change_pct(0, 4), -100.0)

    def test_window_dates(self):
        from datetime import date

        start, prev = pi.window_dates(date(2026, 10, 27), 7)
        self.assertEqual(start, date(2026, 10, 21))  # 7 days including today
        self.assertEqual(prev, date(2026, 10, 14))


class RecordVisitTests(APITestCase):
    def setUp(self):
        self.owner = _user("owner")
        self.viewer = _user("viewer")

    def test_first_visit_creates_a_row_second_increments_it(self):
        self.assertTrue(pi.record_visit(self.owner, self.viewer))
        self.assertTrue(pi.record_visit(self.owner, self.viewer))
        rows = ProfileVisit.objects.filter(profile=self.owner)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().visits, 2)

    def test_not_for_yourself(self):
        self.assertFalse(pi.record_visit(self.owner, self.owner))
        self.assertFalse(ProfileVisit.objects.exists())

    def test_not_when_blocked_either_way(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.viewer)
        self.assertFalse(pi.record_visit(self.owner, self.viewer))
        BlockUser.objects.all().delete()
        BlockUser.objects.create(blocker=self.viewer, blocked=self.owner)
        self.assertFalse(pi.record_visit(self.owner, self.viewer))
        self.assertFalse(ProfileVisit.objects.exists())

    def test_not_for_minor_visitors(self):
        with mock.patch("user_profile.privacy.is_minor_user", return_value=True):
            self.assertFalse(pi.record_visit(self.owner, self.viewer))
        self.assertFalse(ProfileVisit.objects.exists())

    def test_anonymous_and_none_are_ignored(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(pi.record_visit(self.owner, AnonymousUser()))
        self.assertFalse(pi.record_visit(self.owner, None))
        self.assertFalse(pi.record_visit(None, self.viewer))

    def test_inactive_owner_is_not_counted(self):
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertFalse(pi.record_visit(self.owner, self.viewer))

    def test_never_raises(self):
        with mock.patch("user_profile.models.ProfileVisit.objects") as m:
            m.filter.side_effect = RuntimeError("db down")
            self.assertFalse(pi.record_visit(self.owner, self.viewer))

    def test_blocks_checked_flag_skips_the_block_queries(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.viewer)
        # the caller says it already checked -> trusted (the profile view raises 404 before it gets here)
        self.assertTrue(pi.record_visit(self.owner, self.viewer, blocks_checked=True))


class ProfileViewRecordsVisitTests(APITestCase):
    def setUp(self):
        self.owner = _user("owner")
        self.viewer = _user("viewer")

    def _open(self, as_user, username="owner"):
        self.client.force_authenticate(as_user)
        return self.client.get(reverse("user-profile-detail", kwargs={"username": username}))

    def test_opening_a_profile_counts_a_visit(self):
        resp = self._open(self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ProfileVisit.objects.get(profile=self.owner, viewer=self.viewer).visits, 1)
        self._open(self.viewer)
        self.assertEqual(ProfileVisit.objects.get(profile=self.owner, viewer=self.viewer).visits, 2)

    def test_own_profile_is_not_counted(self):
        self._open(self.owner)
        self.assertFalse(ProfileVisit.objects.exists())

    def test_blocked_viewer_gets_404_and_no_row(self):
        BlockUser.objects.create(blocker=self.owner, blocked=self.viewer)
        self.assertEqual(self._open(self.viewer).status_code, 404)
        self.assertFalse(ProfileVisit.objects.exists())

    def test_a_recording_failure_does_not_break_the_profile(self):
        with mock.patch("user_profile.models.ProfileVisit.objects") as m:
            m.filter.side_effect = RuntimeError("boom")
            self.assertEqual(self._open(self.viewer).status_code, 200)


class InsightsEndpointTests(APITestCase):
    url_name = "profile-insights"

    def setUp(self):
        self.owner = _user("owner")
        self.a, self.b, self.c = _user("a"), _user("b"), _user("c")
        self.url = reverse(self.url_name)

    def _get(self, **params):
        self.client.force_authenticate(self.owner)
        return self.client.get(self.url, params)

    def test_requires_login(self):
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))

    def test_shape_for_an_empty_profile(self):
        d = self._get().json()
        self.assertEqual(d["days"], 7)
        self.assertEqual(d["visits"]["total"], 0)
        self.assertEqual(d["visits"]["unique"], 0)
        self.assertIsNone(d["visits"]["change_pct"])
        self.assertEqual(len(d["visits"]["daily"]), 7)
        self.assertEqual(len(d["followers"]["daily"]), 7)
        self.assertEqual(d["followers"]["now"], 0)
        self.assertTrue(d["privacy"]["visitors_hidden"])
        self.assertIn("top_posts", d["content"])

    def test_days_window_and_bad_values(self):
        self.assertEqual(self._get(days=30).json()["days"], 30)
        self.assertEqual(len(self._get(days=30).json()["visits"]["daily"]), 30)
        self.assertEqual(self._get(days="zzz").json()["days"], 7)
        self.assertEqual(self._get(days=9999).json()["days"], 90)

    def test_unique_vs_total_visits_and_previous_window(self):
        _visit(self.owner, self.a, 0, visits=3)
        _visit(self.owner, self.b, 1, visits=1)
        _visit(self.owner, self.a, 2, visits=2)  # a again on another day: total counts it, unique does not
        _visit(self.owner, self.c, 9, visits=4)  # previous 7-day window (days 7..13 ago)
        d = self._get(days=7).json()["visits"]
        self.assertEqual(d["total"], 6)
        self.assertEqual(d["unique"], 2)
        self.assertEqual(d["prev_total"], 4)
        self.assertEqual(d["prev_unique"], 1)
        self.assertEqual(d["change_pct"], 50.0)
        today = d["daily"][-1]
        self.assertEqual(today["visits"], 3)
        self.assertEqual(today["unique"], 1)

    def test_older_than_both_windows_is_ignored(self):
        _visit(self.owner, self.a, 20)
        d = self._get(days=7).json()["visits"]
        self.assertEqual((d["total"], d["prev_total"]), (0, 0))

    def test_follower_growth_counts_only_accepted(self):
        _follow(self.a, self.owner, days_ago=0)
        _follow(self.b, self.owner, days_ago=3)
        _follow(self.c, self.owner, status=Follow.Status.PENDING, days_ago=1)  # not a follower yet
        old = _user("old")
        _follow(old, self.owner, days_ago=10)  # previous window
        f = self._get(days=7).json()["followers"]
        self.assertEqual(f["now"], 3)
        self.assertEqual(f["gained"], 2)
        self.assertEqual(f["prev_gained"], 1)
        self.assertEqual(f["change_pct"], 100.0)
        self.assertEqual(sum(r["gained"] for r in f["daily"]), 2)

    def test_no_visitor_identity_anywhere_in_the_answer(self):
        _visit(self.owner, self.a, 0)
        body = self._get().content.decode()
        self.assertNotIn("viewer", body)
        self.assertNotIn('"a"', body)  # the visitor's username
        self.assertNotIn(self.a.email, body)

    def test_other_peoples_numbers_never_leak(self):
        _visit(self.a, self.b, 0, visits=9)  # b visited a, not owner
        _follow(self.b, self.a)
        d = self._get().json()
        self.assertEqual(d["visits"]["total"], 0)
        self.assertEqual(d["followers"]["now"], 0)

    def test_no_username_parameter_is_accepted(self):
        # the URL has no way to name somebody else
        self.assertNotIn("username", self.url)


class PruneTests(APITestCase):
    def test_old_rows_go_recent_stay(self):
        owner, v = _user("owner"), _user("v")
        _visit(owner, v, 0)
        _visit(owner, _user("w"), pi.retention_days() + 5)
        deleted = pi.prune_old_visits()
        self.assertEqual(deleted, 1)
        self.assertEqual(ProfileVisit.objects.count(), 1)
