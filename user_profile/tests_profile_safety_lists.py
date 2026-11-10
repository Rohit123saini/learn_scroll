"""
P11b - Profile QA + safety, part 2. Written, NOT run:
    python manage.py test user_profile.tests_profile_safety_lists

P11 tested the privacy matrix on the profile page itself (`tests_profile_safety.PrivacyMatrixTests`). The profile also
loads EIGHT more endpoints, and every one of them has to agree with the page:

    followers / following / mutuals / badges / achievements / layout / featured / similar

  SubEndpointMatrixTests   relationship (self / stranger / pending / accepted / blocked-by-me / blocked-me / suspended)
                           x account (public / private)  x  endpoint, against ONE table of expected answers
  FollowListLeakTests      a private account's follower list: no way round the gate with ?q= / ?filter= / ?sort=
  MinorPrivateMatrixTests  under-18 x (public / private) x relationship on the profile page: masked fields stay masked
"""
import itertools

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from .models import BlockUser, Follow
from .tests_profile_safety import User, minor_dob, mk

_ids = itertools.count(1)

RELATIONS = ("self", "stranger", "pending", "accepted", "blocked_by_me", "blocked_me", "suspended")

# endpoint -> URL name (user_profile/urls.py)
ENDPOINTS = {
    "followers": "user-followers",
    "following": "user-following",
    "mutuals": "user-mutuals",
    "badges": "user-badges",
    "achievements": "user-achievements",
    "layout": "user-profile-layout",
    "featured": "user-profile-featured",
    "similar": "user-similar",
}

# What a switched-off (suspended) account answers on each endpoint. The follower lists answer "hidden" (200, empty page - an
# old app build shows an empty list instead of an error); everything that already filtered `is_active` says "no such user".
SUSPENDED = {
    "followers": "hidden", "following": "hidden", "mutuals": "404", "badges": "404",
    "achievements": "404", "layout": "404", "featured": "404",
    "similar": "open",  # the suggestion list does not depend on the profile it is shown under
}


def expected(endpoint, rel, private):
    """ONE place that says what each viewer may get from each endpoint."""
    if rel in ("blocked_by_me", "blocked_me"):
        return "404"  # a block either way looks like "no such user"
    if rel == "suspended":
        return SUSPENDED[endpoint]
    gated = private and rel in ("stranger", "pending")  # private account, viewer is not an ACCEPTED follower
    if endpoint in ("followers", "following"):
        return "hidden" if gated else "open"
    if endpoint == "mutuals":
        return "empty" if (rel == "self" or gated) else "open"
    if endpoint in ("achievements", "layout", "featured"):
        return "locked" if gated else "open"
    return "open"  # badges (empty list for a gated viewer is covered below), similar


def scene(rel, private):
    """(viewer, target) in the given relationship. A third user `mid` is followed by the viewer AND follows the target, so
    \"mutuals\" has a real row to show (or to hide)."""
    i = next(_ids)
    target = mk(f"pq_t{i}", is_private=private)
    viewer = target if rel == "self" else mk(f"pq_v{i}")
    if rel != "self":
        mid = mk(f"pq_m{i}")
        Follow.objects.create(follower=viewer, following=mid, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=mid, following=target, status=Follow.Status.ACCEPTED)
    if rel == "pending":
        Follow.objects.create(follower=viewer, following=target, status=Follow.Status.PENDING)
    elif rel == "accepted":
        Follow.objects.create(follower=viewer, following=target, status=Follow.Status.ACCEPTED)
    elif rel == "blocked_by_me":
        BlockUser.objects.create(blocker=viewer, blocked=target)
    elif rel == "blocked_me":
        BlockUser.objects.create(blocker=target, blocked=viewer)
    elif rel == "suspended":
        User.objects.filter(pk=target.pk).update(is_active=False)
    return viewer, target


def client_for(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def classify(endpoint, r):
    """404 | hidden | empty | locked | open"""
    if r.status_code == 404:
        return "404"
    assert r.status_code == 200, f"{endpoint}: unexpected status {r.status_code}: {getattr(r, 'data', None)}"
    d = r.data
    if endpoint in ("followers", "following"):
        return "hidden" if d.get("hidden") else "open"
    if endpoint == "mutuals":
        return "open" if d.get("total") else "empty"
    if endpoint in ("achievements", "layout", "featured"):
        return "locked" if d.get("locked") else "open"
    return "open"


class SubEndpointMatrixTests(TestCase):
    def setUp(self):
        cache.clear()  # the profile endpoints are throttled per user; keep one test from eating another's budget

    def test_every_endpoint_agrees_with_the_profile_page(self):
        for private in (False, True):
            for rel in RELATIONS:
                viewer, target = scene(rel, private)
                c = client_for(viewer)
                for endpoint, url_name in ENDPOINTS.items():
                    with self.subTest(endpoint=endpoint, relation=rel, private=private):
                        r = c.get(reverse(url_name, args=[target.username]))
                        self.assertEqual(classify(endpoint, r), expected(endpoint, rel, private))

    def test_a_404_for_a_block_is_identical_to_a_404_for_a_user_that_does_not_exist(self):
        viewer, target = scene("blocked_me", True)
        c = client_for(viewer)
        for endpoint, url_name in ENDPOINTS.items():
            with self.subTest(endpoint=endpoint):
                blocked = c.get(reverse(url_name, args=[target.username]))
                missing = c.get(reverse(url_name, args=["pq_nobody_here"]))
                self.assertEqual(blocked.status_code, 404)
                self.assertEqual(missing.status_code, 404)

    def test_private_badges_are_an_empty_list_not_an_error_for_a_gated_viewer(self):
        viewer, target = scene("stranger", True)
        r = client_for(viewer).get(reverse("user-badges", args=[target.username]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["count"], 0)
        self.assertEqual(r.data["badges"], [])

    def test_the_owner_keeps_every_endpoint_even_when_private(self):
        viewer, target = scene("self", True)
        c = client_for(viewer)
        for endpoint, url_name in ENDPOINTS.items():
            with self.subTest(endpoint=endpoint):
                self.assertIn(classify(endpoint, c.get(reverse(url_name, args=[target.username]))), ("open", "empty"))


class FollowListLeakTests(TestCase):
    """Who follows a PRIVATE account is not for strangers - and no query parameter changes that."""

    def setUp(self):
        cache.clear()
        self.owner = mk("pl_owner", is_private=True)
        self.fan = mk("pl_fan")
        self.stranger = mk("pl_stranger")
        self.pending = mk("pl_pending")
        self.friend = mk("pl_friend")
        Follow.objects.create(follower=self.fan, following=self.owner, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.friend, following=self.owner, status=Follow.Status.ACCEPTED)
        Follow.objects.create(follower=self.pending, following=self.owner, status=Follow.Status.PENDING)
        self.owner_follows = mk("pl_owner_follows")
        Follow.objects.create(follower=self.owner, following=self.owner_follows, status=Follow.Status.ACCEPTED)
        self.url_followers = reverse("user-followers", args=[self.owner.username])
        self.url_following = reverse("user-following", args=[self.owner.username])

    def _names(self, user, url, **params):
        r = client_for(user).get(url, params)
        self.assertEqual(r.status_code, 200)
        rows = r.data["data"]["results"] if isinstance(r.data["data"], dict) else r.data["data"]
        return bool(r.data.get("hidden")), {row["username"] for row in rows}

    def test_stranger_and_pending_requester_get_hidden_and_an_empty_page(self):
        for who in (self.stranger, self.pending):
            for url in (self.url_followers, self.url_following):
                with self.subTest(viewer=who.username, url=url):
                    hidden, names = self._names(who, url)
                    self.assertTrue(hidden)
                    self.assertEqual(names, set())

    def test_search_filter_and_sort_do_not_get_round_the_gate(self):
        for params in ({"q": "pl_fan"}, {"filter": "mutual"}, {"filter": "not_following_back"}, {"sort": "name"}, {"q": ""}):
            with self.subTest(params=params):
                hidden, names = self._names(self.stranger, self.url_followers, **params)
                self.assertTrue(hidden)
                self.assertEqual(names, set())

    def test_an_accepted_follower_sees_the_list(self):
        hidden, names = self._names(self.fan, self.url_followers)
        self.assertFalse(hidden)
        self.assertEqual(names, {"pl_fan", "pl_friend"})  # the pending request is not a follower yet
        hidden, names = self._names(self.fan, self.url_following)
        self.assertFalse(hidden)
        self.assertEqual(names, {"pl_owner_follows"})

    def test_the_owner_sees_the_list(self):
        hidden, names = self._names(self.owner, self.url_followers)
        self.assertFalse(hidden)
        self.assertEqual(names, {"pl_fan", "pl_friend"})

    def test_becoming_an_accepted_follower_opens_it_and_unfollowing_closes_it_again(self):
        row = Follow.objects.get(follower=self.pending, following=self.owner)
        row.status = Follow.Status.ACCEPTED
        row.save()
        self.assertFalse(self._names(self.pending, self.url_followers)[0])
        row.delete()
        self.assertTrue(self._names(self.pending, self.url_followers)[0])

    def test_going_public_opens_the_list_to_everyone(self):
        User.objects.filter(pk=self.owner.pk).update(is_private=False)
        self.assertFalse(self._names(self.stranger, self.url_followers)[0])

    def test_the_owners_own_audience_control_still_applies_on_top(self):
        from .models import PrivacySettings

        PrivacySettings.objects.update_or_create(user=self.owner, defaults={"followers_list_from": "nobody"})
        cache.clear()
        self.assertTrue(self._names(self.fan, self.url_followers)[0])  # even an accepted follower: the owner chose "nobody"
        self.assertFalse(self._names(self.owner, self.url_followers)[0])


class MinorPrivateMatrixTests(TestCase):
    """Under-18 account on the profile page: public / private x every relationship. Whatever the page decides to send,
    the masked fields stay masked and owner-only extras never reach anyone else."""

    def setUp(self):
        cache.clear()

    def _viewer_and_minor(self, rel, private):
        i = next(_ids)
        minor = mk(
            f"pq_minor{i}", date_of_birth=minor_dob(), is_private=private, location="Kota", show_birthday=True,
            links=[{"title": "x", "url": "https://example.com"}],
        )
        viewer = minor if rel == "self" else mk(f"pq_mv{i}")
        if rel == "pending":
            Follow.objects.create(follower=viewer, following=minor, status=Follow.Status.PENDING)
        elif rel == "accepted":
            Follow.objects.create(follower=viewer, following=minor, status=Follow.Status.ACCEPTED)
        elif rel == "blocked_by_me":
            BlockUser.objects.create(blocker=viewer, blocked=minor)
        elif rel == "blocked_me":
            BlockUser.objects.create(blocker=minor, blocked=viewer)
        elif rel == "suspended":
            User.objects.filter(pk=minor.pk).update(is_active=False)
        return viewer, minor

    def test_masked_fields_never_leak_in_any_combination(self):
        for private in (False, True):
            for rel in RELATIONS:
                viewer, minor = self._viewer_and_minor(rel, private)
                with self.subTest(relation=rel, private=private):
                    r = client_for(viewer).get(f"/profile/profile/{minor.username}/")
                    if rel == "blocked_me":
                        self.assertEqual(r.status_code, 404)
                        continue
                    self.assertEqual(r.status_code, 200)
                    d = r.data["data"]
                    self.assertIsNone(d.get("birthday"))  # a minor's birthday is shown to NOBODY, not even themselves
                    if rel == "self":
                        self.assertEqual(d.get("location"), "Kota")
                        self.assertEqual(len(d.get("links", [])), 1)
                    else:
                        self.assertIn(d.get("location", ""), ("",))
                        self.assertEqual(d.get("links", []), [])
                        for owner_only in ("completeness", "username_change", "edit_info"):
                            self.assertNotIn(owner_only, d)
