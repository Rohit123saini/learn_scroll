# P-E3_E6 - top-level comments can be paged (offset/limit + X-Has-More); old call without params is unchanged.
# UNVERIFIED: not run.
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Post, PostComment

User = get_user_model()


class CommentListPagingTests(APITestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="pe3_author", password="testpass123")
        self.post = Post.objects.create(user=self.author, content="busy post", category="general")
        for i in range(7):
            PostComment.objects.create(post=self.post, user=self.author, content=f"c{i}")
        self.url = reverse("comment-list", args=[self.post.id])

    def test_no_params_returns_all_as_bare_list(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIsInstance(r.data, list)
        self.assertEqual(len(r.data), 7)
        self.assertEqual(r["X-Has-More"], "0")

    def test_windows_do_not_overlap_and_cover_everything(self):
        first = self.client.get(self.url, {"limit": 3, "sort": "newest"})
        self.assertEqual(len(first.data), 3)
        self.assertEqual(first["X-Has-More"], "1")
        second = self.client.get(self.url, {"limit": 3, "offset": 3, "sort": "newest"})
        third = self.client.get(self.url, {"limit": 3, "offset": 6, "sort": "newest"})
        self.assertEqual(len(third.data), 1)
        self.assertEqual(third["X-Has-More"], "0")
        ids = [c["id"] for c in list(first.data) + list(second.data) + list(third.data)]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7, "a comment appeared in two windows")

    def test_bad_params_fall_back_to_defaults(self):
        r = self.client.get(self.url, {"limit": "abc", "offset": "-5"})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data), 7)

    def test_limit_is_capped_at_50(self):
        r = self.client.get(self.url, {"limit": 9999})
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(len(r.data), 7)
