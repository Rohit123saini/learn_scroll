# post/tests_feed_mix_seen.py — seen-logic inside feed_mix.build_pool_ids():
#   recommended/trending  -> seen posts excluded (before caps)
#   following             -> seen posts pushed down, never dropped
#   tiny pool after exclude -> last-resort top-up with seen posts
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_mix
from .models import Post, PostView

User = get_user_model()

NO_FILL = {"fill_min": 0}


def _boost():
    return 0.0, 0.0


class SeenLogicBase(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.following_ids = {self.friend.id}

    def post(self, user, content="p", days_old=0):
        p = Post.objects.create(user=user, content=content)
        if days_old:
            Post.objects.filter(pk=p.pk).update(created_at=timezone.now() - timedelta(days=days_old))
        return p

    def see(self, post, days_ago=0):
        pv = PostView.objects.create(post=post, user=self.me, is_counted=False)
        if days_ago:
            PostView.objects.filter(pk=pv.pk).update(viewed_at=timezone.now() - timedelta(days=days_ago))
        return pv

    def pools(self):
        base = Post.objects.filter(is_deleted=False).exclude(user=self.me)
        return feed_mix.build_pool_ids(self.me, base, self.following_ids, _boost)


class GetSeenPostIdsTests(SeenLogicBase):
    def test_returns_recent_seen_only(self):
        a, b = self.post(self.stranger, "a"), self.post(self.stranger, "b")
        self.see(a)
        self.see(b, days_ago=45)  # outside the default 30-day window
        self.assertEqual(feed_mix.get_seen_post_ids(self.me), {a.id})
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, window_days=60), {a.id, b.id})

    def test_cap_keeps_most_recently_seen(self):
        old, new = self.post(self.stranger, "old"), self.post(self.stranger, "new")
        self.see(old, days_ago=5)
        self.see(new, days_ago=1)
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, cap=1), {new.id})

    def test_is_per_user_and_ignores_anonymous_rows(self):
        p = self.post(self.stranger)
        PostView.objects.create(post=p, user=self.stranger)
        PostView.objects.create(post=p, user=None)
        self.assertEqual(feed_mix.get_seen_post_ids(self.me), set())

    def test_zero_window_or_cap_disables(self):
        self.see(self.post(self.stranger))
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, window_days=0), set())
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, cap=0), set())


@override_settings(FEED_SEEN_LIMITS=NO_FILL)
class DiscoveryExcludesSeenTests(SeenLogicBase):
    def test_seen_removed_from_recommended_and_trending(self):
        seen_post = self.post(self.stranger, "seen")
        fresh = self.post(self.stranger, "fresh")
        self.see(seen_post)
        pools = self.pools()
        discovery = set(pools["recommended"]) | set(pools["trending"])
        self.assertIn(fresh.id, discovery)
        self.assertNotIn(seen_post.id, discovery)

    def test_pools_stay_disjoint(self):
        for i in range(5):
            self.post(self.stranger, f"s{i}")
        pools = self.pools()
        self.assertFalse(set(pools["recommended"]) & set(pools["trending"]))

    def test_seen_outside_window_comes_back(self):
        old_seen = self.post(self.stranger, "old-seen")
        self.see(old_seen, days_ago=45)
        pools = self.pools()
        self.assertIn(old_seen.id, set(pools["recommended"]) | set(pools["trending"]))

    def test_exclusion_happens_before_the_cap(self):
        # cap=2: the 2 newest posts are seen. If exclusion ran AFTER the cap
        # the pool would be empty; before the cap it must return the 2 unseen.
        unseen = [self.post(self.stranger, f"u{i}", days_old=3 + i) for i in range(2)]
        for i in range(2):
            self.see(self.post(self.stranger, f"s{i}"))
        limits = {"trending_pool_cap": 2, "recommended_pool_cap": 2}
        with override_settings(FEED_MIX_LIMITS=limits):
            pools = self.pools()
        self.assertEqual(set(pools["trending"]), {p.id for p in unseen})


@override_settings(FEED_SEEN_LIMITS=NO_FILL)
class FollowingDemotesSeenTests(SeenLogicBase):
    def test_seen_following_posts_are_kept_but_sorted_below_unseen(self):
        seen_recent = self.post(self.friend, "seen-recent")
        unseen_recent = self.post(self.friend, "unseen-recent")
        seen_old = self.post(self.friend, "seen-old", days_old=20)
        unseen_old = self.post(self.friend, "unseen-old", days_old=21)
        self.see(seen_recent)
        self.see(seen_old)
        order = self.pools()["following"]
        # -is_recent, is_seen, -score: recent unseen, recent seen, old unseen, old seen
        self.assertEqual(order, [unseen_recent.id, seen_recent.id, unseen_old.id, seen_old.id])

    def test_old_seen_post_survives_when_nothing_else_exists(self):
        old_seen = self.post(self.friend, "only", days_old=40)
        self.see(old_seen)
        self.assertEqual(self.pools()["following"], [old_seen.id])


class LastResortFillTests(SeenLogicBase):
    def test_tiny_pool_topped_up_with_seen_after_unseen(self):
        fresh = self.post(self.stranger, "fresh", days_old=1)
        seen_posts = [self.post(self.stranger, f"seen{i}", days_old=2 + i) for i in range(3)]
        for p in seen_posts:
            self.see(p)
        with override_settings(FEED_SEEN_LIMITS={"fill_min": 10}):
            pools = self.pools()
        discovery = pools["trending"] + pools["recommended"]
        self.assertEqual(set(discovery), {fresh.id} | {p.id for p in seen_posts})
        self.assertFalse(set(pools["recommended"]) & set(pools["trending"]))
        # unseen always in front of the seen top-up
        self.assertEqual(pools["trending"][0], fresh.id)

    def test_everything_seen_still_gives_a_non_empty_feed(self):
        posts = [self.post(self.stranger, f"p{i}") for i in range(3)]
        for p in posts:
            self.see(p)
        with override_settings(FEED_SEEN_LIMITS={"fill_min": 10}):
            pools = self.pools()
        self.assertEqual(set(pools["trending"]) | set(pools["recommended"]), {p.id for p in posts})

    def test_no_fill_when_pool_big_enough(self):
        for i in range(3):
            self.post(self.stranger, f"fresh{i}", days_old=1)
        seen_post = self.post(self.stranger, "seen")
        self.see(seen_post)
        with override_settings(FEED_SEEN_LIMITS={"fill_min": 3}):
            pools = self.pools()
        self.assertNotIn(seen_post.id, set(pools["trending"]) | set(pools["recommended"]))


class HomeFeedEndToEndSeenTests(SeenLogicBase):
    def test_feed_hides_seen_discovery_but_never_goes_empty(self):
        self.client.force_authenticate(self.me)
        p = self.post(self.stranger, "only-post")
        self.see(p)
        with override_settings(FEED_SEEN_LIMITS={"fill_min": 5}):
            r = self.client.get(reverse("home-feed"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["id"] for x in r.data["results"]], [str(p.id)])


class GetSeenPostIdsCutoffTests(SeenLogicBase):
    def test_until_ignores_rows_seen_after_the_cutoff(self):
        before, after = self.post(self.stranger, "before"), self.post(self.stranger, "after")
        self.see(before, days_ago=1)
        self.see(after)  # "now"
        cutoff = timezone.now() - timedelta(hours=1)
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, until=cutoff), {before.id})
        self.assertEqual(feed_mix.get_seen_post_ids(self.me), {before.id, after.id})

    def test_window_is_measured_from_the_cutoff(self):
        p = self.post(self.stranger)
        self.see(p, days_ago=31)
        # 31 days ago is outside "now - 30d", but inside "cutoff(=2 days ago) - 30d"
        self.assertEqual(feed_mix.get_seen_post_ids(self.me), set())
        cutoff = timezone.now() - timedelta(days=2)
        self.assertEqual(feed_mix.get_seen_post_ids(self.me, until=cutoff), {p.id})


class SeenCutoffHelpersTests(SeenLogicBase):
    def test_format_roundtrip(self):
        now = timezone.now()
        raw = feed_mix.format_seen_cutoff(now)
        self.assertTrue(raw.endswith("Z"))
        self.assertNotIn("+", raw)
        self.assertEqual(feed_mix.resolve_seen_cutoff(raw, now=now + timedelta(seconds=5)), now)

    def test_bad_missing_future_or_stale_gives_fresh_cutoff(self):
        now = timezone.now()
        for raw in (None, "", "garbage", "2026-99-99T00:00:00Z"):
            self.assertEqual(feed_mix.resolve_seen_cutoff(raw, now=now), now)
        future = feed_mix.format_seen_cutoff(now + timedelta(hours=1))
        self.assertEqual(feed_mix.resolve_seen_cutoff(future, now=now), now)
        stale = feed_mix.format_seen_cutoff(now - timedelta(days=2))
        self.assertEqual(feed_mix.resolve_seen_cutoff(stale, now=now), now)


@override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
class HomeFeedPagingStabilityTests(SeenLogicBase):
    """Marking posts as seen while the user scrolls must not shift pages 2+."""

    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.me)

    def _get(self, url, **params):
        r = self.client.get(url, params) if params else self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return r

    @staticmethod
    def _ids(r):
        return [x["id"] for x in r.data["results"]]

    @staticmethod
    def _query(url):
        from urllib.parse import parse_qs, urlparse

        return parse_qs(urlparse(url).query)

    def _mark_seen(self, ids):
        r = self.client.post(reverse("feed-seen"), {"post_ids": ids}, format="json")
        self.assertEqual(r.status_code, 200)

    def _assert_stable(self, first_response):
        """page 2 via `next` is identical before/after page 1 gets marked seen."""
        next_url = first_response.data["next"]
        self.assertIsNotNone(next_url)
        baseline = self._ids(self._get(next_url))
        self._mark_seen(self._ids(first_response))
        self.assertEqual(self._ids(self._get(next_url)), baseline)
        return baseline, next_url

    def test_response_shape_unchanged(self):
        self.post(self.stranger, "x")
        r = self._get(reverse("home-feed"))
        self.assertEqual(set(r.data.keys()), {"count", "next", "previous", "results"})

    def test_next_and_previous_carry_seen_cutoff(self):
        for i in range(12):
            self.post(self.stranger, f"p{i}", days_old=1)
        p1 = self._get(reverse("home-feed"), page_size=5)
        self.assertIsNone(p1.data["previous"])
        self.assertIn("seen_cutoff", self._query(p1.data["next"]))
        p2 = self._get(p1.data["next"])
        # cursor pagination (Part 2): no "previous" page; `next` of page 2 keeps
        # the same session cutoff + page_size.
        self.assertIsNone(p2.data["previous"])
        q = self._query(p2.data["next"]) if p2.data["next"] else self._query(p1.data["next"])
        self.assertEqual(q["seen_cutoff"], self._query(p1.data["next"])["seen_cutoff"])
        self.assertEqual(q["page_size"], ["5"])

    def test_mixed_feed_page2_unaffected_by_seen_marked_after_page1(self):
        for i in range(30):
            self.post(self.stranger, f"p{i}", days_old=1 + i / 100)
        p1 = self._get(reverse("home-feed"), page_size=5)
        baseline, next_url = self._assert_stable(p1)
        # contrast: WITHOUT the cutoff (a brand-new session) page 2 has moved on
        fresh_p2 = self._ids(self._get(reverse("home-feed"), page_size=5, page=2))
        self.assertNotEqual(fresh_p2, baseline)
        # and no overlap between page 1 and page 2 of the session
        self.assertFalse(set(baseline) & set(self._ids(p1)))

    def test_following_tab_page2_unaffected_and_keeps_source(self):
        for i in range(12):
            self.post(self.friend, f"f{i}", days_old=1 + i / 100)
        p1 = self._get(reverse("home-feed"), page_size=5, source="following")
        q = self._query(p1.data["next"])
        self.assertEqual(q["source"], ["following"])
        self.assertIn("seen_cutoff", q)
        baseline, _ = self._assert_stable(p1)
        fresh_p2 = self._ids(self._get(reverse("home-feed"), page_size=5, page=2, source="following"))
        self.assertNotEqual(fresh_p2, baseline)  # seen ones would have been demoted

    def test_seen_after_cutoff_is_ignored_seen_before_cutoff_is_applied(self):
        hidden_by_old_seen = self.post(self.stranger, "old-seen")
        shown_despite_new_seen = self.post(self.stranger, "new-seen")
        self.see(hidden_by_old_seen, days_ago=1)  # before the cutoff -> excluded
        self.see(shown_despite_new_seen)          # after the cutoff  -> ignored
        cutoff = feed_mix.format_seen_cutoff(timezone.now() - timedelta(hours=1))
        ids = self._ids(self._get(reverse("home-feed"), seen_cutoff=cutoff))
        self.assertEqual(ids, [str(shown_despite_new_seen.id)])

    def test_invalid_seen_cutoff_does_not_break_the_feed(self):
        p = self.post(self.stranger, "x")
        r = self._get(reverse("home-feed"), seen_cutoff="not-a-date")
        self.assertEqual(self._ids(r), [str(p.id)])


class SeenSwitchAndLimitsTests(SeenLogicBase):
    def test_master_switch_off_ignores_seen_completely(self):
        p = self.post(self.stranger)
        self.see(p)
        with override_settings(FEED_SEEN_LIMITS={"enabled": False}):
            self.assertEqual(feed_mix.get_seen_post_ids(self.me), set())
            pools = self.pools()
        self.assertIn(p.id, set(pools["trending"]) | set(pools["recommended"]))

    def test_settings_override_window_and_cap_and_defaults_merge(self):
        a, b = self.post(self.stranger, "a"), self.post(self.stranger, "b")
        self.see(a, days_ago=10)
        self.see(b, days_ago=1)
        with override_settings(FEED_SEEN_LIMITS={"window_days": 5}):
            self.assertEqual(feed_mix.get_seen_post_ids(self.me), {b.id})
            self.assertEqual(feed_mix.get_seen_limits()["cap"], 2000)  # untouched key -> default
        with override_settings(FEED_SEEN_LIMITS={"cap": 1}):
            self.assertEqual(feed_mix.get_seen_post_ids(self.me), {b.id})
