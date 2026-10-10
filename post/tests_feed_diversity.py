# post/tests_feed_diversity.py — T1 Part 1 (diversity re-rank)
from collections import namedtuple
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_diversity as fd
from .models import Post

P = namedtuple("P", "id author kind cat")


def run(items, **cfg):
    return fd.diversify(items, lambda p: p.author, lambda p: p.kind, lambda p: p.cat, cfg or None)


class DiversityPureTests(SimpleTestCase):
    def test_same_elements_in_and_out(self):
        items = [P(i, i % 2, "video", "tech") for i in range(15)]
        out = run(items)
        self.assertEqual(sorted(p.id for p in out), sorted(p.id for p in items))

    def test_already_diverse_page_is_unchanged(self):
        items = [
            P(1, "a", "text", "tech"), P(2, "b", "image", "news"), P(3, "c", "video", "sports"),
            P(4, "d", "text", "jobs"), P(5, "e", "image", "tech"),
        ]
        self.assertEqual(run(items), items)

    def test_author_clump_is_spread(self):
        items = [P(1, "a", "text", "x1"), P(2, "a", "image", "x2"), P(3, "a", "text", "x3"),
                 P(4, "b", "image", "x4"), P(5, "c", "text", "x5"), P(6, "d", "image", "x6")]
        out = run(items, category_gap=0, max_consecutive=0)
        authors = [p.author for p in out]
        for i in range(len(authors) - 1):
            self.assertNotEqual(authors[i], authors[i + 1])  # no back-to-back
        self.assertEqual(out[0].id, 1)  # best post stays first

    def test_same_type_run_is_broken(self):
        items = [P(i, f"u{i}", "video", f"c{i}") for i in range(4)] + [P(10, "z", "text", "cz"), P(11, "y", "image", "cy")]
        out = run(items, author_gap=0, category_gap=0)
        kinds = [p.kind for p in out]
        for i in range(len(kinds) - 2):
            self.assertFalse(kinds[i] == kinds[i + 1] == kinds[i + 2])

    def test_never_drops_when_all_violate(self):
        items = [P(i, "a", "video", "tech") for i in range(6)]
        self.assertEqual([p.id for p in run(items)], list(range(6)))

    def test_post_moves_up_at_most_lookahead_minus_one(self):
        items = [P(i, "a" if i < 6 else f"u{i}", "text", f"c{i}") for i in range(12)]
        out = run(items, lookahead=3, category_gap=0, max_consecutive=0)
        pos = {p.id: n for n, p in enumerate(out)}
        for n, p in enumerate(items):
            self.assertGreaterEqual(pos[p.id], n - 2)

    def test_disabled_and_tiny_lists_untouched(self):
        items = [P(i, "a", "video", "tech") for i in range(5)]
        self.assertEqual(run(items, enabled=False), items)
        self.assertEqual(run(items[:2]), items[:2])

    def test_garbage_config_falls_back(self):
        cfg = fd.merge_config({"author_gap": "x", "lookahead": None, "enabled": 0})
        self.assertEqual(cfg["author_gap"], fd.DEFAULT_DIVERSITY["author_gap"])
        self.assertEqual(cfg["lookahead"], fd.DEFAULT_DIVERSITY["lookahead"])
        self.assertFalse(cfg["enabled"])

    def test_deterministic(self):
        items = [P(i, i % 3, "video" if i % 2 else "text", f"c{i % 4}") for i in range(20)]
        self.assertEqual(run(items), run(items))


class HistoryTests(SimpleTestCase):
    """T1 Part 2 - cross-page memory."""

    def test_history_pushes_same_author_away_from_page_start(self):
        # previous page ended with author "a"; this page starts with "a" again
        items = [P(1, "a", "text", "c1"), P(2, "b", "image", "c2"), P(3, "c", "video", "c3"), P(4, "d", "text", "c4")]
        no_hist = run(items)
        self.assertEqual(no_hist[0].author, "a")
        out = fd.diversify(items, lambda p: p.author, lambda p: p.kind, lambda p: p.cat, None, history=[["a", "text", "c0"]])
        self.assertNotEqual(out[0].author, "a")
        self.assertEqual(sorted(p.id for p in out), [1, 2, 3, 4])  # nothing lost

    def test_history_breaks_type_run_across_pages(self):
        items = [P(1, "a", "video", "c1"), P(2, "b", "text", "c2"), P(3, "c", "image", "c3")]
        out = fd.diversify(items, lambda p: p.author, lambda p: p.kind, lambda p: p.cat,
                           {"author_gap": 0, "category_gap": 0}, history=[["x", "video", "z1"], ["y", "video", "z2"]])
        self.assertNotEqual(out[0].kind, "video")

    def test_short_page_still_uses_history(self):
        items = [P(1, "a", "text", "c1"), P(2, "b", "text", "c2")]
        out = fd.diversify(items, lambda p: p.author, lambda p: p.kind, lambda p: p.cat, None, history=[["a", "text", "c1"]])
        self.assertEqual(out[0].author, "b")

    def test_clean_history_rejects_garbage(self):
        for bad in ("x", 5, [1], [["a", "b"]], [["a", "b", 3]], [["a", "b", "c"]] * 6, [["a" * 100, "b", "c"]]):
            self.assertEqual(fd.clean_history(bad), [])
        self.assertEqual(fd.clean_history([["a", None, "c"]]), [("a", None, "c")])

    def test_garbage_history_never_breaks_diversify(self):
        items = [P(i, f"u{i}", "text", f"c{i}") for i in range(5)]
        out = fd.diversify(items, lambda p: p.author, lambda p: p.kind, lambda p: p.cat, None, history="oops")
        self.assertEqual(out, items)

    def test_page_tail_size_and_topup(self):
        merged = [("following", type("O", (), {"user_id": 7, "post_type": "text", "category": "tech"})())]
        tail = fd.page_tail(merged, None, history=[["a", "video", "news"], ["b", "image", "jobs"]])
        self.assertEqual(len(tail), fd.tail_size())
        self.assertEqual(tail[-1], ["7", "text", "tech"])  # newest last, str-normalised
        self.assertEqual(tail[-2], ["b", "image", "jobs"])


class AuthorCapTests(SimpleTestCase):
    """T1 Part 2 - pool-level author cap."""

    AUTH = {1: "a", 2: "a", 3: "a", 4: "a", 5: "b", 6: "c", 7: "a", 8: "d"}

    def test_soft_cap_demotes_overflow_never_drops(self):
        out = fd.cap_ids([1, 2, 3, 4, 5, 6, 7, 8], self.AUTH, soft_cap=2)
        self.assertEqual(out, [1, 2, 5, 6, 8, 3, 4, 7])
        self.assertEqual(sorted(out), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_hard_cap_drops_overflow(self):
        out = fd.cap_ids([1, 2, 3, 4, 5, 6, 7, 8], self.AUTH, soft_cap=2, hard_cap=3)
        self.assertEqual(out, [1, 2, 5, 6, 8, 3])

    def test_no_caps_is_identity_and_unknown_author_untouched(self):
        ids = [1, 2, 3, 99]
        self.assertEqual(fd.cap_ids(ids, self.AUTH, soft_cap=0, hard_cap=0), ids)
        self.assertEqual(fd.cap_ids([99, 98, 97], {}, soft_cap=1), [99, 98, 97])

    def test_discovery_pools_share_one_counter(self):
        pools = {"following": [1, 2, 3], "trending": [4, 5], "recommended": [7, 6, 8]}
        out = fd.cap_pools(pools, self.AUTH, {"following_soft_cap": 0, "discovery_soft_cap": 1})
        self.assertEqual(out["following"], [1, 2, 3])  # following has its own, off here
        self.assertEqual(out["trending"], [4, 5])  # "a" used its 1 slot in trending
        self.assertEqual(out["recommended"], [6, 8, 7])  # so a's 2nd post is demoted here

    def test_following_is_never_dropped_even_with_hard_cap(self):
        pools = {"following": [1, 2, 3, 4], "trending": [], "recommended": []}
        out = fd.cap_pools(pools, self.AUTH, {"following_soft_cap": 1, "discovery_hard_cap": 1})
        self.assertEqual(sorted(out["following"]), [1, 2, 3, 4])

    def test_disabled(self):
        pools = {"following": [1, 2, 3, 4], "trending": [], "recommended": []}
        self.assertIs(fd.cap_pools(pools, self.AUTH, {"enabled": False}), pools)

    def test_garbage_config_falls_back(self):
        cfg = fd.merge_author_cap_config({"following_soft_cap": "x", "enabled": 0})
        self.assertEqual(cfg["following_soft_cap"], fd.DEFAULT_AUTHOR_CAP["following_soft_cap"])
        self.assertFalse(cfg["enabled"])


User = get_user_model()


class DiversityFeedEndpointTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="div_me", password="x")
        self.heavy = User.objects.create_user(username="div_heavy", password="x")
        self.others = [User.objects.create_user(username=f"div_o{i}", password="x") for i in range(4)]
        for u in [self.heavy] + self.others:
            Follow.objects.create(follower=self.me, following=u, status=Follow.Status.ACCEPTED)
        now = timezone.now()
        # the heavy author owns the 4 newest posts, the others are older
        self.heavy_posts = [self._post(self.heavy, f"h{i}", now - timedelta(minutes=i)) for i in range(4)]
        for i, u in enumerate(self.others):
            self._post(u, f"o{i}", now - timedelta(minutes=10 + i))
        self.client.force_authenticate(self.me)

    @staticmethod
    def _post(user, content, created):
        # created_at is auto_now_add: a value passed to create() is ignored, so set it afterwards
        post = Post.objects.create(
            user=user, content=content, post_type="text", visibility="public",
            moderation_status="approved",
        )
        Post.objects.filter(pk=post.pk).update(created_at=created)
        post.refresh_from_db()
        return post

    def _feed(self):
        resp = self.client.get(reverse("home-feed"), {"page_size": 20, "source": "following", "refresh": 1})  # refresh=1: skip the T1 Part 5 candidate cache so each settings override is really re-ranked
        self.assertEqual(resp.status_code, 200, resp.data)
        return resp.data

    def test_count_and_posts_unchanged_with_and_without_diversity(self):
        with override_settings(FEED_DIVERSITY={"enabled": False}):
            off = self._feed()
        on = self._feed()
        self.assertEqual(off["count"], on["count"])
        self.assertEqual(
            sorted(r["id"] for r in off["results"]), sorted(r["id"] for r in on["results"])
        )

    def test_heavy_author_is_spread_when_enabled(self):
        heavy_ids = {str(p.id) for p in self.heavy_posts}  # API ids are strings
        with override_settings(FEED_DIVERSITY={"enabled": False}):
            off_ids = [r["id"] for r in self._feed()["results"]]
        on_ids = [r["id"] for r in self._feed()["results"]]

        def back_to_back(ids):
            return sum(1 for a, b in zip(ids, ids[1:]) if a in heavy_ids and b in heavy_ids)

        self.assertGreater(back_to_back(off_ids), 0)
        self.assertLess(back_to_back(on_ids), back_to_back(off_ids))


class CursorTailTests(SimpleTestCase):
    def test_tail_roundtrips_through_cursor(self):
        from . import feed_snapshot

        sid = feed_snapshot.new_snapshot_id()
        offs = {"following": 5, "recommended": 3, "trending": 1}
        tail = [["1", "text", "tech"], ["2", "video", None]]
        state = feed_snapshot.decode_cursor(feed_snapshot.encode_cursor(sid, offs, None, tail=tail))
        self.assertEqual(state["tail"], [("1", "text", "tech"), ("2", "video", None)])

    def test_old_cursor_without_tail_still_decodes(self):
        from . import feed_snapshot

        sid = feed_snapshot.new_snapshot_id()
        state = feed_snapshot.decode_cursor(feed_snapshot.encode_cursor(sid, {}, None))
        self.assertEqual(state["tail"], [])

    def test_tampered_tail_is_ignored_not_404(self):
        import base64
        import json

        from . import feed_snapshot

        sid = feed_snapshot.new_snapshot_id()
        raw = {"v": 1, "s": sid, "o": {}, "c": None, "t": "garbage"}
        token = base64.urlsafe_b64encode(json.dumps(raw).encode()).decode()
        self.assertEqual(feed_snapshot.decode_cursor(token)["tail"], [])


class AuthorCapPoolTests(APITestCase):
    """build_pool_ids end-to-end: a prolific followed author is demoted."""

    def setUp(self):
        self.me = User.objects.create_user(username="cap_me", password="x")
        self.heavy = User.objects.create_user(username="cap_heavy", password="x")
        self.light = User.objects.create_user(username="cap_light", password="x")
        for u in (self.heavy, self.light):
            Follow.objects.create(follower=self.me, following=u, status=Follow.Status.ACCEPTED)
        now = timezone.now()
        self.heavy_posts = [self._post(self.heavy, f"h{i}", now - timedelta(minutes=i)) for i in range(8)]
        self.light_post = self._post(self.light, "l0", now - timedelta(minutes=30))
        self.client.force_authenticate(self.me)

    @staticmethod
    def _post(user, content, created):
        # created_at is auto_now_add: a value passed to create() is ignored, so set it afterwards
        post = Post.objects.create(
            user=user, content=content, post_type="text", visibility="public",
            moderation_status="approved",
        )
        Post.objects.filter(pk=post.pk).update(created_at=created)
        post.refresh_from_db()
        return post

    def _contents(self):
        r = self.client.get(reverse("home-feed"), {"page_size": 20, "source": "following", "refresh": 1})  # refresh=1: skip the T1 Part 5 candidate cache so each settings override is really re-ranked
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["count"], [x["content"] for x in r.data["results"]]

    def test_light_author_climbs_when_cap_on_and_nothing_lost(self):
        with override_settings(FEED_AUTHOR_CAP={"enabled": False}, FEED_DIVERSITY={"enabled": False}):
            count_off, off = self._contents()
        with override_settings(FEED_AUTHOR_CAP={"following_soft_cap": 3}, FEED_DIVERSITY={"enabled": False}):
            count_on, on = self._contents()
        self.assertEqual(count_off, count_on)
        self.assertEqual(sorted(off), sorted(on))
        self.assertLess(on.index("l0"), off.index("l0"))  # the quiet author is no longer buried
