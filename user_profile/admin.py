from django.contrib import admin

from .models import BlockUser, CoinLedger, Follow, RestrictUser


@admin.register(Follow)
class FollowAdmin(admin.ModelAdmin):
    list_display = ("id", "follower", "following", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("follower__username", "following__username")
    autocomplete_fields = ("follower", "following")
    ordering = ("-created_at",)


@admin.register(BlockUser)
class BlockUserAdmin(admin.ModelAdmin):
    list_display = ("id", "blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")
    autocomplete_fields = ("blocker", "blocked")
    ordering = ("-created_at",)


@admin.register(RestrictUser)
class RestrictUserAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "restricted", "created_at")
    search_fields = ("user__username", "restricted__username")
    autocomplete_fields = ("user", "restricted")
    ordering = ("-created_at",)


@admin.register(CoinLedger)
class CoinLedgerAdmin(admin.ModelAdmin):
    # 🔥 FIX: `credit`/`debit` no longer exist on CoinLedger — the model
    # was redesigned to a single signed `amount` column plus
    # `transaction_type`, `reference`, and a self-auditing
    # `balance_after` snapshot (see models.py docstring). Listing the old
    # field names here would raise `FieldDoesNotExist` the moment this
    # admin page is opened.
    list_display = (
        "id",
        "user",
        "transaction_type",
        "amount",
        "balance_after",
        "reference",
        "created_at",
    )
    # `transaction_type` is a bounded TextChoices field — filtering by it
    # (like `status`/`created_at` on the other admins) is cheap and useful
    # for "show me all admin_adjustment rows" style audits.
    list_filter = ("transaction_type", "created_at")
    # `reference` is the idempotency key callers pass in (gift id,
    # withdrawal id, payment receipt id) — searchable so support/finance
    # can look up "what happened for reference X".
    search_fields = ("user__username", "reference")
    autocomplete_fields = ("user",)
    ordering = ("-created_at",)