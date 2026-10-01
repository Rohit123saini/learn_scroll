# post/tests_cursor_pagination.py
# TASK "Cursor pagination" (Part 1): engagement_score-ranked feeds
# (GET /post/explore/, GET /post/hashtag/<tag>/) use keyset pagination so
# posts no longer jump up/down between pages while the user scrolls.
import datetime

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from unittest import skipUnless
from rest_framework.test import APITestCase

from common.pagination import EngagementCursorPagination
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
