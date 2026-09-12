# user_profile/admin.py
"""
TASK 5 — this file wasn't part of any earlier upload for this app (see
the CAUTION note in `CoinLedger`'s docstring in models.py — admin.py's
absence was already flagged there as the reason `CoinLedger` was
"sirf admin me registered hai" with NO read-only guard). This pass
adds it from scratch with just the two things TASK 5 actually needs:

  - `CoinLedger` registered READ-ONLY. Per that CAUTION note: letting
    admin add/edit/delete `CoinLedger` rows directly bypasses
    `CoinLedgerManager.record_transaction()` entirely, which can create
    a ledger row with no matching `User.coin` change (or edit an
    existing row's `amount` without touching the balance it supposedly
    explains) — silently breaking the "ledger and balance must always
    agree" invariant this table exists to guarantee. Admin should be
    a VIEWER of this table, never a second unguarded write path.
  - A "Withdrawal eligible" list filter, driven by the
    `metadata["withdrawal_eligible"]` flag `record_transaction()` now
    stamps on every row (see that method's docstring), so ops can
    quickly see/filter which credits are/aren't withdrawal-eligible
    without cross-referencing `transaction_type` by hand.

IMPORTANT: if `Follow`, `BlockUser`, `RestrictUser`,
`CoinPurchaseRequest`, or `CoinWithdrawalRequest` are already
registered in a version of this file elsewhere in the codebase (this
upload never included an existing admin.py, so this pass has no
visibility into one), merge those registrations into this file rather
than letting this overwrite them — this file only defines
`CoinLedger`'s registration; it doesn't know about or touch the
others.
"""
from django.contrib import admin

from .models import CoinLedger


class WithdrawalEligibleFilter(admin.SimpleListFilter):
    """
    Ops-facing filter on the `metadata["withdrawal_eligible"]` flag
    `CoinLedgerManager.record_transaction()` stamps on every row. Reads
    the flag rather than re-deriving eligibility from
    `transaction_type` here, so this filter and `fraud.py` can never
    silently disagree about what "eligible" means for a given row —
    there is exactly one place (`record_transaction`) that decides it.
    """

    title = "withdrawal eligible"
    parameter_name = "withdrawal_eligible"

    def lookups(self, request, model_admin):
        return (
            ("1", "Eligible (purchase / gift)"),
            ("0", "Not eligible (earn / reward / other)"),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(metadata__withdrawal_eligible=True)
        if self.value() == "0":
            return queryset.filter(metadata__withdrawal_eligible=False)
        return queryset


@admin.register(CoinLedger)
class CoinLedgerAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "transaction_type",
        "amount",
        "balance_after",
        "reference",
        "created_at",
    )
    list_filter = ("transaction_type", WithdrawalEligibleFilter)
    search_fields = ("user__username", "reference", "description")
    date_hierarchy = "created_at"

    # Read-only, deliberately: see the module docstring above. Every
    # field is listed (not just a subset) so a future field addition
    # to CoinLedger doesn't accidentally become admin-editable by
    # being left off this list.
    readonly_fields = [f.name for f in CoinLedger._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Deletion (unlike add/change) doesn't corrupt a row's own
        # amount/balance_after — but it DOES let an already-applied
        # balance change vanish from the audit trail while `User.coin`
        # keeps the effect, which is just as bad for the "ledger
        # explains every balance change" guarantee. Blocked for the
        # same reason.
        return False