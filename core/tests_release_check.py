"""X1 — pure unit tests for core/release_check.py (no DB, no Django). Written, NOT run.

    python -m unittest core.tests_release_check
"""
import unittest

from core.release_check import ERROR, WARN, check_env, exit_code, parse_env_file, summarize

GOOD = {
    "ADMIN_URL": "ops-9f3k2/",  # P-B6
    "CSP_ADMIN_MODE": "enforce",  # P-B6
    "DEBUG": "False",
    "SECRET_KEY": "k9$Zp2!vQ7xLm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd0Ey",
    "JWT_SIGNING_KEY": "jwt-Zp2vQ7xLm4Tn8Rw3Yb6Hc1Df5Gs0Jq9Ua2Ie7Ok4Pl3Mz8Xv5Nb1Cr6Wd0Ey-different",
    "ALLOWED_HOSTS": "api.learnscroll.app",
    "CSRF_TRUSTED_ORIGINS": "https://api.learnscroll.app",
    "SECURE_SSL_REDIRECT": "true",
    "DATABASE_URL": "postgres://ls:s3cret@db.internal.net:5432/learnscroll",
    "REDIS_URL": "redis://cache.internal.net:6379/0",
    "CELERY_BROKER_URL": "redis://cache.internal.net:6379/1",
    "CELERY_RESULT_BACKEND": "redis://cache.internal.net:6379/2",
    "USE_S3_STORAGE": "true",
    "AWS_ACCESS_KEY_ID": "AKIAREALKEY",
    "AWS_SECRET_ACCESS_KEY": "realsecretvalue",
    "AWS_STORAGE_BUCKET_NAME": "learnscroll-media",
    "MEDIA_CDN_ENABLED": "true",
    "MEDIA_CDN_URL": "https://cdn.learnscroll.app",
    "FIREBASE_CREDENTIALS_PATH": "/etc/learnscroll/firebase.json",
    "APNS_VOIP_KEY_PATH": "/etc/learnscroll/AuthKey_ABCDE12345.p8",
    "APNS_KEY_ID": "ABCDE12345",
    "APNS_TEAM_ID": "TEAM123456",
    "APNS_BUNDLE_ID": "com.learnscroll.app",
    "APNS_USE_SANDBOX": "false",
    "LIVEKIT_API_KEY": "APIkey123",
    "LIVEKIT_API_SECRET": "livekitsecretvalue",
    "LIVEKIT_WS_URL": "wss://lk.learnscroll.app",
    "RAZORPAY_KEY_ID": "rzp_live_abc123",
    "RAZORPAY_KEY_SECRET": "razorsecret",
    "RAZORPAY_WEBHOOK_SECRET": "whsec_real",
    "SENTRY_DSN": "https://abc@o123.ingest.sentry.io/456",
    "SENTRY_TRACES_SAMPLE_RATE": "0.1",
    "HEALTHCHECK_TOKEN": "tok",
    "ADMIN_REQUIRE_2FA": "true",
    "SUPPORT_EMAIL": "help@learnscroll.app",
    "GRIEVANCE_OFFICER_NAME": "A Person",
    "GRIEVANCE_OFFICER_EMAIL": "grievance@learnscroll.app",
    "LEGAL_PAGES_DRAFT_BANNER": "false",
    "ANDROID_APP_PACKAGE": "com.learnscroll.app",
    "ANDROID_SHA256_CERT_FINGERPRINTS": ":".join(["AB"] * 32),
    "IOS_APP_ID": "TEAM123456.com.learnscroll.app",
    "PLAY_STORE_URL": "https://play.google.com/store/apps/details?id=com.learnscroll.app",
    "GOOGLE_CLIENT_ID": "123.apps.googleusercontent.com",
    "BACKUP_REMOTE": "s3backup:learnscroll-backups",
}
EXISTS = lambda p: True  # noqa: E731
MISSING = lambda p: False  # noqa: E731


def run(**over):
    env = dict(GOOD)
    for k, v in over.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return check_env(env, file_exists=EXISTS)


def keys(findings, level=None):
    return {f.key for f in findings if level is None or f.level == level}


class GoodEnvTests(unittest.TestCase):
    def test_a_complete_env_has_no_findings(self):
        self.assertEqual([str(f) for f in run()], [])

    def test_exit_code(self):
        self.assertEqual(exit_code(run()), 0)
        self.assertEqual(exit_code(run(DEBUG="True")), 1)
        warn_only = run(BACKUP_REMOTE=None)
        self.assertEqual(summarize(warn_only), {"error": 0, "warn": 1})
        self.assertEqual(exit_code(warn_only, "error"), 0)
        self.assertEqual(exit_code(warn_only, "warn"), 1)


class CoreSecretsTests(unittest.TestCase):
    def test_debug_true_or_missing_is_error(self):
        self.assertIn("DEBUG", keys(run(DEBUG="True"), ERROR))
        self.assertIn("DEBUG", keys(run(DEBUG=None), ERROR))

    def test_secret_key_rules(self):
        for bad in (None, "short", "django-insecure-" + "x" * 60, "PUT_A_RANDOM_50_PLUS_CHAR_VALUE_HERE", "a" * 60):
            with self.subTest(bad=bad):
                self.assertIn("SECRET_KEY", keys(run(SECRET_KEY=bad), ERROR))

    def test_jwt_key_rules(self):
        self.assertIn("JWT_SIGNING_KEY", keys(run(JWT_SIGNING_KEY=None), ERROR))
        self.assertIn("JWT_SIGNING_KEY", keys(run(JWT_SIGNING_KEY="x" * 10), ERROR))
        self.assertIn("JWT_SIGNING_KEY", keys(run(JWT_SIGNING_KEY=GOOD["SECRET_KEY"]), ERROR))

    def test_allowed_hosts(self):
        for bad in ("", "*", ".learnscroll.app", "https://api.learnscroll.app", "api.learnscroll.app/x"):
            with self.subTest(bad=bad):
                self.assertIn("ALLOWED_HOSTS", keys(run(ALLOWED_HOSTS=bad), ERROR))
        self.assertIn("ALLOWED_HOSTS", keys(run(ALLOWED_HOSTS="api.learnscroll.app,localhost"), WARN))

    def test_csrf_origins_and_ssl_redirect(self):
        self.assertIn("CSRF_TRUSTED_ORIGINS", keys(run(CSRF_TRUSTED_ORIGINS="http://api.learnscroll.app"), ERROR))
        self.assertIn("CSRF_TRUSTED_ORIGINS", keys(run(CSRF_TRUSTED_ORIGINS=None), WARN))
        self.assertIn("SECURE_SSL_REDIRECT", keys(run(SECURE_SSL_REDIRECT="false"), ERROR))


class DataStoreTests(unittest.TestCase):
    def test_database(self):
        for bad in (None, "sqlite:///db.sqlite3", "mysql://u:p@h/db", "postgres://USER:PASSWORD@HOST:5432/learnscroll"):
            with self.subTest(bad=bad):
                self.assertIn("DATABASE_URL", keys(run(DATABASE_URL=bad), ERROR))
        self.assertEqual(keys(run(DATABASE_URL="postgresql://u:p@db.x.net/ls"), ERROR), set())

    def test_redis_urls(self):
        for k in ("REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND"):
            with self.subTest(k=k):
                self.assertIn(k, keys(run(**{k: None}), ERROR))
                self.assertIn(k, keys(run(**{k: "memcached://x"}), ERROR))
                self.assertIn(k, keys(run(**{k: "redis://HOST:6379/0"}), ERROR))
        self.assertEqual(keys(run(REDIS_URL="rediss://cache.x.net:6380/0"), ERROR), set())

    def test_s3_and_cdn(self):
        self.assertIn("AWS_STORAGE_BUCKET_NAME", keys(run(AWS_STORAGE_BUCKET_NAME=None), ERROR))
        self.assertIn("USE_S3_STORAGE", keys(run(USE_S3_STORAGE="false"), WARN))
        self.assertIn("MEDIA_CDN_URL", keys(run(MEDIA_CDN_URL="cdn.learnscroll.app"), ERROR))
        self.assertIn("MEDIA_CDN_ENABLED", keys(run(MEDIA_CDN_ENABLED="false"), WARN))


class PushTests(unittest.TestCase):
    def test_fcm_file_must_exist(self):
        f = check_env(GOOD, file_exists=MISSING)
        self.assertIn("FIREBASE_CREDENTIALS_PATH", keys(f, ERROR))

    def test_fcm_default_path_is_used_when_env_empty(self):
        seen = []
        check_env({**GOOD, "FIREBASE_CREDENTIALS_PATH": ""}, file_exists=lambda p: seen.append(p) or True, default_fcm_path="/srv/fb.json")
        self.assertIn("/srv/fb.json", seen)

    def test_apns_all_or_nothing(self):
        self.assertIn("APNS_KEY_ID", keys(run(APNS_KEY_ID=None), ERROR))
        none = run(APNS_VOIP_KEY_PATH=None, APNS_KEY_ID=None, APNS_TEAM_ID=None, APNS_BUNDLE_ID=None)
        self.assertEqual(keys(none, ERROR), set())
        self.assertIn("APNS_VOIP_KEY_PATH", keys(none, WARN))

    def test_apns_sandbox_is_error(self):
        self.assertIn("APNS_USE_SANDBOX", keys(run(APNS_USE_SANDBOX="true"), ERROR))

    def test_apns_key_file_must_exist(self):
        f = check_env(GOOD, file_exists=lambda p: p.endswith("firebase.json"))
        self.assertIn("APNS_VOIP_KEY_PATH", keys(f, ERROR))


class ServicesTests(unittest.TestCase):
    def test_livekit_url(self):
        for bad in ("", "ws://lk.learnscroll.app", "wss://localhost:7880", "wss://192.168.1.5", "wss://lk.example.com"):
            with self.subTest(bad=bad):
                self.assertIn("LIVEKIT_WS_URL", keys(run(LIVEKIT_WS_URL=bad), ERROR))

    def test_livekit_keys_required(self):
        self.assertIn("LIVEKIT_API_KEY", keys(run(LIVEKIT_API_KEY=None), ERROR))
        self.assertIn("LIVEKIT_API_SECRET", keys(run(LIVEKIT_API_SECRET=None), ERROR))

    def test_razorpay(self):
        self.assertIn("RAZORPAY_KEY_ID", keys(run(RAZORPAY_KEY_ID="rzp_test_abc"), ERROR))
        self.assertIn("RAZORPAY_KEY_ID", keys(run(RAZORPAY_KEY_ID="rzp_live_xxxxxxxx"), ERROR))  # placeholder
        self.assertIn("RAZORPAY_WEBHOOK_SECRET", keys(run(RAZORPAY_WEBHOOK_SECRET=None), ERROR))

    def test_sentry(self):
        self.assertIn("SENTRY_DSN", keys(run(SENTRY_DSN=None), WARN))
        self.assertIn("SENTRY_DSN", keys(run(SENTRY_DSN="not-a-dsn"), ERROR))
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", keys(run(SENTRY_TRACES_SAMPLE_RATE="2"), ERROR))
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", keys(run(SENTRY_TRACES_SAMPLE_RATE="abc"), ERROR))
        self.assertIn("SENTRY_TRACES_SAMPLE_RATE", keys(run(SENTRY_TRACES_SAMPLE_RATE="0.9"), WARN))


class StoreAndLinksTests(unittest.TestCase):
    def test_support_email_and_legal(self):
        self.assertIn("SUPPORT_EMAIL", keys(run(SUPPORT_EMAIL="support@example.com"), ERROR))
        self.assertIn("LEGAL_PAGES_DRAFT_BANNER", keys(run(LEGAL_PAGES_DRAFT_BANNER="true"), WARN))
        self.assertIn("GRIEVANCE_OFFICER_EMAIL", keys(run(GRIEVANCE_OFFICER_EMAIL=None), WARN))

    def test_android_fingerprint_format(self):
        self.assertIn("ANDROID_SHA256_CERT_FINGERPRINTS", keys(run(ANDROID_SHA256_CERT_FINGERPRINTS="abc"), ERROR))
        self.assertIn("ANDROID_SHA256_CERT_FINGERPRINTS", keys(run(ANDROID_SHA256_CERT_FINGERPRINTS=None), WARN))
        two = ",".join([":".join(["01"] * 32), ":".join(["fe"] * 32)])
        self.assertEqual(keys(run(ANDROID_SHA256_CERT_FINGERPRINTS=two), ERROR), set())

    def test_ios_app_id_format(self):
        self.assertIn("IOS_APP_ID", keys(run(IOS_APP_ID="com.learnscroll.app"), ERROR))
        self.assertIn("IOS_APP_ID", keys(run(IOS_APP_ID=None), WARN))


class ParseEnvFileTests(unittest.TestCase):
    def test_comments_quotes_export_and_inline_comments(self):
        env = parse_env_file(
            "# header\n\nDEBUG=False\nexport A=1\nB='two words'\nC=\"x\"  # trailing\nD=https://x.io/#frag\nE=val   # note\nbad line\n"
        )
        self.assertEqual(env["DEBUG"], "False")
        self.assertEqual(env["A"], "1")
        self.assertEqual(env["B"], "two words")
        self.assertEqual(env["D"], "https://x.io/#frag")
        self.assertEqual(env["E"], "val")
        self.assertNotIn("bad line", env)

    def test_default_admin_url_and_csp_modes_warn(self):  # P-B6
        self.assertIn("ADMIN_URL", keys(run(ADMIN_URL="admin/"), WARN))
        self.assertIn("ADMIN_URL", keys(run(ADMIN_URL=None), WARN))
        self.assertNotIn("ADMIN_URL", keys(run(), WARN))
        self.assertIn("CSP_ADMIN_MODE", keys(run(CSP_ADMIN_MODE="report-only"), WARN))
        self.assertIn("CSP_PUBLIC_MODE", keys(run(CSP_PUBLIC_MODE="off"), WARN))
        self.assertIn("CSP_PUBLIC_MODE", keys(run(CSP_PUBLIC_MODE="sometimes"), ERROR))

    def test_admin_ip_allowlist_rules(self):  # P-B8
        self.assertNotIn("ADMIN_IP_ALLOWLIST", keys(run()))                                  # empty = off = fine
        ok = run(ADMIN_IP_ALLOWLIST="203.0.113.7, 198.51.100.0/24", ADMIN_NUM_PROXIES="1")
        self.assertNotIn("ADMIN_IP_ALLOWLIST", keys(ok))
        self.assertIn("ADMIN_IP_ALLOWLIST", keys(run(ADMIN_IP_ALLOWLIST="203.0.113.7, banana"), ERROR))   # typo = admin unreachable
        self.assertIn("ADMIN_IP_ALLOWLIST", keys(run(ADMIN_IP_ALLOWLIST="0.0.0.0/0", ADMIN_NUM_PROXIES="1"), WARN))
        self.assertIn("ADMIN_IP_ALLOWLIST", keys(run(ADMIN_IP_ALLOWLIST="203.0.113.7"), WARN))           # no proxy count
        self.assertNotIn("ADMIN_IP_ALLOWLIST", keys(run(ADMIN_IP_ALLOWLIST="203.0.113.7", NUM_PROXIES="1"), WARN))
        self.assertIn("ADMIN_NUM_PROXIES", keys(run(ADMIN_NUM_PROXIES="two"), ERROR))

    def test_the_shipped_example_file_is_rejected_until_filled_in(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "deploy", "env.production.example")
        with open(path, encoding="utf-8") as fh:
            env = parse_env_file(fh.read())
        f = check_env(env, file_exists=MISSING)
        self.assertGreater(summarize(f)["error"], 5, "the template must NOT pass as-is")
        self.assertIn("SECRET_KEY", keys(f, ERROR))
        self.assertIn("RAZORPAY_KEY_ID", keys(f, ERROR))


if __name__ == "__main__":
    unittest.main()
