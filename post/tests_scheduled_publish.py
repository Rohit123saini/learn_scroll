"""Scheduled posts: hidden until due, published by post.tasks.publish_scheduled_posts, tags notified on publish.
Run: python manage.py test post.tests_scheduled_publish"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from core.models import Notification
from .models import Post, PostTag
from .tasks import publish_scheduled_posts

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="pass12345")


def _post(user, **kw):
    f = dict(user=user, post_type="text", content="hello", visibility="public", moderation_status="approved")
    f.update(kw)
    return Post.objects.create(**f)


class ScheduledPublishTests(APITestCase):
    def setUp(self):
        self.me = _user("sp_me")
        self.other = _user("sp_other")

    def _due(self, **kw):
        return _post(self.me, is_scheduled=True, published_at=timezone.now() - timedelta(minutes=1), **kw)

    def test_due_post_goes_live_and_future_post_stays_scheduled(self):
        due = self._due()
        future = _post(self.me, is_scheduled=True, published_at=timezone.now() + timedelta(hours=1))
        with mock.patch("post.tasks.notify_followers_new_post.delay") as fan:
            self.assertEqual(publish_scheduled_posts(), 1)
        due.refresh_from_db(); future.refresh_from_db()
        self.assertFalse(due.is_scheduled)
        self.assertTrue(future.is_scheduled)
        fan.assert_called_once_with(due.id)

    def test_publish_is_idempotent(self):
        self._due()
        with mock.patch("post.tasks.notify_followers_new_post.delay"):
            self.assertEqual(publish_scheduled_posts(), 1)
            self.assertEqual(publish_scheduled_posts(), 0)

    def test_tags_notified_only_after_publish(self):
        post = self._due()
        PostTag.objects.create(post=post, tagged_user=self.other)
        self.assertFalse(Notification.objects.filter(recipient=self.other).exists())
        with mock.patch("post.tasks.notify_followers_new_post.delay"):
            publish_scheduled_posts()
        self.assertEqual(Notification.objects.filter(recipient=self.other, notif_type=Notification.NotifType.POST_TAG).count(), 1)

    def test_scheduled_post_hidden_from_profile_list_and_detail(self):
        post = self._due()
        self.client.force_authenticate(self.other)
        r = self.client.get(reverse("post-list"), {"target_user_id": str(self.me.id)})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        rows = body.get("results", []) if isinstance(body, dict) else body  # [] is a valid (empty) page
        self.assertNotIn(str(post.pk), [str(row["id"]) for row in rows])
        self.assertEqual(self.client.get(reverse("post-detail", kwargs={"id": post.pk})).status_code, 404)
