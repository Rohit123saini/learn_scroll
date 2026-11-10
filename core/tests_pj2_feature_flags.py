"""
P-J2 - remote kill switches + staged rollout. NOT RUN by the author (read-only task):
    python manage.py test core.tests_pj2_feature_flags

Pins: no row = default ON (cash-out OFF); admin OFF is a kill switch for everyone (server refuses with 403 feature_disabled);
rollout percentage is stable per device and monotonic (10% devices stay in at 50%); min-version floor; platform row beats the
'all' row; env FEATURE_FLAGS_OFF works without a row; cash-out can be tightened by a row but never opened by one; app-config
carries every flag and switches to a private cache only while a partial rollout exists; the gate middleware refuses only
the listed routes (webhooks / confirm / quality tips stay open); reels upload is refused for kind=reel only; the optional hard
min-version gate answers 426 only for a too-old X-App-Version.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from common import feature_flags as ff
from common.feature_gate import matching_flag
from .models import AppConfig, FeatureFlag

User = get_user_model()


class _Base(TestCase):
    def setUp(self):
        cache.clear()

    def flag(self, key, **kw):
        row = FeatureFlag.objects.create(key=key, **kw)
        ff.invalidate_cache()
        return row


class ResolverTests(_Base):
    def test_defaults_everything_on_except_cashout(self):
        for key in (ff.LIVE, ff.REELS_UPLOAD, ff.COIN_PURCHASE, ff.AI_FEATURES):
            self.assertTrue(ff.evaluate(key).enabled, key)
        self.assertFalse(ff.evaluate(ff.CASHOUT).enabled)

    def test_admin_off_is_a_kill_switch(self):
        self.flag(ff.LIVE, enabled=False, message="Live paused")
        state = ff.evaluate(ff.LIVE, "android", "1.0.0", "dev1")
        self.assertFalse(state.enabled)
        self.assertEqual(state.reason, "admin_off")
        self.assertEqual(state.message, "Live paused")
        self.assertIsNotNone(ff.server_blocked(ff.LIVE, "android"))

    def test_platform_row_beats_all_row(self):
        self.flag(ff.AI_FEATURES, platform="all", enabled=True)
        self.flag(ff.AI_FEATURES, platform="ios", enabled=False)
        self.assertFalse(ff.evaluate(ff.AI_FEATURES, "ios").enabled)
        self.assertTrue(ff.evaluate(ff.AI_FEATURES, "android").enabled)

    @override_settings(FEATURE_FLAGS_OFF="live, ai_features")
    def test_env_emergency_off_without_any_row(self):
        self.assertEqual(ff.evaluate(ff.LIVE).reason, "env_off")
        self.assertFalse(ff.evaluate(ff.AI_FEATURES).enabled)
        self.assertTrue(ff.evaluate(ff.COIN_PURCHASE).enabled)

    def test_rollout_is_stable_per_device_and_monotonic(self):
        devices = ["dev%03d" % i for i in range(400)]
        self.flag(ff.REELS_UPLOAD, rollout_percent=10)
        at10 = {d for d in devices if ff.evaluate(ff.REELS_UPLOAD, "android", "1.0.0", d).enabled}
        again = {d for d in devices if ff.evaluate(ff.REELS_UPLOAD, "android", "1.0.0", d).enabled}
        self.assertEqual(at10, again)  # stable
        self.assertTrue(20 <= len(at10) <= 60, len(at10))  # ~10% of 400
        FeatureFlag.objects.filter(key=ff.REELS_UPLOAD).update(rollout_percent=50)
        ff.invalidate_cache()
        at50 = {d for d in devices if ff.evaluate(ff.REELS_UPLOAD, "android", "1.0.0", d).enabled}
        self.assertTrue(at10 <= at50)  # raising the percentage never removes a device
        self.assertTrue(120 <= len(at50) <= 280, len(at50))

    def test_partial_rollout_without_device_id_is_off_and_zero_percent_is_off(self):
        self.flag(ff.REELS_UPLOAD, rollout_percent=50)
        self.assertEqual(ff.evaluate(ff.REELS_UPLOAD, "android", "1.0.0", None).reason, "not_in_rollout")
        FeatureFlag.objects.filter(key=ff.REELS_UPLOAD).update(rollout_percent=0)
        ff.invalidate_cache()
        self.assertFalse(ff.evaluate(ff.REELS_UPLOAD, "android", "1.0.0", "dev1").enabled)

    def test_rollout_never_blocks_on_the_server(self):
        self.flag(ff.REELS_UPLOAD, rollout_percent=0)  # nobody SEES it, but the server only refuses when `enabled` is off
        self.assertIsNone(ff.server_blocked(ff.REELS_UPLOAD, "android"))

    def test_min_app_version_floor(self):
        self.flag(ff.LIVE, min_app_version="1.5.0")
        self.assertFalse(ff.evaluate(ff.LIVE, "android", "1.4.9").enabled)
        self.assertFalse(ff.evaluate(ff.LIVE, "android", "").enabled)  # unknown build never gets a staged feature
        self.assertFalse(ff.evaluate(ff.LIVE, "android", "junk").enabled)
        self.assertTrue(ff.evaluate(ff.LIVE, "android", "1.10.0+3").enabled)  # numeric, not lexicographic

    @override_settings(COIN_CASHOUT_ENABLED=False)
    def test_a_row_can_never_open_cashout(self):
        self.flag(ff.CASHOUT, enabled=True)
        self.assertFalse(ff.coin_cashout_enabled())
        self.assertFalse(ff.evaluate(ff.CASHOUT).enabled)

    @override_settings(COIN_CASHOUT_ENABLED=True)
    def test_a_row_can_close_cashout_everywhere(self):
        self.assertTrue(ff.coin_cashout_enabled())
        self.flag(ff.CASHOUT, enabled=False)
        self.assertFalse(ff.coin_cashout_enabled())  # the existing wallet / withdrawal views call this function

    def test_database_problem_returns_defaults_not_an_exception(self):
        from unittest import mock

        cache.clear()
        with mock.patch("core.models.FeatureFlag.objects") as objects:
            objects.all.side_effect = RuntimeError("db down")
            self.assertTrue(ff.evaluate(ff.LIVE).enabled)

    def test_saving_a_row_clears_the_cache_at_once(self):
        self.assertTrue(ff.evaluate(ff.LIVE).enabled)  # caches "no rows"
        FeatureFlag.objects.create(key=ff.LIVE, enabled=False)  # post_save signal must clear it
        self.assertFalse(ff.evaluate(ff.LIVE).enabled)


class AppConfigTests(_Base):
    URL = "/core/app-config/"

    def test_every_flag_is_present_and_old_key_is_kept(self):
        body = APIClient().get(self.URL, {"platform": "android", "version": "1.0.0"}).json()
        self.assertEqual(set(body["feature_flags"]), set(ff.ALL_FLAGS))
        self.assertIn("coin_cashout_enabled", body)
        self.assertEqual(body["feature_flags"]["live"]["enabled"], True)

    def test_off_flag_carries_message_and_reason(self):
        self.flag(ff.COIN_PURCHASE, enabled=False, message_hi="Band hai")
        item = APIClient().get(self.URL, {"platform": "android", "version": "1.0.0"}).json()["feature_flags"]["coin_purchase"]
        self.assertFalse(item["enabled"])
        self.assertEqual(item["reason"], "admin_off")
        self.assertEqual(item["message_hi"], "Band hai")

    def test_device_id_header_decides_rollout(self):
        self.flag(ff.AI_FEATURES, rollout_percent=50)
        seen = set()
        for i in range(60):
            res = APIClient().get(self.URL, {"platform": "android", "version": "1.0.0"}, HTTP_X_DEVICE_ID="d%d" % i)
            seen.add(res.json()["feature_flags"]["ai_features"]["enabled"])
        self.assertEqual(seen, {True, False})

    def test_cache_is_public_until_a_partial_rollout_exists(self):
        res = APIClient().get(self.URL, {"platform": "android", "version": "1.0.0"})
        self.assertIn("public", res["Cache-Control"])
        self.flag(ff.AI_FEATURES, rollout_percent=20)
        res = APIClient().get(self.URL, {"platform": "android", "version": "1.0.0"})
        self.assertIn("private", res["Cache-Control"])
        self.assertIn("X-Device-Id", res["Vary"])


class RouteTableTests(SimpleTestCase):
    def test_gated_routes(self):
        self.assertEqual(matching_flag("POST", "/tuitionclass/live/go/"), ff.LIVE)
        self.assertEqual(matching_flag("POST", "/tuitionclass/sessions/12/join/"), ff.LIVE)
        self.assertEqual(matching_flag("POST", "/tuitionclass/sessions/12/token/"), ff.LIVE)
        self.assertEqual(matching_flag("POST", "/tuitionclass/classrooms/7/start-or-join/"), ff.LIVE)
        self.assertEqual(matching_flag("POST", "/tuitionclass/classrooms/7/trial-join/"), ff.LIVE)
        self.assertEqual(matching_flag("POST", "/profile/buy-coin/order/"), ff.COIN_PURCHASE)
        self.assertEqual(matching_flag("POST", "/profile/buy-coin/"), ff.COIN_PURCHASE)
        self.assertEqual(matching_flag("POST", "/post/ai-assist/caption/"), ff.AI_FEATURES)
        self.assertEqual(matching_flag("POST", "/post/ai-assist/questions/"), ff.AI_FEATURES)
        self.assertEqual(matching_flag("POST", "/post/3f2b8a52-1c3d-4e5f-8a9b-0c1d2e3f4a5b/ai-study/"), ff.AI_FEATURES)
        self.assertEqual(matching_flag("POST", "/message/ai/study-buddy/"), ff.AI_FEATURES)
        self.assertEqual(matching_flag("POST", "/message/ai-study-room/"), ff.AI_FEATURES)

    def test_never_gated(self):
        for method, path in [
            ("POST", "/profile/buy-coin/webhook/razorpay/"),   # a paid order must still settle
            ("POST", "/profile/buy-coin/confirm/"),
            ("POST", "/profile/buy-coin/confirm/razorpay/"),
            ("POST", "/profile/buy-coin/admin-confirm/"),
            ("GET", "/profile/buy-coin/order/"),
            ("POST", "/post/ai-assist/quality/"),               # free tips, no AI
            ("GET", "/post/ai-assist/config/"),
            ("POST", "/tuitionclass/sessions/12/end/"),         # a running class must be endable
            ("POST", "/tuitionclass/sessions/12/leave/"),
            ("POST", "/tuitionclass/livekit-webhook/"),
            ("GET", "/tuitionclass/classrooms/7/has-access/"),  # reading is never gated
            ("GET", "/tuitionclass/live/upcoming/"),
            ("POST", "/core/app-config/"),
        ]:
            self.assertIsNone(matching_flag(method, path), (method, path))


class GateMiddlewareTests(_Base):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="pj2user", email="pj2@example.com", password="pass12345")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_switched_off_feature_is_refused_with_the_standard_body(self):
        self.flag(ff.AI_FEATURES, enabled=False)
        res = self.api.post("/post/ai-assist/caption/", {"text": "x"}, format="json")
        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertEqual(body["code"], "feature_disabled")
        self.assertEqual(body["feature"], "ai_features")
        self.assertFalse(body["status"])

    def test_v1_prefix_is_gated_too(self):
        self.flag(ff.AI_FEATURES, enabled=False)
        res = self.api.post("/v1/post/ai-assist/caption/", {"text": "x"}, format="json")
        self.assertEqual(res.status_code, 403)

    def test_coin_order_refused_but_confirm_still_reaches_its_view(self):
        self.flag(ff.COIN_PURCHASE, enabled=False)
        self.assertEqual(self.api.post("/profile/buy-coin/order/", {"coins": 100}, format="json").status_code, 403)
        # confirm is NOT gated: whatever it answers, it must not be our 403 feature_disabled
        res = self.api.post("/profile/buy-coin/confirm/", {}, format="json")
        self.assertNotEqual(res.json().get("code") if res.status_code == 403 else None, "feature_disabled")

    def test_platform_header_selects_the_row(self):
        self.flag(ff.LIVE, platform="ios", enabled=False)
        ios = self.api.post("/tuitionclass/live/go/", {}, format="json", HTTP_X_PLATFORM="ios")
        self.assertEqual(ios.status_code, 403)
        android = self.api.post("/tuitionclass/live/go/", {}, format="json", HTTP_X_PLATFORM="android")
        self.assertNotEqual(android.json().get("code") if android.status_code == 403 else None, "feature_disabled")

    def test_switching_back_on_reopens_it_at_once(self):
        row = self.flag(ff.AI_FEATURES, enabled=False)
        self.assertEqual(self.api.post("/post/ai-assist/caption/", {}, format="json").status_code, 403)
        row.enabled = True
        row.save()
        res = self.api.post("/post/ai-assist/caption/", {}, format="json")
        self.assertFalse(res.status_code == 403 and res.json().get("code") == "feature_disabled")

    def test_reel_upload_refused_only_for_kind_reel(self):
        self.flag(ff.REELS_UPLOAD, enabled=False)
        reel = self.api.post("/post/uploads/", {"idempotency_key": "k-reel-1", "kind": "reel", "payload": {}, "files": []}, format="json")
        self.assertEqual(reel.status_code, 403)
        self.assertEqual(reel.json()["code"], "feature_disabled")
        self.assertEqual(reel.json()["feature"], "reels_upload")
        post = self.api.post("/post/uploads/", {"idempotency_key": "k-post-1", "kind": "post", "payload": {}, "files": []}, format="json")
        self.assertNotEqual(post.json().get("code"), "feature_disabled")  # may fail validation, must not be the kill switch

    @override_settings(APP_HARD_MIN_VERSION_GATE=True)
    def test_hard_version_gate_answers_426_only_for_old_builds(self):
        AppConfig.objects.create(platform="android", min_supported_version="2.0.0")
        cache.clear()
        old = self.api.get("/post/reels/", HTTP_X_PLATFORM="android", HTTP_X_APP_VERSION="1.9.9")
        self.assertEqual(old.status_code, 426)
        self.assertEqual(old.json()["code"], "UPDATE_REQUIRED")
        self.assertNotEqual(self.api.get("/post/reels/", HTTP_X_PLATFORM="android", HTTP_X_APP_VERSION="2.0.0").status_code, 426)
        self.assertNotEqual(self.api.get("/post/reels/").status_code, 426)  # no header (web / curl / webhook) is never blocked
        gate_exempt = self.api.get("/core/app-config/", {"platform": "android", "version": "1.0.0"},
                                   HTTP_X_PLATFORM="android", HTTP_X_APP_VERSION="1.0.0")
        self.assertEqual(gate_exempt.status_code, 200)  # the old build must still be able to learn it has to update

    def test_hard_version_gate_is_off_by_default(self):
        AppConfig.objects.create(platform="android", min_supported_version="2.0.0")
        cache.clear()
        res = self.api.get("/post/reels/", HTTP_X_PLATFORM="android", HTTP_X_APP_VERSION="1.0.0")
        self.assertNotEqual(res.status_code, 426)


class DisabledHeaderTests(_Base):
    """The app's ApiClient flips a flag OFF from the `X-Feature-Disabled` header alone (no body parsing)."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="pj2hdr", email="pj2hdr@example.com", password="pass12345")
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_gate_refusal_names_the_flag_in_a_header(self):
        self.flag(ff.LIVE, enabled=False)
        res = self.api.post("/tuitionclass/live/go/", {}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res["X-Feature-Disabled"], "live")

    def test_reel_upload_refusal_names_the_flag_in_a_header(self):
        self.flag(ff.REELS_UPLOAD, enabled=False)
        res = self.api.post("/post/uploads/", {"idempotency_key": "k-reel-hdr", "kind": "reel", "payload": {}, "files": []}, format="json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res["X-Feature-Disabled"], "reels_upload")

    def test_an_allowed_call_has_no_such_header(self):
        res = self.api.get("/post/reels/")
        self.assertNotIn("X-Feature-Disabled", res)

    def test_drf_helper_sets_the_header_too(self):
        response = ff.flag_disabled_response(ff.COIN_PURCHASE)
        self.assertEqual(response["X-Feature-Disabled"], "coin_purchase")
        self.assertEqual(response.status_code, 403)


class SeedCommandTests(_Base):
    def test_seed_creates_missing_rows_once_and_never_overwrites(self):
        from io import StringIO

        from django.core.management import call_command

        FeatureFlag.objects.create(key=ff.LIVE, platform="all", enabled=False, note="incident")
        ff.invalidate_cache()
        call_command("seed_feature_flags", stdout=StringIO())
        self.assertEqual(FeatureFlag.objects.filter(platform="all").count(), len(ff.ALL_FLAGS))
        self.assertFalse(FeatureFlag.objects.get(key=ff.LIVE, platform="all").enabled)  # the OFF switch survived the seed
        call_command("seed_feature_flags", stdout=StringIO())
        self.assertEqual(FeatureFlag.objects.count(), len(ff.ALL_FLAGS))  # idempotent
        self.assertTrue(ff.evaluate(ff.AI_FEATURES).enabled)
        self.assertFalse(ff.evaluate(ff.CASHOUT).enabled)  # a seeded cashout row never opens cash-out

    def test_status_flag_changes_nothing(self):
        from io import StringIO

        from django.core.management import call_command

        call_command("seed_feature_flags", "--status", stdout=StringIO())
        self.assertEqual(FeatureFlag.objects.count(), 0)
