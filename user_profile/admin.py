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

Registered here now (bottom of this file): `Follow`, `BlockUser`,
`RestrictUser` (plain editable admins — Follow edits/deletes keep the
follower counters exact via user_profile/signals.py) and
`CoinPurchaseRequest` (READ-ONLY viewer; manual top-ups are confirmed
through `POST /profile/buy-coin/admin-confirm/`). `CoinLedger`,
`CoinWithdrawalRequest` and `UserPreference` are registered above. If any
of these models is ALSO registered in another admin.py elsewhere in the
codebase, Django raises AlreadyRegistered at startup — keep exactly one.
"""
from django.contrib import admin, messages

from .models import (
    BlockUser,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
    UserPreference,
)


class WithdrawalEligibleFilter(admin.SimpleListFilter):
    """
    Ops-facing "withdrawal eligible" filter.

    Filters on `transaction_type` using `fraud.eligible_source_types()` — the
    same single definition `fraud.py` and `record_transaction()` use — and
    NOT on `metadata__withdrawal_eligible`. The flag is a pure function of
    `transaction_type`, so querying inside the JSON column bought nothing and
    cost a lot: without a Postgres GIN index that is a full scan of the
    append-only ledger with a JSON extraction per row (the dashboard would
    time out on a big table), and a GIN index is Postgres-only, heavy to
    maintain on a hot insert table, and still wouldn't cover rows written
    before the flag existed. `transaction_type` is covered by the
    (transaction_type, -created_at) index instead.
    """

    title = "withdrawal eligible"
    parameter_name = "withdrawal_eligible"

    def lookups(self, request, model_admin):
        return (
            ("1", "Eligible (purchase / gift / rejected-withdrawal refund)"),
            ("0", "Not eligible (earn / reward / other)"),
        )

    def queryset(self, request, queryset):
        from .fraud import eligible_source_types

        if self.value() == "1":
            return queryset.filter(transaction_type__in=eligible_source_types())
        if self.value() == "0":
            return queryset.exclude(transaction_type__in=eligible_source_types())
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


# ---------------------------------------------------------------------------
# Social-graph tables (issue #4 / #2).
#
# Deleting or editing a Follow here is now SAFE: user_profile/signals.py
# recounts followers_count/following_count on every Follow save/delete
# (including admin's bulk "delete selected", which deletes row by row so
# signals fire). Before, an admin delete silently drifted the counters.
# ---------------------------------------------------------------------------
@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("id", "follower", "following", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("follower__username", "following__username")
    raw_id_fields = ("follower", "following")


@admin.register(BlockUser)
class BlockUserAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    raw_id_fields = ("blocker", "blocked")


@admin.register(RestrictUser)
class RestrictUserAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "restricted", "created_at")
    search_fields = ("user__username", "restricted__username")
    raw_id_fields = ("user", "restricted")


@admin.register(CoinPurchaseRequest)
class CoinPurchaseRequestAdmin(admin.ModelAdmin):
    """
    READ-ONLY viewer, same reasoning as `CoinLedgerAdmin`: flipping `status`
    by hand would skip the manager method that credits the wallet exactly
    once. Pending manual (blank-gateway) top-ups are confirmed through
    `POST /profile/buy-coin/admin-confirm/` (AdminCoinPurchaseConfirmView,
    IsAdminUser) — admin here only lets ops SEE which ones are waiting.
    """

    list_display = ("id", "user", "gateway", "gateway_reference", "amount", "coins", "status", "created_at")
    list_filter = ("status", "gateway")
    search_fields = ("gateway_reference", "user__username")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

