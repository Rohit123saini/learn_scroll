"""Tests for the moderator dashboard (copyrights/dashboard.py): queue, SLA clock, search, detail, actions."""
import json
from datetime import timedelta

from django.test import RequestFactory, override_settings
from django.utils import timezone

from . import dashboard, services
from .models import CopyrightClaim, TrustedClaimant
from .tests import Base, mk_claimant, mk_post, mk_staff, mk_user

C = CopyrightClaim


class DashboardTests(Base):
    def setUp(self):
        super().setUp()
        self.l1, self.l2, self.l3 = mk_staff("l1", 1), mk_staff("l2", 2), mk_staff("l3", 3)
        self.rf = RequestFactory()

    # the views are plain functions (admin_site.admin_view only adds login / 2FA / CSRF around them in urls())
    def get(self, view, user, path="/x/", **params):
        req = self.rf.get(path, params)
        req.user = user
        return view(req)

    def send(self, view, user, body, *args):
        req = self.rf.post("/x/", data=json.dumps(body), content_type="application/json")
        req.user = user
        return view(req, *args)

    def queue(self, user=None, **params):
        resp = self.get(dashboard.api_queue, user or self.l2, **params)
        self.assertEqual(resp.status_code, 200)
        return json.loads(resp.content)

    def make(self, created_hours_ago=1, **kw):
        claim = self.file(post=mk_post(self.owner), **kw)
        C.objects.filter(pk=claim.pk).update(created_at=timezone.now() - timedelta(hours=created_hours_ago))
        return C.objects.get(pk=claim.pk)

    # ---- SLA ----
    def test_sla_states_follow_the_48h_deadline(self):
        ok = self.make(1)
        soon = self.make(46)        # 2 h left  (< 6 h)
        late = self.make(60)        # 12 h over
        rows = {r["id"]: r for r in self.queue()["results"]}
        self.assertEqual(rows[str(ok.id)]["sla"]["state"], "ok")
        self.assertEqual(rows[str(soon.id)]["sla"]["state"], "soon")
        self.assertEqual(rows[str(late.id)]["sla"]["state"], "breached")
        self.assertLess(rows[str(late.id)]["sla"]["seconds_left"], 0)

    def test_queue_is_sorted_most_urgent_first(self):
        a, b, c = self.make(1), self.make(60), self.make(40)
        order = [r["id"] for r in self.queue()["results"]]
        self.assertEqual(order, [str(b.id), str(c.id), str(a.id)])

    def test_publisher_gets_the_shorter_deadline(self):
        pub = mk_claimant("pubco", email="p@pub.com")
        TrustedClaimant.objects.filter(user=pub).update(tier="publisher")
        claim = services.submit_claim(pub, dict(
            target_type="post", target_id=str(mk_post(self.owner).id), claimant_name="P", claimant_email="p@pub.com",
            organisation="Pub", is_rights_owner=True, work_description="mine", original_work_url="https://p.com/x",
            good_faith_statement=True, accuracy_statement=True, signature="Pub Person"))[0]
        C.objects.filter(pk=claim.pk).update(created_at=timezone.now() - timedelta(hours=13))     # 12 h fast track
        row = self.queue(sla="breached")["results"][0]
        self.assertEqual(row["id"], str(claim.id))

    def test_filters_and_stats_agree(self):
        self.make(1); self.make(60); self.make(46)
        self.assertEqual(self.queue(sla="breached")["count"], 1)
        self.assertEqual(self.queue(sla="soon")["count"], 1)
        st = self.queue()["stats"]
        self.assertEqual((st["open"], st["breached"], st["soon"], st["unassigned"]), (3, 1, 1, 3))
        self.assertEqual(self.queue(unassigned="1")["count"], 3)
        self.assertEqual(self.queue(mine="1")["count"], 0)

    # ---- search ----
    def test_search_every_word_must_match(self):
        a = self.make(1, claimant_name="Alice Wonder", organisation="Wonder Press")
        self.make(1, claimant_name="Bob Builder")
        self.assertEqual(self.queue(q="alice")["count"], 1)
        self.assertEqual(self.queue(q="wonder press")["count"], 1)
        self.assertEqual(self.queue(q="alice builder")["count"], 0)
        self.assertEqual(self.queue(q="rita@pub.com")["count"], 2)
        self.assertEqual(self.queue(q=str(a.id)[:8])["results"][0]["id"], str(a.id))
        self.assertEqual(self.queue(q=str(a.id))["count"], 1)            # a pasted full id works too
        self.assertEqual(self.queue(q="owner")["count"], 2)              # content owner username

    def test_search_survives_odd_input(self):
        self.make(1)
        for q in ("%", "'; drop table", "a" * 500, "\u0928\u092e\u0938\u094d\u0924\u0947"):
            self.assertEqual(self.get(dashboard.api_queue, self.l2, q=q).status_code, 200)
        self.assertEqual(self.get(dashboard.api_queue, self.l2, page="abc", size="zzz").status_code, 200)

    # ---- levels ----
    def test_l1_only_sees_level_one_claims_and_cannot_open_the_rest(self):
        simple = self.make(1)
        vip = self.file(post=mk_post(mk_user("vip", is_verified=True)))
        self.assertEqual(vip.required_level, 2)
        ids = {r["id"] for r in self.queue(self.l1)["results"]}
        self.assertEqual(ids, {str(simple.id)})
        req = self.rf.get("/x/"); req.user = self.l1
        self.assertEqual(dashboard.api_detail(req, vip.id).status_code, 404)
        self.assertEqual(len(self.queue(self.l2)["results"]), 2)

    def test_non_staff_and_users_without_a_role_are_refused(self):
        for u in (self.owner, mk_user("plainstaff", is_staff=True)):
            self.assertEqual(self.get(dashboard.api_queue, u).status_code, 403)

    # ---- detail ----
    def test_detail_has_everything_the_panel_shows(self):
        claim = self.make(1)
        req = self.rf.get("/x/"); req.user = self.l2
        data = json.loads(dashboard.api_detail(req, claim.id).content)["data"]
        for key in ("claimant_email", "preview", "owner_info", "timeline", "actions", "proof", "snapshot_files",
                    "claimant_history", "related", "counter_notice"):
            self.assertIn(key, data)
        self.assertEqual(data["owner_info"]["username"], "owner")
        self.assertIn("take", {a["key"] for a in data["actions"]})
        self.assertTrue(data["timeline"])

    def test_buttons_follow_the_level(self):
        claim = self.make(1)
        def keys(user):
            req = self.rf.get("/x/"); req.user = user
            return {a["key"] for a in json.loads(dashboard.api_detail(req, claim.id).content)["data"]["actions"]}
        self.assertNotIn("uphold", keys(self.l1))
        self.assertNotIn("reject_bad_faith", keys(self.l1))
        self.assertNotIn("hold", keys(self.l2))
        self.assertIn("uphold", keys(self.l2))
        self.assertIn("hold", keys(self.l3))
        self.assertNotIn("erase", keys(self.l3))                          # open claim: nothing to erase yet

    # ---- actions ----
    def act(self, user, claim, action, note=""):
        resp = self.send(dashboard.api_action, user, {"action": action, "note": note}, claim.id)
        return resp.status_code, json.loads(resp.content)

    def test_take_reject_and_the_note_rule(self):
        claim = self.make(1)
        self.assertEqual(self.act(self.l1, claim, "take")[0], 200)
        claim.refresh_from_db()
        self.assertEqual((claim.status, claim.reviewer_id), ("under_review", self.l1.pk))
        status, body = self.act(self.l1, claim, "reject", "")
        self.assertEqual(status, 400)                                     # a reason is required
        status, body = self.act(self.l1, claim, "reject", "No proof of ownership")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["status"], "rejected")

    def test_service_level_rules_still_apply_through_the_dashboard(self):
        claim = self.make(1)
        status, body = self.act(self.l1, claim, "uphold")
        self.assertEqual((status, body["code"]), (403, "forbidden"))
        status, _ = self.act(self.l2, claim, "uphold", "confirmed")
        self.assertEqual(status, 200)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "upheld")

    def test_staff_note_lands_in_the_audit_log(self):
        claim = self.make(1)
        self.assertEqual(self.act(self.l1, claim, "note", "called the claimant")[0], 200)
        self.assertTrue(claim.audit.filter(action="staff_note", note="called the claimant").exists())
        self.assertEqual(self.act(self.l1, claim, "note", "  ")[0], 400)

    def test_unknown_action_and_bad_json_are_400_not_500(self):
        claim = self.make(1)
        self.assertEqual(self.act(self.l2, claim, "explode")[0], 400)
        req = self.rf.post("/x/", data="{not json", content_type="application/json"); req.user = self.l2
        self.assertEqual(dashboard.api_action(req, claim.id).status_code, 400)

    def test_erase_button_appears_after_closing_and_works_for_l3_only(self):
        claim = self.make(1)
        self.act(self.l1, claim, "reject", "No proof")
        req = self.rf.get("/x/"); req.user = self.l3
        keys = {a["key"] for a in json.loads(dashboard.api_detail(req, claim.id).content)["data"]["actions"]}
        self.assertIn("erase", keys)
        self.assertEqual(self.act(self.l2, claim, "erase", "asked")[0], 403)
        status, body = self.act(self.l3, claim, "erase", "claimant asked by e-mail")
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["pii_erased"])
        self.assertEqual(body["data"]["claimant_name"], "[erased]")

    # ---- bulk ----
    def test_bulk_runs_what_is_allowed_and_reports_the_rest(self):
        a, b = self.make(1), self.make(2)
        resp = self.send(dashboard.api_bulk, self.l1, {"action": "take", "ids": [str(a.id), str(b.id), "not-a-uuid"]})
        body = json.loads(resp.content)
        self.assertEqual((resp.status_code, body["done"]), (200, 2))
        resp = self.send(dashboard.api_bulk, self.l1, {"action": "uphold", "ids": [str(a.id)], "note": "x"})
        self.assertEqual(json.loads(resp.content)["done"], 0)             # L1 may not uphold
        self.assertEqual(self.send(dashboard.api_bulk, self.l3, {"action": "erase", "ids": [str(a.id)]}).status_code, 400)

    # ---- the page itself ----
    def test_page_renders_for_staff_and_is_forbidden_otherwise(self):
        from django.urls import reverse
        req = self.rf.get("/x/"); req.user = self.l1
        resp = dashboard.page(req)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'id="cfg"', resp.content)
        req.user = self.owner
        self.assertEqual(dashboard.page(req).status_code, 403)
        self.assertTrue(reverse("admin:copyrights_dashboard").endswith("/copyrightclaim/dashboard/"))

    def test_dashboard_url_is_behind_the_admin_login(self):
        resp = self.client.get("/admin/copyrights/copyrightclaim/dashboard/")
        self.assertEqual(resp.status_code, 302)                           # not logged in -> admin login
        self.assertEqual(self.client.get("/admin/copyrights/copyrightclaim/dashboard/api/queue/").status_code, 302)


class DashboardOverHttpTests(Base):
    """Through the real admin URLs (login, CSRF, template) - not just the view functions."""

    BASE = "/admin/copyrights/copyrightclaim/dashboard/"

    def setUp(self):
        super().setUp()
        self.l1 = mk_staff("l1", 1)
        self.claim = self.file()

    def test_logged_in_staff_gets_the_page_and_the_json(self):
        self.client.force_login(self.l1)
        page = self.client.get(self.BASE)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Copyright moderation", page.content)
        data = self.client.get(self.BASE + "api/queue/").json()
        self.assertEqual(data["count"], 1)
        detail = self.client.get(f"{self.BASE}api/claim/{self.claim.id}/").json()
        self.assertEqual(detail["data"]["short_id"], str(self.claim.id)[:8])

    def test_post_without_csrf_token_is_refused(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.l1)
        resp = c.post(f"{self.BASE}api/claim/{self.claim.id}/action/", data=json.dumps({"action": "take"}),
                      content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "submitted")

    def test_changelist_links_to_the_dashboard(self):
        from django.conf import settings
        storages = {**settings.STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
        with override_settings(STORAGES=storages):                      # no collectstatic manifest in tests
            self._changelist_has_the_link()

    def _changelist_has_the_link(self):
        self.client.force_login(self.l1)
        resp = self.client.get("/admin/copyrights/copyrightclaim/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.BASE.encode(), resp.content)

    def test_user_without_a_copyright_role_gets_nothing(self):
        self.client.force_login(mk_user("plainstaff", is_staff=True))
        self.assertIn(self.client.get(self.BASE).status_code, (302, 403))
        self.assertIn(self.client.get(self.BASE + "api/queue/").status_code, (302, 403))
