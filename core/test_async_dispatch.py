# core/test_async_dispatch.py
"""
Tests for the Celery side-effect layer: core/async_utils.py, core/tasks.py,
and the two call sites that use it (campus.bridge.notify, liveclass signals).

Run: python manage.py test core.test_async_dispatch

Under `manage.py test` settings.py turns on CELERY_TASK_ALWAYS_EAGER, so
`apply_async` runs the task inline and no Redis is needed.
"""
from unittest import mock

from django.test import TestCase

from core.async_utils import dispatch_after_commit
from core.models import Notification
from core.tasks import chat_sync, create_notifications
from login.models import User


class DispatchAfterCommitTests(TestCase):
    def test_enqueues_only_after_commit(self):
        task = mock.Mock()
        task.name = "fake.task"
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            dispatch_after_commit(task, 1, "a", k="v")
        task.apply_async.assert_not_called()  # nothing published before commit
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()  # simulate the commit
        task.apply_async.assert_called_once()
        _, kw = task.apply_async.call_args
        self.assertEqual(kw["args"], (1, "a"))
        self.assertEqual(kw["kwargs"], {"k": "v"})

    def test_broker_down_falls_back_to_inline_run(self):
        task = mock.Mock()
        task.name = "fake.task"
        task.apply_async.side_effect = ConnectionError("redis down")
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_after_commit(task, 1)
        task.apply.assert_called_once_with(args=(1,), kwargs={})

    def test_never_raises_even_if_fallback_fails(self):
        task = mock.Mock()
        task.name = "fake.task"
        task.apply_async.side_effect = ConnectionError("redis down")
        task.apply.side_effect = RuntimeError("boom")
        with self.captureOnCommitCallbacks(execute=True):
            dispatch_after_commit(task, 1)  # must not raise


class CreateNotificationsTaskTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass12345")
        self.bob = User.objects.create_user(username="bob", password="pass12345")

    def test_creates_one_row_per_recipient(self):
        create_notifications.apply(
            args=([self.alice.pk, self.bob.pk], Notification.NotifType.NOTICE_POSTED, "Hi", "Body"),
        ).get()
        self.assertEqual(Notification.objects.filter(title="Hi").count(), 2)

    def test_campus_notify_goes_through_celery_layer(self):
        from campus import bridge

        with self.captureOnCommitCallbacks(execute=True):
            bridge.notify(
                users=[self.alice, self.bob.pk],
                notif_type=Notification.NotifType.NOTICE_POSTED,
                title="Campus hello",
                body="x",
            )
        self.assertEqual(Notification.objects.filter(title="Campus hello").count(), 2)

    def test_campus_notify_never_raises_if_dispatch_breaks(self):
        from campus import bridge

        with mock.patch("campus.bridge.dispatch_after_commit", side_effect=RuntimeError("boom")):
            self.assertEqual(bridge.notify(users=[self.alice], notif_type="x", title="t"), [])


class ChatSyncTaskTests(TestCase):
    def test_unknown_action_is_dropped(self):
        chat_sync.apply(args=("nope", 1)).get()  # no exception

    def test_missing_classroom_is_a_noop(self):
        with mock.patch("core.classroom_chat_bridge.sync_group_metadata") as m:
            chat_sync.apply(args=("metadata", 999999)).get()
        m.assert_not_called()
