"""N9 - notification-center extras: 30-day history, class / chat / post mute, "Why am I seeing this?", sound + vibration.

(NOT RUN.)  python manage.py test core.tests_notification_center
"""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import notification_center as center
from .models import Notification, NotificationPreference, NotificationScopeMute
from .tests import CoreTestBase

NT = Notification.NotifType
MUTES_URL = "/core/notification-scope-mutes/"
PREFS_URL = "/core/notification-preferences/me/"
LIST_URL = "/core/notifications/"


def make_row(user, notif_type=NT.GENERIC, age_days=0, **fields):
    data = fields.pop("data", {})
    row = Notification.objects.create(recipient=user, notif_type=notif_type, title="t", message="m", data=data, **fields)
    if age_days:
        Notification.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=age_days))
    return row


# ---------------------------------------------------------------------------------------------------------------------
class HistoryRetentionTests(CoreTestBase):
    def test_policy_defaults(self):
        self.assertEqual(center.history_days(), 30)
        self.assertEqual(center.locked_history_days(), 90)
        self.assertEqual(center.history_policy(), {"history_days": 30, "locked_history_days": 90})

    @override_settings(NOTIFICATION_HISTORY_DAYS=7, NOTIFICATION_LOCKED_HISTORY_DAYS=3)
    def test_locked_window_is_never_shorter_than_normal(self):
        self.assertEqual(center.history_days(), 7)
        self.assertEqual(center.locked_history_days(), 7)

    @override_settings(NOTIFICATION_HISTORY_DAYS="garbage")
    def test_bad_setting_falls_back_to_default(self):
        self.assertEqual(center.history_days(), 30)

    def test_old_rows_go_new_rows_stay_read_or_unread(self):
        old_read = make_row(self.student, age_days=31)
        Notification.objects.filter(pk=old_read.pk).update(is_read=True)
        old_unread = make_row(self.student, age_days=45)
        fresh = make_row(self.student, age_days=29)
        out = center.prune_history()
        self.assertEqual(out["normal"], 2)
        self.assertFalse(Notification.objects.filter(pk__in=[old_read.pk, old_unread.pk]).exists())
        self.assertTrue(Notification.objects.filter(pk=fresh.pk).exists())

    def test_archived_old_row_is_pruned_too(self):
        row = make_row(self.student, age_days=40, archived_at=timezone.now())
        center.prune_history()
        self.assertFalse(Notification.objects.filter(pk=row.pk).exists())

    def test_locked_types_live_to_the_longer_window(self):
        locked = make_row(self.student, NT.CAMPUS_EMERGENCY, age_days=60)
        very_old_locked = make_row(self.student, NT.CAMPUS_EMERGENCY, age_days=120)
        out = center.prune_history()
        self.assertTrue(Notification.objects.filter(pk=locked.pk).exists())
        self.assertFalse(Notification.objects.filter(pk=very_old_locked.pk).exists())
        self.assertEqual(out["locked"], 1)

    def test_other_users_rows_follow_the_same_rule(self):
        mine = make_row(self.student, age_days=50)
        theirs = make_row(self.other_student, age_days=10)
        center.prune_history()
        self.assertFalse(Notification.objects.filter(pk=mine.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=theirs.pk).exists())

    def test_dry_run_counts_but_deletes_nothing(self):
        row = make_row(self.student, age_days=99)
        out = center.prune_history(dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["normal"], 1)
        self.assertTrue(Notification.objects.filter(pk=row.pk).exists())

    def test_expired_scope_mutes_are_removed_active_ones_stay(self):
        now = timezone.now()
        NotificationScopeMute.objects.create(user=self.student, scope_type="class", scope_id="1", muted_until=now - timedelta(hours=1))
        keep = NotificationScopeMute.objects.create(user=self.student, scope_type="class", scope_id="2", muted_until=now + timedelta(hours=1))
        forever = NotificationScopeMute.objects.create(user=self.student, scope_type="post", scope_id="3")
        out = center.prune_history()
        self.assertEqual(out["mutes"], 1)
        self.assertEqual(set(NotificationScopeMute.objects.values_list("pk", flat=True)), {keep.pk, forever.pk})

    def test_batches_are_bounded_and_report_more(self):
        make_row(self.student, age_days=50)
        make_row(self.student, age_days=50)
        make_row(self.student, age_days=50)
        with patch.object(center, "_DELETE_BATCH", 1), patch.object(center, "_MAX_BATCHES_PER_RUN", 2):
            out = center.prune_history()
        self.assertEqual(out["normal"], 2)
        self.assertTrue(out["more"])
        self.assertEqual(Notification.objects.count(), 1)

    def test_celery_task_returns_the_counts(self):
        from .tasks import prune_notification_history

        make_row(self.student, age_days=50)
        out = prune_notification_history()
        self.assertEqual(out["normal"], 1)

    def test_beat_entry_exists(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["core-prune-notification-history"]
        self.assertEqual(entry["task"], "core.prune_notification_history")


# ---------------------------------------------------------------------------------------------------------------------
class ScopeHelpersTests(SimpleTestCase):
    def test_scopes_most_specific_first_and_deduped(self):
        cid = str(uuid.uuid4())
        out = center.scopes_for("x", {"conversation_id": cid, "post_id": 9, "classroom_id": 4}, classroom_id=4)
        self.assertEqual(out, [("conversation", cid), ("class", "4"), ("post", "9")])

    def test_missing_or_blank_ids_give_nothing(self):
        self.assertEqual(center.scopes_for("x", {"post_id": None, "conversation_id": ""}), [])
        self.assertEqual(center.scopes_for("x", None), [])
        self.assertEqual(center.scopes_for("x", "not a dict"), [])

    def test_classroom_id_from_data_when_column_empty(self):
        self.assertEqual(center.scopes_for("x", {"classroom_id": "12"}), [("class", "12")])

    def test_mutable_rules(self):
        self.assertTrue(center.scope_mutable(NT.CHAT_MESSAGE))
        self.assertTrue(center.scope_mutable(NT.SESSION_LIVE))
        self.assertFalse(center.scope_mutable(NT.CAMPUS_EMERGENCY))   # locked + never-mute
        self.assertFalse(center.scope_mutable(NT.INCOMING_CALL))      # quiet-bypass
        self.assertFalse(center.scope_mutable(NT.WITHDRAWAL_PAID))    # locked
        self.assertFalse(center.scope_mutable(NT.COPYRIGHT_NOTICE))   # locked

    def test_parse_duration(self):
        self.assertEqual(center.parse_duration("8h"), timedelta(hours=8))
        self.assertEqual(center.parse_duration("7d"), timedelta(days=7))
        self.assertIsNone(center.parse_duration("forever"))
        self.assertIsNone(center.parse_duration())
        self.assertEqual(center.parse_duration(None, 45), timedelta(minutes=45))
        self.assertEqual(center.parse_duration("1h", 999), timedelta(hours=1))  # preset wins

    def test_parse_duration_rejects_bad_input(self):
        for args in (("2w", None), (None, "abc"), (None, 0), (None, -5), (None, center.MAX_MUTE_MINUTES + 1)):
            with self.assertRaises(center.MuteError, msg=args):
                center.parse_duration(*args)

    def test_clean_scope(self):
        self.assertEqual(center.clean_scope(" Class ", " 12 "), ("class", "12"))
        for bad in (("team", "1"), ("class", ""), ("class", "a b"), ("class", "x" * 41), ("post", "a/b")):
            with self.assertRaises(center.MuteError, msg=bad):
                center.clean_scope(*bad)


# ---------------------------------------------------------------------------------------------------------------------
class ScopeMuteDbTests(CoreTestBase):
    def test_set_is_idempotent_and_updates_duration_and_label(self):
        row, created = center.set_mute(self.student, "class", "5", duration="1h", label="Physics A")
        self.assertTrue(created)
        row2, created2 = center.set_mute(self.student, "class", "5", duration="forever")
        self.assertFalse(created2)
        self.assertEqual(row.pk, row2.pk)
        self.assertIsNone(row2.muted_until)
        self.assertEqual(row2.label, "Physics A")  # blank label does not wipe the stored one
        self.assertEqual(NotificationScopeMute.objects.count(), 1)

    def test_label_is_truncated(self):
        row, _ = center.set_mute(self.student, "post", "1", duration="1h", label="x" * 500)
        self.assertEqual(len(row.label), 120)

    def test_limit_of_active_mutes(self):
        with patch.object(center, "MAX_ACTIVE_MUTES", 2):
            center.set_mute(self.student, "post", "1")
            center.set_mute(self.student, "post", "2")
            with self.assertRaises(center.MuteError) as ctx:
                center.set_mute(self.student, "post", "3")
            self.assertEqual(ctx.exception.code, "too_many")
            center.set_mute(self.student, "post", "2", duration="1h")  # changing an existing one is still fine

    def test_clear(self):
        center.set_mute(self.student, "class", "5")
        self.assertEqual(center.clear_mute(self.student, "class", "5"), 1)
        self.assertEqual(center.clear_mute(self.student, "class", "5"), 0)

    def test_expired_mute_is_not_active(self):
        NotificationScopeMute.objects.create(
            user=self.student, scope_type="class", scope_id="5", muted_until=timezone.now() - timedelta(minutes=1),
        )
        self.assertEqual(center.muted_scope_set(self.student.id, [("class", "5")]), set())

    def test_muted_scope_set_is_one_query_and_per_user(self):
        center.set_mute(self.student, "class", "5")
        center.set_mute(self.other_student, "class", "6")
        with self.assertNumQueries(1):
            got = center.muted_scope_set(self.student.id, [("class", "5"), ("class", "6"), ("post", "5")])
        self.assertEqual(got, {("class", "5")})

    def test_row_push_muted_for_class_chat_and_post(self):
        cid = str(uuid.uuid4())
        center.set_mute(self.student, "class", "5", duration="8h")
        center.set_mute(self.student, "conversation", cid)
        center.set_mute(self.student, "post", "9", duration="7d")
        self.assertTrue(center.push_muted_for_row(make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"})))
        self.assertTrue(center.push_muted_for_row(make_row(self.student, NT.CHAT_MESSAGE, data={"conversation_id": cid})))
        self.assertTrue(center.push_muted_for_row(make_row(self.student, NT.POST_COMMENTED, data={"post_id": 9})))
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.POST_COMMENTED, data={"post_id": 10})))
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.GENERIC)))

    def test_other_users_mute_does_not_silence_me(self):
        center.set_mute(self.other_student, "class", "5")
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"})))

    def test_locked_and_call_types_ignore_the_mute(self):
        center.set_mute(self.student, "class", "5")
        center.set_mute(self.student, "conversation", "c1")
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.CAMPUS_EMERGENCY, data={"classroom_id": "5"})))
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.INCOMING_CALL, data={"conversation_id": "c1"})))

    def test_check_fails_open(self):
        row = make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"})
        with patch.object(center, "muted_scope_set", side_effect=RuntimeError("db down")):
            self.assertFalse(center.push_muted_for_row(row))
        self.assertFalse(center.push_muted_for_row(None))

    def test_filter_unmuted_users(self):
        center.set_mute(self.student, "class", "5")
        ids = [self.student.id, self.other_student.id, self.student.id, None]
        with self.assertNumQueries(1):
            out = center.filter_unmuted_users(ids, "class", 5, NT.SESSION_LIVE)
        self.assertEqual(out, [self.other_student.id])

    def test_filter_unmuted_users_edge_cases(self):
        center.set_mute(self.student, "class", "5")
        self.assertEqual(center.filter_unmuted_users([], "class", 5), [])
        self.assertEqual(center.filter_unmuted_users([self.student.id], "class", None), [self.student.id])
        # a locked type is never filtered out
        self.assertEqual(center.filter_unmuted_users([self.student.id], "class", 5, NT.CAMPUS_EMERGENCY), [self.student.id])
        with patch.object(center, "active_mutes_q", side_effect=RuntimeError("boom")):
            self.assertEqual(center.filter_unmuted_users([self.student.id], "class", 5), [self.student.id])


# ---------------------------------------------------------------------------------------------------------------------
class ScopeMuteApiTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_requires_auth(self):
        self.assertIn(APIClient().get(MUTES_URL).status_code, (401, 403))
        self.assertIn(APIClient().post(MUTES_URL, {}, format="json").status_code, (401, 403))

    def test_post_get_delete_round_trip(self):
        res = self.client.post(MUTES_URL, {"scope_type": "class", "scope_id": 12, "duration": "8h", "label": "Physics A"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data["scope_id"], "12")
        self.assertFalse(res.data["forever"])
        self.assertTrue(res.data["active"])
        res = self.client.post(MUTES_URL, {"scope_type": "class", "scope_id": 12, "duration": "forever"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.data["forever"])

        res = self.client.get(MUTES_URL)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data["results"]), 1)
        self.assertEqual(res.data["results"][0]["label"], "Physics A")
        self.assertEqual(res.data["history_days"], 30)

        res = self.client.delete(f"{MUTES_URL}?scope_type=class&scope_id=12")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.client.delete(f"{MUTES_URL}?scope_type=class&scope_id=12").status_code, 204)  # idempotent
        self.assertEqual(self.client.get(MUTES_URL).data["results"], [])

    def test_bad_input_is_400_with_code(self):
        res = self.client.post(MUTES_URL, {"scope_type": "team", "scope_id": 1}, format="json")
        self.assertEqual((res.status_code, res.data["code"]), (400, "bad_scope_type"))
        res = self.client.post(MUTES_URL, {"scope_type": "class", "scope_id": ""}, format="json")
        self.assertEqual((res.status_code, res.data["code"]), (400, "bad_scope_id"))
        res = self.client.post(MUTES_URL, {"scope_type": "class", "scope_id": 1, "duration": "2w"}, format="json")
        self.assertEqual((res.status_code, res.data["code"]), (400, "bad_duration"))
        res = self.client.post(MUTES_URL, {"scope_type": "class", "scope_id": 1, "minutes": 0}, format="json")
        self.assertEqual((res.status_code, res.data["code"]), (400, "bad_minutes"))
        self.assertEqual(self.client.delete(f"{MUTES_URL}?scope_type=x&scope_id=1").status_code, 400)

    def test_list_hides_expired_and_other_users(self):
        NotificationScopeMute.objects.create(user=self.student, scope_type="class", scope_id="1", muted_until=timezone.now() - timedelta(hours=1))
        NotificationScopeMute.objects.create(user=self.other_student, scope_type="class", scope_id="2")
        self.assertEqual(self.client.get(MUTES_URL).data["results"], [])

    def test_delete_cannot_touch_someone_elses_mute(self):
        NotificationScopeMute.objects.create(user=self.other_student, scope_type="class", scope_id="2")
        self.client.delete(f"{MUTES_URL}?scope_type=class&scope_id=2")
        self.assertEqual(NotificationScopeMute.objects.filter(user=self.other_student).count(), 1)


# ---------------------------------------------------------------------------------------------------------------------
class WhyTests(CoreTestBase):
    def _row(self, notif_type, **fields):
        return Notification(recipient=self.student, notif_type=notif_type, title="t", **fields)

    def test_every_notif_type_has_a_bilingual_reason(self):
        for value in NT.values:
            why = center.why_for(Notification(recipient=self.student, notif_type=value, title="t"))
            self.assertTrue(why["en"].strip(), value)
            self.assertTrue(why["hi"].strip(), value)
            self.assertNotIn("{who}", why["en"] + why["hi"], value)
            self.assertIn(why["control"], ("locked", "type_settings"), value)

    def test_why_needs_no_database(self):
        row = self._row(NT.POST_LIKED, data={"actor_username": "asha", "post_id": 3})
        with self.assertNumQueries(0):
            why = center.why_for(row)
        self.assertIn("@asha", why["en"])
        self.assertIn("@asha", why["hi"])

    def test_actor_name_falls_back_gracefully(self):
        why = center.why_for(self._row(NT.POST_LIKED))
        self.assertTrue(why["en"].startswith("Someone"))

    def test_locked_type_cannot_be_switched_off_and_offers_no_scope(self):
        why = center.why_for(self._row(NT.CAMPUS_EMERGENCY, data={"classroom_id": "5"}))
        self.assertTrue(why["locked"])
        self.assertEqual(why["control"], "locked")
        self.assertIsNone(why["scope"])

    def test_scope_offered_for_chat_class_and_post(self):
        cid = str(uuid.uuid4())
        self.assertEqual(center.why_for(self._row(NT.CHAT_MESSAGE, data={"conversation_id": cid}))["scope"],
                         {"scope_type": "conversation", "scope_id": cid})
        self.assertEqual(center.why_for(self._row(NT.SESSION_LIVE, data={"classroom_id": 5}))["scope"],
                         {"scope_type": "class", "scope_id": "5"})
        self.assertEqual(center.why_for(self._row(NT.POST_COMMENTED, data={"post_id": 7}))["scope"],
                         {"scope_type": "post", "scope_id": "7"})
        self.assertIsNone(center.why_for(self._row(NT.GENERIC))["scope"])

    def test_call_is_never_mutable(self):
        why = center.why_for(self._row(NT.INCOMING_CALL, data={"conversation_id": "c"}))
        self.assertTrue(why["locked"])
        self.assertIsNone(why["scope"])


class ListSerializerTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_rows_carry_why_and_scope_muted(self):
        center.set_mute(self.student, "class", "5")
        muted = make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"})
        open_ = make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "6"})
        plain = make_row(self.student, NT.GENERIC)
        locked = make_row(self.student, NT.CAMPUS_EMERGENCY, data={"classroom_id": "5"})
        res = self.client.get(LIST_URL)
        self.assertEqual(res.status_code, 200)
        rows = {r["id"]: r for r in (res.data["results"] if isinstance(res.data, dict) else res.data)}
        self.assertIs(rows[muted.id]["scope_muted"], True)
        self.assertIs(rows[open_.id]["scope_muted"], False)
        self.assertIsNone(rows[plain.id]["scope_muted"])
        self.assertIsNone(rows[locked.id]["scope_muted"])
        self.assertTrue(rows[muted.id]["why"]["en"])
        self.assertEqual(rows[muted.id]["why"]["scope"], {"scope_type": "class", "scope_id": "5"})

    def test_mute_lookup_is_one_query_for_the_whole_page(self):
        center.set_mute(self.student, "class", "5")
        for i in range(8):
            make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": str(i)})
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(LIST_URL)
        mute_queries = [q for q in ctx.captured_queries if "core_notification_scope_mute" in q["sql"]]
        self.assertEqual(len(mute_queries), 1)


# ---------------------------------------------------------------------------------------------------------------------
class SoundPrefsTests(SimpleTestCase):
    def test_merge_keeps_only_deviations(self):
        out = center.clean_sound_prefs({"social": {"sound": "gentle", "vibrate": False}})
        self.assertEqual(out, {"social": {"sound": "gentle", "vibrate": False}})
        out = center.clean_sound_prefs({"social": {"sound": "default"}}, out)  # back to default sound, vibrate stays
        self.assertEqual(out, {"social": {"vibrate": False}})
        out = center.clean_sound_prefs({"social": {"vibrate": True}}, out)      # all default -> entry removed
        self.assertEqual(out, {})

    def test_null_removes_a_channel(self):
        cur = {"classes": {"sound": "silent"}, "tests": {"vibrate": False}}
        self.assertEqual(center.clean_sound_prefs({"classes": None}, cur), {"tests": {"vibrate": False}})
        self.assertEqual(center.clean_sound_prefs({"tests": {}}, cur), {"classes": {"sound": "silent"}})

    def test_calls_and_safety_channel_can_not_be_silent(self):
        for channel in ("calls", "notifications"):
            with self.assertRaises(ValueError):
                center.clean_sound_prefs({channel: {"sound": "silent"}})
            self.assertEqual(center.clean_sound_prefs({channel: {"vibrate": False}}), {channel: {"vibrate": False}})
            self.assertEqual(center.clean_sound_prefs({channel: {"sound": "gentle"}}), {channel: {"sound": "gentle"}})

    def test_bad_input(self):
        for bad in ("x", [], {"nope": {"sound": "gentle"}}, {"social": {"sound": "loud"}}, {"social": {"vibrate": "yes"}},
                    {"social": {"volume": 3}}, {"social": "gentle"}):
            with self.assertRaises(ValueError, msg=bad):
                center.clean_sound_prefs(bad)

    def test_stale_values_for_unknown_channels_are_dropped_on_merge(self):
        self.assertEqual(center.clean_sound_prefs({}, {"gone_channel": {"sound": "silent"}}), {})

    def test_sound_for_defaults_and_hand_edited_values(self):
        self.assertEqual(center.sound_for(None, "social"), ("default", True))

        class P:  # a row edited by hand in admin
            sound_prefs = {"calls": {"sound": "silent"}, "social": {"sound": "silent", "vibrate": False}, "tests": {"sound": "???"}}

        self.assertEqual(center.sound_for(P, "calls"), ("default", True))   # never silent
        self.assertEqual(center.sound_for(P, "social"), ("silent", False))
        self.assertEqual(center.sound_for(P, "tests"), ("default", True))   # unknown value -> default

    def test_push_data_is_empty_for_defaults(self):
        class P:
            sound_prefs = {"social": {"sound": "gentle", "vibrate": False}}

        self.assertEqual(center.push_sound_data(None, "social"), {})
        self.assertEqual(center.push_sound_data(P, "classes"), {})
        self.assertEqual(center.push_sound_data(P, "social"), {"snd": "gentle", "vib": "0"})

    def test_channels_match_the_push_channel_catalogue(self):
        from message.push_channels import ALL_CHANNELS

        self.assertEqual(set(center.sound_channels()), set(ALL_CHANNELS))


class SoundPrefsApiTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_patch_and_read_back(self):
        res = self.client.patch(PREFS_URL, {"sound_prefs": {"social": {"sound": "gentle", "vibrate": False}}}, format="json")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["sound_prefs"], {"social": {"sound": "gentle", "vibrate": False}})
        res = self.client.patch(PREFS_URL, {"sound_prefs": {"classes": {"sound": "silent"}}}, format="json")
        self.assertEqual(set(res.data["sound_prefs"]), {"social", "classes"})  # merged, not replaced
        res = self.client.patch(PREFS_URL, {"sound_prefs": {"social": None}}, format="json")
        self.assertEqual(set(res.data["sound_prefs"]), {"classes"})
        self.assertEqual(NotificationPreference.objects.get(user=self.student).sound_prefs, {"classes": {"sound": "silent"}})

    def test_invalid_is_400(self):
        res = self.client.patch(PREFS_URL, {"sound_prefs": {"calls": {"sound": "silent"}}}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("sound_prefs", res.data.get("errors") or res.data)
        res = self.client.patch(PREFS_URL, {"sound_prefs": {"bogus": {}}}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_get_exposes_channels_and_history_days(self):
        res = self.client.get(PREFS_URL)
        self.assertEqual(res.data["history_days"], 30)
        by = {c["channel"]: c["can_silence"] for c in res.data["sound_channels"]}
        self.assertFalse(by["calls"])
        self.assertFalse(by["notifications"])
        self.assertTrue(by["social"])

    def test_other_prefs_patch_does_not_touch_sound_prefs(self):
        self.client.patch(PREFS_URL, {"sound_prefs": {"social": {"vibrate": False}}}, format="json")
        res = self.client.patch(PREFS_URL, {"digest_frequency": "daily"}, format="json")
        self.assertEqual(res.data["sound_prefs"], {"social": {"vibrate": False}})

    def test_users_are_isolated(self):
        self.client.patch(PREFS_URL, {"sound_prefs": {"social": {"vibrate": False}}}, format="json")
        other = APIClient()
        other.force_authenticate(self.other_student)
        self.assertEqual(other.get(PREFS_URL).data["sound_prefs"], {})


# ---------------------------------------------------------------------------------------------------------------------
class RowPushIntegrationTests(CoreTestBase):
    """send_push_for_notification: scope mute stops the push (and the N6 cap slot), sound / vibration ride in `data`."""

    def _send(self, row):
        from message import push_utils

        with patch.object(push_utils, "send_rich_push") as send:
            push_utils.send_push_for_notification(row)
        return send

    def test_muted_class_gets_no_push_but_keeps_the_row(self):
        center.set_mute(self.student, "class", "5", duration="8h")
        row = make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"})
        send = self._send(row)
        send.assert_not_called()
        self.assertTrue(Notification.objects.filter(pk=row.pk).exists())

    def test_unmuted_class_still_gets_push(self):
        center.set_mute(self.student, "class", "5", duration="8h")
        send = self._send(make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "6"}))
        send.assert_called_once()

    def test_mute_expiry_restores_push(self):
        NotificationScopeMute.objects.create(
            user=self.student, scope_type="class", scope_id="5", muted_until=timezone.now() - timedelta(seconds=5),
        )
        send = self._send(make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"}))
        send.assert_called_once()

    def test_emergency_ignores_the_mute(self):
        center.set_mute(self.student, "class", "5")
        send = self._send(make_row(self.student, NT.CAMPUS_EMERGENCY, data={"classroom_id": "5"}))
        send.assert_called_once()

    def test_muted_push_does_not_use_a_daily_cap_slot(self):
        center.set_mute(self.student, "class", "5")
        with patch("message.push_utils.reminders.claim_slot", return_value=False) as claim:
            self._send(make_row(self.student, NT.SESSION_LIVE, data={"classroom_id": "5"}))
        claim.assert_not_called()

    def test_sound_choice_is_shipped_in_push_data(self):
        pref = NotificationPreference.for_user(self.student)
        pref.sound_prefs = {"social": {"sound": "gentle", "vibrate": False}}
        pref.save()
        send = self._send(make_row(self.student, NT.POST_LIKED, data={"post_id": 3}))
        data = send.call_args.kwargs["data"]
        self.assertEqual(data["snd"], "gentle")
        self.assertEqual(data["vib"], "0")

    def test_default_sound_adds_no_keys(self):
        send = self._send(make_row(self.student, NT.POST_LIKED, data={"post_id": 3}))
        data = send.call_args.kwargs["data"]
        self.assertNotIn("snd", data)
        self.assertNotIn("vib", data)


class ChatFanOutTests(CoreTestBase):
    def test_muted_chat_user_keeps_bell_row_but_gets_no_push(self):
        from message import push_utils

        cid = str(uuid.uuid4())
        center.set_mute(self.student, "conversation", cid, duration="8h")
        with patch.object(push_utils, "_send_single_chat_push") as single, \
                patch.object(push_utils, "send_chat_digest_push") as digest, \
                patch.object(push_utils, "_filter_recipients_for_focus", side_effect=lambda ids, **k: ids), \
                patch.object(push_utils, "_filter_recipients_for_restrict", side_effect=lambda ids, *a, **k: ids), \
                patch.object(push_utils, "_split_recipients_for_message_request", side_effect=lambda ids, *_: (ids, [])):
            push_utils.send_chat_message_push(
                [self.student.id, self.other_student.id], "Asha", "hello", "text", cid, str(uuid.uuid4()),
            )
        pushed = [uid for call in single.call_args_list for uid in call.args[0]]
        self.assertEqual(pushed, [str(self.other_student.id)])
        digest.assert_not_called()
        self.assertTrue(Notification.objects.filter(recipient=self.student, notif_type=NT.CHAT_MESSAGE).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.other_student, notif_type=NT.CHAT_MESSAGE).exists())


class LiveAudienceTests(CoreTestBase):
    def test_split_push_audience_puts_class_muted_users_in_off(self):
        from tuitionclass.live_push import split_push_audience

        center.set_mute(self.student, "class", "5")
        push, quiet, off = split_push_audience([self.student.id, self.other_student.id], NT.SESSION_LIVE, classroom_id=5)
        self.assertEqual(push, [self.other_student.id])
        self.assertEqual(off, [self.student.id])

    def test_without_classroom_id_nothing_changes(self):
        from tuitionclass.live_push import split_push_audience

        center.set_mute(self.student, "class", "5")
        push, _quiet, off = split_push_audience([self.student.id], NT.SESSION_LIVE)
        self.assertEqual((push, off), ([self.student.id], []))


class MigrationFilePresenceTests(SimpleTestCase):
    def test_migration_0033_follows_0032(self):
        from importlib import import_module

        mod = import_module("core.migrations.0033_notification_center_n9")
        self.assertIn(("core", "0032_channel_policy_n8"), mod.Migration.dependencies)


# ---------------------------------------------------------------------------------------------------------------------
# N9b - campus section mute + "hide from the bell too"   (NOT RUN)
class SectionScopeTests(SimpleTestCase):
    def test_section_id_in_data_is_a_scope(self):
        self.assertEqual(center.scopes_for("x", {"section_id": "7", "campus_id": "2"}), [("section", "7")])

    def test_section_comes_after_class_and_chat_before_post(self):
        out = center.scopes_for("x", {"conversation_id": "c1", "section_id": 7, "post_id": 9}, classroom_id=4)
        self.assertEqual(out, [("conversation", "c1"), ("class", "4"), ("section", "7"), ("post", "9")])

    def test_blank_section_gives_nothing(self):
        self.assertEqual(center.scopes_for("x", {"section_id": ""}), [])
        self.assertEqual(center.scopes_for("x", {"section_id": None}), [])

    def test_section_is_an_accepted_scope_type(self):
        self.assertEqual(center.clean_scope("Section", " 7 "), ("section", "7"))
        self.assertIn("section", center.SCOPE_TYPES)


class SectionMuteDbTests(CoreTestBase):
    def test_section_mute_silences_campus_push_but_not_emergency(self):
        center.set_mute(self.student, "section", "7", duration="8h", label="10-A")
        self.assertTrue(center.push_muted_for_row(make_row(self.student, NT.CAMPUS_RESOURCE_POSTED, data={"section_id": "7"})))
        self.assertTrue(center.push_muted_for_row(make_row(self.student, NT.CAMPUS_NOTE_POSTED, data={"section_id": "7"})))
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.CAMPUS_RESOURCE_POSTED, data={"section_id": "8"})))
        self.assertFalse(center.push_muted_for_row(make_row(self.student, NT.CAMPUS_EMERGENCY, data={"section_id": "7"})))

    def test_hide_bell_defaults_to_false_and_none_keeps_the_value(self):
        row, _ = center.set_mute(self.student, "section", "7")
        self.assertFalse(row.hide_bell)
        row, _ = center.set_mute(self.student, "section", "7", hide_bell=True)
        self.assertTrue(row.hide_bell)
        row, _ = center.set_mute(self.student, "section", "7", duration="1h")  # hide_bell=None -> unchanged
        self.assertTrue(row.hide_bell)
        row, _ = center.set_mute(self.student, "section", "7", hide_bell=False)
        self.assertFalse(row.hide_bell)
        self.assertEqual(center.mute_dict(row)["hide_bell"], False)

    def test_bell_hidden_only_for_hide_bell_mutes_and_one_query(self):
        center.set_mute(self.student, "section", "7", hide_bell=True)
        center.set_mute(self.other_student, "section", "7")  # push-only mute
        with self.assertNumQueries(1):
            got = center.bell_hidden_user_ids(
                [self.student.id, self.other_student.id], NT.CAMPUS_RESOURCE_POSTED, {"section_id": "7"}
            )
        self.assertEqual(got, {self.student.id})

    def test_bell_hidden_ignores_expired_locked_and_unscoped(self):
        center.set_mute(self.student, "section", "7", hide_bell=True, duration="1h", now=timezone.now() - timedelta(hours=3))
        self.assertEqual(center.bell_hidden_user_ids([self.student.id], NT.CAMPUS_RESOURCE_POSTED, {"section_id": "7"}), set())
        center.set_mute(self.student, "section", "7", hide_bell=True)
        self.assertEqual(center.bell_hidden_user_ids([self.student.id], NT.CAMPUS_EMERGENCY, {"section_id": "7"}), set())
        self.assertEqual(center.bell_hidden_user_ids([self.student.id], NT.CAMPUS_RESOURCE_POSTED, {}), set())
        self.assertEqual(center.bell_hidden_user_ids([], NT.CAMPUS_RESOURCE_POSTED, {"section_id": "7"}), set())

    def test_bell_hidden_fails_open(self):
        with patch.object(center, "active_mutes_q", side_effect=RuntimeError("db down")):
            self.assertEqual(
                center.bell_hidden_user_ids([self.student.id], NT.CAMPUS_RESOURCE_POSTED, {"section_id": "7"}), set()
            )

    def test_create_notification_writes_no_row_when_bell_is_hidden(self):
        from .services import create_notification

        center.set_mute(self.student, "section", "7", hide_bell=True)
        self.assertIsNone(create_notification(self.student, NT.CAMPUS_RESOURCE_POSTED, "t", "m", data={"section_id": "7"}))
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)
        # another section, and a push-only mute, still write the row
        self.assertIsNotNone(create_notification(self.student, NT.CAMPUS_RESOURCE_POSTED, "t", "m", data={"section_id": "8"}))
        center.set_mute(self.student, "section", "9")
        self.assertIsNotNone(create_notification(self.student, NT.CAMPUS_RESOURCE_POSTED, "t", "m", data={"section_id": "9"}))

    def test_create_notification_rows_skips_only_hidden_recipients(self):
        from .tasks import create_notification_rows

        center.set_mute(self.student, "section", "7", hide_bell=True)
        failed = create_notification_rows(
            [self.student.id, self.other_student.id], NT.CAMPUS_RESOURCE_POSTED, "t", "m", {"section_id": "7"}
        )
        self.assertEqual(failed, [])
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)
        self.assertEqual(Notification.objects.filter(recipient=self.other_student).count(), 1)


class SectionMuteApiTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_post_section_with_hide_bell_and_list_shows_it(self):
        res = self.client.post(MUTES_URL, {"scope_type": "section", "scope_id": 7, "duration": "24h", "hide_bell": True, "label": "10-A"}, format="json")
        self.assertEqual(res.status_code, 201)
        self.assertEqual((res.data["scope_type"], res.data["hide_bell"]), ("section", True))
        got = self.client.get(MUTES_URL).data["results"]
        self.assertEqual([(r["scope_type"], r["hide_bell"]) for r in got], [("section", True)])

    def test_hide_bell_text_values_and_missing(self):
        self.client.post(MUTES_URL, {"scope_type": "section", "scope_id": 7, "hide_bell": "true"}, format="json")
        self.assertTrue(NotificationScopeMute.objects.get(user=self.student).hide_bell)
        self.client.post(MUTES_URL, {"scope_type": "section", "scope_id": 7, "duration": "1h"}, format="json")  # missing -> keep
        self.assertTrue(NotificationScopeMute.objects.get(user=self.student).hide_bell)
        self.client.post(MUTES_URL, {"scope_type": "section", "scope_id": 7, "hide_bell": "0"}, format="json")
        self.assertFalse(NotificationScopeMute.objects.get(user=self.student).hide_bell)


class MigrationFile0036Tests(SimpleTestCase):
    def test_migration_0036_follows_0035(self):
        from importlib import import_module

        mod = import_module("core.migrations.0036_scope_mute_section_hide_bell")
        self.assertIn(("core", "0035_purchase_receipt_notif_type"), mod.Migration.dependencies)
