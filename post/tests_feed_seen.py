# post/tests_feed_seen.py — reliable "seen" signal: UniqueConstraint(post, user)
# on PostView + POST /post/feed/seen/ (batch, never bumps views_count).
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Post, PostView

User = get_user_model()


class FeedSeenTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.posts = [Post.objects.create(user=self.author, content=f"p{i}") for i in range(3)]
        self.url = reverse("feed-seen")
        self.client.force_authenticate(self.me)

    def _seen(self, ids):
        return self.client.post(self.url, {"post_ids": [str(i) for i in ids]}, format="json")

    def test_requires_auth(self):
        self.client.force_authenticate(None)
        self.assertEqual(self._seen([self.posts[0].id]).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_marks_seen_without_bumping_views_count(self):
        r = self._seen([p.id for p in self.posts])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 3)
        self.assertEqual(PostView.objects.filter(user=self.me).count(), 3)
        self.assertFalse(PostView.objects.filter(user=self.me, is_counted=True).exists())
        for p in self.posts:
            p.refresh_from_db()
            self.assertEqual(p.views_count, 0)

    def test_idempotent_and_keeps_first_viewed_at(self):
        self._seen([self.posts[0].id])
        first = PostView.objects.get(user=self.me, post=self.posts[0])
        self._seen([self.posts[0].id, self.posts[1].id])
        self.assertEqual(PostView.objects.filter(user=self.me).count(), 2)
        again = PostView.objects.get(pk=first.pk)
        self.assertEqual(again.viewed_at, first.viewed_at)

    def test_duplicate_ids_in_payload_collapsed(self):
        pid = self.posts[0].id
        r = self._seen([pid, pid, pid])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 1)
        self.assertEqual(PostView.objects.filter(user=self.me).count(), 1)

    def test_unknown_and_deleted_posts_ignored(self):
        gone = self.posts[2]
        gone.is_deleted = True
        gone.save(update_fields=["is_deleted"])
        r = self._seen([self.posts[0].id, uuid.uuid4(), gone.id])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["accepted"], 1)
        self.assertEqual(PostView.objects.filter(user=self.me).count(), 1)

    def test_max_50_enforced(self):
        self.assertEqual(self._seen([uuid.uuid4() for _ in range(50)]).status_code, 200)
        self.assertEqual(self._seen([uuid.uuid4() for _ in range(51)]).status_code, 400)

    def test_bad_payloads(self):
        self.assertEqual(self.client.post(self.url, {}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"post_ids": "abc"}, format="json").status_code, 400)
        self.assertEqual(self.client.post(self.url, {"post_ids": ["not-a-uuid"]}, format="json").status_code, 400)
        r = self.client.post(self.url, {"post_ids": []}, format="json")
        self.assertEqual((r.status_code, r.data["accepted"]), (200, 0))

    def test_seen_row_is_per_user(self):
        other = User.objects.create_user(username="other", password="x")
        self._seen([self.posts[0].id])
        self.assertFalse(PostView.objects.filter(user=other).exists())


class PostViewConstraintAndCountingTests(APITestCase):
    def setUp(self):
        self.me = User.objects.create_user(username="me", password="x")
        self.author = User.objects.create_user(username="author", password="x")
        self.post = Post.objects.create(user=self.author, content="hello")
        self.client.force_authenticate(self.me)

    def test_unique_post_user_enforced(self):
        PostView.objects.create(post=self.post, user=self.me)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PostView.objects.create(post=self.post, user=self.me)

    def test_null_users_do_not_collide(self):
        PostView.objects.create(post=self.post, user=None)
        PostView.objects.create(post=self.post, user=None)
        self.assertEqual(PostView.objects.filter(post=self.post, user=None).count(), 2)

    def _open(self):
        return self.client.get(reverse("post-detail", args=[self.post.id]))

    def test_detail_open_counts_once(self):
        self.assertEqual(self._open().status_code, 200)
        self._open()
        self.post.refresh_from_db()
        self.assertEqual(self.post.views_count, 1)

    def test_seen_then_open_counts_exactly_once(self):
        self.client.post(reverse("feed-seen"), {"post_ids": [str(self.post.id)]}, format="json")
        self.post.refresh_from_db()
        self.assertEqual(self.post.views_count, 0)
        self._open()
        self._open()
        self.post.refresh_from_db()
        self.assertEqual(self.post.views_count, 1)
        self.assertEqual(PostView.objects.filter(post=self.post, user=self.me).count(), 1)
        self.assertTrue(PostView.objects.get(post=self.post, user=self.me).is_counted)
