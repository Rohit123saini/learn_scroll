"""N1 - per-type x per-channel notification matrix (NOT RUN). python manage.py test core.tests_notification_matrix"""
from unittest.mock import patch

from rest_framework import status
from rest_framework.test import APIClient

from .models import Notification, NotificationPreference
from .services import create_bulk_notifications, create_notification, in_app_allowed
from .tests import CoreTestBase

NT = Notification.NotifType
URL = "/core/notification-preferences/me/"


class MatrixModelTests(CoreTestBase):
    def pref(self, **kw):
        p = NotificationPreference.for_user(self.student)
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return p

    def test_default_everything_allowed(self):
        p = self.pref()
        self.assertEqual(p.allowed_channels_for(NT.POST_LIKED), ["push", "email"])
        self.assertTrue(p.in_app_allowed(NT.POST_LIKED))

    def test_override_switches_one_channel_off_for_one_type_only(self):
        p = self.pref(channel_overrides={NT.POST_LIKED: {"email": False}})
        self.assertEqual(p.allowed_channels_for(NT.POST_LIKED), ["push"])
        self.assertEqual(p.allowed_channels_for(NT.POST_COMMENTED), ["push", "email"])

    def test_override_cannot_switch_on_a_channel_whose_master_is_off(self):
        p = self.pref(sms_enabled=False, channel_overrides={NT.POST_LIKED: {"sms": True}})
        self.assertNotIn("sms", p.allowed_channels_for(NT.POST_LIKED))

    def test_master_push_off_wins(self):
        p = self.pref(push_enabled=False)
        self.assertNotIn("push", p.allowed_channels_for(NT.POST_LIKED))

    def test_locked_type_ignores_push_and_in_app_off_but_honours_email(self):
        p = self.pref(channel_overrides={NT.CAMPUS_EMERGENCY: {"push": False, "in_app": False, "email": False}},
                      muted_types=[NT.CAMPUS_EMERGENCY])
        self.assertIn("push", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))
        self.assertTrue(p.in_app_allowed(NT.CAMPUS_EMERGENCY))
        self.assertNotIn("email", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))

    def test_locked_push_still_needs_master_toggle(self):
        p = self.pref(push_enabled=False)
        self.assertNotIn("push", p.allowed_channels_for(NT.CAMPUS_EMERGENCY))

    def test_types_blocked_for_email(self):
        p = self.pref(muted_types=[NT.MENTION], channel_overrides={NT.POST_LIKED: {"email": False, "push": True}})
        self.assertEqual(set(p.types_blocked_for("email")), {NT.MENTION.value, NT.POST_LIKED.value})
        self.assertEqual(p.types_blocked_for("push"), [NT.MENTION.value])

    def test_groups_cover_every_type_exactly_once(self):
        groups = Notification.preference_groups()
        flat = [t for types in groups.values() for t in types]
        self.assertEqual(sorted(flat), sorted(Notification.NotifType.values))
        self.assertEqual(len(flat), len(set(flat)))

    def test_locked_types_are_a_subset_of_real_types(self):
        self.assertTrue(Notification.locked_types())
        self.assertTrue(Notification.locked_types() <= set(Notification.NotifType.values))


class InAppEnforcementTests(CoreTestBase):
    def off(self, notif_type):
        p = NotificationPreference.for_user(self.student)
        p.channel_overrides = {notif_type.value: {"in_app": False}}
        p.save()

    def test_create_notification_skips_when_in_app_off(self):
        self.off(NT.POST_LIKED)
        self.assertIsNone(create_notification(self.student, NT.POST_LIKED, "x"))
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)
        self.assertIsNotNone(create_notification(self.student, NT.POST_COMMENTED, "y"))

    def test_other_users_unaffected(self):
        self.off(NT.POST_LIKED)
        self.assertIsNotNone(create_notification(self.other_student, NT.POST_LIKED, "x"))

    def test_bulk_drops_only_opted_out_recipients(self):
        self.off(NT.NOTICE_POSTED)
        create_bulk_notifications([self.student, self.other_student], NT.NOTICE_POSTED, "t")
        self.assertEqual(Notification.objects.filter(notif_type=NT.NOTICE_POSTED).count(), 1)
        self.assertEqual(Notification.objects.get(notif_type=NT.NOTICE_POSTED).recipient_id, self.other_student.id)

    def test_locked_type_always_creates_row(self):
        p = NotificationPreference.for_user(self.student)
        p.channel_overrides = {NT.CAMPUS_EMERGENCY.value: {"in_app": False}}
        p.save()
        self.assertTrue(in_app_allowed(self.student, NT.CAMPUS_EMERGENCY))


class MatrixApiTests(CoreTestBase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

    def stored(self):
        return NotificationPreference.for_user(self.student).channel_overrides

    def test_get_exposes_groups_and_locked_types(self):
        r = self.client.get(URL)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["channel_overrides"], {})
        keys = [g["key"] for g in r.data["groups"]]
        self.assertIn("social", keys)
        self.assertIn(NT.CAMPUS_EMERGENCY.value, r.data["locked_types"])

    def test_patch_per_type_override_merges_and_null_clears(self):
        self.client.patch(URL, {"channel_overrides": {"post_liked": {"push": False}}}, format="json")
        self.client.patch(URL, {"channel_overrides": {"post_liked": {"email": False}}}, format="json")
        self.assertEqual(self.stored(), {"post_liked": {"push": False, "email": False}})
        self.client.patch(URL, {"channel_overrides": {"post_liked": {"push": None}}}, format="json")
        self.assertEqual(self.stored(), {"post_liked": {"email": False}})
        self.client.patch(URL, {"channel_overrides": {"post_liked": {"email": True}}}, format="json")
        self.assertEqual(self.stored(), {})

    def test_group_override_expands_to_all_types_and_skips_locked_push(self):
        r = self.client.patch(URL, {"group_overrides": {"safety": {"push": False, "email": False}}}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        stored = self.stored()
        self.assertEqual(stored[NT.CAMPUS_EMERGENCY.value], {"email": False})  # push lock respected
        self.assertNotIn("push", stored[NT.COPYRIGHT_NOTICE.value])

    def test_group_override_social_email_off(self):
        self.client.patch(URL, {"group_overrides": {"social": {"email": False}}}, format="json")
        stored = self.stored()
        self.assertEqual(stored[NT.POST_LIKED.value], {"email": False})
        self.assertNotIn(NT.CHAT_MESSAGE.value, stored)

    def test_reset(self):
        self.client.patch(URL, {"group_overrides": {"social": {"email": False}}}, format="json")
        r = self.client.patch(URL, {"reset_channel_overrides": True}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(self.stored(), {})

    def test_validation(self):
        for body in (
            {"channel_overrides": {"nope": {"push": False}}},
            {"channel_overrides": {"post_liked": {"fax": False}}},
            {"channel_overrides": {"post_liked": {"push": "maybe"}}},
            {"group_overrides": {"nope": {"push": False}}},
            {"group_overrides": {"social": {"fax": False}}},
        ):
            r = self.client.patch(URL, body, format="json")
            self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST, body)

    def test_cannot_touch_another_users_overrides(self):
        other = NotificationPreference.for_user(self.other_student)
        self.client.patch(URL, {"group_overrides": {"social": {"push": False}}}, format="json")
        other.refresh_from_db()
        self.assertEqual(other.channel_overrides, {})


class LegacyMuteFoldTests(CoreTestBase):
    """N1b - `muted_types` and the matrix are ONE source of truth: touching a muted type in the matrix writes the mute
    out as explicit overrides (push / email / sms / whatsapp off, bell row stays) and removes it from muted_types."""

    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)
        p = NotificationPreference.for_user(self.student)
        p.muted_types = [NT.POST_LIKED.value, NT.CONTENT_DIGEST.value]
        p.save()

    def pref(self):
        return NotificationPreference.objects.get(user=self.student)

    def test_type_patch_folds_the_legacy_mute_and_the_asked_channel_wins(self):
        r = self.client.patch(URL, {"channel_overrides": {"post_liked": {"push": True}}}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        p = self.pref()
        self.assertEqual(p.channel_overrides, {"post_liked": {"email": False, "sms": False, "whatsapp": False}})
        self.assertEqual(p.muted_types, [NT.CONTENT_DIGEST.value])  # the digest switch is not a matrix row
        self.assertEqual(p.allowed_channels_for(NT.POST_LIKED), ["push"])
        self.assertTrue(p.in_app_allowed(NT.POST_LIKED))

    def test_group_patch_folds_every_muted_type_of_that_group(self):
        self.client.patch(URL, {"group_overrides": {"social": {"in_app": False}}}, format="json")
        p = self.pref()
        self.assertEqual(p.channel_overrides[NT.POST_LIKED.value]["in_app"], False)
        self.assertEqual(p.channel_overrides[NT.POST_LIKED.value]["push"], False)
        self.assertNotIn(NT.POST_LIKED.value, p.muted_types)

    def test_untouched_muted_type_stays_muted(self):
        self.client.patch(URL, {"channel_overrides": {"post_commented": {"email": False}}}, format="json")
        self.assertIn(NT.POST_LIKED.value, self.pref().muted_types)

    def test_reset_clears_legacy_mutes_but_keeps_the_digest_switch(self):
        self.client.patch(URL, {"reset_channel_overrides": True}, format="json")
        p = self.pref()
        self.assertEqual(p.channel_overrides, {})
        self.assertEqual(p.muted_types, [NT.CONTENT_DIGEST.value])
        self.assertEqual(p.allowed_channels_for(NT.POST_LIKED), ["push", "email"])

    def test_plain_muted_types_patch_still_works_for_old_clients(self):
        r = self.client.patch(URL, {"muted_types": [NT.POST_COMMENTED.value]}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(self.pref().muted_types, [NT.POST_COMMENTED.value])
        self.assertEqual(self.pref().channel_overrides, {})

    def test_get_exposes_type_labels_for_every_type(self):
        labels = self.client.get(URL).data["type_labels"]
        self.assertEqual(set(labels), set(Notification.NotifType.values))
        self.assertTrue(all(labels.values()))


class InAppBlockedIdsTests(CoreTestBase):
    def test_one_query_for_the_whole_batch(self):
        from .services import in_app_blocked_ids

        for u in (self.student, self.other_student):
            NotificationPreference.for_user(u)
        p = NotificationPreference.for_user(self.student)
        p.channel_overrides = {NT.POST_LIKED.value: {"in_app": False}}
        p.save()
        with self.assertNumQueries(1):
            blocked = in_app_blocked_ids([self.student.id, self.other_student.id], NT.POST_LIKED.value)
        self.assertEqual(blocked, {self.student.id})
        self.assertEqual(in_app_blocked_ids([self.student.id], NT.POST_COMMENTED.value), set())
        self.assertEqual(in_app_blocked_ids([self.student.id], NT.CAMPUS_EMERGENCY.value), set())

    def test_followers_fan_out_respects_the_in_app_chip(self):
        p = NotificationPreference.for_user(self.student)
        p.channel_overrides = {NT.NEW_POST_FROM_FOLLOWED.value: {"in_app": False}}
        p.save()
        create_bulk_notifications([self.student, self.other_student], NT.NEW_POST_FROM_FOLLOWED, "new post")
        rows = Notification.objects.filter(notif_type=NT.NEW_POST_FROM_FOLLOWED)
        self.assertEqual([r.recipient_id for r in rows], [self.other_student.id])
