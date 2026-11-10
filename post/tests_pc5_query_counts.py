# post/tests_pc5_query_counts.py
# P-C5: N+1 regression tests for the post list stamping (repost cards) and the comment list.
#
# NOT RUN when written (read-only task). Same philosophy as tests_feed_query_budget.py: the exact absolute query
# count is unknown without executing, so these tests compare the SAME code path with a SMALL and a LARGE page.
# A real N+1 adds >= 1 query per extra row; the allowed growth (GROWTH_SLACK) only covers prefetches that exist
# only when a certain card type is present. Run:  python manage.py test post.tests_pc5_query_counts -v 2
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIRequestFactory, APITestCase

from user_profile.models import Follow

from .models import Post, PostComment, PostLike
from .serializers import PostListSerializer

User = get_user_model()
GROWTH_SLACK = 3


class RepostCardQueryTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="pc5_me", password="x")
        self.public_author = User.objects.create_user(username="pc5_pub", password="x")
        self.private_author = User.objects.create_user(username="pc5_priv", password="x")
        self.private_author.is_private = True
        self.private_author.save(update_fields=["is_private"])
        Follow.objects.create(follower=self.me, following=self.private_author, status=Follow.Status.ACCEPTED)
        self.factory = APIRequestFactory()

    def _repost_cards(self, n):
        out = []
        for i in range(n):
            author = self.private_author if i % 2 else self.public_author
            original = Post.objects.create(
                user=author, content=f"orig {i}", category="tech", post_type="text",
                visibility="public", moderation_status="approved")
            PostLike.objects.create(user=self.me, post=original, reaction_type="like")
            out.append(Post.objects.create(
                user=self.me, content="", category="tech", post_type="repost", original_post=original,
                visibility="public", moderation_status="approved"))
        return out

    def _serialize_count(self):
        qs = (Post.objects.filter(user=self.me, post_type="repost")
              .select_related("user", "original_post__user").prefetch_related("media", "original_post__media"))
        request = self.factory.get("/")
        request.user = self.me
        with CaptureQueriesContext(connection) as ctx:
            data = PostListSerializer(qs, many=True, context={"request": request}).data
        return len(ctx), data

    def test_repost_cards_do_not_add_queries_per_card(self):
        self._repost_cards(2)
        small, _ = self._serialize_count()
        self._repost_cards(8)  # 10 cards total
        large, data = self._serialize_count()
        self.assertEqual(len(data), 10)
        self.assertLessEqual(
            large, small + GROWTH_SLACK,
            f"repost cards: 2 cards = {small} queries, 10 cards = {large} (a per-card N+1 is back in "
            "ViewerStateListSerializer._stamp_repost_originals / get_original_post)")

    def test_private_author_follow_check_is_batched_and_still_correct(self):
        self._repost_cards(4)
        _, data = self._serialize_count()
        # I follow the private author -> its originals stay visible; nothing may come back as the unavailable stub.
        for card in data:
            self.assertFalse((card.get("original_post") or {}).get("is_unavailable"), card)

    def test_private_author_hidden_for_a_non_follower(self):
        self._repost_cards(2)
        stranger = User.objects.create_user(username="pc5_stranger", password="x")
        qs = Post.objects.filter(post_type="repost").select_related("user", "original_post__user")
        request = self.factory.get("/")
        request.user = stranger
        data = PostListSerializer(qs, many=True, context={"request": request}).data
        flags = {bool((c.get("original_post") or {}).get("is_unavailable")) for c in data}
        self.assertIn(True, flags)  # the private author's original is replaced by the stub for a non-follower


class CommentListQueryTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="pc5_c_me", password="x")
        self.other = User.objects.create_user(username="pc5_c_other", password="x")
        self.post = Post.objects.create(
            user=self.other, content="p", category="tech", post_type="text",
            visibility="public", moderation_status="approved")
        self.client.force_authenticate(self.me)

    def _comments(self, n):
        for i in range(n):
            PostComment.objects.create(post=self.post, user=self.other, content=f"c{i}")

    def _count(self):
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse("comment-list", kwargs={"post_id": self.post.id}))
        self.assertEqual(r.status_code, 200, getattr(r, "data", r.content))
        return len(ctx)

    def test_comment_list_queries_do_not_grow_with_page_size(self):
        self._comments(3)
        small = self._count()
        self._comments(15)
        large = self._count()
        self.assertLessEqual(large, small + GROWTH_SLACK, f"3 comments = {small}, 18 comments = {large}")
