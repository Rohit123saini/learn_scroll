"""P-B1 -- API docs lock (Swagger / Redoc / OpenAPI schema). Written by reading the code only - NOT run.

    python manage.py test core.tests_api_docs_lock          # everything (needs the test DB)
    python -m unittest core.tests_api_docs_lock.DocsCheckerRuleTests   # pure rules, no DB / no Django

What is locked (LearnScroll/settings.py -> SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"], default IsAdminUser):
    /api/schema/   /swagger/   /redoc/   /tuitionclass/schema/   /tuitionclass/schema/docs/
Anonymous -> 401, logged-in non-staff -> 403, staff -> 200. `API_DOCS_PUBLIC=true` opens them (dev only).

Note: drf-spectacular reads SERVE_PERMISSIONS once, when drf_spectacular.views is imported, so a test cannot flip it with
override_settings. The "public" switch is therefore tested through the checkers (release_check / check_security_settings)
and through the value settings.py actually produced, not by re-importing the views.
"""
import unittest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from rest_framework.test import APIClient

from core.release_check import ERROR, WARN, check_env
from core.security_settings_check import CSRF_MW, SECURITY_MW, check_settings

User = get_user_model()

DOC_URLS = (
    "/api/schema/",
    "/swagger/",
    "/redoc/",
    "/tuitionclass/schema/",
    "/tuitionclass/schema/docs/",
)
# Rendering the real schema walks every view of the project (slow); the two HTML pages need no schema, so the
# "staff gets in" checks use those.
HTML_DOC_URLS = ("/swagger/", "/redoc/")
OPEN = "rest_framework.permissions.AllowAny"
STAFF_ONLY = "rest_framework.permissions.IsAdminUser"


# ----------------------------------------------------------------------------------------------------------------------
# 1. Real HTTP behaviour
# ----------------------------------------------------------------------------------------------------------------------
class DocsAreLockedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.student = User.objects.create_user("docs_student", "docs_student@x.com", "Tr1cky-Student#Pass-2026")
        cls.staff = User.objects.create_user("docs_staff", "docs_staff@x.com", "Tr1cky-Staff#Pass-2026", is_staff=True)

    def test_anonymous_gets_nothing(self):
        for url in DOC_URLS:
            with self.subTest(url=url):
                r = Client().get(url)
                self.assertIn(r.status_code, (401, 403, 404), f"{url} answered {r.status_code} to an anonymous caller")
                # the body must not leak the schema / the UI shell either way
                body = r.content.decode("utf-8", "ignore").lower()
                self.assertNotIn("openapi", body)
                self.assertNotIn("swagger-ui", body)

    def test_anonymous_api_client_gets_401_with_challenge(self):
        # JWT authenticator is first in SERVE_AUTHENTICATION, so DRF can send WWW-Authenticate -> 401 (not a bare 403).
        r = APIClient().get("/api/schema/")
        self.assertEqual(r.status_code, 401)
        self.assertIn("WWW-Authenticate", r)

    def test_logged_in_non_staff_is_forbidden(self):
        api = APIClient()
        api.force_authenticate(self.student)
        for url in DOC_URLS:
            with self.subTest(url=url):
                self.assertEqual(api.get(url).status_code, 403)

    def test_non_staff_browser_session_is_forbidden(self):
        c = Client()
        c.force_login(self.student)
        for url in HTML_DOC_URLS:
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 403)

    def test_staff_session_can_open_the_ui_pages(self):
        # the way a staff member really uses it: log in (at /admin/), then open /swagger/ in the same browser
        c = Client()
        c.force_login(self.staff)
        for url in HTML_DOC_URLS:
            with self.subTest(url=url):
                self.assertEqual(c.get(url).status_code, 200)

    def test_staff_token_can_open_the_ui_pages(self):
        api = APIClient()
        api.force_authenticate(self.staff)
        for url in HTML_DOC_URLS:
            with self.subTest(url=url):
                self.assertEqual(api.get(url).status_code, 200)


# ----------------------------------------------------------------------------------------------------------------------
# 2. What settings.py actually produced
# ----------------------------------------------------------------------------------------------------------------------
class DocsSettingsTests(unittest.TestCase):
    def test_serve_permissions_follow_the_public_flag(self):
        serve = settings.SPECTACULAR_SETTINGS.get("SERVE_PERMISSIONS")
        self.assertTrue(serve, "SERVE_PERMISSIONS must be set (drf-spectacular's default is AllowAny)")
        if getattr(settings, "API_DOCS_PUBLIC", False):
            self.assertEqual(list(serve), [OPEN])
        else:
            self.assertEqual(list(serve), [STAFF_ONLY])

    def test_public_flag_is_off_in_the_test_environment(self):
        # CI / `make test-backend` never set API_DOCS_PUBLIC. If this fails, someone exported it in the shell or .env.
        self.assertFalse(getattr(settings, "API_DOCS_PUBLIC", None), "API_DOCS_PUBLIC must default to False")

    def test_authentication_lets_staff_use_a_browser_session(self):
        auth = list(settings.SPECTACULAR_SETTINGS.get("SERVE_AUTHENTICATION") or [])
        self.assertEqual(auth[0], "login.authentication.SlidingSessionAuthentication")  # JWT first -> 401 + challenge
        self.assertIn("rest_framework.authentication.SessionAuthentication", auth)


# ----------------------------------------------------------------------------------------------------------------------
# 3. robots.txt / sitemap already keep crawlers out (P-B1 "confirm")
# ----------------------------------------------------------------------------------------------------------------------
class DocsAreNotCrawlableTests(TestCase):
    def test_robots_disallows_every_docs_path(self):
        body = Client().get("/robots.txt").content.decode()
        # /api/ covers /api/schema/, /tuitionclass/ covers /tuitionclass/schema/...
        for prefix in ("/api/", "/swagger/", "/redoc/", "/tuitionclass/"):
            self.assertIn(f"Disallow: {prefix}", body)

    def test_sitemap_never_lists_docs(self):
        xml = Client().get("/sitemap.xml").content.decode()
        for frag in ("/swagger", "/redoc", "/api/schema", "schema/docs"):
            self.assertNotIn(frag, xml)


# ----------------------------------------------------------------------------------------------------------------------
# 4. Release-gate rules (pure: no DB, no Django settings)
# ----------------------------------------------------------------------------------------------------------------------
GOOD_SETTINGS = {
    "DEBUG": False,
    "ALLOWED_HOSTS": ["api.learnscroll.app"],
    "CSRF_TRUSTED_ORIGINS": ["https://api.learnscroll.app"],
    "CSRF_COOKIE_SECURE": True,
    "SESSION_COOKIE_SECURE": True,
    "SESSION_COOKIE_HTTPONLY": True,
    "SESSION_COOKIE_SAMESITE": "Lax",
    "CSRF_COOKIE_SAMESITE": "Lax",
    "SECURE_SSL_REDIRECT": True,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
    "SECURE_HSTS_SECONDS": 60 * 60 * 24 * 30,
    "SECURE_CONTENT_TYPE_NOSNIFF": True,
    "SECURE_REFERRER_POLICY": "same-origin",
    "X_FRAME_OPTIONS": "DENY",
    "CORS_ALLOW_ALL_ORIGINS": False,
    "CORS_ALLOWED_ORIGINS": [],
    "MIDDLEWARE": [
        "corsheaders.middleware.CorsMiddleware",
        SECURITY_MW,
        CSRF_MW,
        "django.middleware.clickjacking.XFrameOptionsMiddleware",
    ],
    "REST_FRAMEWORK": {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]},
}


def keys(findings, level=None):
    return {f.key for f in findings if level is None or f.level == level}


class DocsCheckerRuleTests(unittest.TestCase):
    # --- check_security_settings (live settings) ---
    def test_staff_only_docs_pass(self):
        cfg = {**GOOD_SETTINGS, "SPECTACULAR_SETTINGS": {"SERVE_PERMISSIONS": [STAFF_ONLY]}}
        self.assertNotIn("SPECTACULAR_SETTINGS", keys(check_settings(cfg)))

    def test_public_docs_are_an_error_in_production(self):
        cfg = {**GOOD_SETTINGS, "SPECTACULAR_SETTINGS": {"SERVE_PERMISSIONS": [OPEN]}}
        self.assertIn("SPECTACULAR_SETTINGS", keys(check_settings(cfg), ERROR))

    def test_missing_serve_permissions_means_library_default_which_is_open(self):
        cfg = {**GOOD_SETTINGS, "SPECTACULAR_SETTINGS": {"TITLE": "x"}}
        self.assertIn("SPECTACULAR_SETTINGS", keys(check_settings(cfg), ERROR))

    def test_empty_serve_permissions_is_open_too(self):
        cfg = {**GOOD_SETTINGS, "SPECTACULAR_SETTINGS": {"SERVE_PERMISSIONS": []}}
        self.assertIn("SPECTACULAR_SETTINGS", keys(check_settings(cfg), ERROR))

    def test_public_docs_are_only_a_warning_on_a_dev_laptop(self):
        cfg = {**GOOD_SETTINGS, "DEBUG": True, "SPECTACULAR_SETTINGS": {"SERVE_PERMISSIONS": [OPEN]}}
        found = check_settings(cfg)  # production defaults to `not DEBUG`
        self.assertIn("SPECTACULAR_SETTINGS", keys(found, WARN))
        self.assertNotIn("SPECTACULAR_SETTINGS", keys(found, ERROR))

    def test_snapshot_without_spectacular_key_is_not_judged(self):
        # older callers / tests that never pass SPECTACULAR_SETTINGS keep working
        self.assertNotIn("SPECTACULAR_SETTINGS", keys(check_settings(dict(GOOD_SETTINGS))))

    # --- release_check (env file) ---
    def test_env_with_public_docs_is_an_error(self):
        from core.tests_release_check import GOOD as GOOD_ENV
        env = {**GOOD_ENV, "API_DOCS_PUBLIC": "true"}
        self.assertIn("API_DOCS_PUBLIC", keys(check_env(env, file_exists=lambda p: True), ERROR))

    def test_env_with_false_or_missing_flag_is_fine(self):
        from core.tests_release_check import GOOD as GOOD_ENV
        for env in ({**GOOD_ENV, "API_DOCS_PUBLIC": "false"}, dict(GOOD_ENV)):
            self.assertNotIn("API_DOCS_PUBLIC", keys(check_env(env, file_exists=lambda p: True)))


if __name__ == "__main__":
    unittest.main()
