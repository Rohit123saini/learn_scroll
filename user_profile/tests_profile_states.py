"""P10 - profile page states, query budget, counts recount. Written, NOT run."""
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from . import profile_state
from .management.commands.recount_follow_counts import drifted_users
from .models import BlockUser, Follow

User = get_user_model()


def _url(username):
    return f"/profile/profile/{username}/"


class ProfileStateClassifyTests(TestCase):
    def test_precedence(self):
        c = profile_state.classify
        self.assertEqual(c(is_blocked_by_me=False, is_suspended=False, is_restricted_view=False), "ok")
        self.assertEqual(c(is_blocked_by_me=False, is_suspended=False, is_restricted_view=True), "private")
        self.assertEqual(c(is_blocked_by_me=False, is_suspended=True, is_restricted_view=True), "suspended")
        self.assertEqual(c(is_blocked_by_me=True, is_suspended=True, is_restricted_view=True), "blocked_by_me")

    def test_unavailable_card_leaks_nothing_but_id_and_username(self):
        u = User.objects.create_user(username="gone1", password="x", first_name="Secret", bio="private bio")
        card = profile_state.unavailable_card(u)
        self.assertEqual(set(card), {"id", "username", "first_name", "last_name", "is_private", "is_verified"})
        self.assertEqual(card["first_name"], "")
        self.assertNotIn("bio", card)


class ProfileDetailStateTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="viewer1", password="x")
        self.c = APIClient()
        self.c.force_authenticate(self.me)

    def _get(self, name):
        return self.c.get(_url(name))

    def test_public_profile_is_ok(self):
        User.objects.create_user(username="pub1", password="x", bio="hello")
        r = self._get("pub1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_state"], "ok")
        self.assertEqual(r.data["data"]["bio"], "hello")

    def test_own_profile_is_ok(self):
        self.assertEqual(self._get("viewer1").data["profile_state"], "ok")

    def test_private_profile_is_private_until_accepted(self):
        owner = User.objects.create_user(username="priv1", password="x", is_private=True, bio="secret")
        r = self._get("priv1")
        self.assertEqual(r.data["profile_state"], "private")
        self.assertNotIn("bio", r.data["data"])
        Follow.objects.create(follower=self.me, following=owner, status=Follow.Status.ACCEPTED)
        self.assertEqual(self._get("priv1").data["profile_state"], "ok")

    def test_blocked_by_me_state(self):
        other = User.objects.create_user(username="bl1", password="x", bio="x")
        BlockUser.objects.create(blocker=self.me, blocked=other)
        r = self._get("bl1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_state"], "blocked_by_me")
        self.assertTrue(r.data["is_blocked_by_me"])

    def test_they_blocked_me_is_404_like_no_such_user(self):
        other = User.objects.create_user(username="bl2", password="x")
        BlockUser.objects.create(blocker=other, blocked=self.me)
        self.assertEqual(self._get("bl2").status_code, 404)
        self.assertEqual(self._get("nobody_here").status_code, 404)

    def test_suspended_account_is_minimal(self):
        User.objects.create_user(username="sus1", password="x", first_name="Name", bio="bio", is_active=False)
        r = self._get("sus1")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["profile_state"], "suspended")
        self.assertTrue(r.data["is_restricted_view"])
        self.assertEqual(r.data["data"]["username"], "sus1")
        self.assertEqual(r.data["data"]["first_name"], "")
        self.assertNotIn("bio", r.data["data"])
        self.assertNotIn("followers_count", r.data["data"])

    def test_follow_status_from_the_merged_query_is_still_right(self):
        other = User.objects.create_user(username="two1", password="x")
        Follow.objects.create(follower=self.me, following=other, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=other, following=self.me, status=Follow.Status.PENDING)
        r = self._get("two1")
        self.assertEqual(r.data["my_follow_status"], Follow.Status.ACCEPTED)
        self.assertEqual(r.data["their_follow_status"], Follow.Status.PENDING)
        self.assertIsNotNone(r.data["my_follow_id"])
        self.assertIsNotNone(r.data["their_follow_id"])


class ProfileQueryBudgetTests(TestCase):
    """The number of queries for ONE profile open must not grow with followers / mentions / hashtags."""

    def _count(self, client, name):
        with CaptureQueriesContext(connection) as ctx:
            r = client.get(_url(name))
        self.assertEqual(r.status_code, 200)
        return len(ctx)

    def test_query_count_is_flat(self):
        me = User.objects.create_user(username="viewer1", password="x")
        c = APIClient()
        c.force_authenticate(me)
        small = User.objects.create_user(username="small1", password="x", bio="hi")
        big = User.objects.create_user(username="big1", password="x", bio="hi")
        mentions = []
        for i in range(12):
            u = User.objects.create_user(username=f"fan{i:02d}", password="x")
            Follow.objects.create(follower=u, following=big, status=Follow.Status.ACCEPTED)
            mentions.append(f"@fan{i:02d}")
        big.bio = " ".join(mentions) + " #physics #jee #neet https://example.com"
        big.save(update_fields=["bio"])
        self._count(c, "small1")  # warm-up (content types / caches)
        a = self._count(c, "small1")
        b = self._count(c, "big1")
        # mentions are resolved with ONE extra lookup at most (+ the case-insensitive fallback) - never one per mention
        self.assertLessEqual(b, a + 3, f"profile queries grew: small={a} big={b}")

    def test_budget_ceiling(self):
        me = User.objects.create_user(username="viewer2", password="x")
        c = APIClient()
        c.force_authenticate(me)
        User.objects.create_user(username="t1", password="x")
        self._count(c, "t1")
        self.assertLessEqual(self._count(c, "t1"), 30)


class RecountCommandTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user(username="ra", password="x")
        self.b = User.objects.create_user(username="rb", password="x")
        Follow.objects.create(follower=self.a, following=self.b, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.b, following=self.a, status=Follow.Status.PENDING)  # pending never counts

    def _break(self):
        User.objects.filter(pk=self.b.pk).update(followers_count=99)
        User.objects.filter(pk=self.a.pk).update(following_count=7)

    def test_no_drift_by_default(self):
        self.assertEqual(drifted_users().count(), 0)

    def test_dry_run_reports_but_does_not_write(self):
        self._break()
        out = StringIO()
        call_command("recount_follow_counts", "--dry-run", stdout=out)
        self.assertIn("would fix 2", out.getvalue())
        self.b.refresh_from_db()
        self.assertEqual(self.b.followers_count, 99)

    def test_real_run_fixes_both_directions(self):
        self._break()
        out = StringIO()
        call_command("recount_follow_counts", stdout=out)
        self.assertIn("fixed 2", out.getvalue())
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.b.followers_count, 1)
        self.assertEqual(self.a.following_count, 1)
        self.assertEqual(self.a.followers_count, 0)  # the pending follow is not counted
        self.assertEqual(drifted_users().count(), 0)

    def test_single_username(self):
        self._break()
        call_command("recount_follow_counts", "--username", "rb", stdout=StringIO())
        self.b.refresh_from_db()
        self.a.refresh_from_db()
        self.assertEqual(self.b.followers_count, 1)
        self.assertEqual(self.a.following_count, 7)  # untouched
