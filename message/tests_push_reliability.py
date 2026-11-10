# message/tests_push_reliability.py - N3 push reliability + token hygiene (likha gaya, CHALAYA NAHI gaya)
#
#   python manage.py test message.tests_push_reliability
#
# FCM / Celery mock hain.
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import push_channels, push_reliability as pr, push_utils
from .models import DeviceToken

User = get_user_model()

FLUTTER_IDS = {
    'chat_messages', 'announcements', 'calls', 'classes', 'tests', 'social', 'wallet', 'silent', 'notifications',
}


def _user(name):
    return User.objects.create_user(username=name, password='x', email=f'{name}@example.com')


class ChannelTests(SimpleTestCase):
    def test_channel_ids_match_flutter(self):
        self.assertEqual(FLUTTER_IDS, set(push_channels.ALL_CHANNELS))

    def test_unknown_type_falls_back_to_default(self):
        self.assertEqual(push_channels.CHANNEL_DEFAULT, push_channels.channel_for_notif_type('no_such_type_xyz'))
        self.assertEqual(push_channels.CHANNEL_DEFAULT, push_channels.channel_for_notif_type(None))


class ClassifyErrorTests(SimpleTestCase):
    def test_none_is_other(self):
        self.assertEqual(pr.OTHER, pr.classify_error(None))

    def test_unregistered_is_dead(self):
        from firebase_admin import messaging
        self.assertEqual(pr.DEAD, pr.classify_error(messaging.UnregisteredError('x')))

    def test_unavailable_is_transient(self):
        from firebase_admin import exceptions as fb
        self.assertEqual(pr.TRANSIENT, pr.classify_error(fb.UnavailableError('x')))

    def test_invalid_argument_only_dead_for_registration_token(self):
        from firebase_admin import exceptions as fb
        self.assertEqual(pr.DEAD, pr.classify_error(fb.InvalidArgumentError('The registration token is not valid')))
        self.assertEqual(pr.OTHER, pr.classify_error(fb.InvalidArgumentError('bad payload field')))


class DedupeAndRetryTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_fingerprint_needs_event_id(self):
        self.assertIsNone(pr.event_fingerprint({'type': 'chat_digest', 'count': 5}))
        self.assertEqual('x:message_id:7', pr.event_fingerprint({'type': 'x', 'message_id': 7}))

    def test_second_reserve_for_same_event_drops_token(self):
        fp = pr.event_fingerprint({'type': 'x', 'notification_id': 1})
        first, keys = pr.reserve_dedupe(['t1', 't2'], fp)
        self.assertEqual(['t1', 't2'], first)
        second, _ = pr.reserve_dedupe(['t1', 't3'], fp)
        self.assertEqual(['t3'], second)
        pr.release_dedupe(keys, ['t1'])
        third, _ = pr.reserve_dedupe(['t1'], fp)
        self.assertEqual(['t1'], third)

    def test_no_fingerprint_passes_all(self):
        out, keys = pr.reserve_dedupe(['a', 'a2'], None)
        self.assertEqual(['a', 'a2'], out)
        self.assertEqual({}, keys)

    def test_backoff_and_ttl(self):
        self.assertEqual([5, 20, 80], [pr.retry_countdown(i) for i in range(3)])
        self.assertTrue(pr.should_retry(0, None))
        self.assertFalse(pr.should_retry(pr.MAX_RETRIES, None))
        self.assertFalse(pr.should_retry(0, 3))      # call ring TTL retry se pehle khatam
        self.assertTrue(pr.should_retry(0, 60))


class RegisterTokenTests(TestCase):
    def test_rotation_same_device_replaces_old_row(self):
        u = _user('rot')
        meta = pr.clean_device_meta({'device_id': 'dev-abcdef12', 'app_version': '1.2.3'})
        pr.register_device_token(user=u, token='tok-old', platform='android', meta=meta)
        pr.register_device_token(user=u, token='tok-new', platform='android', meta=meta)
        self.assertEqual(['tok-new'], list(DeviceToken.objects.filter(user=u).values_list('token', flat=True)))

    def test_cap_keeps_newest_ten(self):
        u = _user('cap')
        for i in range(12):
            pr.register_device_token(user=u, token=f'tok-{i}', platform='android', meta={})
        self.assertEqual(pr.MAX_TOKENS_PER_USER, DeviceToken.objects.filter(user=u).count())
        self.assertTrue(DeviceToken.objects.filter(token='tok-11').exists())

    def test_token_moves_to_new_user(self):
        a, b = _user('ua'), _user('ub')
        pr.register_device_token(user=a, token='shared', platform='android', meta={})
        _, created, moved_from = pr.register_device_token(user=b, token='shared', platform='android', meta={})
        self.assertFalse(created)
        self.assertEqual(a.id, moved_from.id)
        self.assertEqual(b.id, DeviceToken.objects.get(token='shared').user_id)

    def test_blank_meta_keeps_old_values(self):
        u = _user('meta')
        pr.register_device_token(user=u, token='t', platform='android', meta={'app_version': '2.0'})
        pr.register_device_token(user=u, token='t', platform='android', meta={'app_version': ''})
        self.assertEqual('2.0', DeviceToken.objects.get(token='t').app_version)

    def test_clean_device_meta_rejects_bad_device_id(self):
        self.assertEqual('', pr.clean_device_meta({'device_id': 'a b!'})['device_id'])
        self.assertEqual(32, len(pr.clean_device_meta({'app_version': 'x' * 100})['app_version']))

    def test_api_register_stores_meta(self):
        u = _user('api')
        c = APIClient()
        c.force_authenticate(u)
        r = c.post('/message/device-token/', {
            'token': 'api-tok', 'platform': 'android', 'device_id': 'dev-12345678',
            'app_version': '1.4.2', 'os_version': '14', 'device_name': 'Moto g34',
        }, format='json')
        self.assertEqual(200, r.status_code)
        row = DeviceToken.objects.get(token='api-tok')
        self.assertEqual(('dev-12345678', '1.4.2', '14', 'Moto g34'),
                         (row.device_id, row.app_version, row.os_version, row.device_name))


class SessionRevokeTests(TestCase):
    def test_revoke_by_session_jti(self):
        u = _user('rev')
        pr.register_device_token(user=u, token='r1', platform='android', meta={}, session_jti='jti-1')
        pr.register_device_token(user=u, token='r2', platform='android', meta={}, session_jti='jti-2')
        self.assertEqual(1, pr.revoke_tokens_for_session('jti-1'))
        self.assertEqual(['r2'], list(DeviceToken.objects.values_list('token', flat=True)))


class RecordAndCleanupTests(TestCase):
    def test_record_send_result(self):
        u = _user('rec')
        for t in ('ok', 'dead', 'fail'):
            DeviceToken.objects.create(user=u, token=t)
        DeviceToken.objects.filter(token='ok').update(failure_count=3)
        pr.record_send_result(['ok'], ['dead'], ['fail'], {'fail': 'UNAVAILABLE'})
        self.assertFalse(DeviceToken.objects.filter(token='dead').exists())
        self.assertEqual(0, DeviceToken.objects.get(token='ok').failure_count)
        f = DeviceToken.objects.get(token='fail')
        self.assertEqual((1, 'UNAVAILABLE'), (f.failure_count, f.last_failure_code))

    def test_cleanup_stale_and_failing(self):
        u = _user('cln')
        now = timezone.now()
        DeviceToken.objects.create(user=u, token='fresh')
        DeviceToken.objects.create(user=u, token='old')
        DeviceToken.objects.create(user=u, token='failing', failure_count=pr.MAX_FAILURES)
        DeviceToken.objects.filter(token='old').update(last_seen_at=now - timedelta(days=pr.STALE_DAYS + 1))
        DeviceToken.objects.filter(token='failing').update(last_seen_at=now - timedelta(days=pr.FAILED_GRACE_DAYS + 1))
        out = pr.cleanup_stale_tokens(now)
        self.assertEqual(1, out['stale'])
        self.assertEqual(1, out['failing'])
        self.assertEqual(['fresh'], list(DeviceToken.objects.values_list('token', flat=True)))

    def test_cleanup_over_cap(self):
        u = _user('big')
        for i in range(pr.MAX_TOKENS_PER_USER + 3):
            DeviceToken.objects.create(user=u, token=f'b{i}')
        out = pr.cleanup_stale_tokens()
        self.assertEqual(3, out['over_cap'])


class SendMulticastTests(TestCase):
    """_send_multicast ka behaviour - firebase_admin messaging + Celery mock."""

    def setUp(self):
        cache.clear()
        self.u = _user('snd')
        for t in ('good', 'dead', 'flaky'):
            DeviceToken.objects.create(user=self.u, token=t)

    def _resp(self, results):
        return SimpleNamespace(
            failure_count=sum(1 for r in results if not r.success), responses=results,
        )

    def test_dead_deleted_transient_retried_ok_untouched(self):
        from firebase_admin import exceptions as fb, messaging
        results = [
            SimpleNamespace(success=True, exception=None),
            SimpleNamespace(success=False, exception=messaging.UnregisteredError('gone')),
            SimpleNamespace(success=False, exception=fb.UnavailableError('503')),
        ]
        with mock.patch.object(push_utils, '_ensure_firebase_initialized'), \
                mock.patch.object(push_utils.messaging, 'send_each_for_multicast', return_value=self._resp(results)), \
                mock.patch('message.tasks.retry_push.apply_async') as retry:
            push_utils._send_multicast(['good', 'dead', 'flaky'], data={'type': 'x', 'notification_id': 5})
        self.assertFalse(DeviceToken.objects.filter(token='dead').exists())
        self.assertTrue(DeviceToken.objects.filter(token='good').exists())
        retry.assert_called_once()
        self.assertEqual(['flaky'], retry.call_args.kwargs['kwargs']['tokens'])
        self.assertEqual(1, retry.call_args.kwargs['kwargs']['attempt'])

    def test_same_event_not_sent_twice(self):
        results = [SimpleNamespace(success=True, exception=None)]
        with mock.patch.object(push_utils, '_ensure_firebase_initialized'), \
                mock.patch.object(push_utils.messaging, 'send_each_for_multicast', return_value=self._resp(results)) as send:
            push_utils._send_multicast(['good'], data={'type': 'x', 'notification_id': 9})
            push_utils._send_multicast(['good'], data={'type': 'x', 'notification_id': 9})
        self.assertEqual(1, send.call_count)

    def test_retry_exhausted_counts_failure(self):
        from firebase_admin import exceptions as fb
        results = [SimpleNamespace(success=False, exception=fb.UnavailableError('503'))]
        with mock.patch.object(push_utils, '_ensure_firebase_initialized'), \
                mock.patch.object(push_utils.messaging, 'send_each_for_multicast', return_value=self._resp(results)), \
                mock.patch('message.tasks.retry_push.apply_async') as retry:
            push_utils._send_multicast(['flaky'], data={'type': 'x'}, retry_attempt=pr.MAX_RETRIES)
        retry.assert_not_called()
        self.assertEqual(1, DeviceToken.objects.get(token='flaky').failure_count)


class DeviceTokenApiValidationTests(TestCase):
    """N3b - bad input is a 400 (never a DB error); DELETE works without a token."""

    URL = '/message/device-token/'

    def setUp(self):
        self.user = _user('n3b_user')
        self.other = _user('n3b_other')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_rejects_overlong_or_dirty_or_non_string_token(self):
        for bad in ('x' * 256, 'has space', 'tab\tchar', 12345, ['a'], {'a': 1}):
            r = self.client.post(self.URL, {'token': bad}, format='json')
            self.assertEqual(r.status_code, 400, repr(bad))
        self.assertFalse(DeviceToken.objects.filter(user=self.user).exists())

    def test_accepts_a_real_looking_fcm_token(self):
        tok = 'dXy1:APA91bF-_abc.DEF' + 'q' * 120
        self.assertEqual(self.client.post(self.URL, {'token': tok, 'platform': 'IOS'}, format='json').status_code, 200)
        self.assertEqual(DeviceToken.objects.get(token=tok).platform, 'ios')

    def test_unknown_platform_is_400_missing_is_android(self):
        self.assertEqual(self.client.post(self.URL, {'token': 'tok-1', 'platform': 'blackberry'}, format='json').status_code, 400)
        self.assertEqual(self.client.post(self.URL, {'token': 'tok-2'}, format='json').status_code, 200)
        self.assertEqual(DeviceToken.objects.get(token='tok-2').platform, 'android')

    def test_delete_with_token_only_touches_own_row(self):
        DeviceToken.objects.create(user=self.user, token='mine-1')
        DeviceToken.objects.create(user=self.other, token='theirs-1')
        self.client.delete(self.URL, {'token': 'theirs-1'}, format='json')
        self.assertTrue(DeviceToken.objects.filter(token='theirs-1').exists())
        self.client.delete(self.URL, {'token': 'mine-1'}, format='json')
        self.assertFalse(DeviceToken.objects.filter(token='mine-1').exists())

    def test_delete_without_token_revokes_this_device_only(self):
        DeviceToken.objects.create(user=self.user, token='a-1', device_id='device-aaaaaaaa')
        DeviceToken.objects.create(user=self.user, token='b-1', device_id='device-bbbbbbbb')
        DeviceToken.objects.create(user=self.other, token='o-1', device_id='device-aaaaaaaa')
        r = self.client.delete(self.URL, {'device_id': 'device-aaaaaaaa'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertFalse(DeviceToken.objects.filter(token='a-1').exists())
        self.assertTrue(DeviceToken.objects.filter(token='b-1').exists())
        self.assertTrue(DeviceToken.objects.filter(token='o-1').exists())  # another user's row is never touched

    def test_delete_without_anything_is_a_harmless_noop(self):
        DeviceToken.objects.create(user=self.user, token='keep-1')
        self.assertEqual(self.client.delete(self.URL, {}, format='json').status_code, 200)
        self.assertTrue(DeviceToken.objects.filter(token='keep-1').exists())


class RevokeForRequestTests(TestCase):
    def test_session_beats_device_and_is_scoped_to_user(self):
        u, o = _user('rr_u'), _user('rr_o')
        DeviceToken.objects.create(user=u, token='s-1', session_jti='jti-1', device_id='device-11111111')
        DeviceToken.objects.create(user=u, token='s-2', session_jti='jti-2', device_id='device-11111111')
        DeviceToken.objects.create(user=o, token='s-3', session_jti='jti-1')
        self.assertEqual(pr.revoke_tokens_for_request(u, session_jti='jti-1', device_id='device-11111111'), 1)
        self.assertTrue(DeviceToken.objects.filter(token='s-2').exists())  # device_id is only the fallback
        self.assertTrue(DeviceToken.objects.filter(token='s-3').exists())


class TokenValidatorTests(SimpleTestCase):
    def test_valid_fcm_token(self):
        self.assertTrue(pr.valid_fcm_token('abc:DEF-1_2.3'))
        self.assertFalse(pr.valid_fcm_token(''))
        self.assertFalse(pr.valid_fcm_token('a b'))
        self.assertFalse(pr.valid_fcm_token('x' * 256))
        self.assertFalse(pr.valid_fcm_token(None))

    def test_clean_platform(self):
        self.assertEqual(pr.clean_platform(None), 'android')
        self.assertEqual(pr.clean_platform(' WEB '), 'web')
        self.assertIsNone(pr.clean_platform('symbian'))

