"""
user_profile/test_discovery.py — P8-BE: mutuals + similar (suggested) users.
Run:  python manage.py test user_profile.test_discovery

Routes are resolved with reverse() on the url NAMES ("user-mutuals" / "user-similar"),
so the test doesn't care what prefix user_profile.urls is mounted under.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase

from .discovery import mutual_followers_queryset, suggested_users_queryset
from .models import BlockUser, Follow, RestrictUser

User = get_user_model()
ACC = Follow.Status.ACCEPTED
PEND = Follow.Status.PENDING


def mk(name, **kw):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345", **kw)


def follow(a, b, st=ACC):
    return Follow.objects.create(follower=a, following=b, status=st)


class MutualsTests(APITestCase):
    def setUp(self):
        self.me = mk("viewer")
        self.target = mk("target")
        self.client.force_authenticate(self.me)
        # a, b, c, d: I follow them AND they follow target  -> mutuals
        self.m = [mk(n) for n in ("m_a", "m_b", "m_c", "m_d")]
        for u in self.m:
            follow(self.me, u)
            follow(u, self.target)
        # only follows target (I don't follow them) / I follow them but they don't follow target
        self.only_target = mk("only_target")
        follow(self.only_target, self.target)
        self.only_me = mk("only_me")
        follow(self.me, self.only_me)

    def _get(self, username="target"):
        return self.client.get(reverse("user-mutuals", kwargs={"username": username}))

    def test_total_and_preview_size(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 4)
        self.assertEqual(len(body["preview"]), 3)
        names = {u["username"] for u in body["preview"]}
        self.assertTrue(names <= {u.username for u in self.m})
        self.assertNotIn("only_target", names)
        self.assertNotIn("only_me", names)

    def test_pending_follows_do_not_count(self):
        Follow.objects.filter(follower=self.me, following=self.m[0]).update(status=PEND)
        Follow.objects.filter(follower=self.m[1], following=self.target).update(status=PEND)
        self.assertEqual(self._get().json()["total"], 2)

    def test_blocked_mutual_is_excluded_both_directions(self):
        BlockUser.objects.create(blocker=self.me, blocked=self.m[0])
        BlockUser.objects.create(blocker=self.m[1], blocked=self.me)
        self.assertEqual(self._get().json()["total"], 2)

    def test_own_profile_is_empty(self):
        self.assertEqual(self._get("viewer").json(), {"status": True, "preview": [], "total": 0})

    def test_private_target_not_followed_is_empty_then_visible_after_accept(self):
        User.objects.filter(pk=self.target.pk).update(is_private=True)
        self.assertEqual(self._get().json()["total"], 0)
        follow(self.me, self.target)
        self.assertEqual(self._get().json()["total"], 4)

    def test_blocked_between_viewer_and_target_is_404(self):
        BlockUser.objects.create(blocker=self.target, blocked=self.me)
        self.assertEqual(self._get().status_code, 404)

    def test_unknown_user_404_and_auth_required(self):
        self.assertEqual(self._get("nobody_here").status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self._get().status_code, (401, 403))

    def test_query_helper_matches(self):
        self.assertEqual(mutual_followers_queryset(self.me, self.target).count(), 4)


class SimilarTests(APITestCase):
    def setUp(self):
        self.me = mk("viewer")
        self.target = mk("target")
        self.client.force_authenticate(self.me)
        self.followed = mk("followed")
        follow(self.me, self.followed)
        self.requested = mk("requested")
        follow(self.me, self.requested, PEND)
        self.blocked = mk("blocked")
        BlockUser.objects.create(blocker=self.me, blocked=self.blocked)
        self.blocker = mk("blocker")
        BlockUser.objects.create(blocker=self.blocker, blocked=self.me)
        self.restricted = mk("restricted")
        RestrictUser.objects.create(user=self.me, restricted=self.restricted)
        self.inactive = mk("inactive", is_active=False)
        self.ok1 = mk("ok_one")
        self.ok2 = mk("ok_two")
        User.objects.filter(pk=self.ok1.pk).update(followers_count=50)

    def _get(self, username="target", **q):
        return self.client.get(reverse("user-similar", kwargs={"username": username}), q)

    def test_excludes_everyone_it_should(self):
        r = self._get()
        self.assertEqual(r.status_code, 200)
        names = [u["username"] for u in r.json()["suggested_users"]]
        for gone in ("viewer", "target", "followed", "requested", "blocked", "blocker", "restricted", "inactive"):
            self.assertNotIn(gone, names, gone)
        self.assertIn("ok_one", names)
        self.assertIn("ok_two", names)

    def test_popularity_order_and_limit(self):
        names = [u["username"] for u in self._get().json()["suggested_users"]]
        self.assertLess(names.index("ok_one"), names.index("ok_two"))
        self.assertEqual(len(self._get(limit=1).json()["suggested_users"]), 1)
        # junk / oversized limits fall back / clamp instead of 500-ing
        self.assertEqual(self._get(limit="abc").status_code, 200)
        self.assertEqual(self._get(limit=9999).status_code, 200)

    def test_blocked_between_viewer_and_target_is_404(self):
        BlockUser.objects.create(blocker=self.target, blocked=self.me)
        self.assertEqual(self._get().status_code, 404)

    def test_helper_is_shared_with_onboarding(self):
        ids = set(suggested_users_queryset(self.me).values_list("id", flat=True))
        self.assertNotIn(self.blocked.id, ids)
        self.assertNotIn(self.blocker.id, ids)
        self.assertNotIn(self.restricted.id, ids)
        self.assertIn(self.target.id, ids)  # only /similar/ excludes the viewed profile
