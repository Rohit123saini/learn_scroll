# message/tests_call_state.py — C6 + C7 (likha gaya, CHALAYA NAHI gaya)
#
#   python manage.py test message.tests_call_state
#
# Push / WebSocket / LiveKit sab mock hain.
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import call_state
from .models import (
    CallParticipant, CallSession, CallStatus, Conversation, ConversationParticipant,
)

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password='x', email=f'{name}@example.com')


def _dm(a, b):
    conv = Conversation.objects.create(type='private', private_key='-'.join(sorted([str(a.id), str(b.id)])))
    for u in (a, b):
        ConversationParticipant.objects.create(conversation=conv, user=u)
    return conv


def _group_conv(*users):
    conv = Conversation.objects.create(type='group')
    for u in users:
        ConversationParticipant.objects.create(conversation=conv, user=u)
    return conv


def _call(conv, caller, callees, *, status=CallStatus.RINGING, group=False, started_ago=0):
    call = CallSession.objects.create(
        type='audio', status=status, conversation=conv, is_group_call=group, caller=caller,
        channel_name=f'call_{CallSession.objects.count()}_{caller.id.hex[:6]}',
        started_at=timezone.now() - timedelta(seconds=started_ago),
        connected_at=timezone.now() if status == CallStatus.ONGOING else None,
    )
    CallParticipant.objects.create(call=call, user=caller, status=CallStatus.ONGOING)
    for u in callees:
        CallParticipant.objects.create(call=call, user=u, status=CallStatus.RINGING)
    return call


class AcceptStateTests(TestCase):
    def setUp(self):
        self.a, self.b = _user('a'), _user('b')
        self.conv = _dm(self.a, self.b)

    def test_accept_moves_ringing_to_ongoing_and_sets_connected_at(self):
        call = _call(self.conv, self.a, [self.b])
        res = call_state.accept_call(call.id, self.b.id)
        self.assertTrue(res.ok and res.changed)
        call.refresh_from_db()
        self.assertEqual(CallStatus.ONGOING, call.status)
        self.assertIsNotNone(call.connected_at)

    def test_accept_is_idempotent(self):
        call = _call(self.conv, self.a, [self.b])
        call_state.accept_call(call.id, self.b.id)
        res = call_state.accept_call(call.id, self.b.id)
        self.assertTrue(res.ok)
        self.assertFalse(res.changed)  # dobara broadcast nahi

    def test_accept_refused_for_every_terminal_status(self):
        for st in (CallStatus.ENDED, CallStatus.MISSED, CallStatus.REJECTED,
                   CallStatus.CANCELLED, CallStatus.BUSY):
            call = _call(self.conv, self.a, [self.b], status=st)
            res = call_state.accept_call(call.id, self.b.id)
            self.assertEqual(400, res.http_status, st)
            self.assertEqual('call_closed', res.code)

    def test_accept_refused_after_own_reject(self):
        call = _call(self.conv, self.a, [self.b], group=True)
        c = _user('c')
        CallParticipant.objects.create(call=call, user=c, status=CallStatus.RINGING)
        call_state.reject_call(call.id, self.b.id)
        res = call_state.accept_call(call.id, self.b.id)
        self.assertEqual(400, res.http_status)
        self.assertEqual('invalid_state', res.code)

    def test_accept_by_non_participant_is_403(self):
        call = _call(self.conv, self.a, [self.b])
        res = call_state.accept_call(call.id, _user('x').id)
        self.assertEqual(403, res.http_status)

    def test_group_participant_can_rejoin_after_leaving_while_ongoing(self):
        c = _user('c')
        conv = _group_conv(self.a, self.b, c)
        call = _call(conv, self.a, [self.b, c], group=True)
        call_state.accept_call(call.id, self.b.id)
        call_state.accept_call(call.id, c.id)
        call_state.end_call(call.id, self.b.id)   # b leaves, c+a still there
        res = call_state.accept_call(call.id, self.b.id)
        self.assertTrue(res.ok and res.changed)


class RejectStateTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')

    def test_1to1_reject_closes_call(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        res = call_state.reject_call(call.id, self.b.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.REJECTED, call.status)
        self.assertEqual({'call_reject', 'call_end'}, {e['event'] for e in res.events})

    def test_reject_is_idempotent_even_after_close(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.reject_call(call.id, self.b.id)
        res = call_state.reject_call(call.id, self.b.id)
        self.assertTrue(res.ok)
        self.assertFalse(res.changed)

    def test_group_reject_notifies_others_with_counts_but_keeps_call(self):
        conv = _group_conv(self.a, self.b, self.c)
        call = _call(conv, self.a, [self.b, self.c], group=True)
        res = call_state.reject_call(call.id, self.b.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.RINGING, call.status)
        ev = res.events[0]
        self.assertEqual('call_reject', ev['event'])
        self.assertEqual(1, ev['pending_count'])

    def test_group_last_reject_closes_call(self):
        conv = _group_conv(self.a, self.b, self.c)
        call = _call(conv, self.a, [self.b, self.c], group=True)
        call_state.reject_call(call.id, self.b.id)
        res = call_state.reject_call(call.id, self.c.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.REJECTED, call.status)
        self.assertTrue(any(e['event'] == 'call_end' and e.get('call_closed') for e in res.events))

    def test_reject_after_accept_is_409(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.accept_call(call.id, self.b.id)
        self.assertEqual(409, call_state.reject_call(call.id, self.b.id).http_status)

    def test_caller_reject_is_treated_as_cancel(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.reject_call(call.id, self.a.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.CANCELLED, call.status)


class EndStateTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')

    def test_caller_end_while_ringing_cancels_whole_call_and_misses_callees(self):
        conv = _group_conv(self.a, self.b, self.c)
        call = _call(conv, self.a, [self.b, self.c], group=True)
        res = call_state.end_call(call.id, self.a.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.CANCELLED, call.status)
        self.assertCountEqual([self.b.id, self.c.id], res.cancel_push_user_ids)
        self.assertEqual(
            {CallStatus.MISSED},
            set(CallParticipant.objects.filter(call=call).exclude(user=self.a).values_list('status', flat=True)),
        )

    def test_1to1_end_while_ongoing_ends_for_both_sides(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.accept_call(call.id, self.b.id)
        call_state.end_call(call.id, self.a.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, call.status)
        self.assertFalse(CallParticipant.objects.filter(call=call, left_at__isnull=True).exists())

    def test_group_end_by_one_keeps_call_until_last_leaves(self):
        conv = _group_conv(self.a, self.b, self.c)
        call = _call(conv, self.a, [self.b, self.c], group=True)
        call_state.accept_call(call.id, self.b.id)
        call_state.accept_call(call.id, self.c.id)
        call_state.end_call(call.id, self.a.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.ONGOING, call.status)
        call_state.end_call(call.id, self.b.id)
        call_state.end_call(call.id, self.c.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, call.status)
        self.assertGreaterEqual(call.duration_seconds, 0)

    def test_callee_end_while_ringing_is_a_reject(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.end_call(call.id, self.b.id)
        call.refresh_from_db()
        self.assertEqual(CallStatus.REJECTED, call.status)

    def test_end_is_idempotent(self):
        call = _call(_dm(self.a, self.b), self.a, [self.b])
        call_state.end_call(call.id, self.a.id)
        res = call_state.end_call(call.id, self.a.id)
        self.assertTrue(res.ok)
        self.assertFalse(res.changed)

    def test_valid_transitions_table(self):
        self.assertTrue(call_state.can_transition(CallStatus.RINGING, CallStatus.CANCELLED))
        self.assertFalse(call_state.can_transition(CallStatus.ENDED, CallStatus.ONGOING))
        self.assertFalse(call_state.can_transition(CallStatus.REJECTED, CallStatus.ONGOING))


class BusyRuleTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')

    def test_user_in_ongoing_call_is_busy(self):
        _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        self.assertIn(self.b.id, call_state.busy_status([self.b.id]))

    def test_user_being_rung_elsewhere_is_busy(self):
        _call(_dm(self.b, self.c), self.c, [self.b])
        info = call_state.busy_status([self.b.id])
        self.assertEqual(call_state.BUSY_RINGING_ELSEWHERE, info[self.b.id][0])

    def test_ghost_ringing_row_older_than_window_is_ignored(self):
        _call(_dm(self.b, self.c), self.c, [self.b], started_ago=600)
        self.assertEqual({}, call_state.busy_status([self.b.id]))

    def test_terminal_call_does_not_make_busy(self):
        _call(_dm(self.b, self.c), self.c, [self.b], status=CallStatus.ENDED)
        self.assertEqual({}, call_state.busy_status([self.b.id]))

    def test_exclude_call_id(self):
        call = _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        self.assertEqual({}, call_state.busy_status([self.b.id], exclude_call_id=call.id))

    def test_split_callees_default_busy(self):
        _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        ring, waiting, busy = call_state.split_callees([self.b.id, self.a.id])
        self.assertEqual(([self.a.id], [], [self.b.id]), (ring, waiting, busy))

    @override_settings(CALL_WAITING_ENABLED=True)
    def test_split_callees_call_waiting_enabled(self):
        _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        ring, waiting, busy = call_state.split_callees([self.b.id])
        self.assertEqual(([], [self.b.id], []), (ring, waiting, busy))

    @override_settings(CALL_WAITING_ENABLED=True)
    def test_ringing_elsewhere_stays_busy_even_with_call_waiting(self):
        _call(_dm(self.b, self.c), self.c, [self.b])
        _ring, _waiting, busy = call_state.split_callees([self.b.id])
        self.assertEqual([self.b.id], busy)


@mock.patch('message.views.calls.send_call_cancelled_push')
@mock.patch('message.views.calls.send_incoming_call_push')
@mock.patch('message.views.calls.generate_livekit_token', return_value='tok')
@mock.patch('message.views.calls.get_channel_layer')
@mock.patch('message.views.calls.async_to_sync', side_effect=lambda f: (lambda *a, **k: None))
class InitiateApiTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')
        self.conv = _dm(self.a, self.b)
        self.client = APIClient()
        self.client.force_authenticate(self.a)

    def _initiate(self, conv=None, **extra):
        return self.client.post(
            '/message/calls/initiate/',
            {'conversation_id': str(( conv or self.conv).id), 'type': 'audio', **extra},
            format='json',
        )

    def test_happy_path_rings_callee(self, _a2s, _cl, _tok, push, _cancel):
        resp = self._initiate()
        self.assertEqual(200, resp.status_code, resp.content)
        push.assert_called_once()
        self.assertEqual([self.b.id], list(push.call_args.kwargs['recipient_ids']))
        self.assertEqual([str(self.b.id)], resp.data['ringing_user_ids'])

    def test_invalid_type_is_400(self, *_m):
        resp = self.client.post('/message/calls/initiate/',
                                {'conversation_id': str(self.conv.id), 'type': 'hologram'}, format='json')
        self.assertEqual(400, resp.status_code)

    def test_callee_busy_returns_409_and_does_not_ring(self, _a2s, _cl, _tok, push, _cancel):
        _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        resp = self._initiate()
        self.assertEqual(409, resp.status_code)
        self.assertEqual('callee_busy', resp.data['code'])
        push.assert_not_called()
        busy_call = CallSession.objects.get(id=resp.data['call_id'])
        self.assertEqual(CallStatus.BUSY, busy_call.status)
        # callee ko missed-call entry
        self.assertTrue(CallParticipant.objects.filter(call=busy_call, user=self.b,
                                                       status=CallStatus.MISSED).exists())

    @override_settings(CALL_WAITING_ENABLED=True)
    def test_call_waiting_rings_with_flag(self, _a2s, _cl, _tok, push, _cancel):
        _call(_dm(self.b, self.c), self.b, [self.c], status=CallStatus.ONGOING)
        resp = self._initiate()
        self.assertEqual(200, resp.status_code)
        self.assertTrue(push.call_args.kwargs.get('call_waiting'))

    def test_caller_already_in_call_is_409(self, _a2s, _cl, _tok, push, _cancel):
        other = _user('o')
        _call(_dm(self.a, other), self.a, [other], status=CallStatus.ONGOING)
        resp = self._initiate()
        self.assertEqual(409, resp.status_code)
        self.assertEqual('already_in_call', resp.data['code'])
        push.assert_not_called()

    def test_replace_active_ends_old_call_and_starts_new(self, _a2s, _cl, _tok, push, _cancel):
        other = _user('o')
        old = _call(_dm(self.a, other), self.a, [other], status=CallStatus.ONGOING)
        resp = self._initiate(replace_active=True)
        self.assertEqual(200, resp.status_code, resp.content)
        old.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, old.status)

    def test_redial_same_conversation_cancels_previous_ring_and_pushes_cancel(self, _a2s, _cl, _tok, push, cancel):
        first = self._initiate()
        self.assertEqual(200, first.status_code)
        second = self._initiate()
        self.assertEqual(200, second.status_code)
        old = CallSession.objects.get(id=first.data['call_id'])
        self.assertEqual(CallStatus.CANCELLED, old.status)
        cancel.assert_called()

    # C11 — group ring push ab default Celery me jaata hai; ye test sync path (flag off) check karta hai.
    @override_settings(CALL_GROUP_PUSH_ASYNC=False)
    def test_group_skips_busy_member_but_still_rings_others(self, _a2s, _cl, _tok, push, _cancel):
        conv = _group_conv(self.a, self.b, self.c)
        _call(_dm(self.b, _user('z')), self.b, [], status=CallStatus.ONGOING)
        resp = self._initiate(conv=conv)
        self.assertEqual(200, resp.status_code, resp.content)
        self.assertEqual([self.c.id], list(push.call_args.kwargs['recipient_ids']))
        self.assertEqual([str(self.b.id)], resp.data['busy_user_ids'])
        self.assertEqual(
            CallStatus.BUSY,
            CallParticipant.objects.get(call_id=resp.data['call_id'], user=self.b).status,
        )

    def test_user_who_left_conversation_cannot_call(self, *_m):
        ConversationParticipant.objects.filter(conversation=self.conv, user=self.a).update(left_at=timezone.now())
        self.assertEqual(404, self._initiate().status_code)


@mock.patch('message.views.calls.send_call_cancelled_push')
@mock.patch('message.views.calls.generate_livekit_token', return_value='tok')
@mock.patch('message.views.calls.get_channel_layer')
@mock.patch('message.views.calls.async_to_sync', side_effect=lambda f: (lambda *a, **k: None))
class ActionApiTests(TestCase):
    def setUp(self):
        self.a, self.b = _user('a'), _user('b')
        self.call = _call(_dm(self.a, self.b), self.a, [self.b])
        self.client = APIClient()
        self.client.force_authenticate(self.b)

    def _act(self, action):
        return self.client.post(f'/message/calls/{self.call.id}/action/', {'action': action}, format='json')

    def test_accept_returns_token(self, *_m):
        resp = self._act('accept')
        self.assertEqual(200, resp.status_code)
        self.assertEqual('tok', resp.data['livekit_token'])

    def test_accept_on_rejected_call_gets_no_token(self, *_m):
        self._act('reject')
        resp = self._act('accept')
        self.assertEqual(400, resp.status_code)
        self.assertNotIn('livekit_token', resp.data)

    def test_reject_twice_is_200_both_times(self, *_m):
        self.assertEqual(200, self._act('reject').status_code)
        self.assertEqual(200, self._act('reject').status_code)

    def test_non_participant_is_403(self, *_m):
        self.client.force_authenticate(_user('stranger'))
        self.assertEqual(403, self._act('accept').status_code)

    def test_caller_cancel_pushes_ring_cancel_to_callee(self, _a2s, _cl, _tok, cancel):
        self.client.force_authenticate(self.a)
        self.assertEqual(200, self._act('end').status_code)
        cancel.assert_called_once()
        self.assertEqual([self.b.id], list(cancel.call_args.kwargs['recipient_ids']))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.CANCELLED, self.call.status)
