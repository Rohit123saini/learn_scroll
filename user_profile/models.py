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

2. RestrictUser — TASK 18: this used to be defined but completely
   unwired (DB-level self-restrict guard only, no view/serializer ever
   touched it). Decision: BUILD the feature, not remove the model —
   `unique_restrict` + `restrict_no_self_restrict` are exactly the
   constraints a real restrict feature needs, and BlockUser next to it
   is proof this app is already the right home for this kind of
   relationship. Now wired via `RestrictedUsersView` /
   `UnrestrictUserView` (views.py) + `RestrictUserSerializer`
   (serializers.py) — see that model's docstring below for the actual
   semantics and what's intentionally still NOT done here.

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

5. CoinLedger — redesigned in an earlier pass. The original had
   `credit`/`debit` as two separate PositiveIntegerFields, no way to
   tell what a transaction was *for*, and no protection against a
   retried webhook/request double-crediting a user. Since the original
   file's own comment already said "Nothing writes to this yet", there
   was no production data to migrate — that was the safe moment to fix
   the shape, not after it went live. Changes made then:
     - `credit`/`debit` -> single signed `amount` (positive = credit,
       negative = debit). One column instead of two, and a CHECK
       constraint guarantees it's never zero (a zero-amount ledger row
       is a bug, not a valid transaction).
     - Added `transaction_type` so "why did this user's balance change"
       is answerable from the row itself, not guessed from context.
     - Added `reference` (indexed, blank-ok) as an idempotency key —
       whatever created the transaction (a gift, a withdrawal, a
       purchase receipt) passes its own id here.
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

   TASK 19 (this pass): the shape above was right, but the table still
   had no write OR read path anywhere in the codebase —
   `admin.site.register()` only, meaning `User.coin` was the only thing
   any real code path touched, so the "source of truth" claim in
   CoinLedger's docstring was aspirational, not true. Added
   `CoinLedger.objects.record_transaction()` (the one sanctioned, atomic
   write path every coin-changing action should call through — replaces
   the old "wrap it in `get_or_create(reference=...)` yourself" guidance,
   which never actually updated `User.coin` in the same breath) and a
   read-only `CoinLedgerListView` (views.py) so a user can see their own
   transaction history. See CoinLedger's class docstring below for the
   full purpose/scope writeup, including why no generic write endpoint
   is exposed and an admin.py caveat this pass couldn't fix (no
   admin.py in this upload).

6. TASK 1 (this pass) — added 5 new `TransactionType` choices:
   `TESTSERIES_PURCHASE`, `TESTSERIES_PAYOUT`, `WITHDRAWAL_REQUESTED`,
   `WITHDRAWAL_COMPLETED`, `WITHDRAWAL_REJECTED`. Pure addition — no
   existing choice renamed or removed, so no migration is needed for
   the enum itself (`choices=` is not a schema-affecting kwarg).
   `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT` were already being
   referenced directly by `testseries/models.py`
   (`TestSeriesPurchase.purchase_and_start_attempt()` / `.release()`)
   before this enum had them defined — that was a live `AttributeError`
   waiting to happen, now fixed. `WITHDRAWAL_*` aren't consumed by any
   code yet in this pass; added ahead of time as a prerequisite for the
   upcoming withdrawal flow, same as `core.Notification.NotifType`
   already carries `WITHDRAWAL_APPROVED`/`WITHDRAWAL_REJECTED`/
   `WITHDRAWAL_PAID` without every one of those being wired up yet.
   Checked field width: longest new value is `withdrawal_requested`/
   `withdrawal_completed` at 20 chars, which still fits the existing
   `transaction_type = CharField(max_length=20, ...)` — no field-length
   change needed either.

7. TASK 4 (this pass) — added `CoinWithdrawalRequest` +
   `CoinWithdrawalRequestManager`: the canonical "cash out coins"
   request for this app, using the `WITHDRAWAL_REQUESTED`/
   `WITHDRAWAL_REJECTED` transaction types TASK 1 added ahead of time.
   Reference read for this task was `liveclass.CoinWithdrawal` /
   `liveclass.CoinTransaction`, which already implement this exact
   escrow pattern (debit the coins the moment the request is made, not
   when the payout completes; refund only on reject; no second ledger
   write on completion since the debit already happened). This
   reproduces that lifecycle on top of `CoinLedger.record_transaction()`
   instead of `liveclass.CoinTransaction`, since `CoinLedger` — not
   `liveclass.CoinTransaction` — is this codebase's one shared,
   canonical coin ledger (see CoinLedger's own docstring). See
   `CoinWithdrawalRequest`'s class docstring below for the full
   lifecycle and what's deliberately left out of this pass (no
   `reviewed_by`, no `MIN_WITHDRAWAL_COINS` floor, no INR snapshot).

8. TASK 30 (this pass) — `CoinPurchaseRequest` diffed directly against
   `liveclass.CoinPurchase`, closing the gap an earlier pass had to
   leave as an inference (that pass's upload didn't include
   `liveclass/models.py`). Result: `liveclass.CoinPurchase` turned out
   to already be deprecated (its own Task 6 disabled
   `mark_success()`/`mark_failed()`), so full parity wasn't the goal —
   but it surfaced one real gap, not just a shape difference: this
   model had nowhere to persist a gateway webhook's payment id/
   signature for later verification. Added `gateway_payment_id` /
   `gateway_signature` to `CoinPurchaseRequest` and wired them through
   `confirm_success()`. See that model's class docstring for the full
   diff (money precision, `gateway` field, `retry_of` — each kept or
   skipped deliberately, not guessed).

9. TASK 31 (this pass) — `UserPreference.for_user()` diffed directly
   against `core.NotificationPreference.for_user()`, same "close the
   inference gap" situation as Task 30 (that earlier pass's upload
   didn't include `core/models.py` either). Unlike Task 30, this one
   turned out to be a clean confirmation, not a fix: classmethod name,
   get-or-create keying (`user=user` only), and default-population
   behavior (no `defaults=` dict on either — both just rely on model
   field defaults) all already matched. No code change needed — see
   `UserPreference`'s class docstring for the full point-by-point diff.

10. TASK 38 (this pass) — `CoinWithdrawalRequest` diffed directly
    against `liveclass.CoinWithdrawal` now that `liveclass/models.py`
    has actually been reviewed (Task 4's docstring above deferred this
    with "no reviewed_by, no MIN_WITHDRAWAL_COINS floor, no INR
    snapshot ... add them if/when an admin-facing withdrawal review UI
    is built" — that's now). See `CoinWithdrawalRequest`'s own
    docstring for the full diff and what each added field means for
    this model's PENDING -> PROCESSING -> SUCCESS/REJECTED lifecycle,
    which is NOT the same status set `liveclass.CoinWithdrawal` uses.
"""
from django.conf import settings
from django.db import IntegrityError, models, transaction
from django.db.models import CheckConstraint, F, Q, UniqueConstraint
from django.utils import timezone


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
    Instagram-style "restrict" — softer than block. `user` restricts
    `restricted`. Unlike BlockUser:
      - It's ONE-WAY and SILENT: `restricted` is never told, and (unlike
        block) nothing here removes an existing Follow relationship or
        hides either party from search/profile views — the whole point
        is that the restricted person's experience looks unchanged.
      - It does NOT block interaction: `restricted` can still view
        `user`'s profile/posts, follow, comment, and DM as normal from
        their own side.

    TASK 18 — now wired: `RestrictedUsersView` (list what I've
    restricted, restrict someone) and `UnrestrictUserView` (undo) live
    in views.py, `RestrictUserSerializer` in serializers.py, both
    reachable at `/profile/restricted-users/` — same URL/view shape as
    BlockUser's `/profile/blocked-users/` for consistency.

    Scope decision: this app (user_profile) only owns the
    relationship itself — who has restricted whom — the same way it
    owns Follow/BlockUser without knowing about posts, comments, DMs,
    or notifications. The actual *effects* of being restricted
    (hide the restricted user's comments from everyone but themselves,
    suppress notifications/read-receipts/online-status from them in
    the message app) are consumer-side integration work for the posts,
    notifications, and message apps respectively — each of those should
    filter through `views.is_restricted_between(user, other)` the same
    way this app's own `is_blocked_between()` is already used. Not
    implemented here because those apps' models/views weren't part of
    this pass.
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


class CoinLedgerManager(models.Manager):
    """
    TASK 19 — this is the actual fix: before this, literally nothing in
    the codebase wrote to `CoinLedger` (it was `admin.site.register()`'d
    only — visible in Django admin, unreachable from any API). `User.coin`
    was the only thing any code path actually touched, which means the
    "source of truth" claim in CoinLedger's docstring was aspirational,
    not true.

    `record_transaction()` is now THE one supported way to change
    `User.coin` — it updates the balance and writes the audit row in the
    same DB transaction, so the two can never drift the way two separate
    writes (`user.coin = user.coin + n; user.save()` then a separate
    `CoinLedger.objects.create(...)` elsewhere) could.
    """

    def record_transaction(self, *, user, transaction_type, amount,
                            reference="", description="", metadata=None):
        """
        Atomically apply `amount` (signed — positive credits, negative
        debits) to `user.coin` and record the matching CoinLedger row.

        Idempotent when `reference` is given: a second call with the
        same (user, reference) returns the FIRST entry as-is instead of
        applying the transaction again — safe for a retried webhook or a
        client retry after a dropped response. This is enforced by
        holding `select_for_update()` on the user's row for the whole
        transaction, so two concurrent calls for the SAME user always
        serialize on that lock — the second one's "does this reference
        already exist" check can never run before the first one's write
        has committed. (No DB UniqueConstraint on (user, reference) was
        added for this — the table has zero rows today, so adding one
        would need a fresh migration this pass doesn't have visibility
        into; the row lock gives the same guarantee for same-user
        concurrency, which is the case that actually matters here.)

        Raises `ValueError` for a zero amount or a debit that would take
        `user.coin` negative (PositiveIntegerField can't hold a negative
        balance anyway — this turns that into a clean, catchable error
        for the caller instead of an IntegrityError bubbling up).

        TASK 5 (this pass) — two fraud/anti-abuse hooks were added here,
        not in the view layer, specifically so they can't be bypassed by
        a call site that doesn't go through a particular view (campus
        tasks, referral bonus, any future caller) — same reasoning this
        being "the one sanctioned write path" already rests on:
          - Earn-rate limiting: for EARN/CAMPUS_REWARD credits only,
            `fraud.check_earn_rate_limit()` is checked (after the row
            lock below, so concurrent earn calls for the same user are
            serialized before the check runs, the same way the
            `reference` idempotency check already relies on that lock).
            A burst-farming caller gets `fraud.EarnRateLimitExceeded`
            instead of a silent credit — nothing here catches it, so it
            propagates straight to the caller (campus tasks, referral
            bonus, ...), which is expected to catch it the same way it's
            expected to catch the `ValueError`s below.
          - `metadata["withdrawal_eligible"]` is now always set (any
            caller-supplied value for that key is overwritten) purely
            from `transaction_type` — True only for
            PURCHASE/GIFT_RECEIVED, per `CoinWithdrawalRequest`'s
            eligibility rule. This is stored in `metadata` rather than a
            new column so this pass doesn't force a schema migration on
            a table that's already live (same "avoid the migration when
            the data doesn't demand a schema change" call this file
            already makes elsewhere) — it's also NOT what withdrawal
            eligibility is actually computed from (`fraud.
            get_withdrawal_eligible_balance()` derives that straight
            from `transaction_type`, the real source of truth); this
            flag exists purely so `admin.py`'s read-only ops filter can
            filter on it without re-deriving it per row.
        """
        if amount == 0:
            raise ValueError("CoinLedger amount must not be zero.")

        UserModel = type(user)  # avoid importing login.User here — this
        # app's models already only ever reference the user model via
        # settings.AUTH_USER_MODEL (see the FK fields above), never a
        # direct import, to keep user_profile decoupled from login.

        # Local import — avoids a module-level circular reference
        # between this module and `fraud.py`, which itself imports
        # `CoinLedger` from here to compute the rate-limit window. Same
        # pattern `CoinPurchaseRequestManager.confirm_success` already
        # uses a local import for, just against a different module.
        from .fraud import EarnRateLimitExceeded, check_earn_rate_limit

        merged_metadata = dict(metadata or {})
        merged_metadata["withdrawal_eligible"] = transaction_type in (
            self.model.TransactionType.PURCHASE,
            self.model.TransactionType.GIFT_RECEIVED,
        )

        with transaction.atomic():
            locked_user = UserModel.objects.select_for_update().get(pk=user.pk)

            if not check_earn_rate_limit(locked_user, transaction_type):
                raise EarnRateLimitExceeded(
                    f"Earn rate limit exceeded for user {locked_user.pk!r} "
                    f"({transaction_type})."
                )

            if reference:
                existing = self.filter(user=locked_user, reference=reference).first()
                if existing is not None:
                    return existing

            new_balance = locked_user.coin + amount
            if new_balance < 0:
                raise ValueError(
                    f"Insufficient coin balance: {locked_user.coin} + {amount} "
                    "would go negative."
                )

            UserModel.objects.filter(pk=locked_user.pk).update(coin=F("coin") + amount)

            return self.create(
                user=locked_user,
                transaction_type=transaction_type,
                amount=amount,
                balance_after=new_balance,
                reference=reference,
                description=description,
                metadata=merged_metadata,
            )


class CoinLedger(models.Model):
    """
    The append-only, auditable history of every change to `User.coin`
    (the running-balance field on `login.User`). `User.coin` is a cache;
    this table is the source of truth — if they ever disagree, this table
    is right and `User.coin` should be recomputed from it.

    TASK 19 — clarifying purpose + wiring it up (decision: build, same
    call as RestrictUser in task 18 — the shape was already right, it
    just had no write or read path). Purpose: an auditable "why did my
    balance change" trail for a coin economy that (per the settings.py
    comments referencing PassPurchase/CoinPurchase in a liveclass app,
    and gifting in a message app) clearly spans more than this one app.

    Scope decision, same boundary as RestrictUser: user_profile owns
    this table and is the only place allowed to write to it — via
    `CoinLedger.objects.record_transaction()` below, never a raw
    `.create()` — but the actual coin-changing *actions* (a purchase
    completing, a gift being sent, an admin adjustment) are triggered by
    whichever app/view performs that action. Those apps weren't part of
    this upload, so what's added here is: the shared, atomic write path
    (`record_transaction`) every app should call through, and a
    read-only "my transaction history" endpoint (`CoinLedgerListView` in
    views.py) so a user can actually see it. No generic "create any
    ledger entry" API is exposed — that would let a client hand-write
    their own `earn`/`refund`/`gift_received` rows and, since
    `record_transaction` also moves the real balance, mint themselves
    coins.

    F-3 (this pass): added `TransactionType.CAMPUS_REWARD` for
    `campus`'s small engagement bonuses (attendance-streak,
    assigments-on-time-streak) -- see that choice's own comment below for
    why it's kept distinct from `EARN` rather than reusing it.

    TASK 1 (this pass): added `TESTSERIES_PURCHASE`/`TESTSERIES_PAYOUT`/
    `WITHDRAWAL_REQUESTED`/`WITHDRAWAL_COMPLETED`/`WITHDRAWAL_REJECTED` —
    see `TransactionType` below for details. Pure choices-only addition,
    same as F-3 was.

    TASK 4 (this pass): `WITHDRAWAL_REQUESTED`/`WITHDRAWAL_REJECTED` are
    now actually consumed, by `CoinWithdrawalRequest` below.
    `WITHDRAWAL_COMPLETED` is still unused on purpose — see
    `CoinWithdrawalRequestManager.confirm_success`'s docstring for why a
    completed withdrawal doesn't get a new ledger row at all (the debit
    already happened at request time, and a zero-amount row would
    violate `coinledger_amount_not_zero`).

    CAUTION (can't fix from this pass — admin.py wasn't uploaded):
    Django admin currently allows raw add/edit/delete on this model
    directly (that's the "sirf admin me registered hai" from the task).
    Any edit made through the admin bypasses `record_transaction()`
    entirely — it can create a ledger row with no matching change to
    `User.coin`, or edit an existing row's `amount` without ever
    touching the balance it supposedly explains — silently breaking the
    exact "these must always agree" invariant this table exists to
    guarantee. If/when admin.py is available, that registration should
    be made read-only (`has_add_permission`/`has_change_permission`
    returning False, or `readonly_fields = [f.name for f in
    CoinLedger._meta.fields]`) so admin is a viewer, not a second
    unguarded write path.

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
        # F-3: campus engagement bonuses (attendance streak, on-time
        # assigments streak) — deliberately its own type, not EARN.
        # Keeping tuition-fee payments (real money, via FeeInvoice/
        # FeePayment) and these small in-app coin bonuses on visibly
        # different transaction_type values matters here specifically
        # because FEE-3/FEE-6 (campus/tasks.py) already reads a
        # student's `User.coin` balance to decide whether it covers an
        # upcoming fee — an admin/support person scanning this user's
        # ledger needs to tell at a glance "this coin came from a
        # reward, not a real top-up" without cross-referencing amounts.
        CAMPUS_REWARD = "campus_reward", "Campus Reward"

        # TASK 1: testseries app's escrow purchase/payout flow
        # (testseries/models.py — TestSeriesPurchase.
        # purchase_and_start_attempt() / .release()) already references
        # these two values directly; they were missing from this enum
        # until now (a live AttributeError waiting to happen). Kept
        # distinct from PURCHASE/EARN for the same "tell it apart at a
        # glance in the ledger" reasoning CAMPUS_REWARD's comment above
        # already gives — a support person scanning a creator's ledger
        # should be able to tell "this coin came from a test series
        # payout" without cross-referencing the reference field.
        TESTSERIES_PURCHASE = "testseries_purchase", "Test Series Purchase"
        TESTSERIES_PAYOUT = "testseries_payout", "Test Series Payout"

        # TASK 1: prerequisite for the upcoming coin-withdrawal flow
        # (a user cashes out coins to real money). TASK 4 wires
        # WITHDRAWAL_REQUESTED/WITHDRAWAL_REJECTED into
        # CoinWithdrawalRequest below; WITHDRAWAL_COMPLETED stays
        # unused — see that model's docstring for why.
        WITHDRAWAL_REQUESTED = "withdrawal_requested", "Withdrawal Requested"
        WITHDRAWAL_COMPLETED = "withdrawal_completed", "Withdrawal Completed"
        WITHDRAWAL_REJECTED = "withdrawal_rejected", "Withdrawal Rejected"

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
    # withdrawal id, a payment gateway receipt id, ...). TASK 19: don't
    # write this field directly — pass `reference=` to
    # `CoinLedger.objects.record_transaction()` above, which handles the
    # idempotent get-or-create-and-apply-balance dance atomically.
    reference = models.CharField(max_length=150, blank=True, db_index=True)

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # TASK 19: swap the default manager for one whose
    # `record_transaction()` is the only sanctioned write path — plain
    # `CoinLedger.objects.create(...)` still works (Manager doesn't
    # remove that), but every call site in this codebase should go
    # through `record_transaction()` instead so `balance_after` and the
    # actual `User.coin` update never come from two separate,
    # driftable writes.
    objects = CoinLedgerManager()

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


class CoinPurchaseRequestManager(models.Manager):
    """
    TASK 3 — the sanctioned write path for `CoinPurchaseRequest`, same
    role `CoinLedgerManager` plays for `CoinLedger` just above: callers
    (views, webhooks, the sweep task) go through these three methods
    instead of `.create()`/`.save()` directly, so "pending never touches
    the balance" and "confirming twice never double-credits" are
    guaranteed in one place rather than re-implemented at every call
    site.
    """

    def start_purchase(self, *, user, gateway_reference, amount, coins, gateway=""):
        """
        Create (or return the existing) PENDING request for this
        `gateway_reference`. Does NOT touch `User.coin` — a purchase
        only ever credits coins via `confirm_success()` below.

        Idempotent the same way `CoinLedger.objects.record_transaction()`
        is idempotent on `reference`: a second call with the same
        `gateway_reference` returns the row that's already there
        (`get_or_create`) instead of raising or creating a duplicate —
        safe for a client retrying a dropped response. `gateway_reference`
        also carries a DB `UniqueConstraint` (see Meta below), so if two
        concurrent requests both miss the `get_or_create` SELECT and race
        to INSERT, the loser's `IntegrityError` is caught here and turned
        into a re-fetch of the winner's row instead of a 500.
        """
        try:
            obj, created = self.get_or_create(
                gateway_reference=gateway_reference,
                defaults={
                    "user": user,
                    "amount": amount,
                    "coins": coins,
                    "gateway": gateway,
                },
            )
        except IntegrityError:
            obj = self.get(gateway_reference=gateway_reference)
            created = False
        return obj, created

    def confirm_success(self, *, gateway_reference, gateway_payment_id="", gateway_signature=""):
        """
        Mark a request SUCCESS and credit `coins` via
        `CoinLedger.objects.record_transaction()` — never a direct
        `User.coin` write — atomically, exactly once.

        [ADDED — Task 30] `gateway_payment_id`/`gateway_signature` are
        optional — a caller that already has them (the normal case: a
        verified gateway webhook) passes them through to be persisted
        on the row; a caller that doesn't (e.g. a manual admin confirm)
        can omit them and this stays exactly as safe as before this
        pass. Neither participates in the idempotency check below —
        that's still `gateway_reference` alone, same as before.

        Idempotent at two layers: if this request is already SUCCESS,
        it's returned as-is with no second credit (checked under
        `select_for_update()` so a retried webhook racing itself can't
        both pass the check); and even if that check were somehow
        bypassed, `record_transaction`'s own `reference=` idempotency
        (keyed on this request's id, not the gateway's reference, so it
        can never collide with an unrelated ledger entry) would still
        refuse to double-credit.

        Raises `ValueError` if the request is already FAILED — a failed
        purchase must be retried as a new request, not resurrected.
        """
        with transaction.atomic():
            purchase = self.select_for_update().get(gateway_reference=gateway_reference)

            if purchase.status == self.model.Status.SUCCESS:
                return purchase

            if purchase.status == self.model.Status.FAILED:
                raise ValueError(
                    f"Coin purchase {gateway_reference!r} already failed — cannot confirm."
                )

            # Local import avoids a module-level circular reference
            # between CoinLedger (defined above) and this manager; kept
            # as a local import anyway for symmetry with how this file
            # otherwise avoids importing the user model directly.
            ledger_entry = CoinLedger.objects.record_transaction(
                user=purchase.user,
                transaction_type=CoinLedger.TransactionType.PURCHASE,
                amount=purchase.coins,
                reference=f"coin_purchase_request:{purchase.pk}",
                description=f"Coin purchase via {purchase.gateway or 'payment gateway'}",
                metadata={
                    "coin_purchase_request_id": purchase.pk,
                    "gateway_reference": purchase.gateway_reference,
                    "amount_paid": str(purchase.amount),
                },
            )

            purchase.status = self.model.Status.SUCCESS
            purchase.ledger_entry = ledger_entry
            update_fields = ["status", "ledger_entry", "updated_at"]
            if gateway_payment_id:
                purchase.gateway_payment_id = gateway_payment_id
                update_fields.append("gateway_payment_id")
            if gateway_signature:
                purchase.gateway_signature = gateway_signature
                update_fields.append("gateway_signature")
            purchase.save(update_fields=update_fields)
            return purchase

    def mark_failed(self, *, gateway_reference, reason=""):
        """
        Mark a request FAILED. Never touches `User.coin` — that's the
        whole point of a two-step (pending -> success/failed) flow: a
        failed payment leaves the wallet exactly where it was.

        Idempotent: already-FAILED is returned as-is. Raises `ValueError`
        for an already-SUCCESS request — a completed purchase can't be
        un-credited by calling this; that would need an explicit refund
        (`CoinLedger.TransactionType.REFUND`), which is out of scope
        here.
        """
        with transaction.atomic():
            purchase = self.select_for_update().get(gateway_reference=gateway_reference)

            if purchase.status == self.model.Status.FAILED:
                return purchase

            if purchase.status == self.model.Status.SUCCESS:
                raise ValueError(
                    f"Coin purchase {gateway_reference!r} already succeeded — cannot fail."
                )

            purchase.status = self.model.Status.FAILED
            purchase.failure_reason = reason
            purchase.save(update_fields=["status", "failure_reason", "updated_at"])
            return purchase


class CoinPurchaseRequest(models.Model):
    """
    TASK 3 — canonical "buy coins" request/receipt row for `user_profile`.
    `liveclass.CoinPurchase` already has a purchase flow, but it's scoped
    to that app; this is the one every coin top-up should go through
    regardless of where in the product it's triggered from, the same way
    `CoinLedger` is the one shared ledger every coin-changing action
    writes to.

    [TASK 30 — RESOLVED] Diffed directly against `liveclass.CoinPurchase`
    now that `liveclass/models.py` has actually been reviewed (previously
    inferred, see git history of this docstring for the old caveat).
    Findings:

      - `liveclass.CoinPurchase` is itself already deprecated as of that
        app's own Task 6: `mark_success()`/`mark_failed()` there raise
        `RuntimeError`, and its own docstring says coin top-ups now go
        through THIS model instead. So field-for-field parity with a
        disabled legacy model was never really the goal — but two real
        gaps below were worth closing regardless of the legacy shape.
      - Money precision: `CoinPurchase.amount_inr` is
        `DecimalField(max_digits=8, decimal_places=2)` (max ~999,999.99).
        This model's `amount` is `max_digits=10` (max ~99,999,999.99) —
        strictly more headroom, not a gap, so left as-is rather than
        narrowed to match a deprecated model.
      - Gateway identity: `CoinPurchase` has no field naming WHICH
        gateway a row went through at all (a Razorpay-shaped design per
        its own module comment, not gateway-agnostic). `gateway` here is
        a deliberate improvement over that, not a divergence to fix.
      - `retry_of` (self-FK linking a retried purchase back to the
        failed one it retries): `CoinPurchase` has it, this model
        doesn't. Deliberately not added — `gateway_reference`'s
        uniqueness already makes a retry just a new row with a new
        reference, and nothing anywhere reads a retry chain today. Flag,
        don't build unread state.
      - [FIX] `gateway_payment_id` / `gateway_signature`: `CoinPurchase`
        has both — the gateway-posted payment id and signature, filled
        in once a webhook confirms payment — and this model had NEITHER
        before this pass. Not a naming difference: without a signature
        field there was nowhere to persist what a gateway webhook
        actually signed, so a `confirm_success()` caller had no way to
        keep proof of verification for later audit/dispute. Added both
        below (blank-ok — optional, so a caller without them yet isn't
        broken), and `confirm_success()` now accepts and stores them.

    Lifecycle: PENDING (created by `start_purchase`, wallet untouched) ->
    SUCCESS (via `confirm_success`, credits `coins` through
    `CoinLedger.objects.record_transaction()`) or FAILED (via
    `mark_failed`, wallet still untouched). SUCCESS and FAILED are both
    terminal — see the manager docstrings above for what happens if
    either is called again.

    Nothing here writes to `User.coin` directly, by design — same
    boundary `CoinLedger` draws: the only sanctioned path to a balance
    change is `CoinLedger.objects.record_transaction()`, so this model's
    manager calls through to it rather than duplicating the balance
    update.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_purchase_requests",
    )

    # Free-text on purpose (not a choices field) — this app doesn't own
    # the set of payment gateways the product integrates with, and
    # locking that down here would mean a migration every time a new
    # gateway is added elsewhere. Blank allowed for the same reason
    # CoinLedger.reference is blank-ok: not every caller may have a
    # gateway name handy (e.g. a manual admin-initiated top-up).
    gateway = models.CharField(max_length=30, blank=True)

    # The gateway's own transaction/order id. This is the idempotency
    # key for the whole request lifecycle: unique at the DB level so two
    # `start_purchase()` calls (or a retried client request) for the
    # same gateway transaction can never create two rows, and it's what
    # `confirm_success`/`mark_failed` key off of instead of an internal
    # id the gateway doesn't know about.
    gateway_reference = models.CharField(max_length=150, unique=True, db_index=True)

    # [ADDED — Task 30] Mirrors liveclass.CoinPurchase.gateway_payment_id
    # / .gateway_signature — filled in by confirm_success() once a
    # gateway webhook actually confirms payment. Both blank-ok at
    # creation time (start_purchase() runs before the gateway has
    # confirmed anything, so neither is known yet); this is the
    # persisted proof of what the gateway signed, for later
    # verification/audit — a real gap this model had before this pass,
    # not just a naming difference from the liveclass model (see class
    # docstring's Task 30 diff for the full comparison).
    gateway_payment_id = models.CharField(max_length=100, blank=True)
    gateway_signature = models.CharField(max_length=255, blank=True)

    # Real money paid, in the product's billing currency. Decimal (not
    # Integer) because money — same reasoning that keeps `CoinLedger`
    # off floats for `amount`, just applied to currency instead of coins.
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Coins to be credited on success. Always positive — this model only
    # represents purchases (money -> coins), never a debit; refunds are
    # a separate `CoinLedger.TransactionType.REFUND` entry, not a
    # negative row here.
    coins = models.PositiveIntegerField()

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Populated by mark_failed() — why the gateway/webhook says this
    # didn't go through, or why the auto-fail sweep gave up on it.
    failure_reason = models.CharField(max_length=255, blank=True)

    # Set only on SUCCESS, by confirm_success(). SET_NULL (not CASCADE
    # or PROTECT): if a CoinLedger row were ever administratively
    # deleted, that shouldn't cascade into deleting the purchase receipt
    # that explains it — the receipt should just lose its back-link.
    ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CoinPurchaseRequestManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # "my purchase history" — same query shape CoinLedger's
            # (user, -created_at) index serves.
            models.Index(fields=["user", "-created_at"]),
            # Supports the auto-fail sweep task's "PENDING older than
            # cutoff" query without a full-table scan.
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=Q(amount__gt=0), name="coinpurchaserequest_amount_positive"),
            CheckConstraint(condition=Q(coins__gt=0), name="coinpurchaserequest_coins_positive"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.coins} coins ({self.status}, {self.gateway_reference})"


class CoinWithdrawalRequestManager(models.Manager):
    """
    TASK 4 — the sanctioned write path for `CoinWithdrawalRequest`, same
    role `CoinPurchaseRequestManager` plays for `CoinPurchaseRequest`
    above: callers (views, admin actions) go through these methods
    instead of `.create()`/`.save()` directly.

    Money direction is the mirror image of `CoinPurchaseRequest`: a
    purchase credits coins only on success; a withdrawal debits coins
    immediately on request. This is the escrow pattern
    `liveclass.CoinWithdrawal.create_request` already uses (reference
    read for this task) — coins leave the wallet the moment the request
    is made, not when the payout is actually confirmed, specifically so
    a user can't request the same coins twice while a withdrawal is
    still pending. They only come back if the request is rejected.
    """

    def request_withdrawal(self, *, user, coins, payout_method="", payout_details=None):
        """
        Debit `coins` from `user` via `CoinLedger.objects.
        record_transaction(transaction_type=WITHDRAWAL_REQUESTED,
        amount=-coins, ...)` and create a PENDING CoinWithdrawalRequest,
        in the same DB transaction.

        [ADDED — Task 38] Enforces `CoinWithdrawalRequest.
        MIN_WITHDRAWAL_COINS` before anything else runs — a request for
        fewer coins than the floor is rejected with `ValueError` before
        the row is created or the ledger is touched, same as the
        existing `coins <= 0` guard just below it. Also snapshots
        `amount_inr = coins * COIN_TO_INR_RATE` onto the request at
        creation time, so a later change to `COIN_TO_INR_RATE` never
        silently rewrites what a past request was actually worth —
        mirrors `liveclass.CoinWithdrawal.amount_inr`'s own snapshot
        comment exactly.

        `record_transaction` raises `ValueError` for insufficient
        balance (see `CoinLedgerManager.record_transaction` above) —
        that propagates straight out of here uncaught. Because the row
        creation and the debit share one `transaction.atomic()` block,
        that ValueError rolls back BOTH: no request row is left behind
        and no partial debit happens. The view is expected to catch
        `ValueError` and turn it into a 402 (same shape as campus's
        `FeePaymentViewSet.pay`).
        """
        if coins <= 0:
            raise ValueError("Withdrawal coins must be positive.")

        if coins < self.model.MIN_WITHDRAWAL_COINS:
            raise ValueError(
                f"Withdrawal of {coins} coins is below the minimum of "
                f"{self.model.MIN_WITHDRAWAL_COINS} coins."
            )

        with transaction.atomic():
            withdrawal = self.create(
                user=user,
                coins=coins,
                amount_inr=coins * self.model.COIN_TO_INR_RATE,
                payout_method=payout_method,
                payout_details=payout_details or {},
                status=self.model.Status.PENDING,
            )
            ledger_entry = CoinLedger.objects.record_transaction(
                user=user,
                transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
                amount=-coins,
                reference=f"coin_withdrawal_request:{withdrawal.pk}",
                description="Coin withdrawal requested",
                metadata={"coin_withdrawal_request_id": withdrawal.pk},
            )
            withdrawal.debit_ledger_entry = ledger_entry
            withdrawal.save(update_fields=["debit_ledger_entry"])
            return withdrawal

    def mark_processing(self, *, withdrawal_id, reviewed_by=None):
        """
        Admin/ops moves a PENDING request into PROCESSING (payout
        initiated externally, e.g. a bank transfer submitted). No coin
        movement — the coins already left the wallet at request time.

        [ADDED — Task 38] `reviewed_by` is the admin/ops user making
        this call — the natural point to stamp `reviewed_by`/
        `reviewed_at`, since this is this model's "an admin has looked
        at this" step, the same role `liveclass.CoinWithdrawal.
        approve(admin_user)` plays there. Optional and only applied
        when not already set, so an existing caller that doesn't pass
        it yet keeps working exactly as before, and a request that was
        already reviewed (e.g. re-queued from PROCESSING) doesn't get
        its original reviewer overwritten.

        Raises `ValueError` if the request is already SUCCESS or
        REJECTED (both terminal).
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status in (self.model.Status.SUCCESS, self.model.Status.REJECTED):
                raise ValueError(
                    f"Withdrawal {withdrawal_id} is already {wr.status} — cannot move to processing."
                )
            wr.status = self.model.Status.PROCESSING
            update_fields = ["status", "updated_at"]
            if reviewed_by is not None and wr.reviewed_by_id is None:
                wr.reviewed_by = reviewed_by
                wr.reviewed_at = timezone.now()
                update_fields += ["reviewed_by", "reviewed_at"]
            wr.save(update_fields=update_fields)
            return wr

    def confirm_success(self, *, withdrawal_id):
        """
        Mark a withdrawal SUCCESS once the payout has actually gone out
        externally (bank transfer / UPI confirmed).

        No new CoinLedger row is written here — the coins were already
        debited via WITHDRAWAL_REQUESTED at request time, and a
        WITHDRAWAL_COMPLETED entry with amount=0 would violate
        `coinledger_amount_not_zero`. This only flips the request's own
        status, same as `liveclass.CoinWithdrawal.approve()`/
        `mark_paid()` not moving any coins either.

        Idempotent: already-SUCCESS is returned as-is. Raises
        `ValueError` if REJECTED — a rejected (already refunded)
        withdrawal can't retroactively be completed.
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status == self.model.Status.SUCCESS:
                return wr
            if wr.status == self.model.Status.REJECTED:
                raise ValueError(
                    f"Withdrawal {withdrawal_id} was already rejected — cannot mark success."
                )
            wr.status = self.model.Status.SUCCESS
            wr.save(update_fields=["status", "updated_at"])
            return wr

    def reject(self, *, withdrawal_id, reason="", reviewed_by=None):
        """
        Reject a PENDING/PROCESSING withdrawal and credit the coins
        back via `CoinLedger.objects.record_transaction(
        transaction_type=WITHDRAWAL_REJECTED, amount=+coins, ...)`.

        [ADDED — Task 38] `reviewed_by` is stamped the same way
        `mark_processing()` stamps it — optional, and only applied when
        `reviewed_by` isn't already set on the row, so a request
        rejected straight from PENDING (skipping `mark_processing`
        entirely) still records who made the call, without overwriting
        an earlier reviewer if one was already recorded.

        Idempotent the same way `CoinPurchaseRequestManager.
        confirm_success` is: the refund is keyed on this request's own
        id via `record_transaction`'s `reference=` argument, so a
        retried reject call (double form submit, admin double-click)
        can never credit the refund twice. Belt-and-braces: the
        `select_for_update()` row lock below also means a second call
        already sees `status == REJECTED` and returns early before it
        even reaches `record_transaction`.

        Raises `ValueError` if the request is already SUCCESS — a
        completed payout can't be un-done by calling this; that would
        need a separate manual adjustment, out of scope here (same
        carve-out `CoinPurchaseRequestManager.mark_failed` makes for an
        already-succeeded purchase).
        """
        with transaction.atomic():
            wr = self.select_for_update().get(pk=withdrawal_id)
            if wr.status == self.model.Status.REJECTED:
                return wr
            if wr.status == self.model.Status.SUCCESS:
                raise ValueError(
                    f"Withdrawal {withdrawal_id} already completed — cannot reject."
                )

            refund_entry = CoinLedger.objects.record_transaction(
                user=wr.user,
                transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
                amount=wr.coins,
                reference=f"coin_withdrawal_request_refund:{wr.pk}",
                description="Coin withdrawal rejected — coins refunded",
                metadata={"coin_withdrawal_request_id": wr.pk},
            )
            wr.status = self.model.Status.REJECTED
            wr.failure_reason = reason
            wr.refund_ledger_entry = refund_entry
            update_fields = ["status", "failure_reason", "refund_ledger_entry", "updated_at"]
            if reviewed_by is not None and wr.reviewed_by_id is None:
                wr.reviewed_by = reviewed_by
                wr.reviewed_at = timezone.now()
                update_fields += ["reviewed_by", "reviewed_at"]
            wr.save(update_fields=update_fields)
            return wr


class CoinWithdrawalRequest(models.Model):
    """
    TASK 4 — canonical "cash out coins" request for `user_profile`, the
    mirror image of `CoinPurchaseRequest` above (coins -> money instead
    of money -> coins), built on the same `CoinLedger` primitives.

    Reference read for this task was `liveclass.CoinWithdrawal`, which
    already implements this exact escrow pattern (debit at request
    time, refund on reject, no second debit/credit on completion) via
    its own `CoinTransaction` ledger. This model reproduces that same
    lifecycle but writes through `CoinLedger.objects.record_transaction()`
    instead, since `user_profile.CoinLedger` — not `liveclass.
    CoinTransaction` — is this codebase's shared, canonical coin ledger
    (see `CoinLedger`'s own docstring above). Differences from
    `liveclass.CoinWithdrawal` that are deliberate, not oversights:
      - No separate APPROVED status — this app's lifecycle is PENDING ->
        PROCESSING -> SUCCESS, or -> REJECTED from PENDING/PROCESSING.
        PROCESSING plays the same "payout initiated, not yet confirmed"
        role `liveclass.CoinWithdrawal`'s APPROVED does.
      - No CANCELLED status — `liveclass.CoinWithdrawal` lets a user
        cancel their own PENDING request; here that's just `reject()`
        called while still PENDING (see that manager method's own
        `liveclass` cross-reference in its Task 6 stub docstring on the
        `liveclass` side). Not revisited by Task 38 — out of scope for
        a fields-only pass.
      - `payout_method`/`payout_details` are still modeled as a
        choices field + JSONField, same shape as `liveclass.
        CoinWithdrawal` uses, since there's no separate saved-bank-
        detail model in this app to reference by id instead.

    [TASK 38 — RESOLVED] Diffed directly against `liveclass.
    CoinWithdrawal` now that `liveclass/models.py` has actually been
    reviewed (Task 4's docstring above deferred all three of these with
    "add them if/when an admin-facing withdrawal review UI is built").
    Findings, each mapped onto THIS model's own shape rather than
    copied field-for-field:

      - [ADDED] `MIN_WITHDRAWAL_COINS = 100` — copied as a plain class
        constant, same value and same reasoning `liveclass.
        CoinWithdrawal.MIN_WITHDRAWAL_COINS` already documents ("below
        this, a bank/UPI transfer typically costs more in fees than the
        payout itself"). Enforced in
        `CoinWithdrawalRequestManager.request_withdrawal()` — the same
        single choke point that already enforces `coins <= 0` and
        sufficient balance, so a caller can't route around the floor by
        skipping a view-level check.
      - [ADDED] `COIN_TO_INR_RATE = 1` and `amount_inr` — also copied
        from `liveclass.CoinWithdrawal` as-is (same rate, same
        "snapshotted at request time so a later rate change never
        rewrites history" reasoning, same `DecimalField(max_digits=10,
        decimal_places=2)` shape). `request_withdrawal()` computes and
        stores it once, at creation; nothing later recomputes it.
        NOTE: this app has its own separate `COIN_TO_INR_RATE` rather
        than importing `liveclass.CoinWithdrawal`'s — importing a model
        constant across apps for one integer is more coupling than the
        value is worth, and `CoinPurchaseRequest` above already sets
        the precedent of this app keeping its own parallel definitions
        (money precision, gateway field) rather than reaching into
        `liveclass`. If the two rates should always move together in
        practice, that's an ops/config concern (keep both settings in
        sync when pricing changes) rather than a code-coupling one.
      - [ADDED] `reviewed_by` / `reviewed_at` — `liveclass.
        CoinWithdrawal.reviewed_by` is a single FK stamped once, at
        `approve()`/`reject()`, whichever comes first (its lifecycle has
        no separate "PROCESSING" stage in between). This model's
        lifecycle does have that extra stage, so `reviewed_by`/
        `reviewed_at` are stamped at whichever of `mark_processing()` /
        `reject()` runs FIRST for a given request (both check `if
        wr.reviewed_by_id is None` before writing) — a request rejected
        straight from PENDING still gets a reviewer recorded, and one
        that goes PENDING -> PROCESSING -> REJECTED keeps the reviewer
        from the PROCESSING step rather than being overwritten by
        whoever calls `reject()` later. `confirm_success()` does NOT
        stamp these — by the time a request reaches SUCCESS it must
        already have passed through `mark_processing()` in the normal
        flow, and if it didn't, that's a process gap for ops to fix, not
        something for this model to paper over with a second reviewer.
        Both fields nullable/blank, same as `liveclass.CoinWithdrawal`'s
        (`on_delete=SET_NULL` so a deleted admin account doesn't cascade
        into deleting withdrawal history).

    Lifecycle: PENDING (created by `request_withdrawal`, debits `coins`
    immediately via a WITHDRAWAL_REQUESTED CoinLedger entry, and now
    also snapshots `amount_inr`) -> PROCESSING (via `mark_processing`,
    no coin movement, now also stamps `reviewed_by`/`reviewed_at` if not
    already set) -> SUCCESS (via `confirm_success`, no coin movement —
    the debit already happened) OR REJECTED (via `reject`, from PENDING
    or PROCESSING, credits `coins` back via a WITHDRAWAL_REJECTED entry,
    and stamps `reviewed_by`/`reviewed_at` if not already set). SUCCESS
    and REJECTED are both terminal.

    Nothing here writes to `User.coin` directly — same boundary
    `CoinPurchaseRequest` draws: the only sanctioned path to a balance
    change is `CoinLedger.objects.record_transaction()`.
    """

    # [ADDED — Task 38] Copied from `liveclass.CoinWithdrawal` — see the
    # class docstring's Task 38 section for why this app keeps its own
    # copy rather than importing the liveclass one.
    COIN_TO_INR_RATE = 1  # 1 coin == this many INR; adjust to match the actual coin pricing used when passes are priced
    MIN_WITHDRAWAL_COINS = 100  # below this, a bank/UPI transfer typically costs more in fees than the payout itself

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SUCCESS = "success", "Success"
        REJECTED = "rejected", "Rejected"

    class PayoutMethod(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"
        UPI = "upi", "UPI"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coin_withdrawal_requests",
    )

    coins = models.PositiveIntegerField()

    # [ADDED — Task 38] coins * COIN_TO_INR_RATE, snapshotted once by
    # `request_withdrawal()` at request time — see class docstring.
    amount_inr = models.DecimalField(max_digits=10, decimal_places=2)

    payout_method = models.CharField(max_length=20, choices=PayoutMethod.choices, blank=True)

    # Bank: {"account_holder", "account_number", "ifsc"}. UPI: {"upi_id"}.
    # Kept as JSON (not separate columns), same reasoning as
    # liveclass.CoinWithdrawal.payout_details — validated against
    # payout_method in the serializer, not here, so a new payout method
    # never needs a migration.
    payout_details = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Populated by reject() — why the withdrawal was turned down.
    failure_reason = models.CharField(max_length=255, blank=True)

    # [ADDED — Task 38] The admin/ops user who first reviewed this
    # request (via mark_processing() or reject(), whichever ran first).
    # SET_NULL so a deleted admin account doesn't cascade into deleting
    # withdrawal history — same reasoning debit_ledger_entry/
    # refund_ledger_entry already use SET_NULL for.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coin_withdrawal_requests_reviewed",
    )

    # [ADDED — Task 38] Set alongside reviewed_by, same call site(s).
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # The debit written by request_withdrawal(). SET_NULL for the same
    # reason CoinPurchaseRequest.ledger_entry is SET_NULL: an
    # administratively-deleted ledger row shouldn't cascade into
    # deleting the request that explains it.
    debit_ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    # The refund written by reject(), if any. Stays null for PENDING/
    # PROCESSING/SUCCESS requests — only ever set once reject() runs.
    refund_ledger_entry = models.ForeignKey(
        CoinLedger,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CoinWithdrawalRequestManager()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # "my withdrawal history" — same query shape CoinLedger's
            # and CoinPurchaseRequest's (user, -created_at) index serve.
            models.Index(fields=["user", "-created_at"]),
            # Supports an admin queue view's "PENDING/PROCESSING oldest
            # first" query without a full-table scan — same shape
            # CoinPurchaseRequest's (status, created_at) index serves
            # for its own sweep task.
            models.Index(fields=["status", "created_at"]),
        ]
        constraints = [
            CheckConstraint(condition=Q(coins__gt=0), name="coinwithdrawalrequest_coins_positive"),
            # [ADDED — Task 38] Mirrors coinpurchaserequest_amount_positive
            # above — amount_inr is derived from coins so it should never
            # be able to reach zero or negative either.
            CheckConstraint(condition=Q(amount_inr__gt=0), name="coinwithdrawalrequest_amount_inr_positive"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.coins} coins withdrawal ({self.status})"


class UserPreference(models.Model):
    """
    TASK 1 (this pass) — per-user UI/locale preferences: `theme` and
    `language`. Deliberately the same OneToOne + get-or-create shape as
    `core.NotificationPreference`.

    [TASK 31 — RESOLVED] Diffed directly against `core.NotificationPreference`
    now that `core/models.py` has actually been reviewed (previously an
    unconfirmed guess — see git history of this docstring for the old
    caveat). Confirmed matching on every point that was in question:

      - Classmethod name: `for_user()` on both — the guess was right,
        no rename needed.
      - get-or-create keying: both do `cls.objects.get_or_create(user=
        user)`, keyed on `user` alone, no other lookup fields.
      - Default-population behavior: neither passes a `defaults=` dict
        to `get_or_create` — both just let the model field defaults
        (`push_enabled=True` etc. there; `Theme.SYSTEM`/`language="en"`
        here) apply on first creation. Same pattern exactly.
      - Shape: `OneToOneField` to the user + an `updated_at =
        DateTimeField(auto_now=True)` on both.

    Two harmless, non-functional differences, left as-is (nothing to
    reconcile):
      - `NotificationPreference.Meta.db_table` pins it to
        `"liveclass_notificationpreference"` — a legacy-table artifact
        from that model's own history, not something this model has or
        needs (this is a fresh table with no prior name to preserve).
      - `NotificationPreference` declares no `Meta.ordering`;
        `UserPreference` orders by `-updated_at`. Irrelevant in
        practice — both are OneToOne (one row per user via
        `for_user()`), so ordering only matters for a hypothetical
        "list every user's preference row" view, which neither app has.

    Row is NOT created at signup — it's get-or-created lazily the first
    time anything calls `for_user()` (same lazy-row idea `CoinLedger`
    etc. don't need, but a preferences table specifically benefits from:
    most users never touch theme/language, so this avoids a write on
    every signup for a row most rows will just sit at defaults for).
    """

    class Theme(models.TextChoices):
        LIGHT = "light", "Light"
        DARK = "dark", "Dark"
        SYSTEM = "system", "System"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        related_name="preferences",
        on_delete=models.CASCADE,
    )

    theme = models.CharField(
        max_length=10,
        choices=Theme.choices,
        default=Theme.SYSTEM,
    )

    # ISO 639-1 code ("en", "hi", "es", ...). max_length=10 rather than
    # 2 so a locale-qualified tag ("en-US", "zh-Hans") fits too, without
    # forcing a migration the day someone needs that.
    language = models.CharField(max_length=10, default="en")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username} preferences ({self.theme}/{self.language})"

    @classmethod
    def for_user(cls, user):
        """
        Get-or-create wrapper — mirrors `core.NotificationPreference`'s
        pattern so every caller (this app's own view, or another app
        that wants to know a user's theme/language later) gets a row
        back unconditionally instead of having to branch on
        DoesNotExist for a user who's never saved a preference before.
        """
        obj, _created = cls.objects.get_or_create(user=user)
        return obj