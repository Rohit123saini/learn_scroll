"""N6 - reminders: daily cap, same-type collapse, Snooze, Turn off, action buttons, endpoints.
(NOT RUN.)  python manage.py test core.tests_notification_reminders"""
from datetime import datetime, timedelta, timezone as dt_tz
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from . import notification_reminders as rem
from .models import Notification, NotificationPreference
from .services import build_push_payload, in_app_allowed
from .tests import CoreTestBase

NT = Notification.NotifType
NOON = datetime(2026, 10, 26, 12, 0, tzinfo=dt_tz.utc)


def make_row(user, notif_type=NT.STREAK_AT_RISK, **data):
    return Notification.objects.create(recipient=user, notif_type=notif_type, title="t", message="m", data=data)


class ReminderBase(CoreTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)


class TypeSetTests(ReminderBase):
    def test_every_listed_name_is_a_real_member(self):
        for name in rem.REMINDER_TYPE_NAMES:
            self.assertTrue(hasattr(NT, name), name)

    def test_locked_types_are_not_reminders(self):
        self.assertFalse(rem.reminder_types() & NotificationPreference.NEVER_MUTE_TYPES)

    def test_gated_types_exclude_urgent_ones(self):
        gated = rem.gated_types()
        self.assertIn(NT.STREAK_AT_RISK.value, gated)
        self.assertIn(NT.WEEKLY_RECAP_READY.value, gated)
        self.assertNotIn(NT.TESTSERIES_DUE_SOON.value, gated)  # urgent: never capped
        self.assertNotIn(NT.CAMPUS_CLASS_STARTING.value, gated)

    def test_non_reminder_type_is_never_gated(self):
        self.assertIsNone(rem.claim_slot(self.student.id, NT.POST_LIKED.value))


@override_settings(NOTIF_REMINDER_DAILY_CAP=2, NOTIF_REMINDER_COLLAPSE_MINUTES=0)
class DailyCapTests(ReminderBase):
    def test_third_gated_push_of_the_day_is_blocked(self):
        types = [NT.STREAK_AT_RISK, NT.WEEKLY_RECAP_READY, NT.CONTENT_DIGEST]
        results = [rem.claim_slot(self.student.id, t.value, now=NOON) for t in types]
        self.assertEqual(results, [None, None, rem.REASON_CAP])
        self.assertEqual(rem.reminders_pushed_today(self.student.id, now=NOON), 2)

    def test_blocked_push_does_not_burn_a_slot(self):
        for t in (NT.STREAK_AT_RISK, NT.WEEKLY_RECAP_READY, NT.CONTENT_DIGEST, NT.CONTENT_DIGEST):
            rem.claim_slot(self.student.id, t.value, now=NOON)
        self.assertEqual(rem.reminders_pushed_today(self.student.id, now=NOON), 2)

    def test_next_day_starts_fresh(self):
        for t in (NT.STREAK_AT_RISK, NT.WEEKLY_RECAP_READY):
            rem.claim_slot(self.student.id, t.value, now=NOON)
        self.assertEqual(rem.blocked_reason(self.student.id, NT.CONTENT_DIGEST.value, now=NOON), rem.REASON_CAP)
        self.assertIsNone(rem.blocked_reason(self.student.id, NT.CONTENT_DIGEST.value, now=NOON + timedelta(days=1)))

    def test_urgent_reminders_ignore_the_cap(self):
        for t in (NT.STREAK_AT_RISK, NT.WEEKLY_RECAP_READY):
            rem.claim_slot(self.student.id, t.value, now=NOON)
        self.assertIsNone(rem.claim_slot(self.student.id, NT.TESTSERIES_DUE_SOON.value, now=NOON))
        self.assertIsNone(rem.claim_slot(self.student.id, NT.CAMPUS_CLASS_STARTING.value, now=NOON))

    def test_users_are_independent(self):
        for t in (NT.STREAK_AT_RISK, NT.WEEKLY_RECAP_READY):
            rem.claim_slot(self.student.id, t.value, now=NOON)
        self.assertIsNone(rem.claim_slot(self.other_student.id, NT.CONTENT_DIGEST.value, now=NOON))

    @override_settings(NOTIF_REMINDER_DAILY_CAP=0)
    def test_cap_zero_means_off(self):
        for _ in range(6):
            self.assertIsNone(rem.claim_slot(self.student.id, NT.STREAK_AT_RISK.value, now=NOON))


@override_settings(NOTIF_REMINDER_DAILY_CAP=0, NOTIF_REMINDER_COLLAPSE_MINUTES=240)
class CollapseTests(ReminderBase):
    def test_same_type_twice_collapses(self):
        self.assertIsNone(rem.claim_slot(self.student.id, NT.STREAK_AT_RISK.value))
        self.assertEqual(rem.claim_slot(self.student.id, NT.STREAK_AT_RISK.value), rem.REASON_COLLAPSED)

    def test_different_type_is_not_collapsed(self):
        self.assertIsNone(rem.claim_slot(self.student.id, NT.STREAK_AT_RISK.value))
        self.assertIsNone(rem.claim_slot(self.student.id, NT.WEEKLY_RECAP_READY.value))

    def test_blocked_reason_is_read_only(self):
        self.assertIsNone(rem.blocked_reason(self.student.id, NT.STREAK_AT_RISK.value))
        self.assertIsNone(rem.blocked_reason(self.student.id, NT.STREAK_AT_RISK.value))  # still free: nothing claimed
        rem.claim_slot(self.student.id, NT.STREAK_AT_RISK.value)
        self.assertEqual(rem.blocked_reason(self.student.id, NT.STREAK_AT_RISK.value), rem.REASON_COLLAPSED)


class PushIntegrationTests(ReminderBase):
    """send_push_for_notification: cap / collapse drop the buzz, never the bell row; reminder_gate=False bypasses."""

    @override_settings(NOTIF_REMINDER_DAILY_CAP=3, NOTIF_REMINDER_COLLAPSE_MINUTES=240)
    def test_second_same_type_push_is_dropped(self):
        from message.push_utils import send_push_for_notification

        first, second = make_row(self.student), make_row(self.student)
        with patch("message.push_utils.send_rich_push") as send:
            send_push_for_notification(first)
            send_push_for_notification(second)
        self.assertEqual(send.call_count, 1)
        self.assertTrue(Notification.objects.filter(pk=second.pk).exists())  # bell row stays

    @override_settings(NOTIF_REMINDER_DAILY_CAP=3, NOTIF_REMINDER_COLLAPSE_MINUTES=240)
    def test_reminder_gate_false_always_sends(self):
        from message.push_utils import send_push_for_notification

        with patch("message.push_utils.send_rich_push") as send:
            send_push_for_notification(make_row(self.student))
            send_push_for_notification(make_row(self.student), reminder_gate=False)
        self.assertEqual(send.call_count, 2)


class ActionButtonTests(ReminderBase):
    def test_reminder_push_carries_snooze_turn_off_and_open(self):
        ids = [a["id"] for a in build_push_payload(make_row(self.student))["actions"]]
        self.assertEqual(ids, [rem.ACTION_SNOOZE, rem.ACTION_TURN_OFF, rem.ACTION_OPEN])

    def test_testseries_due_soon_has_buttons_and_route(self):
        payload = build_push_payload(make_row(self.student, NT.TESTSERIES_DUE_SOON, series_id="abc"))
        self.assertEqual([a["id"] for a in payload["actions"]], [rem.ACTION_SNOOZE, rem.ACTION_TURN_OFF, rem.ACTION_OPEN])

    def test_open_is_the_third_button_and_ios_category_knows_it(self):
        from . import notification_rich as rich

        self.assertEqual([a["id"] for a in rem.REMINDER_ACTIONS][2], rem.ACTION_OPEN)
        self.assertEqual(rich.CATEGORY_BY_ACTION_ID[rem.ACTION_OPEN], "REMINDER")
        self.assertIn(rem.ACTION_OPEN, rich.APNS_CATEGORIES["REMINDER"])

    def test_join_now_reminder_keeps_three_buttons_and_drops_open(self):
        from . import notification_rich as rich

        out = rich.finalize_actions(
            [dict(a) for a in rem.REMINDER_ACTIONS], NT.SESSION_REMINDER.value, {"route": "classroom"},
        )
        self.assertEqual([a["id"] for a in out], ["join_now", rem.ACTION_SNOOZE, rem.ACTION_TURN_OFF])

    def test_ordinary_push_has_no_reminder_buttons(self):
        ids = [a["id"] for a in build_push_payload(make_row(self.student, NT.POST_LIKED))["actions"]]
        self.assertNotIn(rem.ACTION_SNOOZE, ids)
        self.assertNotIn(rem.ACTION_TURN_OFF, ids)

    def test_action_specs_for(self):
        self.assertEqual(rem.action_specs_for(NT.STREAK_AT_RISK.value), rem.REMINDER_ACTIONS)
        self.assertEqual(rem.action_specs_for(NT.POST_LIKED.value), ())


class TurnOffTests(ReminderBase):
    def test_turn_off_sets_push_override_only(self):
        rem.turn_off_type(self.student, NT.STREAK_AT_RISK.value)
        pref = NotificationPreference.objects.get(user=self.student)
        self.assertIs(pref.channel_overrides[NT.STREAK_AT_RISK.value]["push"], False)
        self.assertNotIn("push", pref.allowed_channels_for(NT.STREAK_AT_RISK.value))
        self.assertTrue(in_app_allowed(self.student.id, NT.STREAK_AT_RISK.value))  # bell row keeps coming

    def test_turn_off_keeps_other_channel_overrides_and_is_idempotent(self):
        pref = NotificationPreference.for_user(self.student)
        pref.channel_overrides = {NT.STREAK_AT_RISK.value: {"email": False}}
        pref.save()
        rem.turn_off_type(self.student, NT.STREAK_AT_RISK.value)
        rem.turn_off_type(self.student, NT.STREAK_AT_RISK.value)
        entry = NotificationPreference.objects.get(user=self.student).channel_overrides[NT.STREAK_AT_RISK.value]
        self.assertEqual(entry, {"email": False, "push": False})

    def test_turn_on_restores_and_drops_empty_override(self):
        rem.turn_off_type(self.student, NT.STREAK_AT_RISK.value)
        rem.turn_on_type(self.student, NT.STREAK_AT_RISK.value)
        pref = NotificationPreference.objects.get(user=self.student)
        self.assertNotIn(NT.STREAK_AT_RISK.value, pref.channel_overrides)
        self.assertIn("push", pref.allowed_channels_for(NT.STREAK_AT_RISK.value))

    def test_locked_type_is_refused(self):
        locked = next(iter(NotificationPreference.NEVER_MUTE_TYPES))
        with self.assertRaises(rem.ReminderActionError) as ctx:
            rem.turn_off_type(self.student, locked)
        self.assertEqual(ctx.exception.code, "locked")


class SnoozeTests(ReminderBase):
    def test_clamp(self):
        self.assertEqual(rem.clamp_snooze_minutes(None), rem.SNOOZE_DEFAULT_MINUTES)
        self.assertEqual(rem.clamp_snooze_minutes("30"), 30)
        for bad in (5, 99999, "soon", True):
            with self.assertRaises(rem.ReminderActionError) as ctx:
                rem.clamp_snooze_minutes(bad)
            self.assertEqual(ctx.exception.code, "bad_minutes")

    def test_snooze_writes_state_and_schedules(self):
        row = make_row(self.student)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async") as sched:
            result = rem.snooze_reminder(row, 30, now=NOON)
        row.refresh_from_db()
        self.assertEqual(result["minutes"], 30)
        self.assertEqual(row.data["snooze_state"], "pending")
        self.assertEqual(sched.call_args.kwargs["countdown"], 30 * 60)

    def test_long_snooze_is_chained_in_short_hops(self):
        row = make_row(self.student)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async") as sched:
            rem.snooze_reminder(row, 1440, now=NOON)
        self.assertEqual(sched.call_args.kwargs["countdown"], rem.SNOOZE_HOP_SECONDS)

    def test_non_reminder_row_cannot_be_snoozed(self):
        with self.assertRaises(rem.ReminderActionError) as ctx:
            rem.snooze_reminder(make_row(self.student, NT.POST_LIKED))
        self.assertEqual(ctx.exception.code, "not_a_reminder")

    def _snoozed(self, notif_type=NT.CONTENT_DIGEST, minutes=30, **data):
        row = make_row(self.student, notif_type, **data)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            rem.snooze_reminder(row, minutes, now=NOON)
        row.refresh_from_db()
        return row

    def test_delivery_sends_once_and_marks_sent(self):
        row = self._snoozed()
        until = row.data["snooze_until"]
        with patch("message.push_utils.send_rich_push") as send:
            self.assertEqual(rem.deliver_snoozed(row.id, row.data["snooze_token"], until, now=NOON + timedelta(minutes=30)), "sent")
            self.assertEqual(rem.deliver_snoozed(row.id, row.data["snooze_token"], until, now=NOON + timedelta(minutes=30)), "superseded")
        self.assertEqual(send.call_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.data["snooze_state"], "sent")

    def test_newer_snooze_supersedes_older_hop(self):
        row = self._snoozed()
        old_token, old_until = row.data["snooze_token"], row.data["snooze_until"]
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            rem.snooze_reminder(row, 60, now=NOON)
        with patch("message.push_utils.send_rich_push") as send:
            self.assertEqual(rem.deliver_snoozed(row.id, old_token, old_until, now=NOON + timedelta(minutes=30)), "superseded")
        send.assert_not_called()

    def test_early_hop_waits_instead_of_sending(self):
        row = self._snoozed(minutes=600)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async") as sched, patch("message.push_utils.send_rich_push") as send:
            outcome = rem.deliver_snoozed(row.id, row.data["snooze_token"], row.data["snooze_until"], now=NOON + timedelta(minutes=50))
        self.assertEqual(outcome, "waiting")
        sched.assert_called_once()
        send.assert_not_called()

    def test_turned_off_type_is_not_pushed_after_snooze(self):
        row = self._snoozed()
        rem.turn_off_type(self.student, row.notif_type)
        with patch("message.push_utils.send_rich_push") as send:
            outcome = rem.deliver_snoozed(row.id, row.data["snooze_token"], row.data["snooze_until"], now=NOON + timedelta(minutes=30))
        self.assertEqual(outcome, "dropped")
        send.assert_not_called()

    def test_deleted_row_is_gone(self):
        row = self._snoozed()
        row_id, token, until = row.id, row.data["snooze_token"], row.data["snooze_until"]
        row.delete()
        self.assertEqual(rem.deliver_snoozed(row_id, token, until, now=NOON + timedelta(minutes=30)), "gone")

    @override_settings(NOTIF_REMINDER_DAILY_CAP=1, NOTIF_REMINDER_COLLAPSE_MINUTES=240)
    def test_snooze_delivery_ignores_cap_and_collapse(self):
        rem.claim_slot(self.student.id, NT.CONTENT_DIGEST.value)  # cap + collapse window now full
        row = self._snoozed()
        with patch("message.push_utils.send_rich_push") as send:
            outcome = rem.deliver_snoozed(row.id, row.data["snooze_token"], row.data["snooze_until"], now=NOON + timedelta(minutes=30))
        self.assertEqual(outcome, "sent")
        self.assertEqual(send.call_count, 1)


class UnsnoozeTests(ReminderBase):
    """R9 - snooze undo."""

    def _snoozed(self, notif_type=NT.CONTENT_DIGEST, minutes=30):
        row = make_row(self.student, notif_type)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            rem.snooze_reminder(row, minutes, now=NOON)
        row.refresh_from_db()
        return row

    def test_unsnooze_cancels_the_pending_snooze(self):
        row = self._snoozed()
        token, until = row.data["snooze_token"], row.data["snooze_until"]
        self.assertEqual(rem.unsnooze_reminder(row), {"cancelled": True})
        row.refresh_from_db()
        self.assertEqual(row.data["snooze_state"], "cancelled")
        self.assertNotIn("snooze_until", row.data)
        # the Celery hop that was already queued finds nothing pending -> no push
        with patch("message.push_utils.send_rich_push") as send:
            self.assertEqual(rem.deliver_snoozed(row.id, token, until, now=NOON + timedelta(minutes=30)), "superseded")
        send.assert_not_called()

    def test_unsnooze_is_idempotent_and_safe_without_a_snooze(self):
        row = make_row(self.student)
        self.assertEqual(rem.unsnooze_reminder(row), {"cancelled": False})
        row = self._snoozed()
        rem.unsnooze_reminder(row)
        self.assertEqual(rem.unsnooze_reminder(row), {"cancelled": False})

    def test_unsnooze_after_delivery_changes_nothing(self):
        row = self._snoozed()
        with patch("message.push_utils.send_rich_push"):
            rem.deliver_snoozed(row.id, row.data["snooze_token"], row.data["snooze_until"], now=NOON + timedelta(minutes=30))
        row.refresh_from_db()
        self.assertEqual(rem.unsnooze_reminder(row), {"cancelled": False})
        row.refresh_from_db()
        self.assertEqual(row.data["snooze_state"], "sent")

    def test_non_reminder_row_cannot_be_unsnoozed(self):
        with self.assertRaises(rem.ReminderActionError) as ctx:
            rem.unsnooze_reminder(make_row(self.student, NT.POST_LIKED))
        self.assertEqual(ctx.exception.code, "not_a_reminder")

    def test_snooze_again_after_undo_works(self):
        row = self._snoozed()
        rem.unsnooze_reminder(row)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            rem.snooze_reminder(row, 60, now=NOON)
        row.refresh_from_db()
        self.assertEqual(row.data["snooze_state"], "pending")


class StillRelevantTests(ReminderBase):
    def test_test_closing_is_irrelevant_after_the_window(self):
        row = make_row(self.student, NT.TESTSERIES_DUE_SOON, ends_at=(NOON - timedelta(minutes=1)).isoformat())
        self.assertFalse(rem.still_relevant(row, now=NOON))

    def test_unknown_data_defaults_to_deliver(self):
        self.assertTrue(rem.still_relevant(make_row(self.student, NT.CONTENT_DIGEST), now=NOON))


class EndpointTests(ReminderBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def url(self, row, action):
        return f"/core/notifications/{row.id}/{action}/"

    def test_snooze_endpoint(self):
        row = make_row(self.student)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            res = self.client.post(self.url(row, "snooze"), {"minutes": 30}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["minutes"], 30)
        self.assertEqual(res.data["id"], row.id)

    def test_snooze_bad_minutes_and_non_reminder_are_400(self):
        row = make_row(self.student)
        self.assertEqual(self.client.post(self.url(row, "snooze"), {"minutes": 2}, format="json").status_code, 400)
        post_row = make_row(self.student, NT.POST_LIKED)
        self.assertEqual(self.client.post(self.url(post_row, "snooze"), {}, format="json").status_code, 400)

    def test_other_users_row_is_404(self):
        row = make_row(self.other_student)
        self.assertEqual(self.client.post(self.url(row, "snooze"), {}, format="json").status_code, 404)
        self.assertEqual(self.client.post(self.url(row, "turn-off-type"), {}, format="json").status_code, 404)

    def test_unsnooze_endpoint(self):
        row = make_row(self.student)
        with patch("core.tasks.deliver_snoozed_reminder.apply_async"):
            self.client.post(self.url(row, "snooze"), {"minutes": 30}, format="json")
        res = self.client.post(self.url(row, "unsnooze"))
        self.assertEqual((res.status_code, res.data["cancelled"], res.data["id"]), (200, True, row.id))
        res = self.client.post(self.url(row, "unsnooze"))
        self.assertEqual((res.status_code, res.data["cancelled"]), (200, False))  # idempotent

    def test_unsnooze_non_reminder_is_400_and_other_users_row_is_404(self):
        post_row = make_row(self.student, NT.POST_LIKED)
        self.assertEqual(self.client.post(self.url(post_row, "unsnooze")).status_code, 400)
        self.assertEqual(self.client.post(self.url(make_row(self.other_student), "unsnooze")).status_code, 404)

    def test_turn_off_and_on_endpoints(self):
        row = make_row(self.student)
        res = self.client.post(self.url(row, "turn-off-type"))
        self.assertEqual((res.status_code, res.data["push"]), (200, False))
        res = self.client.post(self.url(row, "turn-on-type"))
        self.assertEqual((res.status_code, res.data["push"]), (200, True))

    def test_turn_off_locked_type_is_403(self):
        locked = next(iter(NotificationPreference.NEVER_MUTE_TYPES))
        row = make_row(self.student, locked)
        self.assertEqual(self.client.post(self.url(row, "turn-off-type")).status_code, 403)
