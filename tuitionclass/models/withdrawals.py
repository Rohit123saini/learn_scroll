# tuitionclass/models/withdrawals.py — split out of the old tuitionclass/models.py (P-C4). Code is moved verbatim.

from login.models import User
from django.db import models


# ---------------------------------------------------------------------------
# 13C. COIN WITHDRAWAL (cash-out of a real, earned coin balance to a bank
#      account / UPI id — the off-ramp this app was missing)
#
# GAP THIS CLOSES: CoinTransaction.Reason.CLASS_EARNING already credits a
# teacher's User.coin balance one calendar day at a time as classes actually
# happen (see PassPurchase.charge_for_session above), and REFERRAL_BONUS
# credits it too — but until now there was NO way for that balance to ever
# leave the platform as real money. A teacher could earn coins forever and
# never be able to withdraw them. This model + CoinWithdrawalViewSet (views.py)
# is that missing off-ramp. Coin TOP-UP (real money -> coins, i.e. buying
# coins) is deliberately OUT of scope here per product decision — this only
# covers coins -> real money, the direction that was actually missing.
#
# Design mirrors PassPurchase.reverse()'s pattern: debit eagerly at REQUEST
# time (so a user can't request the same coins twice while a request is
# pending, without needing a separate "coins on hold" bookkeeping field),
# refund on reject/cancel, no further coin movement on approve/mark_paid
# (the coins already left the wallet the moment the request was made — all
# that's left is the actual bank/UPI transfer, done OUTSIDE this app by
# finance/admin, then recorded here for audit).
# ---------------------------------------------------------------------------
class CoinWithdrawal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"  # admin approved, payout not sent yet
        REJECTED = "rejected", "Rejected"  # coins refunded back to wallet
        PAID = "paid", "Paid"  # payout actually sent (external to this app)
        CANCELLED = "cancelled", "Cancelled"  # user cancelled their own request

    class PayoutMethod(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        UPI = "upi", "UPI"

    # Coins earned genuinely have a real INR value the moment they're
    # credited via CLASS_EARNING (they're what a student actually paid for a
    # pass) — COIN_TO_INR_RATE is the single conversion constant the whole
    # withdrawal flow (validation, serializer display, admin payout amount)
    # reads from, so it only ever needs to change in one place.
    COIN_TO_INR_RATE = 1  # 1 coin == this many INR; adjust to match the actual coin pricing used when passes are priced
    MIN_WITHDRAWAL_COINS = 100  # below this, a bank/UPI transfer typically costs more in fees than the payout itself

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="coin_withdrawals")

    coins = models.PositiveIntegerField()
    amount_inr = models.DecimalField(max_digits=10, decimal_places=2)  # coins * COIN_TO_INR_RATE, snapshotted at request time so a later rate change never rewrites history

    payout_method = models.CharField(max_length=20, choices=PayoutMethod.choices)
    # Bank: {"account_holder", "account_number", "ifsc"}. UPI: {"upi_id"}.
    # Validated by CoinWithdrawalSerializer against payout_method at request
    # time — kept as JSON (not separate columns) so adding a payout method
    # later (e.g. a wallet provider) never needs a migration.
    payout_details = models.JSONField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    admin_note = models.TextField(blank=True)  # rejection reason, or any payout note
    external_reference = models.CharField(max_length=100, blank=True)  # bank UTR / UPI txn id, filled in on mark_paid

    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="coin_withdrawals_reviewed"
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [models.Index(fields=["status", "requested_at"])]

    def __str__(self):
        return f"{self.user} withdraw {self.coins} coins ({self.status})"

    # TASK 6 — READ-ONLY as of this pass. Withdrawals now go through
    # user_profile.CoinWithdrawalRequest (request_withdrawal/mark_processing/
    # confirm_success/reject), which writes through the shared
    # CoinLedger.objects.record_transaction() instead of touching User.coin
    # directly here. These five methods were the only places this model
    # ever moved a balance (CoinWithdrawalViewSet's create/cancel/approve/
    # reject/mark-paid in tuitionclass/views.py were already turned into
    # 410-Gone stubs in the same pass) — kept as raising stubs, not
    # deleted, so any call site this review didn't find fails loudly
    # instead of quietly re-moving coins through a now-deprecated path.
    # Existing rows may still be read freely (list/retrieve in
    # CoinWithdrawalViewSet, the migrate_tuitionclass_coin_models command,
    # admin) — only the writes are blocked.
    @classmethod
    def create_request(cls, user, coins: int, payout_method: str, payout_details: dict) -> "CoinWithdrawal":
        raise RuntimeError(
            "CoinWithdrawal.create_request() is disabled (Task 6) — coin "
            "withdrawals are now requested via "
            "user_profile.CoinWithdrawalRequest.objects.request_withdrawal()."
        )

    def _refund_coins(self, reason_note: str) -> None:
        raise RuntimeError("CoinWithdrawal._refund_coins() is disabled (Task 6).")

    def approve(self, admin_user) -> None:
        raise RuntimeError(
            "CoinWithdrawal.approve() is disabled (Task 6) — use "
            "user_profile.CoinWithdrawalRequest.objects.mark_processing()."
        )

    def reject(self, admin_user, reason: str) -> None:
        raise RuntimeError(
            "CoinWithdrawal.reject() is disabled (Task 6) — use "
            "user_profile.CoinWithdrawalRequest.objects.reject()."
        )

    def cancel(self) -> None:
        raise RuntimeError(
            "CoinWithdrawal.cancel() is disabled (Task 6) — cancellation "
            "isn't a separate case in the new model; use "
            "user_profile.CoinWithdrawalRequest.objects.reject() while "
            "still PENDING."
        )

    def mark_paid(self, admin_user, external_reference: str) -> None:
        raise RuntimeError(
            "CoinWithdrawal.mark_paid() is disabled (Task 6) — use "
            "user_profile.CoinWithdrawalRequest.objects.confirm_success()."
        )
