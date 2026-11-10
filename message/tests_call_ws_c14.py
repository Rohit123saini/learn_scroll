# message/tests_call_ws_c14.py
"""
C14 - CallConsumer: dual signaling cleanup.

Covered:
  1. Group member jiski CallParticipant row NAHI hai -> socket connect reject (4003).
  2. Caller / invited (RINGING) callee -> connect allowed.
  3. {"type": "signal"} -> error `signal_not_supported`, kisi doosre socket ko relay NAHI.
  4. mute: sirf ONGOING participant; broadcast me client ke `event` / `user_id` / extra keys spoof nahi hote.
  5. RINGING participant ka connect "user_joined" broadcast nahi karta.

Run:  python manage.py test message.tests_call_ws_c14   (abhi chalaya nahi gaya)
"""
from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from .models import (
    CallParticipant, CallSession, CallStatus, Conversation, ConversationParticipant,
)
from .routing import websocket_urlpatterns

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password='x', email=f'{name}@example.com')


def _setup():
    caller, callee, outsider = _user('c14_caller'), _user('c14_callee'), _user('c14_outsider')
    conv = Conversation.objects.create(type='group')
    for u in (caller, callee, outsider):
        ConversationParticipant.objects.create(conversation=conv, user=u)
    call = CallSession.objects.create(
        type='audio', status=CallStatus.ONGOING, conversation=conv, is_group_call=False, caller=caller,
        channel_name='call_c14_test', started_at=timezone.now(), connected_at=timezone.now(),
    )
    CallParticipant.objects.create(call=call, user=caller, status=CallStatus.ONGOING)
    CallParticipant.objects.create(call=call, user=callee, status=CallStatus.RINGING)
    return caller, callee, outsider, call


class CallConsumerC14Tests(TransactionTestCase):
    def _comm(self, call, user):
        comm = WebsocketCommunicator(URLRouter(websocket_urlpatterns), f'/ws/call/{call.id}/')
        comm.scope['user'] = user
        return comm

    async def test_non_participant_cannot_connect(self):
        caller, callee, outsider, call = await sync_to_async(_setup)()
        ok, code = await self._comm(call, outsider).connect()
        self.assertFalse(ok)
        self.assertEqual(code, 4003)

    async def test_caller_and_ringing_callee_can_connect(self):
        caller, callee, outsider, call = await sync_to_async(_setup)()
        c1, c2 = self._comm(call, caller), self._comm(call, callee)
        self.assertTrue((await c1.connect())[0])
        self.assertTrue((await c2.connect())[0])
        await c1.disconnect()
        await c2.disconnect()

    async def test_signal_is_not_relayed(self):
        caller, callee, outsider, call = await sync_to_async(_setup)()
        await sync_to_async(
            CallParticipant.objects.filter(call=call, user=callee).update
        )(status=CallStatus.ONGOING)
        a, b = self._comm(call, caller), self._comm(call, callee)
        await a.connect()
        await b.connect()
        while not await b.receive_nothing(timeout=0.2):
            await b.receive_json_from()
        while not await a.receive_nothing(timeout=0.2):
            await a.receive_json_from()

        await a.send_json_to({'type': 'signal', 'payload': {'sdp': 'x'}})
        self.assertEqual((await a.receive_json_from())['code'], 'signal_not_supported')
        self.assertTrue(await b.receive_nothing(timeout=0.3))
        await a.disconnect()
        await b.disconnect()

    async def test_mute_payload_is_explicit_and_not_spoofable(self):
        caller, callee, outsider, call = await sync_to_async(_setup)()
        await sync_to_async(
            CallParticipant.objects.filter(call=call, user=callee).update
        )(status=CallStatus.ONGOING)
        a, b = self._comm(call, caller), self._comm(call, callee)
        await a.connect()
        await b.connect()
        while not await b.receive_nothing(timeout=0.2):
            await b.receive_json_from()

        await a.send_json_to({'type': 'mute', 'is_muted': True, 'event': 'signal',
                              'user_id': 'someone-else', 'junk': 'x'})
        msg = await b.receive_json_from()
        self.assertEqual(msg, {'event': 'mute', 'user_id': str(caller.id), 'is_muted': True})
        await a.disconnect()
        await b.disconnect()

    async def test_ringing_participant_connect_does_not_broadcast_user_joined(self):
        caller, callee, outsider, call = await sync_to_async(_setup)()
        a = self._comm(call, caller)
        await a.connect()
        while not await a.receive_nothing(timeout=0.2):
            await a.receive_json_from()
        b = self._comm(call, callee)   # callee abhi RINGING (accept nahi kiya)
        await b.connect()
        self.assertTrue(await a.receive_nothing(timeout=0.3))
        await a.disconnect()
        await b.disconnect()
