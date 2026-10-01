# post/tests_feed_mix.py — discovery mix (60/30/10) for GET /post/feed/
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_mix
from .models import Post, PostView

User = get_user_model()


class FeedMixPureLogicTests(SimpleTestCase):
    def test_quotas_60_30_10(self):
        q = feed_mix.compute_quotas(20, feed_mix.DEFAULT_RATIOS)
        self.assertEqual(q, {"following": 12, "recommended": 6, "trending": 2})

    def test_quotas_always_sum_to_page_size(self):
        for size in range(1, 51):
            self.assertEqual(sum(feed_mix.compute_quotas(size, feed_mix.DEFAULT_RATIOS).values()), size)

    def test_bad_ratios_fall_back_to_default(self):
        self.assertEqual(feed_mix.normalize_ratios({"following": "x", "recommended": -3}), feed_mix.DEFAULT_RATIOS)

    def test_pages_never_overlap_and_cover_everything(self):
        sizes = {"following": 50, "recommended": 30, "trending": 10}
        seen = {s: [] for s in feed_mix.SOURCES}
        for page in range(1, 10):
            sl = feed_mix.allocate_page(page, 20, sizes, feed_mix.DEFAULT_RATIOS)
            for s, (a, b) in sl.items():
                seen[s].extend(range(a, b))
        for s in feed_mix.SOURCES:
            self.assertEqual(seen[s], list(range(sizes[s])))  # no dup, no gap

    def test_empty_following_refilled_from_discovery(self):
        sizes = {"following": 0, "recommended": 30, "trending": 10}
        sl = feed_mix.allocate_page(1, 20, sizes, feed_mix.DEFAULT_RATIOS)
        self.assertEqual(sum(b - a for a, b in sl.values()), 20)
        self.assertEqual(sl["following"], (0, 0))

    def test_interleave_spreads_sources(self):
        out = feed_mix.interleave(
            {"following": list("abcdefghijkl"), "recommended": list("123456"), "trending": ["x", "y"]},
            feed_mix.DEFAULT_RATIOS,
        )
        srcs = [s for s, _ in out]
        self.assertEqual(len(out), 20)
        self.assertNotEqual(srcs[:12], ["following"] * 12)  # not one big block


class HomeFeedMixApiTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        for i in range(15):
            Post.objects.create(user=self.friend, content=f"f{i}", category="tech", post_type="text",
                                visibility="public", moderation_status="approved")
        for i in range(15):
            Post.objects.create(user=self.stranger, content=f"s{i}", category="tech", post_type="text",
                                visibility="public", moderation_status="approved",
                                likes_count=i, created_at=timezone.now() - timedelta(hours=i))

    def _feed(self, **params):
        resp = self.client.get(reverse("home-feed"), params)
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_feed_contains_following_and_discovery(self):
        results = self._feed(page_size=20)["results"]
        sources = {r["feed_source"] for r in results}
        self.assertIn("following", sources)
        self.assertTrue(sources & {"recommended", "trending"})

    def test_no_duplicates_across_pages(self):
        ids, page = [], 1
        while True:
            data = self._feed(page=page, page_size=10)
            ids += [r["id"] for r in data["results"]]
            if not data["next"]:
                break
            page += 1
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 30)

    def test_user_following_nobody_still_gets_feed(self):
        Follow.objects.all().delete()
        results = self._feed()["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["feed_source"] in ("recommended", "trending") for r in results))

    def test_own_posts_never_in_feed(self):
        Post.objects.create(user=self.me, content="mine", post_type="text", visibility="public",
                            moderation_status="approved")
        for r in self._feed(page_size=50)["results"]:
            self.assertNotEqual(r["content"], "mine")

    def test_page_past_end_is_empty_not_404(self):
        data = self.client.get(reverse("home-feed"), {"page": 99})
        self.assertEqual(data.status_code, 200)
        self.assertEqual(data.data["results"], [])
        self.assertIsNone(data.data["next"])


class HomeFeedSeenTests(APITestCase):
    """Seen-logic through GET /post/feed/ (details: tests_feed_mix_seen.py)."""

    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)

    def _post(self, user, content, hours_old=0):
        return Post.objects.create(
            user=user, content=content, post_type="text", visibility="public",
            moderation_status="approved", created_at=timezone.now() - timedelta(hours=hours_old),
        )

    def _see(self, *posts):
        for p in posts:
            PostView.objects.create(post=p, user=self.me, is_counted=False)

    def _feed(self, **params):
        r = self.client.get(reverse("home-feed"), params)
        self.assertEqual(r.status_code, 200, r.data)
        return r.data

    def _by_source(self, data):
        out = {}
        for r in data["results"]:
            out.setdefault(r["feed_source"], []).append(r["content"])
        return out

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_seen_post_not_in_recommended_or_trending(self):
        seen_post = self._post(self.stranger, "seen-one")
        fresh = self._post(self.stranger, "fresh-one")
        self._see(seen_post)
        by_source = self._by_source(self._feed())
        discovery = by_source.get("recommended", []) + by_source.get("trending", [])
        self.assertIn("fresh-one", discovery)
        self.assertNotIn("seen-one", discovery)

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_seen_following_post_moves_down_but_stays(self):
        newest_seen = self._post(self.friend, "f-seen", hours_old=1)
        unseen = [self._post(self.friend, f"f-unseen{i}", hours_old=2 + i) for i in range(3)]
        self._see(newest_seen)
        contents = self._by_source(self._feed(source="following"))["following"]
        self.assertEqual(contents[-1], "f-seen")  # last, not removed
        self.assertEqual(set(contents[:-1]), {p.content for p in unseen})

    def test_thin_pool_falls_back_to_seen_so_feed_is_not_empty(self):
        posts = [self._post(self.stranger, f"only{i}") for i in range(3)]
        self._see(*posts)
        with override_settings(FEED_SEEN_LIMITS={"fill_min": 10}):
            data = self._feed()
        self.assertEqual({r["content"] for r in data["results"]}, {p.content for p in posts})

    def test_seen_switch_off_brings_seen_posts_back(self):
        p = self._post(self.stranger, "seen-one")
        self._see(p)
        with override_settings(FEED_SEEN_LIMITS={"enabled": False, "fill_min": 0}):
            data = self._feed()
        self.assertEqual([r["content"] for r in data["results"]], ["seen-one"])

    @override_settings(FEED_SEEN_LIMITS={"fill_min": 0})
    def test_no_repeat_across_two_pages_even_if_page1_marked_seen(self):
        for i in range(12):
            self._post(self.friend, f"f{i}", hours_old=1 + i)
        for i in range(12):
            self._post(self.stranger, f"s{i}", hours_old=1 + i)
        page1 = self._feed(page_size=10)
        self.assertIsNotNone(page1["next"])
        page1_ids = [r["id"] for r in page1["results"]]
        # the app reports page 1 as seen while the user scrolls to page 2 ...
        resp = self.client.post(reverse("feed-seen"), {"post_ids": page1_ids}, format="json")
        self.assertEqual(resp.status_code, 200)
        # ... page 2 comes from the `next` link (carries seen_cutoff)
        page2 = self.client.get(page1["next"])
        self.assertEqual(page2.status_code, 200)
        page2_ids = [r["id"] for r in page2.data["results"]]
        self.assertTrue(page2_ids)
        self.assertFalse(set(page1_ids) & set(page2_ids))
