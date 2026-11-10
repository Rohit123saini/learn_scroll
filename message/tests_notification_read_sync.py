# message/tests_notification_read_sync.py
"""
Chats badge / bell desync fix: after a conversation/message is read, the
`core.Notification` rows behind `GET /core/notifications/unread-count/
?source=message` must be cleared on EVERY read path.

Covered:
  1. WebSocket `read` event      -> CHAT_MESSAGE (+ MENTION) row for that message cleared.
  2. REST `read_all`             -> CHAT_MESSAGE rows cleared.
  3. REST `read_all`             -> MENTION and INCOMING_CALL rows (data.conversation_id) cleared.
  4. REST `read_all` on a long chat (> 500 messages) leaves NO unread MessageStatus
     (the old `[:500]` cap skipped the rest).
  5. Control: rows of ANOTHER conversation stay unread.

Run:  python manage.py test message.tests_notification_read_sync
"""
from unittest import mock

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from core.models import Notification

from .models import Conversation, Message, MessageStatus
from .routing import websocket_urlpatterns

User = get_user_model()
NT = Notification.NotifType

# Same patch target message/tests.py uses: follower DMs are accepted directly.
FOLLOW_FN = "message.message_requests.is_following"
UNREAD_URL = "/core/notifications/unread-count/?source=message"


def _setup_chat():
    """sender -> receiver DM (accepted) with one message + the receiver's MessageStatus."""
    sender = User.objects.create_user(username="rs_sender", password="x")
    receiver = User.objects.create_user(username="rs_receiver", password="x")
    with mock.patch(FOLLOW_FN, return_value=True):
        conv, _ = Conversation.get_or_create_private(sender, receiver)
        msg = Message.objects.create(conversation=conv, sender=sender, text="hi")
    MessageStatus.objects.get_or_create(message=msg, user=receiver)
    return sender, receiver, conv, msg


def _notif(user, notif_type, conv, msg=None, **extra):
    data = {"conversation_id": str(conv.id), **extra}
    if msg is not None:
        data["message_id"] = str(msg.id)
    return Notification.objects.create(
        recipient=user, notif_type=notif_type, title=str(notif_type), data=data,
    )


class _UnreadCountMixin:
    def _unread(self, user):
        client = APIClient()
        client.force_authenticate(user)
        res = client.get(UNREAD_URL)
        self.assertEqual(res.status_code, 200)
        return res.json()["unread_count"]


class RestReadAllNotificationSyncTests(_UnreadCountMixin, TestCase):
    def setUp(self):
        self.sender, self.receiver, self.conv, self.msg = _setup_chat()
        self.client_r = APIClient()
        self.client_r.force_authenticate(self.receiver)

    def _read_all(self, conv=None):
        with mock.patch(FOLLOW_FN, return_value=True):
            return self.client_r.post(f"/message/conversations/{(conv or self.conv).id}/read_all/")

    def test_read_all_clears_chat_message_badge(self):
        _notif(self.receiver, NT.CHAT_MESSAGE, self.conv, self.msg)
        self.assertEqual(self._unread(self.receiver), 1)

        self.assertEqual(self._read_all().status_code, 200)

        self.assertEqual(self._unread(self.receiver), 0)

    def test_read_all_clears_mention_and_incoming_call_rows(self):
        _notif(self.receiver, NT.CHAT_MESSAGE, self.conv, self.msg)
        _notif(self.receiver, NT.MENTION, self.conv, self.msg)
        _notif(self.receiver, NT.INCOMING_CALL, self.conv, call_id="1", channel_name="ch")
        self.assertEqual(self._unread(self.receiver), 3)

        self.assertEqual(self._read_all().status_code, 200)

        self.assertEqual(self._unread(self.receiver), 0)

    def test_read_all_leaves_other_conversations_unread(self):
        other = User.objects.create_user(username="rs_other", password="x")
        with mock.patch(FOLLOW_FN, return_value=True):
            other_conv, _ = Conversation.get_or_create_private(other, self.receiver)
        _notif(self.receiver, NT.MENTION, self.conv, self.msg)
        _notif(self.receiver, NT.INCOMING_CALL, other_conv, call_id="2", channel_name="ch")

        self._read_all()

        self.assertEqual(self._unread(self.receiver), 1)  # only other_conv's call row

    def test_read_all_marks_every_message_in_long_chat(self):
        """> 500 messages: the old `[:500]` slice left the rest unread."""
        Message.objects.bulk_create(
            [Message(conversation=self.conv, sender=self.sender, text=f"m{i}") for i in range(520)]
        )
        extra = Message.objects.filter(conversation=self.conv).exclude(pk=self.msg.pk)
        MessageStatus.objects.bulk_create(
            [MessageStatus(message=m, user=self.receiver) for m in extra], ignore_conflicts=True,
        )
        qs = MessageStatus.objects.filter(
            user=self.receiver, message__conversation=self.conv, is_read=False,
        )
        self.assertEqual(qs.count(), 521)

        self.assertEqual(self._read_all().status_code, 200)

        self.assertEqual(qs.count(), 0)


class WsReadNotificationSyncTests(_UnreadCountMixin, TransactionTestCase):
    def _communicator(self, conv, user):
        app = URLRouter(websocket_urlpatterns)
        comm = WebsocketCommunicator(app, f"/ws/chat/{conv.id}/")
        comm.scope["user"] = user
        return comm

    async def _drain(self, comm):
        while not await comm.receive_nothing(timeout=0.2):
            await comm.receive_json_from()

    def _prepare(self):
        sender, receiver, conv, msg = _setup_chat()
        _notif(receiver, NT.CHAT_MESSAGE, conv, msg)
        _notif(receiver, NT.MENTION, conv, msg)
        return receiver, conv, msg

    async def test_ws_read_clears_message_badge_and_publishes_badge(self):
        receiver, conv, msg = await sync_to_async(self._prepare)()
        self.assertEqual(await sync_to_async(self._unread)(receiver), 2)

        comm = self._communicator(conv, receiver)
        ok, _ = await comm.connect()
        self.assertTrue(ok)
        await self._drain(comm)

        with mock.patch("core.services.publish_unread_badge") as publish:
            await comm.send_json_to({"type": "read", "message_id": str(msg.id)})
            await self._drain(comm)

        self.assertEqual(await sync_to_async(self._unread)(receiver), 0)
        publish.assert_called_with(receiver.id)
        await comm.disconnect()
