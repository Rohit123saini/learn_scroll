"""Tests for audit items 16-21 (reports on more targets, notices + appeals, child safety,
brigading, audit trail). Written without being able to run them - run:
    python manage.py test user_profile.tests_trust_safety
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from copyrights import appeals as cr_appeals
from copyrights import services as cr_services
from copyrights.models import CopyrightAppeal, CopyrightStanding
from copyrights.tests import mk_claimant, mk_post, mk_staff, mk_user, notice
from post.models import Post, PostComment
from user_profile import appeals, child_safety
from user_profile.models import (
    AutoModerationFlag, ChildSafetyCase, ContentReport, ModerationAction, ModerationAppeal,
)
from user_profile.report_automation import analyse_reports, resolve_reports
from user_profile.services import file_report

User = get_user_model()


def old_users(prefix, n):
    us = [mk_user(f"{prefix}{i}") for i in range(n)]
    User.objects.filter(pk__in=[u.pk for u in us]).update(date_joined=timezone.now() - timedelta(days=90))
    return us


class AuditAndNoticeTests(TestCase):
    def setUp(self):
        self.author = mk_user("author")
        self.post = mk_post(self.author)
        self.reps = old_users("r", 6)

    def hold(self):
        for r in self.reps[:3]:
            file_report(r, "post", self.post.id, "nudity")

    def test_auto_hold_is_logged_and_owner_gets_a_notice_with_appeal(self):
        self.hold()
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")
        row = ModerationAction.objects.get(action="auto_hold")
        self.assertEqual((row.target_owner_id, row.owner_visible, row.can_appeal), (self.author.id, True, True))
        c = APIClient()
        c.force_authenticate(self.author)
        data = c.get("/profile/notices/").json()["data"]
        self.assertEqual(len(data), 1)
        self.assertTrue(data[0]["can_appeal"])
        self.assertEqual(data[0]["note"], "")  # internal note never shown

    def test_appeal_accepted_restores_content_and_needs_another_reviewer(self):
        self.hold()
        row = ModerationAction.objects.get(action="auto_hold")
        ap = appeals.file_appeal(self.author, row.pk, "This is my own photography class post")
        with self.assertRaises(appeals.AppealError):  # one appeal per decision
            appeals.file_appeal(self.author, row.pk, "Please look again at this one")
        staff = mk_user("mod", is_staff=True)
        appeals.decide_appeal(ap, staff, True, "looks fine")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.assertTrue(ModerationAction.objects.filter(action="appeal_accepted").exists())
        self.assertEqual(ModerationAppeal.objects.get(pk=ap.pk).status, "accepted")

    def test_appeal_rejected_needs_reason(self):
        self.hold()
        row = ModerationAction.objects.get(action="auto_hold")
        ap = appeals.file_appeal(self.author, row.pk, "This is my own photography class post")
        staff = mk_user("mod", is_staff=True)
        with self.assertRaises(appeals.AppealError):
            appeals.decide_appeal(ap, staff, False, "")
        appeals.decide_appeal(ap, staff, False, "nudity confirmed")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")

    def test_only_owner_can_appeal(self):
        self.hold()
        row = ModerationAction.objects.get(action="auto_hold")
        with self.assertRaises(appeals.AppealError):
            appeals.file_appeal(self.reps[5], row.pk, "not mine but I want it back")

    def test_staff_hide_and_dismiss_are_audited_with_the_staff_member(self):
        staff = mk_user("mod", is_staff=True)
        rep, _ = file_report(self.reps[0], "post", self.post.id, "spam")
        resolve_reports([rep], "hide", staff)
        row = ModerationAction.objects.get(action="hide")
        self.assertEqual((row.actor_id, row.actor_label), (staff.id, "mod"))
        p2 = mk_post(self.author)
        rep2, _ = file_report(self.reps[1], "post", p2.id, "spam")
        resolve_reports([rep2], "dismiss", staff)
        self.assertTrue(ModerationAction.objects.filter(action="dismiss", target_id=str(p2.id)).exists())


class NewTargetTests(TestCase):
    def setUp(self):
        self.owner = mk_user("owner")
        self.rep = old_users("r", 1)[0]

    def test_profile_photo_needs_a_photo_and_cannot_be_own(self):
        with self.assertRaises(LookupError):
            file_report(self.rep, "profile_photo", self.owner.id, "nudity")  # no photo
        User.objects.filter(pk=self.owner.pk).update(profile_photo="profile/x.jpg")
        r, created = file_report(self.rep, "profile_photo", self.owner.id, "nudity")
        self.assertTrue(created)
        self.assertEqual(r.reported_user_id, self.owner.id)
        with self.assertRaises(ValueError):
            file_report(self.owner, "profile_photo", self.owner.id, "nudity")

    def test_unknown_message_is_not_found(self):
        with self.assertRaises(LookupError):
            file_report(self.rep, "message", "00000000-0000-0000-0000-000000000000", "harassment")

    def test_target_type_accepted_by_api(self):
        c = APIClient()
        c.force_authenticate(self.rep)
        r = c.post("/profile/reports/", {"target_type": "live_session", "target_id": "999", "reason": "other"}, format="json")
        self.assertEqual(r.status_code, 404)  # valid type, nothing there


class ChildSafetyTests(TestCase):
    def setUp(self):
        self.author = mk_user("author")
        self.post = mk_post(self.author)
        self.rep = old_users("r", 1)[0]

    def test_first_report_holds_opens_case_and_is_urgent(self):
        rep, _ = file_report(self.rep, "post", self.post.id, "child_safety")
        rep.refresh_from_db()
        self.assertEqual(rep.priority, "urgent")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")
        case = ChildSafetyCase.objects.get(target_id=str(self.post.id))
        self.assertTrue(case.snapshot.get("owner_id") == self.author.id)
        self.assertGreater(case.preserve_until, timezone.now() + timedelta(days=100))
        row = ModerationAction.objects.get(action="auto_hold_child")
        self.assertFalse(row.can_appeal)  # no appeal, no tipping off

    def test_owner_cannot_appeal_child_safety_hold(self):
        file_report(self.rep, "post", self.post.id, "child_safety")
        row = ModerationAction.objects.get(action="auto_hold_child")
        with self.assertRaises(appeals.AppealError):
            appeals.file_appeal(self.author, row.pk, "I did nothing wrong at all")

    def test_api_returns_help_numbers(self):
        c = APIClient()
        c.force_authenticate(self.rep)
        r = c.post("/profile/reports/", {"target_type": "post", "target_id": str(self.post.id),
                                         "reason": "child_safety"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()["help"]["childline"], "1098")

    def test_staff_need_permission_and_reference(self):
        file_report(self.rep, "post", self.post.id, "child_safety")
        case = ChildSafetyCase.objects.get()
        plain = mk_user("plain", is_staff=True)
        with self.assertRaises(child_safety.ChildSafetyError):
            child_safety.mark_reported(case, plain, "SJPU", "FIR 1")
        officer = mk_user("officer", is_staff=True)
        officer.user_permissions.add(Permission.objects.get(codename="handle_child_safety"))
        officer = User.objects.get(pk=officer.pk)
        with self.assertRaises(child_safety.ChildSafetyError):
            child_safety.mark_reported(case, officer, "SJPU", "")
        child_safety.mark_reported(case, officer, "SJPU Moradabad", "ACK-77")
        case.refresh_from_db()
        self.assertEqual((case.status, case.authority_reference), ("reported", "ACK-77"))
        self.assertTrue(child_safety.evidence_must_be_kept("post", self.post.id))
        self.assertTrue(ModerationAction.objects.filter(action="child_reported_authority").exists())

    def test_overdue_case_warns_once(self):
        file_report(self.rep, "post", self.post.id, "child_safety")
        ChildSafetyCase.objects.update(due_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(child_safety.warn_overdue_cases(), 1)
        self.assertEqual(child_safety.warn_overdue_cases(), 0)


class BrigadingTests(TestCase):
    def setUp(self):
        self.author = mk_user("author")
        self.post = mk_post(self.author)

    def test_new_accounts_count_half(self):
        news = [mk_user(f"n{i}") for i in range(3)]
        for u in news:
            file_report(u, "post", self.post.id, "nudity")
        a = analyse_reports("post", self.post.id)
        self.assertEqual((a["raw_count"], a["severe"]), (3, 1))
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")

    def test_mostly_new_accounts_goes_to_review_not_hold(self):
        news = [mk_user(f"n{i}") for i in range(8)]
        for u in news:
            file_report(u, "post", self.post.id, "harassment")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "approved")
        self.assertTrue(AutoModerationFlag.objects.filter(reason="report_brigading").exists())
        self.assertTrue(ContentReport.objects.filter(target_id=str(self.post.id), brigading_suspected=True).exists())

    @override_settings(REPORT_BRIGADE_MODE="off", REPORT_NEW_ACCOUNT_WEIGHT=1.0)
    def test_guard_can_be_switched_off(self):
        for u in [mk_user(f"n{i}") for i in range(3)]:
            file_report(u, "post", self.post.id, "nudity")
        self.assertEqual(Post.objects.get(pk=self.post.pk).moderation_status, "flagged")

    @override_settings(REPORT_PERSISTENT_DISMISSED=2)
    def test_persistent_complainant_counts_zero_against_same_person(self):
        pest = old_users("pest", 1)[0]
        for _ in range(2):
            p = mk_post(self.author)
            rep, _ = file_report(pest, "post", p.id, "spam")
            ContentReport.objects.filter(pk=rep.pk).update(status="dismissed")
        file_report(pest, "post", self.post.id, "nudity")
        self.assertEqual(analyse_reports("post", self.post.id)["severe"], 0)


class StrikeAppealTests(TestCase):
    def setUp(self):
        from django.core.management import call_command

        call_command("setup_copyright_roles", verbosity=0)
        self.owner = mk_user("owner")
        self.l2 = mk_staff("l2", 2)
        self.l3a = mk_staff("l3a", 3)
        self.l3b = mk_staff("l3b", 3)
        post = mk_post(self.owner)
        claimant = mk_claimant("claimant")  # e-mail verified + vetted (v4 rules), matches notice() default e-mail
        claim, _ = cr_services.submit_claim(claimant, notice("post", post.id))
        cr_services.uphold_claim(claim, self.l3a, "ok")
        self.strike = claim.strike

    def test_strike_appeal_accept_by_other_l3_revokes(self):
        ap = cr_appeals.file_strike_appeal(self.owner, self.strike.pk, "I own the original photo, here is proof")
        with self.assertRaises(cr_services.ClaimError):  # same strike can't be appealed twice
            cr_appeals.file_strike_appeal(self.owner, self.strike.pk, "Another try with more words")
        with self.assertRaises(cr_services.ClaimError):  # issuer can't decide
            cr_appeals.decide_appeal(ap, self.l3a, True, "ok")
        cr_appeals.decide_appeal(ap, self.l3b, True, "proof accepted")
        self.strike.refresh_from_db()
        self.assertIsNotNone(self.strike.revoked_at)

    def test_l2_cannot_decide(self):
        ap = cr_appeals.file_strike_appeal(self.owner, self.strike.pk, "I own the original photo, here is proof")
        with self.assertRaises(Exception):
            cr_appeals.decide_appeal(ap, self.l2, True, "x")

    def test_other_user_cannot_appeal_my_strike(self):
        other = mk_user("other")
        with self.assertRaises(cr_services.ClaimError):
            cr_appeals.file_strike_appeal(other, self.strike.pk, "this is not even my strike")

    def test_termination_appeal_is_public_generic_and_reinstates(self):
        cr_services.terminate_account(self.owner, self.l3a, "repeat")
        c = APIClient()
        body = {"identifier": "owner", "contact_email": "owner@x.com", "message": "Please review my case again"}
        r = c.post("/copyright/termination-appeal/", body, format="json")
        self.assertEqual(r.status_code, 202)
        unknown = c.post("/copyright/termination-appeal/", {**body, "identifier": "nobody"}, format="json")
        self.assertEqual(r.json()["message"], unknown.json()["message"])  # same answer either way
        ap = CopyrightAppeal.objects.get(kind="termination")
        cr_appeals.decide_appeal(ap, self.l3b, True, "reinstated")
        self.assertTrue(User.objects.get(pk=self.owner.pk).is_active)
        self.assertNotEqual(CopyrightStanding.objects.get(user=self.owner).level, "terminated")

    def test_live_account_cannot_file_termination_appeal(self):
        cr_appeals.file_termination_appeal("owner", "owner@x.com", "I am not even terminated")
        self.assertFalse(CopyrightAppeal.objects.exists())
