"""N10 - notification QA: channel matrix, quiet hours, DND, dedupe, reminder cap, ops counters, endpoints.

Likha gaya, CHALAYA NAHI gaya.   python manage.py test core.tests_notification_qa

Sab FCM / Celery mock hai. Cache (locmem) har test me clear hota hai.
"""
import itertools
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from . import notification_ops as ops
from .models import Notification, NotificationPreference
from .tests import CoreTestBase

NT = Notification.NotifType
User = get_user_model()

# Fixed instant: Mon 2026-10-26 17:00 UTC = 22:30 in Asia/Kolkata (UTC+5:30, no DST).
NOW = datetime(2026, 10, 26, 17, 0, tzinfo=dt_timezone.utc)


def _t(h, m=0):
    from datetime import time

    return time(h, m)


class OpsTestMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        ops.reset_local()

    def tearDown(self):
        cache.clear()
        ops.reset_local()
        super().tearDown()


# =====================================================================================================================
# 1. pure normalisation (no DB)
# =====================================================================================================================
class NormalisationTests(OpsTestMixin, SimpleTestCase):
    def test_unknown_type_becomes_other(self):
        self.assertEqual(ops.normalize_type("definitely_not_a_type"), ops.OTHER_TYPE)
        self.assertEqual(ops.normalize_type(None), ops.OTHER_TYPE)
        self.assertEqual(ops.normalize_type(""), ops.OTHER_TYPE)

    def test_label_injection_is_neutralised(self):
        # a hostile / buggy value can never become a new Prometheus label value
        self.assertEqual(ops.normalize_type('x"} 1\n# HELP'), ops.OTHER_TYPE)

    def test_known_types_cover_core_and_extra(self):
        known = ops.known_types()
        self.assertIn(NT.POST_LIKED.value, known)
        self.assertIn("chat_message", known)
        self.assertEqual(known[-1], ops.OTHER_TYPE)
        self.assertEqual(len(known), len(set(known)))

    def test_type_of_push_prefers_notif_type(self):
        self.assertEqual(ops.type_of_push({"notif_type": "post_liked", "type": "chat_message"}), "post_liked")
        self.assertEqual(ops.type_of_push({"type": "chat_message"}), "chat_message")
        self.assertEqual(ops.type_of_push(None), ops.OTHER_TYPE)
        self.assertEqual(ops.type_of_push("not a dict"), ops.OTHER_TYPE)

    def test_skip_reason_unknown_is_not_allowed(self):
        self.assertEqual(ops.normalize_skip_reason("dnd"), ops.SKIP_DND)
        self.assertEqual(ops.normalize_skip_reason("bogus"), ops.SKIP_NOT_ALLOWED)
        self.assertEqual(ops.normalize_skip_reason(None), ops.SKIP_NOT_ALLOWED)

    def test_fail_code_aliases(self):
        self.assertEqual(ops.normalize_fail_code("UNAVAILABLE"), "unavailable")
        self.assertEqual(ops.normalize_fail_code("UnregisteredError"), ops.FAIL_UNREGISTERED)
        self.assertEqual(ops.normalize_fail_code("QuotaExceededError"), "resource_exhausted")
        self.assertEqual(ops.normalize_fail_code("third_party_auth_error"), "third_party_auth_error")
        self.assertEqual(ops.normalize_fail_code("???"), "other")
        self.assertEqual(ops.normalize_fail_code(""), "other")

    def test_surface(self):
        self.assertEqual(ops.normalize_surface("Banner"), "banner")
        self.assertEqual(ops.normalize_surface("nope"), "other")
        self.assertEqual(ops.normalize_surface(None), "other")


# =====================================================================================================================
# 2. preference decisions: allowed_channels matrix, quiet hours, DND
# =====================================================================================================================
class PrefMixin:
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return p


class QuietHoursTests(PrefMixin, OpsTestMixin, CoreTestBase):
    """Window 22:00 -> 07:00 in Asia/Kolkata crosses midnight."""

    def wrap(self):
        return self.pref(quiet_start=_t(22), quiet_end=_t(7), timezone="Asia/Kolkata")

    def test_inside_before_midnight(self):
        self.assertTrue(self.wrap().is_quiet_now(NOW))  # 22:30 IST

    def test_inside_after_midnight(self):
        p = self.wrap()
        self.assertTrue(p.is_quiet_now(datetime(2026, 10, 26, 20, 0, tzinfo=dt_timezone.utc)))  # 01:30 IST next day
        self.assertTrue(p.is_quiet_now(datetime(2026, 10, 27, 1, 29, tzinfo=dt_timezone.utc)))  # 06:59 IST

    def test_end_is_exclusive(self):
        self.assertFalse(self.wrap().is_quiet_now(datetime(2026, 10, 27, 1, 30, tzinfo=dt_timezone.utc)))  # 07:00 IST

    def test_start_is_inclusive(self):
        p = self.wrap()
        self.assertTrue(p.is_quiet_now(datetime(2026, 10, 26, 16, 30, tzinfo=dt_timezone.utc)))  # 22:00 IST
        self.assertFalse(p.is_quiet_now(datetime(2026, 10, 26, 16, 29, tzinfo=dt_timezone.utc)))  # 21:59 IST

    def test_daytime_not_quiet(self):
        self.assertFalse(self.wrap().is_quiet_now(datetime(2026, 10, 27, 8, 0, tzinfo=dt_timezone.utc)))  # 13:30 IST

    def test_same_day_window(self):
        p = self.pref(quiet_start=_t(13), quiet_end=_t(15), timezone="Asia/Kolkata")
        self.assertTrue(p.is_quiet_now(datetime(2026, 10, 27, 8, 0, tzinfo=dt_timezone.utc)))  # 13:30 IST
        self.assertFalse(p.is_quiet_now(NOW))  # 22:30 IST

    def test_zone_matters(self):
        # same instant, window set in UTC: 17:00 UTC is NOT inside 22:00-07:00 UTC
        p = self.pref(quiet_start=_t(22), quiet_end=_t(7), timezone="UTC")
        self.assertFalse(p.is_quiet_now(NOW))

    def test_bad_timezone_does_not_crash(self):
        p = self.pref(quiet_start=_t(22), quiet_end=_t(7), timezone="Not/AZone")
        p.is_quiet_now(NOW)  # falls back to UTC, must not raise

    def test_equal_start_end_means_off(self):
        self.assertFalse(self.pref(quiet_start=_t(9), quiet_end=_t(9)).is_quiet_now(NOW))

    def test_unset_means_off(self):
        self.assertFalse(self.pref().is_quiet_now(NOW))

    def test_quiet_drops_push_keeps_email(self):
        p = self.wrap()
        self.assertEqual(p.allowed_channels_for(NT.POST_COMMENTED, NOW), ["email"])
        self.assertEqual(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW), ops.SKIP_QUIET_HOURS)

    def test_quiet_bypass_type_still_pushes(self):
        p = self.wrap()
        self.assertIn("push", p.allowed_channels_for(NT.INCOMING_CALL, NOW))
        self.assertIsNone(ops.push_skip_reason(p, NT.INCOMING_CALL, NOW))

    def test_emergency_pushes_through_quiet_hours(self):
        p = self.wrap()
        self.assertIsNone(ops.push_skip_reason(p, NT.CAMPUS_EMERGENCY, NOW))


class DndTests(PrefMixin, OpsTestMixin, CoreTestBase):
    def test_dnd_active(self):
        p = self.pref(dnd_until=NOW + timedelta(hours=1))
        self.assertTrue(p.is_quiet_now(NOW))
        self.assertEqual(p.push_block_reason(NT.POST_COMMENTED, NOW), "dnd")
        self.assertEqual(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW), ops.SKIP_DND)

    def test_dnd_expired(self):
        p = self.pref(dnd_until=NOW - timedelta(minutes=1))
        self.assertFalse(p.is_quiet_now(NOW))
        self.assertIsNone(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW))

    def test_dnd_exactly_at_end_is_over(self):
        p = self.pref(dnd_until=NOW)
        self.assertFalse(p.is_quiet_now(NOW))  # `now < dnd_until`

    def test_dnd_wins_over_quiet_label(self):
        p = self.pref(dnd_until=NOW + timedelta(hours=1), quiet_start=_t(22), quiet_end=_t(7), timezone="Asia/Kolkata")
        self.assertEqual(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW), ops.SKIP_DND)

    def test_dnd_bypass_types(self):
        p = self.pref(dnd_until=NOW + timedelta(hours=1))
        self.assertIsNone(ops.push_skip_reason(p, NT.INCOMING_CALL, NOW))
        self.assertIsNone(ops.push_skip_reason(p, NT.PARENT_DEVICE_PENDING, NOW))

    def test_dnd_keeps_email(self):
        p = self.pref(dnd_until=NOW + timedelta(hours=1))
        self.assertIn("email", p.allowed_channels_for(NT.POST_COMMENTED, NOW))


class SkipReasonTests(PrefMixin, OpsTestMixin, CoreTestBase):
    def test_no_row_means_allowed(self):
        self.assertIsNone(ops.push_skip_reason(None, NT.POST_COMMENTED, NOW))

    def test_defaults_allowed(self):
        self.assertIsNone(ops.push_skip_reason(self.pref(), NT.POST_COMMENTED, NOW))

    def test_master_off(self):
        self.assertEqual(ops.push_skip_reason(self.pref(push_enabled=False), NT.POST_COMMENTED, NOW),
                         ops.SKIP_PUSH_DISABLED)

    def test_type_muted(self):
        p = self.pref(muted_types=[NT.POST_COMMENTED.value])
        self.assertEqual(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW), ops.SKIP_TYPE_MUTED)

    def test_matrix_chip_off(self):
        p = self.pref(channel_overrides={NT.POST_COMMENTED.value: {"push": False}})
        self.assertEqual(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW), ops.SKIP_CHANNEL_OFF)
        self.assertIsNone(ops.push_skip_reason(p, NT.MENTION, NOW))  # other types unaffected

    def test_low_priority_rollup(self):
        p = self.pref(low_priority_rollup=True)
        self.assertEqual(ops.push_skip_reason(p, NT.POST_LIKED, NOW), ops.SKIP_LOW_PRIORITY)
        self.assertIsNone(ops.push_skip_reason(p, NT.POST_COMMENTED, NOW))  # normal tier

    def test_low_beats_quiet(self):
        p = self.pref(low_priority_rollup=True, dnd_until=NOW + timedelta(hours=1))
        self.assertEqual(ops.push_skip_reason(p, NT.POST_LIKED, NOW), ops.SKIP_LOW_PRIORITY)

    def test_locked_type_ignores_mute_and_chip(self):
        p = self.pref(muted_types=[NT.CAMPUS_EMERGENCY.value],
                      channel_overrides={NT.CAMPUS_EMERGENCY.value: {"push": False}})
        self.assertIsNone(ops.push_skip_reason(p, NT.CAMPUS_EMERGENCY, NOW))

    def test_locked_type_still_needs_master(self):
        p = self.pref(push_enabled=False)
        self.assertEqual(ops.push_skip_reason(p, NT.CAMPUS_EMERGENCY, NOW), ops.SKIP_PUSH_DISABLED)

    def test_every_reason_is_in_the_fixed_list(self):
        for kw in ({"push_enabled": False}, {"muted_types": [NT.POST_COMMENTED.value]},
                   {"channel_overrides": {NT.POST_COMMENTED.value: {"push": False}}},
                   {"dnd_until": NOW + timedelta(hours=1)}):
            reason = ops.push_skip_reason(self.pref(**kw), NT.POST_COMMENTED, NOW)
            self.assertIn(reason, ops.SKIP_REASONS)


class AllowedChannelsParityTests(PrefMixin, OpsTestMixin, CoreTestBase):
    """push_skip_reason is None  <=>  'push' in allowed_channels_for  (over a whole matrix of settings)."""

    TYPES = (NT.POST_LIKED, NT.POST_COMMENTED, NT.MENTION, NT.INCOMING_CALL, NT.CAMPUS_EMERGENCY,
             NT.PARENT_DEVICE_PENDING)

    def test_parity_matrix(self):
        p = NotificationPreference.for_user(self.student)
        p.timezone = "Asia/Kolkata"
        checked = 0
        for push_on, muted, chip_off, dnd, quiet, rollup in itertools.product(
                (True, False), (True, False), (True, False), (True, False), (True, False), (True, False)):
            for t in self.TYPES:
                p.push_enabled = push_on
                p.muted_types = [t.value] if muted else []
                p.channel_overrides = {t.value: {"push": False}} if chip_off else {}
                p.dnd_until = NOW + timedelta(hours=1) if dnd else None
                p.quiet_start, p.quiet_end = (_t(22), _t(7)) if quiet else (None, None)
                p.low_priority_rollup = rollup
                allowed = "push" in p.allowed_channels_for(t, NOW)
                reason = ops.push_skip_reason(p, t, NOW)
                with self.subTest(type=t.value, push_on=push_on, muted=muted, chip_off=chip_off, dnd=dnd,
                                  quiet=quiet, rollup=rollup):
                    self.assertEqual(allowed, reason is None, reason)
                    if reason is not None:
                        self.assertIn(reason, ops.SKIP_REASONS)
                        self.assertNotEqual(reason, ops.SKIP_NOT_ALLOWED)  # every case is itemised
                checked += 1
        self.assertEqual(checked, 64 * len(self.TYPES))


# =====================================================================================================================
# 3. counters + summary + SLO
# =====================================================================================================================
class CounterTests(OpsTestMixin, SimpleTestCase):
    def test_send_delivered_failed(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=3, ok=2, failed_by_code={"unavailable": 1})
        s = ops.summarize_last(15)
        self.assertEqual((s["sent"], s["delivered"], s["failed"]), (3, 2, 1))
        self.assertEqual(s["failed_other"], 1)
        self.assertEqual(s["failed_by_code"], {"unavailable": 1})
        self.assertEqual(s["delivery_success_pct"], 66.67)

    def test_dead_tokens_do_not_hurt_the_slo(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=10, ok=8, failed_by_code={"unregistered": 2})
        s = ops.summarize_last(15)
        self.assertEqual(s["failed"], 2)
        self.assertEqual(s["failed_dead_tokens"], 2)
        self.assertEqual(s["failed_other"], 0)
        self.assertEqual(s["delivery_success_pct"], 100.0)

    def test_retry_attempt_is_not_sent_again(self):
        data = {"notif_type": "post_commented"}
        ops.record_send(data, attempted=2, ok=1, retried=1)
        ops.record_send(data, attempted=1, ok=1, retry_attempt=1)
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 2)
        self.assertEqual(s["delivered"], 2)
        self.assertEqual(s["retried"], 1)

    def test_skip_with_data_dict_and_string(self):
        ops.record_skip({"notif_type": "post_liked"}, ops.SKIP_DND)
        ops.record_skip("post_liked", ops.SKIP_DND, 2)
        ops.record_skip("post_liked", "totally-unknown")
        s = ops.summarize_last(15)
        self.assertEqual(s["skipped"], 4)
        self.assertEqual(s["skipped_by_reason"], {ops.SKIP_DND: 3, ops.SKIP_NOT_ALLOWED: 1})

    def test_zero_and_negative_counts_ignored(self):
        ops.record(ops.EV_SENT, "post_liked", n=0)
        ops.record(ops.EV_SENT, "post_liked", n=-4)
        ops.record("no_such_event", "post_liked")
        self.assertEqual(ops.summarize_last(15)["sent"], 0)

    def test_open_rate_and_cap(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=4, ok=4)
        ops.record_opened("post_commented", "push")
        ops.record_opened("post_commented", "banner")
        s = ops.summarize_last(15)
        self.assertEqual(s["opened"], 2)
        self.assertEqual(s["open_rate_pct"], 50.0)
        self.assertEqual(s["opened_by_surface"], {"push": 1, "banner": 1})
        for _ in range(10):
            ops.record_opened("post_commented", "list")
        self.assertEqual(ops.summarize_last(15)["open_rate_pct"], 100.0)  # never above 100

    def test_empty_window(self):
        s = ops.summarize_last(15)
        self.assertEqual((s["sent"], s["delivered"], s["skipped"], s["opened"]), (0, 0, 0, 0))
        self.assertIsNone(s["delivery_success_pct"])
        self.assertIsNone(s["open_rate_pct"])

    def test_window_clamped(self):
        self.assertEqual(ops.summarize_last(0)["minutes"], 1)
        self.assertEqual(ops.summarize_last(10 ** 9)["minutes"], ops.MAX_SUMMARY_MINUTES)

    def test_old_bucket_falls_out_of_window(self):
        with mock.patch.object(ops.time, "time", return_value=1_000_000.0):
            ops.record(ops.EV_SENT, "post_liked", n=5)
        # real "now" is far later than the fake timestamp: the 15 min window must not see it
        self.assertEqual(ops.summarize_last(15)["sent"], 0)
        # ... but the cumulative per-type table still does
        rows = {r["type"]: r for r in ops.totals_by_type()}
        self.assertEqual(rows["post_liked"]["sent"], 5)

    def test_unknown_type_is_counted_as_other(self):
        ops.record(ops.EV_SENT, "zzz_brand_new", n=2)
        rows = {r["type"]: r for r in ops.totals_by_type()}
        self.assertEqual(rows["other"]["sent"], 2)
        self.assertNotIn("zzz_brand_new", rows)

    def test_totals_by_type_sorted_busiest_first(self):
        ops.record(ops.EV_SENT, "post_liked", n=1)
        ops.record(ops.EV_SENT, "post_commented", n=9)
        ops.record_skip("mention", ops.SKIP_DND, 4)
        order = [r["type"] for r in ops.totals_by_type()]
        self.assertEqual(order[:3], ["post_commented", "mention", "post_liked"])

    def test_totals_by_type_top_limit(self):
        for t in ("post_liked", "post_commented", "mention"):
            ops.record(ops.EV_SENT, t)
        self.assertEqual(len(ops.totals_by_type(top=2)), 2)

    def test_cache_down_falls_back_to_process_local(self):
        broken = mock.MagicMock()
        broken.add.side_effect = RuntimeError("redis down")
        broken.incr.side_effect = RuntimeError("redis down")
        broken.get_many.side_effect = RuntimeError("redis down")
        with mock.patch.object(ops, "cache", broken), mock.patch.object(ops.logger, "error"):
            ops.record(ops.EV_SENT, "post_liked", n=3)  # must not raise
            ops.record_skip("post_liked", ops.SKIP_DND)
            s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 3)
        self.assertEqual(s["skipped"], 1)

    def test_recording_never_raises_on_garbage(self):
        ops.record(None, None)
        ops.record_send(None, attempted="x", ok=None)  # type: ignore[arg-type]
        ops.record_skip(object(), None)
        ops.record_opened(None, None)


class SloTests(OpsTestMixin, SimpleTestCase):
    SLO = {"delivery_success_pct": 97.0, "alert_window_minutes": 15, "alert_min_attempts": 50,
           "max_not_allowed_pct": 5.0}

    def test_healthy(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=100, ok=99, failed_by_code={"unavailable": 1})
        self.assertEqual(ops.breaches(ops.summarize_last(15), self.SLO), [])

    def test_low_delivery_breaches_and_names_the_code(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=60, ok=40, failed_by_code={"unavailable": 20})
        problems = ops.breaches(ops.summarize_last(15), self.SLO)
        self.assertEqual(len(problems), 1)
        self.assertIn("66.67", problems[0])
        self.assertIn("unavailable=20", problems[0])

    def test_too_little_traffic_is_not_judged(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=10, ok=0, failed_by_code={"unavailable": 10})
        self.assertEqual(ops.breaches(ops.summarize_last(15), self.SLO), [])

    def test_dead_tokens_alone_never_breach(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=100, ok=40, failed_by_code={"unregistered": 60})
        self.assertEqual(ops.breaches(ops.summarize_last(15), self.SLO), [])

    def test_unexplained_skips_breach(self):
        ops.record_skip("post_commented", ops.SKIP_NOT_ALLOWED, 30)
        ops.record_skip("post_commented", ops.SKIP_ERROR, 30)
        problems = ops.breaches(ops.summarize_last(15), self.SLO)
        self.assertEqual(len(problems), 1)
        self.assertIn("no clear reason", problems[0])

    def test_explained_skips_do_not_breach(self):
        ops.record_skip("post_commented", ops.SKIP_DND, 200)
        self.assertEqual(ops.breaches(ops.summarize_last(15), self.SLO), [])

    @override_settings(NOTIFICATION_SLO={"delivery_success_pct": 50.0})
    def test_settings_override_merges_with_defaults(self):
        slo = ops.get_slo()
        self.assertEqual(slo["delivery_success_pct"], 50.0)
        self.assertEqual(slo["alert_min_attempts"], 50)

    def test_watch_logs_an_error_on_breach(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=60, ok=20, failed_by_code={"unavailable": 40})
        with mock.patch.object(ops, "_sentry") as sentry, self.assertLogs("core.notification_ops", "ERROR") as logs:
            out = ops.check_slo_and_alert()
        self.assertTrue(out["breaches"])
        self.assertIn("NOTIFICATION SLO ALERT", logs.output[0])
        sentry.assert_called_once()

    def test_watch_is_quiet_when_healthy(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=100, ok=100)
        with mock.patch.object(ops, "_sentry") as sentry, mock.patch.object(ops.logger, "error") as err:
            out = ops.check_slo_and_alert()
        self.assertEqual(out["breaches"], [])
        err.assert_not_called()
        sentry.assert_not_called()


class PrometheusTextTests(OpsTestMixin, SimpleTestCase):
    def test_counters_and_gauges(self):
        ops.record_send({"notif_type": "post_commented"}, attempted=2, ok=2)
        ops.record_skip("post_liked", ops.SKIP_DND, 3)
        ops.record_opened("post_commented", "banner")
        text = ops.prometheus_text()
        self.assertTrue(text.endswith("\n"))
        self.assertIn("# TYPE learnscroll_notification_push_sent_total counter", text)
        self.assertIn('learnscroll_notification_push_sent_total{type="post_commented"} 2', text)
        self.assertIn('learnscroll_notification_push_skipped_total{type="post_liked",reason="dnd"} 3', text)
        self.assertIn('learnscroll_notification_opened_total{type="post_commented",surface="banner"} 1', text)
        self.assertIn("# TYPE learnscroll_notification_delivery_success_pct gauge", text)
        self.assertIn('learnscroll_notification_delivery_success_pct{window="15m"} 100.0', text)

    def test_empty_still_valid(self):
        text = ops.prometheus_text()
        self.assertIn("# HELP learnscroll_notification_push_sent_total", text)
        self.assertNotIn("None", text)  # a gauge with no data is left out, not printed as None


# =====================================================================================================================
# 4. sender hooks (message.push_utils) - FCM mocked
# =====================================================================================================================
class SendMulticastOpsTests(OpsTestMixin, CoreTestBase):
    DATA = {"notif_type": "post_commented", "notification_id": 77}

    def _resp(self, results):
        return SimpleNamespace(failure_count=sum(1 for r in results if not r.success), responses=results)

    def _send(self, tokens, results, **kw):
        from message import push_utils

        with mock.patch.object(push_utils, "_ensure_firebase_initialized"), \
                mock.patch.object(push_utils.messaging, "send_each_for_multicast", return_value=self._resp(results)), \
                mock.patch("message.tasks.retry_push.apply_async") as retry:
            push_utils._send_multicast(tokens, data=kw.pop("data", self.DATA), **kw)
        return retry

    def test_ok_dead_transient(self):
        from firebase_admin import exceptions as fb, messaging

        results = [
            SimpleNamespace(success=True, exception=None),
            SimpleNamespace(success=False, exception=messaging.UnregisteredError("gone")),
            SimpleNamespace(success=False, exception=fb.UnavailableError("503")),
        ]
        retry = self._send(["good", "dead", "flaky"], results)
        retry.assert_called_once()
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 3)
        self.assertEqual(s["delivered"], 1)
        self.assertEqual(s["failed"], 1)  # the dead token
        self.assertEqual(s["failed_dead_tokens"], 1)
        self.assertEqual(s["retried"], 1)  # the flaky one, queued for another try - not a final failure
        self.assertEqual(s["failed_other"], 0)

    def test_retry_attempt_succeeds_without_counting_sent_again(self):
        from firebase_admin import exceptions as fb

        self._send(["flaky"], [SimpleNamespace(success=False, exception=fb.UnavailableError("503"))])
        self._send(["flaky"], [SimpleNamespace(success=True, exception=None)], retry_attempt=1)
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 1)
        self.assertEqual(s["delivered"], 1)
        self.assertEqual(s["retried"], 1)

    def test_retries_exhausted_is_a_final_failure(self):
        from firebase_admin import exceptions as fb
        from message import push_reliability as pr

        retry = self._send(["flaky"], [SimpleNamespace(success=False, exception=fb.UnavailableError("503"))],
                           retry_attempt=pr.MAX_RETRIES)
        retry.assert_not_called()
        s = ops.summarize_last(15)
        self.assertEqual(s["retried"], 0)
        self.assertEqual(s["failed"], 1)
        self.assertEqual(s["failed_other"], 1)

    def test_same_event_twice_counts_a_dedupe_skip(self):
        ok = [SimpleNamespace(success=True, exception=None)]
        self._send(["good"], ok)
        self._send(["good"], ok)
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 1)
        self.assertEqual(s["skipped_by_reason"], {ops.SKIP_DEDUPE: 1})

    def test_no_tokens_is_a_skip(self):
        from message import push_utils

        push_utils._send_multicast([], data=self.DATA)
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_NO_TOKENS: 1})

    def test_fcm_not_initialised_counts_every_token_as_failed(self):
        from message import push_utils

        with mock.patch.object(push_utils, "_ensure_firebase_initialized", side_effect=RuntimeError("no creds")):
            push_utils._send_multicast(["a", "b"], data=self.DATA)
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 2)
        self.assertEqual(s["delivered"], 0)
        self.assertEqual(s["failed_by_code"], {"fcm_down": 2})

    def test_whole_call_failure_is_fcm_down_and_retried(self):
        from message import push_utils

        with mock.patch.object(push_utils, "_ensure_firebase_initialized"), \
                mock.patch.object(push_utils.messaging, "send_each_for_multicast", side_effect=RuntimeError("net")), \
                mock.patch("message.tasks.retry_push.apply_async"):
            push_utils._send_multicast(["a", "b"], data=self.DATA)
        s = ops.summarize_last(15)
        self.assertEqual(s["sent"], 2)
        self.assertEqual(s["retried"], 2)
        self.assertEqual(s["failed"], 0)

    def test_chat_push_type_label(self):
        ok = [SimpleNamespace(success=True, exception=None)]
        self._send(["good"], ok, data={"type": "chat_message", "message_id": 5})
        rows = {r["type"]: r for r in ops.totals_by_type()}
        self.assertEqual(rows["chat_message"]["sent"], 1)


class SendPushForNotificationSkipTests(PrefMixin, OpsTestMixin, CoreTestBase):
    """send_push_for_notification: every early return counts a reason."""

    def row(self, notif_type=NT.POST_COMMENTED):
        return Notification.objects.create(recipient=self.student, notif_type=notif_type, title="t", message="m")

    def run_send(self, row, **patches):
        from message import push_utils

        with mock.patch.object(push_utils, "send_rich_push") as rich_send:
            push_utils.send_push_for_notification(row)
        return rich_send

    def test_none_row_is_noop(self):
        from message import push_utils

        push_utils.send_push_for_notification(None)
        self.assertEqual(ops.summarize_last(15)["skipped"], 0)

    def test_push_disabled(self):
        self.pref(push_enabled=False)
        sent = self.run_send(self.row())
        sent.assert_not_called()
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_PUSH_DISABLED: 1})

    def test_type_muted(self):
        self.pref(muted_types=[NT.POST_COMMENTED.value])
        self.run_send(self.row())
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_TYPE_MUTED: 1})

    def test_matrix_chip_off(self):
        self.pref(channel_overrides={NT.POST_COMMENTED.value: {"push": False}})
        self.run_send(self.row())
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_CHANNEL_OFF: 1})

    def test_dnd(self):
        from django.utils import timezone

        self.pref(dnd_until=timezone.now() + timedelta(hours=1))
        self.run_send(self.row())
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_DND: 1})

    def test_scope_muted(self):
        from message import push_utils

        with mock.patch.object(push_utils.center, "push_muted_for_row", return_value=True):
            sent = self.run_send(self.row())
        sent.assert_not_called()
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_SCOPE_MUTED: 1})

    def test_reminder_cap_and_collapsed(self):
        from message import push_utils

        for slot, expected in (("cap", ops.SKIP_REMINDER_CAP), ("collapsed", ops.SKIP_REMINDER_COLLAPSED)):
            cache.clear()
            ops.reset_local()
            with mock.patch.object(push_utils.center, "push_muted_for_row", return_value=False), \
                    mock.patch.object(push_utils.reminders, "claim_slot", return_value=slot):
                sent = self.run_send(self.row())
            sent.assert_not_called()
            self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {expected: 1}, slot)

    def test_decision_code_error_is_counted_not_raised(self):
        from message import push_utils

        with mock.patch.object(push_utils.center, "push_muted_for_row", side_effect=RuntimeError("boom")):
            self.run_send(self.row())  # must not raise
        self.assertEqual(ops.summarize_last(15)["skipped_by_reason"], {ops.SKIP_ERROR: 1})

    def test_allowed_row_reaches_the_sender_and_counts_no_skip(self):
        from message import push_utils

        with mock.patch.object(push_utils.center, "push_muted_for_row", return_value=False), \
                mock.patch.object(push_utils.reminders, "claim_slot", return_value=None):
            sent = self.run_send(self.row())
        sent.assert_called_once()
        self.assertEqual(ops.summarize_last(15)["skipped"], 0)


# =====================================================================================================================
# 5. HTTP endpoints
# =====================================================================================================================
class OpenedEndpointTests(OpsTestMixin, CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)
        self.row = Notification.objects.create(
            recipient=self.student, notif_type=NT.POST_COMMENTED, title="t", message="m")

    def url(self, row=None):
        return f"/core/notifications/{(row or self.row).pk}/opened/"

    def test_first_open_is_recorded_second_is_not(self):
        first = self.client.post(self.url(), {"surface": "banner"}, format="json")
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data, {"recorded": True})
        second = self.client.post(self.url(), {"surface": "list"}, format="json")
        self.assertEqual(second.data, {"recorded": False})
        s = ops.summarize_last(15)
        self.assertEqual(s["opened"], 1)
        self.assertEqual(s["opened_by_surface"], {"banner": 1})

    def test_unknown_surface_becomes_other(self):
        self.client.post(self.url(), {"surface": "<script>"}, format="json")
        self.assertEqual(ops.summarize_last(15)["opened_by_surface"], {"other": 1})

    def test_missing_body_is_fine(self):
        res = self.client.post(self.url(), {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(ops.summarize_last(15)["opened_by_surface"], {"other": 1})

    def test_does_not_mark_read(self):
        self.client.post(self.url(), {"surface": "push"}, format="json")
        self.row.refresh_from_db()
        self.assertFalse(self.row.is_read)

    def test_someone_elses_row_is_404_and_not_counted(self):
        theirs = Notification.objects.create(
            recipient=self.other_student, notif_type=NT.POST_COMMENTED, title="t", message="m")
        res = self.client.post(self.url(theirs), {"surface": "push"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(ops.summarize_last(15)["opened"], 0)

    def test_anonymous_is_rejected(self):
        res = APIClient().post(self.url(), {"surface": "push"}, format="json")
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_counted_under_the_rows_type(self):
        self.client.post(self.url(), {"surface": "push"}, format="json")
        rows = {r["type"]: r for r in ops.totals_by_type()}
        self.assertEqual(rows[NT.POST_COMMENTED.value]["opened"], 1)

    def test_cache_down_still_answers(self):
        broken = mock.MagicMock()
        broken.add.side_effect = RuntimeError("redis down")
        with mock.patch("django.core.cache.cache", broken):
            res = self.client.post(self.url(), {"surface": "push"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)


class OpsEndpointTests(OpsTestMixin, CoreTestBase):
    URL = "/core/notification-ops/"

    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(username="ops_staff", password="x", email="ops@example.com",
                                              is_staff=True)
        ops.record_send({"notif_type": "post_commented"}, attempted=3, ok=3)

    def test_staff_gets_summary(self):
        c = APIClient()
        c.force_authenticate(self.staff)
        res = c.get(self.URL + "?minutes=15&by_type=1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data["data"]
        self.assertEqual(data["sent"], 3)
        self.assertEqual(data["breaches"], [])
        self.assertEqual(data["by_type"][0]["type"], "post_commented")

    def test_by_type_is_opt_in(self):
        c = APIClient()
        c.force_authenticate(self.staff)
        self.assertNotIn("by_type", c.get(self.URL).data["data"])

    def test_bad_minutes_falls_back(self):
        c = APIClient()
        c.force_authenticate(self.staff)
        self.assertEqual(c.get(self.URL + "?minutes=abc").data["data"]["minutes"], 15)

    def test_normal_user_is_403(self):
        c = APIClient()
        c.force_authenticate(self.student)
        self.assertEqual(c.get(self.URL).status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_is_403(self):
        self.assertEqual(APIClient().get(self.URL).status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(NOTIFICATION_OPS_SCRAPE_TOKEN="scrape-secret")
    def test_scrape_token_reads_prometheus_text(self):
        res = APIClient().get(self.URL + "?format=prom", HTTP_AUTHORIZATION="Bearer scrape-secret")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("text/plain", res["Content-Type"])
        self.assertIn('learnscroll_notification_push_sent_total{type="post_commented"} 3', res.content.decode())

    @override_settings(NOTIFICATION_OPS_SCRAPE_TOKEN="scrape-secret")
    def test_wrong_scrape_token_is_403(self):
        res = APIClient().get(self.URL + "?format=prom", HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(NOTIFICATION_OPS_SCRAPE_TOKEN="", FEED_OPS_SCRAPE_TOKEN="feed-secret")
    def test_falls_back_to_the_feed_scrape_token(self):
        res = APIClient().get(self.URL + "?format=prom", HTTP_AUTHORIZATION="Bearer feed-secret")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    @override_settings(NOTIFICATION_OPS_SCRAPE_TOKEN="", FEED_OPS_SCRAPE_TOKEN="")
    def test_no_token_configured_means_staff_only(self):
        res = APIClient().get(self.URL, HTTP_AUTHORIZATION="Bearer ")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_no_user_data_in_the_payload(self):
        c = APIClient()
        c.force_authenticate(self.staff)
        body = str(c.get(self.URL + "?by_type=1").data)
        self.assertNotIn(self.student.username, body)
        self.assertNotIn(self.student.email, body)


class SloWatchTaskTests(OpsTestMixin, SimpleTestCase):
    def test_task_returns_the_summary(self):
        from . import tasks

        ops.record_send({"notif_type": "post_commented"}, attempted=5, ok=5)
        out = tasks.notification_slo_watch()
        self.assertEqual(out["sent"], 5)
        self.assertEqual(out["breaches"], [])
