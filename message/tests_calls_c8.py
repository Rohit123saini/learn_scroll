# message/tests_calls_c8.py — C8 (likha gaya, CHALAYA NAHI gaya)
#
# Pehle `CallSession` / `calls/initiate` ka koi test nahi tha. Ye file baaki gaps cover karti hai
# (state machine / busy ke tests `tests_call_state.py`, push routing `tests_call_push.py` me):
#
#   initiate  : blocked, group call_permission, throttle, LiveKit-config-missing (503), validation
#   races     : accept vs end/cancel (sequential + real threads jahan DB row-lock support kare)
#   sweeper   : ring timeout, empty ongoing, max duration, late invites, idempotency, fresh calls safe
#   webhook   : joined / left / room_finished / egress_ended, idempotency, signature 401/503
#   add-participant : auth (stranger 404, non-ongoing 403), membership, block, busy, re-invite
#   missed list     : kaun dikhta hai, since filter, own calls exclude, cancelled-by-caller
#
#   python manage.py test message.tests_calls_c8
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from . import call_state, tasks
from .livekit_webhook import WebhookVerificationError, handle_message_event
from .models import (
    BlockedUser, CallParticipant, CallSession, CallStatus, Group, GroupMember,
)
from .tests_call_state import _call, _dm, _group_conv, _user


def _mk_group(conv, owner, *, call_permission='everyone'):
    return Group.objects.create(
        conversation=conv, name='Study Group', created_by=owner, call_permission=call_permission,
    )


# Views ke network side-effects (WS / push / LiveKit token) mock karne wale patches.
# Mock args ka order: pehle lagaya gaya patch = pehla argument, isliye test signature
# `(self, _cancel, push, _tok, _cl, _a2s)` hai.
def _view_patches(fn):
    fn = mock.patch('message.views.calls.send_call_cancelled_push')(fn)
    fn = mock.patch('message.views.calls.send_incoming_call_push')(fn)
    fn = mock.patch('message.views.calls.generate_livekit_token', return_value='tok')(fn)
    fn = mock.patch('message.views.calls.get_channel_layer')(fn)
    fn = mock.patch('message.views.calls.async_to_sync', side_effect=lambda f: (lambda *a, **k: None))(fn)
    return fn


# ======================================================================
# INITIATE
# ======================================================================
class InitiateGuardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')
        self.client = APIClient()
        self.client.force_authenticate(self.a)

    def _post(self, conv, **extra):
        return self.client.post('/message/calls/initiate/',
                                {'conversation_id': str(conv.id), 'type': 'audio', **extra}, format='json')

    def test_missing_conversation_id_is_400(self):
        resp = self.client.post('/message/calls/initiate/', {'type': 'audio'}, format='json')
        self.assertEqual(400, resp.status_code)

    def test_unauthenticated_is_rejected(self):
        resp = APIClient().post('/message/calls/initiate/', {'conversation_id': 'x'}, format='json')
        self.assertIn(resp.status_code, (401, 403))

    def test_stranger_conversation_is_404(self):
        conv = _dm(self.b, self.c)
        self.assertEqual(404, self._post(conv).status_code)

    @_view_patches
    def test_blocked_pair_is_403_and_creates_nothing(self, _cancel, push, _tok, _cl, _a2s):
        conv = _dm(self.a, self.b)
        BlockedUser.objects.create(blocker=self.b, blocked=self.a)  # b ne a ko block kiya
        resp = self._post(conv)
        self.assertEqual(403, resp.status_code)
        self.assertEqual(0, CallSession.objects.count())
        push.assert_not_called()

    @_view_patches
    def test_blocked_in_either_direction(self, _cancel, push, _tok, _cl, _a2s):
        conv = _dm(self.a, self.b)
        BlockedUser.objects.create(blocker=self.a, blocked=self.b)  # a ne b ko block kiya
        self.assertEqual(403, self._post(conv).status_code)

    @_view_patches
    def test_group_admins_only_blocks_plain_member(self, _cancel, push, _tok, _cl, _a2s):
        conv = _group_conv(self.a, self.b, self.c)
        group = _mk_group(conv, self.b, call_permission='admins_only')
        GroupMember.objects.create(group=group, user=self.b, role='admin')
        GroupMember.objects.create(group=group, user=self.a, role='member')
        GroupMember.objects.create(group=group, user=self.c, role='member')
        resp = self._post(conv)
        self.assertEqual(403, resp.status_code)
        self.assertEqual(0, CallSession.objects.count())
        push.assert_not_called()

    @_view_patches
    def test_group_admins_only_allows_admin(self, _cancel, push, _tok, _cl, _a2s):
        conv = _group_conv(self.a, self.b, self.c)
        group = _mk_group(conv, self.a, call_permission='admins_only')
        GroupMember.objects.create(group=group, user=self.a, role='admin')
        GroupMember.objects.create(group=group, user=self.b, role='member')
        GroupMember.objects.create(group=group, user=self.c, role='member')
        resp = self._post(conv)
        self.assertEqual(200, resp.status_code, resp.content)
        self.assertCountEqual([self.b.id, self.c.id], list(push.call_args.kwargs['recipient_ids']))

    @_view_patches
    def test_group_everyone_allows_member(self, _cancel, push, _tok, _cl, _a2s):
        conv = _group_conv(self.a, self.b)
        group = _mk_group(conv, self.b, call_permission='everyone')
        GroupMember.objects.create(group=group, user=self.a, role='member')
        GroupMember.objects.create(group=group, user=self.b, role='admin')
        self.assertEqual(200, self._post(conv).status_code)

    @_view_patches
    def test_livekit_not_configured_is_503_without_ghost_ring(self, _cancel, push, tok, _cl, _a2s):
        tok.side_effect = RuntimeError('LIVEKIT_API_KEY missing')
        resp = self._post(_dm(self.a, self.b))
        self.assertEqual(503, resp.status_code)
        self.assertEqual(0, CallSession.objects.count())
        push.assert_not_called()

    def test_call_initiate_user_throttle_429_after_10_per_minute(self):
        # throttle view ke andar ki validation se pehle chalta hai => 400 wale requests bhi gine jaate hain
        codes = [self.client.post('/message/calls/initiate/', {}, format='json').status_code for _ in range(11)]
        self.assertEqual([400] * 10, codes[:10])
        self.assertEqual(429, codes[10])


# ======================================================================
# RACES
# ======================================================================
class SequentialRaceTests(TestCase):
    """Dono orderings: jo bhi pehle lock le, final state consistent honi chahiye."""

    def setUp(self):
        self.a, self.b = _user('a'), _user('b')
        self.conv = _dm(self.a, self.b)

    def _assert_consistent(self, call):
        call.refresh_from_db()
        self.assertIn(call.status, call_state.TERMINAL_STATUSES)
        self.assertFalse(
            CallParticipant.objects.filter(call=call, status=CallStatus.ONGOING, left_at__isnull=True).exists(),
            "terminal call me koi live ONGOING participant nahi hona chahiye",
        )

    def test_accept_then_caller_end(self):
        call = _call(self.conv, self.a, [self.b])
        self.assertTrue(call_state.accept_call(call.id, self.b.id).ok)
        call_state.end_call(call.id, self.a.id)
        self._assert_consistent(call)
        self.assertEqual(CallStatus.ENDED, call.status)

    def test_caller_cancel_then_late_accept_gets_no_join(self):
        call = _call(self.conv, self.a, [self.b])
        call_state.end_call(call.id, self.a.id)
        res = call_state.accept_call(call.id, self.b.id)
        self.assertEqual(400, res.http_status)
        self._assert_consistent(call)
        self.assertEqual(CallStatus.CANCELLED, call.status)

    def test_sweeper_timeout_then_late_accept_gets_no_join(self):
        call = _call(self.conv, self.a, [self.b], started_ago=100)
        with mock.patch.object(tasks, 'get_channel_layer', return_value=None), \
             mock.patch('message.push_utils.send_call_cancelled_push'):
            tasks.sweep_stale_calls()
        self.assertEqual(400, call_state.accept_call(call.id, self.b.id).http_status)
        self._assert_consistent(call)

    def test_reject_then_accept_then_end_all_safe(self):
        call = _call(self.conv, self.a, [self.b])
        call_state.reject_call(call.id, self.b.id)
        self.assertEqual(400, call_state.accept_call(call.id, self.b.id).http_status)
        self.assertTrue(call_state.end_call(call.id, self.a.id).ok)
        self._assert_consistent(call)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentRaceTests(TransactionTestCase):
    """Asli threads — sirf Postgres/MySQL par (SQLite me select_for_update no-op hai)."""

    def test_accept_vs_caller_end_never_leaves_live_participant(self):
        a, b = _user('ca'), _user('cb')
        conv = _dm(a, b)
        for _ in range(5):
            call = _call(conv, a, [b])
            barrier = threading.Barrier(2)
            results = {}

            def run(name, fn):
                try:
                    barrier.wait()
                    results[name] = fn(call.id, a.id if name == 'end' else b.id)
                finally:
                    connection.close()

            t1 = threading.Thread(target=run, args=('accept', call_state.accept_call))
            t2 = threading.Thread(target=run, args=('end', call_state.end_call))
            t1.start(); t2.start(); t1.join(); t2.join()

            call.refresh_from_db()
            self.assertIn(call.status, (CallStatus.ENDED, CallStatus.CANCELLED), call.status)
            self.assertFalse(CallParticipant.objects.filter(
                call=call, status=CallStatus.ONGOING, left_at__isnull=True).exists())
            # accept sirf tab safal ho sakta tha jab wo end se pehle lock le
            if results['accept'].http_status == 400:
                self.assertEqual(CallStatus.CANCELLED, call.status)


# ======================================================================
# SWEEPER
# ======================================================================
@mock.patch('message.push_utils.send_call_cancelled_push')
@mock.patch.object(tasks, 'get_channel_layer', return_value=None)
class SweeperTests(TestCase):
    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')
        self.conv = _dm(self.a, self.b)

    def test_ring_timeout_marks_missed_and_pushes_cancel(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], started_ago=100)
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.MISSED, call.status)
        self.assertIsNotNone(call.ended_at)
        self.assertEqual(CallStatus.MISSED, CallParticipant.objects.get(call=call, user=self.b).status)
        self.assertEqual(CallStatus.ENDED, CallParticipant.objects.get(call=call, user=self.a).status)
        cancel.assert_called_once()
        self.assertEqual([self.b.id], list(cancel.call_args.kwargs['recipient_ids']))

    def test_fresh_ringing_call_is_left_alone(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], started_ago=5)
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.RINGING, call.status)
        cancel.assert_not_called()

    def test_sweeper_is_idempotent(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], started_ago=100)
        tasks.sweep_stale_calls()
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.MISSED, call.status)
        self.assertEqual(1, cancel.call_count)

    def test_terminal_calls_untouched(self, _cl, cancel):
        for st in (CallStatus.ENDED, CallStatus.REJECTED, CallStatus.CANCELLED, CallStatus.BUSY):
            c = _call(self.conv, self.a, [self.b], status=st, started_ago=1000)
            before = c.status
            tasks.sweep_stale_calls()
            c.refresh_from_db()
            self.assertEqual(before, c.status)

    def test_ongoing_with_no_live_participant_is_ended(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], status=CallStatus.ONGOING, started_ago=200)
        CallParticipant.objects.filter(call=call).update(status=CallStatus.ENDED, left_at=timezone.now())
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, call.status)

    def test_ongoing_with_live_participant_is_kept(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], status=CallStatus.ONGOING, started_ago=200)
        CallParticipant.objects.filter(call=call, user=self.b).update(status=CallStatus.ONGOING)
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.ONGOING, call.status)

    @override_settings(CALL_STALE_ONGOING_SECONDS=3600)
    def test_max_duration_hard_cap(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], status=CallStatus.ONGOING, started_ago=7200)
        CallParticipant.objects.filter(call=call).update(status=CallStatus.ONGOING)
        tasks.sweep_stale_calls()
        call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, call.status)
        self.assertGreaterEqual(call.duration_seconds, 0)
        self.assertFalse(CallParticipant.objects.filter(call=call, left_at__isnull=True).exists())

    def test_late_invite_on_running_call_becomes_missed_but_call_continues(self, _cl, cancel):
        call = _call(self.conv, self.a, [self.b], status=CallStatus.ONGOING, started_ago=300)
        CallParticipant.objects.filter(call=call, user=self.b).update(status=CallStatus.ONGOING)
        late = CallParticipant.objects.create(call=call, user=self.c, status=CallStatus.RINGING)
        CallParticipant.objects.filter(id=late.id).update(joined_at=timezone.now() - timedelta(seconds=120))
        tasks.sweep_stale_calls()
        late.refresh_from_db()
        call.refresh_from_db()
        self.assertEqual(CallStatus.MISSED, late.status)
        self.assertEqual(CallStatus.ONGOING, call.status)
        cancel.assert_called_once()


# ======================================================================
# LIVEKIT WEBHOOK
# ======================================================================
def _evt(name, room=None, identity=None, **extra):
    ns = SimpleNamespace(event=name)
    if room is not None:
        ns.room = SimpleNamespace(name=room)
    if identity is not None:
        ns.participant = SimpleNamespace(identity=str(identity))
    for k, v in extra.items():
        setattr(ns, k, v)
    return ns


@mock.patch('message.push_utils.send_call_cancelled_push')
@mock.patch.object(tasks, 'get_channel_layer', return_value=None)
@mock.patch('message.livekit_webhook._broadcast')
class WebhookHandlerTests(TestCase):
    def setUp(self):
        self.a, self.b = _user('a'), _user('b')
        self.conv = _dm(self.a, self.b)
        self.call = _call(self.conv, self.a, [self.b])

    def test_callee_join_marks_ongoing_and_connects_call(self, _bc, _cl, _cancel):
        handle_message_event(_evt('participant_joined', self.call.channel_name, self.b.id))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.ONGOING, self.call.status)
        self.assertIsNotNone(self.call.connected_at)
        self.assertEqual(CallStatus.ONGOING, CallParticipant.objects.get(call=self.call, user=self.b).status)

    def test_caller_join_alone_does_not_connect_call(self, _bc, _cl, _cancel):
        handle_message_event(_evt('participant_joined', self.call.channel_name, self.a.id))
        self.call.refresh_from_db()
        self.assertIsNone(self.call.connected_at)
        self.assertEqual(CallStatus.RINGING, self.call.status)

    def test_join_does_not_resurrect_missed_or_rejected(self, _bc, _cl, _cancel):
        CallParticipant.objects.filter(call=self.call, user=self.b).update(status=CallStatus.MISSED)
        handle_message_event(_evt('participant_joined', self.call.channel_name, self.b.id))
        self.assertEqual(CallStatus.MISSED, CallParticipant.objects.get(call=self.call, user=self.b).status)

    def test_join_of_uninvited_identity_is_ignored(self, _bc, _cl, _cancel):
        stranger = _user('stranger')
        handle_message_event(_evt('participant_joined', self.call.channel_name, stranger.id))
        self.assertFalse(CallParticipant.objects.filter(call=self.call, user=stranger).exists())

    def test_egress_bot_identity_is_ignored(self, _bc, _cl, _cancel):
        handle_message_event(_evt('participant_joined', self.call.channel_name, 'EG_abc123'))
        self.assertEqual(2, CallParticipant.objects.filter(call=self.call).count())

    def test_join_after_terminal_is_ignored(self, _bc, _cl, _cancel):
        CallSession.objects.filter(id=self.call.id).update(status=CallStatus.CANCELLED)
        handle_message_event(_evt('participant_joined', self.call.channel_name, self.b.id))
        self.assertEqual(CallStatus.RINGING, CallParticipant.objects.get(call=self.call, user=self.b).status)

    def test_all_left_on_connected_call_ends_it(self, _bc, _cl, _cancel):
        call_state.accept_call(self.call.id, self.b.id)
        handle_message_event(_evt('participant_left', self.call.channel_name, self.b.id))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.ONGOING, self.call.status)  # caller abhi bhi live
        handle_message_event(_evt('participant_left', self.call.channel_name, self.a.id))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, self.call.status)

    def test_caller_disconnect_while_ringing_does_not_end_call(self, _bc, _cl, _cancel):
        handle_message_event(_evt('participant_left', self.call.channel_name, self.a.id))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.RINGING, self.call.status)

    def test_connection_aborted_behaves_like_left(self, _bc, _cl, _cancel):
        call_state.accept_call(self.call.id, self.b.id)
        handle_message_event(_evt('participant_connection_aborted', self.call.channel_name, self.b.id))
        self.assertEqual(CallStatus.ENDED, CallParticipant.objects.get(call=self.call, user=self.b).status)

    def test_room_finished_ends_call_and_clears_ghost_participants(self, _bc, _cl, _cancel):
        call_state.accept_call(self.call.id, self.b.id)  # b REST se ONGOING par LiveKit me kabhi nahi aaya
        handle_message_event(_evt('room_finished', self.call.channel_name))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.ENDED, self.call.status)
        self.assertFalse(CallParticipant.objects.filter(call=self.call, left_at__isnull=True).exists())

    def test_duplicate_events_are_idempotent(self, _bc, _cl, _cancel):
        ev = _evt('room_finished', self.call.channel_name)
        handle_message_event(ev)
        handle_message_event(ev)
        self.call.refresh_from_db()
        self.assertIn(self.call.status, call_state.TERMINAL_STATUSES)

    def test_non_call_room_and_unknown_room_ignored(self, _bc, _cl, _cancel):
        self.assertTrue(handle_message_event(_evt('room_finished', 'study_room_xyz')))
        self.assertTrue(handle_message_event(_evt('room_finished', 'call_doesnotexist')))
        self.call.refresh_from_db()
        self.assertEqual(CallStatus.RINGING, self.call.status)

    def test_unknown_event_returns_false(self, _bc, _cl, _cancel):
        self.assertFalse(handle_message_event(_evt('track_published', self.call.channel_name)))

    def test_handler_never_raises(self, _bc, _cl, _cancel):
        bad = SimpleNamespace(event='participant_joined', room=None, participant=None)
        handle_message_event(bad)  # koi exception bahar nahi aani chahiye

    @mock.patch('message.livekit_webhook._build_recording_url', return_value='https://cdn.example/rec.mp4')
    def test_egress_ended_sets_recording_url_and_stops_flag(self, _url, _bc, _cl, _cancel):
        CallSession.objects.filter(id=self.call.id).update(
            is_recording=True, recording_egress_id='EG_1', recording_output_path='rec/x.mp4')
        info = SimpleNamespace(egress_id='EG_1', file_results=[], status=0)
        handle_message_event(SimpleNamespace(event='egress_ended', egress_info=info))
        self.call.refresh_from_db()
        self.assertFalse(self.call.is_recording)
        self.assertEqual('https://cdn.example/rec.mp4', self.call.recording_url)

    def test_egress_for_unknown_id_ignored(self, _bc, _cl, _cancel):
        info = SimpleNamespace(egress_id='EG_other', file_results=[], status=0)
        self.assertTrue(handle_message_event(SimpleNamespace(event='egress_ended', egress_info=info)))


class WebhookViewTests(TestCase):
    URL = '/message/livekit/webhook/'

    def test_bad_signature_is_401(self):
        with mock.patch('message.livekit_webhook.verify_webhook_event', side_effect=WebhookVerificationError('bad')):
            resp = APIClient().post(self.URL, data=b'{}', content_type='application/webhook+json')
        self.assertEqual(401, resp.status_code)

    def test_not_configured_is_503(self):
        with mock.patch('message.livekit_webhook.verify_webhook_event', side_effect=RuntimeError('no key')):
            resp = APIClient().post(self.URL, data=b'{}', content_type='application/webhook+json')
        self.assertEqual(503, resp.status_code)

    def test_valid_event_is_200_without_user_auth(self):
        with mock.patch('message.livekit_webhook.verify_webhook_event', return_value=_evt('track_published')), \
             mock.patch('message.livekit_webhook.handle_message_event') as handler:
            resp = APIClient().post(self.URL, data=b'{}', content_type='application/webhook+json')
        self.assertEqual(200, resp.status_code)
        handler.assert_called_once()


# ======================================================================
# ADD PARTICIPANT
# ======================================================================
class AddParticipantTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.c, self.d = _user('a'), _user('b'), _user('c'), _user('d')
        self.conv = _group_conv(self.a, self.b, self.c)
        self.call = _call(self.conv, self.a, [self.b], status=CallStatus.ONGOING, group=True)
        CallParticipant.objects.filter(call=self.call, user=self.b).update(status=CallStatus.ONGOING)
        self.client = APIClient()
        self.client.force_authenticate(self.a)

    def _add(self, user_id, who=None):
        if who is not None:
            self.client.force_authenticate(who)
        return self.client.post(f'/message/calls/history/{self.call.id}/add-participant/',
                                {'user_id': str(user_id)} if user_id else {}, format='json')

    @_view_patches
    def test_member_can_be_added_and_gets_ring(self, _cancel, push, _tok, _cl, _a2s):
        resp = self._add(self.c.id)
        self.assertEqual(201, resp.status_code, resp.content)
        self.assertEqual(CallStatus.RINGING, CallParticipant.objects.get(call=self.call, user=self.c).status)
        self.assertEqual([self.c.id], list(push.call_args.kwargs['recipient_ids']))

    @_view_patches
    def test_user_id_required(self, *_m):
        self.assertEqual(400, self._add(None).status_code)

    @_view_patches
    def test_stranger_cannot_touch_call_404(self, *_m):
        self.assertEqual(404, self._add(self.c.id, who=self.d).status_code)

    @_view_patches
    def test_participant_who_is_only_ringing_cannot_add_403(self, *_m):
        # c invited par abhi uthaya nahi — usko dusron ko add karne ka haq nahi
        CallParticipant.objects.create(call=self.call, user=self.c, status=CallStatus.RINGING)
        self.assertEqual(403, self._add(self.d.id, who=self.c).status_code)

    @_view_patches
    def test_non_member_target_is_403(self, *_m):
        self.assertEqual(403, self._add(self.d.id).status_code)
        self.assertFalse(CallParticipant.objects.filter(call=self.call, user=self.d).exists())

    @_view_patches
    def test_member_who_left_conversation_is_403(self, *_m):
        from .models import ConversationParticipant
        ConversationParticipant.objects.filter(conversation=self.conv, user=self.c).update(left_at=timezone.now())
        self.assertEqual(403, self._add(self.c.id).status_code)

    @_view_patches
    def test_blocked_pair_is_403(self, *_m):
        BlockedUser.objects.create(blocker=self.c, blocked=self.a)
        self.assertEqual(403, self._add(self.c.id).status_code)

    @_view_patches
    def test_busy_target_is_409_and_not_rung(self, _cancel, push, *_m):
        _call(_dm(self.c, self.d), self.c, [self.d], status=CallStatus.ONGOING)
        resp = self._add(self.c.id)
        self.assertEqual(409, resp.status_code)
        self.assertEqual('callee_busy', resp.data['code'])
        push.assert_not_called()

    @override_settings(CALL_WAITING_ENABLED=True)
    @_view_patches
    def test_busy_target_with_call_waiting_rings_with_flag(self, _cancel, push, *_m):
        _call(_dm(self.c, self.d), self.c, [self.d], status=CallStatus.ONGOING)
        self.assertEqual(201, self._add(self.c.id).status_code)
        self.assertTrue(push.call_args.kwargs.get('call_waiting'))

    @_view_patches
    def test_already_ongoing_target_is_400(self, *_m):
        self.assertEqual(400, self._add(self.b.id).status_code)

    @_view_patches
    def test_missed_target_can_be_reinvited(self, _cancel, push, *_m):
        CallParticipant.objects.create(call=self.call, user=self.c, status=CallStatus.MISSED, left_at=timezone.now())
        self.assertEqual(201, self._add(self.c.id).status_code)
        cp = CallParticipant.objects.get(call=self.call, user=self.c)
        self.assertEqual(CallStatus.RINGING, cp.status)
        self.assertIsNone(cp.left_at)

    @_view_patches
    def test_ended_call_cannot_add_400(self, *_m):
        CallSession.objects.filter(id=self.call.id).update(status=CallStatus.ENDED)
        self.assertEqual(400, self._add(self.c.id).status_code)

    @_view_patches
    def test_adding_to_1to1_turns_it_into_group_call(self, *_m):
        conv = _group_conv(self.a, self.b, self.c)
        call = _call(conv, self.a, [self.b], status=CallStatus.ONGOING, group=False)
        CallParticipant.objects.filter(call=call, user=self.b).update(status=CallStatus.ONGOING)
        resp = self.client.post(f'/message/calls/history/{call.id}/add-participant/',
                                {'user_id': str(self.c.id)}, format='json')
        self.assertEqual(201, resp.status_code, resp.content)
        call.refresh_from_db()
        self.assertTrue(call.is_group_call)


# ======================================================================
# MISSED LIST + HISTORY VISIBILITY
# ======================================================================
class MissedListTests(TestCase):
    URL = '/message/calls/history/missed/'

    def setUp(self):
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')
        self.conv = _dm(self.a, self.b)
        self.client = APIClient()
        self.client.force_authenticate(self.b)

    def _ids(self, resp):
        data = resp.data['results'] if isinstance(resp.data, dict) and 'results' in resp.data else resp.data
        return [str(row['id']) for row in data]

    def test_missed_by_timeout_is_listed_for_callee_only(self):
        call = _call(self.conv, self.a, [self.b], started_ago=100)
        with mock.patch.object(tasks, 'get_channel_layer', return_value=None), \
             mock.patch('message.push_utils.send_call_cancelled_push'):
            tasks.sweep_stale_calls()
        self.assertEqual([str(call.id)], self._ids(self.client.get(self.URL)))
        self.client.force_authenticate(self.a)  # caller ko apni hi call "missed" nahi dikhni chahiye
        self.assertEqual([], self._ids(self.client.get(self.URL)))

    def test_caller_cancelled_call_shows_as_missed_for_callee(self):
        call = _call(self.conv, self.a, [self.b])
        call_state.end_call(call.id, self.a.id)
        self.assertEqual([str(call.id)], self._ids(self.client.get(self.URL)))

    def test_rejected_answered_and_ringing_not_listed(self):
        rejected = _call(self.conv, self.a, [self.b]); call_state.reject_call(rejected.id, self.b.id)
        answered = _call(self.conv, self.a, [self.b]); call_state.accept_call(answered.id, self.b.id)
        _call(self.conv, self.a, [self.b])  # abhi ringing
        self.assertEqual([], self._ids(self.client.get(self.URL)))

    def test_busy_line_missed_entry_is_listed(self):
        call = _call(self.conv, self.a, [], status=CallStatus.BUSY)
        CallParticipant.objects.create(call=call, user=self.b, status=CallStatus.MISSED, left_at=timezone.now())
        self.assertEqual([str(call.id)], self._ids(self.client.get(self.URL)))

    def test_since_filter(self):
        old = _call(self.conv, self.a, [self.b])
        call_state.end_call(old.id, self.a.id)
        CallSession.objects.filter(id=old.id).update(created_at=timezone.now() - timedelta(days=2))
        new = _call(self.conv, self.a, [self.b])
        call_state.end_call(new.id, self.a.id)
        since = (timezone.now() - timedelta(hours=1)).isoformat()
        resp = self.client.get(self.URL, {'since': since})
        self.assertEqual([str(new.id)], self._ids(resp))

    def test_invalid_since_is_400(self):
        self.assertEqual(400, self.client.get(self.URL, {'since': 'yesterday-ish'}).status_code)

    def test_newest_first(self):
        first = _call(self.conv, self.a, [self.b]); call_state.end_call(first.id, self.a.id)
        second = _call(self.conv, self.a, [self.b]); call_state.end_call(second.id, self.a.id)
        CallSession.objects.filter(id=first.id).update(created_at=timezone.now() - timedelta(minutes=5))
        self.assertEqual([str(second.id), str(first.id)], self._ids(self.client.get(self.URL)))

    def test_other_users_calls_never_leak(self):
        other_conv = _dm(self.a, self.c)
        call = _call(other_conv, self.a, [self.c]); call_state.end_call(call.id, self.a.id)
        self.assertEqual([], self._ids(self.client.get(self.URL)))

    def test_history_detail_404_for_stranger(self):
        call = _call(_dm(self.a, self.c), self.a, [self.c])
        self.assertEqual(404, self.client.get(f'/message/calls/history/{call.id}/').status_code)
