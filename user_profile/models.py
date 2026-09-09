# user_profile/models.py
"""
WHAT CHANGED in this pass, and why:

1. Follow / BlockUser — removed the single-column `models.Index(fields=
   ["follower"])`, `["following"])`, `["blocker"])`, `["blocked"])`
   entries. Django already creates a database index on every
   ForeignKey column automatically (that's what `db_index` defaults to
   True for on ForeignKey). Declaring them again in Meta.indexes doesn't
   make lookups faster — it silently builds a second, identical btree
   index that Postgres still has to update on every INSERT/DELETE. Kept
   the *composite* indexes (`follower`+`status`, `following`+`status`)
   since those are NOT automatic and genuinely serve the "my pending
   follow requests" / "who I actively follow" query shapes.

2. RestrictUser — unchanged in shape, still intentionally not wired into
   any view (flagged as a product decision, not a bug, same as before).

4. All four `CheckConstraint(...)` calls (Follow, BlockUser,
   RestrictUser, CoinLedger) now pass `condition=` instead of `check=`.
   Django deprecated `CheckConstraint(check=...)` in favor of
   `condition=` back in 5.1, and removed `check` entirely in Django 6.0
   — on Django 6.0+, `check=` raises `TypeError: CheckConstraint.
   __init__() got an unexpected keyword argument 'check'` at import
   time (which is exactly what breaks `makemigrations`/`migrate` and
   even the dev server on startup). `condition=` works the same way and
   is supported back to Django 5.1, so this is safe on any currently
   supported Django version.

5. CoinLedger — redesigned. The original had `credit`/`debit` as two
   separate PositiveIntegerFields, no way to tell what a transaction was
   *for*, and no protection against a retried webhook/request double-
   crediting a user. Since the original file's own comment already says
   "Nothing writes to this yet", there's no production data to migrate —
   this is the safe moment to fix the shape, not after it's live.
   Changes:
     - `credit`/`debit` -> single signed `amount` (positive = credit,
       negative = debit). One column instead of two, and a CHECK
       constraint guarantees it's never zero (a zero-amount ledger row
       is a bug, not a valid transaction).
     - Added `transaction_type` so "why did this user's balance change"
       is answerable from the row itself, not guessed from context.
     - Added `reference` (indexed, blank-ok) as an idempotency key —
       whatever created the transaction (a gift, a withdrawal, a
       purchase receipt) passes its own id here; wrap the write in
       `get_or_create(reference=..., defaults={...})` at the call site
       and a retried webhook can never double-credit.
     - Added `balance_after` — a snapshot of `User.coin` immediately
       after this entry. This is what makes the ledger self-auditing:
       if `User.coin` and the ledger ever drift, you can bisect the
       ledger to find exactly where, instead of trusting a running
       total nobody can verify.
     - Added `description` for a human-readable reason, and `metadata`
       for anything structured that doesn't need its own column.
     - Added `Meta.ordering` and an index on (`user`, `-created_at`) —
       "this user's transaction history" is the obvious primary query
       and had no supporting index before.
"""
from django.conf import settings
from django.db import models
from django.db.models import CheckConstraint, F, Q, UniqueConstraint


class Follow(models.Model):

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"

    follower = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="following_relation",
        on_delete=models.CASCADE,
    )

    following = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="followers_relation",
        on_delete=models.CASCADE,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACCEPTED,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Newest-first by default — earlier there was no ordering, so
        # followers/following lists came back in whatever order the DB
        # felt like.
        ordering = ["-created_at"]

        constraints = [
            UniqueConstraint(
                fields=["follower", "following"],
                name="unique_follow",
            ),
            # "can't follow yourself" was only enforced in a commented-out
            # `clean()` (which ModelForms call, but plain
            # `.objects.create()` from views.py never does). A DB-level
            # constraint makes it impossible to bypass, from the admin, a
            # shell, or a future view that forgets the check.
            CheckConstraint(
                condition=~Q(follower=F("following")),
                name="follow_no_self_follow",
            ),
        ]

        indexes = [
            # NOTE: no standalone index on `follower` / `following` here —
            # Django already auto-indexes both ForeignKey columns
            # individually. Only the composite pairs below (which are NOT
            # automatic) are declared explicitly.
            models.Index(fields=["follower", "status"]),
            models.Index(fields=["following", "status"]),
        ]

    def __str__(self):
        return f"{self.follower.username} -> {self.following.username} ({self.status})"


class BlockUser(models.Model):

    blocker = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocked_relation",
        on_delete=models.CASCADE,
    )

    blocked = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="blocked_by_relation",
        on_delete=models.CASCADE,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            UniqueConstraint(
                fields=["blocker", "blocked"],
                name="unique_block",
            ),
            # Same self-relation gap as Follow — serializer-level
            # `validate_blocked` was the only guard before this.
            CheckConstraint(
                condition=~Q(blocker=F("blocked")),
                name="block_no_self_block",
            ),
        ]

        # NOTE: no explicit indexes here — `blocker` and `blocked` are
        # both ForeignKeys and already get an automatic single-column
        # index each; there's no composite query shape for BlockUser that
        # needs one beyond that (unlike Follow, which is also filtered by
        # `status`).

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"


class RestrictUser(models.Model):
    """
    Defined but not wired into any view/serializer yet (Instagram-style
    "restrict" — softer than block: restricted user can still see/comment
    but you don't see notifications from them, etc). Left as-is since
    building that feature is a product decision, not a bug fix — but
    flagging it so it doesn't get forgotten or shipped half-done.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="restricted_users",
    )

    restricted = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="restricted_by",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            UniqueConstraint(
                fields=["user", "restricted"],
                name="unique_restrict",
            ),
            CheckConstraint(
                condition=~Q(user=F("restricted")),
                name="restrict_no_self_restrict",
            ),
        ]

    def __str__(self):
        return f"{self.user.username} restricted {self.restricted.username}"


class CoinLedger(models.Model):
    """
    The append-only, auditable history of every change to `User.coin`
    (the running-balance field on `login.User`). `User.coin` is a cache;
    this table is the source of truth — if they ever disagree, this table
    is right and `User.coin` should be recomputed from it.

    Renamed from the original lowercase `coins` — kept here as `CoinLedger`
    (not `CoinTransaction`) to avoid breaking any existing imports/
    related_name usage elsewhere in the codebase; only the *fields*
    changed, not the class name or `related_name`.

    If a migration already exists against the old `credit`/`debit` shape,
    write this as a real migration (RemoveField credit/debit, AddField
    amount/transaction_type/reference/balance_after/description/metadata)
    rather than running makemigrations blind on a prod DB — but per the
    original comment ("Nothing writes to this yet"), there should be zero
    rows to migrate, so this is the cheap moment to make this change.
    """

    class TransactionType(models.TextChoices):
        EARN = "earn", "Earned"
        PURCHASE = "purchase", "Purchased"
        SPEND = "spend", "Spent"
        REFUND = "refund", "Refunded"
        GIFT_SENT = "gift_sent", "Gift Sent"
        GIFT_RECEIVED = "gift_received", "Gift Received"
        ADMIN_ADJUSTMENT = "admin_adjustment", "Admin Adjustment"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_ledger_entries",
    )

    transaction_type = models.CharField(max_length=20, choices=TransactionType.choices)

    # Signed: positive = credit (balance goes up), negative = debit
    # (balance goes down). Replaces the old separate `credit`/`debit`
    # PositiveIntegerFields — one column, and a row can never accidentally
    # have both credit and debit set (which the old shape allowed and
    # meant nothing).
    amount = models.IntegerField()

    # Snapshot of User.coin immediately after this entry was applied.
    # Makes the ledger self-auditing: replay it in order and every
    # balance_after must equal the running sum — any mismatch pinpoints
    # exactly which write went wrong.
    balance_after = models.PositiveIntegerField()

    # Idempotency key for whatever created this row (a gift id, a
    # withdrawal id, a payment gateway receipt id, ...). Callers should
    # `get_or_create(reference=..., defaults={...})` so a retried
    # webhook/request can never double-apply the same transaction.
    reference = models.CharField(max_length=150, blank=True, db_index=True)

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=~Q(amount=0), name="coinledger_amount_not_zero"),
        ]

    def __str__(self):
        sign = "+" if self.amount >= 0 else ""
        return f"{self.user.username}: {sign}{self.amount} ({self.transaction_type})"