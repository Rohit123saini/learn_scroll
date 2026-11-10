"""X6 -- pure unit tests for core/security_settings_check.py (no DB, no Django). Written, NOT run.

    python -m unittest core.tests_security_settings_check
"""
import unittest

from core.release_check import ERROR, WARN
from core.security_settings_check import CSRF_MW, SECURITY_MW, check_settings

GOOD = {
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
        "common.csp.ContentSecurityPolicyMiddleware",
    ],
    "REST_FRAMEWORK": {"DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"]},
}


def keys(findings, level=None):
    return {f.key for f in findings if level is None or f.level == level}


class SecuritySettingsCheckTests(unittest.TestCase):
    def test_good_config_has_no_errors(self):
        self.assertEqual(keys(check_settings(GOOD), ERROR), set())

    def test_wildcard_host_is_error_in_production(self):
        self.assertIn("ALLOWED_HOSTS", keys(check_settings({**GOOD, "ALLOWED_HOSTS": ["*"]}), ERROR))

    def test_http_csrf_origin_is_error(self):
        cfg = {**GOOD, "CSRF_TRUSTED_ORIGINS": ["http://api.learnscroll.app"]}
        self.assertIn("CSRF_TRUSTED_ORIGINS", keys(check_settings(cfg), ERROR))

    def test_missing_csrf_middleware_is_error(self):
        cfg = {**GOOD, "MIDDLEWARE": [m for m in GOOD["MIDDLEWARE"] if m != CSRF_MW]}
        self.assertIn("MIDDLEWARE", keys(check_settings(cfg), ERROR))

    def test_missing_csp_middleware_is_error(self):  # P-B6
        cfg = {**GOOD, "MIDDLEWARE": [m for m in GOOD["MIDDLEWARE"] if "csp" not in m]}
        self.assertIn("MIDDLEWARE", keys(check_settings(cfg), ERROR))

    def test_public_csp_off_is_error(self):  # P-B6
        self.assertIn("CSP_PUBLIC_MODE", keys(check_settings({**GOOD, "CSP_PUBLIC_MODE": "off"}), ERROR))

    def test_default_admin_url_is_only_a_warning_in_production(self):  # P-B6
        f = check_settings({**GOOD, "ADMIN_URL": "admin/"}, production=True)
        self.assertNotIn("ADMIN_URL", keys(f, ERROR))
        self.assertIn("ADMIN_URL", keys(f))

    def test_cors_allow_all_is_error(self):
        self.assertIn("CORS_ALLOW_ALL_ORIGINS", keys(check_settings({**GOOD, "CORS_ALLOW_ALL_ORIGINS": True}), ERROR))

    def test_insecure_cookies_are_errors(self):
        cfg = {**GOOD, "SESSION_COOKIE_SECURE": False, "CSRF_COOKIE_SECURE": False}
        self.assertTrue({"SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE"} <= keys(check_settings(cfg), ERROR))

    def test_dev_run_downgrades_https_rules_to_warnings(self):
        cfg = {**GOOD, "DEBUG": True, "ALLOWED_HOSTS": ["*"], "SESSION_COOKIE_SECURE": False,
               "SECURE_SSL_REDIRECT": False, "SECURE_HSTS_SECONDS": 0}
        self.assertEqual(keys(check_settings(cfg), ERROR), set())
        self.assertIn("SESSION_COOKIE_SECURE", keys(check_settings(cfg), WARN))

    def test_debug_true_is_error_only_when_production_forced(self):
        self.assertIn("DEBUG", keys(check_settings({**GOOD, "DEBUG": True}, production=True), ERROR))


if __name__ == "__main__":
    unittest.main()


class AdminIpAndTwoFactorSettingsTests(unittest.TestCase):  # P-B8
    MW = "common.admin_ip_allowlist.AdminIPAllowlistMiddleware"

    def test_list_without_middleware_is_an_error(self):
        cfg = {**GOOD, "ADMIN_IP_ALLOWLIST": "203.0.113.7"}
        self.assertIn("MIDDLEWARE", keys(check_settings(cfg), ERROR))
        cfg["MIDDLEWARE"] = [*GOOD["MIDDLEWARE"], self.MW]
        self.assertNotIn("MIDDLEWARE", keys(check_settings(cfg), ERROR))

    def test_empty_list_needs_no_middleware(self):
        self.assertNotIn("MIDDLEWARE", keys(check_settings({**GOOD, "ADMIN_IP_ALLOWLIST": ""}), ERROR))

    def test_2fa_off_in_production_is_an_error(self):
        self.assertIn("ADMIN_REQUIRE_2FA", keys(check_settings({**GOOD, "ADMIN_REQUIRE_2FA": False}, production=True), ERROR))
        self.assertNotIn("ADMIN_REQUIRE_2FA", keys(check_settings({**GOOD, "ADMIN_REQUIRE_2FA": True}, production=True), ERROR))
        self.assertNotIn("ADMIN_REQUIRE_2FA", keys(check_settings({**GOOD, "DEBUG": True, "ADMIN_REQUIRE_2FA": False}, production=False), ERROR))
