"""Tests for: legal (court / government) orders, the public policy page, the 21-day default, system checks."""
from datetime import timedelta

from django.core.checks import run_checks
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from post.models import Post, Story

from . import legal_orders, services
from .models import CopyrightAuditLog, LegalOrder, LegalOrderItem
from .tests import Base, mk_post, mk_staff, mk_story, mk_user, notice

DATA = dict(reference="WP(C) 1234/2026", order_type="court", authority="High Court", summary="Remove the post.")


class LegalOrderTests(Base):
    def setUp(self):
        super().setUp()
        self.l1, self.l2, self.l3 = mk_staff("g1", 1), mk_staff("g2", 2), mk_staff("g3", 3)

    def register(self, targets=None, **kw):
        return legal_orders.register_order(self.l2, {**DATA, **kw}, targets or [("post", self.post.id)])

    def test_deadline_is_36_hours_by_default(self):
        o = self.register()
        self.assertAlmostEqual((o.due_at - o.received_at).total_seconds(), 36 * 3600, delta=5)
        self.assertEqual(o.items.get().owner, self.owner)

    @override_settings(LEGAL_ORDER_SLA_HOURS=24)
    def test_deadline_setting(self):
        o = self.register()
        self.assertAlmostEqual((o.due_at - o.received_at).total_seconds(), 24 * 3600, delta=5)

    def test_levels(self):
        with self.assertRaises(PermissionDenied):
            legal_orders.register_order(self.l1, DATA, [("post", self.post.id)])
        o = self.register()                                    # L2 may register
        with self.assertRaises(PermissionDenied):
            legal_orders.comply(o, self.l2)                    # ... but not decide
        legal_orders.start_review(o, self.l2)
        legal_orders.comply(o, self.l3, "ok")
        self.assertEqual(self.status_of(self.post), "legal_removed")

    def test_registering_needs_reference_authority_summary_and_targets(self):
        for bad in ({"reference": ""}, {"authority": " "}, {"summary": ""}):
            with self.assertRaises(services.ClaimError):
                self.register(**bad)
        with self.assertRaises(services.ClaimError):
            legal_orders.register_order(self.l2, DATA, [])

    def test_nothing_is_removed_until_comply_and_no_strike_is_issued(self):
        o = self.register()
        self.assertEqual(self.status_of(self.post), "approved")
        legal_orders.comply(o, self.l3)
        self.assertEqual(services.active_strike_count(self.owner), 0)
        o.refresh_from_db()
        self.assertEqual(o.status, "complied")
        with self.assertRaises(services.ClaimError):
            legal_orders.comply(o, self.l3)                    # already decided

    def test_removed_content_is_unreachable_and_not_editable(self):
        legal_orders.comply(self.register(), self.l3)
        c = APIClient()
        c.force_authenticate(mk_user("viewer"))
        self.assertEqual(c.get(f"/post/details/{self.post.id}/").status_code, 404)
        c.force_authenticate(self.owner)
        self.assertEqual(c.patch(f"/post/{self.post.id}/edit/", {"content": "x"}, format="json").status_code, 403)

    def test_owner_is_told_unless_order_forbids_it(self):
        from core.models import Notification
        legal_orders.comply(self.register(), self.l3)
        self.assertTrue(Notification.objects.filter(recipient=self.owner, title="Your content was removed").exists())
        other = mk_user("o2")
        p2 = mk_post(other)
        legal_orders.comply(self.register(targets=[("post", p2.id)], reference="X2", notify_owner=False), self.l3)
        self.assertFalse(Notification.objects.filter(recipient=other).exists())

    def test_reject_removes_nothing(self):
        o = self.register()
        with self.assertRaises(services.ClaimError):
            legal_orders.reject_order(o, self.l3, "")
        legal_orders.reject_order(o, self.l3, "not signed by a judge - challenging")
        self.assertEqual(self.status_of(self.post), "approved")

    def test_reverse_restores_exact_previous_state(self):
        p = mk_post(self.owner, moderation_status="flagged")
        o = self.register(targets=[("post", p.id)])
        legal_orders.comply(o, self.l3)
        with self.assertRaises(PermissionDenied):
            legal_orders.reverse_order(o, self.l2, "set aside")
        legal_orders.reverse_order(o, self.l3, "court set aside the order")
        self.assertEqual(self.status_of(p), "flagged")
        o.refresh_from_db()
        self.assertEqual(o.status, "reversed")

    def test_story_removed_and_restored(self):
        s = mk_story(self.owner)
        o = self.register(targets=[("story", s.id)])
        legal_orders.comply(o, self.l3)
        self.assertTrue(Story.objects.get(pk=s.pk).is_deleted)
        legal_orders.reverse_order(o, self.l3, "set aside")
        self.assertFalse(Story.objects.get(pk=s.pk).is_deleted)

    def test_missing_content_is_recorded_not_fatal(self):
        o = self.register(targets=[("post", "00000000-0000-0000-0000-000000000000"), ("post", self.post.id)])
        legal_orders.comply(o, self.l3)
        self.assertEqual(sorted(o.items.values_list("state", flat=True)), ["missing", "removed"])

    def test_two_orders_same_post_stays_down_until_both_reversed(self):
        o1 = self.register(reference="A")
        o2 = self.register(reference="B")
        legal_orders.comply(o1, self.l3)
        legal_orders.comply(o2, self.l3)
        legal_orders.reverse_order(o1, self.l3, "A set aside")
        self.assertEqual(self.status_of(self.post), "legal_removed")
        legal_orders.reverse_order(o2, self.l3, "B set aside")
        self.assertEqual(self.status_of(self.post), "approved")

    def test_copyright_claim_cannot_bring_back_legally_removed_post(self):
        legal_orders.comply(self.register(), self.l3)
        claim = self.file()                                    # a claim arrives later...
        services.withdraw_claim(claim, self.rita)              # ...and is withdrawn
        self.assertEqual(self.status_of(self.post), "legal_removed")

    def test_legal_order_over_copyright_hold_then_claim_closed_then_reversed(self):
        claim = self.file()                                    # post is copyright_hold
        o = self.register()
        legal_orders.comply(o, self.l3)
        services.withdraw_claim(claim, self.rita)              # claim closed while order runs
        self.assertEqual(self.status_of(self.post), "legal_removed")
        legal_orders.reverse_order(o, self.l3, "set aside")
        self.assertEqual(self.status_of(self.post), "approved")  # not stuck in a dead copyright state

    def test_story_stays_down_for_order_when_copyright_claim_is_withdrawn(self):
        s = mk_story(self.owner)
        claim, _ = services.submit_claim(self.rita, notice("story", s.id))
        legal_orders.comply(self.register(targets=[("story", s.id)]), self.l3)
        services.withdraw_claim(claim, self.rita)
        self.assertTrue(Story.objects.get(pk=s.pk).is_deleted)

    def test_deadline_warnings_fire_once_each(self):
        o = self.register()
        self.assertEqual(legal_orders.warn_due_orders(), 0)                       # 36h left: quiet
        LegalOrder.objects.filter(pk=o.pk).update(due_at=timezone.now() + timedelta(hours=5))
        self.assertEqual(legal_orders.warn_due_orders(), 1)
        self.assertEqual(legal_orders.warn_due_orders(), 0)                       # not again
        LegalOrder.objects.filter(pk=o.pk).update(due_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(legal_orders.warn_due_orders(), 1)                       # overdue
        self.assertEqual(legal_orders.warn_due_orders(), 0)
        self.assertEqual(CopyrightAuditLog.objects.filter(legal_order=o, action__startswith="order_").count(), 3)

    def test_decided_orders_are_not_warned_about(self):
        o = self.register()
        legal_orders.comply(o, self.l3)
        LegalOrder.objects.filter(pk=o.pk).update(due_at=timezone.now() - timedelta(hours=3))
        self.assertEqual(legal_orders.warn_due_orders(), 0)

    def test_report_hide_does_not_overwrite_legal_removal(self):
        from user_profile.report_automation import _hide_target
        legal_orders.comply(self.register(), self.l3)
        _hide_target("post", self.post.id)
        self.assertEqual(self.status_of(self.post), "legal_removed")

    def test_task_and_beat_entry_exist(self):
        from django.conf import settings
        from . import tasks
        self.assertEqual(tasks.warn_due_legal_orders(), 0)
        self.assertIn("copyrights.warn_due_legal_orders", {v["task"] for v in settings.CELERY_BEAT_SCHEDULE.values()})

    def test_admin_queryset_and_permissions(self):
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory
        ma = dj_admin.site._registry[LegalOrder]
        req = RequestFactory().get("/")
        req.user = self.l1
        self.assertFalse(ma.has_module_permission(req))
        req.user = self.l2
        self.assertTrue(ma.has_add_permission(req))
        self.assertFalse(ma.has_change_permission(req))
        self.assertFalse(ma.has_delete_permission(req))


class PolicyAndDefaultsTests(TestCase):
    def test_counter_notice_default_is_21_days(self):
        from django.conf import settings
        self.assertEqual(settings.COPYRIGHT_COUNTER_WAIT_DAYS, 21)

    @override_settings(GRIEVANCE_OFFICER_NAME="Asha Verma", GRIEVANCE_OFFICER_EMAIL="grievance@learnscroll.in",
                       DMCA_AGENT_EMAIL="dmca@learnscroll.in", DMCA_AGENT_NAME="D. Agent",
                       LEGAL_ORDERS_EMAIL="legal@learnscroll.in", COPYRIGHT_COUNTER_WAIT_DAYS=21)
    def test_public_endpoint_needs_no_login_and_matches_settings(self):
        r = APIClient().get("/copyright/policy/")
        self.assertEqual(r.status_code, 200)
        d = r.json()["data"]
        self.assertEqual(d["grievance_officer"]["name"], "Asha Verma")
        self.assertEqual(d["dmca_agent"]["email"], "dmca@learnscroll.in")
        self.assertEqual(d["process"]["counter_notice_wait_days"], 21)
        self.assertTrue(d["configured"])

    def test_public_endpoint_ignores_a_bad_token(self):
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        self.assertEqual(c.get("/copyright/policy/").status_code, 200)

    @override_settings(GRIEVANCE_OFFICER_NAME="Asha <b>Verma</b>", GRIEVANCE_OFFICER_EMAIL="g@x.in",
                       COPYRIGHT_COUNTER_WAIT_DAYS=21, COPYRIGHT_STRIKE_DAYS=180, DMCA_AGENT_EMAIL="", LEGAL_ORDERS_EMAIL="l@x.in")
    def test_html_page_has_the_numbers_escapes_input_and_skips_missing_dmca(self):
        for url in ("/copyright/policy/page/", "/legal/copyright-policy/"):
            r = APIClient().get(url)
            self.assertEqual(r.status_code, 200)
            html = r.content.decode()
            self.assertIn("21 days", html)
            self.assertIn("180 days", html)
            self.assertIn("g@x.in", html)
            self.assertIn("l@x.in", html)
            self.assertNotIn("<b>Verma</b>", html)           # settings values are escaped
            self.assertNotIn("Designated agent", html)       # no DMCA agent configured -> section omitted

    @override_settings(DEBUG=False, GRIEVANCE_OFFICER_NAME="", GRIEVANCE_OFFICER_EMAIL="", LEGAL_ORDERS_EMAIL="")
    def test_system_check_warns_when_legal_settings_are_empty(self):
        ids = {m.id for m in run_checks()}
        self.assertIn("copyrights.W001", ids)
        self.assertIn("copyrights.W002", ids)

    @override_settings(DEBUG=False, GRIEVANCE_OFFICER_NAME="A", GRIEVANCE_OFFICER_EMAIL="a@x.in",
                       LEGAL_ORDERS_EMAIL="l@x.in", COPYRIGHT_COUNTER_WAIT_DAYS=21)
    def test_no_warning_when_configured(self):
        self.assertFalse({m.id for m in run_checks()} & {"copyrights.W001", "copyrights.W002", "copyrights.W003"})
