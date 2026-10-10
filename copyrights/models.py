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

    target_type = models.CharField(max_length=10, choices=TargetType.choices)
    target_id = models.CharField(max_length=64)
    # The ACCOUNT behind the content - so everything against one person is in one place.
    content_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="copyright_claims_received",
    )
    # Short text copy of what was claimed (caption / title) - the content itself
    # may be removed or expire (stories), the claim record must stay readable.
    content_snapshot = models.CharField(max_length=300, blank=True, default="")

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
    target_type = models.CharField(max_length=10)
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
    action = models.CharField(max_length=40, db_index=True)
    note = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.actor_label}: {self.action}"
