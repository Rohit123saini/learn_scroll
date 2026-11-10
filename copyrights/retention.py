"""
copyrights/retention.py - how long a rights holder's (claimant's) personal data is kept.

DPDP Act 2023, s.8(7): personal data must be erased once the purpose it was collected for is over,
unless a law requires it to be kept. The Act does NOT fix a number of days for this case, so the
periods below are our POLICY (settings.py, env-overridable) - confirm them with your lawyer.

  WHAT IS KEPT / ERASED
  --------------------
  Claim is OPEN (submitted / needs_info / under_review / countered)      never touched
  Claim is CLOSED, rejected / withdrawn / restored                       erased COPYRIGHT_PII_RETENTION_DAYS
                                                                         (365) after the last change
  Claim is UPHELD or found in BAD FAITH                                  erased COPYRIGHT_PII_RETENTION_UPHELD_DAYS
                                                                         (1095 = 3 years: appeals, strike history,
                                                                         limitation period for a civil suit)
  Verification codes (e-mail + code hash)                                deleted COPYRIGHT_EMAIL_CODE_RETENTION_DAYS
                                                                         (45) after they were created (never earlier
                                                                         than COPYRIGHT_EMAIL_VERIFY_DAYS + 1)
  Any claim with retention_hold=True (L3 legal hold) or an OPEN appeal   never touched until released / decided

  "Erased" means: claimant_name -> "[erased]", claimant_email -> "erased@invalid.local", organisation -> "",
  signature -> "[erased]", and every CopyrightEvidence row of the claim (proof the claimant uploaded + the
  saved copy of the reported content) is deleted together with its FILE in storage. The text of the staff alert
  that named the claimant (core.Notification) is blanked, and the content owner's typed name on a counter-notice
  of that claim becomes "[erased]". Decided appeals lose their contact e-mail after the same COPYRIGHT_PII_RETENTION_DAYS.
  A claimant can ask for earlier erasure: an L3 admin uses the "Erase claimant data now" action (admin / dashboard)
  = request_erasure().

  WHAT STAYS (no personal data of the claimant, or needed by law / for abuse prevention)
  ------------------------------------------------------------------------------------
  the claim row (status, dates, target, decision, bad_faith flag), strikes, takedown rows, the audit log,
  BlockedMediaHash / MediaFingerprint (hashes of removed files only) and the claimant FK (account link; it is set
  to NULL by itself when that account is deleted). Transparency numbers are counts only.

Every erasure writes ONE audit row (action "pii_erased", no personal data in the note). The daily Celery task
copyrights.purge_personal_data (copyrights/tasks.py) calls purge_personal_data(); it is idempotent and works in
batches, so a backlog is cleared over a few days. Manual run / preview:
    python manage.py purge_claimant_data --dry-run
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import permissions as perms
from .models import (
    ClaimantEmailVerification, CopyrightAppeal, CopyrightClaim, CopyrightCounterNotice, CopyrightEvidence,
)

logger = logging.getLogger(__name__)

C = CopyrightClaim

ERASED_NAME = "[erased]"
ERASED_EMAIL = "erased@invalid.local"


def _days(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def retention_days():
    """The numbers in force (0 or less = that category is never erased)."""
    return {
        "claim": _days("COPYRIGHT_PII_RETENTION_DAYS", 365),
        "upheld": _days("COPYRIGHT_PII_RETENTION_UPHELD_DAYS", 1095),
        # a code must outlive the e-mail verification window, else a verified address would "unverify" early
        "email_code": max(_days("COPYRIGHT_EMAIL_CODE_RETENTION_DAYS", 45),
                          _days("COPYRIGHT_EMAIL_VERIFY_DAYS", 30) + 1),
    }


def is_erased_email(email):
    return (email or "").strip().lower() == ERASED_EMAIL


# --------------------------------------------------------------------------
# which claims are due
# --------------------------------------------------------------------------
def due_claims(now=None):
    """Closed claims whose retention period has passed (the clock = the last change of the closed claim)."""
    now = now or timezone.now()
    d = retention_days()
    base = (C.objects.filter(pii_erased_at__isnull=True, retention_hold=False)
            .exclude(status__in=C.OPEN_STATUSES)
            .exclude(source=C.Source.AUTO))          # automatic matches carry no claimant data
    long_term = Q(status=C.Status.UPHELD) | Q(bad_faith=True)
    cond = None
    if d["claim"] > 0:
        cond = ~long_term & Q(updated_at__lte=now - timedelta(days=d["claim"]))
    if d["upheld"] > 0:
        upheld = long_term & Q(updated_at__lte=now - timedelta(days=d["upheld"]))
        cond = upheld if cond is None else (cond | upheld)
    if cond is None:
        return base.none()
    return base.filter(cond)


# --------------------------------------------------------------------------
# erase one claim
# --------------------------------------------------------------------------
def _delete_files(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception("retention: could not delete stored evidence file")


def erase_claim_personal_data(claim, *, reason="retention", actor=None):
    """Wipe the claimant's personal data of ONE closed claim.

    -> number of evidence files deleted (0 or more) when the claim was erased, None when it was skipped
    (still open, legal hold, open appeal, already erased). Safe to call twice. Can also be used for an
    early erasure request from the claimant - the same safety rules apply.
    """
    from . import services  # late: services imports a lot

    with transaction.atomic():
        claim = C.objects.select_for_update().get(pk=claim.pk)
        if claim.pii_erased_at or claim.retention_hold or claim.status in C.OPEN_STATUSES:
            return None
        if CopyrightAppeal.objects.filter(strike__claim=claim, status=CopyrightAppeal.Status.OPEN).exists():
            return None

        evidence = list(CopyrightEvidence.objects.filter(claim=claim))
        files = [(ev.file.storage, ev.file.name) for ev in evidence if ev.file and ev.file.name]
        CopyrightEvidence.objects.filter(pk__in=[ev.pk for ev in evidence]).delete()

        # the new-claim alert that went to staff carried the claimant's name in its text
        from core.models import Notification
        Notification.objects.filter(data__claim_id=str(claim.id), data__role="staff").update(message="")
        # the content owner's typed name on their counter-notice is personal data too (the explanation stays: it is the case)
        CopyrightCounterNotice.objects.filter(claim=claim).update(signature=ERASED_NAME)

        claim.claimant_name = ERASED_NAME
        claim.claimant_email = ERASED_EMAIL
        claim.organisation = ""
        claim.signature = ERASED_NAME
        claim.pii_erased_at = timezone.now()
        claim.save(update_fields=["claimant_name", "claimant_email", "organisation", "signature", "pii_erased_at"])

        services._log(claim, "pii_erased", actor=actor, label="retention" if actor is None else None,
                      note=f"{reason}; status={claim.status}; evidence_rows={len(evidence)}")
        # files go only after the DB change is really committed (a rollback must not leave rows without files)
        transaction.on_commit(lambda: _delete_files(files))
    return len(files)


def purge_email_codes(now=None, batch=1000):
    """Old verification codes (they hold an e-mail address). -> rows deleted."""
    now = now or timezone.now()
    cutoff = now - timedelta(days=retention_days()["email_code"])
    ids = list(ClaimantEmailVerification.objects.filter(created_at__lte=cutoff)
               .values_list("pk", flat=True)[:batch])
    if not ids:
        return 0
    return ClaimantEmailVerification.objects.filter(pk__in=ids).delete()[0]


def purge_appeal_contacts(now=None, batch=1000, dry_run=False):
    """Decided appeals: the contact e-mail is only needed to send the answer. Cleared COPYRIGHT_PII_RETENTION_DAYS
    after the decision. -> rows cleared."""
    now = now or timezone.now()
    days = retention_days()["claim"]
    if days <= 0:
        return 0
    qs = (CopyrightAppeal.objects.exclude(status=CopyrightAppeal.Status.OPEN).exclude(contact_email="")
          .filter(decided_at__lte=now - timedelta(days=days)))
    if dry_run:
        return qs.count()
    ids = list(qs.values_list("pk", flat=True)[:batch])
    return CopyrightAppeal.objects.filter(pk__in=ids).update(contact_email="") if ids else 0


def purge_personal_data(now=None, batch=None, dry_run=False):
    """Daily job. -> {"claims_erased", "evidence_files", "claims_skipped", "email_codes", "appeal_contacts"}
    (counts only)."""
    now = now or timezone.now()
    batch = batch or _days("COPYRIGHT_PII_PURGE_BATCH", 500)
    out = {"claims_erased": 0, "evidence_files": 0, "claims_skipped": 0, "email_codes": 0, "appeal_contacts": 0}
    ids = list(due_claims(now).order_by("updated_at").values_list("pk", flat=True)[:batch])
    if dry_run:
        out["claims_erased"] = len(ids)
        out["email_codes"] = ClaimantEmailVerification.objects.filter(
            created_at__lte=now - timedelta(days=retention_days()["email_code"])).count()
        out["appeal_contacts"] = purge_appeal_contacts(now, dry_run=True)
        return out
    for pk in ids:
        try:
            files = erase_claim_personal_data(C(pk=pk))
        except Exception:
            logger.exception("retention: erasing claim %s failed", pk)   # one bad row must not stop the rest
            out["claims_skipped"] += 1
            continue
        if files is None:
            out["claims_skipped"] += 1
        else:
            out["claims_erased"] += 1
            out["evidence_files"] += files
    out["email_codes"] = purge_email_codes(now)
    out["appeal_contacts"] = purge_appeal_contacts(now)
    return out


# --------------------------------------------------------------------------
# early erasure on request (DPDP s.12): the claimant asks, an L3 admin does it
# --------------------------------------------------------------------------
def request_erasure(claim, actor, note=""):
    """Erase now instead of waiting for the retention period. L3 only. Refused (with the reason) while the claim is
    open, under legal hold, has an open appeal, or is already erased. -> number of evidence files deleted."""
    from . import services

    perms.require_level(actor, perms.L3, "erasing a claimant's personal data")
    claim = C.objects.get(pk=claim.pk)
    if claim.pii_erased_at:
        raise services.ClaimError("already_erased", "The claimant's details were already erased.")
    if claim.status in C.OPEN_STATUSES:
        raise services.ClaimError("claim_open", "The claim is still open - decide it first.")
    if claim.retention_hold:
        raise services.ClaimError("legal_hold", "A legal hold is on - release it first.")
    files = erase_claim_personal_data(claim, reason=("erasure request. " + note).strip(), actor=actor)
    if files is None:
        raise services.ClaimError("cannot_erase", "An appeal about this claim is still open.")
    return files


# --------------------------------------------------------------------------
# legal hold (L3): keep everything of a claim that is part of a dispute
# --------------------------------------------------------------------------
def set_retention_hold(claim, actor, hold, note=""):
    from . import services

    perms.require_level(actor, perms.L3, "a legal hold on claimant data")
    with transaction.atomic():
        claim = C.objects.select_for_update().get(pk=claim.pk)
        if hold and claim.pii_erased_at:
            raise services.ClaimError("already_erased", "The claimant's details were already erased.")
        if claim.retention_hold == bool(hold):
            return claim
        claim.retention_hold = bool(hold)
        claim.save(update_fields=["retention_hold"])      # NOT updated_at: a hold must not restart the retention clock
        services._log(claim, "retention_hold_on" if hold else "retention_hold_off", actor=actor, note=note)
    return claim
