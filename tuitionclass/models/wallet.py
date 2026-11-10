# tuitionclass/models/wallet.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
)
from login.models import User
from django.db import models
from django.utils import timezone
from .classroom import Classroom


# ---------------------------------------------------------------------------
# 12. COUPONS (discounts on ClassPass purchases)
# ---------------------------------------------------------------------------
class Coupon(models.Model):
    classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name="coupons", null=True, blank=True
    )  # null = usable across all of this teacher's classrooms (set via created_by)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coupons_created")

    code = models.CharField(max_length=30, unique=True)
    discount_percent = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    discount_amount = models.DecimalField(
        max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )

    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField()
    max_uses = models.PositiveIntegerField(null=True, blank=True)  # null = unlimited
    used_count = models.PositiveIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

    def is_valid(self) -> bool:
        now = timezone.now()
        if not self.is_active or not (self.valid_from <= now <= self.valid_until):
            return False
        if self.max_uses and self.used_count >= self.max_uses:
            return False
        return True


# ---------------------------------------------------------------------------
# 13. COIN WALLET TRANSACTIONS (ledger for User.coin balance)
# ---------------------------------------------------------------------------
class CoinTransaction(models.Model):
    class TxnType(models.TextChoices):
        CREDIT = "credit", "Credit"
        DEBIT = "debit", "Debit"

    class Reason(models.TextChoices):
        PASS_PURCHASE = "pass_purchase", "Pass Purchase"
        CLASS_EARNING = "class_earning", "Class Earning"
        REFERRAL_BONUS = "referral_bonus", "Referral Bonus"
        # FEATURE (refer & earn — class-level, see Classroom.referral_enabled
        # above): distinct from REFERRAL_BONUS, which is a flat one-time
        # payout for bringing a brand-new USER onto the platform (see the
        # Referral model, 13B below). This one is the ongoing, per-day cut
        # of a specific classroom's escrow release — see
        # PassPurchase.charge_for_session.
        CLASS_REFERRAL_COMMISSION = "class_referral_commission", "Class Referral Commission"
        # NEW (task 65 — classroom refer & earn, student side): one-time,
        # platform-funded bonus credited to the STUDENT when they join a
        # classroom via someone else's referral link (Classroom.referral_urls)
        # and referral_enabled=True at accept-time. Distinct from
        # CLASS_REFERRAL_COMMISSION above, which pays the REFERRER an
        # ongoing per-day cut out of the teacher's own share — this one is a
        # flat top-up funded by the platform (see
        # settings.CLASSROOM_REFERRAL_JOIN_BONUS_COINS), mirroring how
        # REFERRAL_BONUS already rewards both sides of a signup referral.
        # Awarded once per PassPurchase, from _charge_and_create_purchase
        # (views.py) at the same moment referred_by is set.
        CLASS_REFERRAL_JOIN_BONUS = "class_referral_join_bonus", "Class Referral Join Bonus"
        REFUND = "refund", "Refund"
        # NEW (Pass 14 — gifting a pass): distinct from PASS_PURCHASE/REFUND
        # so a wallet-history screen can tell "I bought this for myself" and
        # "I bought this for someone else" apart, and so a recipient's
        # claimed gift never looks like a REFUND (they never paid anything).
        GIFT_SENT = "gift_sent", "Pass Gifted To Someone"
        GIFT_CANCELLED_REFUND = "gift_cancelled_refund", "Unclaimed Gift Refunded"
        # NEW (Pass 15 — auto-renew passes): distinct from PASS_PURCHASE so
        # a wallet-history screen can tell "I bought this" apart from "this
        # renewed itself" — see PassPurchase.renew() below.
        PASS_AUTO_RENEWED = "pass_auto_renewed", "Pass Auto-Renewed"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"
        TOPUP = "topup", "Wallet Top-up"
        WITHDRAWAL = "withdrawal", "Withdrawal"  # debited the moment a CoinWithdrawal request is made
        WITHDRAWAL_REVERSED = "withdrawal_reversed", "Withdrawal Reversed"  # credited back on reject/cancel

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_transactions")

    txn_type = models.CharField(max_length=10, choices=TxnType.choices)
    reason = models.CharField(max_length=32, choices=Reason.choices)
    amount = models.PositiveIntegerField()
    balance_after = models.PositiveIntegerField()

    reference_id = models.CharField(max_length=100, blank=True)  # e.g. PassPurchase id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} {self.txn_type} {self.amount} ({self.reason})"


# ---------------------------------------------------------------------------
# 13A2. COIN PURCHASE (real-money -> coin top-up)
#
# GAP THIS CLOSES: CoinTransaction.Reason.TOPUP has existed as an enum
# choice since the ledger was first written, but nothing anywhere ever
# created one — there was no payment gateway integration, no order/verify
# flow, and critically no way to RETRY a failed top-up (a student whose
# payment failed had no recovery path except contacting support). This is
# that missing feature: a gateway-agnostic order -> verify -> credit flow,
# modeled closely on how Razorpay/similar Indian PGs work (order created
# server-side, client completes payment, gateway posts a signed
# confirmation back), but the actual `_verify_signature`/`_create_order`
# calls are provider stubs — wire in real Razorpay/Cashfree/etc. keys via
# settings before going live (see CoinPurchaseViewSet in views.py).
# ---------------------------------------------------------------------------
class CoinPurchase(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_purchases")
    coins = models.PositiveIntegerField()
    amount_inr = models.DecimalField(max_digits=8, decimal_places=2)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Gateway-side identifiers. order_id is ours (created up front, always
    # present); gateway_payment_id/gateway_signature only get filled in once
    # the client actually completes payment and we verify it.
    order_id = models.CharField(max_length=100, unique=True)
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    gateway_signature = models.CharField(max_length=255, blank=True)

    # NOTE: a FAILED purchase is never mutated back to PENDING/SUCCESS —
    # retrying creates a brand-new CoinPurchase row (new order_id) that
    # points back here, so the failure stays on the record instead of
    # being overwritten. See CoinPurchaseViewSet.retry in views.py.
    retry_of = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="retries"
    )
    failure_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status", "-created_at"])]

    def __str__(self):
        return f"{self.user} - {self.coins} coins ({self.get_status_display()})"

    # TASK 6 — READ-ONLY as of this pass. Coin top-ups now go through
    # user_profile.CoinPurchaseRequest (start_purchase/confirm_success),
    # which writes through the shared CoinLedger.objects.record_transaction()
    # instead of touching User.coin directly here. These two methods are
    # the only places this model ever used to move a balance
    # (CoinPurchaseViewSet.initiate/verify/retry in tuitionclass/views.py were
    # already turned into 410-Gone stubs in the same pass) — kept as raising
    # stubs, not deleted, so any call site this review didn't find (an admin
    # action, a Celery task, a shell script) fails loudly instead of quietly
    # re-crediting a wallet through a now-deprecated path. Existing rows and
    # `mark_tuitionclass_coin_purchase_readonly = migrate_tuitionclass_coin_models`
    # management command may still read every field here freely — only the
    # writes are blocked.
    def mark_success(self, gateway_payment_id: str, gateway_signature: str) -> None:
        raise RuntimeError(
            "CoinPurchase.mark_success() is disabled (Task 6) — coin "
            "purchases are now confirmed via "
            "user_profile.CoinPurchaseRequest.objects.confirm_success()."
        )

    def mark_failed(self, reason: str = "") -> None:
        raise RuntimeError(
            "CoinPurchase.mark_failed() is disabled (Task 6) — coin "
            "purchases are now failed via "
            "user_profile.CoinPurchaseRequest.objects.mark_failed()."
        )
