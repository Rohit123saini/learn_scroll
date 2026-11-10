"""X3 - privacy-safe product analytics + experiments.

Likha gaya, CHALAYA NAHI gaya.   python manage.py test core.tests_product_analytics

Cache (locmem) har test me clear hota hai. Koi migration nahi chahiye (counters cache me rehte hain).
"""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from login import data_rights

from . import experiments, product_analytics as pa
from .models import Notification, NotificationPreference

User = get_user_model()
NT = Notification.NotifType

COPY_EXP = {
    "notif_copy_test_v1": {
        "enabled": True, "salt": "t1", "rollout_pct": 100, "notif_types": ["streak_at_risk"],
        "variants": {
            "control": {"weight": 50},
            "b": {"weight": 50, "title": "New title", "body": "{body} (b)"},
        },
    }
}
FLAG_EXP = {
    "flag_new_inbox_v1": {
        "enabled": True, "salt": "f1", "rollout_pct": 100,
        "variants": {"off": {"weight": 50}, "on": {"weight": 50}},
    }
}


def _user(name, minor=False):
    u = User.objects.create_user(username=name, password="Str0ng-pass-123!", email=f"{name}@example.com")
    if minor:
        today = date.today()
        u.date_of_birth = today.replace(year=today.year - 10)
        u.save()
    return u


class Base:
    def setUp(self):
        super().setUp()
        cache.clear()
        pa.reset_local()
        experiments._warned.clear()  # noqa: SLF001

    def tearDown(self):
        cache.clear()
        pa.reset_local()
        super().tearDown()


# =====================================================================================================================
# 1. experiments (pure)
# =====================================================================================================================
@override_settings(EXPERIMENTS={**COPY_EXP, **FLAG_EXP})
class ExperimentTests(Base, SimpleTestCase):
    def test_assignment_is_deterministic(self):
        exp = experiments.get("notif_copy_test_v1")
        first = [experiments.pick_variant(exp, uid) for uid in range(1, 200)]
        again = [experiments.pick_variant(exp, uid) for uid in range(1, 200)]
        self.assertEqual(first, again)

    def test_split_is_roughly_by_weight(self):
        exp = experiments.get("notif_copy_test_v1")
        picks = [experiments.pick_variant(exp, uid) for uid in range(1, 4001)]
        share_b = picks.count("b") / len(picks)
        self.assertTrue(0.44 < share_b < 0.56, share_b)

    def test_new_salt_reshuffles(self):
        exp = experiments.get("notif_copy_test_v1")
        other = dict(exp, salt="t2")
        moved = sum(experiments.pick_variant(exp, u) != experiments.pick_variant(other, u) for u in range(1, 500))
        self.assertGreater(moved, 100)

    def test_rollout_zero_puts_nobody_in(self):
        exp = dict(experiments.get("notif_copy_test_v1"), rollout_pct=0)
        self.assertTrue(all(experiments.pick_variant(exp, u) is None for u in range(1, 300)))

    def test_rollout_pct_limits_participants(self):
        exp = dict(experiments.get("notif_copy_test_v1"), rollout_pct=20)
        inside = sum(experiments.pick_variant(exp, u) is not None for u in range(1, 4001)) / 4000
        self.assertTrue(0.15 < inside < 0.25, inside)

    @override_settings(EXPERIMENTS={"notif_copy_test_v1": {**COPY_EXP["notif_copy_test_v1"], "enabled": False}})
    def test_disabled_experiment_is_off(self):
        self.assertIsNone(experiments.get("notif_copy_test_v1"))

    @override_settings(EXPERIMENTS_ENABLED=False)
    def test_master_switch_stops_everything(self):
        self.assertIsNone(experiments.get("notif_copy_test_v1"))
        self.assertIsNone(experiments.copy_experiment_for("streak_at_risk"))

    def test_sample_in_defaults_is_off(self):
        self.assertFalse(experiments.registry()["notif_copy_streak_at_risk_v1"]["enabled"])

    @override_settings(EXPERIMENTS={"bad_one": {"enabled": True, "variants": {"only": {"weight": 1}}}})
    def test_experiment_with_one_variant_is_skipped(self):
        self.assertNotIn("bad_one", experiments.registry())

    @override_settings(EXPERIMENTS={"Bad Key!": {"enabled": True, "variants": {"a": {}, "b": {}}}})
    def test_bad_key_is_skipped(self):
        self.assertNotIn("Bad Key!", experiments.registry())

    def test_never_mute_types_cannot_be_copy_tested(self):
        locked = sorted(NotificationPreference.NEVER_MUTE_TYPES)[0]
        cfg = {"locked_copy_v1": {
            "enabled": True, "notif_types": [locked, "streak_at_risk"],
            "variants": {"a": {"weight": 1}, "b": {"weight": 1, "title": "x"}},
        }}
        with override_settings(EXPERIMENTS=cfg):
            reg = experiments.registry()["locked_copy_v1"]
            self.assertNotIn(locked, reg["notif_types"])
            self.assertIn("streak_at_risk", reg["notif_types"])
            self.assertIsNone(experiments.copy_experiment_for(locked))
        only_locked = {"locked_only_v1": {
            "enabled": True, "notif_types": [locked],
            "variants": {"a": {"weight": 1}, "b": {"weight": 1, "title": "x"}},
        }}
        with override_settings(EXPERIMENTS=only_locked):
            self.assertNotIn("locked_only_v1", experiments.registry())  # would silently become a flag: refused

    def test_flag_kind_when_no_notif_types(self):
        self.assertEqual(experiments.registry()["flag_new_inbox_v1"]["kind"], experiments.KIND_FLAG)
        self.assertEqual(experiments.registry()["notif_copy_test_v1"]["kind"], experiments.KIND_COPY)

    def test_split_tag_only_accepts_registered_pairs(self):
        self.assertEqual(experiments.split_tag("notif_copy_test_v1:b"), ("notif_copy_test_v1", "b"))
        self.assertEqual(experiments.split_tag("notif_copy_test_v1:zzz"), ("", ""))
        self.assertEqual(experiments.split_tag("nope:b"), ("", ""))
        self.assertEqual(experiments.split_tag(""), ("", ""))


@override_settings(EXPERIMENTS=COPY_EXP)
class CopyTests(Base, TestCase):
    def _find_user(self, want):
        for i in range(400):
            u = _user(f"cu{want}{i}")
            if experiments.assign("notif_copy_test_v1", u) == want:
                return u
        self.fail("no user found")

    def test_variant_b_changes_the_push_text(self):
        u = self._find_user("b")
        title, body, tag = experiments.apply_copy(u, "streak_at_risk", "Old", "Keep going")
        self.assertEqual((title, body, tag), ("New title", "Keep going (b)", "notif_copy_test_v1:b"))

    def test_control_keeps_original_but_is_tagged(self):
        u = self._find_user("control")
        self.assertEqual(experiments.apply_copy(u, "streak_at_risk", "Old", "Keep going"),
                         ("Old", "Keep going", "notif_copy_test_v1:control"))

    def test_other_types_are_untouched(self):
        u = self._find_user("b")
        self.assertEqual(experiments.apply_copy(u, "post_liked", "T", "B"), ("T", "B", ""))

    def test_minor_is_never_in_a_copy_experiment(self):
        kid = _user("kidcopy", minor=True)
        self.assertEqual(experiments.apply_copy(kid, "streak_at_risk", "T", "B"), ("T", "B", ""))
        self.assertEqual(experiments.flags_for(kid), {})

    def test_anonymous_gets_original(self):
        self.assertEqual(experiments.apply_copy(None, "streak_at_risk", "T", "B"), ("T", "B", ""))

    def test_stray_braces_in_original_text_do_not_raise(self):
        u = self._find_user("b")
        title, body, tag = experiments.apply_copy(u, "streak_at_risk", "A {x} {", "{0} }")
        self.assertTrue(tag)

    def test_broken_registry_never_breaks_a_push(self):
        u = self._find_user("b")
        with mock.patch.object(experiments, "pick_variant", side_effect=RuntimeError("boom")):
            self.assertEqual(experiments.apply_copy(u, "streak_at_risk", "T", "B"), ("T", "B", ""))


# =====================================================================================================================
# 2. consent gate + counters
# =====================================================================================================================
class ConsentGateTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.u = _user("gate")

    def _summary(self):
        return pa.summary(1)

    def test_never_asked_is_not_collected_by_default(self):
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))
        s = self._summary()
        self.assertEqual(s["events"][pa.EV_PROFILE_EDIT_OPENED]["total"], 0)
        self.assertEqual(s["dropped"]["no_consent"], 1)

    @override_settings(PRODUCT_ANALYTICS_REQUIRE_OPT_IN=False)
    def test_unasked_counts_when_opt_in_not_required(self):
        self.assertTrue(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))

    @override_settings(PRODUCT_ANALYTICS_REQUIRE_OPT_IN=False)
    def test_withdrawn_never_counts_even_without_opt_in_rule(self):
        data_rights.record_consent(self.u, "analytics", False)
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))

    def test_granted_counts(self):
        data_rights.record_consent(self.u, "analytics", True)
        self.assertTrue(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))
        self.assertEqual(self._summary()["events"][pa.EV_PROFILE_EDIT_OPENED]["total"], 1)

    def test_withdrawal_takes_effect_on_the_next_event(self):
        data_rights.record_consent(self.u, "analytics", True)
        self.assertTrue(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))  # consent now cached as granted
        data_rights.record_consent(self.u, "analytics", False)        # must invalidate that cache
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))

    def test_other_purposes_do_not_enable_analytics(self):
        data_rights.record_consent(self.u, "marketing", True)
        data_rights.record_consent(self.u, "personalisation", True)
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))

    def test_minor_is_never_counted_even_with_consent(self):
        kid = _user("kidgate", minor=True)
        data_rights.record_consent(kid, "analytics", True)
        self.assertFalse(pa.track(kid, pa.EV_PROFILE_EDIT_OPENED))

    @override_settings(PRODUCT_ANALYTICS_ENABLED=False)
    def test_master_switch_off(self):
        data_rights.record_consent(self.u, "analytics", True)
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))

    def test_anonymous_is_never_counted(self):
        self.assertFalse(pa.track(None, pa.EV_PROFILE_EDIT_OPENED))

    def test_track_never_raises(self):
        data_rights.record_consent(self.u, "analytics", True)
        with mock.patch.object(pa, "_count", side_effect=RuntimeError("boom")):
            self.assertFalse(pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED))


class VocabularyTests(Base, TestCase):
    """No free text / PII can become a stored label."""

    def setUp(self):
        super().setUp()
        self.u = _user("vocab")
        data_rights.record_consent(self.u, "analytics", True)

    def test_unknown_section_becomes_other(self):
        self.assertTrue(pa.track(self.u, pa.EV_SETTINGS_OPENED, "my.email@example.com"))
        rows = pa.summary(1)["events"][pa.EV_SETTINGS_OPENED]["by"]
        self.assertEqual(rows, [{"dims": {"section": "other"}, "count": 1}])

    def test_key_is_sanitised(self):
        pa.track(self.u, pa.EV_SETTINGS_CHANGED, "privacy", 'Hide From"} 1\n# HELP')
        by = pa.summary(1)["events"][pa.EV_SETTINGS_CHANGED]["by"]
        self.assertEqual(by[0]["dims"]["key"], "hidefrom1help")
        text = pa.prometheus_text()
        # the injected quote / newline / "# HELP" never reaches the exposition text: only the sanitised key does
        self.assertIn('key="hidefrom1help"} 1', text)
        self.assertNotIn("Hide From", text)
        self.assertNotIn('Hide From"} 1', text)

    def test_wrong_number_of_dims_is_invalid(self):
        self.assertFalse(pa.track(self.u, pa.EV_SETTINGS_CHANGED, "privacy"))
        self.assertEqual(pa.summary(1)["dropped"]["invalid"], 0)  # nothing to drop-count: wrong arity is a code bug

    def test_bad_bucket_is_dropped(self):
        self.assertFalse(pa.track(self.u, pa.EV_PROFILE_PROGRESS, "banana"))
        self.assertEqual(pa.summary(1)["dropped"]["invalid"], 1)

    def test_unknown_event_is_ignored(self):
        self.assertFalse(pa.track(self.u, "made_up_event"))

    def test_nothing_in_the_counters_identifies_the_user(self):
        pa.track(self.u, pa.EV_PROFILE_EDIT_OPENED)
        pa.track(self.u, pa.EV_SETTINGS_OPENED, "privacy")
        blob = pa.prometheus_text() + str(pa.summary(1))
        self.assertNotIn(self.u.username, blob)
        self.assertNotIn(self.u.email, blob)
        self.assertNotIn(f"user={self.u.pk}", blob)


class SettingsChangedTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.u = _user("setchg")
        data_rights.record_consent(self.u, "analytics", True)

    class _F:
        def __init__(self, read_only=False):
            self.read_only = read_only

    def test_counts_only_declared_writable_fields_never_values(self):
        fields = {"hide_from_search": self._F(), "updated_at": self._F(read_only=True)}
        data = {"hide_from_search": True, "updated_at": "x", "invented_field": "y"}
        n = pa.track_settings_changed(self.u, "privacy", data, fields)
        self.assertEqual(n, 1)
        by = pa.summary(1)["events"][pa.EV_SETTINGS_CHANGED]["by"]
        self.assertEqual(by, [{"dims": {"section": "privacy", "key": "hide_from_search"}, "count": 1}])
        self.assertNotIn("True", str(by))

    def test_user_pref_sections(self):
        self.assertEqual(pa.user_pref_section("theme"), "appearance")
        self.assertEqual(pa.user_pref_section("font_scale"), "appearance")
        self.assertEqual(pa.user_pref_section("login_alerts"), "security")
        self.assertEqual(pa.user_pref_section("exam_target"), "learning")

    def test_key_cap_per_request(self):
        fields = {f"k{i}": self._F() for i in range(40)}
        n = pa.track_settings_changed(self.u, "other", {k: 1 for k in fields}, fields)
        self.assertEqual(n, pa.MAX_KEYS_PER_REQUEST)


class ProfileTrackingTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.u = _user("proftrk")
        data_rights.record_consent(self.u, "analytics", True)

    def test_new_field_and_progress_bucket(self):
        before = {"percent": 10, "done": ["name"]}
        after = {"percent": 55, "done": ["name", "bio", "photo"]}
        pa.track_profile_change(self.u, before, after)
        ev = pa.summary(1)["events"]
        fields = {r["dims"]["field"] for r in ev[pa.EV_PROFILE_FIELD]["by"]}
        self.assertEqual(fields, {"bio", "photo"})
        self.assertEqual(ev[pa.EV_PROFILE_PROGRESS]["by"][0]["dims"]["bucket"], "50_74")
        self.assertEqual(ev[pa.EV_PROFILE_COMPLETED]["total"], 0)

    def test_completed_only_when_crossing_100(self):
        pa.track_profile_change(self.u, {"percent": 95, "done": []}, {"percent": 100, "done": ["dob"]})
        pa.track_profile_change(self.u, {"percent": 100, "done": ["dob"]}, {"percent": 100, "done": ["dob"]})
        self.assertEqual(pa.summary(1)["events"][pa.EV_PROFILE_COMPLETED]["total"], 1)

    def test_no_progress_counts_nothing(self):
        pa.track_profile_change(self.u, {"percent": 40, "done": ["bio"]}, {"percent": 40, "done": ["bio"]})
        pa.track_profile_change(self.u, {"percent": 40, "done": ["bio"]}, {"percent": 30, "done": []})
        self.assertEqual(pa.summary(1)["events"][pa.EV_PROFILE_PROGRESS]["total"], 0)

    def test_buckets(self):
        self.assertEqual([pa.bucket_of(p) for p in (0, 24, 25, 49, 50, 74, 75, 99, 100)],
                         ["0_24", "0_24", "25_49", "25_49", "50_74", "50_74", "75_99", "75_99", "100"])


@override_settings(EXPERIMENTS=COPY_EXP)
class VariantMeasurementTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.u = _user("varmeas")
        data_rights.record_consent(self.u, "analytics", True)
        self.tag = f"notif_copy_test_v1:{experiments.assign('notif_copy_test_v1', self.u)}"

    def test_pushed_then_opened_gives_open_rate(self):
        self.assertTrue(pa.record_variant_pushed(self.u, 501, self.tag))
        self.assertTrue(pa.record_variant_opened(self.u, 501))
        exp = pa.summary(1)["funnels"]["experiments"]["notif_copy_test_v1"]
        variant = self.tag.split(":")[1]
        self.assertEqual(exp[variant], {"pushed": 1, "opened": 1, "open_rate_pct": 100.0})

    def test_open_without_a_pushed_tag_is_not_attributed(self):
        self.assertFalse(pa.record_variant_opened(self.u, 999))

    def test_no_consent_means_no_pushed_count_and_no_tag(self):
        other = _user("varnoconsent")
        tag = f"notif_copy_test_v1:{experiments.assign('notif_copy_test_v1', other)}"
        self.assertFalse(pa.record_variant_pushed(other, 777, tag))
        self.assertEqual(experiments.tag_of(777), "")

    def test_unknown_variant_in_tag_is_refused(self):
        self.assertFalse(pa.record_variant_pushed(self.u, 502, "notif_copy_test_v1:hacked"))


# =====================================================================================================================
# 3. endpoints
# =====================================================================================================================
@override_settings(EXPERIMENTS={**COPY_EXP, **FLAG_EXP})
class EndpointTests(Base, TestCase):
    def setUp(self):
        super().setUp()
        self.u = _user("apiuser")
        self.client = APIClient()
        self.client.force_authenticate(self.u)

    def _post(self, events):
        return self.client.post("/core/analytics/events/", {"events": events}, format="json")

    def test_config_before_consent(self):
        r = self.client.get("/core/analytics/config/")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["collect"])
        self.assertEqual(r.data["consent"], "unasked")
        self.assertIn("flag_new_inbox_v1", r.data["experiments"])  # a variant is not tracking
        self.assertEqual(r["Cache-Control"], "private, no-store")

    def test_config_after_consent(self):
        data_rights.record_consent(self.u, "analytics", True)
        r = self.client.get("/core/analytics/config/")
        self.assertTrue(r.data["collect"])
        self.assertEqual(r.data["consent"], "granted")

    # ---- X3b: first-run consent card (`can_ask`) ----
    def test_can_ask_for_a_grown_up_who_was_never_asked(self):
        r = self.client.get("/core/analytics/config/")
        self.assertTrue(r.data["can_ask"])

    def test_can_ask_is_false_after_either_answer(self):
        for granted in (True, False):
            u = _user(f"answered{int(granted)}")
            data_rights.record_consent(u, "analytics", granted)
            c = APIClient()
            c.force_authenticate(u)
            self.assertFalse(c.get("/core/analytics/config/").data["can_ask"])

    def test_can_ask_is_false_for_a_minor_and_collection_stays_off(self):
        kid = _user("kidcard", minor=True)
        c = APIClient()
        c.force_authenticate(kid)
        r = c.get("/core/analytics/config/")
        self.assertFalse(r.data["can_ask"])
        self.assertFalse(r.data["collect"])

    @override_settings(PRODUCT_ANALYTICS_ENABLED=False)
    def test_can_ask_is_false_when_analytics_is_switched_off(self):
        self.assertFalse(self.client.get("/core/analytics/config/").data["can_ask"])

    @override_settings(PRODUCT_ANALYTICS_REQUIRE_OPT_IN=False)
    def test_can_ask_is_false_when_consent_is_not_required(self):
        self.assertFalse(self.client.get("/core/analytics/config/").data["can_ask"])

    def test_card_answer_is_recorded_with_its_source_and_closes_the_card(self):
        r = self.client.post("/login/auth/data/consents/",
                             {"purpose": "analytics", "granted": True, "source": "first_run_card"}, format="json")
        self.assertEqual(r.status_code, 200)
        hist = data_rights.consent_history(self.u)
        self.assertEqual(hist[0]["source"], "first_run_card")
        cfg = self.client.get("/core/analytics/config/").data
        self.assertTrue(cfg["collect"])
        self.assertFalse(cfg["can_ask"])

    def test_unknown_consent_source_falls_back_to_app(self):
        self.client.post("/login/auth/data/consents/",
                         {"purpose": "analytics", "granted": False, "source": "<script>"}, format="json")
        self.assertEqual(data_rights.consent_history(self.u)[0]["source"], "app")

    def test_config_requires_login(self):
        self.assertIn(APIClient().get("/core/analytics/config/").status_code, (401, 403))

    def test_events_without_consent_are_accepted_but_not_counted(self):
        r = self._post([{"event": "settings_opened", "section": "privacy"}])
        self.assertEqual(r.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual((r.data["counted"], r.data["consent"]), (0, False))
        self.assertEqual(pa.summary(1)["events"][pa.EV_SETTINGS_OPENED]["total"], 0)
        self.assertEqual(pa.summary(1)["dropped"]["no_consent"], 1)

    def test_events_with_consent_are_counted(self):
        data_rights.record_consent(self.u, "analytics", True)
        r = self._post([{"event": "settings_opened", "section": "privacy"}, {"event": "profile_edit_opened"}])
        self.assertEqual((r.data["accepted"], r.data["counted"], r.data["consent"]), (2, 2, True))

    def test_phone_cannot_send_server_only_events(self):
        data_rights.record_consent(self.u, "analytics", True)
        for name in ("profile_completed", "settings_changed", "notification_variant", "made_up"):
            r = self._post([{"event": name}])
            self.assertEqual(r.status_code, 400, name)

    def test_bad_bodies(self):
        self.assertEqual(self.client.post("/core/analytics/events/", {"events": "x"}, format="json").status_code, 400)
        self.assertEqual(self._post([{"event": "profile_edit_opened"}] * 21).status_code, 400)

    def test_exposure_must_match_own_assignment(self):
        data_rights.record_consent(self.u, "analytics", True)
        mine = experiments.assign("flag_new_inbox_v1", self.u)
        wrong = "on" if mine == "off" else "off"
        r = self._post([{"event": "experiment_exposure", "experiment": "flag_new_inbox_v1", "variant": wrong}])
        self.assertEqual(r.data["counted"], 0)
        r = self._post([{"event": "experiment_exposure", "experiment": "flag_new_inbox_v1", "variant": mine}])
        self.assertEqual(r.data["counted"], 1)

    def test_copy_experiments_are_not_client_reportable(self):
        data_rights.record_consent(self.u, "analytics", True)
        r = self._post([{"event": "experiment_exposure", "experiment": "notif_copy_test_v1", "variant": "b"}])
        self.assertEqual(r.data["counted"], 0)

    # ---- server-side hooks -------------------------------------------------------------------------------------
    def test_privacy_patch_counts_field_name_only(self):
        data_rights.record_consent(self.u, "analytics", True)
        r = self.client.patch("/profile/privacy/", {"hide_from_search": True}, format="json")
        self.assertEqual(r.status_code, 200)
        by = pa.summary(1)["events"][pa.EV_SETTINGS_CHANGED]["by"]
        self.assertEqual(by[0]["dims"], {"section": "privacy", "key": "hide_from_search"})

    def test_privacy_patch_without_consent_counts_nothing(self):
        self.client.patch("/profile/privacy/", {"hide_from_search": True}, format="json")
        self.assertEqual(pa.summary(1)["events"][pa.EV_SETTINGS_CHANGED]["total"], 0)

    def test_notification_opened_counts_type_and_surface(self):
        data_rights.record_consent(self.u, "analytics", True)
        row = Notification.objects.create(recipient=self.u, notif_type=NT.GENERIC, title="Hi")
        r = self.client.post(f"/core/notifications/{row.pk}/opened/", {"surface": "banner"}, format="json")
        self.assertEqual(r.status_code, 200)
        by = pa.summary(1)["events"][pa.EV_NOTIF_OPENED]["by"]
        self.assertEqual(by[0]["dims"]["surface"], "banner")
        # the second tap on the same row is not counted twice (existing N10 once-per-row rule)
        self.client.post(f"/core/notifications/{row.pk}/opened/", {"surface": "banner"}, format="json")
        self.assertEqual(pa.summary(1)["events"][pa.EV_NOTIF_OPENED]["total"], 1)

    def test_withdrawing_consent_stops_collection_immediately(self):
        data_rights.record_consent(self.u, "analytics", True)
        self._post([{"event": "profile_edit_opened"}])
        data_rights.record_consent(self.u, "analytics", False)
        r = self._post([{"event": "profile_edit_opened"}])
        self.assertEqual(r.data["counted"], 0)
        self.assertEqual(pa.summary(1)["events"][pa.EV_PROFILE_EDIT_OPENED]["total"], 1)


class OpsEndpointTests(Base, TestCase):
    def test_staff_only(self):
        c = APIClient()
        self.assertEqual(c.get("/core/analytics/product/").status_code, 403)
        normal = _user("opsnormal")
        c.force_authenticate(normal)
        self.assertEqual(c.get("/core/analytics/product/").status_code, 403)

    @override_settings(PRODUCT_ANALYTICS_SCRAPE_TOKEN="s3cret-token")
    def test_scrape_token_gets_prometheus_text(self):
        r = APIClient().get("/core/analytics/product/?format=prom", HTTP_AUTHORIZATION="Bearer s3cret-token")
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn("# TYPE learnscroll_product_settings_changed_total counter", body)
        self.assertIn("learnscroll_product_profile_completed_total{} 0", body)
        self.assertIn('learnscroll_product_dropped_total{reason="no_consent"}', body)
        self.assertIn("learnscroll_product_analytics_enabled 1", body)

    @override_settings(PRODUCT_ANALYTICS_SCRAPE_TOKEN="s3cret-token")
    def test_wrong_token_is_refused(self):
        r = APIClient().get("/core/analytics/product/?format=prom", HTTP_AUTHORIZATION="Bearer nope")
        self.assertEqual(r.status_code, 403)

    @override_settings(PRODUCT_ANALYTICS_SCRAPE_TOKEN="s3cret-token")
    def test_json_summary(self):
        r = APIClient().get("/core/analytics/product/?days=3", HTTP_AUTHORIZATION="Bearer s3cret-token")
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertEqual(data["window"]["days"], 3)
        self.assertIn("funnels", data)
        self.assertIn("dropped", data)

    @override_settings(EXPERIMENTS=COPY_EXP, PRODUCT_ANALYTICS_SCRAPE_TOKEN="t")
    def test_running_experiments_are_exported_as_a_gauge(self):
        body = APIClient().get("/core/analytics/product/?format=prom", HTTP_AUTHORIZATION="Bearer t").content.decode()
        self.assertIn('learnscroll_experiment_running{experiment="notif_copy_test_v1",kind="copy"} 1', body)
