# post/tests_cursor_pagination.py
# TASK "Cursor pagination" (Part 1): engagement_score-ranked feeds
# (GET /post/explore/, GET /post/hashtag/<tag>/) use keyset pagination so
# posts no longer jump up/down between pages while the user scrolls.
import datetime
from urllib.parse import parse_qs, urlparse

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from unittest import skipUnless
from rest_framework.exceptions import NotFound
from rest_framework.test import APITestCase

from common.pagination import EngagementCursorPagination
from user_profile.models import Follow
from . import feed_snapshot
from .models import Post

User = get_user_model()


class CursorCodecTests(SimpleTestCase):
    def test_roundtrip(self):
        import uuid
        pk = uuid.uuid4()
        now = timezone.now()
        token = EngagementCursorPagination.encode_cursor(12.5, now, pk)
        score, created_at, out_pk = EngagementCursorPagination.decode_cursor(token)
        self.assertEqual(score, 12.5)
        self.assertEqual(created_at, now)
        self.assertEqual(out_pk, pk)

    def test_garbage_cursor_is_rejected(self):
        from rest_framework.exceptions import NotFound
        for bad in ("!!!", "e30=", "W10=", "bm90LWpzb24="):
            with self.assertRaises(NotFound):
                EngagementCursorPagination.decode_cursor(bad)


class ExploreCursorPaginationTests(APITestCase):
    URL_NAME = "post-explore"

    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        base = timezone.now() - datetime.timedelta(days=5)  # outside the 3h/12h/48h boost tiers
        self.posts = []
        for i in range(25):
            p = Post.objects.create(user=self.author, content=f"p{i}", likes_count=i % 6)
            Post.objects.filter(pk=p.pk).update(created_at=base + datetime.timedelta(minutes=i))
            self.posts.append(p)
        self.client.force_authenticate(self.me)
        self.url = reverse(self.URL_NAME)

    def _walk(self, page_size=7, between_pages=None):
        """Follow `next` links to the end; returns the list of ids in order."""
        ids, url, n = [], f"{self.url}?page_size={page_size}", 0
        while url:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, r.content)
            ids.extend(str(x["id"]) for x in r.data["results"])
            url = r.data["next"]
            n += 1
            self.assertLess(n, 20, "pagination did not terminate")
            if between_pages and url:
                between_pages(n)
        return ids

    def test_response_shape_has_no_offset_fields(self):
        r = self.client.get(self.url, {"page_size": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.data.keys()), {"next", "previous", "results"})
        self.assertIsNone(r.data["previous"])
        self.assertEqual(len(r.data["results"]), 5)
        self.assertIn("cursor=", r.data["next"])

    def test_walk_returns_every_post_once_in_rank_order(self):
        ids = self._walk(page_size=7)
        self.assertEqual(len(ids), 25)
        self.assertEqual(len(set(ids)), 25)
        # rank order: likes desc, then newest first
        expected = [str(p.id) for p in sorted(
            self.posts, key=lambda p: (-p.likes_count, -self.posts.index(p)))]
        self.assertEqual(ids, expected)

    def test_scores_changing_between_pages_do_not_duplicate_or_shift(self):
        """The bug: with OFFSET, liking an already-seen post pushed everything
        down one slot, so the last post of page 1 reappeared on page 2."""
        first = self.client.get(self.url, {"page_size": 7})
        seen = [str(x["id"]) for x in first.data["results"]]
        # someone hammers likes on a post the user already scrolled past,
        # and on one further down the list
        Post.objects.filter(pk=seen[-1]).update(likes_count=500)
        Post.objects.filter(pk=self.posts[0].pk).update(likes_count=400)

        ids, url = list(seen), first.data["next"]
        while url:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            ids.extend(str(x["id"]) for x in r.data["results"])
            url = r.data["next"]
        self.assertEqual(len(ids), len(set(ids)), "duplicate post across pages")
        # nothing that was ranked below the cursor got skipped either
        below_cursor = {str(p.id) for p in self.posts} - set(seen) - {str(self.posts[0].id)}
        self.assertTrue(below_cursor.issubset(set(ids)))

    def test_ties_on_score_and_created_at_are_still_stable(self):
        same = timezone.now() - datetime.timedelta(days=6)
        Post.objects.filter(user=self.author).update(likes_count=3, created_at=same)
        ids = self._walk(page_size=4)
        self.assertEqual(len(ids), 25)
        self.assertEqual(len(set(ids)), 25)

    def test_last_page_has_no_next(self):
        r = self.client.get(self.url, {"page_size": 100})
        self.assertEqual(len(r.data["results"]), 25)
        self.assertIsNone(r.data["next"])

    def test_page_size_is_clamped_and_bad_values_fall_back(self):
        r = self.client.get(self.url, {"page_size": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.data["results"]), 20)
        r = self.client.get(self.url, {"page_size": 0})
        self.assertEqual(len(r.data["results"]), 1)

    def test_invalid_cursor_returns_404(self):
        r = self.client.get(self.url, {"cursor": "definitely-not-a-cursor"})
        self.assertEqual(r.status_code, 404)

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)


@skipUnless(connection.vendor == "postgresql",
            "JSONField `contains` (hashtags__contains) is not supported on SQLite")
class HashtagCursorPaginationTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        for i in range(15):
            Post.objects.create(user=self.author, content=f"h{i}", hashtags=["python"], likes_count=i % 4)
        self.client.force_authenticate(self.me)

    def test_walk_hashtag_feed(self):
        url, ids = reverse("hashtag-posts", kwargs={"tag": "python"}) + "?page_size=4", []
        while url:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200)
            ids.extend(str(x["id"]) for x in r.data["results"])
            url = r.data["next"]
        self.assertEqual(len(ids), 15)
        self.assertEqual(len(set(ids)), 15)


# ---------------------------------------------------------------------------
# Part 3: the HOME feed (GET /post/feed/) - duplicate / gap tests for a whole scrolling session.
# Home uses the frozen-snapshot cursor of post/feed_snapshot.py (same model as Reels). The basic
# contract tests live in tests_feed_snapshot.py; these walk the WHOLE feed the way the Flutter app
# does (only ever following `next`) while things change underneath. NOT RUN.
# ---------------------------------------------------------------------------
class HomeFeedCursorWalkTests(APITestCase):
    TOTAL = 40

    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")
        self.posts = []
        for author, tag in ((self.friend, "f"), (self.stranger, "s")):
            for i in range(20):
                self.posts.append(Post.objects.create(
                    user=author, content=f"{tag}{i}", category="tech", post_type="text",
                    visibility="public", moderation_status="approved", likes_count=i))
        # out of the 3h/12h/48h velocity tiers + deterministic order
        old = timezone.now() - datetime.timedelta(days=10)
        for n, p in enumerate(self.posts):
            Post.objects.filter(pk=p.pk).update(created_at=old + datetime.timedelta(minutes=n))
        self.all_ids = {str(p.id) for p in self.posts}

    def _get(self, url, **params):
        r = self.client.get(url, params) if params else self.client.get(url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return r

    @staticmethod
    def _ids(r):
        return [str(x["id"]) for x in r.data["results"]]

    def _walk(self, first, before_each_next=None):
        """Follows ONLY `next` (like the app). Returns ids in order, one list per page."""
        pages, r, n = [self._ids(first)], first, 0
        while r.data["next"]:
            n += 1
            self.assertLess(n, 40, "endless `next` chain")
            if before_each_next:
                before_each_next(n, r)
            r = self._get(r.data["next"])
            pages.append(self._ids(r))
        return pages

    def _assert_complete(self, pages):
        flat = [i for page in pages for i in page]
        self.assertEqual(len(flat), len(set(flat)), "duplicate post across pages")
        self.assertEqual(set(flat), self.all_ids, "gap: a post was skipped")

    def test_plain_walk_every_post_exactly_once(self):
        self._assert_complete(self._walk(self._get(self.url, page_size=6)))

    def test_next_url_is_cursor_based_on_every_page(self):
        r = self._get(self.url, page_size=6)
        while r.data["next"]:
            q = parse_qs(urlparse(r.data["next"]).query)
            self.assertIn("cursor", q)
            self.assertNotIn("page", q)
            r = self._get(r.data["next"])

    def test_cache_flushed_before_every_page_still_no_duplicates_no_gaps(self):
        # snapshot TTL expiry / Redis flush at ANY point of the session: the server rebuilds with the
        # cursor's seen_cutoff and continues at the cursor's offsets
        pages = self._walk(self._get(self.url, page_size=7), before_each_next=lambda n, r: cache.clear())
        self._assert_complete(pages)

    def test_page_size_changing_on_every_page(self):
        first = self._get(self.url, page_size=3)
        sizes = [9, 5, 12, 4, 20]
        pages, r, n = [self._ids(first)], first, 0
        while r.data["next"]:
            size = sizes[n % len(sizes)]
            n += 1
            self.assertLess(n, 40)
            nxt = r.data["next"]
            self.assertIn("page_size=", nxt)
            nxt = nxt.replace(f"page_size={parse_qs(urlparse(nxt).query)['page_size'][0]}", f"page_size={size}")
            r = self._get(nxt)
            pages.append(self._ids(r))
        self._assert_complete(pages)

    def test_posts_created_mid_scroll_neither_shift_nor_duplicate_the_session(self):
        first = self._get(self.url, page_size=8)

        def new_posts(n, r):
            if n == 1:
                for i in range(5):
                    Post.objects.create(user=self.friend, content=f"new{i}", category="tech", post_type="text",
                                        visibility="public", moderation_status="approved", likes_count=500)

        pages = self._walk(first, before_each_next=new_posts)
        self._assert_complete(pages)  # the 5 new posts belong to the NEXT session, not this one

    def test_marking_every_page_seen_while_scrolling_changes_nothing(self):
        first = self._get(self.url, page_size=8)
        seen_calls = []

        def mark_seen(n, r):
            res = self.client.post(reverse("feed-seen"), {"post_ids": self._ids(r)}, format="json")
            seen_calls.append(res.status_code)

        # mark page 1 now, every later page right before moving on
        self.client.post(reverse("feed-seen"), {"post_ids": self._ids(first)}, format="json")
        pages = self._walk(first, before_each_next=mark_seen)
        self.assertTrue(all(c == 200 for c in seen_calls))
        self._assert_complete(pages)

    def test_score_wave_on_every_page_does_not_reorder_the_frozen_session(self):
        first = self._get(self.url, page_size=8)

        def score_wave(n, r):
            for p in self.posts:
                Post.objects.filter(pk=p.pk).update(likes_count=(p.likes_count + n * 37) % 101)

        self._assert_complete(self._walk(first, before_each_next=score_wave))

    def test_following_tab_walk_covers_exactly_the_snapshot(self):
        first = self._get(self.url, page_size=6, source="following")
        pages = self._walk(first)
        flat = [i for page in pages for i in page]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(len(flat), first.data["count"])  # `count` = frozen snapshot size

    def test_a_post_deleted_mid_scroll_is_the_only_missing_one(self):
        first = self._get(self.url, page_size=8)
        gone = Post.objects.exclude(id__in=self._ids(first)).first()

        def delete_one(n, r):
            if n == 1:
                Post.objects.filter(pk=gone.pk).update(is_deleted=True)

        flat = [i for page in self._walk(first, before_each_next=delete_one) for i in page]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(set(flat), self.all_ids - {str(gone.id)})

    def test_garbage_and_foreign_cursors_are_rejected(self):
        self.assertEqual(self.client.get(self.url, {"cursor": "nope"}).status_code, 404)
        # a REELS cursor must not be accepted by Home ...
        reels_cursor = feed_snapshot.encode_list_cursor("reels", "a" * 32, 3, None)
        self.assertEqual(self.client.get(self.url, {"cursor": reels_cursor}).status_code, 404)
        # ... and a HOME cursor must not be accepted by Reels' decoder
        home_cursor = feed_snapshot.encode_cursor("b" * 32, {}, None)
        with self.assertRaises(NotFound):
            feed_snapshot.decode_list_cursor(home_cursor, "reels")

