# core/tests.py
from unittest.mock import patch

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from login.models import User

from .models import Notification, NotificationPreference
from .services import create_bulk_notifications, create_notification


class CoreTestBase(TestCase):
    """Minimal fixtures — core stays app-agnostic, so unlike
    liveclass.tests.LiveClassTestBase this doesn't set up a
    Classroom/ClassSession/ClassPass; individual tests create those
    directly only when a test specifically needs the classroom/session
    FK on Notification."""

    def setUp(self):
        self.student = User.objects.create_user(
            username="student1", password="pass12345", email="student1@example.com"
        )
        self.other_student = User.objects.create_user(
            username="student2", password="pass12345", email="student2@example.com"
        )


# ===========================================================================
# Notification model
# ===========================================================================
class NotificationModelTests(CoreTestBase):
    def test_mark_read_sets_is_read_and_read_at(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Hi",
        )
        self.assertFalse(notification.is_read)
        self.assertIsNone(notification.read_at)

        notification.mark_read()

        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

    def test_mark_read_is_idempotent(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Hi",
        )
        notification.mark_read()
        first_read_at = notification.read_at

        # Calling again shouldn't touch read_at a second time (no-op save
        # skipped entirely once is_read is already True).
        notification.mark_read()
        self.assertEqual(notification.read_at, first_read_at)

    def test_data_field_defaults_to_empty_dict(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.CHAT_MESSAGE, title="New message",
        )
        self.assertEqual(notification.data, {})

    def test_message_app_types_set_matches_new_notif_types(self):
        self.assertEqual(
            Notification.MESSAGE_APP_TYPES,
            {Notification.NotifType.CHAT_MESSAGE, Notification.NotifType.MENTION, Notification.NotifType.INCOMING_CALL},
        )


# ===========================================================================
# NotificationPreference — task 47 regression: same assertions as the
# original liveclass.tests.NotificationPreferenceTests, now exercised
# directly against core.models from the new location.
# ===========================================================================
class NotificationPreferenceTests(CoreTestBase):
    def test_for_user_creates_sane_defaults(self):
        pref = NotificationPreference.for_user(self.student)
        self.assertTrue(pref.push_enabled)
        self.assertTrue(pref.email_enabled)
        self.assertFalse(pref.sms_enabled)
        self.assertFalse(pref.whatsapp_enabled)
        self.assertEqual(pref.digest_frequency, NotificationPreference.DigestFrequency.OFF)

    def test_for_user_is_idempotent(self):
        first = NotificationPreference.for_user(self.student)
        second = NotificationPreference.for_user(self.student)
        self.assertEqual(first.pk, second.pk)

    def test_allowed_channels_respects_toggles(self):
        pref = NotificationPreference.objects.create(
            user=self.student, push_enabled=True, email_enabled=False, sms_enabled=True, whatsapp_enabled=False,
        )
        self.assertEqual(pref.allowed_channels_for(Notification.NotifType.SESSION_LIVE), ["push", "sms"])

    def test_muted_type_returns_no_channels(self):
        pref = NotificationPreference.objects.create(
            user=self.student, muted_types=[Notification.NotifType.NOTICE_POSTED],
        )
        self.assertEqual(pref.allowed_channels_for(Notification.NotifType.NOTICE_POSTED), [])
        self.assertNotEqual(pref.allowed_channels_for(Notification.NotifType.SESSION_LIVE), [])


# ===========================================================================
# create_notification() / create_bulk_notifications()
# ===========================================================================
class CreateNotificationTests(CoreTestBase):
    def test_creates_a_row(self):
        notification = create_notification(
            self.student, Notification.NotifType.GENERIC, "Title", "Body",
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient_id, self.student.id)
        self.assertEqual(notification.title, "Title")
        self.assertEqual(notification.message, "Body")

    def test_accepts_a_raw_recipient_id(self):
        """message/push_utils.py's call-sites only have recipient_ids
        (from _tokens_for_users-style lookups), not hydrated User
        objects — create_notification must accept either."""
        notification = create_notification(
            self.student.id, Notification.NotifType.CHAT_MESSAGE, "New message", "hey",
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient_id, self.student.id)

    def test_stores_data_dict(self):
        notification = create_notification(
            self.student, Notification.NotifType.MENTION, "Mentioned", "text",
            data={"conversation_id": "abc123"},
        )
        self.assertEqual(notification.data, {"conversation_id": "abc123"})

    def test_swallows_exceptions_and_returns_none(self):
        with patch("core.services.Notification.objects.create", side_effect=Exception("db down")):
            result = create_notification(self.student, Notification.NotifType.GENERIC, "Title")
        self.assertIsNone(result)

    def test_never_sends_a_push_itself(self):
        """Regression guard for create_notification()'s core contract:
        it ONLY ever writes a Notification row. If this starts failing,
        someone added a push/email/sms send inside core.services — that
        belongs at the call-site instead, exactly like every existing
        call-site already does it (create_notification(...) alongside a
        separate send_notification(...)/send_push_to_users(...) call)."""
        with patch("message.push_utils._send_multicast") as mock_send_multicast:
            create_notification(self.student, Notification.NotifType.CHAT_MESSAGE, "New message", "hey")
        mock_send_multicast.assert_not_called()

    def test_create_bulk_notifications_dedupes_recipients(self):
        create_bulk_notifications(
            [self.student, self.student, self.student.id, self.other_student],
            Notification.NotifType.NOTICE_POSTED,
            "New notice",
        )
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.other_student).count(), 1)

    def test_create_bulk_notifications_skips_empty_recipients(self):
        create_bulk_notifications([], Notification.NotifType.NOTICE_POSTED, "New notice")
        self.assertEqual(Notification.objects.count(), 0)


# ===========================================================================
# NotificationViewSet / NotificationPreferenceView
# ===========================================================================
class NotificationViewSetTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_unread_count(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="B")
        Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="C", is_read=True,
        )
        # ⚠️ Path assumption: root urlconf mounts core.urls at "core/"
        # (see LearnScroll/urls.py). If that prefix ever changes, this
        # hardcoded path needs updating alongside it.
        response = self.client.get("/core/notifications/unread-count/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 2)

    def test_list_only_returns_own_notifications(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="Mine")
        Notification.objects.create(
            recipient=self.other_student, notif_type=Notification.NotifType.GENERIC, title="Not mine",
        )
        response = self.client.get("/core/notifications/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Mine")

    def test_list_includes_unread_count(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        response = self.client.get("/core/notifications/")
        self.assertIn("unread_count", response.data)
        self.assertEqual(response.data["unread_count"], 1)

    def test_source_filter_splits_message_and_liveclass_types(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.CHAT_MESSAGE, title="Chat")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.SESSION_LIVE, title="Live")

        response = self.client.get("/core/notifications/?source=message")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Chat")
        self.assertEqual(response.data["results"][0]["source"], "message")

        response = self.client.get("/core/notifications/?source=liveclass")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Live")
        self.assertEqual(response.data["results"][0]["source"], "liveclass")

    def test_mark_read(self):
        notification = Notification.objects.create(
            recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A",
        )
        response = self.client.post(f"/core/notifications/{notification.id}/mark-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_read(self):
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="A")
        Notification.objects.create(recipient=self.student, notif_type=Notification.NotifType.GENERIC, title="B")
        response = self.client.post("/core/notifications/mark-all-read/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_read"], 2)
        self.assertEqual(Notification.objects.filter(recipient=self.student, is_read=False).count(), 0)

    def test_cannot_delete_someone_elses_notification(self):
        notification = Notification.objects.create(
            recipient=self.other_student, notif_type=Notification.NotifType.GENERIC, title="Not mine",
        )
        response = self.client.delete(f"/core/notifications/{notification.id}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Notification.objects.filter(pk=notification.pk).exists())


class NotificationPreferenceViewTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def test_get_creates_default_preference(self):
        response = self.client.get("/core/notification-preferences/me/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["push_enabled"])

    def test_patch_updates_preference(self):
        response = self.client.patch("/core/notification-preferences/me/", {"push_enabled": False}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["push_enabled"])
        self.assertFalse(NotificationPreference.for_user(self.student).push_enabled)

    def test_patch_rejects_unknown_muted_type(self):
        response = self.client.patch(
            "/core/notification-preferences/me/", {"muted_types": ["not_a_real_type"]}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)