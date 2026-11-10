"""
copyrights/models.py - copyright (IP) complaints, takedowns, counter-notices
and the repeat-infringer ("strikes") policy.

Flow (all state changes go through copyrights/services.py, never by hand):

  rights holder files a CopyrightClaim (formal notice: what work, which
  content, good-faith + accuracy statements, signature)
      -> complete notice  => content is put on HOLD at once (hidden, reversible)
      -> staff review     => UPHELD (content removed + strike) or REJECTED (content back)
  content owner may file a CopyrightCounterNotice
      -> if the claimant does not report court action within
         COPYRIGHT_COUNTER_WAIT_DAYS the content is restored automatically
  strikes expire after COPYRIGHT_STRIKE_DAYS; 2 active strikes = uploads
  blocked, 3 = termination review (only a level-3 admin can end an account).

Staff levels (see copyrights/permissions.py):
  L1 reviewer  L2 senior reviewer  L3 copyright admin
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class CopyrightClaim(models.Model):
    class TargetType(models.TextChoices):
        POST = "post", "Post / reel"
        STORY = "story", "Story"
        # --- study material (see copyrights/targets.py for what "hide" means for each) ---
        CLASS_MATERIAL = "class_material", "Class material (notes / PDF / slides)"
        CLASS_RECORDING = "class_recording", "Live-class recording"
        TEST_SERIES = "test_series", "Test series (incl. its questions)"

    class Status(models.TextChoices):
        SUBMITTED = "submitted", "Submitted"
        NEEDS_INFO = "needs_info", "Waiting for claimant"
        UNDER_REVIEW = "under_review", "Under review"
        UPHELD = "upheld", "Upheld - content removed"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn by claimant"
        COUNTERED = "countered", "Counter-notice filed"
        RESTORED = "restored", "Content restored"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    class Source(models.TextChoices):
        USER = "user", "Filed by a rights holder"
        REPORT = "report", "Raised from user reports"
        STAFF = "staff", "Filed by staff"
        AUTO = "auto", "Automatic match (re-upload of removed content)"

    # States in which a claim is still being worked on (content may be on hold).
    OPEN_STATUSES = ("submitted", "needs_info", "under_review", "countered")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    claimant = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="copyright_claims_filed",
    )
    claimant_name = models.CharField(max_length=120)
    claimant_email = models.EmailField()
    organisation = models.CharField(max_length=120, blank=True, default="")
    # True = owner of the work; False = authorised agent acting for the owner.
    is_rights_owner = models.BooleanField(default=True)

    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    target_id = models.CharField(max_length=64)
    # The ACCOUNT behind the content - so everything against one person is in one place.
    content_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="copyright_claims_received",
    )
    # Short text copy of what was claimed (caption / title) - the content itself
    # may be removed or expire (stories), the claim record must stay readable.
    content_snapshot = models.CharField(max_length=300, blank=True, default="")
    # Facts about the content frozen at filing time (owner, created_at, files + their SHA-256).
    # The files themselves are copied into CopyrightEvidence(kind="content_snapshot").
    content_meta = models.JSONField(default=dict, blank=True)

    work_description = models.TextField(max_length=2000)
    original_work_url = models.URLField(max_length=500, blank=True, default="")
    infringement_details = models.TextField(max_length=2000, blank=True, default="")

    # Sworn statements + typed signature = what makes this a valid formal notice.
    good_faith_statement = models.BooleanField(default=False)
    accuracy_statement = models.BooleanField(default=False)
    signature = models.CharField(max_length=120)

    status = models.CharField(max_length=14, choices=Status.choices, default=Status.SUBMITTED, db_index=True)
    priority = models.CharField(max_length=6, choices=Priority.choices, default=Priority.NORMAL, db_index=True)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.USER)
    # "new" / "standard" / "trusted" / "publisher" - how much we trusted the claimant when the notice
    # arrived. "trusted" = staff-vetted (TrustedClaimant tier "verified"); "publisher" = vetted publisher
    # on the fast track (TrustedClaimant tier "publisher"). See claimant.py.
    claimant_tier = models.CharField(max_length=10, blank=True, default="")
    # 1/2/3 = lowest staff level allowed to decide this claim (set by automation).
    required_level = models.PositiveSmallIntegerField(default=1)

    duplicate_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="duplicates",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="copyright_claims_reviewed",
    )
    decision_note = models.TextField(blank=True, default="")
    # Set by a level-2+ reviewer when rejecting: the notice was knowingly false.
    bad_faith = models.BooleanField(default=False, db_index=True)
    needs_info_message = models.CharField(max_length=500, blank=True, default="")
    info_requested_at = models.DateTimeField(null=True, blank=True)

    escalated_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    # DPDP Act 2023 data retention (copyrights/retention.py): once a CLOSED claim is old enough the
    # claimant's personal details are wiped (the claim row itself stays - strikes / audit need it).
    # retention_hold = L3 legal hold (litigation, court order): nothing is erased while it is on.
    retention_hold = models.BooleanField(default=False, db_index=True)
    pii_erased_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"], name="cr_claim_status_idx"),
            models.Index(fields=["target_type", "target_id"], name="cr_claim_target_idx"),
            models.Index(fields=["claimant", "-created_at"], name="cr_claim_claimant_idx"),
        ]
        permissions = [
            ("review_claim", "L1: can review claims (reject, ask for info, notes)"),
            ("uphold_claim", "L2: can uphold claims, remove content, issue strikes, restore content"),
            ("revoke_strike", "L3: can revoke strikes and reverse decisions"),
            ("terminate_account", "L3: can terminate / reinstate accounts for repeat infringement"),
            ("handle_legal_order", "L3: can comply with / reject / reverse court and government orders"),
        ]

    def __str__(self):
        return f"[{self.status}] {self.target_type}:{self.target_id} by {self.claimant_name}"

    @property
    def is_open(self):
        return self.status in self.OPEN_STATUSES


class CopyrightTakedown(models.Model):
    """What actually happened to the content for one claim - and how to undo it."""

    class State(models.TextChoices):
        HELD = "held", "On hold (temporary)"
        REMOVED = "removed", "Removed"
        RESTORED = "restored", "Restored"

    claim = models.OneToOneField(CopyrightClaim, on_delete=models.CASCADE, related_name="takedown")
    target_type = models.CharField(max_length=20)
    target_id = models.CharField(max_length=64)
    state = models.CharField(max_length=10, choices=State.choices, default=State.HELD, db_index=True)
    # {"moderation_status": "approved"} / {"is_deleted": false, ...} - exact undo.
    previous_state = models.JSONField(default=dict, blank=True)
    auto_applied = models.BooleanField(default=False)
    applied_at = models.DateTimeField(default=timezone.now)
    restored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["target_type", "target_id", "state"], name="cr_takedown_target_idx")]

    def __str__(self):
        return f"{self.state} {self.target_type}:{self.target_id}"


class CopyrightCounterNotice(models.Model):
    """The content owner disputes a claim. Content comes back automatically
    after `restore_after` unless the claimant reports court action first."""

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting period running"
        COURT_ACTION = "court_action", "Claimant reported court action"
        RESTORED = "restored", "Restored"
        REJECTED = "rejected", "Rejected by staff"

    claim = models.OneToOneField(CopyrightClaim, on_delete=models.CASCADE, related_name="counter_notice")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="copyright_counter_notices",
    )
    explanation = models.TextField(max_length=2000)
    good_faith_statement = models.BooleanField(default=False)  # removed by mistake / misidentification
    jurisdiction_consent = models.BooleanField(default=False)
    signature = models.CharField(max_length=120)
    status = models.CharField(max_length=14, choices=Status.choices, default=Status.WAITING, db_index=True)
    filed_at = models.DateTimeField(auto_now_add=True)
    restore_after = models.DateTimeField(db_index=True)
    court_action_note = models.TextField(max_length=1000, blank=True, default="")
    court_action_reported_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"counter-notice {self.claim_id} ({self.status})"


class CopyrightStrike(models.Model):
    """One strike per upheld claim. Expires on its own; only L3 can revoke early."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="copyright_strikes")
    claim = models.OneToOneField(CopyrightClaim, on_delete=models.CASCADE, related_name="strike")
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    revoke_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["user", "revoked_at", "expires_at"], name="cr_strike_user_idx")]

    @property
    def is_active(self):
        return self.revoked_at is None and self.expires_at > timezone.now()

    def __str__(self):
        return f"strike {self.user_id} ({'active' if self.is_active else 'inactive'})"


class CopyrightStanding(models.Model):
    """Cached per-user copyright standing, rebuilt by services.recompute_standing()."""

    class Level(models.TextChoices):
        GOOD = "good", "Good standing"
        WARNING = "warning", "Warning (1 strike)"
        RESTRICTED = "restricted", "Uploads blocked (2 strikes)"
        REVIEW = "review", "Termination review (3+ strikes)"
        TERMINATED = "terminated", "Terminated"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="copyright_standing")
    active_strikes = models.PositiveSmallIntegerField(default=0)
    level = models.CharField(max_length=10, choices=Level.choices, default=Level.GOOD, db_index=True)
    terminated_at = models.DateTimeField(null=True, blank=True)
    terminated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "copyright standings"

    @property
    def uploads_blocked(self):
        return self.level in (self.Level.RESTRICTED, self.Level.REVIEW, self.Level.TERMINATED)

    def __str__(self):
        return f"{self.user_id}: {self.level} ({self.active_strikes})"


class CopyrightAuditLog(models.Model):
    """Append-only trail of every staff / automation decision."""

    claim = models.ForeignKey(CopyrightClaim, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )  # the account the action was about
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )  # NULL = automation / the claimant or owner themselves (see actor_label)
    actor_label = models.CharField(max_length=40, default="system")
    legal_order = models.ForeignKey(
        "LegalOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit",
    )
    action = models.CharField(max_length=40, db_index=True)
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor_label}: {self.action}"


class LegalOrder(models.Model):
    """A court order or a government / authority notice to remove content.

    Different from a private copyright claim: no counter-notice, no strike, no
    auto-restore - it stays down until a level-3 admin reverses it (for example
    because the court set the order aside). Rules 2021 give ~36 hours from
    receipt, so every order has a deadline (settings.LEGAL_ORDER_SLA_HOURS) and an
    hourly automation warns L3 staff before it is missed.
    Staff: L2+ can REGISTER an order, only L3 can comply / reject / reverse.
    """

    class OrderType(models.TextChoices):
        COURT = "court", "Court order"
        GOVERNMENT = "government", "Government / authority notice"
        OTHER = "other", "Other legal request"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        IN_REVIEW = "in_review", "In legal review"
        COMPLIED = "complied", "Complied - content removed"
        REJECTED = "rejected", "Rejected / challenged"
        REVERSED = "reversed", "Reversed - content restored"

    reference = models.CharField(max_length=120, help_text="Case / notice number as written on the order")
    order_type = models.CharField(max_length=12, choices=OrderType.choices, default=OrderType.COURT)
    authority = models.CharField(max_length=200, help_text="Court / ministry / officer that issued it")
    received_via = models.CharField(max_length=60, blank=True, default="", help_text="email, post, portal...")
    summary = models.TextField(max_length=2000, help_text="What the order requires, in plain words")
    document = models.FileField(upload_to="legal_orders/%Y/%m/", blank=True, null=True)
    received_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    # Some orders forbid telling the user. Default is to notify (transparency).
    notify_owner = models.BooleanField(default=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    decision_note = models.TextField(blank=True, default="")
    decided_at = models.DateTimeField(null=True, blank=True)
    sla_warned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_at"]

    def __str__(self):
        return f"{self.order_type} {self.reference} ({self.status})"

    @property
    def is_open(self):
        return self.status in (self.Status.RECEIVED, self.Status.IN_REVIEW)


class LegalOrderItem(models.Model):
    """One piece of content named in an order."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        REMOVED = "removed", "Removed"
        RESTORED = "restored", "Restored"
        MISSING = "missing", "Content not found"

    order = models.ForeignKey(LegalOrder, on_delete=models.CASCADE, related_name="items")
    target_type = models.CharField(max_length=20, choices=CopyrightClaim.TargetType.choices)
    target_id = models.CharField(max_length=64)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    previous_state = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["order", "target_type", "target_id"], name="cr_order_item_uniq")]
        indexes = [models.Index(fields=["target_type", "target_id", "state"], name="cr_orderitem_target_idx")]

    def __str__(self):
        return f"{self.target_type}:{self.target_id} ({self.state})"


class TargetHold(models.Model):
    """ONE row per piece of content that is currently hidden by us (copyright hold / removal or a
    legal order). It is the single source of truth for "how do I put this back": the state the content
    had BEFORE the first hold. copyrights/targets.py:refresh() recomputes the visible state from the
    active takedowns + legal items, so overlapping claims and orders can never un-hide each other."""

    target_type = models.CharField(max_length=20)
    target_id = models.CharField(max_length=64)
    previous_state = models.JSONField(default=dict, blank=True)
    level = models.CharField(max_length=20, blank=True, default="")  # strongest active hold
    media_manifest = models.JSONField(default=list, blank=True)  # [{"name", "quarantine"}] moved away
    media_quarantined = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["target_type", "target_id"], name="cr_hold_target_uniq")]

    def __str__(self):
        return f"{self.target_type}:{self.target_id} ({self.level})"


def _evidence_upload_to(instance, filename):
    import os

    base = os.path.basename(filename)[:120]
    owner = instance.claim_id or f"order-{instance.legal_order_id}"
    return f"copyright_evidence/{owner}/{uuid.uuid4().hex[:8]}_{base}"


class CopyrightEvidence(models.Model):
    """Files that back a claim: what the claimant uploaded as proof, and the copy of the reported
    content we made when the notice arrived (so deleting the post cannot erase the evidence)."""

    class Kind(models.TextChoices):
        OWNERSHIP_PROOF = "ownership_proof", "Proof of ownership"
        ORIGINAL_WORK = "original_work", "Original work"
        OTHER = "other", "Other"
        CONTENT_SNAPSHOT = "content_snapshot", "Copy of the reported content"

    claim = models.ForeignKey(CopyrightClaim, null=True, blank=True, on_delete=models.CASCADE, related_name="evidence")
    legal_order = models.ForeignKey("LegalOrder", null=True, blank=True, on_delete=models.CASCADE, related_name="evidence")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    file = models.FileField(upload_to=_evidence_upload_to, max_length=300)
    original_name = models.CharField(max_length=200, blank=True, default="")
    sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    size_bytes = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.kind} {self.original_name}"


class ClaimantEmailVerification(models.Model):
    """A 6-digit code mailed to the address the claimant gives on the notice. A notice can only be
    filed with an address verified in the last COPYRIGHT_EMAIL_VERIFY_DAYS days."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    email = models.EmailField()
    code_hash = models.CharField(max_length=64)
    attempts = models.PositiveSmallIntegerField(default=0)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["user", "email", "-created_at"], name="cr_emailver_idx")]


class TrustedClaimant(models.Model):
    """Publishers / institutes staff have vetted: their complete notices are held at once even
    without attached proof. Managed by L2+ in Django admin.

    Tiers (claimant.py decides what each one gets):
      verified   staff-vetted; complete notice -> content held at once (the original "trusted" behaviour)
      publisher  verified publisher, FAST TRACK: the same hold, plus priority HIGH in the review queue, a
                 shorter review deadline (COPYRIGHT_PUBLISHER_SLA_HOURS) and higher daily limits.
    Fast track never decides anything: a reviewer still upholds / rejects, the owner can still file a
    counter-notice, and a claim against a high-reach / verified / struck account still needs L2.
    `valid_until` forces a re-vetting: after it passes the account is treated as an ordinary claimant."""

    class Tier(models.TextChoices):
        VERIFIED = "verified", "Verified (held at once)"
        PUBLISHER = "publisher", "Verified publisher (fast track)"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="copyright_trusted")
    organisation = models.CharField(max_length=120, blank=True, default="")
    tier = models.CharField(max_length=10, choices=Tier.choices, default=Tier.VERIFIED)
    valid_until = models.DateTimeField(
        null=True, blank=True,
        help_text="Empty = no expiry. After this moment the account is an ordinary claimant again (re-vet it).",
    )
    note = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.tier}: {self.user_id} {self.organisation}"


class BlockedMediaHash(models.Model):
    """SHA-256 of a file that was REMOVED for copyright / by a legal order. A new upload with the
    same bytes is held automatically (copyrights/signals.py) - catches plain re-uploads and copies.
    Exact match only (a re-encoded or cropped copy has a different hash)."""

    sha256 = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True, db_index=True)
    claim = models.ForeignKey(CopyrightClaim, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    legal_order = models.ForeignKey("LegalOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sha256[:12]}... ({'active' if self.active else 'off'})"


class MediaFingerprint(models.Model):
    """Perceptual fingerprint of a file that was REMOVED (upheld claim / legal order) - catches the
    re-encoded / resized / mirrored / re-exported copy that BlockedMediaHash (exact bytes) cannot.
    Built and compared by copyrights/fingerprint.py. `active=False` = stop matching (content restored
    or a licence was proven)."""

    kind = models.CharField(max_length=10, db_index=True)          # image / video / audio / pdf
    algo = models.CharField(max_length=12, db_index=True)          # dhash64 / vframes64 / chroma / minhash64 / pdfimg64
    data = models.JSONField(default=dict, blank=True)
    source_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source_name = models.CharField(max_length=300, blank=True, default="")
    claim = models.ForeignKey(CopyrightClaim, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    legal_order = models.ForeignKey("LegalOrder", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind}/{self.algo} {self.source_sha256[:10]} ({'active' if self.active else 'off'})"


class FingerprintBand(models.Model):
    """LSH index row: a candidate lookup key of a MediaFingerprint (so a new upload is compared with a
    handful of fingerprints, not with all of them)."""

    fingerprint = models.ForeignKey(MediaFingerprint, on_delete=models.CASCADE, related_name="bands")
    key = models.CharField(max_length=32, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["key", "fingerprint"], name="cr_fpband_key_idx")]

    def __str__(self):
        return self.key


class CopyrightAppeal(models.Model):
    """The account owner contests a PENALTY (audit item 21) - separate from a counter-notice,
    which contests the CLAIM and brings the content back.

      kind = strike       "this strike should not count" (claim upheld wrongly / mistaken identity)
      kind = termination  "my account should not have been terminated"

    A terminated account can't log in, so a termination appeal is also accepted from outside
    the app (copyrights/views.TerminationAppealView, rate-limited, identity checked by staff via
    the e-mail on file). Only level-3 staff decide; the person who issued the strike / terminated
    the account can't decide the appeal against their own decision (four-eyes).
    """

    class Kind(models.TextChoices):
        STRIKE = "strike", "Strike"
        TERMINATION = "termination", "Account termination"

    class Status(models.TextChoices):
        OPEN = "open", "Waiting for review"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="copyright_appeals")
    kind = models.CharField(max_length=12, choices=Kind.choices, db_index=True)
    strike = models.ForeignKey(CopyrightStrike, null=True, blank=True, on_delete=models.CASCADE, related_name="appeals")
    message = models.TextField(max_length=2000)
    contact_email = models.EmailField(blank=True, default="")  # termination appeals: where the answer goes
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN, db_index=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    decision_note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # one open termination appeal per account ...
            models.UniqueConstraint(fields=["user"], condition=models.Q(status="open", kind="termination"),
                                    name="cr_one_open_termination_appeal"),
            # ... and one open appeal per strike (NULL strike = termination row, never conflicts)
            models.UniqueConstraint(fields=["strike"], condition=models.Q(status="open"),
                                    name="cr_one_open_strike_appeal"),
        ]

    def __str__(self):
        return f"{self.kind} appeal by {self.user_id} ({self.status})"
