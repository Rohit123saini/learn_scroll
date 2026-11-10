"""R5 / S3b - new-device sign-in PUSH (next to the existing e-mail + audit row).
(NOT RUN.)  python manage.py test login.tests_new_device_push

Covers: copy, the locked / urgent / routed NotifType, one push per device (dedupe), the one `login_alerts` switch for
push AND mail, first login silent, a push failure never breaks a login, row creation through the real worker.
"""
from unittest import mock

from django.core import mail
from django.core.cache import cache

from core.models import Notification, NotificationPreference
from core.notification_routes import route_for
from core.tasks import create_notification_rows

from . import login_alerts
from .tests_two_factor import PW, TwoFactorBase, _device_headers

NT = Notification.NotifType
_PATCH = "core.async_utils.dispatch_after_commit"


class NewDeviceTypeTests(TwoFactorBase):
    def test_type_is_locked_so_it_can_not_be_muted_per_type(self):
        self.assertIn(NT.NEW_DEVICE_LOGIN, NotificationPreference.NEVER_MUTE_TYPES)
        self.assertLess(len(NT.NEW_DEVICE_LOGIN.value), 30)

    def test_type_is_urgent_and_in_the_safety_group(self):
        from core import notification_priority as prio

        self.assertEqual(prio.priority_for(NT.NEW_DEVICE_LOGIN.value), prio.URGENT)
        self.assertIn(NT.NEW_DEVICE_LOGIN.value, NotificationPreference.preference_groups()["safety"])

    def test_push_uses_the_high_importance_default_channel_never_silent(self):
        from message.push_channels import CHANNEL_DEFAULT, channel_for_notif_type

        self.assertEqual(channel_for_notif_type(NT.NEW_DEVICE_LOGIN.value), CHANNEL_DEFAULT)

    def test_tap_opens_devices_and_sessions_even_without_any_payload(self):
        self.assertEqual(route_for(NT.NEW_DEVICE_LOGIN.value, {}).route, "sessions")
        self.assertEqual(route_for(NT.NEW_DEVICE_LOGIN.value, {"device_name": "Pixel"}, in_app=True).route, "sessions")


class PushCopyTests(TwoFactorBase):
    def test_copy_names_the_device_and_the_time(self):
        title, body = login_alerts.build_push("Pixel 7", "1.2.3.4", "27 Oct 2026, 21:00")
        self.assertIn("sign-in", title.lower())
        self.assertIn("Pixel 7", body)
        self.assertIn("27 Oct 2026, 21:00", body)
        self.assertNotIn("1.2.3.4", body)  # the IP stays in the row data, not in a lock-screen text

    def test_unknown_device_has_a_readable_fallback(self):
        _, body = login_alerts.build_push("", "", "now")
        self.assertIn("unknown device", body)


class NewDevicePushFlowTests(TwoFactorBase):
    def _login_from(self, device_id):
        return self.client.post(
            "/login/", {"username": "sec", "password": PW}, format="json", **_device_headers(device_id)
        )

    def test_first_ever_login_sends_no_push(self):
        with mock.patch(_PATCH) as dispatch:
            self._login_from("device-aaaaaaaa")
        dispatch.assert_not_called()

    def test_new_device_queues_one_push_for_the_account_owner(self):
        self._login_from("device-aaaaaaaa")
        with mock.patch(_PATCH) as dispatch:
            res = self._login_from("device-bbbbbbbb")
        self.assertEqual(res.status_code, 200)
        dispatch.assert_called_once()
        args = dispatch.call_args.args
        self.assertEqual(args[1], [self.user.pk])
        self.assertEqual(args[2], NT.NEW_DEVICE_LOGIN.value)
        self.assertTrue(args[-1])  # push=True
        data = args[5]
        self.assertEqual(data["kind"], "new_device")
        self.assertEqual(data["type"], NT.NEW_DEVICE_LOGIN.value)
        self.assertEqual(len(mail.outbox), 1)  # e-mail still goes out as before

    def test_same_new_device_is_announced_once_inside_the_window(self):
        self._login_from("device-aaaaaaaa")
        info = {"device_id": "device-bbbbbbbb", "device_name": "Pixel", "ip_address": "1.1.1.1"}
        with mock.patch(_PATCH) as dispatch:
            self.assertTrue(login_alerts.push_new_device(self.user, info, method="password", when_text="now"))
            self.assertFalse(login_alerts.push_new_device(self.user, info, method="otp", when_text="now"))
        dispatch.assert_called_once()

    def test_alerts_off_means_no_push_and_no_mail_but_the_audit_row_stays(self):
        from user_profile.models import UserPreference

        pref = UserPreference.for_user(self.user)
        pref.login_alerts = False
        pref.save()
        self._login_from("device-aaaaaaaa")
        with mock.patch(_PATCH) as dispatch:
            self._login_from("device-bbbbbbbb")
        dispatch.assert_not_called()
        self.assertEqual(len(mail.outbox), 0)

    def test_push_failure_never_breaks_the_login_and_the_mail_still_goes(self):
        self._login_from("device-aaaaaaaa")
        with mock.patch(_PATCH, side_effect=RuntimeError("broker down")):
            res = self._login_from("device-bbbbbbbb")
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_account_without_email_still_gets_the_push(self):
        self.user.email = ""
        self.user.save()
        self._login_from("device-aaaaaaaa")
        with mock.patch(_PATCH) as dispatch:
            self._login_from("device-bbbbbbbb")
        dispatch.assert_called_once()
        self.assertEqual(len(mail.outbox), 0)


class NewDeviceRowTests(TwoFactorBase):
    def test_worker_creates_the_bell_row_with_the_sessions_route(self):
        cache.clear()
        failed = create_notification_rows(
            [self.user.pk], NT.NEW_DEVICE_LOGIN.value, "New sign-in to your account", "Signed in from Pixel.",
            {"kind": "new_device", "device_name": "Pixel"}, push=False,
        )
        self.assertEqual(failed, [])
        row = Notification.objects.get(recipient=self.user, notif_type=NT.NEW_DEVICE_LOGIN.value)
        self.assertEqual(route_for(row.notif_type, row.data or {}, in_app=True).route, "sessions")
