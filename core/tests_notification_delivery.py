"""N5 - smart delivery: focus / exam mode, priority tiers, DND presets, timezone sync, roll-up, digest preview.
(NOT RUN.)  python manage.py test core.tests_notification_delivery"""
from datetime import datetime, timedelta, time as dtime, timezone as dt_tz
from unittest.mock import patch

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from . import notification_delivery as nd
from . import notification_priority as prio
from .models import Notification, NotificationPreference
from .services import create_notification
from .tests import CoreTestBase

NT = Notification.NotifType
PREFS = "/core/notification-preferences/me/"
TZ_URL = "/core/notification-preferences/timezone/"
PREVIEW_URL = "/core/notification-preferences/digest-preview/"
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=dt_tz.utc)  # a Monday, 12:00 UTC


def set_exam_mode(user, on=True, exam_date=None):
    from user_profile.models import UserPreference

    UserPreference.objects.update_or_create(user=user, defaults={"exam_mode": on, "exam_date": exam_date})


class PriorityTierTests(CoreTestBase):
    def test_tiers(self):
        self.assertEqual(prio.priority_for(NT.SESSION_LIVE), prio.URGENT)
        self.assertEqual(prio.priority_for(NT.INCOMING_CALL), prio.URGENT)
        self.assertEqual(prio.priority_for(NT.POST_LIKED), prio.LOW)
        self.assertEqual(prio.priority_for(NT.CHAT_MESSAGE), prio.NORMAL)
        self.assertEqual(prio.priority_for("some_future_type"), prio.NORMAL)

    def test_locked_and_bypass_types_are_never_low(self):
        low = prio.low_types()
        for t in Notification.locked_types() | set(NotificationPreference.QUIET_BYPASS_TYPES):
            self.assertNotIn(t, low)
            self.assertEqual(prio.priority_for(t), prio.URGENT)

    def test_every_listed_name_is_a_real_member(self):
        for name in prio.URGENT_TYPE_NAMES + prio.LOW_TYPE_NAMES:
            self.assertTrue(hasattr(NT, name), name)


class FocusModeTests(CoreTestBase):
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return NotificationPreference.objects.get(pk=p.pk)  # fresh instance: focus cache starts empty

    def test_exam_mode_on_only_urgent_types_push(self):
        set_exam_mode(self.student)
        p = self.pref()
        self.assertNotIn("push", p.allowed_channels_for(NT.CHAT_MESSAGE))
        self.assertNotIn("push", p.allowed_channels_for(NT.POST_LIKED))
        self.assertIn("push", p.allowed_channels_for(NT.SESSION_LIVE))
        self.assertIn("push", p.allowed_channels_for(NT.assigments_DUE_SOON))
        self.assertIn("push", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))
        self.assertEqual(p.push_block_reason(NT.CHAT_MESSAGE), "focus")

    def test_focus_keeps_email(self):
        set_exam_mode(self.student)
        self.assertIn("email", self.pref().allowed_channels_for(NT.CHAT_MESSAGE))

    def test_exam_mode_off_everything_pushes(self):
        set_exam_mode(self.student, on=False)
        self.assertIn("push", self.pref().allowed_channels_for(NT.CHAT_MESSAGE))

    def test_exam_date_passed_means_no_focus(self):
        set_exam_mode(self.student, exam_date=timezone.localdate() - timedelta(days=1))
        self.assertIn("push", self.pref().allowed_channels_for(NT.CHAT_MESSAGE))

    def test_user_can_opt_out_of_priority_only(self):
        set_exam_mode(self.student)
        self.assertIn("push", self.pref(focus_priority_only=False).allowed_channels_for(NT.CHAT_MESSAGE))

    def test_bell_row_is_still_created_in_focus_mode(self):
        set_exam_mode(self.student)
        self.pref()
        row = create_notification(self.student, NT.CHAT_MESSAGE, "Hi", "yo")
        self.assertIsNotNone(row)

    def test_switching_exam_mode_on_creates_the_preference_row(self):
        NotificationPreference.objects.filter(user=self.student).delete()
        set_exam_mode(self.student)
        self.assertTrue(NotificationPreference.objects.filter(user=self.student).exists())

    def test_prime_focus_uses_one_query_for_many(self):
        set_exam_mode(self.student)
        a = self.pref()
        b = NotificationPreference.for_user(self.other_student)
        with self.assertNumQueries(1):
            NotificationPreference.prime_focus([a, b])
        self.assertTrue(a.focus_active())
        self.assertFalse(b.focus_active())

    def test_split_push_audience_drops_focus_users_into_off(self):
        from tuitionclass.live_push import split_push_audience

        set_exam_mode(self.student)
        self.pref()
        NotificationPreference.for_user(self.other_student)
        push, quiet, off = split_push_audience([self.student.id, self.other_student.id], NT.CHAT_MESSAGE)
        self.assertEqual(push, [self.other_student.id])
        self.assertEqual(off, [self.student.id])
        push, _, off = split_push_audience([self.student.id], NT.SESSION_LIVE)
        self.assertEqual(push, [self.student.id])

    def test_streak_style_helper_skips_for_focus_but_defers_for_quiet(self):
        from .tasks import _push_state_for

        set_exam_mode(self.student)
        self.assertEqual(_push_state_for(self.pref(), NT.CHAT_MESSAGE, NOON), "skip")
        set_exam_mode(self.student, on=False)
        p = self.pref(dnd_until=NOON + timedelta(hours=1))
        self.assertEqual(_push_state_for(p, NT.CHAT_MESSAGE, NOON), "defer")


class DndAndLowTierTests(CoreTestBase):
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return NotificationPreference.objects.get(pk=p.pk)

    def test_dnd_blocks_push_but_bell_row_is_made(self):
        p = self.pref(dnd_until=timezone.now() + timedelta(hours=1))
        self.assertNotIn("push", p.allowed_channels_for(NT.CHAT_MESSAGE))
        self.assertEqual(p.push_block_reason(NT.CHAT_MESSAGE), "dnd")
        self.assertIsNotNone(create_notification(self.student, NT.CHAT_MESSAGE, "Hi"))

    def test_dnd_does_not_block_calls_or_emergencies(self):
        p = self.pref(dnd_until=timezone.now() + timedelta(hours=1))
        self.assertIn("push", p.allowed_channels_for(NT.INCOMING_CALL))
        self.assertIn("push", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))

    def test_quiet_window_wrapping_midnight(self):
        p = self.pref(quiet_start=dtime(22, 0), quiet_end=dtime(7, 0), timezone="UTC")
        late = datetime(2026, 10, 26, 23, 30, tzinfo=dt_tz.utc)
        early = datetime(2026, 10, 27, 6, 59, tzinfo=dt_tz.utc)
        self.assertEqual(p.push_block_reason(NT.CHAT_MESSAGE, late), "quiet")
        self.assertEqual(p.push_block_reason(NT.CHAT_MESSAGE, early), "quiet")
        self.assertIsNone(p.push_block_reason(NT.CHAT_MESSAGE, NOON))

    def test_low_rollup_is_opt_in(self):
        self.assertIn("push", self.pref().allowed_channels_for(NT.POST_LIKED))
        p = self.pref(low_priority_rollup=True)
        self.assertNotIn("push", p.allowed_channels_for(NT.POST_LIKED))
        self.assertEqual(p.push_block_reason(NT.POST_LIKED), "low")
        self.assertIn("push", p.allowed_channels_for(NT.CHAT_MESSAGE))
        self.assertIn("push", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))


class DndPresetTests(CoreTestBase):
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        return p

    def test_fixed_presets(self):
        p = self.pref()
        self.assertEqual(nd.dnd_until_for_preset(p, "1h", NOON), NOON + timedelta(hours=1))
        self.assertEqual(nd.dnd_until_for_preset(p, "8h", NOON), NOON + timedelta(hours=8))
        self.assertEqual(nd.dnd_until_for_preset(p, "24h", NOON), NOON + timedelta(hours=24))
        self.assertIsNone(nd.dnd_until_for_preset(p, "off", NOON))

    def test_tomorrow_uses_quiet_end_or_seven(self):
        p = self.pref(timezone="UTC")
        self.assertEqual(nd.dnd_until_for_preset(p, "tomorrow", NOON), datetime(2026, 10, 27, 7, 0, tzinfo=p._tzinfo()))
        p = self.pref(timezone="UTC", quiet_start=dtime(23, 0), quiet_end=dtime(6, 30))
        self.assertEqual(nd.dnd_until_for_preset(p, "tomorrow", NOON), datetime(2026, 10, 27, 6, 30, tzinfo=p._tzinfo()))

    def test_tomorrow_in_the_small_hours_means_this_morning_but_never_in_under_an_hour(self):
        p = self.pref(timezone="UTC")
        three = datetime(2026, 10, 26, 3, 0, tzinfo=dt_tz.utc)
        self.assertEqual(nd.dnd_until_for_preset(p, "tomorrow", three).date(), three.date())
        almost = datetime(2026, 10, 26, 6, 30, tzinfo=dt_tz.utc)
        self.assertEqual(nd.dnd_until_for_preset(p, "tomorrow", almost).date(), almost.date() + timedelta(days=1))

    def test_custom_rules(self):
        p = self.pref()
        ok = NOON + timedelta(hours=5)
        self.assertEqual(nd.dnd_until_for_preset(p, "custom", NOON, ok), ok)
        with self.assertRaises(ValueError):
            nd.dnd_until_for_preset(p, "custom", NOON, NOON - timedelta(minutes=1))
        with self.assertRaises(ValueError):
            nd.dnd_until_for_preset(p, "custom", NOON, NOON + timedelta(days=8))
        with self.assertRaises(ValueError):
            nd.dnd_until_for_preset(p, "custom", NOON, None)
        with self.assertRaises(ValueError):
            nd.dnd_until_for_preset(p, "forever", NOON)


class TimezoneSyncTests(CoreTestBase):
    def test_auto_follows_the_device(self):
        r = nd.sync_timezone(self.student, "Asia/Kolkata", NOON)
        self.assertTrue(r["applied"] and r["changed"])
        p = NotificationPreference.for_user(self.student)
        self.assertEqual((p.timezone, p.timezone_mode), ("Asia/Kolkata", "auto"))
        self.assertEqual(p.timezone_synced_at, NOON)

    def test_unchanged_zone_is_not_rewritten_within_12h(self):
        nd.sync_timezone(self.student, "Asia/Kolkata", NOON)
        r = nd.sync_timezone(self.student, "Asia/Kolkata", NOON + timedelta(hours=1))
        self.assertTrue(r["applied"] and not r["changed"])
        self.assertEqual(NotificationPreference.for_user(self.student).timezone_synced_at, NOON)

    def test_manual_mode_ignores_the_device(self):
        p = NotificationPreference.for_user(self.student)
        p.timezone, p.timezone_mode = "America/New_York", "manual"
        p.save()
        r = nd.sync_timezone(self.student, "Asia/Kolkata", NOON)
        self.assertFalse(r["applied"])
        self.assertEqual(r["reason"], "manual")
        self.assertEqual(NotificationPreference.for_user(self.student).timezone, "America/New_York")

    def test_invalid_zone_rejected(self):
        for bad in ("Mars/Olympus", "", None, "x" * 100):
            self.assertEqual(nd.sync_timezone(self.student, bad)["reason"], "invalid")


class RollupTests(CoreTestBase):
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return NotificationPreference.objects.get(pk=p.pk)

    def rows(self, ntype, n, **kw):
        for i in range(n):
            Notification.objects.create(recipient=self.student, notif_type=ntype, title=f"t{i}", **kw)

    def test_build_rollup_counts_only_unread_low_rows(self):
        p = self.pref(low_priority_rollup=True)
        self.rows(NT.POST_LIKED, 3)
        self.rows(NT.STORY_REACTION, 1)
        self.rows(NT.CHAT_MESSAGE, 5)                 # normal tier: not part of it
        self.rows(NT.POST_LIKED, 2, is_read=True)     # read: not counted
        r = nd.build_rollup(self.student.id, p, timezone.now() + timedelta(minutes=1), "en")
        self.assertEqual(r["total"], 4)
        self.assertEqual([(g["key"], g["count"]) for g in r["groups"]], [("likes", 3), ("reactions", 1)])
        self.assertEqual(r["body"], "3 likes · 1 reaction")

    def test_hindi_labels(self):
        p = self.pref()
        self.rows(NT.POST_LIKED, 2)
        r = nd.build_rollup(self.student.id, p, timezone.now() + timedelta(minutes=1), "hi")
        self.assertEqual(r["body"], "2 लाइक")

    def test_send_rollups_once_per_local_day(self):
        p = self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=9)
        self.rows(NT.POST_LIKED, 2)
        now = timezone.now().replace(hour=12, minute=0) + timedelta(minutes=1)
        with patch.object(nd, "_push_rollup") as push:
            out = nd.send_rollups(now)
            self.assertEqual(out["sent"], 1)
            out = nd.send_rollups(now + timedelta(hours=1))   # same local day: nothing
            self.assertEqual(out["checked"], 0)
        self.assertEqual(push.call_count, 1)

    def test_not_before_the_chosen_hour(self):
        self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=20)
        self.rows(NT.POST_LIKED, 1)
        now = timezone.now().replace(hour=12, minute=0)
        with patch.object(nd, "_push_rollup") as push:
            self.assertEqual(nd.send_rollups(now)["checked"], 0)
        push.assert_not_called()

    def test_dnd_waits_and_is_not_marked_done(self):
        p = self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=9)
        self.rows(NT.POST_LIKED, 1)
        now = timezone.now().replace(hour=12, minute=0) + timedelta(minutes=1)
        p.dnd_until = now + timedelta(hours=2)
        p.save()
        with patch.object(nd, "_push_rollup") as push:
            out = nd.send_rollups(now)
            self.assertEqual(out["waiting"], 1)
            push.assert_not_called()
            self.assertIsNone(NotificationPreference.objects.get(pk=p.pk).last_rollup_at)
            out = nd.send_rollups(now + timedelta(hours=3))   # window over -> goes out
            self.assertEqual(out["sent"], 1)

    def test_empty_day_sends_nothing_but_is_marked_done(self):
        p = self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=9)
        now = timezone.now().replace(hour=12, minute=0) + timedelta(minutes=1)
        with patch.object(nd, "_push_rollup") as push:
            self.assertEqual(nd.send_rollups(now)["empty"], 1)
        push.assert_not_called()
        self.assertIsNotNone(NotificationPreference.objects.get(pk=p.pk).last_rollup_at)

    def test_focus_mode_holds_the_summary_until_focus_ends(self):
        p = self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=9)
        self.rows(NT.POST_LIKED, 2)
        set_exam_mode(self.student, True)
        now = timezone.now().replace(hour=12, minute=0) + timedelta(minutes=1)
        with patch.object(nd, "_push_rollup") as push:
            out = nd.send_rollups(now)
            self.assertEqual(out["waiting"], 1)
            push.assert_not_called()
            self.assertIsNone(NotificationPreference.objects.get(pk=p.pk).last_rollup_at)  # not marked done
            set_exam_mode(self.student, False)
            self.assertEqual(nd.send_rollups(now + timedelta(hours=1))["sent"], 1)  # focus over -> one summary

    def test_push_off_drops_the_summary(self):
        self.pref(low_priority_rollup=True, timezone="UTC", content_digest_hour=9, push_enabled=False)
        self.rows(NT.POST_LIKED, 1)
        now = timezone.now().replace(hour=12, minute=0) + timedelta(minutes=1)
        with patch.object(nd, "_push_rollup") as push:
            self.assertEqual(nd.send_rollups(now)["dropped"], 1)
        push.assert_not_called()

    def test_users_without_rollup_are_never_touched(self):
        self.pref(low_priority_rollup=False)
        self.rows(NT.POST_LIKED, 4)
        with patch.object(nd, "_push_rollup") as push:
            self.assertEqual(nd.send_rollups(timezone.now() + timedelta(days=1))["checked"], 0)
        push.assert_not_called()


class DeliveryApiTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(self.student)

    def test_patch_dnd_preset_8h(self):
        r = self.api.patch(PREFS, {"dnd_preset": "8h"}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.content)
        p = NotificationPreference.for_user(self.student)
        self.assertAlmostEqual((p.dnd_until - timezone.now()).total_seconds(), 8 * 3600, delta=60)
        self.assertIsNotNone(r.json()["dnd_until"])

    def test_patch_dnd_preset_off_clears(self):
        self.api.patch(PREFS, {"dnd_preset": "1h"}, format="json")
        r = self.api.patch(PREFS, {"dnd_preset": "off"}, format="json")
        self.assertIsNone(r.json()["dnd_until"])

    def test_patch_dnd_preset_tomorrow(self):
        r = self.api.patch(PREFS, {"dnd_preset": "tomorrow"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertGreater(NotificationPreference.for_user(self.student).dnd_until, timezone.now())

    def test_custom_needs_a_time_in_range(self):
        self.assertEqual(self.api.patch(PREFS, {"dnd_preset": "custom"}, format="json").status_code, 400)
        past = (timezone.now() - timedelta(hours=1)).isoformat()
        self.assertEqual(self.api.patch(PREFS, {"dnd_preset": "custom", "dnd_custom_until": past}, format="json").status_code, 400)
        ok = (timezone.now() + timedelta(hours=3)).isoformat()
        self.assertEqual(self.api.patch(PREFS, {"dnd_preset": "custom", "dnd_custom_until": ok}, format="json").status_code, 200)

    def test_custom_until_alone_and_both_pause_styles_are_400(self):
        ok = (timezone.now() + timedelta(hours=3)).isoformat()
        self.assertEqual(self.api.patch(PREFS, {"dnd_custom_until": ok}, format="json").status_code, 400)
        self.assertEqual(self.api.patch(PREFS, {"dnd_preset": "1h", "dnd_for_minutes": 30}, format="json").status_code, 400)

    def test_old_dnd_for_minutes_still_works(self):
        self.assertEqual(self.api.patch(PREFS, {"dnd_for_minutes": 60}, format="json").status_code, 200)

    def test_switches_round_trip(self):
        r = self.api.patch(PREFS, {"low_priority_rollup": True, "focus_priority_only": False, "timezone_mode": "manual",
                                   "timezone": "Asia/Kolkata"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["low_priority_rollup"])
        self.assertFalse(body["focus_priority_only"])
        self.assertEqual(body["timezone_mode"], "manual")
        self.assertIn("focus_active", body)

    def test_focus_active_flag_in_response(self):
        set_exam_mode(self.student)
        self.assertTrue(self.api.get(PREFS).json()["focus_active"])

    def test_read_only_fields_cannot_be_written(self):
        self.api.patch(PREFS, {"last_rollup_at": "2020-01-01T00:00:00Z", "timezone_synced_at": "2020-01-01T00:00:00Z"}, format="json")
        p = NotificationPreference.for_user(self.student)
        self.assertIsNone(p.last_rollup_at)
        self.assertIsNone(p.timezone_synced_at)

    def test_timezone_sync_endpoint(self):
        r = self.api.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()["timezone"], "Asia/Kolkata")
        self.assertEqual(self.api.post(TZ_URL, {"timezone": "Nope/Nope"}, format="json").status_code, 400)
        self.assertEqual(self.api.post(TZ_URL, {}, format="json").status_code, 400)

    def test_timezone_sync_respects_manual_mode_and_other_users_rows(self):
        self.api.patch(PREFS, {"timezone_mode": "manual", "timezone": "America/New_York"}, format="json")
        r = self.api.post(TZ_URL, {"timezone": "Asia/Kolkata"}, format="json")
        self.assertFalse(r.json()["applied"])
        self.assertEqual(NotificationPreference.for_user(self.other_student).timezone, "UTC")

    def test_endpoints_need_login(self):
        anon = APIClient()
        self.assertIn(anon.post(TZ_URL, {"timezone": "UTC"}, format="json").status_code, (401, 403))
        self.assertIn(anon.get(PREVIEW_URL).status_code, (401, 403))

    def test_digest_preview_shape_and_example(self):
        Notification.objects.create(recipient=self.student, notif_type=NT.POST_LIKED, title="x")
        r = self.api.get(PREVIEW_URL)
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        for key in ("rollup", "study", "example", "focus_active", "timezone", "email_digest_frequency"):
            self.assertIn(key, body)
        self.assertEqual(body["rollup"]["total"], 1)
        self.assertTrue(body["example"]["rollup_body"])
        self.assertFalse(body["rollup"]["enabled"])

    def test_digest_preview_only_shows_own_rows(self):
        Notification.objects.create(recipient=self.other_student, notif_type=NT.POST_LIKED, title="x")
        self.assertEqual(self.api.get(PREVIEW_URL).json()["rollup"]["total"], 0)
