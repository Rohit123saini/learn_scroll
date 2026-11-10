# post/tests_feed_query_budget.py
# H2: query-count regression tests for GET /post/feed/ (HomeFeedView).
#
# NOT RUN when written (read-only task). Why ceilings and not exact `assertNumQueries(N)`: the exact N of the pool
# build (~dozens of signal / context / exploration loaders) cannot be known without executing it, and a wrong
# guess would fail on day one. So each budget is a CEILING. Workflow:
#     python manage.py test post.tests_feed_query_budget -v 2
# every test prints `[feed-budget] <name>: N queries`; once you have real numbers (dev DB and the staging run),
# lower PAGE1_BUDGET / PAGE2_BUDGET to measured + ~10 % so a new query in the hot path fails the build.
#
# What is guarded:
#   1. page 1 (new session, pools built)            <= PAGE1_BUDGET
#   2. page 2 (cursor, frozen snapshot in cache)    <= PAGE2_BUDGET  and cheaper than page 1
#   3. page 2 never rebuilds the pools: no `post_views` (seen ids) statement, no pool/ranking statement
#   4. queries do not grow with page size           (the classic N+1: +1 query per extra post)
#   5. Notes/Resource cards: attachments are prefetched, not queried per post
import datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIRequestFactory, APITestCase

from user_profile.models import Follow

from .models import Post, PostAnswer, PostAttachment
from .serializers import PostListSerializer

User = get_user_model()

# Initial CEILINGS (set by reading the code path, not by measuring) - tighten after the first real run.
PAGE1_BUDGET = 90   # follow ids + seen ids + signal/context/penalty loaders + 3 pools + exploration + caps + page fetch + stamps
PAGE2_BUDGET = 20   # follow ids + 1 page fetch (+ media/original-media prefetch) + viewer-state stamps (<= ~8)
# Allowed growth when page_size goes 5 -> 20. A real N+1 adds ~1 query per extra post (+15); the slack only
# covers prefetches that exist only when a post type is present on the page (poll / tags / reposts / attachments).
PAGE_SIZE_SLACK = 4


class FeedQueryBudgetTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.me = User.objects.create_user(username="me", password="x")
        self.friend = User.objects.create_user(username="friend", password="x")
        self.stranger = User.objects.create_user(username="stranger", password="x")
        Follow.objects.create(follower=self.me, following=self.friend, status=Follow.Status.ACCEPTED)
        self.client.force_authenticate(self.me)
        self.url = reverse("home-feed")

        old = timezone.now() - datetime.timedelta(days=10)  # outside the 3h/12h/48h velocity tiers
        types = ["text", "image", "video"]
        posts = []
        for i in range(30):
            for author in (self.friend, self.stranger):
                posts.append(Post.objects.create(
                    user=author, content=f"{author.username} {i}", category="tech", post_type=types[i % 3],
                    visibility="public", moderation_status="approved", likes_count=i))
        for n, p in enumerate(posts):
            Post.objects.filter(pk=p.pk).update(created_at=old + datetime.timedelta(minutes=n))

    # ---- helpers --------------------------------------------------------
    def _capture(self, name, url, **params):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(url, params) if params else self.client.get(url)
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        print(f"[feed-budget] {name}: {len(ctx)} queries")  # for calibrating the budgets
        return r, ctx

    # ---- 1) page 1 ------------------------------------------------------
    def test_page1_within_budget(self):
        _, ctx = self._capture("page1", self.url, page_size=20)
        self.assertLessEqual(
            len(ctx), PAGE1_BUDGET,
            f"page 1 ran {len(ctx)} queries (budget {PAGE1_BUDGET}). Slowest/newest statements:\n"
            + "\n".join(q["sql"][:200] for q in ctx.captured_queries[-8:]))

    # ---- 2) page 2 ------------------------------------------------------
    def test_page2_within_budget_and_cheaper_than_page1(self):
        r1, ctx1 = self._capture("page1", self.url, page_size=10)
        self.assertTrue(r1.data["next"], "need a page 2 for this test")
        r2, ctx2 = self._capture("page2", r1.data["next"])
        self.assertLessEqual(
            len(ctx2), PAGE2_BUDGET,
            f"page 2 ran {len(ctx2)} queries (budget {PAGE2_BUDGET}):\n"
            + "\n".join(q["sql"][:200] for q in ctx2.captured_queries))
        self.assertLess(len(ctx2), len(ctx1), "page 2 must be much cheaper than page 1 (frozen snapshot, no pool build)")

    # ---- 3) page 2 does not rebuild the pools ---------------------------
    def test_page2_does_not_rebuild_pools(self):
        r1, _ = self._capture("page1", self.url, page_size=10)
        _, ctx2 = self._capture("page2", r1.data["next"])
        sqls = [q["sql"].lower() for q in ctx2.captured_queries]
        self.assertFalse([s for s in sqls if "post_views" in s],
                         "page 2 queried post_views: the seen-ids lookup belongs to the pool build only")
        # pool statements rank with a computed `score` column; a snapshot page is a plain `id IN (...)` fetch
        self.assertFalse([s for s in sqls if " as \"score\"" in s or "as score" in s],
                         "page 2 ran a ranked pool query - the snapshot was not used")

    # ---- 4) no growth with page size (N+1 guard) ------------------------
    def test_queries_do_not_grow_with_page_size(self):
        cache.clear()
        _, small = self._capture("page1 size=5", self.url, page_size=5)
        cache.clear()  # otherwise the 30 s candidate cache would make the 2nd request look cheaper
        _, big = self._capture("page1 size=20", self.url, page_size=20)
        self.assertLessEqual(
            len(big) - len(small), PAGE_SIZE_SLACK,
            f"page_size 5 -> 20 added {len(big) - len(small)} queries ({len(small)} -> {len(big)}): per-post queries (N+1)")

    def test_page2_queries_do_not_grow_with_page_size(self):
        counts = {}
        for size in (5, 20):
            cache.clear()
            r1, _ = self._capture(f"page1 size={size}", self.url, page_size=size)
            _, ctx2 = self._capture(f"page2 size={size}", r1.data["next"])
            counts[size] = len(ctx2)
        self.assertLessEqual(counts[20] - counts[5], PAGE_SIZE_SLACK, f"page 2 grew with page size: {counts}")


class ResourceCardQueryTests(APITestCase):
    """Notes/Resource posts used to cost one `attachments` query EACH on every feed page."""

    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.posts = []
        for i in range(6):
            p = Post.objects.create(
                user=self.author, content=f"notes {i}", kind="resource", post_type="document",
                visibility="public", moderation_status="approved")
            PostAttachment.objects.create(
                post=p, kind="pdf", file=f"resources/n{i}.pdf", original_name=f"n{i}.pdf",
                mime="application/pdf", size=10)
            self.posts.append(p)

    def _serialize_queries(self, n):
        request = APIRequestFactory().get("/post/feed/")
        request.user = self.me
        qs = (Post.objects.filter(pk__in=[p.pk for p in self.posts[:n]])
              .select_related("user", "original_post__user").prefetch_related("media", "original_post__media"))
        with CaptureQueriesContext(connection) as ctx:
            data = PostListSerializer(qs, many=True, context={"request": request, "following_ids": set()}).data
        self.assertEqual(len(data), n)
        self.assertEqual(len(data[0]["attachments"]), 1)  # the files are still in the payload
        return len(ctx)

    def test_attachment_queries_do_not_scale_with_number_of_resource_posts(self):
        one, six = self._serialize_queries(1), self._serialize_queries(6)
        self.assertEqual(one, six, f"1 resource post = {one} queries, 6 = {six}: attachments are queried per post again")


class DoubtCardQueryTests(APITestCase):
    """Doubt posts used to run one `answers.filter(is_best_answer=True).first()` query EACH per feed page."""

    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.helper = User.objects.create_user(username="helper", password="x")
        self.posts = []
        for i in range(6):
            p = Post.objects.create(
                user=self.author, content=f"doubt {i}?", kind="doubt", post_type="doubt",
                visibility="public", moderation_status="approved")
            PostAnswer.objects.create(post=p, user=self.helper, content="because", is_best_answer=True)
            self.posts.append(p)

    def _serialize(self, n):
        request = APIRequestFactory().get("/post/feed/")
        request.user = self.me
        qs = (Post.objects.filter(pk__in=[p.pk for p in self.posts[:n]])
              .select_related("user", "original_post__user").prefetch_related("media", "original_post__media"))
        with CaptureQueriesContext(connection) as ctx:
            data = PostListSerializer(qs, many=True, context={"request": request, "following_ids": set()}).data
        self.assertEqual(len(data), n)
        self.assertEqual(data[0]["best_answer"]["content"], "because")  # still in the payload
        return len(ctx)

    def test_best_answer_queries_do_not_scale_with_number_of_doubt_posts(self):
        one, six = self._serialize(1), self._serialize(6)
        self.assertEqual(one, six, f"1 doubt post = {one} queries, 6 = {six}: best_answer is queried per post again")
