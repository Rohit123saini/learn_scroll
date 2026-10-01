# message/tests.py
"""
Study-room whiteboard: multi-colour strokes must survive BOTH transports.

The backend never interprets whiteboard geometry — it is an opaque JSON
passthrough — so "multi-colour" needs no schema/consumer change. These tests
pin that contract so a future "validation/sanitising" change can't silently
strip the per-point `color` (and `tool`, `strokeWidth`) the Flutter client
relies on:

  1. REST persistence: PUT /message/study-room/<id>/state/ then GET as a
     different participant (a late joiner) returns every stroke with its own
     colour, eraser strokes keep their `tool`, shapes/text/sticky notes keep
     their colours.
  2. Realtime relay: ChatConsumer.handle_study_room_event forwards `data`
     verbatim to every OTHER socket in the room (not echoed to the sender).
"""
from unittest import mock

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient

from . import push_utils
from .models import (
    Conversation,
    ConversationParticipant,
    ConversationType,
    Message,
    MessageStatus,
    RequestStatus,
)
from .routing import websocket_urlpatterns

User = get_user_model()

RED, BLUE, GREEN, CUSTOM = 0xFFF44336, 0xFF2196F3, 0xFF4CAF50, 0xFF12AB34


def _pt(dx, dy, color, tool="marker", width=3.0):
    return {"dx": dx, "dy": dy, "color": color, "strokeWidth": width, "tool": tool}


def _board():
    return {
        "pages": [
            {
                "id": "page_1",
                "strokes": [
                    [_pt(1, 1, RED), _pt(2, 2, RED)],
                    [_pt(5, 5, BLUE, "paint", 6.0), _pt(6, 6, BLUE, "paint", 6.0)],
                    [_pt(9, 9, CUSTOM, "highlighter", 10.0)],
                    [_pt(3, 3, 0xFF000000, "eraser", 16.0), _pt(4, 4, 0xFF000000, "eraser", 16.0)],
                ],
                "shapes": [
                    {
                        "id": "s1", "userId": "1", "startDx": 0, "startDy": 0,
                        "endDx": 10, "endDy": 10, "tool": "rectangle",
                        "color": GREEN, "strokeWidth": 4.0,
                    }
                ],
                "texts": [
                    {"id": "t1", "userId": "1", "text": "hi", "dx": 1, "dy": 2,
                     "color": CUSTOM, "fontSize": 16}
                ],
                "stickyNotes": [],
            }
        ]
    }


def _make_room():
    teacher = User.objects.create_user(username="teacher", password="x")
    student = User.objects.create_user(username="student", password="x")
    conv = Conversation.objects.create(type=ConversationType.GROUP)
    ConversationParticipant.objects.create(conversation=conv, user=teacher)
    ConversationParticipant.objects.create(conversation=conv, user=student)
    return conv, teacher, student


class WhiteboardColourPersistenceTests(TestCase):
    def test_late_joiner_loads_every_stroke_with_its_own_colour(self):
        conv, teacher, student = _make_room()
        url = f"/message/study-room/{conv.id}/state/"

        c1 = APIClient()
        c1.force_authenticate(teacher)
        board = _board()
        self.assertEqual(c1.put(url, board, format="json").status_code, 200)

        c2 = APIClient()
        c2.force_authenticate(student)  # joins later
        res = c2.get(url)
        self.assertEqual(res.status_code, 200)
        page = res.json()["pages"][0]

        # Exactly what was saved — colours, tools and widths all intact.
        self.assertEqual(page["strokes"], board["pages"][0]["strokes"])
        self.assertEqual(
            [s[0]["color"] for s in page["strokes"]], [RED, BLUE, CUSTOM, 0xFF000000]
        )
        self.assertEqual(page["strokes"][3][0]["tool"], "eraser")
        self.assertEqual(page["shapes"][0]["color"], GREEN)
        self.assertEqual(page["texts"][0]["color"], CUSTOM)


class WhiteboardColourRealtimeRelayTests(TransactionTestCase):
    def _communicator(self, conv, user):
        app = URLRouter(websocket_urlpatterns)
        comm = WebsocketCommunicator(app, f"/ws/chat/{conv.id}/")
        comm.scope["user"] = user
        return comm

    async def _drain(self, comm):
        """Discard connect-time chatter (presence / sync) until quiet."""
        while not await comm.receive_nothing(timeout=0.2):
            await comm.receive_json_from()

    async def test_draw_point_colour_reaches_other_participants_only(self):
        from asgiref.sync import sync_to_async

        conv, teacher, student = await sync_to_async(_make_room)()
        a = self._communicator(conv, teacher)
        b = self._communicator(conv, student)
        ok_a, _ = await a.connect()
        ok_b, _ = await b.connect()
        self.assertTrue(ok_a and ok_b)
        await self._drain(a)
        await self._drain(b)

        payload = {
            "point": _pt(10, 20, CUSTOM, "paint", 6.0),
            "isNew": True,
            "pageId": "page_1",
            "userId": str(teacher.id),
        }
        await a.send_json_to(
            {"type": "study_room_event", "action": "draw_point", "data": payload}
        )

        got = await b.receive_json_from(timeout=2)
        self.assertEqual(got["type"], "study_room_event")
        self.assertEqual(got["action"], "draw_point")
        self.assertEqual(got["data"], payload)  # verbatim, colour included
        self.assertEqual(got["data"]["point"]["color"], CUSTOM)

        # The sender is not echoed its own stroke.
        self.assertTrue(await a.receive_nothing(timeout=0.3))

        await a.disconnect()
        await b.disconnect()


# ======================================================================
# M1-BE — MESSAGE REQUESTS
# ======================================================================
# Follow model (`user_profile`) ka exact naam in tests se independent rakha
# hai: `message_requests.is_following` ko patch karte hain. Adapter ka apna
# fail-open behaviour alag test me hai.
FOLLOW_FN = "message.message_requests.is_following"


def _follows(*pairs):
    """is_following(follower_id, target_id) ka fake — sirf diye gaye (a, b) pairs True."""
    allowed = {(a.id, b.id) for a, b in pairs}
    return lambda follower_id, target_id: (follower_id, target_id) in allowed


def _dm(sender, receiver, text="hi"):
    """Do users ke beech private conversation (agar nahi hai) + usme sender ka ek message."""
    conv, _ = Conversation.get_or_create_private(sender, receiver)
    msg = Message.objects.create(conversation=conv, sender=sender, text=text)
    Conversation.objects.filter(pk=conv.pk).update(
        last_message_text=text, last_message_at=msg.created_at, last_message_sender=sender,
    )
    return conv, msg


def _status(conv, user):
    return ConversationParticipant.objects.get(conversation=conv, user=user).request_status


def _ids(response, key="results"):
    body = response.json()
    rows = body[key] if isinstance(body, dict) and key in body else body
    return {str(r["id"]) for r in rows}


class MessageRequestRuleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sender = User.objects.create_user(username="sender", password="x")
        self.receiver = User.objects.create_user(username="receiver", password="x")
        self.client_r = APIClient()
        self.client_r.force_authenticate(self.receiver)
        self.client_s = APIClient()
        self.client_s.force_authenticate(self.sender)

    # ---- routing: kaun inbox me, kaun requests me ----
    def test_followers_dm_lands_directly_in_inbox(self):
        # sender receiver ko follow karta hai ("follower ka DM")
        with mock.patch(FOLLOW_FN, side_effect=_follows((self.sender, self.receiver))):
            conv, _ = _dm(self.sender, self.receiver)
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)
        self.assertIn(str(conv.id), _ids(self.client_r.get("/message/conversations/")))
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/requests/")))

    def test_followed_user_dm_lands_directly_in_inbox(self):
        # receiver sender ko follow karta hai — ye bhi known contact hai
        with mock.patch(FOLLOW_FN, side_effect=_follows((self.receiver, self.sender))):
            conv, _ = _dm(self.sender, self.receiver)
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

    def test_strangers_dm_goes_to_requests_not_inbox(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver, "hello stranger")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.PENDING)
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/conversations/")))

        res = self.client_r.get("/message/requests/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(str(conv.id), _ids(res))
        row = next(r for r in res.json()["results"] if str(r["id"]) == str(conv.id))
        self.assertEqual(row["request_status"], "pending")
        self.assertEqual(row["requester"]["username"], "sender")
        inbox = self.client_s.get("/message/conversations/").json()["results"]
        self.assertTrue(inbox and all(r["request_status"] == "accepted" for r in inbox))

        # sender ki apni inbox normal rehti hai
        self.assertEqual(_status(conv, self.sender), RequestStatus.ACCEPTED)
        self.assertIn(str(conv.id), _ids(self.client_s.get("/message/conversations/")))

    def test_pending_conversation_is_still_openable_by_receiver(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver, "open me")
        res = self.client_r.get(f"/message/conversations/{conv.id}/messages/")
        self.assertEqual(res.status_code, 200)

    def test_only_first_message_creates_request(self):
        # Pehle se chal rahi (accepted) chat me naya message pending nahi banata.
        conv, _ = Conversation.get_or_create_private(self.sender, self.receiver)
        Message.objects.create(conversation=conv, sender=self.receiver, text="old history")
        with mock.patch(FOLLOW_FN, return_value=False):
            Message.objects.create(conversation=conv, sender=self.sender, text="later")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

    def test_group_conversations_never_become_requests(self):
        conv, teacher, student = _make_room()
        with mock.patch(FOLLOW_FN, return_value=False):
            Message.objects.create(conversation=conv, sender=teacher, text="class at 5")
        self.assertEqual(_status(conv, student), RequestStatus.ACCEPTED)

    def test_system_messages_do_not_create_requests(self):
        conv, _ = Conversation.get_or_create_private(self.sender, self.receiver)
        with mock.patch(FOLLOW_FN, return_value=False):
            Message.objects.create(
                conversation=conv, sender=self.sender, text="joined", is_system_message=True,
            )
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

    def test_pending_is_promoted_when_follow_appears(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
        self.assertEqual(_status(conv, self.receiver), RequestStatus.PENDING)
        with mock.patch(FOLLOW_FN, side_effect=_follows((self.receiver, self.sender))):
            Message.objects.create(conversation=conv, sender=self.sender, text="again")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

    def test_replying_accepts_the_request(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
            Message.objects.create(conversation=conv, sender=self.receiver, text="hey!")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)
        self.assertIn(str(conv.id), _ids(self.client_r.get("/message/conversations/")))

    def test_real_follow_model_pending_follow_request_does_not_count(self):
        from user_profile.models import Follow

        # sender ne receiver (private profile) ko follow-REQUEST bheji, abhi PENDING
        Follow.objects.create(follower=self.sender, following=self.receiver, status=Follow.Status.PENDING)
        conv, _ = _dm(self.sender, self.receiver)
        self.assertEqual(_status(conv, self.receiver), RequestStatus.PENDING)

    def test_real_follow_model_accepted_follow_counts_both_directions(self):
        from user_profile.models import Follow

        Follow.objects.create(follower=self.sender, following=self.receiver, status=Follow.Status.ACCEPTED)
        conv, _ = _dm(self.sender, self.receiver)
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

        a = User.objects.create_user(username="oa", password="x")
        b = User.objects.create_user(username="ob", password="x")
        Follow.objects.create(follower=b, following=a, status=Follow.Status.ACCEPTED)
        conv2, _ = _dm(a, b)  # receiver (b) sender (a) ko follow karta hai
        self.assertEqual(_status(conv2, b), RequestStatus.ACCEPTED)

    def test_follow_lookup_failure_fails_open(self):
        conv, _ = Conversation.get_or_create_private(self.sender, self.receiver)
        with self.settings(MESSAGE_REQUESTS_FOLLOW_MODEL="nope.DoesNotExist"):
            Message.objects.create(conversation=conv, sender=self.sender, text="hi")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)

    # ---- accept / decline ----
    def test_accept_moves_chat_to_inbox(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
        res = self.client_r.post(f"/message/requests/{conv.id}/accept/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["request_status"], "accepted")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.ACCEPTED)
        self.assertIn(str(conv.id), _ids(self.client_r.get("/message/conversations/")))
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/requests/")))
        # idempotent
        self.assertEqual(self.client_r.post(f"/message/requests/{conv.id}/accept/").status_code, 200)

    def test_decline_hides_chat_and_sender_notices_nothing(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, msg = _dm(self.sender, self.receiver)
        messages_before = Message.all_objects.filter(conversation=conv).count()

        with mock.patch.object(push_utils, "_send_multicast") as multicast:
            res = self.client_r.post(f"/message/requests/{conv.id}/decline/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["request_status"], "declined")
        multicast.assert_not_called()  # decline pe koi push nahi

        # receiver: na inbox, na requests
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/conversations/")))
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/requests/")))

        # sender: kuch nahi badla
        self.assertEqual(_status(conv, self.sender), RequestStatus.ACCEPTED)
        self.assertIn(str(conv.id), _ids(self.client_s.get("/message/conversations/")))
        self.assertEqual(
            self.client_s.get(f"/message/conversations/{conv.id}/messages/").status_code, 200
        )
        self.assertEqual(Message.all_objects.filter(conversation=conv).count(), messages_before)

    def test_declined_stays_declined_on_new_messages(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
            self.client_r.post(f"/message/requests/{conv.id}/decline/")
            Message.objects.create(conversation=conv, sender=self.sender, text="please?")
        self.assertEqual(_status(conv, self.receiver), RequestStatus.DECLINED)
        self.assertNotIn(str(conv.id), _ids(self.client_r.get("/message/requests/")))

    def test_cannot_decline_an_accepted_chat(self):
        conv, _ = Conversation.get_or_create_private(self.sender, self.receiver)
        res = self.client_r.post(f"/message/requests/{conv.id}/decline/")
        self.assertEqual(res.status_code, 409)

    def test_non_member_gets_404(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
        outsider = User.objects.create_user(username="outsider", password="x")
        c = APIClient()
        c.force_authenticate(outsider)
        self.assertEqual(c.post(f"/message/requests/{conv.id}/accept/").status_code, 404)

    # ---- badge / search ----
    def test_unread_count_excludes_pending_but_reports_requests(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, _ = _dm(self.sender, self.receiver)
        ConversationParticipant.objects.filter(conversation=conv, user=self.receiver).update(unread_count=3)
        body = self.client_r.get("/message/conversations/unread-count/").json()
        self.assertEqual(body["unread_count"], 0)
        self.assertEqual(body["message_requests_count"], 1)

        self.client_r.post(f"/message/requests/{conv.id}/accept/")
        body = self.client_r.get("/message/conversations/unread-count/").json()
        self.assertEqual(body["unread_count"], 3)
        self.assertEqual(body["message_requests_count"], 0)

    # ---- read receipts ----
    def test_read_all_by_pending_receiver_does_not_mark_read(self):
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, msg = _dm(self.sender, self.receiver)
        MessageStatus.objects.get_or_create(message=msg, user=self.receiver)

        self.assertEqual(self.client_r.post(f"/message/conversations/{conv.id}/read_all/").status_code, 200)
        self.assertEqual(self.client_r.post(f"/message/messages/{msg.id}/read/").status_code, 200)
        st = MessageStatus.objects.get(message=msg, user=self.receiver)
        self.assertFalse(st.is_read)
        self.assertIsNone(st.read_at)

        # accept ke baad normal read-receipt chalti hai
        self.client_r.post(f"/message/requests/{conv.id}/accept/")
        self.client_r.post(f"/message/conversations/{conv.id}/read_all/")
        st.refresh_from_db()
        self.assertTrue(st.is_read)


class MessageRequestPushTests(TestCase):
    def setUp(self):
        cache.clear()
        self.sender = User.objects.create_user(username="sender", password="x")
        self.pending = User.objects.create_user(username="pending", password="x")
        self.declined = User.objects.create_user(username="declined", password="x")
        self.normal = User.objects.create_user(username="normal", password="x")
        # 3 alag private chats, sender -> har receiver
        with mock.patch(FOLLOW_FN, return_value=False):
            self.c_pending, self.m_pending = _dm(self.sender, self.pending, "secret text")
            self.c_declined, self.m_declined = _dm(self.sender, self.declined)
        ConversationParticipant.objects.filter(
            conversation=self.c_declined, user=self.declined,
        ).update(request_status=RequestStatus.DECLINED)
        with mock.patch(FOLLOW_FN, return_value=True):  # follower ka DM -> seedha accepted
            self.c_normal, self.m_normal = _dm(self.sender, self.normal)

    def _push(self, user, conv, msg):
        with mock.patch.object(push_utils, "_send_multicast") as multicast, \
                mock.patch.object(push_utils, "create_notification") as bell:
            push_utils.send_chat_message_push(
                [user.id], "Sender", msg.text, "text", conv.id, msg.id,
            )
        return multicast, bell

    def test_pending_gets_message_request_push_without_text_and_no_bell_row(self):
        multicast, bell = self._push(self.pending, self.c_pending, self.m_pending)
        multicast.assert_called_once()
        data = multicast.call_args.kwargs["data"]
        self.assertEqual(data["type"], "message_request")
        self.assertEqual(data["title"], "Message request")
        self.assertNotIn("secret text", str(data))
        bell.assert_not_called()

    def test_pending_push_is_sent_only_once_per_conversation(self):
        first, _ = self._push(self.pending, self.c_pending, self.m_pending)
        second, _ = self._push(self.pending, self.c_pending, self.m_pending)
        first.assert_called_once()
        second.assert_not_called()

    def test_declined_gets_no_push_and_no_bell_row(self):
        multicast, bell = self._push(self.declined, self.c_declined, self.m_declined)
        multicast.assert_not_called()
        bell.assert_not_called()

    def test_accepted_gets_normal_chat_push(self):
        multicast, bell = self._push(self.normal, self.c_normal, self.m_normal)
        multicast.assert_called_once()
        self.assertEqual(multicast.call_args.kwargs["data"]["type"], "chat_message")
        bell.assert_called_once()

    def test_pending_mention_push_is_dropped(self):
        with mock.patch.object(push_utils, "_send_multicast") as multicast, \
                mock.patch.object(push_utils, "create_notification") as bell:
            push_utils.send_mention_push(
                [self.pending.id], "Sender", "@pending hi", self.c_pending.id, self.m_pending.id,
            )
        multicast.assert_not_called()
        bell.assert_not_called()


class MessageRequestRealtimeTests(TransactionTestCase):
    """Pending me receiver ke typing/read sender tak NAHI jaate (control: sender ke typing receiver tak jaate hain)."""

    def _communicator(self, conv, user):
        app = URLRouter(websocket_urlpatterns)
        comm = WebsocketCommunicator(app, f"/ws/chat/{conv.id}/")
        comm.scope["user"] = user
        return comm

    async def _drain(self, comm):
        while not await comm.receive_nothing(timeout=0.2):
            await comm.receive_json_from()

    def _setup(self):
        sender = User.objects.create_user(username="sender", password="x")
        receiver = User.objects.create_user(username="receiver", password="x")
        with mock.patch(FOLLOW_FN, return_value=False):
            conv, msg = _dm(sender, receiver)
        MessageStatus.objects.get_or_create(message=msg, user=receiver)
        return conv, msg, sender, receiver

    async def test_pending_receivers_typing_and_read_never_reach_sender(self):
        from asgiref.sync import sync_to_async

        conv, msg, sender, receiver = await sync_to_async(self._setup)()
        s = self._communicator(conv, sender)
        r = self._communicator(conv, receiver)
        ok_s, _ = await s.connect()
        ok_r, _ = await r.connect()
        self.assertTrue(ok_s and ok_r)
        await self._drain(s)
        await self._drain(r)

        await r.send_json_to({"type": "typing", "is_typing": True})
        await r.send_json_to({"type": "read", "message_id": str(msg.id)})
        self.assertTrue(await s.receive_nothing(timeout=0.5))

        st = await sync_to_async(MessageStatus.objects.get)(message=msg, user=receiver)
        self.assertFalse(st.is_read)
        self.assertFalse(st.is_delivered)

        # control: channel zinda hai — sender ka typing receiver tak pahunchta hai
        await s.send_json_to({"type": "typing", "is_typing": True})
        got = await r.receive_json_from(timeout=2)
        self.assertEqual(got["type"], "typing")

        await s.disconnect()
        await r.disconnect()
