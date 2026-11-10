"""ViewerStateListSerializer (post/serializers.py): per-page batching of is_liked / is_saved / my_reaction /
is_reposted_by_me / poll. Must return EXACTLY what the old per-post queries returned, in O(1) queries."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory

from .models import Post, PostLike, PostPoll, PostSave
from .serializers import PostListSerializer

User = get_user_model()
VIEWER_FIELDS = ("is_liked", "is_saved", "my_reaction", "is_reposted_by_me", "poll")


def _post(author, text, **kw):
    return Post.objects.create(user=author, content=text, post_type="text", visibility="public",
                               moderation_status="approved", category="tech", **kw)


class ViewerStateBatchTests(TestCase):
    def setUp(self):
        self.me = User.objects.create_user("viewer", "v@x.com", "x")
        self.author = User.objects.create_user("author", "a@x.com", "x")
        req = APIRequestFactory().get("/")
        req.user = self.me
        # real list views pass the viewer's follow set (HomeFeedView / Explore) -> no per-post follow query
        self.ctx = {"request": req, "following_ids": set()}

    def _page(self, n):
        return [_post(self.author, f"p{i}") for i in range(n)]

    def _serialize_many(self, posts):
        return PostListSerializer(posts, many=True, context=self.ctx).data

    def _serialize_one_by_one(self, posts):
        return [PostListSerializer(p, context=self.ctx).data for p in posts]  # old, un-batched path

    def test_batched_equals_per_post_results(self):
        posts = self._page(8)
        PostLike.objects.create(post=posts[0], user=self.me, reaction_type="like")
        PostLike.objects.create(post=posts[3], user=self.me, reaction_type="love")
        PostLike.objects.create(post=posts[3], user=self.author, reaction_type="like")  # someone else's like
        PostSave.objects.create(post=posts[1], user=self.me)
        PostSave.objects.create(post=posts[3], user=self.author)                         # someone else's save
        Post.objects.create(user=self.me, post_type="repost", original_post=posts[5], visibility="public",
                            moderation_status="approved", content="")
        PostPoll.objects.create(post=posts[6])

        fresh = list(Post.objects.filter(pk__in=[p.pk for p in posts]).order_by("created_at"))
        batched = self._serialize_many(list(fresh))
        single = self._serialize_one_by_one(list(Post.objects.filter(pk__in=[p.pk for p in posts]).order_by("created_at")))
        for b, s in zip(batched, single):
            for f in VIEWER_FIELDS:
                self.assertEqual(b[f], s[f], f"{f} differs for post {b['id']}")
        by_id = {str(d["id"]): d for d in batched}
        self.assertTrue(by_id[str(posts[0].id)]["is_liked"])
        self.assertEqual(by_id[str(posts[3].id)]["my_reaction"], "love")
        self.assertFalse(by_id[str(posts[2].id)]["is_liked"])
        self.assertTrue(by_id[str(posts[1].id)]["is_saved"])
        self.assertFalse(by_id[str(posts[3].id)]["is_saved"])       # only the AUTHOR saved it
        self.assertTrue(by_id[str(posts[5].id)]["is_reposted_by_me"])
        self.assertIsNotNone(by_id[str(posts[6].id)]["poll"])
        self.assertIsNone(by_id[str(posts[0].id)]["poll"])

    def test_query_count_is_flat_in_page_size(self):
        def count(n):
            Post.objects.all().delete()
            posts = self._page(n)
            PostLike.objects.create(post=posts[0], user=self.me, reaction_type="like")
            fresh = list(Post.objects.select_related("user").prefetch_related("media").order_by("created_at"))
            with CaptureQueriesContext(connection) as ctx:
                data = self._serialize_many(fresh)
            self.assertEqual(len(data), n)
            return len(ctx)

        small, big = count(3), count(30)
        self.assertLessEqual(big - small, 2, f"N+1 came back: 3 posts={small} queries, 30 posts={big}")

    def test_anonymous_viewer_gets_false_and_no_crash(self):
        from django.contrib.auth.models import AnonymousUser
        posts = self._page(3)
        req = APIRequestFactory().get("/")
        req.user = AnonymousUser()
        data = PostListSerializer(posts, many=True, context={"request": req}).data
        self.assertTrue(all(d["is_liked"] is False and d["is_saved"] is False and d["my_reaction"] is None
                            for d in data))

    def test_empty_page(self):
        self.assertEqual(self._serialize_many([]), [])
