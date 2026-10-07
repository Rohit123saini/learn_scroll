"""
core/tests_follow_notifications.py   (Task 3.1 / 3.2 / 3.4)

Run:  python manage.py test core.tests_follow_notifications

3.1  a new follower folded into an already-READ batch row makes it unread again
     and moves it to the top (the bug: row stayed read/old -> bell never lit up)
3.2  follow / follow-request send a push (first follower of a batch only);
     push toggle + muted types are honoured
3.4  repost notifies the original owner (batched, undone on delete)
"""
import datetime
from unittest import mock

from django.core.cache import cache
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Notification, NotificationPreference

NT = Notification.NotifType
User = get_user_model()
PUSH = "message.push_utils.send_rich_push"


def _user(name, **kw):
    return User.objects.create_user(username=name, password="pw12345!", **kw)


class FollowNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = _user("owner")
        self.fans = [_user(f"fan{i}") for i in range(3)]

    def follow(self, who, target=None):
        c = APIClient()
        c.force_authenticate(who)
        r = c.post(reverse("follow-user", args=[(target or self.owner).id]))
        self.assertIn(r.status_code, (200, 201), getattr(r, "data", r.content))
        return r

    def rows(self, t=NT.FOLLOW_REQUEST_ACCEPTED):
        return Notification.objects.filter(recipient=self.owner, notif_type=t)

    # ---- 3.1 -----------------------------------------------------------
    def test_public_follow_creates_an_unread_row(self):
        self.follow(self.fans[0])
        row = self.rows().get()
        self.assertFalse(row.is_read)
        self.assertEqual(row.data["actor_id"], self.fans[0].id)

    def test_second_follower_after_read_makes_the_row_unread_and_newest(self):
        self.follow(self.fans[0])
        row = self.rows().get()
        old = timezone.now() - datetime.timedelta(hours=3)
        Notification.objects.filter(pk=row.pk).update(is_read=True, read_at=old, created_at=old)

        self.follow(self.fans[1])

        row.refresh_from_db()
        self.assertEqual(self.rows().count(), 1)  # still ONE batched row
        self.assertEqual(row.data["actor_count"], 2)
        self.assertFalse(row.is_read)
        self.assertIsNone(row.read_at)
        self.assertGreater(row.created_at, old + datetime.timedelta(hours=2))

    def test_unfollow_does_not_resurrect_a_read_row(self):
        self.follow(self.fans[0])
        self.follow(self.fans[1])
        row = self.rows().get()
        Notification.objects.filter(pk=row.pk).update(is_read=True)
        self.follow(self.fans[1])  # toggles = unfollow
        row.refresh_from_db()
        self.assertEqual(row.data["actor_count"], 1)
        self.assertTrue(row.is_read)

    # ---- 3.2 -----------------------------------------------------------
    @mock.patch(PUSH)
    def test_follow_sends_one_push_per_batch(self, push):
        self.follow(self.fans[0])
        self.follow(self.fans[1])
        self.follow(self.fans[2])
        self.assertEqual(push.call_count, 1)  # later followers fold, no push spam

    @mock.patch(PUSH)
    def test_follow_push_carries_follow_back_action(self, push):
        self.follow(self.fans[0])
        args, kwargs = push.call_args
        self.assertEqual(args[0], [self.owner.id])
        self.assertIn("follow_back", [a["id"] for a in kwargs["actions"]])

    @mock.patch(PUSH)
    def test_follow_request_to_private_account_pushes_with_accept_decline(self, push):
        User.objects.filter(pk=self.owner.pk).update(is_private=True)
        self.owner.refresh_from_db()
        self.follow(self.fans[0])
        self.assertEqual(self.rows(NT.FOLLOW_REQUEST_RECEIVED).count(), 1)
        self.assertEqual(push.call_count, 1)
        self.assertEqual({a["id"] for a in push.call_args.kwargs["actions"]}, {"confirm_follow", "delete_follow"})

    @mock.patch(PUSH)
    def test_push_disabled_in_preferences_means_bell_row_only(self, push):
        NotificationPreference.objects.update_or_create(user=self.owner, defaults={"push_enabled": False})
        self.follow(self.fans[0])
        self.assertEqual(self.rows().count(), 1)
        push.assert_not_called()

    @mock.patch(PUSH)
    def test_muted_type_means_bell_row_only(self, push):
        NotificationPreference.objects.update_or_create(
            user=self.owner, defaults={"muted_types": [NT.FOLLOW_REQUEST_ACCEPTED]})
        self.follow(self.fans[0])
        self.assertEqual(self.rows().count(), 1)
        push.assert_not_called()

    @mock.patch(PUSH, side_effect=RuntimeError("fcm down"))
    def test_push_failure_never_breaks_follow(self, push):
        r = self.follow(self.fans[0])
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.rows().count(), 1)


class RepostNotificationTests(TestCase):
    def setUp(self):
        cache.clear()
        from post.models import Post

        self.owner = _user("owner")
        self.a, self.b = _user("a"), _user("b")
        self.original = Post.objects.create(
            user=self.owner, content="hello", post_type="text", visibility="public",
            moderation_status="approved",
        )

    def repost(self, who):
        c = APIClient()
        c.force_authenticate(who)
        r = c.post(reverse("post-repost", args=[self.original.id]), {}, format="json")
        self.assertEqual(r.status_code, 201, getattr(r, "data", r.content))
        return r

    def rows(self):
        return Notification.objects.filter(recipient=self.owner, notif_type=NT.POST_REPOSTED)

    def test_repost_notifies_owner_and_batches(self):
        self.repost(self.a)
        self.repost(self.b)
        row = self.rows().get()
        self.assertEqual(row.data["actor_count"], 2)
        self.assertEqual(row.data["post_id"], str(self.original.id))
        self.assertIn("reposted your post", row.title)

    def test_own_repost_does_not_notify(self):
        self.repost(self.owner)
        self.assertEqual(self.rows().count(), 0)

    def test_undo_repost_removes_the_actor(self):
        r = self.repost(self.a)
        repost_id = r.data["data"]["id"]
        c = APIClient()
        c.force_authenticate(self.a)
        d = c.delete(reverse("post-delete", args=[repost_id]))
        self.assertEqual(d.status_code, 204)
        self.assertEqual(self.rows().count(), 0)

    def test_new_type_choices_exist_and_land_in_other(self):
        self.assertEqual(Notification.category_for(NT.POST_REPOSTED), "other")
        self.assertEqual(Notification.category_for(NT.STORY_REACTION), "other")


class LiveBadgeTests(TestCase):
    """3.3 - every create / read / delete publishes the unread total on the
    user's inbox socket group (message/consumers.py::notification_badge)."""

    def setUp(self):
        cache.clear()
        self.user = _user("owner")
        self.layer = mock.MagicMock()
        self.sent = []

        async def group_send(group, event):
            self.sent.append((group, event))

        self.layer.group_send = group_send

    def _run(self):
        return mock.patch("channels.layers.get_channel_layer", return_value=self.layer)

    def test_create_read_delete_publish_the_new_total(self):
        with self._run(), self.captureOnCommitCallbacks(execute=True):
            n = Notification.objects.create(recipient=self.user, notif_type=NT.GENERIC, title="a")
        self.assertEqual(self.sent[-1], (f"user_{self.user.id}", {"type": "notification_badge", "unread_count": 1}))

        with self._run(), self.captureOnCommitCallbacks(execute=True):
            n.mark_read()
        self.assertEqual(self.sent[-1][1]["unread_count"], 0)

        with self._run(), self.captureOnCommitCallbacks(execute=True):
            Notification.objects.create(recipient=self.user, notif_type=NT.GENERIC, title="b")
            Notification.objects.filter(recipient=self.user, is_read=False).first().delete()
        self.assertEqual(self.sent[-1][1]["unread_count"], 0)

    def test_no_channel_layer_is_a_silent_noop(self):
        with mock.patch("channels.layers.get_channel_layer", return_value=None), \
                self.captureOnCommitCallbacks(execute=True):
            Notification.objects.create(recipient=self.user, notif_type=NT.GENERIC, title="a")
        self.assertEqual(Notification.objects.count(), 1)
