"""Tests for the public transparency report: the numbers (claims / removals / restored / counter-notices /
strikes / appeals-free parts / court orders / automatic matches), the period parameters, the public JSON + web
page + CSV + command, and that nothing personal is in it."""
import json
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import legal_orders, services, transparency
from .models import CopyrightAuditLog, CopyrightClaim
from .tests import Base, mk_post, mk_staff, notice

ORDER = dict(reference="WP(C) 77/2026", order_type="court", authority="High Court", summary="Remove the post.")
COUNTER = dict(explanation="This is my own photo, here is the raw file.", good_faith_statement=True,
               jurisdiction_consent=True, signature="Olly Owner")


@override_settings(COPYRIGHT_TRANSPARENCY_CACHE_SECONDS=0, COPYRIGHT_TRANSPARENCY_PUBLIC=True)
class TransparencyTests(Base):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("tr2", 2), mk_staff("tr3", 3)

    def report(self, **params):
        return transparency.get_report(params or None, use_cache=False)

    def scenario(self):
        """4 notices: one upheld (+strike), one rejected, one withdrawn, one countered then restored."""
        posts = [self.post] + [mk_post(self.owner) for _ in range(3)]
        claims = [services.submit_claim(self.rita, notice("post", p.id))[0] for p in posts]
        c1, c2, c3, c4 = claims
        services.uphold_claim(c1, self.l2, "ok")
        services.reject_claim(c2, self.l2, "not their work")
        services.withdraw_claim(c3, self.rita)
        services.file_counter_notice(self.owner, c4, COUNTER)
        services.restore_claim(c4, self.l2, "owner proved it is theirs")
        return claims

    # ------------------------------------------------------------ numbers
    def test_empty_report_has_a_stable_shape_and_zeros(self):
        r = self.report(**{"from": "2020-01-01", "to": "2020-01-31"})
        self.assertEqual(r["claims"]["received"], 0)
        self.assertEqual(r["content"]["restored"]["by_reason"],
                         {"claim_rejected": 0, "claim_withdrawn": 0, "counter_notice": 0, "other": 0})
        self.assertEqual(r["claims"]["by_content_type"]["post"], 0)
        self.assertIsNone(r["claims"]["median_hours_to_decision"])
        self.assertEqual(r["period"], {"from": "2020-01-01", "to": "2020-01-31", "days": 31})

    def test_claims_removals_and_restorations(self):
        self.scenario()
        r = self.report()
        cl = r["claims"]
        self.assertEqual(cl["received"], 4)
        self.assertEqual(cl["by_source"]["user"], 4)
        self.assertEqual(cl["by_content_type"]["post"], 4)
        self.assertEqual({k: cl["outcome_of_received"][k] for k in ("upheld", "rejected", "withdrawn", "restored")},
                         {"upheld": 1, "rejected": 1, "withdrawn": 1, "restored": 1})
        self.assertEqual(cl["decided_in_period"]["upheld"], 1)
        self.assertEqual(cl["decided_in_period"]["rejected"], 1)
        self.assertEqual(cl["decided_in_period"]["withdrawn"], 1)
        self.assertIsNotNone(cl["median_hours_to_decision"])

        c = r["content"]
        self.assertEqual(c["held_at_once"], 4)
        self.assertEqual(c["removed"], {"claims_upheld": 1, "content_items": 1})
        self.assertEqual(c["restored"]["total"], 3)
        self.assertEqual(c["restored"]["by_reason"],
                         {"claim_rejected": 1, "claim_withdrawn": 1, "counter_notice": 1, "other": 0})

        self.assertEqual(r["counter_notices"]["filed"], 1)
        self.assertEqual(r["counter_notices"]["outcome_of_filed"]["restored"], 1)
        self.assertEqual(r["strikes_and_accounts"]["issued"], 1)
        self.assertEqual(r["strikes_and_accounts"]["revoked"], 0)
        self.assertEqual(r["right_now"]["content_removed"], 1)
        self.assertEqual(r["right_now"]["open_claims"], 0)

    def test_revoked_strike_is_counted(self):
        c1 = self.scenario()[0]
        services.restore_claim(c1, self.l3, "court set the notice aside")
        r = self.report()
        self.assertEqual(r["strikes_and_accounts"]["revoked"], 1)
        self.assertEqual(r["content"]["restored"]["by_reason"]["counter_notice"], 2)   # claim status "restored"

    def test_claims_outside_the_period_are_not_counted(self):
        claims = self.scenario()
        CopyrightClaim.objects.filter(pk__in=[c.pk for c in claims]).update(
            created_at=timezone.now() - timedelta(days=400))
        self.assertEqual(self.report()["claims"]["received"], 0)

    def test_legal_orders_are_reported_separately(self):
        o = legal_orders.register_order(self.l2, ORDER, [("post", self.post.id)])
        legal_orders.comply(o, self.l3, "valid")
        r = self.report()
        lo = r["legal_orders"]
        self.assertEqual((lo["received"], lo["by_type"]["court"]), (1, 1))
        self.assertEqual(lo["outcome_of_received"]["complied"], 1)
        self.assertEqual(lo["decided_in_period"]["complied"], 1)
        self.assertEqual(lo["content_items"]["removed"], 1)
        self.assertEqual(r["claims"]["received"], 0)                      # an order is not a copyright claim
        self.assertEqual(r["strikes_and_accounts"]["issued"], 0)          # ... and gives no strike

    def test_automatic_detection_numbers(self):
        services.open_auto_claim("post", str(self.post.id), "a" * 64)
        CopyrightAuditLog.objects.create(action="reupload_blocked", actor_label="automation", note="x")
        r = self.report()
        self.assertEqual(r["automatic_detection"]["matches_held_for_review"], 1)
        self.assertEqual(r["automatic_detection"]["uploads_refused_exact_copy"], 1)
        self.assertEqual(r["claims"]["by_source"]["auto"], 1)
        self.assertEqual(r["claimant_quality"], {})                       # automatic matches have no claimant

    def test_claimant_quality_by_trust_level(self):
        self.scenario()
        q = self.report()["claimant_quality"]["trusted"]
        self.assertEqual(q, {"received": 4, "upheld": 1, "rejected": 1, "bad_faith": 0})

    # ------------------------------------------------------------ period parameters
    def test_period_parameters(self):
        _, _, first, last = transparency.resolve_period({"year": "2026", "half": "1"})
        self.assertEqual((first.isoformat(), last.isoformat()), ("2026-01-01", "2026-06-30"))
        _, _, first, last = transparency.resolve_period({"year": "2026", "half": "2"})
        self.assertEqual((first.isoformat(), last.isoformat()), ("2026-07-01", "2026-12-31"))
        _, _, first, last = transparency.resolve_period({"year": "2026"})
        self.assertEqual((last - first).days + 1, 365)
        _, _, first, last = transparency.resolve_period({})
        self.assertEqual((last - first).days + 1, 180)

    @override_settings(COPYRIGHT_TRANSPARENCY_DEFAULT_DAYS=30)
    def test_default_days_setting(self):
        _, _, first, last = transparency.resolve_period({})
        self.assertEqual((last - first).days + 1, 30)

    def test_bad_period_parameters_are_refused(self):
        for params in ({"year": "abc"}, {"year": "2026", "half": "3"}, {"half": "1"}, {"from": "2026-01-01"},
                       {"from": "2026-02-01", "to": "2026-01-01"}, {"from": "nope", "to": "2026-01-01"},
                       {"from": "2025-01-01", "to": "2026-06-01"}, {"year": "1999"}):
            with self.assertRaises(ValueError, msg=str(params)):
                transparency.resolve_period(params)

    # ------------------------------------------------------------ public endpoints
    def test_public_json_needs_no_login(self):
        self.scenario()
        res = APIClient().get("/copyright/transparency/")
        self.assertEqual(res.status_code, 200)
        data = res.json()["data"]
        self.assertEqual(data["claims"]["received"], 4)
        self.assertEqual(data["content"]["removed"]["claims_upheld"], 1)

    def test_public_json_period_and_errors(self):
        c = APIClient()
        ok = c.get("/copyright/transparency/", {"year": 2020, "half": 1})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["data"]["period"]["from"], "2020-01-01")
        bad = c.get("/copyright/transparency/", {"year": 2026, "half": 9})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.json()["status"])

    @override_settings(COPYRIGHT_TRANSPARENCY_PUBLIC=False)
    def test_can_be_switched_off(self):
        self.assertEqual(APIClient().get("/copyright/transparency/").status_code, 404)
        self.assertEqual(APIClient().get("/legal/transparency-report/").status_code, 404)
        self.assertEqual(APIClient().get("/copyright/transparency/page/").status_code, 404)
        out = StringIO()
        call_command("copyright_transparency_report", stdout=out)                  # staff tooling still works
        self.assertIn('"claims"', out.getvalue())

    def test_web_page(self):
        self.scenario()
        res = APIClient().get("/legal/transparency-report/")
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Transparency Report", html)
        self.assertIn("Claims upheld (content removed)", html)
        self.assertEqual(APIClient().get("/legal/transparency-report/", {"year": "x"}).status_code, 400)
        self.assertEqual(APIClient().get("/copyright/transparency/page/").status_code, 200)

    def test_policy_page_links_to_the_report(self):
        res = APIClient().get("/legal/copyright-policy/")
        self.assertIn("/legal/transparency-report/", res.content.decode())

    # ------------------------------------------------------------ privacy
    def test_nothing_personal_is_published(self):
        self.scenario()
        blob = json.dumps(self.report()) + transparency.render_html(self.report()) + transparency.render_csv(self.report())
        for secret in ("rita", "Rita", "owner", "Olly", "Pub Co", "pub.com", "my original-looking post"):
            self.assertNotIn(secret, blob)

    # ------------------------------------------------------------ csv + command
    def test_csv_rows(self):
        self.scenario()
        text = transparency.render_csv(self.report())
        lines = text.splitlines()
        self.assertEqual(lines[0], "section,item,value")
        self.assertIn("claims,received,4", lines)
        self.assertIn("content,removed.content_items,1", lines)

    def test_management_command_writes_a_file(self):
        import os
        import tempfile

        self.scenario()
        path = os.path.join(tempfile.mkdtemp(prefix="cr_tr_"), "t.csv")
        out = StringIO()
        call_command("copyright_transparency_report", "--format", "csv", "--out", path, stdout=out)
        self.assertTrue(open(path, encoding="utf-8").read().startswith("section,item,value"))
        self.assertIn("Wrote", out.getvalue())
        with self.assertRaises(Exception):
            call_command("copyright_transparency_report", "--from", "2026-02-01", "--to", "2026-01-01",
                         stdout=StringIO())

    def test_report_is_cached_when_enabled(self):
        from django.core.cache import cache

        cache.clear()
        with override_settings(COPYRIGHT_TRANSPARENCY_CACHE_SECONDS=600):
            first = transparency.get_report({})
            self.scenario()
            again = transparency.get_report({})
            self.assertEqual(first["claims"]["received"], again["claims"]["received"])    # served from cache
            self.assertEqual(transparency.get_report({}, use_cache=False)["claims"]["received"], 4)
        cache.clear()
