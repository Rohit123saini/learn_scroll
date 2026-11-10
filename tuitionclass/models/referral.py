# tuitionclass/models/referral.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

import secrets
from django.db import IntegrityError as DjangoIntegrityError
from login.models import User
from django.db import (
    models,
    transaction,
)
from django.utils import timezone


# ---------------------------------------------------------------------------
# 13B. REFERRAL PROGRAM
#
# GAP THIS CLOSES: CoinTransaction.Reason.REFERRAL_BONUS has existed as an
# enum choice since the coin ledger was first written, but nothing anywhere
# ever created a transaction with that reason — there was no referral code,
# no redeem endpoint, no Referral model. This is that missing feature.
#
# Design: no new field needed on User (the `login` app's User model is out
# of scope for this app to migrate) — a referral code is just the user's id
# obfuscated well enough that it isn't a guessable sequential integer in the
# app's UI, decoded back to a user id on redeem. Not cryptographic secrecy
# (a determined person could brute-force it), just enough to stop a plain
# "?ref=124" counter from being trivially incremented/guessed by a casual
# user copy-pasting someone else's link.
# ---------------------------------------------------------------------------
# TASK 12 / 12.4 — UNGUESSABLE CODES. The first version of this feature used
# "R" + hex(user_id * 7919): reversible by anyone with a calculator, so any
# user id could be turned into a valid code (and a sequential scan finds every
# user). Codes are now random strings stored in ReferralCode (below), prefix
# "L", alphabet without look-alikes (no 0/O/1/I/L), 8 chars ~= 8.5e11 values.
# Legacy "R..." codes still DECODE while settings.REFERRAL_ACCEPT_LEGACY_CODES
# is on, so links already shared keep working; no new legacy code is ever
# issued. The public function names/signatures are unchanged on purpose
# (views, Classroom.referral_urls, testseries certificate card all import them).
_LEGACY_REFERRAL_CODE_SALT = 7919
_REFERRAL_CODE_PREFIX = "L"
_REFERRAL_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_REFERRAL_CODE_RANDOM_LEN = 8


def generate_referral_code() -> str:
    """Fresh random code, e.g. 'LK7Q2XM9P'. Uniqueness is enforced by the DB
    (ReferralCode.code unique) — callers retry on IntegrityError."""
    return _REFERRAL_CODE_PREFIX + "".join(
        secrets.choice(_REFERRAL_CODE_ALPHABET) for _ in range(_REFERRAL_CODE_RANDOM_LEN)
    )


def referral_code_for_user(user_id: int) -> str:
    """user id -> that user's permanent random code (created on first call).
    Race-safe: two concurrent first calls end up with the same row."""
    existing = ReferralCode.objects.filter(user_id=user_id).values_list("code", flat=True).first()
    if existing:
        return existing
    for _ in range(5):
        try:
            with transaction.atomic():
                row, _created = ReferralCode.objects.get_or_create(
                    user_id=user_id, defaults={"code": generate_referral_code()}
                )
            return row.code
        except DjangoIntegrityError:
            # Either a code collision (retry with a new code) or we lost a
            # race for this user (their row now exists).
            existing = ReferralCode.objects.filter(user_id=user_id).values_list("code", flat=True).first()
            if existing:
                return existing
    raise RuntimeError("Could not allocate a unique referral code.")


def referral_code_to_user_id(code: str) -> "int | None":
    """code -> user id, or None for anything unknown/malformed (callers treat
    None as 'invalid code'). Does not check the user still exists/is active."""
    from django.conf import settings as django_settings

    code = (code or "").strip().upper()
    if not code:
        return None
    if code.startswith(_REFERRAL_CODE_PREFIX):
        return ReferralCode.objects.filter(code=code).values_list("user_id", flat=True).first()
    if code.startswith("R") and getattr(django_settings, "REFERRAL_ACCEPT_LEGACY_CODES", True):
        try:
            value = int(code[1:], 16)
        except ValueError:
            return None
        if value <= 0 or value % _LEGACY_REFERRAL_CODE_SALT != 0:
            return None
        return value // _LEGACY_REFERRAL_CODE_SALT
    return None


class ReferralCode(models.Model):
    """One permanent random referral code per user (see the note above)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referral_code_row")
    code = models.CharField(max_length=12, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_id}:{self.code}"


class Referral(models.Model):
    """One row per successfully-redeemed referral. `referred` is a
    OneToOne, not a plain FK — a user can be referred (i.e. redeem a
    referral code as the NEW user) at most once ever, which is what
    prevents someone farming REFERRAL_BONUS by redeeming repeatedly.
    There's no matching cap on `referrer` — a single existing user is
    meant to refer many people."""

    referrer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referrals_made")
    referred = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referral_used")
    bonus_amount = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    # T2.7 — set (atomically, by a conditional UPDATE) the moment the
    # cost-free rewards (streak freeze / badges / founding member) for this
    # referral have been handed out, so a retried Celery task can never pay
    # them twice. NULL = not granted yet.
    rewards_granted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.referrer} referred {self.referred}"



class ReferralAttribution(models.Model):
    """TASK 12 / 12.1 — LINK-BASED ATTRIBUTION. "This user arrived through
    that referrer's link, and is that referrer's customer until expires_at."

    * FIRST TOUCH WINS: referee is a OneToOne, and `claim()` refuses to
      reassign a user whose attribution is still active — opening five
      different friends' links never steals credit from the first one.
    * EXPIRY: `expires_at = created_at + REFERRAL_ATTRIBUTION_DAYS`. After it,
      `active_for()` returns None and the user can be claimed again by a new
      link. The window runs from the first click, not from first purchase.
    * It only records WHO sent the user. Whether a purchase actually pays is
      decided at purchase time (feature on, fraud checks, source rules).
    """

    class SourceType(models.TextChoices):
        APP = "app", "App invite"
        TESTSERIES = "testseries", "Test series"
        CLASSROOM = "classroom", "Classroom"
        CERTIFICATE = "certificate", "Certificate share"

    # COMMISSION RULE: only a link to a specific CLASSROOM or TEST SERIES can
    # ever earn commission. Sharing the app itself ("app") or a profile /
    # certificate ("certificate") never pays, so such a row is recorded but
    # ignored by every payout path (see active_for(commissionable_only=True))
    # and is overwritten by a later classroom / test-series link.
    COMMISSION_SOURCES = ("classroom", "testseries")

    referee = models.OneToOneField(User, on_delete=models.CASCADE, related_name="referral_attribution")
    referrer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referral_attributions_made")
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.APP)
    source_id = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    first_purchase_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["referrer", "-created_at"], name="tuitioncl_referrer_9f3a1c_idx")]

    def __str__(self):
        return f"{self.referrer_id} -> {self.referee_id} ({self.source_type})"

    @property
    def is_active(self) -> bool:
        return self.expires_at > timezone.now()

    @classmethod
    def active_for(cls, user, commissionable_only: bool = False) -> "ReferralAttribution | None":
        """`commissionable_only=True` is what every payout path must use: it
        ignores attributions that came from an app / profile / certificate
        share, which never earn commission."""
        if user is None or not getattr(user, "pk", None):
            return None
        qs = cls.objects.filter(referee=user, expires_at__gt=timezone.now())
        if commissionable_only:
            qs = qs.filter(source_type__in=cls.COMMISSION_SOURCES)
        return qs.select_related("referrer").first()

    @classmethod
    def claim(cls, *, referee, referrer, source_type=SourceType.APP, source_id=""):
        """-> (attribution, created). Never raises for the 'already owned'
        case; the caller can tell by `created` / `attribution.referrer_id`."""
        from django.conf import settings as django_settings

        days = int(getattr(django_settings, "REFERRAL_ATTRIBUTION_DAYS", 30))
        now = timezone.now()
        defaults = {
            "referrer": referrer,
            "source_type": source_type,
            "source_id": str(source_id or "")[:64],
            "expires_at": now + timezone.timedelta(days=days),
        }
        try:
            with transaction.atomic():
                row = cls.objects.select_for_update().filter(referee=referee).first()
                if row is None:
                    return cls.objects.create(referee=referee, **defaults), True
                if row.expires_at > now:
                    # First touch wins - EXCEPT that a non-commissionable row
                    # (app / profile share) must not block a real class or
                    # test-series link that arrives later.
                    if (
                        row.source_type not in cls.COMMISSION_SOURCES
                        and defaults["source_type"] in cls.COMMISSION_SOURCES
                        and row.first_purchase_at is None
                    ):
                        for k, v in defaults.items():
                            setattr(row, k, v)
                        row.created_at = now
                        row.save()
                        return row, True
                    return row, False
                # Expired -> this user is up for grabs again.
                for k, v in defaults.items():
                    setattr(row, k, v)
                row.created_at = now
                row.first_purchase_at = None
                row.save()
                return row, True
        except DjangoIntegrityError:
            # Two simultaneous first claims: the loser reads the winner's row.
            return cls.objects.get(referee=referee), False

    @classmethod
    def mark_converted(cls, referee):
        cls.objects.filter(referee=referee, first_purchase_at__isnull=True).update(
            first_purchase_at=timezone.now()
        )


class ReferralCommission(models.Model):
    """TASK 12 / 12.1 — COMMISSION LEDGER. One row per commission payout (or
    per payout that fraud rules BLOCKED, kept for audit). The coins themselves
    always move through the normal wallet paths (CoinLedger for test series,
    CoinTransaction for classes); this table is the referral-specific record
    the referrals screen and ops read.

    `reference` is the idempotency key (unique): releasing the same purchase
    twice, or a retried task, can never pay twice.
      testseries: "testseries_referral:<TestSeriesPurchase.id>"
      classroom:  "passpurchase:<id>:referral:day:<date>"  (same string as the
                  CoinTransaction.reference_id of that day's payout)
    """

    class Kind(models.TextChoices):
        TESTSERIES = "testseries", "Test series"
        CLASSROOM = "classroom", "Classroom"

    class Status(models.TextChoices):
        PAID = "paid", "Paid"
        BLOCKED = "blocked", "Blocked (fraud rule)"

    referrer = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referral_commissions")
    referee = models.ForeignKey(User, on_delete=models.CASCADE, related_name="referral_commissions_generated")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    source_id = models.CharField(max_length=64, blank=True, default="")
    reference = models.CharField(max_length=150, unique=True)
    gross_coins = models.PositiveIntegerField(help_text="What the commission was calculated on.")
    percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_coins = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PAID, db_index=True)
    block_reason = models.CharField(max_length=40, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["referrer", "-created_at"], name="tuitioncl_referrer_2b7e44_idx"),
            models.Index(fields=["referrer", "status", "created_at"], name="tuitioncl_referrer_5c81d0_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.commission_coins}c {self.referrer_id}<-{self.referee_id} [{self.status}]"
