"""
copyrights/claimant.py - who may file, and how much we trust them (anti-misuse for item "competitor hides my post").

  1. E-MAIL VERIFICATION   the address on the notice must be verified (6-digit code, 30 days).
  2. EVIDENCE              up to 5 files (pdf / image / doc / txt, 10 MB each) as proof of ownership.
  3. TIERS                 publisher: vetted publisher on the FAST TRACK         -> held at once + priority HIGH,
                                      (TrustedClaimant.tier="publisher")           shorter review deadline, higher limits
                           trusted  : staff-vetted publisher (TrustedClaimant)   -> held at once
                           standard : account older than COPYRIGHT_NEW_ACCOUNT_DAYS or with a past
                                      upheld claim                               -> held at once ONLY with evidence attached
                           new      : young account, no history                -> never auto-held; goes to review, 1 notice/day
  4. BRAKE                 at most COPYRIGHT_AUTO_HOLDS_PER_DAY automatic holds per claimant per day. Trusted tiers
                           have a much higher cap (COPYRIGHT_TRUSTED_HOLDS_PER_DAY / _PUBLISHER_HOLDS_PER_DAY, 0 = none)
                           so a hijacked publisher account cannot hide the whole platform.
  5. DEMOTION              a TrustedClaimant row can expire (valid_until) or be switched off; one notice found to be
                           in BAD FAITH within the last year drops "publisher" back to "trusted" (no fast track).
Content that is not held stays visible while a reviewer looks at it (SLA escalation still applies).

Settings: COPYRIGHT_REQUIRE_EMAIL_VERIFICATION (True), COPYRIGHT_EMAIL_VERIFY_DAYS (30),
COPYRIGHT_NEW_ACCOUNT_DAYS (7), COPYRIGHT_NEW_CLAIMANT_PER_DAY (1), COPYRIGHT_AUTO_HOLDS_PER_DAY (3),
COPYRIGHT_EVIDENCE_MAX_MB (10), COPYRIGHT_EVIDENCE_MAX_FILES (5), COPYRIGHT_TRUSTED_HOLDS_PER_DAY (50),
COPYRIGHT_PUBLISHER_HOLDS_PER_DAY (200), COPYRIGHT_PUBLISHER_CLAIMS_PER_DAY (100), COPYRIGHT_PUBLISHER_SLA_HOURS (12).
"""

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import notify
from .models import ClaimantEmailVerification, CopyrightClaim, CopyrightEvidence, TrustedClaimant

EVIDENCE_EXTENSIONS = ("pdf", "jpg", "jpeg", "png", "webp", "doc", "docx", "txt")
CODE_TTL_MINUTES = 15
MAX_CODE_ATTEMPTS = 5
MAX_CODES_PER_HOUR = 5


def _cfg(name, default):
    return getattr(settings, name, default)


def _norm(email):
    return (email or "").strip().lower()


def _digest(code, email):
    return hashlib.sha256(f"{settings.SECRET_KEY}|{_norm(email)}|{code}".encode()).hexdigest()


def _err(code, msg):
    from .services import ClaimError

    return ClaimError(code, msg)


# ------------------------------------------------------------------ e-mail verification
def send_email_code(user, email):
    email = _norm(email)
    if not email:
        raise _err("incomplete", "Enter an e-mail address.")
    since = timezone.now() - timedelta(hours=1)
    if ClaimantEmailVerification.objects.filter(user=user, created_at__gte=since).count() >= MAX_CODES_PER_HOUR:
        raise _err("rate_limited", "Too many codes requested. Try again in an hour.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    ClaimantEmailVerification.objects.create(
        user=user, email=email, code_hash=_digest(code, email),
        expires_at=timezone.now() + timedelta(minutes=CODE_TTL_MINUTES),
    )
    from django.db import transaction

    msg = (f"Your {_cfg('APP_NAME', 'LearnScroll')} copyright notice verification code is {code}. "
           f"It expires in {CODE_TTL_MINUTES} minutes. If you did not ask for it, ignore this e-mail.")
    transaction.on_commit(lambda: notify._email_later_sync(email, "Verify your e-mail for a copyright notice", msg))
    return True


def confirm_email_code(user, email, code):
    email = _norm(email)
    rec = (ClaimantEmailVerification.objects.filter(user=user, email=email, verified_at=None)
           .order_by("-created_at").first())
    if rec is None or rec.expires_at < timezone.now():
        raise _err("code_expired", "That code has expired. Ask for a new one.")
    if rec.attempts >= MAX_CODE_ATTEMPTS:
        raise _err("code_expired", "Too many wrong attempts. Ask for a new code.")
    rec.attempts += 1
    if not hmac.compare_digest(rec.code_hash, _digest((code or "").strip(), email)):
        rec.save(update_fields=["attempts"])
        raise _err("bad_code", "That code is not correct.")
    rec.verified_at = timezone.now()
    rec.save(update_fields=["attempts", "verified_at"])
    return True


def has_verified_email(user, email):
    if not _cfg("COPYRIGHT_REQUIRE_EMAIL_VERIFICATION", True):
        return True
    since = timezone.now() - timedelta(days=_cfg("COPYRIGHT_EMAIL_VERIFY_DAYS", 30))
    return ClaimantEmailVerification.objects.filter(
        user=user, email=_norm(email), verified_at__gte=since).exists()


# ------------------------------------------------------------------ tiers
TRUSTED_TIERS = ("trusted", "publisher")


def is_trusted_tier(tier):
    return tier in TRUSTED_TIERS


def is_fast_track(tier):
    return tier == "publisher"


def trusted_record(user):
    """The user's active, not-expired TrustedClaimant row (or None)."""
    rec = TrustedClaimant.objects.filter(user=user, active=True).first()
    if rec is None:
        return None
    if rec.valid_until is not None and rec.valid_until <= timezone.now():
        return None
    return rec


def has_recent_bad_faith(user):
    since = timezone.now() - timedelta(days=365)
    return CopyrightClaim.objects.filter(claimant=user, bad_faith=True, created_at__gte=since).exists()


def tier_for(user):
    rec = trusted_record(user)
    if rec is not None:
        if rec.tier == TrustedClaimant.Tier.PUBLISHER and not has_recent_bad_faith(user):
            return "publisher"
        return "trusted"
    young = (timezone.now() - user.date_joined) < timedelta(days=_cfg("COPYRIGHT_NEW_ACCOUNT_DAYS", 7))
    proven = CopyrightClaim.objects.filter(claimant=user, status="upheld").exists()
    return "new" if (young and not proven) else "standard"


def claims_per_day(tier):
    """How many notices one claimant may file in 24 h."""
    if tier == "new":
        return _cfg("COPYRIGHT_NEW_CLAIMANT_PER_DAY", 1)
    if tier == "publisher":
        return _cfg("COPYRIGHT_PUBLISHER_CLAIMS_PER_DAY", 100)
    return _cfg("COPYRIGHT_CLAIMS_PER_DAY", 10)


def sla_hours(tier):
    """Hours an untouched notice may wait before it is escalated (fast track = shorter)."""
    if tier == "publisher":
        return _cfg("COPYRIGHT_PUBLISHER_SLA_HOURS", 12)
    return _cfg("COPYRIGHT_SLA_HOURS", 48)


def auto_holds_today(user):
    since = timezone.now() - timedelta(days=1)
    return CopyrightClaim.objects.filter(
        claimant=user, created_at__gte=since, takedown__auto_applied=True).count()


def may_auto_hold(user, tier, has_evidence):
    """Should a complete notice from this claimant hide the content at once?"""
    if not _cfg("COPYRIGHT_AUTO_HOLD", True) or tier == "new":
        return False
    if tier in TRUSTED_TIERS:
        cap = _cfg("COPYRIGHT_PUBLISHER_HOLDS_PER_DAY", 200) if tier == "publisher" \
            else _cfg("COPYRIGHT_TRUSTED_HOLDS_PER_DAY", 50)
        return cap <= 0 or auto_holds_today(user) < cap       # 0 = no cap
    if not has_evidence:
        return False
    return auto_holds_today(user) < _cfg("COPYRIGHT_AUTO_HOLDS_PER_DAY", 3)


# ------------------------------------------------------------------ evidence files
def validate_evidence(files):
    files = list(files or [])
    if len(files) > _cfg("COPYRIGHT_EVIDENCE_MAX_FILES", 5):
        raise _err("incomplete", f"At most {_cfg('COPYRIGHT_EVIDENCE_MAX_FILES', 5)} evidence files.")
    limit = _cfg("COPYRIGHT_EVIDENCE_MAX_MB", 10) * 1024 * 1024
    for f in files:
        ext = os.path.splitext(f.name or "")[1].lstrip(".").lower()
        if ext not in EVIDENCE_EXTENSIONS:
            raise _err("incomplete", f"'{f.name}': allowed types are {', '.join(EVIDENCE_EXTENSIONS)}.")
        if f.size > limit:
            raise _err("incomplete", f"'{f.name}' is larger than {_cfg('COPYRIGHT_EVIDENCE_MAX_MB', 10)} MB.")
        if f.size == 0:
            raise _err("incomplete", f"'{f.name}' is empty.")


def add_evidence(claim, files, user, kind="ownership_proof"):
    """Store claimant-uploaded proof. Returns the created rows."""
    files = list(files or [])
    existing = CopyrightEvidence.objects.filter(claim=claim).exclude(kind="content_snapshot").count()
    if existing + len(files) > _cfg("COPYRIGHT_EVIDENCE_MAX_FILES", 5):
        raise _err("incomplete", f"At most {_cfg('COPYRIGHT_EVIDENCE_MAX_FILES', 5)} evidence files per claim.")
    validate_evidence(files)
    out = []
    for f in files:
        h = hashlib.sha256()
        for chunk in f.chunks():
            h.update(chunk)
        f.seek(0)
        ev = CopyrightEvidence(claim=claim, kind=kind, original_name=(f.name or "")[:200],
                               sha256=h.hexdigest(), size_bytes=f.size, uploaded_by=user)
        ev.file.save(os.path.basename(f.name or "evidence"), f, save=False)
        ev.save()
        out.append(ev)
    return out


def has_proof(claim):
    return CopyrightEvidence.objects.filter(claim=claim).exclude(kind="content_snapshot").exists()
