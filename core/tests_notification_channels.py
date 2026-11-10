"""N8 - e-mail / SMS / WhatsApp channel setup (written, NOT RUN).
python manage.py test core.tests_notification_channels
"""
import re
from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from . import channel_policy as policy
from . import channel_templates as tpl
from .models import ChannelConsent, ChannelSendLog, ChannelSuppression, Notification, NotificationPreference
from .tests import CoreTestBase

NT = Notification.NotifType
LOCMEM = override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://ls.example",
                           DEFAULT_FROM_EMAIL="no-reply@ls.example", NOTIF_WEBHOOK_SECRET="s3cret")


class ChannelBase(CoreTestBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        mail.outbox = []
        self.pref = NotificationPreference.for_user(self.student)

    def verify_email(self):
        self.pref.email_verified_address = self.student.email
        self.pref.email_verified_at = timezone.now()
        self.pref.save()

    def give_phone(self, verified=True, number="+919876543210"):
        self.student.phone = number
        self.student.phone_verified = verified
        self.student.save()


# ------------------------------------------------------------------------------------------------ pure helpers
class AddressTests(ChannelBase):
    def test_normalize(self):
        self.assertEqual(policy.normalize_address("email", " A@B.com "), "a@b.com")
        self.assertEqual(policy.normalize_address("sms", "98765 43210"), "+919876543210")
        self.assertEqual(policy.normalize_address("sms", "+91 98765-43210"), "+919876543210")
        self.assertEqual(policy.normalize_address("sms", ""), "")

    def test_mask(self):
        self.assertEqual(policy.mask_address("email", "asha@gmail.com"), "a***@gmail.com")
        self.assertTrue(policy.mask_address("sms", "+919876543210").endswith("3210"))
        self.assertNotIn("98765", policy.mask_address("sms", "+919876543210"))

    def test_phone_for_reads_real_user_phone_field(self):
        self.give_phone()
        self.assertEqual(policy.phone_for(self.student), "+919876543210")


# ------------------------------------------------------------------------------------------------ decide()
class DecideEmailTests(ChannelBase):
    def test_unverified_email_is_blocked(self):
        d = policy.decide(self.student, "email", NT.POST_COMMENTED)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "email_unverified")

    def test_verified_email_allowed(self):
        self.verify_email()
        d = policy.decide(self.student, "email", NT.POST_COMMENTED)
        self.assertTrue(d.allowed)
        self.assertEqual(d.address, "student1@example.com")

    def test_changing_the_account_email_un_verifies(self):
        self.verify_email()
        self.student.email = "new@example.com"
        self.student.save()
        self.assertFalse(policy.email_verified(self.student))
        self.assertEqual(policy.decide(self.student, "email").reason, "email_unverified")

    def test_master_off_and_type_off(self):
        self.verify_email()
        self.pref.email_enabled = False
        self.pref.save()
        self.assertEqual(policy.decide(self.student, "email").reason, "master_off")
        self.pref.email_enabled = True
        self.pref.channel_overrides = {NT.POST_LIKED: {"email": False}}
        self.pref.save()
        self.assertEqual(policy.decide(self.student, "email", NT.POST_LIKED).reason, "type_off")
        self.assertTrue(policy.decide(self.student, "email", NT.POST_COMMENTED).allowed)

    def test_no_address(self):
        self.student.email = None
        self.student.save()
        self.assertEqual(policy.decide(self.student, "email").reason, "no_address")

    def test_suppressed_address_blocked(self):
        self.verify_email()
        policy.suppress("email", "STUDENT1@example.com", ChannelSuppression.Reason.HARD_BOUNCE)
        # suppress() also switches the master toggle off for every account on that address, and decide() checks the
        # master first -> a normal send says "master_off"; the digest path (respect_master=False) reaches "suppressed"
        self.assertEqual(policy.decide(self.student, "email").reason, "master_off")
        d = policy.decide(self.student, "email", None, respect_master=False, apply_caps=False)
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "suppressed")

    def test_digest_mode_ignores_master_switch_and_caps(self):
        self.verify_email()
        self.pref.email_enabled = False
        self.pref.save()
        d = policy.decide(self.student, "email", None, respect_master=False, apply_caps=False)
        self.assertTrue(d.allowed)

    @override_settings(NOTIF_EMAIL_DAILY_CAP=2)
    def test_daily_cap_and_locked_bypass(self):
        self.verify_email()
        for _ in range(2):
            policy.record_send(self.student, "email", "mention")
        self.assertEqual(policy.decide(self.student, "email", NT.MENTION).reason, "daily_cap")
        self.assertTrue(policy.decide(self.student, "email", NT.CAMPUS_EMERGENCY).allowed)  # locked: never capped

    @override_settings(NOTIF_EMAIL_DAILY_CAP=1)
    def test_old_sends_do_not_count(self):
        self.verify_email()
        row = policy.record_send(self.student, "email", "mention")
        ChannelSendLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(hours=25))
        self.assertTrue(policy.decide(self.student, "email", NT.MENTION).allowed)


class DecidePhoneTests(ChannelBase):
    def enable(self, channel="sms"):
        setattr(self.pref, f"{channel}_enabled", True)
        self.pref.save()

    def test_needs_verified_phone_then_consent(self):
        self.enable()
        self.assertEqual(policy.decide(self.student, "sms").reason, "no_address")
        self.give_phone(verified=False)
        self.assertEqual(policy.decide(self.student, "sms").reason, "phone_unverified")
        self.give_phone(verified=True)
        self.assertEqual(policy.decide(self.student, "sms").reason, "no_consent")
        policy.record_consent(self.student, "sms", True)
        self.assertTrue(policy.decide(self.student, "sms").allowed)

    def test_consent_is_tied_to_the_number(self):
        self.enable()
        self.give_phone()
        policy.record_consent(self.student, "sms", True)
        self.give_phone(number="+919000000001")  # number changed -> must opt in again
        self.assertEqual(policy.decide(self.student, "sms").reason, "no_consent")

    def test_withdrawn_consent_blocks(self):
        self.enable()
        self.give_phone()
        policy.record_consent(self.student, "sms", True)
        policy.record_consent(self.student, "sms", False)
        self.assertEqual(policy.decide(self.student, "sms").reason, "no_consent")

    @override_settings(NOTIF_SMS_DAILY_CAP=1)
    def test_sms_daily_cap(self):
        self.enable()
        self.give_phone()
        policy.record_consent(self.student, "sms", True)
        policy.record_send(self.student, "sms", "mention")
        self.assertEqual(policy.decide(self.student, "sms", NT.MENTION).reason, "daily_cap")

    @override_settings(NOTIF_SMS_GLOBAL_DAILY_CAP=1)
    def test_global_cost_guard(self):
        self.enable()
        self.give_phone()
        policy.record_consent(self.student, "sms", True)
        policy.record_send(self.other_student, "sms", "mention")
        self.assertEqual(policy.decide(self.student, "sms", NT.MENTION).reason, "global_cap")

    def test_quiet_hours_hold_sms_but_not_emergency(self):
        self.enable()
        self.give_phone()
        policy.record_consent(self.student, "sms", True)
        self.pref.dnd_until = timezone.now() + timedelta(hours=1)
        self.pref.save()
        self.assertEqual(policy.decide(self.student, "sms", NT.MENTION).reason, "quiet")
        self.assertTrue(policy.decide(self.student, "sms", NT.CAMPUS_EMERGENCY).allowed)


# ------------------------------------------------------------------------------------------------ enabling + consent
class EnableTests(ChannelBase):
    def test_enable_blockers(self):
        self.assertEqual(policy.enable_blockers(self.student, "sms"), ["no_address"])
        self.give_phone(verified=False)
        self.assertEqual(policy.enable_blockers(self.student, "sms"), ["phone_unverified", "no_consent"])

    def test_set_channel_enabled_writes_consent_and_switch(self):
        self.give_phone()
        self.assertEqual(policy.set_channel_enabled(self.student, "sms", True, ip="1.2.3.4"), [])
        self.pref.refresh_from_db()
        self.assertTrue(self.pref.sms_enabled)
        row = ChannelConsent.objects.get(user=self.student, channel="sms")
        self.assertTrue(row.granted)
        self.assertEqual(row.address, "+919876543210")
        self.assertEqual(row.ip_address, "1.2.3.4")
        self.assertEqual(row.text_version, policy.consent_text_version())

    def test_withdraw_is_a_new_row_not_an_edit(self):
        self.give_phone()
        policy.set_channel_enabled(self.student, "sms", True)
        policy.set_channel_enabled(self.student, "sms", False)
        rows = list(ChannelConsent.objects.filter(user=self.student, channel="sms").order_by("id"))
        self.assertEqual([r.granted for r in rows], [True, False])
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.sms_enabled)

    def test_enable_refused_without_verified_phone(self):
        self.give_phone(verified=False)
        self.assertEqual(policy.set_channel_enabled(self.student, "sms", True), ["phone_unverified"])
        self.assertFalse(ChannelConsent.objects.exists())

    def test_enable_refused_when_suppressed(self):
        self.give_phone()
        policy.suppress("sms", "+919876543210", ChannelSuppression.Reason.STOP)
        self.assertEqual(policy.set_channel_enabled(self.student, "sms", True), ["suppressed"])


# ------------------------------------------------------------------------------------------------ verification code
@LOCMEM
class EmailCodeTests(ChannelBase):
    def code_from_mail(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_send_and_confirm(self):
        r = policy.request_email_code(self.student)
        self.assertTrue(r["ok"])
        self.assertEqual(mail.outbox[-1].to, ["student1@example.com"])
        res = policy.confirm_email_code(self.student, self.code_from_mail())
        self.assertTrue(res["ok"])
        self.assertTrue(policy.email_verified(self.student))

    def test_wrong_code_counts_attempts_then_locks(self):
        policy.request_email_code(self.student)
        good = self.code_from_mail()
        bad = "000000" if good != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(policy.confirm_email_code(self.student, bad)["reason"], "bad_code") if _ < 4 else None
        last = policy.confirm_email_code(self.student, bad)
        self.assertIn(last["reason"], ("bad_code", "too_many_attempts"))
        self.assertFalse(policy.confirm_email_code(self.student, good)["ok"])  # locked even with the right code

    def test_resend_cooldown(self):
        policy.request_email_code(self.student)
        self.assertEqual(policy.request_email_code(self.student)["reason"], "too_soon")
        self.assertEqual(len(mail.outbox), 1)

    def test_code_does_not_survive_an_email_change(self):
        policy.request_email_code(self.student)
        code = self.code_from_mail()
        self.student.email = "other@example.com"
        self.student.save()
        self.assertFalse(policy.confirm_email_code(self.student, code)["ok"])

    def test_already_verified_sends_nothing(self):
        self.verify_email()
        self.assertEqual(policy.request_email_code(self.student)["reason"], "already_verified")
        self.assertEqual(mail.outbox, [])

    def test_hindi_user_gets_hindi_mail(self):
        from user_profile.models import UserPreference

        UserPreference.objects.update_or_create(user=self.student, defaults={"language": "hi"})
        policy.request_email_code(self.student)
        self.assertIn("वेरिफ़िकेशन", mail.outbox[-1].subject)


# ------------------------------------------------------------------------------------------------ unsubscribe
@LOCMEM
class UnsubscribeTests(ChannelBase):
    def test_token_roundtrip_and_tamper(self):
        tok = policy.make_unsubscribe_token(self.student, "email", "channel")
        self.assertEqual(policy.read_unsubscribe_token(tok)["u"], self.student.id)
        self.assertIsNone(policy.read_unsubscribe_token(tok[:-3] + "xxx"))
        self.assertIsNone(policy.read_unsubscribe_token("garbage"))

    def test_url_and_headers(self):
        url = policy.unsubscribe_url(self.student)
        self.assertTrue(url.startswith("https://ls.example/core/notifications/unsubscribe/"))
        h = policy.email_headers(url)
        self.assertEqual(h["List-Unsubscribe"], f"<{url}>")
        self.assertEqual(h["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        self.assertEqual(policy.email_headers(""), {})

    @override_settings(SITE_URL="")
    def test_no_site_url_means_no_link_not_a_broken_one(self):
        self.assertEqual(policy.unsubscribe_url(self.student), "")

    def test_get_shows_page_and_changes_nothing(self):
        tok = policy.make_unsubscribe_token(self.student)
        res = APIClient().get(f"/core/notifications/unsubscribe/{tok}/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"<form", res.content)
        self.pref.refresh_from_db()
        self.assertTrue(self.pref.email_enabled)

    def test_post_turns_email_and_digest_off_logged_out(self):
        self.pref.digest_frequency = "daily"
        self.pref.save()
        tok = policy.make_unsubscribe_token(self.student)
        res = APIClient().post(f"/core/notifications/unsubscribe/{tok}/", {}, format="json")
        self.assertEqual(res.status_code, 200)
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.email_enabled)
        self.assertEqual(self.pref.digest_frequency, "off")

    def test_digest_scope_only_stops_the_digest(self):
        self.pref.digest_frequency = "weekly"
        self.pref.save()
        tok = policy.make_unsubscribe_token(self.student, "email", "digest")
        APIClient().post(f"/core/notifications/unsubscribe/{tok}/", {}, format="json")
        self.pref.refresh_from_db()
        self.assertTrue(self.pref.email_enabled)
        self.assertEqual(self.pref.digest_frequency, "off")

    def test_bad_token_post_is_400(self):
        res = APIClient().post("/core/notifications/unsubscribe/nope/", {}, format="json")
        self.assertEqual(res.status_code, 400)

    def test_withdrawal_is_logged_when_there_was_consent(self):
        policy.record_consent(self.student, "email", True)
        tok = policy.make_unsubscribe_token(self.student)
        APIClient().post(f"/core/notifications/unsubscribe/{tok}/", {}, format="json")
        last = policy.latest_consent(self.student, "email")
        self.assertFalse(last.granted)
        self.assertEqual(last.source, "unsubscribe_link")


# ------------------------------------------------------------------------------------------------ bounce / STOP
@LOCMEM
class ProviderEventTests(ChannelBase):
    def test_hard_bounce_suppresses_and_switches_off(self):
        r = policy.handle_provider_event("email", "bounce", "Student1@example.com", bounce_type="hard")
        self.assertEqual(r["action"], "suppressed")
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.email_enabled)
        self.assertTrue(policy.is_suppressed("email", "student1@example.com"))

    @override_settings(NOTIF_EMAIL_SOFT_BOUNCE_LIMIT=3)
    def test_soft_bounces_count_then_suppress(self):
        for i in (1, 2):
            r = policy.handle_provider_event("email", "bounce", "student1@example.com", bounce_type="soft")
            self.assertEqual((r["action"], r["soft_bounces"]), ("counted", i))
        self.assertFalse(policy.is_suppressed("email", "student1@example.com"))
        r = policy.handle_provider_event("email", "bounce", "student1@example.com", bounce_type="soft")
        self.assertEqual(r["action"], "suppressed")
        self.assertEqual(ChannelSuppression.objects.get(address="student1@example.com").reason, "soft_bounce_limit")

    def test_complaint_suppresses(self):
        policy.handle_provider_event("email", "complaint", "student1@example.com")
        self.assertEqual(ChannelSuppression.objects.get(channel="email").reason, "complaint")

    def test_sms_stop_switches_both_phone_channels_off(self):
        self.give_phone()
        self.pref.sms_enabled = self.pref.whatsapp_enabled = True
        self.pref.save()
        policy.handle_provider_event("sms", "stop", "+919876543210")
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.sms_enabled)
        self.assertFalse(self.pref.whatsapp_enabled)
        self.assertTrue(policy.is_suppressed("whatsapp", "+919876543210"))

    def test_unknown_event_and_bad_address(self):
        self.assertFalse(policy.handle_provider_event("email", "opened", "a@b.com")["ok"])
        self.assertFalse(policy.handle_provider_event("email", "bounce", "")["ok"])

    def test_clear_suppression(self):
        policy.suppress("email", "a@b.com", "hard_bounce")
        self.assertEqual(policy.clear_suppression("email", "A@B.com"), 1)
        self.assertFalse(policy.is_suppressed("email", "a@b.com"))

    def test_webhook_needs_the_secret(self):
        c = APIClient()
        body = {"event": "bounce", "bounce_type": "hard", "address": "student1@example.com"}
        self.assertEqual(c.post("/core/channel-webhooks/email/", body, format="json").status_code, 403)
        ok = c.post("/core/channel-webhooks/email/", body, format="json", HTTP_X_WEBHOOK_SECRET="s3cret")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(policy.is_suppressed("email", "student1@example.com"))

    def test_webhook_batch(self):
        res = APIClient().post(
            "/core/channel-webhooks/email/",
            {"events": [{"event": "complaint", "address": "x@y.com"}, {"event": "bounce", "address": "z@y.com"}, "junk"]},
            format="json", HTTP_X_WEBHOOK_SECRET="s3cret",
        )
        self.assertEqual(res.json()["processed"], 2)

    @override_settings(NOTIF_WEBHOOK_SECRET="")
    def test_webhook_off_without_secret(self):
        res = APIClient().post("/core/channel-webhooks/email/", {}, format="json", HTTP_X_WEBHOOK_SECRET="")
        self.assertEqual(res.status_code, 503)


# ------------------------------------------------------------------------------------------------ API
@LOCMEM
class ChannelApiTests(ChannelBase):
    def setUp(self):
        super().setUp()
        self.c = APIClient()
        self.c.force_authenticate(self.student)

    def test_status_shape(self):
        data = self.c.get("/core/notification-preferences/channels/").json()
        for ch in ("email", "sms", "whatsapp"):
            for key in ("enabled", "address_masked", "verified", "consent", "suppressed", "can_enable", "blockers",
                        "daily_cap", "sent_24h", "sending_active"):
                self.assertIn(key, data[ch])
        self.assertEqual(data["email"]["address_masked"], "s***@example.com")
        self.assertFalse(data["email"]["verified"])
        self.assertIn("no_address", data["sms"]["blockers"])

    def test_verify_flow_over_http(self):
        self.assertEqual(self.c.post("/core/notification-preferences/channels/email/send-code/").status_code, 200)
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        bad = self.c.post("/core/notification-preferences/channels/email/verify/", {"code": "abc"}, format="json")
        self.assertEqual(bad.status_code, 400)
        ok = self.c.post("/core/notification-preferences/channels/email/verify/", {"code": code}, format="json")
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(ok.json()["channels"]["email"]["verified"])
        self.assertTrue(self.c.get("/core/notification-preferences/me/").json()["email_verified"])

    def test_resend_too_soon_is_429(self):
        self.c.post("/core/notification-preferences/channels/email/send-code/")
        again = self.c.post("/core/notification-preferences/channels/email/send-code/")
        self.assertEqual(again.status_code, 429)

    def test_consent_needs_verified_phone(self):
        res = self.c.post("/core/notification-preferences/channels/consent/", {"channel": "sms", "granted": True}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "no_address")
        self.give_phone()
        res = self.c.post("/core/notification-preferences/channels/consent/", {"channel": "sms", "granted": True}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["sms"]["enabled"])
        self.assertTrue(res.json()["sms"]["sending_active"])

    def test_consent_validation(self):
        r = self.c.post("/core/notification-preferences/channels/consent/", {"channel": "fax", "granted": True}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/core/notification-preferences/channels/consent/", {"channel": "sms", "granted": "yes"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_direct_patch_cannot_opt_into_sms(self):
        res = self.c.patch("/core/notification-preferences/me/", {"sms_enabled": True}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertIn("sms_enabled", res.json().get("errors") or res.json())
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.sms_enabled)

    def test_direct_patch_can_switch_sms_off(self):
        self.give_phone()
        policy.set_channel_enabled(self.student, "sms", True)
        res = self.c.patch("/core/notification-preferences/me/", {"sms_enabled": False}, format="json")
        self.assertEqual(res.status_code, 200)
        self.pref.refresh_from_db()
        self.assertFalse(self.pref.sms_enabled)

    def test_anonymous_cannot_read_status(self):
        self.assertEqual(APIClient().get("/core/notification-preferences/channels/").status_code, 401)

    def test_other_users_data_is_never_returned(self):
        self.other_student.email = "someone.else@example.com"
        self.other_student.save()
        body = self.c.get("/core/notification-preferences/channels/").content.decode()
        self.assertNotIn("someone.else", body)


# ------------------------------------------------------------------------------------------------ senders (end to end)
@LOCMEM
class SenderGateTests(ChannelBase):
    def test_event_email_blocked_until_verified_then_sent_with_unsubscribe(self):
        from tuitionclass.notifications import send_notification

        self.assertFalse(send_notification(self.student, "Hello", "World", channel="email", data={"type": "mention"}))
        self.assertEqual(mail.outbox, [])
        self.verify_email()
        self.assertTrue(send_notification(self.student, "Hello <b>", "World", channel="email", data={"type": "mention"}))
        m = mail.outbox[0]
        self.assertEqual(m.to, ["student1@example.com"])
        self.assertIn("List-Unsubscribe", m.extra_headers)
        self.assertEqual(m.extra_headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        html = m.alternatives[0][0]
        self.assertIn("&lt;b&gt;", html)  # escaped
        self.assertIn("/core/notifications/unsubscribe/", html)
        self.assertEqual(ChannelSendLog.objects.filter(user=self.student, channel="email").count(), 1)

    def test_bounced_address_gets_nothing(self):
        from tuitionclass.notifications import send_notification

        self.verify_email()
        policy.suppress("email", self.student.email, "hard_bounce")
        self.assertFalse(send_notification(self.student, "x", "y", channel="email"))
        self.assertEqual(mail.outbox, [])

    @override_settings(MSG91_AUTH_KEY="k", MSG91_SMS_SENDER_ID="LRNSCR")
    def test_sms_blocked_without_consent_and_sent_with_it(self):
        from tuitionclass.notifications import send_notification

        self.give_phone()
        self.pref.sms_enabled = True
        self.pref.save()
        with patch("tuitionclass.notifications.requests.post") as post:
            self.assertFalse(send_notification(self.student, "t", "m", channel="sms", data={"type": "mention"}))
            post.assert_not_called()
            policy.record_consent(self.student, "sms", True)
            self.assertTrue(send_notification(self.student, "t", "m", channel="sms", data={"type": "mention"}))
            sent = post.call_args.kwargs["json"]["sms"][0]
            self.assertEqual(sent["to"], ["919876543210"])  # no "+"
            self.assertIn("STOP", sent["message"])
        self.assertEqual(ChannelSendLog.objects.filter(channel="sms").count(), 1)

    @override_settings(MSG91_AUTH_KEY="k", MSG91_WHATSAPP_INTEGRATED_NUMBER="9100", MSG91_WHATSAPP_TEMPLATE_NAME="gen",
                       MSG91_WHATSAPP_TEMPLATE_NAME_HI="gen_hi")
    def test_whatsapp_uses_hindi_template_for_hindi_user(self):
        from tuitionclass.notifications import send_notification
        from user_profile.models import UserPreference

        UserPreference.objects.update_or_create(user=self.student, defaults={"language": "hi"})
        self.give_phone()
        self.pref.whatsapp_enabled = True
        self.pref.save()
        policy.record_consent(self.student, "whatsapp", True)
        with patch("tuitionclass.notifications.requests.post") as post:
            self.assertTrue(send_notification(self.student, "t", "line1\nline2", channel="whatsapp", data={"type": "mention"}))
            tmpl = post.call_args.kwargs["json"]["payload"]["template"]
            self.assertEqual(tmpl["name"], "gen_hi")
            self.assertEqual(tmpl["language"]["code"], "hi")
            self.assertNotIn("\n", tmpl["to_and_components"][0]["components"]["body_1"]["value"])


@LOCMEM
class DigestGateTests(ChannelBase):
    def test_digest_needs_verified_email_and_carries_unsubscribe(self):
        from tuitionclass.tasks import send_notification_digests

        self.pref.digest_frequency = "daily"
        self.pref.save()
        Notification.objects.create(recipient=self.student, notif_type="notice_posted", title="New notice")
        res = send_notification_digests()
        self.assertEqual((res["sent"], res["skipped_gate"]), (0, 1))
        self.verify_email()
        res = send_notification_digests()
        self.assertEqual(res["sent"], 1)
        m = mail.outbox[0]
        self.assertIn("List-Unsubscribe", m.extra_headers)
        self.assertIn("New notice", m.body)

    def test_suppressed_address_gets_no_digest(self):
        from tuitionclass.tasks import send_notification_digests

        self.verify_email()
        self.pref.digest_frequency = "daily"
        self.pref.save()
        Notification.objects.create(recipient=self.student, notif_type="notice_posted", title="New notice")
        policy.suppress("email", self.student.email, "complaint")
        self.assertEqual(send_notification_digests()["sent"], 0)


# ------------------------------------------------------------------------------------------------ templates
class TemplateTests(ChannelBase):
    def test_sms_has_stop_line_and_is_capped(self):
        text = tpl.render_sms("Title", "x" * 600, "en")
        self.assertTrue(text.endswith("Reply STOP to opt out."))
        self.assertLessEqual(len(text), 160 * 2)

    def test_hindi_sms_uses_70_char_segments(self):
        text = tpl.render_sms("शीर्षक", "क" * 600, "hi")
        self.assertLessEqual(len(text), 70 * 2)
        self.assertIn("STOP", text)

    @override_settings(NOTIF_SMS_APPEND_STOP=False)
    def test_stop_line_can_be_switched_off(self):
        self.assertNotIn("STOP", tpl.render_sms("T", "m", "en"))

    def test_digest_pluralisation_and_language(self):
        class N:  # noqa: D401
            def __init__(self, t, m=""):
                self.title, self.message = t, m

        subj, text, html = tpl.render_digest_email([N("A")], "daily", "en")
        self.assertIn("1 update)", subj)
        subj, *_ = tpl.render_digest_email([N("A"), N("B")], "weekly", "en")
        self.assertIn("2 updates", subj)
        subj, *_ = tpl.render_digest_email([N("A")], "daily", "hi")
        self.assertIn("डाइजेस्ट", subj)

    def test_whatsapp_template_fallback(self):
        with override_settings(MSG91_WHATSAPP_TEMPLATE_NAME="gen", MSG91_WHATSAPP_TEMPLATE_NAME_HI=""):
            self.assertEqual(tpl.whatsapp_template("hi"), ("gen", "en"))
        with override_settings(MSG91_WHATSAPP_TEMPLATE_NAME_HI="gen_hi"):
            self.assertEqual(tpl.whatsapp_template("hi"), ("gen_hi", "hi"))

    def test_event_email_without_link_has_no_unsubscribe_anchor(self):
        _, text, html = tpl.render_event_email("T", "m", "en", "")
        self.assertNotIn("<a ", html)

    def test_every_template_key_exists_in_both_languages(self):
        self.assertEqual(set(tpl._T["en"]), set(tpl._T["hi"]))


class PruneTests(ChannelBase):
    def test_prune_send_log(self):
        old = policy.record_send(self.student, "sms")
        policy.record_send(self.student, "sms")
        ChannelSendLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        self.assertEqual(policy.prune_send_log(35), 1)
        self.assertEqual(ChannelSendLog.objects.count(), 1)


# ------------------------------------------------------------------------------------------------ provider adapters (N8)
import json  # noqa: E402

from django.test import SimpleTestCase  # noqa: E402

from . import channel_provider_adapters as adapters  # noqa: E402


class ProviderAdapterTests(SimpleTestCase):
    def test_ses_permanent_bounce_is_hard_transient_is_soft(self):
        def sns(btype):
            inner = {"notificationType": "Bounce", "bounce": {"bounceType": btype, "bouncedRecipients": [{"emailAddress": "a@b.com"}]}}
            return {"Type": "Notification", "Message": json.dumps(inner)}

        self.assertEqual(adapters.from_ses(sns("Permanent"))[0]["bounce_type"], "hard")
        self.assertEqual(adapters.from_ses(sns("Transient"))[0]["bounce_type"], "soft")

    def test_ses_complaint_and_ignored_kinds(self):
        inner = {"notificationType": "Complaint", "complaint": {"complainedRecipients": [{"emailAddress": "c@d.com"}]}}
        ev = adapters.from_ses({"Type": "Notification", "Message": json.dumps(inner)})
        self.assertEqual([(e["event"], e["address"]) for e in ev], [("complaint", "c@d.com")])
        self.assertEqual(adapters.from_ses({"Type": "Notification", "Message": json.dumps({"notificationType": "Delivery"})}), [])
        self.assertEqual(adapters.from_ses({"Type": "Notification", "Message": "not json"}), [])

    def test_sns_subscribe_url_only_for_amazonaws_https(self):
        ok = {"Type": "SubscriptionConfirmation", "SubscribeURL": "https://sns.ap-south-1.amazonaws.com/?Action=ConfirmSubscription"}
        self.assertTrue(adapters.sns_subscribe_url(ok))
        evil = {"Type": "SubscriptionConfirmation", "SubscribeURL": "https://evil.example.com/amazonaws.com"}
        self.assertIsNone(adapters.sns_subscribe_url(evil))
        self.assertIsNone(adapters.sns_subscribe_url({"Type": "Notification"}))

    def test_sendgrid_events(self):
        ev = adapters.from_sendgrid([
            {"event": "bounce", "email": "a@b.com", "type": "bounce"},
            {"event": "bounce", "email": "s@b.com", "type": "blocked"},
            {"event": "spamreport", "email": "c@b.com"},
            {"event": "dropped", "email": "d@b.com", "reason": "Bounced Address"},
            {"event": "dropped", "email": "e@b.com", "reason": "Rate limited"},
            {"event": "delivered", "email": "f@b.com"},
        ])
        self.assertEqual(
            [(e["event"], e["address"], e.get("bounce_type", "")) for e in ev],
            [("bounce", "a@b.com", "hard"), ("bounce", "s@b.com", "soft"), ("complaint", "c@b.com", ""), ("bounce", "d@b.com", "hard")],
        )

    def test_stop_words(self):
        for t in ("STOP", " stop ", "Stop.", "unsubscribe", "बंद"):
            self.assertTrue(adapters.is_stop_text(t), t)
        for t in ("please do not stop", "", None, "stopping"):
            self.assertFalse(adapters.is_stop_text(t), t)

    def test_msg91_stop_reply_and_invalid_number(self):
        ev = adapters.from_msg91([
            {"mobile": "919876543210", "text": "STOP"},
            {"mobile": "919811111111", "status": "FAILED", "description": "Invalid number"},
            {"mobile": "919822222222", "status": "FAILED", "description": "network timeout"},
        ])
        self.assertEqual([(e["event"], e["address"]) for e in ev], [("stop", "919876543210"), ("invalid", "919811111111")])

    def test_meta_inbound_stop_and_undeliverable(self):
        payload = {"entry": [{"changes": [{"value": {
            "messages": [{"from": "919876543210", "text": {"body": "Stop"}}],
            "statuses": [{"status": "failed", "recipient_id": "919833333333", "errors": [{"code": 131026, "title": "Undeliverable"}]}],
        }}]}]}
        ev = adapters.from_meta(payload)
        self.assertEqual([(e["event"], e["address"]) for e in ev], [("stop", "919876543210"), ("invalid", "919833333333")])

    def test_garbage_never_raises(self):
        for fmt in adapters.ADAPTERS:
            for junk in (None, 5, "x", [], [1, None], {"entry": "x"}, {"Message": 5}):
                self.assertIsInstance(adapters.normalise(fmt, junk), list)
        self.assertEqual(adapters.normalise("nope", {}), [])


@LOCMEM
class ProviderWebhookFormatTests(ChannelBase):
    URL = "/core/channel-webhooks/email/"

    def post(self, url, body, **extra):
        return APIClient().post(url, body, format="json", HTTP_X_WEBHOOK_SECRET="s3cret", **extra)

    def test_sendgrid_bounce_suppresses_through_the_view(self):
        res = self.post(self.URL + "?format=sendgrid", [{"event": "bounce", "email": "Dead@Example.com", "type": "bounce"}])
        self.assertEqual(res.status_code, 200)
        self.assertTrue(policy.is_suppressed("email", "dead@example.com"))

    def test_sns_text_plain_body_and_key_in_query(self):
        inner = {"notificationType": "Complaint", "complaint": {"complainedRecipients": [{"emailAddress": "spam@example.com"}]}}
        body = json.dumps({"Type": "Notification", "Message": json.dumps(inner)})
        res = APIClient().post(self.URL + "?format=ses&key=s3cret", body, content_type="text/plain; charset=UTF-8")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(policy.is_suppressed("email", "spam@example.com"))

    def test_wrong_key_in_query_is_403(self):
        res = APIClient().post(self.URL + "?format=ses&key=nope", "{}", content_type="text/plain")
        self.assertEqual(res.status_code, 403)

    def test_sns_subscription_confirmation_is_returned_not_fetched(self):
        url = "https://sns.ap-south-1.amazonaws.com/?Action=ConfirmSubscription&Token=t"
        res = self.post(self.URL + "?format=ses", {"Type": "SubscriptionConfirmation", "SubscribeURL": url})
        self.assertEqual(res.json()["subscribe_url"], url)

    def test_format_must_match_channel_and_be_known(self):
        self.assertEqual(self.post("/core/channel-webhooks/sms/?format=sendgrid", []).status_code, 400)
        self.assertEqual(self.post(self.URL + "?format=mailchimp", {}).status_code, 400)

    def test_msg91_stop_suppresses_both_phone_channels(self):
        res = self.post("/core/channel-webhooks/sms/?format=msg91", {"mobile": "9876543210", "text": "STOP"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(policy.is_suppressed("sms", "+919876543210"))
        self.assertTrue(policy.is_suppressed("whatsapp", "+919876543210"))


class DeliverableEmailsTests(ChannelBase):
    def test_dead_addresses_dropped_rest_kept_in_order(self):
        policy.suppress("email", "dead@x.com", "hard_bounce")
        out = policy.deliverable_emails(["a@x.com", "DEAD@x.com", "b@x.com", "a@x.com", ""])
        self.assertEqual(out, ["a@x.com", "b@x.com"])
