# message/tests_call_ops.py — X1 call observability (likha gaya, CHALAYA NAHI gaya)
#
# call_ops (counters, SLIs, alert, Prometheus text) + views (CallTelemetryView, CallOpsAPIView) + initiate/ring hooks.
# Cache har test se pehle saaf hota hai (counters cache buckets me rehte hain).
#
#   python manage.py test message.tests_call_ops
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import call_ops, call_state, tasks
from .models import CallStatus
from .tests_call_state import _call, _dm, _user


def _summary(minutes=10):
    return call_ops.summarize_last(minutes)


class CallOpsCounterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        with call_ops._local_lock:
            call_ops._local.clear()

    # ---- connect ----
    def test_connect_ok_and_fail_make_success_pct(self):
        for i in range(19):
            self.assertTrue(call_ops.record_connect(f'c{i}', 1, ok=True, setup_ms=1500))
        self.assertTrue(call_ops.record_connect('cx', 1, ok=False, reason='ice_failed'))
        c = _summary()['connect']
        self.assertEqual(20, c['attempts'])
        self.assertEqual(95.0, c['success_pct'])
        self.assertEqual(1, c['failure_reasons']['ice_failed'])

    def test_connect_counted_once_per_call_and_user(self):
        self.assertTrue(call_ops.record_connect('c1', 7, ok=True, setup_ms=900))
        self.assertFalse(call_ops.record_connect('c1', 7, ok=False, reason='timeout'))   # retry / double send
        self.assertTrue(call_ops.record_connect('c1', 8, ok=True, setup_ms=900))          # other participant counts
        c = _summary()['connect']
        self.assertEqual(2, c['ok'])
        self.assertEqual(0, c['failed'])

    def test_unknown_reason_becomes_other_and_free_text_never_stored(self):
        call_ops.record_connect('c1', 1, ok=False, reason='my password is hunter2')
        call_ops.record_connect('c2', 1, ok=False, reason=None)
        call_ops.record_connect('c3', 1, ok=False, reason='  TIMEOUT ')
        reasons = _summary()['connect']['failure_reasons']
        self.assertEqual(2, reasons['other'])
        self.assertEqual(1, reasons['timeout'])
        self.assertEqual(set(call_ops.FAIL_REASONS), set(reasons))

    def test_setup_histogram_and_quantiles(self):
        for i, ms in enumerate([400, 800, 1500, 2500, 4000, 9000, 'junk', -5, 10 ** 9]):
            call_ops.record_connect(f'c{i}', 1, ok=True, setup_ms=ms)
        s = _summary()['setup_ms']
        self.assertEqual(8, s['samples'])             # 'junk' has no histogram sample; -5 -> 0 (<=500); huge -> clamped 120000 (+Inf)
        self.assertEqual(1, s['histogram']['+Inf'])
        self.assertEqual(2, s['histogram']['500'])    # 400 and the clamped -5
        self.assertIsNotNone(s['avg'])
        self.assertEqual(2000, s['p50'])              # 4th of 8 samples falls in the <=2000 bucket
        self.assertIsNone(s['p95'])                   # 95th percentile is in the +Inf bucket
        self.assertEqual(9, _summary()['connect']['ok'])   # junk setup_ms still counts as a successful connect

    def test_no_attempts_means_100_pct_and_no_breach(self):
        s = _summary()
        self.assertEqual(100.0, s['connect']['success_pct'])
        self.assertEqual([], call_ops.breaches(s))

    # ---- ring ----
    def test_ring_outcome_once_per_call_and_validates_name(self):
        self.assertTrue(call_ops.note_ring_outcome('answered', call_id='r1', ring_seconds=4.2))
        self.assertFalse(call_ops.note_ring_outcome('missed', call_id='r1'))   # race: sweeper after accept
        self.assertFalse(call_ops.note_ring_outcome('exploded', call_id='r2'))
        o = _summary()['ring']['outcomes']
        self.assertEqual(1, o['answered'])
        self.assertEqual(0, o['missed'])

    def test_answer_rate_excludes_busy(self):
        for i, out in enumerate(['answered', 'answered', 'missed', 'rejected', 'busy', 'busy']):
            call_ops.note_ring_outcome(out, call_id=f'r{i}', ring_seconds=5 if out == 'answered' else None)
        ring = _summary()['ring']
        self.assertEqual(50.0, ring['answer_rate_pct'])   # 2 / (2+1+1+0)
        self.assertEqual(2, ring['outcomes']['busy'])
        self.assertEqual(2, ring['to_answer_s']['samples'])
        self.assertEqual(5.0, ring['to_answer_s']['avg'])

    def test_answer_rate_is_none_without_rings(self):
        self.assertIsNone(_summary()['ring']['answer_rate_pct'])

    def test_note_call_result_maps_codes(self):
        now = timezone.now()
        mk = lambda code, status, **kw: SimpleNamespace(   # noqa: E731
            changed=kw.pop('changed', True), code=code,
            call=SimpleNamespace(id=f'{code}-{status}-{kw.get("n", 0)}', status=status, started_at=now - timedelta(seconds=7)))
        call_ops.note_call_result(mk('accepted', 'ongoing'))
        call_ops.note_call_result(mk('rejected', 'rejected'))
        call_ops.note_call_result(mk('rejected', 'ringing', n=1))     # group: one callee declined, call still ringing
        call_ops.note_call_result(mk('cancelled', 'cancelled'))
        call_ops.note_call_result(mk('accepted', 'ongoing', changed=False, n=2))   # no-op repeat
        call_ops.note_call_result(None)
        o = _summary()['ring']['outcomes']
        self.assertEqual({'answered': 1, 'rejected': 1, 'cancelled': 1, 'missed': 0, 'busy': 0}, o)
        self.assertEqual(1, _summary()['ring']['to_answer_s']['samples'])

    def test_initiated_split_group_direct(self):
        call_ops.note_initiated(is_group=False)
        call_ops.note_initiated(is_group=True)
        s = _summary()
        self.assertEqual(2, s['initiated'])
        self.assertEqual(1, s['initiated_group'])

    # ---- reconnect ----
    def test_summary_counts_reconnects_and_clamps(self):
        self.assertTrue(call_ops.record_summary('c1', 1, reconnects=3, reconnect_ok=2, reconnect_fail=1))
        self.assertFalse(call_ops.record_summary('c1', 1, reconnects=9))     # duplicate
        self.assertTrue(call_ops.record_summary('c2', 1, reconnects=10 ** 6, reconnect_ok='x', reconnect_fail=-4))
        r = _summary()['reconnect']
        self.assertEqual(2, r['calls_reported'])
        self.assertEqual(2, r['calls_with_reconnect'])
        self.assertEqual(53, r['events'])      # 3 + clamp(50)
        self.assertEqual(2, r['recovered'])
        self.assertEqual(1, r['lost'])
        self.assertAlmostEqual(66.67, r['recovered_pct'], places=2)

    # ---- breaches + alert ----
    def _fill(self, ok, fail, reason='ice_failed'):
        n = 0
        for _ in range(ok):
            n += 1
            call_ops.record_connect(f'ok{n}', n, ok=True, setup_ms=1000)
        for _ in range(fail):
            n += 1
            call_ops.record_connect(f'bad{n}', n, ok=False, reason=reason)

    def test_breach_lists_top_reasons(self):
        self._fill(ok=18, fail=7)     # 72 %
        problems = call_ops.breaches(_summary())
        self.assertEqual(1, len(problems))
        self.assertIn('connect-success 72.0% < 95.0%', problems[0])
        self.assertIn('ice_failed=7', problems[0])

    def test_alert_fires_once_enough_attempts(self):
        self._fill(ok=10, fail=15)
        with mock.patch.object(call_ops, '_sentry') as sentry, self.assertLogs(call_ops.logger, 'ERROR') as logs:
            summary = call_ops.check_and_alert()
        self.assertEqual(1, len(summary['problems']))
        sentry.assert_called_once()
        self.assertIn('CALL SLO ALERT', logs.output[0])

    def test_alert_silent_below_min_attempts_or_when_healthy(self):
        self._fill(ok=2, fail=5)      # 7 attempts < 20
        with mock.patch.object(call_ops, '_sentry') as sentry:
            self.assertEqual([], call_ops.check_and_alert()['problems'])
        sentry.assert_not_called()
        cache.clear()
        self._fill(ok=40, fail=1)     # 97.56 %
        with mock.patch.object(call_ops, '_sentry') as sentry:
            self.assertEqual([], call_ops.check_and_alert()['problems'])
        sentry.assert_not_called()

    @override_settings(CALL_SLO={'connect_success_pct': 99.0, 'alert_min_attempts': 5})
    def test_slo_is_configurable(self):
        self._fill(ok=9, fail=1)      # 90 % < 99 %, 10 attempts >= 5
        with mock.patch.object(call_ops, '_sentry'), self.assertLogs(call_ops.logger, 'ERROR'):
            self.assertEqual(1, len(call_ops.check_and_alert()['problems']))

    # ---- prometheus ----
    def test_prometheus_text_has_series(self):
        self._fill(ok=1, fail=1)
        call_ops.note_ring_outcome('missed', call_id='p1')
        text = call_ops.prometheus_text()
        self.assertIn('# TYPE learnscroll_call_connect_success_pct gauge', text)
        self.assertIn('learnscroll_call_connect_success_pct{window="5m"} 50.0', text)
        self.assertIn('learnscroll_call_connect_failures{reason="ice_failed",window="1h"} 1', text)
        self.assertIn('learnscroll_call_ring_outcomes{outcome="missed",window="24h"} 1', text)
        self.assertTrue(text.endswith('\n'))

    def test_prometheus_text_is_valid_on_empty_data(self):
        text = call_ops.prometheus_text()
        self.assertNotIn('connect_success_pct', text)     # no attempts => no misleading 0 / 100
        self.assertIn('learnscroll_call_initiated{window="5m"} 0', text)

    # ---- never raises ----
    def test_cache_down_falls_back_to_process_local_and_never_raises(self):
        dead = mock.Mock()
        dead.add.side_effect = dead.incr.side_effect = dead.get_many.side_effect = RuntimeError('redis down')
        with mock.patch.object(call_ops, 'cache', dead):
            self.assertTrue(call_ops.record_connect('c1', 1, ok=True, setup_ms=500))   # _once -> True when cache down
            c = _summary()['connect']
        self.assertEqual(1, c['ok'])


class CallOpsTaskTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_slo_watch_task_returns_numbers(self):
        out = tasks.call_slo_watch()
        self.assertEqual({'attempts': 0, 'success_pct': 100.0, 'problems': []}, out)


class CallTelemetryViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.a, self.b, self.c = _user('a'), _user('b'), _user('c')
        self.call = _call(_dm(self.a, self.b), self.a, [self.b])
        self.client = APIClient()
        self.client.force_authenticate(self.b)
        self.url = f'/message/calls/{self.call.id}/telemetry/'

    def test_participant_connect_ok(self):
        resp = self.client.post(self.url, {'kind': 'connect', 'ok': True, 'setup_ms': 1840}, format='json')
        self.assertEqual(200, resp.status_code)
        self.assertEqual({'counted': True}, resp.json())
        self.assertEqual(1, _summary()['connect']['ok'])

    def test_second_connect_is_not_counted(self):
        self.client.post(self.url, {'kind': 'connect', 'ok': True, 'setup_ms': 100}, format='json')
        resp = self.client.post(self.url, {'kind': 'connect', 'ok': False, 'reason': 'timeout'}, format='json')
        self.assertEqual({'counted': False}, resp.json())
        self.assertEqual(0, _summary()['connect']['failed'])

    def test_connect_fail_with_reason(self):
        self.client.post(self.url, {'kind': 'connect', 'ok': False, 'reason': 'signal_ws'}, format='json')
        self.assertEqual(1, _summary()['connect']['failure_reasons']['signal_ws'])

    def test_ok_as_string_false_is_failure(self):
        self.client.post(self.url, {'kind': 'connect', 'ok': 'false', 'reason': 'other'}, format='json')
        self.assertEqual(1, _summary()['connect']['failed'])

    def test_summary_kind(self):
        resp = self.client.post(self.url, {'kind': 'summary', 'reconnects': 2, 'reconnect_ok': 1, 'reconnect_fail': 1}, format='json')
        self.assertEqual({'counted': True}, resp.json())
        self.assertEqual(2, _summary()['reconnect']['events'])

    def test_invalid_kind_400(self):
        resp = self.client.post(self.url, {'kind': 'hack'}, format='json')
        self.assertEqual(400, resp.status_code)
        self.assertEqual('invalid_kind', resp.json()['code'])

    def test_stranger_403_and_nothing_counted(self):
        client = APIClient()
        client.force_authenticate(self.c)
        resp = client.post(self.url, {'kind': 'connect', 'ok': True}, format='json')
        self.assertEqual(403, resp.status_code)
        self.assertEqual('not_participant', resp.json()['code'])
        self.assertEqual(0, _summary()['connect']['attempts'])

    def test_unknown_call_404(self):
        resp = self.client.post('/message/calls/00000000-0000-0000-0000-000000000000/telemetry/',
                                {'kind': 'connect', 'ok': True}, format='json')
        self.assertEqual(404, resp.status_code)

    def test_unauthenticated_rejected(self):
        resp = APIClient().post(self.url, {'kind': 'connect', 'ok': True}, format='json')
        self.assertIn(resp.status_code, (401, 403))


class CallOpsViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = _user('staff')
        self.staff.is_staff = True
        self.staff.save(update_fields=['is_staff'])
        self.user = _user('plain')
        call_ops.record_connect('c1', 1, ok=True, setup_ms=700)

    def test_staff_gets_json(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        resp = client.get('/message/calls/ops/?minutes=15')
        self.assertEqual(200, resp.status_code)
        data = resp.json()['data']
        self.assertEqual(1, data['connect']['ok'])
        self.assertEqual([], data['breaches'])

    def test_non_staff_403(self):
        client = APIClient()
        client.force_authenticate(self.user)
        self.assertEqual(403, client.get('/message/calls/ops/').status_code)

    def test_anonymous_403(self):
        self.assertEqual(403, APIClient().get('/message/calls/ops/?format=prom').status_code)

    @override_settings(CALL_OPS_SCRAPE_TOKEN='scrape-tok', FEED_OPS_SCRAPE_TOKEN='')
    def test_scrape_token_gets_prometheus_text(self):
        resp = APIClient().get('/message/calls/ops/?format=prom', HTTP_AUTHORIZATION='Bearer scrape-tok')
        self.assertEqual(200, resp.status_code)
        self.assertTrue(resp['Content-Type'].startswith('text/plain'))
        self.assertIn('learnscroll_call_connect_attempts', resp.content.decode())

    @override_settings(CALL_OPS_SCRAPE_TOKEN='scrape-tok', FEED_OPS_SCRAPE_TOKEN='')
    def test_wrong_token_403(self):
        resp = APIClient().get('/message/calls/ops/?format=prom', HTTP_AUTHORIZATION='Bearer nope')
        self.assertEqual(403, resp.status_code)

    @override_settings(CALL_OPS_SCRAPE_TOKEN='', FEED_OPS_SCRAPE_TOKEN='feed-tok')
    def test_falls_back_to_feed_token(self):
        resp = APIClient().get('/message/calls/ops/?format=prom', HTTP_AUTHORIZATION='Bearer feed-tok')
        self.assertEqual(200, resp.status_code)

    @override_settings(CALL_OPS_SCRAPE_TOKEN='', FEED_OPS_SCRAPE_TOKEN='')
    def test_empty_tokens_never_match_empty_bearer(self):
        resp = APIClient().get('/message/calls/ops/?format=prom', HTTP_AUTHORIZATION='Bearer ')
        self.assertEqual(403, resp.status_code)


class CallRingHooksTests(TestCase):
    """Server-side hooks: accept / reject / cancel through call_state results, and the sweeper's `missed`."""

    def setUp(self):
        cache.clear()
        self.a, self.b = _user('a'), _user('b')
        self.conv = _dm(self.a, self.b)

    def test_accept_result_counts_answered_once(self):
        call = _call(self.conv, self.a, [self.b], started_ago=6)
        res = call_state.accept_call(call.id, self.b.id)
        call_ops.note_call_result(res)
        call_ops.note_call_result(res)     # repeat delivery
        ring = _summary()['ring']
        self.assertEqual(1, ring['outcomes']['answered'])
        self.assertEqual(1, ring['to_answer_s']['samples'])

    def test_call_status_strings_match_hook(self):
        # call_ops compares plain strings; fail loudly if the enum values ever change.
        self.assertEqual('rejected', CallStatus.REJECTED)
        self.assertEqual('cancelled', CallStatus.CANCELLED)
