"""
copyrights tests - claim flow, hold/restore, staff levels, counter-notice, strikes,
automations (Celery task bodies), report automation, and the block / visibility gaps
that were fixed together with it.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from post.models import Post, PostAnswer, PostComment, PostSave, Story
from user_profile.models import AutoModerationFlag, BlockUser, ContentReport
from user_profile.report_automation import resolve_reports
from user_profile.services import file_report

from . import services
from .models import (
    CopyrightAuditLog, CopyrightClaim, CopyrightCounterNotice, CopyrightStanding,
    CopyrightStrike, CopyrightTakedown,
)

User = get_user_model()
C = CopyrightClaim


def mk_user(name, **kw):
    return User.objects.create_user(username=name, password="pw12345678", email=f"{name}@x.com", **kw)


def mk_staff(name, level):
    u = mk_user(name, is_staff=True)
    group = Group.objects.get(name={1: "Copyright L1 Reviewer", 2: "Copyright L2 Senior", 3: "Copyright L3 Admin"}[level])
    u.groups.add(group)
    return User.objects.get(pk=u.pk)  # drop the perm cache


def mk_post(user, **kw):
    f = dict(user=user, post_type="text", content="my original-looking post", visibility="public",
             moderation_status="approved")
    f.update(kw)
    return Post.objects.create(**f)


def mk_story(user):
    return Story.objects.create(user=user, media="stories/2026/01/01/t.jpg", media_type="image", caption="cap")


def notice(target_type, target_id, **kw):
    d = dict(target_type=target_type, target_id=str(target_id), claimant_name="Rita Rights",
             claimant_email="rita@pub.com", organisation="Pub Co", is_rights_owner=True,
             work_description="My photo 'Sunrise' published in 2021.", original_work_url="https://pub.com/sunrise",
             good_faith_statement=True, accuracy_statement=True, signature="Rita Rights")
    d.update(kw)
    return d


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_copyright_roles", verbosity=0)

    def setUp(self):
        self.owner = mk_user("owner")
        self.rita = mk_user("rita")
        self.post = mk_post(self.owner)

    def file(self, post=None, claimant=None, **kw):
        post = post or self.post
        claim, created = services.submit_claim(claimant or self.rita, notice("post", post.id, **kw))
        return claim

    def status_of(self, post):
        return Post.objects.get(pk=post.pk).moderation_status


class ClaimFlowTests(Base):
    def test_complete_notice_puts_content_on_hold_and_logs(self):
        claim = self.file()
        self.assertEqual(self.status_of(self.post), "copyright_hold")
        self.assertEqual(claim.takedown.state, "held")
        self.assertEqual(claim.content_owner, self.owner)
        self.assertTrue(CopyrightAuditLog.objects.filter(claim=claim, action="auto_hold").exists())

    @override_settings(COPYRIGHT_AUTO_HOLD=False)
    def test_auto_hold_can_be_switched_off(self):
        self.file()
        self.assertEqual(self.status_of(self.post), "approved")

    def test_incomplete_notice_is_refused_and_nothing_is_hidden(self):
        for bad in ({"good_faith_statement": False}, {"accuracy_statement": False}, {"signature": "x"}):
            with self.assertRaises(services.ClaimError) as cm:
                services.submit_claim(self.rita, notice("post", self.post.id, **bad))
            self.assertEqual(cm.exception.code, "incomplete")
        self.assertEqual(self.status_of(self.post), "approved")

    def test_cannot_claim_own_or_missing_content(self):
        with self.assertRaises(services.ClaimError) as cm:
            services.submit_claim(self.owner, notice("post", self.post.id))
        self.assertEqual(cm.exception.code, "own_content")
        with self.assertRaises(services.ClaimError) as cm:
            services.submit_claim(self.rita, notice("post", "00000000-0000-0000-0000-000000000000"))
        self.assertEqual(cm.exception.code, "not_found")

    def test_same_claimant_same_content_is_idempotent(self):
        a, created_a = services.submit_claim(self.rita, notice("post", self.post.id))
        b, created_b = services.submit_claim(self.rita, notice("post", self.post.id))
        self.assertTrue(created_a)
        self.assertFalse(created_b)
        self.assertEqual(a.id, b.id)

    @override_settings(COPYRIGHT_CLAIMS_PER_DAY=2)
    def test_claimant_rate_limit(self):
        for _ in range(2):
            self.file(post=mk_post(self.owner))
        with self.assertRaises(services.ClaimError) as cm:
            self.file(post=mk_post(self.owner))
        self.assertEqual(cm.exception.code, "rate_limited")

    def test_claimant_with_bad_faith_history_is_blocked(self):
        l2 = mk_staff("l2a", 2)
        for _ in range(3):
            c = self.file(post=mk_post(self.owner))
            services.reject_claim(c, l2, "false claim", bad_faith=True)
        with self.assertRaises(services.ClaimError) as cm:
            self.file(post=mk_post(self.owner))
        self.assertEqual(cm.exception.code, "claimant_blocked")

    def test_second_claim_on_same_content_is_a_duplicate_and_follows_the_first(self):
        l2 = mk_staff("l2b", 2)
        first = self.file()
        second = self.file(claimant=mk_user("bob"))
        self.assertEqual(second.duplicate_of_id, first.id)
        services.uphold_claim(first, l2, "ok")
        second.refresh_from_db()
        self.assertEqual(second.status, "upheld")
        self.assertEqual(CopyrightStrike.objects.filter(user=self.owner).count(), 1)  # ONE strike only

    def test_withdraw_restores_content(self):
        claim = self.file()
        services.withdraw_claim(claim, self.rita)
        self.assertEqual(self.status_of(self.post), "approved")
        claim.refresh_from_db()
        self.assertEqual(claim.status, "withdrawn")

    def test_story_is_held_and_restored(self):
        story = mk_story(self.owner)
        claim, _ = services.submit_claim(self.rita, notice("story", story.id))
        self.assertTrue(Story.objects.get(pk=story.pk).is_deleted)
        services.withdraw_claim(claim, self.rita)
        self.assertFalse(Story.objects.get(pk=story.pk).is_deleted)

    def test_previous_moderation_state_is_restored_exactly(self):
        p = mk_post(self.owner, moderation_status="flagged")
        claim = self.file(post=p)
        services.withdraw_claim(claim, self.rita)
        self.assertEqual(self.status_of(p), "flagged")


class StaffLevelTests(Base):
    def setUp(self):
        super().setUp()
        self.l1, self.l2, self.l3 = mk_staff("l1", 1), mk_staff("l2", 2), mk_staff("l3", 3)

    def test_level_is_derived_from_group_permissions(self):
        from .permissions import level_for
        self.assertEqual([level_for(self.l1), level_for(self.l2), level_for(self.l3)], [1, 2, 3])
        self.assertEqual(level_for(self.owner), 0)
        su = User.objects.create_superuser("root", "r@x.com", "pw12345678")
        self.assertEqual(level_for(su), 3)
        not_staff = mk_user("ns")
        not_staff.groups.add(Group.objects.get(name="Copyright L3 Admin"))
        self.assertEqual(level_for(User.objects.get(pk=not_staff.pk)), 0)  # must be is_staff

    def test_l1_cannot_uphold_but_l2_can(self):
        claim = self.file()
        with self.assertRaises(PermissionDenied):
            services.uphold_claim(claim, self.l1, "x")
        self.assertEqual(self.status_of(self.post), "copyright_hold")
        services.uphold_claim(claim, self.l2, "confirmed")
        self.assertEqual(self.status_of(self.post), "copyright_removed")
        self.assertEqual(CopyrightStrike.objects.filter(user=self.owner).count(), 1)

    def test_l1_can_triage_and_reject_a_simple_claim(self):
        claim = self.file()
        services.take_for_review(claim, self.l1)
        services.reject_claim(claim, self.l1, "No proof of ownership")
        self.assertEqual(self.status_of(self.post), "approved")
        claim.refresh_from_db()
        self.assertEqual(claim.status, "rejected")

    def test_claims_against_verified_or_repeat_accounts_need_l2(self):
        verified = mk_user("vip", is_verified=True)
        claim = self.file(post=mk_post(verified))
        self.assertEqual(claim.required_level, 2)
        with self.assertRaises(PermissionDenied):
            services.reject_claim(claim, self.l1, "nope")
        services.reject_claim(claim, self.l2, "nope")

    def test_bad_faith_flag_needs_l2(self):
        claim = self.file()
        with self.assertRaises(PermissionDenied):
            services.reject_claim(claim, self.l1, "fake", bad_faith=True)
        services.reject_claim(claim, self.l2, "fake", bad_faith=True)
        claim.refresh_from_db()
        self.assertTrue(claim.bad_faith)

    def test_request_info_then_claimant_answers(self):
        claim = self.file()
        services.request_info(claim, self.l1, "Send a link to the original")
        claim.refresh_from_db()
        self.assertEqual(claim.status, "needs_info")
        services.provide_info(claim, self.rita, "Here: https://pub.com/sunrise")
        claim.refresh_from_db()
        self.assertEqual(claim.status, "under_review")

    def test_only_l3_can_reverse_an_upheld_claim_and_strike_goes_away(self):
        claim = self.file()
        services.uphold_claim(claim, self.l2, "ok")
        with self.assertRaises(PermissionDenied):
            services.restore_claim(claim, self.l2, "changed my mind")
        services.restore_claim(claim, self.l3, "wrongly upheld")
        self.assertEqual(self.status_of(self.post), "approved")
        self.assertEqual(services.active_strike_count(self.owner), 0)

    def test_only_l3_can_revoke_strikes_and_terminate(self):
        claim = self.file()
        services.uphold_claim(claim, self.l2, "ok")
        strike = claim.strike
        with self.assertRaises(PermissionDenied):
            services.revoke_strike(strike, self.l2, "x")
        with self.assertRaises(PermissionDenied):
            services.terminate_account(self.owner, self.l2, "x")
        services.revoke_strike(strike, self.l3, "appeal accepted")
        self.assertEqual(services.active_strike_count(self.owner), 0)

    def test_uphold_without_strike(self):
        claim = self.file()
        services.uphold_claim(claim, self.l2, "first time", issue_strike=False)
        self.assertEqual(self.status_of(self.post), "copyright_removed")
        self.assertEqual(services.active_strike_count(self.owner), 0)

    def test_l1_only_sees_simple_claims_in_admin_queryset(self):
        from django.contrib import admin as dj_admin
        from django.test import RequestFactory
        simple = self.file()
        vip = self.file(post=mk_post(mk_user("vip2", is_verified=True)))
        ma = dj_admin.site._registry[CopyrightClaim]
        req = RequestFactory().get("/")
        req.user = self.l1
        self.assertEqual(set(ma.get_queryset(req)), {simple})
        req.user = self.l2
        self.assertEqual(set(ma.get_queryset(req)), {simple, vip})

    def test_audit_log_records_actor(self):
        claim = self.file()
        services.uphold_claim(claim, self.l2, "ok")
        row = CopyrightAuditLog.objects.get(claim=claim, action="claim_upheld")
        self.assertEqual(row.actor, self.l2)


class CounterNoticeTests(Base):
    def setUp(self):
        super().setUp()
        self.l2 = mk_staff("l2c", 2)
        self.claim = self.file()
        services.uphold_claim(self.claim, self.l2, "ok")
        self.counter_data = dict(explanation="This is my own photo, here is the raw file.",
                                 good_faith_statement=True, jurisdiction_consent=True, signature="Olly Owner")

    def test_counter_notice_needs_statements_and_owner(self):
        with self.assertRaises(PermissionDenied):
            services.file_counter_notice(self.rita, self.claim, self.counter_data)
        with self.assertRaises(services.ClaimError):
            services.file_counter_notice(self.owner, self.claim, {**self.counter_data, "jurisdiction_consent": False})

    def test_auto_restore_after_wait_removes_strike(self):
        services.file_counter_notice(self.owner, self.claim, self.counter_data)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "countered")
        self.assertEqual(self.status_of(self.post), "copyright_removed")  # NOT restored yet
        self.assertEqual(services.auto_restore_due_counter_notices(), 0)  # waiting period still running
        CopyrightCounterNotice.objects.update(restore_after=timezone.now() - timedelta(minutes=1))
        self.assertEqual(services.auto_restore_due_counter_notices(), 1)
        self.assertEqual(self.status_of(self.post), "approved")
        self.assertEqual(services.active_strike_count(self.owner), 0)
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, "restored")
        self.assertEqual(services.auto_restore_due_counter_notices(), 0)  # idempotent

    def test_court_action_stops_the_auto_restore(self):
        services.file_counter_notice(self.owner, self.claim, self.counter_data)
        services.report_court_action(self.claim, self.rita, "Filed in Delhi High Court, case 123/2026, 3 Oct")
        CopyrightCounterNotice.objects.update(restore_after=timezone.now() - timedelta(days=1))
        self.assertEqual(services.auto_restore_due_counter_notices(), 0)
        self.assertEqual(self.status_of(self.post), "copyright_removed")

    def test_only_one_counter_notice(self):
        services.file_counter_notice(self.owner, self.claim, self.counter_data)
        with self.assertRaises(services.ClaimError):
            services.file_counter_notice(self.owner, self.claim, self.counter_data)

    @override_settings(COPYRIGHT_COUNTER_WAIT_DAYS=3)
    def test_wait_days_setting(self):
        c = services.file_counter_notice(self.owner, self.claim, self.counter_data)
        self.assertAlmostEqual((c.restore_after - timezone.now()).total_seconds(), 3 * 86400, delta=60)

    def test_upholding_again_rejects_the_counter_notice(self):
        services.file_counter_notice(self.owner, self.claim, self.counter_data)
        services.uphold_claim(self.claim, self.l2, "counter-notice not convincing")
        self.assertEqual(self.claim.counter_notice.__class__.objects.get().status, "rejected")
        CopyrightCounterNotice.objects.update(restore_after=timezone.now() - timedelta(days=1))
        self.assertEqual(services.auto_restore_due_counter_notices(), 0)
        self.assertEqual(self.status_of(self.post), "copyright_removed")


class StrikePolicyTests(Base):
    def setUp(self):
        super().setUp()
        self.l2, self.l3 = mk_staff("l2d", 2), mk_staff("l3d", 3)

    def strike_once(self):
        claim = self.file(post=mk_post(self.owner), claimant=mk_user(f"c{CopyrightClaim.objects.count()}"))
        services.uphold_claim(claim, self.l2, "ok")
        return claim

    def standing(self):
        return CopyrightStanding.objects.get(user=self.owner)

    def test_standing_ladder(self):
        self.strike_once()
        self.assertEqual(self.standing().level, "warning")
        self.assertTrue(services.can_upload(self.owner)[0])
        self.strike_once()
        self.assertEqual(self.standing().level, "restricted")
        self.assertFalse(services.can_upload(self.owner)[0])
        self.strike_once()
        self.assertEqual(self.standing().level, "review")

    def test_strikes_expire_and_uploads_come_back(self):
        self.strike_once()
        self.strike_once()
        self.assertFalse(services.can_upload(self.owner)[0])
        CopyrightStrike.objects.update(expires_at=timezone.now() - timedelta(days=1))
        services.expire_strikes()
        self.assertEqual(self.standing().level, "good")
        self.assertTrue(services.can_upload(self.owner)[0])

    def test_terminate_and_reinstate(self):
        for _ in range(3):
            self.strike_once()
        services.terminate_account(self.owner, self.l3, "repeat infringer")
        self.owner.refresh_from_db()
        self.assertFalse(self.owner.is_active)
        self.assertFalse(services.can_upload(self.owner)[0])
        services.expire_strikes()
        self.assertEqual(self.standing().level, "terminated")  # automation never un-terminates
        services.reinstate_account(self.owner, self.l3, "appeal ok")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)

    def test_staff_cannot_be_terminated(self):
        with self.assertRaises(services.ClaimError):
            services.terminate_account(self.l2, self.l3, "x")

    def test_blocked_uploader_gets_403_from_post_and_story_create(self):
        self.strike_once()
        self.strike_once()
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.owner)
        r = client.post(reverse("post-create"), {"content": "hi", "post_type": "text"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "copyright_upload_blocked")
        r = client.post(reverse("story-create"), {})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "copyright_upload_blocked")


class AutomationTests(Base):
    def test_stale_claims_are_escalated_once(self):
        claim = self.file()
        CopyrightClaim.objects.filter(pk=claim.pk).update(created_at=timezone.now() - timedelta(hours=60))
        self.assertEqual(services.escalate_stale_claims(), 1)
        claim.refresh_from_db()
        self.assertEqual((claim.priority, claim.required_level), ("urgent", 2))
        self.assertIsNotNone(claim.escalated_at)
        self.assertEqual(services.escalate_stale_claims(), 0)

    def test_fresh_claims_are_not_escalated(self):
        self.file()
        self.assertEqual(services.escalate_stale_claims(), 0)

    def test_needs_info_expiry_closes_claim_and_restores_content(self):
        l1 = mk_staff("l1e", 1)
        claim = self.file()
        services.request_info(claim, l1, "need the link")
        CopyrightClaim.objects.filter(pk=claim.pk).update(info_requested_at=timezone.now() - timedelta(days=20))
        self.assertEqual(services.expire_needs_info(), 1)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "rejected")
        self.assertEqual(self.status_of(self.post), "approved")

    def test_celery_tasks_delegate(self):
        from . import tasks
        self.assertEqual(tasks.auto_restore_counter_notices(), 0)
        self.assertEqual(tasks.escalate_stale_claims(), 0)
        self.assertEqual(tasks.expire_needs_info(), 0)
        self.assertEqual(tasks.expire_strikes(), 0)

    def test_beat_schedule_points_at_registered_tasks(self):
        from django.conf import settings
        names = {v["task"] for k, v in settings.CELERY_BEAT_SCHEDULE.items() if k.startswith("copyrights-")}
        self.assertEqual(names, {"copyrights.auto_restore_counter_notices", "copyrights.expire_strikes",
                                 "copyrights.escalate_stale_claims", "copyrights.expire_needs_info"})
        from . import tasks
        registered = {tasks.auto_restore_counter_notices.name, tasks.expire_strikes.name,
                      tasks.escalate_stale_claims.name, tasks.expire_needs_info.name}
        self.assertEqual(names, registered)


class ApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_copyright_roles", verbosity=0)

    def setUp(self):
        self.owner, self.rita = mk_user("owner"), mk_user("rita")
        self.post = mk_post(self.owner)

    def test_full_api_round_trip(self):
        self.client.force_authenticate(self.rita)
        r = self.client.post("/copyright/claims/", notice("post", self.post.id), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        claim_id = r.json()["data"]["id"]
        self.assertEqual(self.client.get("/copyright/claims/").json()["data"][0]["id"], claim_id)

        # the post is gone for everyone else, still visible to its author
        self.client.force_authenticate(mk_user("viewer"))
        self.assertEqual(self.client.get(f"/post/details/{self.post.id}/").status_code, 404)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get(f"/post/details/{self.post.id}/").status_code, 200)

        # owner sees the notice WITHOUT claimant e-mail and can counter
        r = self.client.get("/copyright/notices/")
        row = r.json()["data"][0]
        self.assertNotIn("claimant_email", row)
        self.assertTrue(row["can_counter"])
        r = self.client.post(f"/copyright/notices/{claim_id}/counter/", dict(
            explanation="It is my own photograph, raw file available.", good_faith_statement=True,
            jurisdiction_consent=True, signature="Olly Owner"), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["data"]["status"], "countered")

        # stranger cannot see someone else's notice
        self.client.force_authenticate(mk_user("nosy"))
        self.assertEqual(self.client.get(f"/copyright/notices/{claim_id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/copyright/claims/{claim_id}/").status_code, 404)

    def test_validation_and_errors(self):
        self.client.force_authenticate(self.rita)
        r = self.client.post("/copyright/claims/", notice("post", self.post.id, good_faith_statement=False), format="json")
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/copyright/claims/", notice("post", "00000000-0000-0000-0000-000000000000"), format="json")
        self.assertEqual(r.status_code, 404)
        self.client.force_authenticate(self.owner)
        r = self.client.post("/copyright/claims/", notice("post", self.post.id), format="json")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["code"], "own_content")

    def test_standing_endpoint(self):
        self.client.force_authenticate(self.owner)
        d = self.client.get("/copyright/standing/").json()["data"]
        self.assertEqual((d["level"], d["active_strikes"], d["uploads_blocked"]), ("good", 0, False))

    def test_anonymous_is_rejected(self):
        self.assertIn(self.client.get("/copyright/claims/").status_code, (401, 403))


class VisibilityGuardAndBlockGapTests(APITestCase):
    """Takedown must really take the post down, and block must cover Saved + answers."""

    def setUp(self):
        self.author, self.viewer = mk_user("author"), mk_user("viewer")

    def test_hidden_post_cannot_be_reached_by_id(self):
        p = mk_post(self.author, moderation_status="copyright_removed")
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(f"/post/details/{p.id}/").status_code, 404)
        self.assertEqual(self.client.post(f"/post/like/{p.id}/reaction/", {"reaction": "like"}, format="json").status_code, 404)
        self.assertEqual(self.client.get(f"/post/like/{p.id}/reaction/").status_code, 404)
        self.assertEqual(self.client.post(f"/post/{p.id}/save/").status_code, 404)  # can't save a hidden post

    def test_save_blocked_for_blocked_author_but_unsave_always_works(self):
        p = mk_post(self.author)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.post(f"/post/{p.id}/save/").status_code, 201)   # save
        BlockUser.objects.create(blocker=self.viewer, blocked=self.author)
        self.assertEqual(self.client.post(f"/post/{p.id}/save/").status_code, 200)   # un-save still allowed
        self.assertFalse(PostSave.objects.filter(post=p, user=self.viewer).exists())
        self.assertEqual(self.client.post(f"/post/{p.id}/save/").status_code, 404)   # re-save refused

    def test_flagged_and_deleted_posts_are_hidden_too(self):
        flagged = mk_post(self.author, moderation_status="flagged")
        gone = mk_post(self.author, is_deleted=True)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.client.get(f"/post/details/{flagged.id}/").status_code, 404)
        self.assertEqual(self.client.get(f"/post/details/{gone.id}/").status_code, 404)

    def test_owner_cannot_edit_while_copyright_hold(self):
        p = mk_post(self.author, moderation_status="copyright_hold")
        self.client.force_authenticate(self.author)
        r = self.client.patch(f"/post/{p.id}/edit/", {"content": "new"}, format="json")
        self.assertIn(r.status_code, (403, 404))

    def test_saved_list_hides_taken_down_and_blocked_authors(self):
        ok = mk_post(self.author)
        removed = mk_post(self.author, moderation_status="copyright_removed")
        other = mk_user("other")
        from_other = mk_post(other)
        for p in (ok, removed, from_other):
            PostSave.objects.create(post=p, user=self.viewer)
        BlockUser.objects.create(blocker=self.viewer, blocked=other)
        self.client.force_authenticate(self.viewer)
        data = self.client.get(reverse("saved-posts-list")).json()
        results = data.get("results", data.get("data", data))
        ids = {str(r["id"]) for r in (results.get("results", results) if isinstance(results, dict) else results)}
        self.assertEqual(ids, {str(ok.id)})

    def test_doubt_answers_respect_block_and_hidden_posts(self):
        doubt = mk_post(self.author, post_type="doubt", content="how?")
        blocked_guy = mk_user("blockedguy")
        PostAnswer.objects.create(post=doubt, user=blocked_guy, content="answer from a blocked person")
        good = mk_user("goodguy")
        PostAnswer.objects.create(post=doubt, user=good, content="fine answer")
        BlockUser.objects.create(blocker=self.viewer, blocked=blocked_guy)
        self.client.force_authenticate(self.viewer)
        body = self.client.get(f"/post/{doubt.id}/answers/").json()
        rows = body.get("results", body)
        self.assertEqual(len(rows), 1)
        # blocked person cannot answer a doubt of someone who blocked them
        BlockUser.objects.create(blocker=self.author, blocked=blocked_guy)
        self.client.force_authenticate(blocked_guy)
        r = self.client.post(f"/post/{doubt.id}/answers/", {"content": "sneaky"}, format="json")
        self.assertEqual(r.status_code, 404)
        # hidden doubt: nobody else sees answers
        Post.objects.filter(pk=doubt.pk).update(moderation_status="copyright_removed")
        self.client.force_authenticate(self.viewer)
        body = self.client.get(f"/post/{doubt.id}/answers/").json()
        self.assertEqual(len(body.get("results", body)), 0)


class ReportAutomationTests(TestCase):
    def setUp(self):
        self.author = mk_user("author")
        self.post = mk_post(self.author)
        self.reporters = [mk_user(f"rep{i}") for i in range(10)]

    def report(self, i, reason="nudity", target=None, ttype="post"):
        return file_report(self.reporters[i], ttype, (target or self.post).id, reason)

    def test_reported_count_is_now_maintained(self):
        self.report(0, "spam")
        self.report(1, "spam")
        self.assertEqual(Post.objects.get(pk=self.post.pk).reported_count, 2)

    def test_severe_reports_auto_hold_the_post_and_flag_it(self):
        self.report(0)
        self.report(1)
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.report(2)  # 3rd distinct severe reporter
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")
        flag = AutoModerationFlag.objects.get(target_type="post", target_id=str(self.post.id))
        self.assertEqual((flag.source, flag.severity, flag.status), ("reports", "high", "open"))

    def test_mild_reports_need_the_higher_threshold(self):
        for i in range(7):
            self.report(i, "spam")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.report(7, "spam")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")

    def test_same_person_cannot_report_twice_to_force_a_hold(self):
        for _ in range(5):
            self.report(0)
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")

    @override_settings(REPORT_UNRELIABLE_DISMISSED=3)
    def test_unreliable_reporters_do_not_count(self):
        troll = self.reporters[0]
        for i in range(3):  # troll's earlier reports were all dismissed
            p = mk_post(self.author)
            rep, _ = file_report(troll, "post", p.id, "spam")
            ContentReport.objects.filter(pk=rep.pk).update(status="dismissed")
        self.report(0)
        self.report(1)
        self.report(2)  # troll + 2 reliable = only 2 count
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.report(3)
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")

    def test_copyright_report_does_not_auto_hold_and_points_to_the_notice(self):
        for i in range(5):
            self.report(i, "copyright")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.reporters[9])
        r = client.post("/profile/reports/", {"target_type": "post", "target_id": str(self.post.id),
                                              "reason": "copyright"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["next"], "copyright_claim")

    def test_comment_is_hidden_at_threshold(self):
        comment = PostComment.objects.create(post=self.post, user=self.author, content="rude")
        for i in range(3):
            self.report(i, "harassment", target=comment, ttype="comment")
        self.assertTrue(PostComment.objects.get(pk=comment.pk).is_hidden)

    def test_moderator_hide_action_closes_all_sibling_reports(self):
        for i in range(2):
            self.report(i, "nudity")
        first = ContentReport.objects.filter(target_id=str(self.post.id)).first()
        resolve_reports([first], "hide")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "rejected")
        self.assertFalse(ContentReport.objects.filter(target_id=str(self.post.id)).exclude(status="actioned").exists())

    def test_moderator_dismiss_restores_auto_held_post(self):
        for i in range(3):
            self.report(i, "nudity")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")
        resolve_reports([ContentReport.objects.filter(target_id=str(self.post.id)).first()], "dismiss")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.assertEqual(AutoModerationFlag.objects.get(target_id=str(self.post.id)).status, "dismissed")

    def test_hide_never_overwrites_a_copyright_state(self):
        Post.objects.filter(pk=self.post.pk).update(moderation_status="copyright_removed")
        self.report(0)
        resolve_reports([ContentReport.objects.first()], "hide")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "copyright_removed")
