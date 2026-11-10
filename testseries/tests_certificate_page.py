"""
P3 - public certificate verify page `GET /certificate/<code>/` + `verify_url` in the certificate API. NOT RUN:

    python manage.py test testseries.tests_certificate_page
"""
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .models import TestCertificate
from .tests_advanced import _Base, _answers, _client, _series

SITE = "https://learnscroll.test"


@override_settings(PUBLIC_WEB_BASE_URL=SITE)
@mock.patch("testseries.models._notify")
class CertificateVerifyPageTests(_Base):
    def setUp(self):
        super().setUp()
        cache.clear()

    def _earn(self, title="Physics Foundations"):
        series = _series(
            self.teacher, questions=2, pass_percentage=50, certificate_enabled=True, certificate_title=title,
        )
        attempt_id = self.start(series).json()["id"]
        self.submit(attempt_id, _answers(series, ["a", "a"]))
        return TestCertificate.objects.get(series=series, student=self.student)

    def _page(self, code):
        # the page is public: no auth, no token
        from django.test import Client

        return Client().get(f"/certificate/{code}/")

    def test_valid_certificate_page(self, _notify):
        cert = self._earn()
        res = self._page(cert.code)
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Certificate is valid", html)
        self.assertIn(cert.code, html)
        self.assertIn("Physics Foundations", html)
        self.assertIn(f'href="{SITE}/certificate/{cert.code}/"', html)  # canonical
        self.assertIn("noindex", html)
        self.assertEqual(res["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-cache", res["Cache-Control"])  # a revocation must show immediately

    def test_lowercase_code_works(self, _notify):
        cert = self._earn()
        self.assertEqual(self._page(cert.code.lower()).status_code, 200)

    def test_revoked_certificate_says_so(self, _notify):
        cert = self._earn()
        cert.revoked_at = timezone.now()
        cert.revoked_reason = "Answer sheet was copied"
        cert.save(update_fields=["revoked_at", "revoked_reason"])
        html = self._page(cert.code).content.decode()
        self.assertIn("Certificate revoked", html)
        self.assertIn("Answer sheet was copied", html)
        self.assertNotIn("Certificate is valid", html)

    def test_unknown_or_malformed_code_is_404(self, _notify):
        self.assertEqual(self._page("LS-AAAA-BBBB-CCCC").status_code, 404)  # well-formed, no such certificate
        self.assertEqual(self._page("not-a-code").status_code, 404)  # never reaches the DB
        self.assertEqual(self._page("LS-0000-1111-OOOO").status_code, 404)  # characters outside the alphabet

    def test_holder_name_is_html_escaped(self, _notify):
        cert = self._earn()
        self.student.first_name = "<script>alert(1)</script>"
        self.student.save(update_fields=["first_name"])
        html = self._page(cert.code).content.decode()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_per_ip_rate_limit(self, _notify):
        from django.test import Client

        client = Client()
        for _ in range(30):
            client.get("/certificate/LS-AAAA-BBBB-CCCC/")
        self.assertEqual(client.get("/certificate/LS-AAAA-BBBB-CCCC/").status_code, 429)

    def test_api_returns_verify_url(self, _notify):
        cert = self._earn()
        mine = _client(self.student).get(reverse("testseries-certificate-mine")).json()
        rows = mine["results"] if isinstance(mine, dict) and "results" in mine else mine
        self.assertEqual(rows[0]["verify_url"], f"{SITE}/certificate/{cert.code}/")

        public = _client().get(reverse("testseries-certificate-verify", kwargs={"code": cert.code})).json()
        self.assertEqual(public["verify_url"], f"{SITE}/certificate/{cert.code}/")
