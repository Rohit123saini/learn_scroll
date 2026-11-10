"""P-B6 -- Content-Security-Policy middleware + configurable admin URL."""
import base64
import hashlib

from django.http import HttpResponse, JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from common.csp import ContentSecurityPolicyMiddleware, admin_prefix, build_policy, inline_script_hashes

SCRIPT = "document.title='x'"
HTML = ('<html><head><script type="application/ld+json">{"a":1}</script><style>p{}</style></head>'
        "<body><script>%s</script><script src='/static/a.js'></script></body></html>" % SCRIPT)


def _hash(text):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


def _run(path, response):
    mw = ContentSecurityPolicyMiddleware(lambda request: response)
    return mw(RequestFactory().get(path))


class HashTests(SimpleTestCase):
    def test_only_inline_executable_scripts_are_hashed(self):
        self.assertEqual(inline_script_hashes(HTML.encode()), [_hash(SCRIPT)])

    def test_no_scripts_means_none(self):
        self.assertEqual(inline_script_hashes(b"<p>hi</p>"), [])


class PolicyTests(SimpleTestCase):
    def test_public_policy_blocks_scripts_unless_hashed(self):
        p = build_policy("public")
        self.assertIn("script-src 'none'", p)
        self.assertIn("frame-ancestors 'none'", p)
        self.assertNotIn("unsafe-eval", p)
        self.assertIn("script-src 'sha256-abc'", build_policy("public", ["'sha256-abc'"]))

    def test_admin_policy_allows_own_origin_scripts_only(self):
        p = build_policy("admin", ["'sha256-abc'"])
        self.assertIn("script-src 'self' 'sha256-abc'", p)
        self.assertNotIn("unsafe-inline'; ", p.split("style-src")[0])  # scripts never get unsafe-inline


class MiddlewareTests(SimpleTestCase):
    def test_public_html_is_enforced_with_hash(self):
        r = _run("/test-series/abc/", HttpResponse(HTML, content_type="text/html"))
        self.assertIn(_hash(SCRIPT), r["Content-Security-Policy"])
        self.assertNotIn("Content-Security-Policy-Report-Only", r)

    def test_admin_is_report_only_by_default(self):
        r = _run("/admin/login/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertIn("Content-Security-Policy-Report-Only", r)
        self.assertNotIn("Content-Security-Policy", r)

    @override_settings(CSP_ADMIN_MODE="enforce")
    def test_admin_can_be_enforced(self):
        r = _run("/admin/login/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertIn("script-src 'self'", r["Content-Security-Policy"])

    @override_settings(CSP_PUBLIC_MODE="off")
    def test_off_adds_nothing(self):
        r = _run("/u/x/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertNotIn("Content-Security-Policy", r)

    def test_json_and_schema_pages_are_untouched(self):
        self.assertNotIn("Content-Security-Policy", _run("/post/feed/", JsonResponse({"a": 1})))
        r = _run("/swagger/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertNotIn("Content-Security-Policy", r)
        self.assertNotIn("Content-Security-Policy-Report-Only", r)

    def test_an_existing_header_is_not_replaced(self):
        resp = HttpResponse("<p>x</p>", content_type="text/html")
        resp["Content-Security-Policy"] = "default-src 'none'"
        self.assertEqual(_run("/u/x/", resp)["Content-Security-Policy"], "default-src 'none'")

    @override_settings(ADMIN_URL="ops-9f3k2/")
    def test_custom_admin_url_gets_the_admin_policy(self):
        self.assertEqual(admin_prefix(), "/ops-9f3k2/")
        r = _run("/ops-9f3k2/login/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertIn("Content-Security-Policy-Report-Only", r)
        r2 = _run("/admin/login/", HttpResponse("<p>x</p>", content_type="text/html"))
        self.assertIn("Content-Security-Policy", r2)  # no longer the admin -> public policy, enforced


class AdminUrlSettingTests(SimpleTestCase):
    def test_admin_url_setting_is_normalised(self):
        from django.conf import settings

        self.assertTrue(settings.ADMIN_URL.endswith("/") and not settings.ADMIN_URL.startswith("/"))

    def test_robots_never_names_a_private_admin_path(self):
        """robots.txt keeps the old '/admin/' line only; a custom ADMIN_URL must not be advertised there."""
        from common import seo_pages

        self.assertIn("/admin/", seo_pages._DISALLOW)
        self.assertFalse(any("ops-" in p for p in seo_pages._DISALLOW))
