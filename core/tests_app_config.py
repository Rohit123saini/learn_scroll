# core/tests_app_config.py
"""
T1.8 (R8) — `GET /core/app-config/` force-update / maintenance config.

Pins: version parsing/compare (1.10 > 1.9), fail-open on garbage, PUBLIC
access (no token), settings defaults vs DB-row override, per-platform rows,
store-url fallback, maintenance flag, cache header.
"""
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from .app_config import is_older, normalize_platform, parse_version
from .models import AppConfig


class VersionHelperTests(SimpleTestCase):
    def test_parse(self):
        self.assertEqual(parse_version("1.4.2"), (1, 4, 2))
        self.assertEqual(parse_version("1.4.2+17"), (1, 4, 2))
        self.assertEqual(parse_version("v2.0-beta"), (2, 0))
        self.assertEqual(parse_version("3"), (3,))
        for bad in (None, "", "abc", "x.y"):
            self.assertIsNone(parse_version(bad))

    def test_is_older_numeric_not_lexicographic(self):
        self.assertTrue(is_older("1.9.3", "1.10.0"))
        self.assertFalse(is_older("1.10.0", "1.9.3"))
        self.assertFalse(is_older("1.2.0", "1.2"))      # trailing zeros equal
        self.assertTrue(is_older("1.2", "1.2.1"))
        self.assertFalse(is_older("1.2.0", "1.2.0"))

    def test_garbage_fails_open(self):
        self.assertFalse(is_older("garbage", "1.0.0"))
        self.assertFalse(is_older("1.0.0", "garbage"))
        self.assertFalse(is_older(None, "1.0.0"))
        self.assertFalse(is_older("1.0.0", ""))

    def test_platform(self):
        self.assertEqual(normalize_platform(" Android "), "android")
        self.assertEqual(normalize_platform("IOS"), "ios")
        self.assertEqual(normalize_platform("windows"), "")
        self.assertEqual(normalize_platform(None), "")


class AppConfigEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()  # deliberately unauthenticated
        self.url = reverse("app-config")

    def _get(self, **params):
        return self.client.get(self.url, params)

    def test_public_no_token_needed(self):
        res = self._get(platform="android", version="1.0.0")
        self.assertEqual(res.status_code, 200)

    def test_cache_header(self):
        self.assertEqual(self._get()["Cache-Control"], "public, max-age=60")

    @override_settings(APP_MIN_SUPPORTED_VERSION="", APP_LATEST_VERSION="", APP_MAINTENANCE_MODE=False)
    def test_nothing_configured_never_forces(self):
        data = self._get(platform="android", version="0.0.1").data
        self.assertFalse(data["update_required"])
        self.assertFalse(data["update_available"])
        self.assertFalse(data["maintenance"]["active"])
        self.assertIsNone(data["min_supported_version"])

    @override_settings(APP_MIN_SUPPORTED_VERSION="1.5.0", APP_LATEST_VERSION="1.8.0")
    def test_settings_defaults_drive_decision(self):
        old = self._get(platform="android", version="1.4.9").data
        self.assertTrue(old["update_required"])
        self.assertTrue(old["update_available"])
        mid = self._get(platform="android", version="1.6.0").data
        self.assertFalse(mid["update_required"])
        self.assertTrue(mid["update_available"])
        new = self._get(platform="android", version="1.8.0+3").data
        self.assertFalse(new["update_required"])
        self.assertFalse(new["update_available"])

    @override_settings(APP_MIN_SUPPORTED_VERSION="1.5.0")
    def test_missing_or_bad_version_fails_open_but_returns_floors(self):
        for params in ({"platform": "android"}, {"platform": "android", "version": "zzz"}):
            data = self._get(**params).data
            self.assertFalse(data["update_required"])
            self.assertEqual(data["min_supported_version"], "1.5.0")

    @override_settings(APP_MIN_SUPPORTED_VERSION="1.0.0", PLAY_STORE_URL="https://play/x", APP_STORE_URL="https://apple/x")
    def test_db_row_overrides_settings_per_platform(self):
        AppConfig.objects.create(
            platform="android", min_supported_version="2.0.0", latest_version="2.1.0",
            update_message="Please update", update_message_hi="कृपया अपडेट करें",
        )
        a = self._get(platform="android", version="1.9.0").data
        self.assertTrue(a["update_required"])
        self.assertEqual(a["min_supported_version"], "2.0.0")
        self.assertEqual(a["message"], "Please update")
        self.assertEqual(a["message_hi"], "कृपया अपडेट करें")
        self.assertEqual(a["store_url"], "https://play/x")  # blank row.store_url -> setting
        # iOS has no row -> settings defaults, apple store link.
        i = self._get(platform="ios", version="1.9.0").data
        self.assertFalse(i["update_required"])
        self.assertEqual(i["store_url"], "https://apple/x")

    def test_row_store_url_wins(self):
        AppConfig.objects.create(platform="ios", store_url="https://apps.apple.com/app/id9")
        self.assertEqual(self._get(platform="ios").data["store_url"], "https://apps.apple.com/app/id9")

    def test_misconfigured_latest_below_min_still_required(self):
        AppConfig.objects.create(platform="android", min_supported_version="2.0.0", latest_version="1.0.0")
        data = self._get(platform="android", version="1.5.0").data
        self.assertTrue(data["update_required"])
        self.assertTrue(data["update_available"])

    def test_maintenance_from_row(self):
        AppConfig.objects.create(
            platform="android", maintenance_mode=True,
            maintenance_message="Back at 6 PM", maintenance_message_hi="शाम 6 बजे तक",
        )
        m = self._get(platform="android", version="9.9.9").data["maintenance"]
        self.assertTrue(m["active"])
        self.assertEqual(m["message"], "Back at 6 PM")
        self.assertEqual(m["message_hi"], "शाम 6 बजे तक")

    @override_settings(APP_MAINTENANCE_MODE=True, APP_MAINTENANCE_MESSAGE="Env maintenance")
    def test_maintenance_from_env_when_no_row(self):
        m = self._get(platform="ios", version="1.0.0").data["maintenance"]
        self.assertTrue(m["active"])
        self.assertEqual(m["message"], "Env maintenance")

    def test_unknown_platform_is_tolerated(self):
        data = self._get(platform="windows", version="1.0.0").data
        self.assertIsNone(data["platform"])
        self.assertFalse(data["update_required"])

    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)


class FeedPreviewSecondsTests(SimpleTestCase):
    """T5 - `feed_preview_seconds` lets ops change the feed preview length without an app release."""

    def test_default_is_15(self):
        from .app_config import feed_preview_seconds
        with override_settings(FEED_PREVIEW_SECONDS=15):
            self.assertEqual(feed_preview_seconds(), 15)

    def test_override_and_clamp(self):
        from .app_config import feed_preview_seconds
        with override_settings(FEED_PREVIEW_SECONDS=30):
            self.assertEqual(feed_preview_seconds(), 30)
        with override_settings(FEED_PREVIEW_SECONDS=0):
            self.assertEqual(feed_preview_seconds(), 3)
        with override_settings(FEED_PREVIEW_SECONDS=100000):
            self.assertEqual(feed_preview_seconds(), 120)
        with override_settings(FEED_PREVIEW_SECONDS="abc"):
            self.assertEqual(feed_preview_seconds(), 15)


class FeedPreviewInResponseTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(FEED_PREVIEW_SECONDS=20)
    def test_in_public_response(self):
        res = APIClient().get("/v1/core/app-config/", {"platform": "android", "version": "1.0.0"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["feed_preview_seconds"], 20)


class CreateLimitsConfigTests(TestCase):
    """P3-4 - post / story video limits come from the server (defaults = old app constants 90 / 60)."""

    def setUp(self):
        cache.clear()

    def test_defaults(self):
        from .app_config import post_video_max_seconds, story_video_max_seconds
        self.assertEqual(post_video_max_seconds(), 90)
        self.assertEqual(story_video_max_seconds(), 60)

    def test_override_clamp_and_garbage(self):
        from .app_config import post_video_max_seconds, story_video_max_seconds
        with override_settings(POST_VIDEO_MAX_SECONDS=120, STORY_VIDEO_MAX_SECONDS=30):
            self.assertEqual(post_video_max_seconds(), 120)
            self.assertEqual(story_video_max_seconds(), 30)
        with override_settings(POST_VIDEO_MAX_SECONDS=0, STORY_VIDEO_MAX_SECONDS=99999):
            self.assertEqual(post_video_max_seconds(), 5)
            self.assertEqual(story_video_max_seconds(), 600)
        with override_settings(POST_VIDEO_MAX_SECONDS="abc", STORY_VIDEO_MAX_SECONDS=None):
            self.assertEqual(post_video_max_seconds(), 90)
            self.assertEqual(story_video_max_seconds(), 60)

    @override_settings(POST_VIDEO_MAX_SECONDS=100, STORY_VIDEO_MAX_SECONDS=45)
    def test_in_public_response(self):
        res = APIClient().get("/v1/core/app-config/", {"platform": "android", "version": "1.0.0"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["post_video_max_seconds"], 100)
        self.assertEqual(res.json()["story_video_max_seconds"], 45)

    def test_reel_limit_default_override_and_response(self):
        from .app_config import reel_video_max_seconds
        self.assertEqual(reel_video_max_seconds(), 180)
        with override_settings(REEL_VIDEO_MAX_SECONDS=120):
            self.assertEqual(reel_video_max_seconds(), 120)
            res = APIClient().get("/v1/core/app-config/", {"platform": "android", "version": "1.0.0"})
            self.assertEqual(res.json()["reel_video_max_seconds"], 120)
        with override_settings(REEL_VIDEO_MAX_SECONDS=99999):
            self.assertEqual(reel_video_max_seconds(), 600)
        with override_settings(REEL_VIDEO_MAX_SECONDS="abc"):
            self.assertEqual(reel_video_max_seconds(), 180)
