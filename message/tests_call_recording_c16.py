# message/tests_call_recording_c16.py — C16 recording consent (likha gaya, CHALAYA NAHI gaya)
#
#   start     : adults ok (+ consent log started / notice_shown + notice in broadcast), minor callee / minor host =>
#               409 `recording_minor_blocked` (egress start hi nahi hota), unknown DOB = adult, non-host 403
#   join      : minor accept recording ke dauran 409 (participant RINGING rehta hai), recording stop ke baad ho jata hai
#   notice    : GET / ack (idempotent, ek recording = ek ack), stranger 404, call me na hone wala 403,
#               refresh-token reply me `recording_notice`
#   webhook   : minor purane token se room me aaye to ENDED + room se remove
#
#   python manage.py test message.tests_call_recording_c16
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from . import call_recording_consent as consent
from . import call_state, livekit_webhook
from .models import CallParticipant, CallRecordingConsent, CallStatus
from .tests_call_state import _call, _dm, _user

Event = CallRecordingConsent.Event


def _dob(years):
    today = timezone.localdate()
    return today.replace(year=today.year - years, day=1) - timedelta(days=40)


def _aged(name, years):
    u = _user(name)
    u.date_of_birth = _dob(years) if years is not None else None
    u.save(update_fields=['date_of_birth'])
    return u


def _ongoing(conv, host, guest):
    call = _call(conv, host, [guest], status=CallStatus.ONGOING)
    CallParticipant.objects.filter(call=call, user=guest).update(status=CallStatus.ONGOING)
    return call


class _RecBase(TestCase):
    def setUp(self):
        self.sent = []
        patches = [
            mock.patch('message.views.calls.start_room_recording', return_value=('EG_1', 'rec/x.mp4')),
            mock.patch('message.views.calls.stop_room_recording', return_value='https://cdn.example/x.mp4'),
            mock.patch('message.views.calls.get_channel_layer'),
            mock.patch('message.views.calls.async_to_sync',
                       side_effect=lambda f: (lambda *a, **k: self.sent.append(a))),
        ]
        self.start_mock, self.stop_mock = patches[0].start(), patches[1].start()
        patches[2].start()
        patches[3].start()
        for p in patches:
            self.addCleanup(p.stop)

    def _rec(self, client_user, call, action):
        c = APIClient()
        c.force_authenticate(client_user)
        return c.post(f'/message/calls/{call.id}/recording/', {'action': action}, format='json')


class StartRuleTests(_RecBase):
    def test_adults_start_logs_and_broadcasts_notice(self):
        host, guest = _aged('h', 30), _aged('g', 28)
        call = _ongoing(_dm(host, guest), host, guest)
        resp = self._rec(host, call, 'start')
        self.assertEqual(200, resp.status_code)
        call.refresh_from_db()
        self.assertTrue(call.is_recording)
        events = set(CallRecordingConsent.objects.filter(call=call).values_list('event', flat=True))
        self.assertEqual({Event.STARTED, Event.NOTICE_SHOWN}, events)
        data = self.sent[-1][1]['data']   # group_send(group, {'type': ..., 'data': ...})
        self.assertEqual('recording_started', data['event'])
        self.assertTrue(data['sound'])
        self.assertEqual(consent.NOTICE_VERSION, data['notice']['version'])

    def test_unknown_dob_is_treated_like_adult(self):
        host, guest = _aged('h', 30), _aged('g', None)
        call = _ongoing(_dm(host, guest), host, guest)
        self.assertEqual(200, self._rec(host, call, 'start').status_code)

    def test_minor_participant_blocks_start(self):
        host, kid = _aged('h', 30), _aged('k', 15)
        call = _ongoing(_dm(host, kid), host, kid)
        resp = self._rec(host, call, 'start')
        self.assertEqual(409, resp.status_code)
        self.assertEqual(consent.MINOR_BLOCK_CODE, resp.data['code'])
        self.start_mock.assert_not_called()
        call.refresh_from_db()
        self.assertFalse(call.is_recording)
        self.assertTrue(CallRecordingConsent.objects.filter(call=call, event=Event.BLOCKED_MINOR_START).exists())

    def test_minor_host_blocks_start(self):
        kid, other = _aged('k', 16), _aged('o', 30)
        call = _ongoing(_dm(kid, other), kid, other)
        self.assertEqual(409, self._rec(kid, call, 'start').status_code)
        self.start_mock.assert_not_called()

    def test_minor_who_is_only_ringing_does_not_block_start(self):
        host, kid = _aged('h', 30), _aged('k', 15)
        call = _call(_dm(host, kid), host, [kid], status=CallStatus.ONGOING)   # kid abhi RINGING
        self.assertEqual(200, self._rec(host, call, 'start').status_code)

    def test_non_host_cannot_start(self):
        host, guest = _aged('h', 30), _aged('g', 28)
        call = _ongoing(_dm(host, guest), host, guest)
        self.assertEqual(403, self._rec(guest, call, 'start').status_code)

    def test_stop_is_logged(self):
        host, guest = _aged('h', 30), _aged('g', 28)
        call = _ongoing(_dm(host, guest), host, guest)
        self._rec(host, call, 'start')
        self.assertEqual(200, self._rec(host, call, 'stop').status_code)
        self.assertTrue(CallRecordingConsent.objects.filter(call=call, event=Event.STOPPED).exists())


class MinorJoinTests(_RecBase):
    def test_minor_cannot_accept_while_recording_then_can_after_stop(self):
        host, kid = _aged('h', 30), _aged('k', 15)
        call = _call(_dm(host, kid), host, [kid], status=CallStatus.ONGOING)   # kid RINGING
        self.assertEqual(200, self._rec(host, call, 'start').status_code)

        res = call_state.accept_call(call.id, kid.id)
        self.assertFalse(res.ok)
        self.assertEqual(409, res.http_status)
        self.assertEqual(consent.MINOR_BLOCK_CODE, res.code)
        self.assertEqual(CallStatus.RINGING, CallParticipant.objects.get(call=call, user=kid).status)
        self.assertTrue(CallRecordingConsent.objects.filter(call=call, event=Event.BLOCKED_MINOR_JOIN).exists())

        self._rec(host, call, 'stop')
        self.assertTrue(call_state.accept_call(call.id, kid.id).ok)

    def test_adult_can_accept_while_recording(self):
        host, guest = _aged('h', 30), _aged('g', 28)
        call = _call(_dm(host, guest), host, [guest], status=CallStatus.ONGOING)
        self._rec(host, call, 'start')
        self.assertTrue(call_state.accept_call(call.id, guest.id).ok)


class NoticeAckTests(_RecBase):
    def setUp(self):
        super().setUp()
        self.host, self.guest, self.stranger = _aged('h', 30), _aged('g', 28), _aged('s', 40)
        self.call = _ongoing(_dm(self.host, self.guest), self.host, self.guest)

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user)
        return c

    def _notice(self, user):
        return self._client(user).get(f'/message/calls/{self.call.id}/recording-notice/')

    def _ack(self, user):
        return self._client(user).post(f'/message/calls/{self.call.id}/recording-notice/ack/')

    def test_no_recording_means_nothing_to_show(self):
        data = self._notice(self.guest).data
        self.assertFalse(data['show'])
        self.assertFalse(data['ack_required'])

    def test_guest_must_ack_then_ack_is_idempotent(self):
        self._rec(self.host, self.call, 'start')
        data = self._notice(self.guest).data
        self.assertTrue(data['show'] and data['sound'] and data['ack_required'])
        self.assertFalse(data['acknowledged'])
        self.assertEqual(200, self._ack(self.guest).status_code)
        self.assertEqual(200, self._ack(self.guest).status_code)
        self.assertEqual(1, CallRecordingConsent.objects.filter(
            call=self.call, user=self.guest, event=Event.ACKNOWLEDGED).count())
        after = self._notice(self.guest).data
        self.assertTrue(after['acknowledged'])
        self.assertFalse(after['ack_required'])

    def test_host_never_needs_to_ack(self):
        self._rec(self.host, self.call, 'start')
        data = self._notice(self.host).data
        self.assertTrue(data['is_host'])
        self.assertFalse(data['ack_required'])

    def test_stranger_gets_404(self):
        self.assertEqual(404, self._notice(self.stranger).status_code)
        self.assertEqual(404, self._ack(self.stranger).status_code)

    def test_participant_who_left_cannot_ack(self):
        self._rec(self.host, self.call, 'start')
        CallParticipant.objects.filter(call=self.call, user=self.guest).update(
            status=CallStatus.ENDED, left_at=timezone.now())
        resp = self._ack(self.guest)
        self.assertEqual(403, resp.status_code)
        self.assertEqual('not_in_call', resp.data['code'])

    @mock.patch('message.views.calls.generate_livekit_token', return_value='tok')
    def test_refresh_token_carries_recording_notice(self, _tok):
        self._rec(self.host, self.call, 'start')
        resp = self._client(self.guest).post(f'/message/calls/{self.call.id}/refresh-token/')
        self.assertEqual(200, resp.status_code)
        self.assertTrue(resp.data['recording_notice']['show'])

    @mock.patch('message.views.calls.generate_livekit_token', return_value='tok')
    def test_refresh_token_without_recording_has_no_notice(self, _tok):
        resp = self._client(self.guest).post(f'/message/calls/{self.call.id}/refresh-token/')
        self.assertEqual(200, resp.status_code)
        self.assertIsNone(resp.data['recording_notice'])


class WebhookMinorGuardTests(_RecBase):
    def test_minor_joining_recorded_room_is_removed_and_not_marked_ongoing(self):
        host, kid = _aged('h', 30), _aged('k', 15)
        call = _call(_dm(host, kid), host, [kid], status=CallStatus.ONGOING)
        self._rec(host, call, 'start')
        event = SimpleNamespace(room=SimpleNamespace(name=call.channel_name),
                                participant=SimpleNamespace(identity=str(kid.id)))
        with mock.patch('message.livekit_utils.remove_participant') as remove:
            livekit_webhook._on_participant_joined(event)
        remove.assert_called_once_with(call.channel_name, str(kid.id))
        self.assertEqual(CallStatus.ENDED, CallParticipant.objects.get(call=call, user=kid).status)

    def test_adult_joining_recorded_room_is_ongoing(self):
        host, guest = _aged('h', 30), _aged('g', 28)
        call = _call(_dm(host, guest), host, [guest], status=CallStatus.ONGOING)
        self._rec(host, call, 'start')
        event = SimpleNamespace(room=SimpleNamespace(name=call.channel_name),
                                participant=SimpleNamespace(identity=str(guest.id)))
        with mock.patch('message.livekit_utils.remove_participant') as remove:
            livekit_webhook._on_participant_joined(event)
        remove.assert_not_called()
        self.assertEqual(CallStatus.ONGOING, CallParticipant.objects.get(call=call, user=guest).status)
