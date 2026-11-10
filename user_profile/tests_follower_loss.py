"""
user_profile/tests_follower_loss.py  (P9b - lost followers in Profile insights)

Likhe gaye hain, CHALAYE NAHI gaye.  Run:
    python manage.py test user_profile.tests_follower_loss

Covers
  * record_loss: one row per (profile, day) that counts up, never for None / count < 1, never raises
  * unfollow (POST /profile/follow/<id>/ on an existing ACCEPTED follow) and "Remove follower" record one loss;
    a cancelled PENDING request does not
  * GET /profile/insights/: lost / prev_lost / net, daily series carries `lost`, nobody's identity in the answer
  * prune_old_visits also removes old lost-follower counters
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile import profile_insights as pi
from user_profile.models import Follow, FollowerLoss

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


class RecordLossTests(TestCase):
    def setUp(self):
        self.owner = _user("owner")

    def test_first_creates_second_increments(self):
        self.assertTrue(pi.record_loss(self.owner))
        self.assertTrue(pi.record_loss(self.owner))
        row = FollowerLoss.objects.get(profile=self.owner)
        self.assertEqual((row.day, row.lost), (timezone.localdate(), 2))
        self.assertEqual(FollowerLoss.objects.count(), 1)

    def test_bad_input_is_ignored(self):
        self.assertFalse(pi.record_loss(None))
        self.assertFalse(pi.record_loss(self.owner, 0))
        self.assertEqual(FollowerLoss.objects.count(), 0)

    def test_never_raises(self):
        with mock.patch("user_profile.models.FollowerLoss.objects") as m:
            m.filter.side_effect = RuntimeError("db down")
            self.assertFalse(pi.record_loss(self.owner))


class HooksTests(APITestCase):
    def setUp(self):
        self.owner, self.a, self.b = _user("owner"), _user("a"), _user("b")

    def test_unfollow_counts_one_loss_for_the_followed_user(self):
        Follow.objects.create(follower=self.a, following=self.owner, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.a)
        r = self.client.post(reverse("follow-user", args=[self.owner.id]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Follow.objects.filter(follower=self.a, following=self.owner).exists())
        self.assertEqual(FollowerLoss.objects.get(profile=self.owner).lost, 1)

    def test_cancelling_a_pending_request_is_not_a_loss(self):
        Follow.objects.create(follower=self.a, following=self.owner, status=Follow.Status.PENDING)
        self.client.force_authenticate(self.a)
        self.client.post(reverse("follow-user", args=[self.owner.id]))
        self.assertEqual(FollowerLoss.objects.count(), 0)

    def test_remove_follower_counts_one_loss_and_double_tap_does_not(self):
        Follow.objects.create(follower=self.a, following=self.owner, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.owner)
        url = reverse("remove-follower", args=[self.a.id])
        self.assertEqual(self.client.delete(url).status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(FollowerLoss.objects.get(profile=self.owner).lost, 1)

    def test_a_failing_counter_never_breaks_unfollow(self):
        Follow.objects.create(follower=self.a, following=self.owner, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.a)
        with mock.patch("user_profile.models.FollowerLoss.objects") as m:
            m.filter.side_effect = RuntimeError("db down")
            r = self.client.post(reverse("follow-user", args=[self.owner.id]))
        self.assertEqual(r.status_code, 200)


class ReportTests(APITestCase):
    url_name = "profile-insights"

    def setUp(self):
        self.owner, self.a = _user("owner"), _user("a")
        self.url = reverse(self.url_name)

    def _get(self, **params):
        self.client.force_authenticate(self.owner)
        return self.client.get(self.url, params)

    def _loss(self, days_ago, n):
        FollowerLoss.objects.create(profile=self.owner, day=timezone.localdate() - timedelta(days=days_ago), lost=n)

    def test_lost_prev_lost_and_net(self):
        self._loss(0, 2)
        self._loss(3, 1)
        self._loss(10, 4)   # previous window (7d)
        self._loss(20, 9)   # older than both windows
        row = Follow.objects.create(follower=self.a, following=self.owner, status=Follow.Status.ACCEPTED)
        Follow.objects.filter(pk=row.pk).update(created_at=timezone.now())
        f = self._get(days=7).json()["followers"]
        self.assertEqual((f["lost"], f["prev_lost"]), (3, 4))
        self.assertEqual(f["gained"], 1)
        self.assertEqual(f["net"], -2)
        self.assertEqual(sum(r["lost"] for r in f["daily"]), 3)
        self.assertEqual(len(f["daily"]), 7)

    def test_empty_profile_has_zero_loss_and_notes_say_losses_are_counted(self):
        d = self._get().json()
        self.assertEqual((d["followers"]["lost"], d["followers"]["net"]), (0, 0))
        self.assertFalse(d["notes"]["followers_gained_only"])

    def test_other_peoples_losses_never_leak(self):
        FollowerLoss.objects.create(profile=self.a, day=timezone.localdate(), lost=50)
        self.assertEqual(self._get().json()["followers"]["lost"], 0)

    def test_no_identity_in_the_answer(self):
        self._loss(0, 1)
        body = self._get().content.decode()
        self.assertNotIn("a@example.com", body)


class PruneTests(TestCase):
    def test_old_counters_go_recent_stay(self):
        owner = _user("owner")
        FollowerLoss.objects.create(profile=owner, day=timezone.localdate() - timedelta(days=pi.retention_days() + 5), lost=1)
        FollowerLoss.objects.create(profile=owner, day=timezone.localdate(), lost=1)
        self.assertEqual(pi.prune_old_visits(), 1)
        self.assertEqual(FollowerLoss.objects.count(), 1)
