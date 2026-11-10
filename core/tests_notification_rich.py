"""N7 - rich notifications: absolute image / avatar URLs, Mark as read + Join now buttons, iOS categories (+ Flutter parity),
group key, icon badge, badge-only push, mark-read endpoint.
(NOT RUN.)  python manage.py test core.tests_notification_rich"""
import re
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIClient

from . import notification_rich as rich
from .models import Notification
from .services import build_push_payload
from .tests import CoreTestBase

NT = Notification.NotifType
REPO = Path(__file__).resolve().parents[2]  # same convention as tests_notification_routes.py
DART = REPO / "lib" / "lib" / "notifications" / "notification_rich_logic.dart"

REPLY = {"id": "reply_message", "label": "Reply", "input": True}
ACCEPT = {"id": "confirm_follow", "label": "Accept"}
DECLINE = {"id": "delete_follow", "label": "Decline"}


def ids(actions):
    return [a["id"] for a in actions]


def make_row(user, notif_type=NT.GENERIC, **fields):
    data = fields.pop("data", {})
    return Notification.objects.create(recipient=user, notif_type=notif_type, title="t", message="m", data=data, **fields)


# ---------------------------------------------------------------------------------------------------------------------
class AbsoluteUrlTests(SimpleTestCase):
    @override_settings(MEDIA_ABSOLUTE_BASE_URL="https://cdn.example.com/", SITE_URL="https://site.example.com")
    def test_relative_path_joins_media_base(self):
        self.assertEqual(rich.absolute_url("/media/a.jpg"), "https://cdn.example.com/media/a.jpg")

    @override_settings(MEDIA_ABSOLUTE_BASE_URL="", SITE_URL="https://site.example.com/")
    def test_falls_back_to_site_url(self):
        self.assertEqual(rich.absolute_url("/media/a.jpg"), "https://site.example.com/media/a.jpg")

    @override_settings(MEDIA_ABSOLUTE_BASE_URL="", SITE_URL="")
    def test_relative_without_any_base_is_dropped(self):
        self.assertIsNone(rich.absolute_url("/media/a.jpg"))

    def test_absolute_stays_and_junk_is_none(self):
        self.assertEqual(rich.absolute_url(" https://x.com/a.png "), "https://x.com/a.png")
        for bad in (None, "", "a.jpg", "//evil.com/a.jpg", "ftp://x/a.jpg", 5):
            self.assertIsNone(rich.absolute_url(bad), bad)

    def test_thumbnail_first_usable_key_wins(self):
        raw = {"media_url": "nope", "thumbnail_url": "https://x.com/t.jpg", "cover_url": "https://x.com/c.jpg"}
        self.assertEqual(rich.thumbnail_url(raw), "https://x.com/t.jpg")
        self.assertIsNone(rich.thumbnail_url({"media_url": "bare.jpg"}))
        self.assertIsNone(rich.thumbnail_url("not a dict"))

    def test_avatar_from_row_data_then_actors_preview(self):
        self.assertEqual(rich.avatar_url({"actor_photo": "https://x.com/p.jpg"}), "https://x.com/p.jpg")
        raw = {"actors_preview": [{"photo": "https://x.com/first.jpg"}, {"photo": "https://x.com/second.jpg"}]}
        self.assertEqual(rich.avatar_url(raw), "https://x.com/first.jpg")

    def test_avatar_never_raises_without_actor(self):
        self.assertIsNone(rich.avatar_url({}))
        self.assertIsNone(rich.avatar_url(None))
        self.assertIsNone(rich.avatar_url({"actors_preview": "oops"}))


# ---------------------------------------------------------------------------------------------------------------------
class ActionRulesTests(CoreTestBase):
    def test_informational_push_gets_mark_read(self):
        out = rich.finalize_actions([], NT.POST_LIKED.value, {})
        self.assertEqual(ids(out), ["mark_read"])

    def test_reply_push_gets_mark_read_after_reply(self):
        out = rich.finalize_actions([dict(REPLY)], NT.CHAT_MESSAGE.value, {})
        self.assertEqual(ids(out), ["reply_message", "mark_read"])

    def test_decision_buttons_never_get_mark_read(self):
        out = rich.finalize_actions([dict(ACCEPT), dict(DECLINE)], NT.FOLLOW_REQUEST_RECEIVED.value, {})
        self.assertEqual(ids(out), ["confirm_follow", "delete_follow"])
        out = rich.finalize_actions([{"id": "follow_back", "label": "Follow back"}], NT.FOLLOW_REQUEST_ACCEPTED.value, {})
        self.assertEqual(ids(out), ["follow_back"])

    def test_reminder_never_gets_mark_read(self):
        snooze = [{"id": "snooze_reminder", "label": "Snooze"}, {"id": "turn_off_type", "label": "Turn off"}]
        out = rich.finalize_actions(snooze, NT.STREAK_AT_RISK.value, {})
        self.assertEqual(ids(out), ["snooze_reminder", "turn_off_type"])

    def test_locked_type_never_gets_mark_read(self):
        locked = sorted(Notification.locked_types())
        self.assertTrue(locked, "expected at least one locked (never-mute) type")
        for t in locked:
            self.assertEqual(rich.finalize_actions([], t, {}), [], t)

    def test_already_present_mark_read_is_not_doubled(self):
        out = rich.finalize_actions([{"id": "mark_read", "label": "x"}], NT.POST_LIKED.value, {})
        self.assertEqual(ids(out), ["mark_read"])

    @override_settings(NOTIF_PUSH_MARK_READ_BUTTON=False)
    def test_ops_switch_turns_mark_read_off(self):
        self.assertEqual(rich.finalize_actions([dict(REPLY)], NT.CHAT_MESSAGE.value, {}), [dict(REPLY)])
        self.assertEqual(rich.finalize_actions([], NT.POST_LIKED.value, {}), [])

    def test_input_list_is_not_modified(self):
        original = [dict(REPLY)]
        rich.finalize_actions(original, NT.CHAT_MESSAGE.value, {})
        self.assertEqual(original, [dict(REPLY)])

    def test_never_more_than_three_buttons(self):
        many = [{"id": f"a{i}", "label": "x"} for i in range(6)]
        self.assertEqual(len(rich.finalize_actions(many, NT.GENERIC.value, {})), rich.MAX_ACTIONS)


class JoinNowTests(CoreTestBase):
    def test_live_class_with_class_route_gets_join_now_first(self):
        for t in (NT.SESSION_REMINDER, NT.SESSION_LIVE):
            out = rich.finalize_actions([], t.value, {"route": "classroom"})
            self.assertEqual(ids(out)[0], "join_now", t)
            if t is NT.SESSION_REMINDER:
                # a reminder type: Snooze / Turn off are its own buttons, so it never gets "Mark as read"
                self.assertNotIn("mark_read", ids(out), t)
            else:
                self.assertIn("mark_read", ids(out), t)

    def test_campus_live_route(self):
        out = rich.finalize_actions([], NT.CAMPUS_SESSION_LIVE.value, {"route": "campusLive"})
        self.assertEqual(ids(out), ["join_now", "mark_read"])

    def test_no_join_now_when_target_is_gone(self):
        for route in ("unavailable", "notifications", "", None):
            out = rich.finalize_actions([], NT.SESSION_LIVE.value, {"route": route})
            self.assertNotIn("join_now", ids(out), route)

    def test_other_types_never_get_join_now(self):
        out = rich.finalize_actions([], NT.POST_LIKED.value, {"route": "classroom"})
        self.assertNotIn("join_now", ids(out))

    def test_join_now_not_duplicated(self):
        out = rich.finalize_actions([{"id": "join_now", "label": "Join now"}], NT.SESSION_LIVE.value, {"route": "classroom"})
        self.assertEqual(ids(out).count("join_now"), 1)


# ---------------------------------------------------------------------------------------------------------------------
class PayloadTests(CoreTestBase):
    @override_settings(MEDIA_ABSOLUTE_BASE_URL="https://cdn.example.com", SITE_URL="")
    def test_relative_thumbnail_is_made_absolute_not_dropped(self):
        row = make_row(self.student, NT.POST_LIKED, data={"media_url": "/media/thumb.jpg"})
        p = build_push_payload(row)
        self.assertEqual(p["image_url"], "https://cdn.example.com/media/thumb.jpg")

    def test_avatar_url_in_payload(self):
        row = make_row(self.student, NT.POST_LIKED, data={"actor_photo": "https://x.com/a.jpg"})
        self.assertEqual(build_push_payload(row)["avatar_url"], "https://x.com/a.jpg")

    def test_no_media_means_no_image_and_no_crash(self):
        p = build_push_payload(make_row(self.student, NT.GENERIC))
        self.assertIsNone(p["image_url"])

    def test_chat_row_payload_has_reply_then_mark_read(self):
        row = make_row(self.student, NT.CHAT_MESSAGE, data={"conversation_id": "c1"})
        self.assertEqual(ids(build_push_payload(row)["actions"]), ["reply_message", "mark_read"])

    def test_follow_request_payload_buttons_unchanged(self):
        row = make_row(self.student, NT.FOLLOW_REQUEST_RECEIVED, data={"follow_id": 7})
        self.assertEqual(ids(build_push_payload(row)["actions"]), ["confirm_follow", "delete_follow"])


# ---------------------------------------------------------------------------------------------------------------------
class CategoryTests(SimpleTestCase):
    def test_every_action_maps_to_a_category_that_contains_it(self):
        for action_id, cat in rich.CATEGORY_BY_ACTION_ID.items():
            self.assertIn(action_id, rich.APNS_CATEGORIES[cat], action_id)

    def test_every_category_button_has_a_category_mapping(self):
        for cat, buttons in rich.APNS_CATEGORIES.items():
            for b in buttons:
                self.assertIn(b, rich.CATEGORY_BY_ACTION_ID, (cat, b))

    def test_category_is_the_first_buttons(self):
        self.assertEqual(rich.category_for_actions([{"id": "join_now"}, {"id": "mark_read"}]), "JOIN_NOW")
        self.assertEqual(rich.category_for_actions([{"id": "reply_comment"}, {"id": "mark_read"}]), "COMMENT_REPLY")
        self.assertEqual(rich.category_for_actions([{"id": "mark_read"}]), "MARK_READ")
        self.assertIsNone(rich.category_for_actions([]))
        self.assertIsNone(rich.category_for_actions([{"id": "unknown"}]))

    def _dart_tables(self):
        if not DART.exists():
            self.skipTest("notification_rich_logic.dart not in this checkout")
        text = DART.read_text(encoding="utf-8")
        cats_block = re.search(r"kPushIosCategories\s*=\s*\{(.*?)\n\};", text, re.S).group(1)
        by_block = re.search(r"kPushIosCategoryByActionId\s*=\s*\{(.*?)\n\};", text, re.S).group(1)
        cats = {
            name: tuple(re.findall(r"'([a-z_]+)'", body))
            for name, body in re.findall(r"'([A-Z_]+)'\s*:\s*\[(.*?)\]", cats_block)
        }
        by = dict(re.findall(r"'([a-z_]+)'\s*:\s*'([A-Z_]+)'", by_block))
        return cats, by

    def test_flutter_categories_match_backend(self):
        cats, by = self._dart_tables()
        self.assertEqual(cats, dict(rich.APNS_CATEGORIES))
        self.assertEqual(by, dict(rich.CATEGORY_BY_ACTION_ID))


# ---------------------------------------------------------------------------------------------------------------------
class GroupAndBadgeTests(SimpleTestCase):
    def test_group_key(self):
        self.assertEqual(rich.group_key("social"), "ls.group.social")
        self.assertEqual(rich.group_key(""), "ls.group.notifications")
        self.assertEqual(rich.group_key(None), "ls.group.notifications")
        self.assertLessEqual(len(rich.group_key("x" * 200)), 64)

    def test_clamp_badge(self):
        self.assertEqual(rich.clamp_badge(-4), 0)
        self.assertEqual(rich.clamp_badge("7"), 7)
        self.assertEqual(rich.clamp_badge("abc"), 0)
        self.assertEqual(rich.clamp_badge(10 ** 9), rich.BADGE_MAX)

    def test_badge_count_none_when_it_cannot_be_computed(self):
        with patch("core.services.unread_badge_count", side_effect=RuntimeError("db down")):
            self.assertIsNone(rich.badge_count_for(1))


class SendRichPushTests(CoreTestBase):
    def test_payload_carries_group_key_avatar_badge_and_category(self):
        from message import push_utils

        with patch.object(push_utils, "_tokens_for_users", return_value=["tok"]), \
                patch.object(push_utils, "_send_multicast") as send:
            push_utils.send_rich_push(
                [self.student.id], "T", "B", actions=[{"id": "join_now", "label": "Join now"}, {"id": "mark_read", "label": "x"}],
                channel_id="classes", avatar_url="https://x.com/a.jpg", badge=4,
            )
        kwargs = send.call_args.kwargs
        data = kwargs["data"]
        self.assertEqual(data["group_key"], "ls.group.classes")
        self.assertEqual(data["avatar_url"], "https://x.com/a.jpg")
        self.assertEqual(data["badge_count"], 4)
        self.assertEqual(kwargs["apns_alert"]["category"], "JOIN_NOW")
        self.assertEqual(kwargs["apns_alert"]["badge"], 4)

    def test_no_badge_means_icon_is_left_alone(self):
        from message import push_utils

        with patch.object(push_utils, "_tokens_for_users", return_value=["tok"]), \
                patch.object(push_utils, "_send_multicast") as send:
            push_utils.send_rich_push([self.student.id], "T", "B")
        self.assertNotIn("badge_count", send.call_args.kwargs["data"])
        self.assertIsNone(send.call_args.kwargs["apns_alert"]["badge"])


class BadgeSyncPushTests(CoreTestBase):
    def _token(self, platform, token):
        from message.models import DeviceToken

        return DeviceToken.objects.create(user=self.student, token=token, platform=platform)

    def test_only_ios_tokens_get_the_badge_only_push(self):
        from message import push_utils

        self._token("ios", "ios-tok")
        self._token("android", "and-tok")
        with patch.object(rich, "badge_count_for", return_value=3), patch.object(push_utils, "_send_multicast") as send:
            push_utils.send_badge_sync_push(self.student.id)
        self.assertEqual(send.call_args.args[0], ["ios-tok"])
        self.assertEqual(send.call_args.kwargs["data"], {"type": "badge_sync", "badge_count": 3})
        self.assertTrue(send.call_args.kwargs["apns_alert"]["badge_only"])
        self.assertEqual(send.call_args.kwargs["apns_alert"]["badge"], 3)

    def test_no_ios_token_sends_nothing(self):
        from message import push_utils

        self._token("android", "and-tok")
        with patch.object(rich, "badge_count_for", return_value=3), patch.object(push_utils, "_send_multicast") as send:
            push_utils.send_badge_sync_push(self.student.id)
        send.assert_not_called()

    def test_unknown_count_never_sends_a_wrong_zero(self):
        from message import push_utils

        self._token("ios", "ios-tok")
        with patch.object(rich, "badge_count_for", return_value=None), patch.object(push_utils, "_send_multicast") as send:
            push_utils.send_badge_sync_push(self.student.id)
        send.assert_not_called()

    def test_never_raises(self):
        from message import push_utils

        with patch.object(rich, "badge_count_for", side_effect=RuntimeError("boom")):
            push_utils.send_badge_sync_push(self.student.id)  # must not raise


# ---------------------------------------------------------------------------------------------------------------------
class MarkReadButtonEndpointTests(CoreTestBase):
    """The push \"Mark as read\" button POSTs core/notifications/{id}/mark-read/ with the user's token."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.student)

    def test_marks_own_row_read_and_syncs_ios_badge(self):
        row = make_row(self.student, NT.POST_LIKED)
        with patch("message.push_utils.send_badge_sync_push") as sync:
            res = self.client.post(f"/core/notifications/{row.id}/mark-read/")
        self.assertEqual(res.status_code, 200)
        row.refresh_from_db()
        self.assertTrue(row.is_read)
        sync.assert_called_once_with(self.student.id)

    def test_second_tap_does_not_resend_badge_push(self):
        row = make_row(self.student, NT.POST_LIKED)
        row.mark_read()
        with patch("message.push_utils.send_badge_sync_push") as sync:
            res = self.client.post(f"/core/notifications/{row.id}/mark-read/")
        self.assertEqual(res.status_code, 200)
        sync.assert_not_called()

    def test_cannot_mark_someone_elses_row(self):
        row = make_row(self.other_student, NT.POST_LIKED)
        res = self.client.post(f"/core/notifications/{row.id}/mark-read/")
        self.assertEqual(res.status_code, 404)
        row.refresh_from_db()
        self.assertFalse(row.is_read)

    def test_badge_push_failure_does_not_fail_the_request(self):
        row = make_row(self.student, NT.POST_LIKED)
        with patch("message.push_utils.send_badge_sync_push", side_effect=RuntimeError("fcm down")):
            res = self.client.post(f"/core/notifications/{row.id}/mark-read/")
        self.assertEqual(res.status_code, 200)

    def test_mark_all_read_syncs_ios_badge(self):
        make_row(self.student, NT.POST_LIKED)
        with patch("message.push_utils.send_badge_sync_push") as sync:
            res = self.client.post("/core/notifications/mark-all-read/")
        self.assertEqual(res.status_code, 200)
        sync.assert_called_with(self.student.id)


# ---------------------------------------------------------------------------------------------------------------------
class ChatSenderAvatarTests(SimpleTestCase):
    """N7 - client-built CHAT notifications get the sender's photo (`avatar_url`) like every other rich push."""

    AVATAR = "https://cdn.example.com/media/asha.jpg"

    def test_avatar_key_added_only_when_sender_has_a_photo(self):
        from message import push_utils

        with patch.object(push_utils, "_chat_sender_avatar", return_value=self.AVATAR):
            self.assertEqual(push_utils._with_chat_avatar({"type": "chat_message"}, 5)["avatar_url"], self.AVATAR)
        with patch.object(push_utils, "_chat_sender_avatar", return_value=None):
            self.assertNotIn("avatar_url", push_utils._with_chat_avatar({"type": "chat_message"}, 5))

    def test_lookup_never_raises(self):
        from message import push_utils

        with patch.object(push_utils.Message.objects, "filter", side_effect=RuntimeError("db down")):
            self.assertIsNone(push_utils._chat_sender_avatar(1))

    def test_single_chat_push_carries_avatar_and_reply_button(self):
        from message import push_utils

        with patch.object(push_utils, "_tokens_for_users", return_value=["tok"]), \
                patch.object(push_utils, "_chat_sender_avatar", return_value=self.AVATAR), \
                patch.object(push_utils, "_send_multicast") as send:
            push_utils._send_single_chat_push([1], "Asha", "hello", 7, 9)
        data = send.call_args.kwargs["data"]
        self.assertEqual(data["avatar_url"], self.AVATAR)
        self.assertEqual(data["type"], "chat_message")
        self.assertIn("reply_message", data["actions"])  # Reply button still rides along
