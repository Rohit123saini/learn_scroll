# post/tests_feed_snapshot.py
# TASK "Cursor pagination" - PART 2: GET /post/feed/ ranks ONCE per scrolling
# session, freezes the ordered id pools in the cache (Redis in prod) and pages
# through them with an opaque `cursor`.
import datetime
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from user_profile.models import Follow

from . import feed_mix, feed_snapshot
from .models import Post

User = get_user_model()


class AllocateNextTests(SimpleTestCase):
    def test_stepping_offsets_equals_allocate_page(self):
        for sizes in ({"following": 40, "recommended": 25, "trending": 7},
                      {"following": 0, "recommended": 30, "trending": 30},
                      {"following": 3, "recommended": 0, "trending": 0}):
            for ratios in (None, {"following": 1.0}):
                consumed = {s: 0 for s in feed_mix.SOURCES}
                for page in range(1, 12):
                    expected = feed_mix.allocate_page(page, 9, sizes, ratios)
                    got = feed_mix.allocate_next(consumed, 9, sizes, ratios)
                    self.assertEqual(got, expected)
                    consumed = {s: got[s][1] for s in feed_mix.SOURCES}

    def test_offsets_work_with_changing_page_size(self):
        sizes = {"following": 50, "recommended": 30, "trending": 10}
        consumed, seen = {s: 0 for s in feed_mix.SOURCES}, {s: 0 for s in feed_mix.SOURCES}
        for size in (5, 20, 3, 50, 1, 50):
            got = feed_mix.allocate_next(consumed, size, sizes, None)
            for s in feed_mix.SOURCES:
                self.assertEqual(got[s][0], seen[s])  # contiguous, no overlap, no gap
                seen[s] = got[s][1]
            consumed = dict(seen)
        self.assertEqual(sum(seen.values()), 90)


class CursorCodecTests(SimpleTestCase):
    def test_roundtrip(self):
        sid = feed_snapshot.new_snapshot_id()
        offsets = {"following": 12, "recommended": 6, "trending": 2}
        state = feed_snapshot.decode_cursor(feed_snapshot.encode_cursor(sid, offsets, "2026-09-29T12:00:00.000000Z"))
        self.assertEqual(state["snapshot_id"], sid)
        self.assertEqual(state["offsets"], offsets)
        self.assertEqual(state["seen_cutoff"], "2026-09-29T12:00:00.000000Z")

    def test_garbage_and_tampered_cursors_rejected(self):
        import base64
        import json
        from rest_framework.exceptions import NotFound

        def enc(obj):
            return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode()

        sid = feed_snapshot.new_snapshot_id()
        bad = [
            "!!!", "e30=", enc([]), enc({"v": 2}),
            enc({"v": 1, "s": "nothex", "o": {}, "c": None}),
            enc({"v": 1, "s": sid, "o": {"following": -1}, "c": None}),
            enc({"v": 1, "s": sid, "o": {"following": "5"}, "c": None}),
            enc({"v": 1, "s": sid, "o": {"following": True}, "c": None}),
            enc({"v": 1, "s": sid, "o": [], "c": None}),
            enc({"v": 1, "s": sid, "o": {}, "c": 5}),
        ]
        for token in bad:
            with self.assertRaises(NotFound, msg=token):
                feed_snapshot.decode_cursor(token)


class HomeFeedSnapshotTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")
        self.posts = []
        for i in range(20):
            self.posts.append(Post.objects.create(
                user=self.friend, content=f"f{i}", category="tech", post_type="text",
                visibility="public", moderation_status="approved", likes_count=i))
        for i in range(20):
            self.posts.append(Post.objects.create(
                user=self.stranger, content=f"s{i}", category="tech", post_type="text",
                visibility="public", moderation_status="approved", likes_count=i))
            # push everything out of the 3h/12h/48h velocity tiers and make order deterministic
        old = timezone.now() - datetime.timedelta(days=10)
        for n, p in enumerate(self.posts):
            Post.objects.filter(pk=p.pk).update(created_at=old + datetime.timedelta(minutes=n))

    def _get(self, url, **params):
        r = self.client.get(url, params) if params else self.client.get(url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r

    @staticmethod
    def _ids(r):
        return [x["id"] for x in r.data["results"]]

    @staticmethod
    def _q(url):
        return parse_qs(urlparse(url).query)

    def _walk(self, first, on_page=None):
        """Follow `next` links from response `first`; returns all ids in order."""
        ids, r, n = self._ids(first), first, 0
        while r.data["next"]:
            n += 1
            self.assertLess(n, 30)
            if on_page:
                on_page(n)
            r = self._get(r.data["next"])
            ids += self._ids(r)
        return ids

    # ---- contract -------------------------------------------------------
    def test_first_page_returns_cursor_next_and_stable_count(self):
        r = self._get(self.url, page_size=10)
        self.assertEqual(set(r.data.keys()), {"count", "next", "previous", "results"})
        self.assertIsNone(r.data["previous"])
        self.assertEqual(r.data["count"], 40)
        q = self._q(r.data["next"])
        self.assertIn("cursor", q)
        self.assertIn("seen_cutoff", q)
        self.assertNotIn("page", q)
        self.assertEqual(q["page_size"], ["10"])

    def test_full_walk_has_no_duplicates_and_no_gaps(self):
        ids = self._walk(self._get(self.url, page_size=7))
        self.assertEqual(len(ids), 40)
        self.assertEqual(len(set(ids)), 40)

    def test_snapshot_is_stored_in_cache(self):
        r = self._get(self.url, page_size=10)
        state = feed_snapshot.decode_cursor(self._q(r.data["next"])["cursor"][0])
        snap = feed_snapshot.load(self.me.pk, state["snapshot_id"])
        self.assertIsNotNone(snap)
        self.assertEqual(sum(len(v) for v in snap["pools"].values()), 40)

    # ---- the actual bug ---------------------------------------------------
    def test_score_changes_mid_scroll_do_not_reorder_or_duplicate(self):
        p1 = self._get(self.url, page_size=10)
        baseline_p2 = self._ids(self._get(p1.data["next"]))

        # while the user scrolls: a wave of likes on posts they haven't seen yet,
        # and on one already on screen
        for p in self.posts[:10] + self.posts[20:30]:
            Post.objects.filter(pk=p.pk).update(likes_count=9999)
        Post.objects.filter(pk=self._ids(p1)[0]).update(likes_count=0)

        self.assertEqual(self._ids(self._get(p1.data["next"])), baseline_p2)  # frozen
        ids = self._walk(p1)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 40)

    def test_marking_seen_mid_scroll_does_not_shift_pages(self):
        p1 = self._get(self.url, page_size=10)
        baseline = self._ids(self._get(p1.data["next"]))
        r = self.client.post(reverse("feed-seen"), {"post_ids": self._ids(p1)}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._ids(self._get(p1.data["next"])), baseline)

    def test_new_session_is_reranked(self):
        first = self._ids(self._get(self.url, page_size=10))
        Post.objects.filter(pk=self.posts[0].pk).update(likes_count=100000)
        # T1 Part 5: a plain second session inside the candidate-cache TTL reuses the
        # cached candidates; pull-to-refresh (`refresh=1`) always re-ranks.
        again = self._ids(self._get(self.url, page_size=10, refresh=1))
        self.assertIn(str(self.posts[0].id), again)
        self.assertNotEqual(first, again)

    # ---- robustness -------------------------------------------------------
    def test_page_size_can_change_between_pages(self):
        p1 = self._get(self.url, page_size=5)
        next_url = p1.data["next"].replace("page_size=5", "page_size=13")
        p2 = self._get(next_url)
        self.assertEqual(len(self._ids(p2)), 13)
        self.assertFalse(set(self._ids(p1)) & set(self._ids(p2)))

    def test_expired_snapshot_falls_back_and_continues(self):
        p1 = self._get(self.url, page_size=10)
        cache.clear()  # TTL expiry / flushed Redis
        p2 = self._get(p1.data["next"])
        self.assertTrue(self._ids(p2))
        self.assertFalse(set(self._ids(p1)) & set(self._ids(p2)))
        # the fallback re-saves the snapshot under the same id -> chain continues
        ids = self._ids(p1) + self._ids(p2)
        if p2.data["next"]:
            ids += self._walk(p2)[len(self._ids(p2)):]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 40)

    def test_cursor_of_another_user_never_returns_my_list(self):
        p1 = self._get(self.url, page_size=10)
        sid = feed_snapshot.decode_cursor(self._q(p1.data["next"])["cursor"][0])["snapshot_id"]
        other = User.objects.create_user(username="other", password="x")
        self.client.force_authenticate(other)
        # snapshots are keyed by user id -> a miss for `other`, rebuilt from HIS pools
        self.assertIsNone(feed_snapshot.load(other.pk, sid))
        r = self._get(p1.data["next"])
        self.assertEqual(r.status_code, 200)
        self.assertTrue(feed_snapshot.load(self.me.pk, sid))  # mine is untouched

    def test_invalid_cursor_returns_404(self):
        self.assertEqual(self.client.get(self.url, {"cursor": "nope"}).status_code, 404)

    def test_last_page_has_no_next(self):
        r = self._get(self.url, page_size=50)
        self.assertEqual(len(self._ids(r)), 40)
        self.assertIsNone(r.data["next"])

    def test_deleted_post_between_pages_is_skipped_not_500(self):
        p1 = self._get(self.url, page_size=10)
        gone = Post.objects.exclude(id__in=self._ids(p1)).first()
        Post.objects.filter(pk=gone.pk).update(is_deleted=True)
        ids = self._walk(p1)
        self.assertNotIn(str(gone.id), ids)
        self.assertEqual(len(ids), 39)

    def test_following_tab_keeps_source_through_cursor(self):
        p1 = self._get(self.url, page_size=8, source="following")
        self.assertEqual(self._q(p1.data["next"])["source"], ["following"])
        ids = self._walk(p1)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(r["feed_source"] in ("following", "recommended", "trending")
                            for r in self._get(p1.data["next"]).data["results"]))

    # ---- back-compat ------------------------------------------------------
    def test_legacy_page_param_still_works_for_old_apps(self):
        r = self._get(self.url, page=2, page_size=10)
        self.assertEqual(len(self._ids(r)), 10)
        self.assertIn("page=3", r.data["next"])

    @override_settings(FEED_SNAPSHOT={"enabled": False})
    def test_switch_off_restores_offset_pagination(self):
        r = self._get(self.url, page_size=10)
        self.assertIn("page=2", r.data["next"])
        self.assertNotIn("cursor", r.data["next"])
