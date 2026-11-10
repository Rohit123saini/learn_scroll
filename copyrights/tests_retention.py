"""Tests for the DPDP Act 2023 data-retention rules (copyrights/retention.py)."""
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from core.models import Notification

from . import notify, retention, services, tasks
from .models import (
    ClaimantEmailVerification, CopyrightAppeal, CopyrightAuditLog, CopyrightClaim, CopyrightCounterNotice,
    CopyrightEvidence, CopyrightStrike,
)
from .tests import Base, mk_post, mk_staff, mk_user

C = CopyrightClaim


def age(claim, days):
    """Make a claim look `days` old (updated_at is auto_now, so it needs a queryset update)."""
    C.objects.filter(pk=claim.pk).update(updated_at=timezone.now() - timedelta(days=days))
    return C.objects.get(pk=claim.pk)


class RetentionTests(Base):
    def setUp(self):
        super().setUp()
        self.l1, self.l2, self.l3 = mk_staff("l1", 1), mk_staff("l2", 2), mk_staff("l3", 3)

    def rejected(self, **kw):
        claim = self.file(**kw)
        services.take_for_review(claim, self.l1)
        services.reject_claim(claim, self.l1, "No proof of ownership")
        return C.objects.get(pk=claim.pk)

    def upheld(self):
        claim = self.file()
        services.uphold_claim(claim, self.l2, "confirmed")
        return C.objects.get(pk=claim.pk)

    def add_file(self, claim, kind="ownership_proof"):
        ev = CopyrightEvidence(claim=claim, kind=kind, original_name="id.pdf", size_bytes=3)
        ev.file.save("id.pdf", ContentFile(b"abc"), save=True)
        return ev

    # ---- what is erased ----
    def test_closed_claim_past_retention_loses_all_claimant_details(self):
        claim = self.rejected()
        ev = self.add_file(claim)
        storage, name = ev.file.storage, ev.file.name
        self.assertTrue(storage.exists(name))
        age(claim, 366)
        with self.captureOnCommitCallbacks(execute=True):               # files are deleted AFTER the commit
            res = retention.purge_personal_data()
        self.assertEqual((res["claims_erased"], res["evidence_files"]), (1, 1))
        claim.refresh_from_db()
        self.assertEqual((claim.claimant_name, claim.claimant_email, claim.organisation, claim.signature),
                         ("[erased]", "erased@invalid.local", "", "[erased]"))
        self.assertIsNotNone(claim.pii_erased_at)
        self.assertEqual(claim.status, "rejected")                      # the decision itself stays
        self.assertFalse(CopyrightEvidence.objects.filter(claim=claim).exists())
        self.assertFalse(storage.exists(name))
        log = CopyrightAuditLog.objects.get(claim=claim, action="pii_erased")
        self.assertNotIn("Rita", log.note)

    def test_staff_alert_text_and_counter_notice_name_are_wiped_too(self):
        claim = self.rejected()
        self.assertTrue(Notification.objects.filter(data__claim_id=str(claim.id), data__role="staff",
                                                    message__contains="Rita Rights").exists())
        CopyrightCounterNotice.objects.create(claim=claim, user=self.owner, explanation="mine", signature="Olly Owner",
                                              restore_after=timezone.now(), status="restored")
        age(claim, 400)
        retention.purge_personal_data()
        self.assertFalse(Notification.objects.filter(data__claim_id=str(claim.id), message__contains="Rita").exists())
        self.assertEqual(CopyrightCounterNotice.objects.get(claim=claim).signature, "[erased]")

    # ---- what is NOT erased ----
    def test_open_and_recent_claims_are_never_touched(self):
        open_claim = age(self.file(), 5000)                              # open: no matter how old
        recent = age(self.rejected(post=mk_post(self.owner)), 100)
        retention.purge_personal_data()
        for c in (open_claim, recent):
            c.refresh_from_db()
            self.assertEqual(c.claimant_name, "Rita Rights")
            self.assertIsNone(c.pii_erased_at)

    def test_upheld_claims_are_kept_three_years(self):
        claim = age(self.upheld(), 400)                                  # > 365 but < 1095
        retention.purge_personal_data()
        claim.refresh_from_db()
        self.assertIsNone(claim.pii_erased_at)
        age(claim, 1100)
        retention.purge_personal_data()
        claim.refresh_from_db()
        self.assertIsNotNone(claim.pii_erased_at)

    def test_legal_hold_and_open_appeal_block_erasure(self):
        held = age(self.rejected(), 400)
        retention.set_retention_hold(held, self.l3, True, "court case")
        retention.purge_personal_data()
        held.refresh_from_db()
        self.assertIsNone(held.pii_erased_at)
        retention.set_retention_hold(held, self.l3, False)              # releasing must not restart the clock
        retention.purge_personal_data()
        held.refresh_from_db()
        self.assertIsNotNone(held.pii_erased_at)

        claim = self.upheld()
        strike = CopyrightStrike.objects.get(claim=claim)
        CopyrightAppeal.objects.create(user=self.owner, kind="strike", strike=strike, message="mistake")
        age(claim, 2000)
        self.assertEqual(retention.purge_personal_data()["claims_erased"], 0)

    def test_only_l3_can_set_a_hold_or_erase_early(self):
        claim = self.rejected()
        with self.assertRaises(PermissionDenied):
            retention.set_retention_hold(claim, self.l2, True)
        with self.assertRaises(PermissionDenied):
            retention.request_erasure(claim, self.l2)

    def test_erasure_on_request_runs_now_but_not_on_open_or_held_claims(self):
        open_claim = self.file()
        with self.assertRaises(services.ClaimError) as cm:
            retention.request_erasure(open_claim, self.l3)
        self.assertEqual(cm.exception.code, "claim_open")
        claim = self.rejected(post=mk_post(self.owner))
        retention.set_retention_hold(claim, self.l3, True)
        with self.assertRaises(services.ClaimError) as cm:
            retention.request_erasure(claim, self.l3)
        self.assertEqual(cm.exception.code, "legal_hold")
        retention.set_retention_hold(claim, self.l3, False)
        retention.request_erasure(claim, self.l3, "claimant asked by e-mail")
        claim.refresh_from_db()
        self.assertIsNotNone(claim.pii_erased_at)
        with self.assertRaises(services.ClaimError) as cm:               # twice is refused, not silently repeated
            retention.request_erasure(claim, self.l3)
        self.assertEqual(cm.exception.code, "already_erased")

    def test_second_run_does_nothing_and_dry_run_changes_nothing(self):
        claim = age(self.rejected(), 400)
        self.assertEqual(retention.purge_personal_data(dry_run=True)["claims_erased"], 1)
        claim.refresh_from_db()
        self.assertIsNone(claim.pii_erased_at)
        self.assertEqual(retention.purge_personal_data()["claims_erased"], 1)
        self.assertEqual(retention.purge_personal_data()["claims_erased"], 0)

    @override_settings(COPYRIGHT_PII_RETENTION_DAYS=0)
    def test_zero_switches_that_category_off(self):
        claim = age(self.rejected(), 5000)
        retention.purge_personal_data()
        claim.refresh_from_db()
        self.assertIsNone(claim.pii_erased_at)

    # ---- the other personal data ----
    def test_old_email_codes_and_decided_appeal_contacts_are_removed(self):
        old = ClaimantEmailVerification.objects.create(user=self.owner, email="o@x.com", code_hash="x",
                                                       expires_at=timezone.now())
        ClaimantEmailVerification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=60))
        fresh = ClaimantEmailVerification.objects.create(user=self.owner, email="o2@x.com", code_hash="y",
                                                         expires_at=timezone.now())
        done = CopyrightAppeal.objects.create(user=self.owner, kind="termination", message="m", contact_email="a@b.com",
                                              status="rejected", decided_at=timezone.now() - timedelta(days=400))
        still_open = CopyrightAppeal.objects.create(user=mk_user("u2"), kind="termination", message="m",
                                                    contact_email="c@d.com")
        res = retention.purge_personal_data()
        self.assertEqual((res["email_codes"], res["appeal_contacts"]), (1, 1))
        self.assertFalse(ClaimantEmailVerification.objects.filter(pk=old.pk).exists())
        self.assertTrue(ClaimantEmailVerification.objects.filter(pk=fresh.pk).exists())
        done.refresh_from_db(); still_open.refresh_from_db()
        self.assertEqual((done.contact_email, still_open.contact_email), ("", "c@d.com"))

    def test_email_code_is_kept_at_least_as_long_as_the_verify_window(self):
        with override_settings(COPYRIGHT_EMAIL_CODE_RETENTION_DAYS=1, COPYRIGHT_EMAIL_VERIFY_DAYS=30):
            self.assertEqual(retention.retention_days()["email_code"], 31)

    # ---- plumbing ----
    def test_erased_placeholder_address_is_never_mailed(self):
        claim = age(self.rejected(), 400)
        retention.purge_personal_data()
        claim.refresh_from_db()
        self.assertTrue(retention.is_erased_email(claim.claimant_email))
        self.assertNotEqual(notify._email_for(self.rita, {"claim_id": str(claim.id), "role": "claimant"}),
                            "erased@invalid.local")

    def test_task_command_and_policy_page(self):
        age(self.rejected(), 400)
        self.assertEqual(tasks.purge_personal_data()["claims_erased"], 1)
        out = StringIO()
        call_command("purge_claimant_data", "--dry-run", stdout=out)
        self.assertIn("would erase", out.getvalue())
        from .policy import policy_context
        self.assertEqual(policy_context()["process"]["pii_retention_days"], 365)
