"""Tests for the trusted-claimant tiers: "verified" (held at once) and "publisher" (fast track:
queue priority, shorter review deadline, higher limits, expiry, demotion after bad faith)."""
from datetime import timedelta

from django.test import override_settings
from django.utils import timezone

from . import claimant as claimant_rules
from . import services
from .models import CopyrightAuditLog, CopyrightClaim, TrustedClaimant
from .tests import Base, mk_post, mk_staff, notice


def make_publisher(user, **kw):
    rec = TrustedClaimant.objects.filter(user=user).first()
    if rec is None:
        return TrustedClaimant.objects.create(user=user, organisation="Big Publisher",
                                              tier=TrustedClaimant.Tier.PUBLISHER, **kw)
    for k, v in dict(tier=TrustedClaimant.Tier.PUBLISHER, **kw).items():
        setattr(rec, k, v)
    rec.save()
    return rec


class TrustedTierTests(Base):
    """self.rita (from Base) is a staff-vetted claimant on the default tier = "verified"."""

    def file_on(self, post, claimant=None):
        return services.submit_claim(claimant or self.rita, notice("post", post.id))[0]

    def test_default_tier_is_verified_and_behaves_as_before(self):
        self.assertEqual(TrustedClaimant.objects.get(user=self.rita).tier, "verified")
        claim = self.file_on(self.post)
        self.assertEqual(claim.claimant_tier, "trusted")
        self.assertEqual(claim.priority, "normal")
        self.assertEqual(self.status_of(self.post), "copyright_hold")

    def test_publisher_is_held_at_once_and_goes_to_the_front_of_the_queue(self):
        make_publisher(self.rita)
        claim = self.file_on(self.post)
        self.assertEqual(claim.claimant_tier, "publisher")
        self.assertEqual(claim.priority, "high")
        self.assertEqual(claim.required_level, 1)                  # fast track is not a higher bar for the decision
        self.assertEqual(self.status_of(self.post), "copyright_hold")
        self.assertTrue(CopyrightAuditLog.objects.filter(claim=claim, action="claim_submitted",
                                                         note__contains="fast track").exists())

    def test_publisher_claim_against_a_big_account_still_needs_l2(self):
        make_publisher(self.rita)
        type(self.owner).objects.filter(pk=self.owner.pk).update(followers_count=50000)
        claim = self.file_on(self.post)
        self.assertEqual((claim.priority, claim.required_level), ("high", 2))

    def test_publisher_notice_is_never_decided_by_itself(self):
        make_publisher(self.rita)
        claim = self.file_on(self.post)
        claim.refresh_from_db()
        self.assertEqual(claim.status, "submitted")                # still waits for a reviewer
        self.assertIsNone(claim.decided_at)

    def test_expired_vetting_falls_back_to_a_standard_claimant(self):
        make_publisher(self.rita, valid_until=timezone.now() - timedelta(days=1))
        self.assertEqual(claimant_rules.tier_for(self.rita), "standard")
        claim = self.file_on(self.post)
        self.assertEqual(self.status_of(self.post), "approved")    # no proof -> not held
        self.assertEqual(claim.priority, "normal")

    def test_vetting_that_has_not_expired_yet_counts(self):
        make_publisher(self.rita, valid_until=timezone.now() + timedelta(days=30))
        self.assertEqual(claimant_rules.tier_for(self.rita), "publisher")

    def test_switched_off_row_is_an_ordinary_claimant(self):
        make_publisher(self.rita, active=False)
        self.assertEqual(claimant_rules.tier_for(self.rita), "standard")

    def test_bad_faith_finding_removes_the_fast_track_but_keeps_the_hold(self):
        make_publisher(self.rita)
        first = self.file_on(self.post)
        services.reject_claim(first, mk_staff("l2", 2), "false notice", bad_faith=True)
        self.assertEqual(claimant_rules.tier_for(self.rita), "trusted")
        p2 = mk_post(self.owner)
        c2 = self.file_on(p2)
        self.assertEqual((c2.claimant_tier, c2.priority), ("trusted", "normal"))
        self.assertEqual(self.status_of(p2), "copyright_hold")

    def test_old_bad_faith_finding_is_forgotten_after_a_year(self):
        make_publisher(self.rita)
        old = self.file_on(self.post)
        services.reject_claim(old, mk_staff("l2b", 2), "false notice", bad_faith=True)
        CopyrightClaim.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=400))
        self.assertEqual(claimant_rules.tier_for(self.rita), "publisher")

    @override_settings(COPYRIGHT_TRUSTED_HOLDS_PER_DAY=2)
    def test_verified_tier_has_a_daily_cap_on_automatic_holds(self):
        posts = [mk_post(self.owner) for _ in range(3)]
        for p in posts:
            self.file_on(p)
        states = [self.status_of(p) for p in posts]
        self.assertEqual((states.count("copyright_hold"), states.count("approved")), (2, 1))

    @override_settings(COPYRIGHT_TRUSTED_HOLDS_PER_DAY=0)
    def test_cap_zero_means_no_cap(self):
        posts = [mk_post(self.owner) for _ in range(4)]
        for p in posts:
            self.file_on(p)
        self.assertTrue(all(self.status_of(p) == "copyright_hold" for p in posts))

    @override_settings(COPYRIGHT_TRUSTED_HOLDS_PER_DAY=1, COPYRIGHT_PUBLISHER_HOLDS_PER_DAY=3)
    def test_publisher_has_its_own_higher_cap(self):
        make_publisher(self.rita)
        posts = [mk_post(self.owner) for _ in range(4)]
        for p in posts:
            self.file_on(p)
        states = [self.status_of(p) for p in posts]
        self.assertEqual((states.count("copyright_hold"), states.count("approved")), (3, 1))

    @override_settings(COPYRIGHT_CLAIMS_PER_DAY=1, COPYRIGHT_PUBLISHER_CLAIMS_PER_DAY=5)
    def test_publisher_may_file_more_notices_a_day(self):
        self.file_on(self.post)
        with self.assertRaises(services.ClaimError) as cm:
            self.file_on(mk_post(self.owner))
        self.assertEqual(cm.exception.code, "rate_limited")
        make_publisher(self.rita)
        self.file_on(mk_post(self.owner))                          # now allowed

    def test_fast_track_claims_escalate_sooner(self):
        make_publisher(self.rita)
        fast = self.file_on(self.post)
        slow_claimant = self.rita
        TrustedClaimant.objects.filter(user=slow_claimant).update(tier="verified")
        slow = self.file_on(mk_post(self.owner))
        old = timezone.now() - timedelta(hours=13)
        CopyrightClaim.objects.filter(pk__in=[fast.pk, slow.pk]).update(created_at=old)
        self.assertEqual(services.escalate_stale_claims(), 1)      # only the publisher claim (12 h) is overdue
        fast.refresh_from_db(); slow.refresh_from_db()
        self.assertEqual((fast.priority, slow.priority), ("urgent", "normal"))
        self.assertTrue(CopyrightAuditLog.objects.filter(claim=fast, action="auto_escalated",
                                                         note__contains="fast track").exists())
        CopyrightClaim.objects.filter(pk=slow.pk).update(created_at=timezone.now() - timedelta(hours=49))
        self.assertEqual(services.escalate_stale_claims(), 1)      # normal deadline (48 h) unchanged

    def test_publisher_sla_follows_the_setting(self):
        self.assertEqual(claimant_rules.sla_hours("publisher"), 12)
        with override_settings(COPYRIGHT_PUBLISHER_SLA_HOURS=4):
            self.assertEqual(claimant_rules.sla_hours("publisher"), 4)
        self.assertEqual(claimant_rules.sla_hours("trusted"), 48)
