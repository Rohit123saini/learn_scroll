"""X12 live safety: word filter + age rules (live_safety), flood / duplicate / strikes / report budget / mute check
(live_abuse), moderation dispatcher guards + audit shape (live_moderation), the central push gate (live_push) and the
order of the checks in ChatMessageViewSet.perform_create.

No database: sessions / users / events are SimpleNamespace stand-ins and everything that would touch the DB is patched, so
these are plain SimpleTestCase (same style as test_slow_mode.py). They were WRITTEN, NOT RUN - run them with
    python manage.py test tuitionclass.test_live_safety
"""
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import Throttled, ValidationError

from tuitionclass import live_abuse, live_moderation, live_push, live_safety
from tuitionclass.livekit_utils import ParticipantRole
from tuitionclass.views import live_chat


def _session(sid=1, **kw):
    classroom = kw.pop("classroom", SimpleNamespace(id=9, teacher_id=100, recording_enabled=True, recording_auto=False,
                                                    title="Maths"))
    return SimpleNamespace(id=sid, classroom=classroom, egress_id=kw.pop("egress_id", ""),
                           chat_filter_mode=kw.pop("chat_filter_mode", ""), **kw)


def _user(uid=7, **kw):
    return SimpleNamespace(id=uid, is_staff=False, **kw)


# =====================================================================================================
# live_safety - word filter
# =====================================================================================================
class FilterTests(SimpleTestCase):
    def test_contact_info_detection(self):
        for text in ("call 9876543210", "see www.example.com", "mail me a.b@c.in", "add me on whatsapp", "insta: me"):
            self.assertTrue(live_safety.has_contact_info(text), text)
        for text in ("hello class", "answer is 42", "", None):
            self.assertFalse(live_safety.has_contact_info(text), text)

    def test_strict_contact_blocks_even_when_filter_is_off(self):
        r = live_safety.filter_chat_text("call 9876543210", "off", strict_contact=True)
        self.assertTrue(r.blocked)
        self.assertEqual(r.reason, "contact_info")

    def test_contact_info_allowed_when_rule_does_not_apply(self):
        with mock.patch.object(live_safety, "_profanity_spans", return_value=([], True)), \
                mock.patch("common.moderation.screen_text", return_value=(False, "")):
            r = live_safety.filter_chat_text("see www.example.com", "block", strict_contact=False)
        self.assertEqual(r.action, "allow")

    def test_off_mode_lets_profanity_through(self):
        r = live_safety.filter_chat_text("damn it", "off")
        self.assertEqual(r.action, "allow")

    def test_mask_flag_block_modes(self):
        with mock.patch.object(live_safety, "_profanity_spans", return_value=([(0, 4)], True)):
            masked = live_safety.filter_chat_text("damn x", "mask")
            flagged = live_safety.filter_chat_text("damn x", "flag")
            blocked = live_safety.filter_chat_text("damn x", "block")
        self.assertEqual((masked.action, masked.text), ("mask", "d*** x"))
        self.assertEqual((flagged.action, flagged.text), ("flag", "damn x"))
        self.assertTrue(blocked.blocked)

    def test_unmappable_spans_are_blocked_not_masked(self):
        with mock.patch.object(live_safety, "_profanity_spans", return_value=([(0, 4)], False)):
            self.assertTrue(live_safety.filter_chat_text("damn x", "mask").blocked)

    def test_mask_spans_short_words_become_all_stars(self):
        self.assertEqual(live_safety.mask_spans("ab cd", [(0, 2)]), "** cd")
        self.assertEqual(live_safety.mask_spans("abcd", [(0, 4)]), "a***")

    def test_filter_never_raises(self):
        with mock.patch.object(live_safety, "_profanity_spans", side_effect=RuntimeError("boom")):
            r = live_safety.filter_chat_text("hello", "mask")
        self.assertEqual(r.action, "allow")

    def test_fingerprint_ignores_case_and_spacing(self):
        self.assertEqual(live_safety.text_fingerprint("Hello   World"), live_safety.text_fingerprint(" hello world "))
        self.assertNotEqual(live_safety.text_fingerprint("a"), live_safety.text_fingerprint("b"))

    @override_settings(LIVE_CHAT_FILTER_DEFAULT="flag")
    def test_effective_mode_session_overrides_default(self):
        self.assertEqual(live_safety.effective_filter_mode(_session(chat_filter_mode="block")), "block")
        self.assertEqual(live_safety.effective_filter_mode(_session(chat_filter_mode="")), "flag")
        self.assertEqual(live_safety.effective_filter_mode(_session(chat_filter_mode="nonsense")), "flag")

    @override_settings(LIVE_CHAT_FILTER_DEFAULT="nonsense")
    def test_effective_mode_bad_default_falls_back_to_mask(self):
        self.assertEqual(live_safety.effective_filter_mode(_session()), "mask")


# =====================================================================================================
# live_safety - age rules + recording notice
# =====================================================================================================
class AgeRuleTests(SimpleTestCase):
    def test_strict_contact_rule(self):
        s, u = _session(), _user()
        self.assertFalse(live_safety.strict_contact_rule(s, u, sender_is_staff=True))
        with mock.patch.object(live_safety, "is_minor", return_value=True):
            self.assertTrue(live_safety.strict_contact_rule(s, u, sender_is_staff=False))
        with mock.patch.object(live_safety, "is_minor", return_value=False), \
                mock.patch.object(live_safety, "minors_present", return_value=True):
            self.assertTrue(live_safety.strict_contact_rule(s, u, sender_is_staff=False))
        with mock.patch.object(live_safety, "is_minor", return_value=False), \
                mock.patch.object(live_safety, "minors_present", return_value=False):
            self.assertFalse(live_safety.strict_contact_rule(s, u, sender_is_staff=False))

    def test_age_group_unknown_without_dob(self):
        self.assertEqual(live_safety.age_group(SimpleNamespace()), "unknown")
        self.assertEqual(live_safety.age_group(SimpleNamespace(is_minor=True)), "minor")
        self.assertEqual(live_safety.age_group(SimpleNamespace(is_minor=False, is_verified_adult=True)), "adult")

    def test_stage_invite_rules_for_minor(self):
        part = SimpleNamespace(user=_user(), recording_notice_ack_at=None)
        with mock.patch.object(live_safety, "is_minor", return_value=True):
            with override_settings(LIVE_MINOR_STAGE_POLICY="never"):
                self.assertEqual(live_safety.stage_invite_error(_session(), part)[0], "minor_stage_not_allowed")
            with override_settings(LIVE_MINOR_STAGE_POLICY="with_notice"):
                self.assertEqual(live_safety.stage_invite_error(_session(egress_id="e1"), part)[0],
                                 "recording_notice_required")
                self.assertIsNone(live_safety.stage_invite_error(_session(egress_id=""), part))
                part.recording_notice_ack_at = timezone.now()
                self.assertIsNone(live_safety.stage_invite_error(_session(egress_id="e1"), part))
            with override_settings(LIVE_MINOR_STAGE_POLICY="allow"):
                part.recording_notice_ack_at = None
                self.assertIsNone(live_safety.stage_invite_error(_session(egress_id="e1"), part))

    def test_stage_invite_never_blocks_adults(self):
        part = SimpleNamespace(user=_user(), recording_notice_ack_at=None)
        with mock.patch.object(live_safety, "is_minor", return_value=False), \
                override_settings(LIVE_MINOR_STAGE_POLICY="never"):
            self.assertIsNone(live_safety.stage_invite_error(_session(egress_id="e1"), part))

    def test_notice_payload_ack_required_only_for_minor_while_recording(self):
        s = _session(egress_id="e1")
        with mock.patch.object(live_safety, "is_minor", return_value=True):
            p = live_safety.notice_payload(s, _user(), None)
            self.assertTrue(p["ack_required"] and p["show"] and p["recording"] and not p["acknowledged"])
            acked = SimpleNamespace(recording_notice_ack_at=timezone.now())
            self.assertFalse(live_safety.notice_payload(s, _user(), acked)["ack_required"])
        with mock.patch.object(live_safety, "is_minor", return_value=False):
            self.assertFalse(live_safety.notice_payload(s, _user(), None)["ack_required"])

    def test_notice_shown_before_auto_record_starts(self):
        classroom = SimpleNamespace(recording_enabled=True, recording_auto=True)
        s = _session(egress_id="", classroom=classroom)
        with mock.patch.object(live_safety, "is_minor", return_value=False):
            p = live_safety.notice_payload(s, _user(), None)
        self.assertTrue(p["show"])
        self.assertFalse(p["recording"])
        self.assertTrue(p["auto"])

    def test_recording_never_blocks_host_or_non_recording(self):
        self.assertFalse(live_safety.recording_blocks_publish(_session(egress_id=""), ParticipantRole.STUDENT, _user()))
        self.assertFalse(live_safety.recording_blocks_publish(_session(egress_id="e"), ParticipantRole.HOST, _user()))
        self.assertFalse(live_safety.recording_blocks_publish(_session(egress_id="e"), ParticipantRole.CO_HOST, _user()))

    def test_notice_has_both_languages(self):
        p = live_safety.broadcast_notice_payload(_session())
        for key in ("title_en", "body_en", "title_hi", "body_hi"):
            self.assertTrue(p[key])


# =====================================================================================================
# live_abuse
# =====================================================================================================
class _Clock:
    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now


@override_settings(LIVE_CHAT_FLOOD_WINDOW_SECONDS=10, LIVE_CHAT_FLOOD_MAX=3, LIVE_DUP_GRACE_SECONDS=3)
class AbuseGuardTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.s, self.u = _session(), _user()
        self.clock = _Clock(1000.0)
        patcher = mock.patch.object(live_abuse, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flood_allows_limit_then_throttles_with_one_strike(self):
        with mock.patch.object(live_abuse, "register_strike") as strike:
            for _ in range(3):
                live_abuse.check_flood(self.s, self.u)
            with self.assertRaises(Throttled) as ctx:
                live_abuse.check_flood(self.s, self.u)
            self.assertEqual(ctx.exception.machine_code, "chat_flood")
            self.assertTrue(1 <= ctx.exception.wait <= 10)
            with self.assertRaises(Throttled):
                live_abuse.check_flood(self.s, self.u)
        strike.assert_called_once_with(self.s, self.u, "chat_flood")     # 30 spammed lines = ONE strike

    def test_flood_window_resets(self):
        with mock.patch.object(live_abuse, "register_strike"):
            for _ in range(3):
                live_abuse.check_flood(self.s, self.u)
            self.clock.now += 11
            live_abuse.check_flood(self.s, self.u)      # new bucket, no error

    def test_flood_is_per_user_and_per_session(self):
        with mock.patch.object(live_abuse, "register_strike"):
            for _ in range(3):
                live_abuse.check_flood(self.s, self.u)
            live_abuse.check_flood(self.s, _user(8))
            live_abuse.check_flood(_session(2), self.u)

    def test_duplicate_double_tap_is_rejected_without_strike(self):
        with mock.patch.object(live_abuse, "register_strike") as strike:
            live_abuse.check_duplicate(self.s, self.u, "Hello there")
            with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
                live_abuse.check_duplicate(self.s, self.u, "hello  there")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.machine_code, "duplicate_message")
        strike.assert_not_called()

    def test_duplicate_after_grace_is_a_strike(self):
        with mock.patch.object(live_abuse, "register_strike") as strike:
            live_abuse.check_duplicate(self.s, self.u, "same")
            self.clock.now += 5
            with self.assertRaises(live_abuse.LiveSafetyError):
                live_abuse.check_duplicate(self.s, self.u, "same")
        strike.assert_called_once_with(self.s, self.u, "duplicate_message")

    def test_different_text_is_fine(self):
        live_abuse.check_duplicate(self.s, self.u, "one")
        live_abuse.check_duplicate(self.s, self.u, "two")

    def test_guards_fail_open_when_cache_is_down(self):
        with mock.patch.object(live_abuse.cache, "add", side_effect=RuntimeError("redis down")):
            live_abuse.check_flood(self.s, self.u)
            live_abuse.check_duplicate(self.s, self.u, "x")


@override_settings(LIVE_STRIKE_LIMIT=3, LIVE_STRIKE_WINDOW_SECONDS=600, LIVE_AUTO_MUTE_MINUTES=5,
                   LIVE_AUTO_MUTE_MAX_MINUTES=60)
class StrikeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.s, self.u = _session(), _user()

    def test_third_strike_mutes_and_doubles_each_time(self):
        until = timezone.now() + timedelta(minutes=5)
        with mock.patch.object(live_abuse, "exempt", return_value=False), \
                mock.patch.object(live_moderation, "count_events", side_effect=[0, 1, 2, 5]) as count, \
                mock.patch.object(live_moderation, "apply_chat_mute", return_value=until) as mute:
            self.assertIsNone(live_abuse.register_strike(self.s, self.u, "a"))
            self.assertIsNone(live_abuse.register_strike(self.s, self.u, "b"))
            first = live_abuse.register_strike(self.s, self.u, "c")
            self.assertEqual(first["minutes"], 5)
            for expected in (10, 20, 40):          # the counter was cleared, three more strikes = next mute
                for r in ("a", "b"):
                    live_abuse.register_strike(self.s, self.u, r)
                self.assertEqual(live_abuse.register_strike(self.s, self.u, "c")["minutes"], expected)
        self.assertEqual(mute.call_count, 4)
        self.assertEqual(count.call_count, 4)

    @override_settings(LIVE_AUTO_MUTE_MAX_MINUTES=30)
    def test_mute_length_is_capped(self):
        with mock.patch.object(live_abuse, "exempt", return_value=False), \
                mock.patch.object(live_moderation, "count_events", return_value=9), \
                mock.patch.object(live_moderation, "apply_chat_mute", return_value=timezone.now()):
            for r in ("a", "b"):
                live_abuse.register_strike(self.s, self.u, r)
            self.assertEqual(live_abuse.register_strike(self.s, self.u, "c")["minutes"], 30)

    def test_host_tier_is_never_struck(self):
        with mock.patch.object(live_abuse, "exempt", return_value=True), \
                mock.patch.object(live_moderation, "apply_chat_mute") as mute:
            for _ in range(10):
                self.assertIsNone(live_abuse.register_strike(self.s, self.u, "x"))
        mute.assert_not_called()

    def test_register_strike_never_raises(self):
        with mock.patch.object(live_abuse, "exempt", side_effect=RuntimeError("boom")):
            self.assertIsNone(live_abuse.register_strike(self.s, self.u, "x"))


class MuteCheckTests(SimpleTestCase):
    def test_not_muted_passes(self):
        with mock.patch.object(live_abuse, "chat_mute_until", return_value=None):
            live_abuse.ensure_can_chat(_session(), _user())

    def test_muted_is_403_with_until(self):
        until = timezone.now() + timedelta(minutes=3)
        with mock.patch.object(live_abuse, "chat_mute_until", return_value=until):
            with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
                live_abuse.ensure_can_chat(_session(), _user())
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.machine_code, "chat_muted")
        self.assertEqual(str(ctx.exception.detail["muted_until"]), until.isoformat())


@override_settings(LIVE_REPORT_MAX_PER_SESSION=2, LIVE_REPORT_MAX_PER_HOUR=3)
class ReportBudgetTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_per_session_limit(self):
        s, u = _session(), _user()
        live_abuse.consume_report_budget(s, u)
        live_abuse.consume_report_budget(s, u)
        with self.assertRaises(Throttled) as ctx:
            live_abuse.consume_report_budget(s, u)
        self.assertEqual(ctx.exception.machine_code, "report_limit")

    def test_per_hour_limit_spans_sessions(self):
        u = _user()
        for sid in (1, 2):
            live_abuse.consume_report_budget(_session(sid), u)
        live_abuse.consume_report_budget(_session(3), u)
        with self.assertRaises(Throttled) as ctx:
            live_abuse.consume_report_budget(_session(4), u)
        self.assertEqual(ctx.exception.machine_code, "report_limit")


# =====================================================================================================
# live_moderation - guards that need no DB + the shape of what moderators / the audit log see
# =====================================================================================================
class ModerationGuardTests(SimpleTestCase):
    def test_unknown_action_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            live_moderation.moderate(_session(), _user(), "explode", 5)

    def test_non_moderator_cannot_act(self):
        from rest_framework.exceptions import PermissionDenied

        with mock.patch.object(live_moderation, "is_host_tier", return_value=False), self.assertRaises(PermissionDenied):
            live_moderation.moderate(_session(), _user(), "warn", 5)

    def test_unknown_report_reason_is_a_validation_error(self):
        with self.assertRaises(ValidationError):
            live_moderation.file_report(_session(), _user(), reason="not-a-reason")

    def test_resolve_report_validates(self):
        with self.assertRaises(ValidationError):
            live_moderation.resolve_report(SimpleNamespace(kind="report", resolved_at=None), _user(), "maybe")
        with self.assertRaises(ValidationError):
            live_moderation.resolve_report(SimpleNamespace(kind="warn", resolved_at=None), _user(), "dismissed")

    def test_minor_safety_reports_map_to_the_urgent_platform_reason(self):
        self.assertEqual(live_moderation.REPORT_REASONS["minor_safety"], "child_safety")

    def test_audit_item_never_names_the_reporter(self):
        reporter = SimpleNamespace(id=5, username="reporter_rita", get_full_name=lambda: "Rita Reporter")
        target = SimpleNamespace(id=6, username="bob", get_full_name=lambda: "Bob")
        event = SimpleNamespace(id=1, kind="report", created_at=timezone.now(), actor=reporter, actor_id=5,
                                target_user=target, reason="spam", detail="")
        row = live_moderation.audit_item(event)
        self.assertIsNone(row["actor"])
        self.assertNotIn("Rita", json.dumps(row))
        self.assertNotIn("reporter_rita", json.dumps(row))

    def test_audit_item_names_the_moderator_for_actions_and_marks_system_rows(self):
        mod = SimpleNamespace(id=2, username="teach", get_full_name=lambda: "Teacher T")
        target = SimpleNamespace(id=6, username="bob", get_full_name=lambda: "")
        row = live_moderation.audit_item(SimpleNamespace(
            id=2, kind="chat_mute", created_at=timezone.now(), actor=mod, actor_id=2, target_user=target,
            reason="moderator", detail="10 min"))
        self.assertEqual((row["actor"], row["system"], row["target"]), ("Teacher T", False, "bob"))
        auto = live_moderation.audit_item(SimpleNamespace(
            id=3, kind="auto_mute", created_at=timezone.now(), actor=None, actor_id=None, target_user=target,
            reason="chat_flood", detail=""))
        self.assertTrue(auto["system"])
        self.assertIsNone(auto["actor"])


# =====================================================================================================
# live_push - the central quiet-hours gate used by notifications._send_push
# =====================================================================================================
class CentralPushGateTests(SimpleTestCase):
    types = SimpleNamespace(values=["generic", "live_report"], GENERIC="generic")

    def _gate(self, audience=None, error=None):
        patches = [mock.patch.object(live_push, "_notif_types", return_value=self.types)]
        if error is not None:
            patches.append(mock.patch.object(live_push, "split_push_audience", side_effect=error))
        else:
            patches.append(mock.patch.object(live_push, "split_push_audience", return_value=audience))
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return live_push.central_push_allowed(_user(), {"type": "waitlist_promoted"})

    def test_push_allowed_when_user_is_in_push_list(self):
        self.assertTrue(self._gate(([7], [], [])))

    def test_quiet_hours_and_muted_types_hold_the_push_back(self):
        self.assertFalse(self._gate(([], [7], [])))
        self.assertFalse(self._gate(([], [], [7])))

    def test_lookup_error_fails_open(self):
        self.assertTrue(self._gate(error=RuntimeError("db down")))

    @override_settings(TUITIONCLASS_PUSH_RESPECT_QUIET_HOURS=False)
    def test_kill_switch(self):
        self.assertTrue(live_push.central_push_allowed(_user(), {}))

    def test_unknown_type_is_checked_as_generic(self):
        with mock.patch.object(live_push, "_notif_types", return_value=self.types), \
                mock.patch.object(live_push, "split_push_audience", return_value=([7], [], [])) as split:
            live_push.central_push_allowed(_user(), {"type": "waitlist_promoted"})
        split.assert_called_once_with([7], "generic")


# =====================================================================================================
# ChatMessageViewSet.perform_create - order and outcome of the X12 checks
# =====================================================================================================
class ChatHookTests(SimpleTestCase):
    def setUp(self):
        self.session, self.user = _session(), _user()
        self.view = live_chat.ChatMessageViewSet()
        self.view.request = SimpleNamespace(user=self.user)
        self.instance = SimpleNamespace(id=11, pk=11, message="hi", is_flagged=False, flagged_reason="")
        self.serializer = mock.Mock()
        self.serializer.validated_data = {"session": self.session, "message": "damn it"}
        self.serializer.instance = self.instance
        self.calls = []
        self.p = {}
        for target, name, kw in (
            (live_chat, "_has_room_access", {"return_value": True}),
            (live_chat, "_enforce_slow_mode", {}),
            (live_chat, "broadcast_to_session", {}),
            (live_chat, "ChatMessageSerializer", {}),
            (live_chat, "ChatMessage", {}),
            (live_chat, "screen_message", {"return_value": (False, "")}),
            (live_abuse, "ensure_can_chat", {}),
            (live_abuse, "exempt", {"return_value": False}),
            (live_abuse, "check_flood", {}),
            (live_abuse, "check_duplicate", {}),
            (live_abuse, "register_strike", {"return_value": None}),
            (live_moderation, "is_host_tier", {"return_value": False}),
            (live_moderation, "log_event", {}),
            (live_safety, "effective_filter_mode", {"return_value": "mask"}),
            (live_safety, "strict_contact_rule", {"return_value": False}),
        ):
            patcher = mock.patch.object(target, name, **kw)
            self.p[name] = patcher.start()
            self.addCleanup(patcher.stop)

    def _filter(self, result):
        patcher = mock.patch.object(live_safety, "filter_chat_text", return_value=result)
        self.p["filter_chat_text"] = patcher.start()
        self.addCleanup(patcher.stop)

    def test_blocked_line_is_refused_logged_and_struck_and_never_saved(self):
        self._filter(live_safety.FilterResult("damn it", "block", "profanity"))
        with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
            self.view.perform_create(self.serializer)
        self.assertEqual(ctx.exception.machine_code, "message_blocked")
        self.serializer.save.assert_not_called()
        self.assertEqual(self.p["log_event"].call_args[0][1], "filter_block")
        self.p["register_strike"].assert_called_once_with(self.session, self.user, "filter_block")
        self.p["broadcast_to_session"].assert_not_called()

    def test_blocked_contact_info_uses_its_own_code_and_minor_guard_row(self):
        self._filter(live_safety.FilterResult("call 99999", "block", "contact_info"))
        with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
            self.view.perform_create(self.serializer)
        self.assertEqual(ctx.exception.machine_code, "contact_info_blocked")
        self.assertEqual(self.p["log_event"].call_args[0][1], "minor_guard")

    def test_auto_mute_from_the_strike_is_reported_in_the_error(self):
        until = timezone.now() + timedelta(minutes=5)
        self.p["register_strike"].return_value = {"minutes": 5, "until": until}
        self._filter(live_safety.FilterResult("damn it", "block", "profanity"))
        with self.assertRaises(live_abuse.LiveSafetyError) as ctx:
            self.view.perform_create(self.serializer)
        self.assertEqual(str(ctx.exception.detail["muted_until"]), until.isoformat())

    def test_masked_line_is_saved_masked_and_logged(self):
        self._filter(live_safety.FilterResult("d*** it", "mask", "profanity"))
        self.view.perform_create(self.serializer)
        self.serializer.save.assert_called_once_with(sender=self.user, message="d*** it")
        self.assertEqual(self.p["log_event"].call_args[0][1], "filter_mask")
        self.p["broadcast_to_session"].assert_called_once()

    def test_flag_mode_saves_the_original_and_flags_it(self):
        self._filter(live_safety.FilterResult("damn it", "flag", "profanity"))
        self.view.perform_create(self.serializer)
        self.serializer.save.assert_called_once_with(sender=self.user)
        self.assertTrue(self.instance.is_flagged)
        self.assertEqual(self.instance.flagged_reason, "auto:profanity")

    def test_clean_line_goes_through(self):
        self._filter(live_safety.FilterResult("hello", "allow"))
        self.view.perform_create(self.serializer)
        self.serializer.save.assert_called_once_with(sender=self.user)
        self.p["check_flood"].assert_called_once()
        self.p["check_duplicate"].assert_called_once()

    def test_muted_user_is_stopped_before_anything_else(self):
        self.p["ensure_can_chat"].side_effect = live_abuse.LiveSafetyError("muted", code="chat_muted", status_code=403)
        with self.assertRaises(live_abuse.LiveSafetyError):
            self.view.perform_create(self.serializer)
        self.p["_enforce_slow_mode"].assert_not_called()
        self.serializer.save.assert_not_called()

    def test_flood_stops_the_line_before_the_filter_runs(self):
        self._filter(live_safety.FilterResult("x", "allow"))
        self.p["check_flood"].side_effect = Throttled(wait=3)
        with self.assertRaises(Throttled):
            self.view.perform_create(self.serializer)
        self.p["filter_chat_text"].assert_not_called()
        self.serializer.save.assert_not_called()

    def test_host_tier_skips_flood_duplicate_and_filter(self):
        self.p["exempt"].return_value = True
        self.p["is_host_tier"].return_value = True
        self._filter(live_safety.FilterResult("x", "block", "profanity"))
        self.view.perform_create(self.serializer)
        self.p["check_flood"].assert_not_called()
        self.p["check_duplicate"].assert_not_called()
        self.p["filter_chat_text"].assert_not_called()
        self.serializer.save.assert_called_once_with(sender=self.user)
        self.p["ensure_can_chat"].assert_called_once()          # nobody is exempt from the mute check itself

    def test_no_room_access_is_403_before_any_guard(self):
        from rest_framework.exceptions import PermissionDenied

        self.p["_has_room_access"].return_value = False
        with self.assertRaises(PermissionDenied):
            self.view.perform_create(self.serializer)
        self.p["ensure_can_chat"].assert_not_called()
