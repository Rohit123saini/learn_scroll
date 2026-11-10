# message/tests_call_push.py — C5 (likha gaya, CHALAYA NAHI gaya)
#
# Firebase / APNs dono mock hain; koi network nahi. Chalane ke liye:
#   python manage.py test message.tests_call_push
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from . import push_utils, voip_push
from .models import DeviceToken

User = get_user_model()


def _user(name):
    return User.objects.create_user(username=name, password='x', email=f'{name}@example.com')


class CallPushRoutingTests(TestCase):
    def setUp(self):
        self.android = _user('and_user')
        self.ios_voip = _user('ios_voip_user')
        self.ios_plain = _user('ios_plain_user')
        DeviceToken.objects.create(user=self.android, token='fcm-android', platform='android')
        DeviceToken.objects.create(user=self.ios_voip, token='fcm-ios-1', platform='ios', voip_token='voip-1')
        DeviceToken.objects.create(user=self.ios_plain, token='fcm-ios-2', platform='ios')
        self.ids = [self.android.id, self.ios_voip.id, self.ios_plain.id]

    def _send(self, voip_delivered):
        with mock.patch('message.voip_push.send_voip_call_push', return_value=voip_delivered) as voip, \
             mock.patch.object(push_utils, '_send_multicast') as fcm, \
             mock.patch.object(push_utils, 'create_notification'):
            push_utils.send_incoming_call_push(
                self.ids, 'Asha', 'audio', 'c0ffee00-0000-0000-0000-000000000001', 'conv1', 'chan1')
        return voip, fcm

    def test_ios_voip_device_skips_fcm_when_voip_delivered(self):
        voip, fcm = self._send({'fcm-ios-1'})
        voip.assert_called_once()
        self.assertEqual([('fcm-ios-1', 'voip-1')], list(voip.call_args.args[0]))
        tokens = fcm.call_args.args[0]
        self.assertCountEqual(['fcm-android', 'fcm-ios-2'], tokens)

    def test_voip_failure_falls_back_to_fcm(self):
        _voip, fcm = self._send(set())
        self.assertCountEqual(['fcm-android', 'fcm-ios-1', 'fcm-ios-2'], fcm.call_args.args[0])

    def test_fcm_call_push_has_ttl_and_collapse_key(self):
        _voip, fcm = self._send(set())
        kw = fcm.call_args.kwargs
        self.assertEqual(30, kw['ttl_seconds'])
        self.assertEqual('call_c0ffee00-0000-0000-0000-000000000001', kw['collapse_key'])
        self.assertIsNone(kw['notification'])  # data-only rehna chahiye

    @override_settings(CALL_PUSH_TTL_SECONDS=20)
    def test_ttl_follows_setting(self):
        _voip, fcm = self._send(set())
        self.assertEqual(20, fcm.call_args.kwargs['ttl_seconds'])

    def test_cancel_push_uses_same_collapse_key_and_never_voip(self):
        with mock.patch('message.voip_push.send_voip_call_push') as voip, \
             mock.patch.object(push_utils, '_send_multicast') as fcm:
            push_utils.send_call_cancelled_push(self.ids, 'c0ffee00-0000-0000-0000-000000000001', 'conv1')
        voip.assert_not_called()
        self.assertEqual('call_c0ffee00-0000-0000-0000-000000000001', fcm.call_args.kwargs['collapse_key'])


class MulticastTtlTests(TestCase):
    def test_ttl_and_collapse_reach_android_and_apns(self):
        with mock.patch.object(push_utils, '_ensure_firebase_initialized'), \
             mock.patch.object(push_utils.messaging, 'send_each_for_multicast') as send:
            send.return_value = mock.Mock(failure_count=0, responses=[])
            push_utils._send_multicast(['t1'], data={'a': 1}, ttl_seconds=30, collapse_key='call_x')
        msg = send.call_args.args[0]
        self.assertEqual(timedelta(seconds=30), msg.android.ttl)
        self.assertEqual('call_x', msg.android.collapse_key)
        self.assertEqual('10', msg.apns.headers['apns-priority'])
        self.assertEqual('call_x', msg.apns.headers['apns-collapse-id'])
        self.assertIn('apns-expiration', msg.apns.headers)

    def test_no_ttl_keeps_old_behaviour(self):
        with mock.patch.object(push_utils, '_ensure_firebase_initialized'), \
             mock.patch.object(push_utils.messaging, 'send_each_for_multicast') as send:
            send.return_value = mock.Mock(failure_count=0, responses=[])
            push_utils._send_multicast(['t1'], data={'a': 1})
        msg = send.call_args.args[0]
        self.assertIsNone(msg.android.ttl)
        self.assertNotIn('apns-expiration', msg.apns.headers)


class VoipPushTests(TestCase):
    def test_not_configured_returns_empty_set(self):
        with mock.patch.object(voip_push, 'is_configured', return_value=False):
            out = voip_push.send_voip_call_push(
                [('f', 'v')], call_id='id', caller_name='A', call_type='audio',
                conversation_id='c', channel_name='ch')
        self.assertEqual(set(), out)

    def test_dead_token_is_cleared_and_not_delivered(self):
        u = _user('dead_tok')
        DeviceToken.objects.create(user=u, token='fcm-d', platform='ios', voip_token='voip-dead')
        with mock.patch.object(voip_push, 'is_configured', return_value=True), \
             mock.patch.object(voip_push, '_send_one', return_value=(False, True)):
            out = voip_push.send_voip_call_push(
                [('fcm-d', 'voip-dead')], call_id='id', caller_name='A', call_type='video',
                conversation_id='c', channel_name='ch')
        self.assertEqual(set(), out)
        self.assertIsNone(DeviceToken.objects.get(token='fcm-d').voip_token)

    def test_success_returns_fcm_token(self):
        with mock.patch.object(voip_push, 'is_configured', return_value=True), \
             mock.patch.object(voip_push, '_send_one', return_value=(True, False)):
            out = voip_push.send_voip_call_push(
                [('fcm-ok', 'voip-ok')], call_id='id', caller_name='A', call_type='audio',
                conversation_id='c', channel_name='ch')
        self.assertEqual({'fcm-ok'}, out)

    def test_payload_has_keys_the_appdelegate_reads(self):
        p = voip_push._build_payload(
            call_id='abc', caller_name='Asha', call_type='video', conversation_id='c1',
            channel_name='ch', caller_avatar=None)
        self.assertEqual('abc', p['id'])
        self.assertTrue(p['isVideo'])
        self.assertEqual('abc', p['extra']['call_id'])
        self.assertEqual('video', p['extra']['call_type'])


class DeviceTokenVoipRegistrationTests(TestCase):
    def setUp(self):
        self.user = _user('reg_user')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, **body):
        return self.client.post('/message/device-token/', body, format='json')

    def test_ios_registers_voip_token(self):
        self.assertEqual(200, self._post(token='f1', platform='ios', voip_token='v1').status_code)
        self.assertEqual('v1', DeviceToken.objects.get(token='f1').voip_token)

    def test_android_ignores_voip_token(self):
        self._post(token='f2', platform='android', voip_token='v2')
        self.assertIsNone(DeviceToken.objects.get(token='f2').voip_token)

    def test_missing_key_does_not_wipe_existing_voip_token(self):
        self._post(token='f3', platform='ios', voip_token='v3')
        self._post(token='f3', platform='ios')
        self.assertEqual('v3', DeviceToken.objects.get(token='f3').voip_token)

    def test_empty_string_clears_voip_token(self):
        self._post(token='f4', platform='ios', voip_token='v4')
        self._post(token='f4', platform='ios', voip_token='')
        self.assertIsNone(DeviceToken.objects.get(token='f4').voip_token)

    def test_voip_token_moves_off_stale_row(self):
        self._post(token='old-fcm', platform='ios', voip_token='same-voip')
        self._post(token='new-fcm', platform='ios', voip_token='same-voip')
        self.assertIsNone(DeviceToken.objects.get(token='old-fcm').voip_token)
        self.assertEqual('same-voip', DeviceToken.objects.get(token='new-fcm').voip_token)
