# core/test_notification_batching.py
from django.core.cache import cache
from django.test import TestCase

from login.models import User

from .models import Notification
from .notification_batching import create_batched_notification


class NotificationBatchingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="batch_owner", password="pass12345")
        self.liker1 = User.objects.create_user(username="batch_liker1", password="pass12345")
        self.liker2 = User.objects.create_user(username="batch_liker2", password="pass12345")
        self.liker3 = User.objects.create_user(username="batch_liker3", password="pass12345")

    def _title_fn(self, n, actors):
        return "New like" if n <= 1 else f"{n} people liked your post"

    def test_first_event_creates_a_row_and_sends_push(self):
        pushes = []

        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,
            target_id="post-1", title_fn=self._title_fn,
            send_push_fn=lambda r, t, m, d: pushes.append((r.id, t, m, d)),
        )

        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)
        notif = Notification.objects.get(recipient=self.owner)
        self.assertEqual(notif.title, "New like")
        self.assertEqual(len(pushes), 1)

    def test_second_event_within_window_updates_same_row_no_new_push(self):
        pushes = []
        push_fn = lambda r, t, m, d: pushes.append((r.id, t, m, d))

        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,
            target_id="post-1", title_fn=self._title_fn, send_push_fn=push_fn,
        )
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker2,
            target_id="post-1", title_fn=self._title_fn, send_push_fn=push_fn,
        )

        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)
        notif = Notification.objects.get(recipient=self.owner)
        self.assertEqual(notif.title, "2 people liked your post")
        self.assertEqual(len(pushes), 1)  # still just the one push from event #1

    def test_repeat_actor_does_not_inflate_count(self):
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,
            target_id="post-1", title_fn=self._title_fn,
        )
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,  # same actor again
            target_id="post-1", title_fn=self._title_fn,
        )

        notif = Notification.objects.get(recipient=self.owner)
        self.assertEqual(notif.title, "New like")  # still count=1

    def test_different_targets_batch_separately(self):
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,
            target_id="post-1", title_fn=self._title_fn,
        )
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker2,
            target_id="post-2", title_fn=self._title_fn,
        )

        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 2)

    def test_window_expiry_starts_a_fresh_batch(self):
        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker1,
            target_id="post-1", title_fn=self._title_fn, window_seconds=1,
        )
        first_id = Notification.objects.get(recipient=self.owner).id

        import time

        time.sleep(1.2)  # let the window expire

        create_batched_notification(
            recipient=self.owner, notif_type="post_liked", actor=self.liker2,
            target_id="post-1", title_fn=self._title_fn, window_seconds=1,
        )

        notifs = Notification.objects.filter(recipient=self.owner).order_by("created_at")
        self.assertEqual(notifs.count(), 2)
        self.assertNotEqual(notifs.first().id, notifs.last().id)
        self.assertEqual(notifs.first().id, first_id)