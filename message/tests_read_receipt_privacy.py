# message/tests_read_receipt_privacy.py
"""
WS `read` broadcast must follow the same mutual WhatsApp-style rule as the REST
`read-status` endpoint (UserPresence.show_read_receipts):

  * reader has receipts OFF            -> NOBODY gets the blue-tick event
  * viewer (other socket) has it OFF   -> that viewer does not get the event
  * both ON                            -> event reaches the other participant
  * DB bookkeeping (MessageStatus.is_read / unread_count) is correct in EVERY case.

Run:  python manage.py test message.tests_read_receipt_privacy
"""
from unittest import mock

from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase

from .models import Conversation, ConversationParticipant, Message, MessageStatus, UserPresence
from .routing import websocket_urlpatterns

User = get_user_model()
FOLLOW_FN = "message.message_requests.is_following"


def _set_receipts(user, enabled):
    UserPresence.objects.update_or_create(user=user, defaults={"show_read_receipts": enabled})


def _setup(sender_receipts=True, reader_receipts=True):
    sender = User.objects.create_user(username="rr_sender", password="x")
    reader = User.objects.create_user(username="rr_reader", password="x")
    with mock.patch(FOLLOW_FN, return_value=True):  # accepted DM
        conv, _ = Conversation.get_or_create_private(sender, reader)
        msg = Message.objects.create(conversation=conv, sender=sender, text="hi")
    MessageStatus.objects.get_or_create(message=msg, user=reader)
    ConversationParticipant.objects.filter(conversation=conv, user=reader).update(unread_count=1)
    _set_receipts(sender, sender_receipts)
    _set_receipts(reader, reader_receipts)
    return sender, reader, conv, msg


class WsReadReceiptPrivacyTests(TransactionTestCase):
    def _comm(self, conv, user):
        comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f"/ws/chat/{conv.id}/")
        comm.scope["user"] = user
        return comm

    async def _drain(self, comm):
        while not await comm.receive_nothing(timeout=0.2):
            await comm.receive_json_from()

    async def _read_and_collect(self, sender_receipts, reader_receipts):
        """Reader sends `read`; returns (event seen by sender or None, status row, unread_count)."""
        sender, reader, conv, msg = await sync_to_async(_setup)(sender_receipts, reader_receipts)
        s, r = self._comm(conv, sender), self._comm(conv, reader)
        ok_s, _ = await s.connect()
        ok_r, _ = await r.connect()
        self.assertTrue(ok_s and ok_r)
        await self._drain(s)
        await self._drain(r)

        await r.send_json_to({"type": "read", "message_id": str(msg.id)})

        seen = None
        if not await s.receive_nothing(timeout=0.7):
            seen = await s.receive_json_from()
        await self._drain(r)

        status = await sync_to_async(MessageStatus.objects.get)(message=msg, user=reader)
        unread = await sync_to_async(
            lambda: ConversationParticipant.objects.get(conversation=conv, user=reader).unread_count
        )()
        await s.disconnect()
        await r.disconnect()
        return seen, status, unread

    async def test_both_on_sender_gets_read_event(self):
        seen, status, unread = await self._read_and_collect(True, True)
        self.assertIsNotNone(seen)
        self.assertEqual(seen["type"], "read")
        self.assertEqual(seen["user_id"], str(status.user_id))
        self.assertTrue(status.is_read)
        self.assertEqual(unread, 0)

    async def test_reader_off_nobody_gets_event_but_db_is_correct(self):
        seen, status, unread = await self._read_and_collect(True, False)
        self.assertIsNone(seen)
        self.assertTrue(status.is_read)
        self.assertIsNotNone(status.read_at)
        self.assertEqual(unread, 0)

    async def test_viewer_off_does_not_get_event_but_db_is_correct(self):
        seen, status, unread = await self._read_and_collect(False, True)
        self.assertIsNone(seen)
        self.assertTrue(status.is_read)
        self.assertEqual(unread, 0)

    async def test_both_off_no_event_db_correct(self):
        seen, status, unread = await self._read_and_collect(False, False)
        self.assertIsNone(seen)
        self.assertTrue(status.is_read)
        self.assertEqual(unread, 0)
