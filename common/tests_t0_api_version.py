"""
T0 - `/v1/...` alias must keep working (written by reading the code; NOT run yet).
Run: python manage.py test common.tests_t0_api_version

Root cause that was fixed: a missing comma after `'common.api_version.ApiVersionMiddleware'` in settings.MIDDLEWARE glued two
dotted paths together, so the middleware never loaded and every `/v1/...` URL answered 404.
"""
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from common.api_version import ApiVersionMiddleware, strip_version_prefix


class MiddlewareSettingsTests(SimpleTestCase):
    def test_api_version_middleware_is_its_own_entry(self):
        self.assertIn("common.api_version.ApiVersionMiddleware", settings.MIDDLEWARE)

    def test_no_middleware_entry_is_two_paths_glued_together(self):
        for entry in settings.MIDDLEWARE:
            self.assertEqual(entry.count("Middleware"), 1, "missing comma? -> %r" % entry)

    def test_every_middleware_entry_imports(self):
        from django.utils.module_loading import import_string

        for entry in settings.MIDDLEWARE:
            import_string(entry)

    def test_class_is_importable(self):
        self.assertTrue(callable(ApiVersionMiddleware))


class StripPrefixTests(SimpleTestCase):
    def test_strip(self):
        self.assertEqual(strip_version_prefix("/v1/core/app-config/"), ("/core/app-config/", 1))
        self.assertEqual(strip_version_prefix("/core/app-config/"), ("/core/app-config/", 0))
        self.assertEqual(strip_version_prefix("/v10/x/"), ("/v10/x/", 0))
        self.assertEqual(strip_version_prefix("/v1"), ("/v1", 0))


class AppConfigBothPathsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()  # public endpoint: no token

    def test_versioned_and_plain_app_config_both_200(self):
        plain = self.client.get("/core/app-config/", {"platform": "android", "version": "1.0.0"})
        versioned = self.client.get("/v1/core/app-config/", {"platform": "android", "version": "1.0.0"})
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(versioned.status_code, 200)
        self.assertEqual(versioned["X-API-Version"], "1")
        # `server_time` is stamped per request, so two calls a few microseconds apart can never be byte-equal
        strip = lambda d: {k: v for k, v in dict(d).items() if k != "server_time"}  # noqa: E731
        self.assertEqual(strip(plain.data), strip(versioned.data))

    def test_unknown_v1_path_is_still_404(self):
        self.assertEqual(self.client.get("/v1/definitely/not/here/").status_code, 404)

    def test_login_alias_reaches_the_same_view(self):
        # POST with no body is a 400 (validation), NOT a 404 - the route was found through /v1.
        res = self.client.post("/v1/login/", {}, format="json")
        self.assertNotEqual(res.status_code, 404)
