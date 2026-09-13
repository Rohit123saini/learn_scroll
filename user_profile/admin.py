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
from django.contrib import admin, messages

from .models import CoinLedger, CoinWithdrawalRequest, UserPreference


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

@admin.register(CoinWithdrawalRequest)
class CoinWithdrawalRequestAdmin(admin.ModelAdmin):
    """
    §11 item 12 — the Django-admin half of the same gap
    `CoinWithdrawalAdminActionView` (views.py) fills over the API:
    `CoinWithdrawalRequestManager.mark_processing()`/`confirm_success()`/
    `reject()` existed and were unit-tested, but nothing (view or admin)
    could reach them without a Django shell.

    Same read-only reasoning as `CoinLedgerAdmin` above applies to the
    fields themselves — letting admin hand-edit `status`/`coins`/
    `debit_ledger_entry` etc. directly would let a withdrawal's status
    change without going through the manager methods that keep it in
    sync with `CoinLedger` (a REJECTED row with no refund entry, or a
    SUCCESS row that never actually got debited). So add/change/delete
    stay blocked here exactly like `CoinLedgerAdmin`. Unlike
    `CoinLedgerAdmin` though, this table isn't meant to be *inert* in
    admin — ops needs a way to actually move a request forward — so the
    three actions below are the sanctioned way in: each calls the
    matching manager method (never touches a field directly), so admin
    becomes a safe front end for that method instead of a second write
    path around it.
    """

    list_display = (
        "id",
        "user",
        "coins",
        "payout_method",
        "status",
        "failure_reason",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "payout_method")
    search_fields = ("user__username", "failure_reason")
    date_hierarchy = "created_at"

    # Every field, for the same "don't let a future field addition
    # sneak in editable" reason CoinLedgerAdmin's readonly_fields lists
    # every field rather than a hand-picked subset.
    readonly_fields = [f.name for f in CoinWithdrawalRequest._meta.fields]

    actions = ["mark_processing_action", "confirm_success_action", "reject_action"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Same reasoning as CoinLedgerAdmin.has_delete_permission: a
        # deleted request row would make an already-applied debit (or
        # refund) vanish from the audit trail while the coin movement
        # itself stands, which is exactly the drift this table's
        # ForeignKeys to CoinLedger exist to prevent.
        return False

    def _run_bulk_action(self, request, queryset, method_name, ok_message, **kwargs):
        """
        Shared runner for the three actions below: calls
        `CoinWithdrawalRequest.objects.<method_name>(withdrawal_id=...,
        **kwargs)` per selected row, catching the `ValueError` each
        manager method raises for an invalid state transition (e.g.
        rejecting an already-SUCCESS request) so one bad row in a bulk
        selection doesn't stop the rest — same "well-formed action,
        wrong current state" case `CoinWithdrawalAdminActionView`
        (views.py) turns into a 409 for the single-row API equivalent.
        """
        succeeded = 0
        for withdrawal in queryset:
            try:
                getattr(CoinWithdrawalRequest.objects, method_name)(
                    withdrawal_id=withdrawal.pk, **kwargs
                )
                succeeded += 1
            except ValueError as exc:
                self.message_user(request, f"Withdrawal {withdrawal.pk}: {exc}", level=messages.WARNING)
        if succeeded:
            self.message_user(request, f"{ok_message} ({succeeded} request(s)).")

    @admin.action(description="Mark selected withdrawals as processing")
    def mark_processing_action(self, request, queryset):
        self._run_bulk_action(request, queryset, "mark_processing", "Moved to processing")

    @admin.action(description="Mark selected withdrawals as successful")
    def confirm_success_action(self, request, queryset):
        self._run_bulk_action(request, queryset, "confirm_success", "Marked successful")

    @admin.action(description="Reject selected withdrawals (refunds coins)")
    def reject_action(self, request, queryset):
        self._run_bulk_action(
            request,
            queryset,
            "reject",
            "Rejected and refunded",
            reason="Rejected via admin bulk action",
        )


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    """
    TASK 1 -- unlike CoinLedger above, this table has no audit-trail
    invariant to protect (a theme/language row has nothing else in the
    system it needs to stay consistent with), so normal add/change/
    delete is left enabled -- useful for support to fix a stuck value
    for a user without going through the API.
    """
    list_display = ("id", "user", "theme", "language", "updated_at")
    list_filter = ("theme", "language")
    search_fields = ("user__username",)