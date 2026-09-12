"""
management command: migrate_liveclass_coin_models

TASK 6 — one-time backfill of liveclass.CoinPurchase / liveclass.CoinWithdrawal
history into user_profile's CoinPurchaseRequest / CoinWithdrawalRequest +
CoinLedger, so `user_profile` becomes the single place coin purchase/
withdrawal history lives going forward (liveclass/views.py and
liveclass/models.py in this same pass turn CoinPurchase/CoinWithdrawal
read-only — see the 410-Gone stubs in CoinPurchaseViewSet/
CoinWithdrawalViewSet and the RuntimeError stubs on the models themselves).

Run as: python manage.py migrate_liveclass_coin_models [--dry-run]
                                                         [--rollback-log PATH]

=== WHY THIS NEVER TOUCHES User.coin ==============================================
The balance already reflects every historical purchase/withdrawal. This
command only writes audit-trail rows that *describe* that history in the
new tables — it never calls CoinPurchaseRequest.objects.start_purchase()/
confirm_success() or CoinWithdrawalRequest.objects.request_withdrawal(),
because those managers are the LIVE write path and apply a balance delta
as part of the call (see their own docstrings in user_profile/models.py).
Reusing them here would re-debit/re-credit coins that already moved once.
Every row below is written with a direct .objects.create(), bypassing the
managers' balance side effects entirely.
====================================================================================

=== WHERE balance_after COMES FROM =================================================
liveclass.CoinTransaction already snapshots the wallet balance at the
exact moment of every purchase/withdrawal event:
  - CoinPurchase.mark_success() writes one CoinTransaction row:
        reason=TOPUP, txn_type=CREDIT, amount=purchase.coins,
        reference_id=f"coinpurchase:{purchase.id}", balance_after=<real>
  - CoinWithdrawal.create_request() writes one CoinTransaction row at
    request time:
        reason=WITHDRAWAL, txn_type=DEBIT, amount=coins,
        reference_id=f"withdrawal:{withdrawal.id}", balance_after=<real>
  - CoinWithdrawal._refund_coins() (called from reject()/cancel()) writes a
    SECOND CoinTransaction row when the request ends up REJECTED/CANCELLED:
        reason=WITHDRAWAL_REVERSED, txn_type=CREDIT, amount=coins,
        reference_id=f"withdrawal:{withdrawal.id}", balance_after=<real>
This command reads balance_after from those rows rather than
reconstructing it — it's the real historical number, not a guess.
====================================================================================

Idempotent: re-running skips anything already migrated (checked via
CoinPurchaseRequest.gateway_reference / a liveclass_coin_withdrawal_debit:
CoinLedger.reference), so a second run after a partial failure is safe.

Per-row savepoints: one bad/inconsistent source row (e.g. a SUCCESS
CoinPurchase whose CoinTransaction is missing — a pre-existing data bug,
not something this command should paper over) is logged and skipped
without aborting the rest of the batch. Nothing already committed by an
earlier row in the run is rolled back by a later row's failure.

Rollback log: one JSON line per row this command creates, appended to
--rollback-log. To roll back, delete every user_profile.CoinLedger /
CoinPurchaseRequest / CoinWithdrawalRequest row whose id appears in the
log — none of them ever touched User.coin, so no balance repair is
needed on rollback either.

LIMITATION (documented, not silently papered over): if a CoinWithdrawal is
migrated while still PENDING/APPROVED and later resolves (PAID/REJECTED/
CANCELLED) in liveclass — which shouldn't happen once liveclass/views.py
and liveclass/models.py are both read-only, but could if this command runs
before that deploy lands — re-running this command will NOT retroactively
add the missing refund/completion side to the row it already created,
because the existence check for "already migrated" is per-withdrawal, not
per-state. Run this command only after the read-only deploy, once no
in-flight liveclass withdrawal can change state again.
"""
import json
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from liveclass.models import CoinPurchase, CoinTransaction, CoinWithdrawal
from user_profile.models import CoinLedger, CoinPurchaseRequest, CoinWithdrawalRequest

logger = logging.getLogger(__name__)


# liveclass.CoinWithdrawal.Status -> user_profile.CoinWithdrawalRequest.Status
# CANCELLED has no separate terminal state in the new model — a cancelled
# request and a rejected one are the same thing there (coins refunded,
# terminal). The distinction is preserved in failure_reason/description
# instead of a status value, so nothing about "who ended it" is lost.
WITHDRAWAL_STATUS_MAP = {
    CoinWithdrawal.Status.PENDING: CoinWithdrawalRequest.Status.PENDING,
    CoinWithdrawal.Status.APPROVED: CoinWithdrawalRequest.Status.PROCESSING,
    CoinWithdrawal.Status.PAID: CoinWithdrawalRequest.Status.SUCCESS,
    CoinWithdrawal.Status.REJECTED: CoinWithdrawalRequest.Status.REJECTED,
    CoinWithdrawal.Status.CANCELLED: CoinWithdrawalRequest.Status.REJECTED,
}


class MigrationRowError(Exception):
    """Raised for a single source row that can't be migrated — caught by
    the per-row savepoint loop so it doesn't abort the whole batch."""


class Command(BaseCommand):
    help = (
        "One-time backfill of liveclass CoinPurchase/CoinWithdrawal history "
        "into user_profile's CoinPurchaseRequest/CoinWithdrawalRequest + "
        "CoinLedger. Idempotent — safe to re-run; already-migrated rows are "
        "skipped. Never writes to User.coin. Writes a rollback log of every "
        "row it creates."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be migrated without writing anything.",
        )
        parser.add_argument(
            "--rollback-log",
            default="migrate_liveclass_coin_models.rollback.jsonl",
            help="Path to append one JSON line per row created, for rollback.",
        )

    # ------------------------------------------------------------------
    # CoinPurchase -> CoinPurchaseRequest + CoinLedger
    # ------------------------------------------------------------------
    def _migrate_purchase(self, purchase, dry_run, rollback_entries):
        gateway_reference = purchase.order_id
        if CoinPurchaseRequest.objects.filter(gateway_reference=gateway_reference).exists():
            return "skipped"

        source_txn = CoinTransaction.objects.filter(
            user_id=purchase.user_id,
            reason=CoinTransaction.Reason.TOPUP,
            reference_id=f"coinpurchase:{purchase.id}",
        ).first()
        if source_txn is None:
            raise MigrationRowError(
                f"CoinPurchase {purchase.pk}: no matching CoinTransaction "
                f"(reason=TOPUP, reference_id=coinpurchase:{purchase.pk}) — "
                "cannot determine historical balance_after. Skipped, not guessed."
            )

        if dry_run:
            return "created"

        with transaction.atomic():
            ledger_entry = CoinLedger.objects.create(
                user_id=purchase.user_id,
                transaction_type=CoinLedger.TransactionType.PURCHASE,
                amount=source_txn.amount,
                balance_after=source_txn.balance_after,
                reference=f"liveclass_coin_purchase:{purchase.pk}",
                description="Migrated from liveclass.CoinPurchase",
                metadata={
                    "migrated_from": "liveclass.CoinPurchase",
                    "source_id": purchase.pk,
                    "gateway_reference": gateway_reference,
                    "source_coin_transaction_id": source_txn.pk,
                },
            )
            CoinLedger.objects.filter(pk=ledger_entry.pk).update(
                created_at=purchase.verified_at or purchase.created_at
            )

            req = CoinPurchaseRequest.objects.create(
                user_id=purchase.user_id,
                gateway="razorpay",
                gateway_reference=gateway_reference,
                amount=purchase.amount_inr,
                coins=purchase.coins,
                status=CoinPurchaseRequest.Status.SUCCESS,
                ledger_entry=ledger_entry,
            )
            CoinPurchaseRequest.objects.filter(pk=req.pk).update(
                created_at=purchase.created_at,
                updated_at=purchase.verified_at or purchase.created_at,
            )

        rollback_entries.append({
            "table": "CoinPurchaseRequest", "id": req.pk,
            "ledger_entry_id": ledger_entry.pk,
            "source": "liveclass.CoinPurchase", "source_id": purchase.pk,
        })
        return "created"

    # ------------------------------------------------------------------
    # CoinWithdrawal -> CoinWithdrawalRequest + CoinLedger
    # ------------------------------------------------------------------
    def _migrate_withdrawal(self, withdrawal, dry_run, rollback_entries):
        debit_reference = f"liveclass_coin_withdrawal_debit:{withdrawal.pk}"
        if CoinLedger.objects.filter(reference=debit_reference).exists():
            return "skipped"

        debit_txn = CoinTransaction.objects.filter(
            user_id=withdrawal.user_id,
            reason=CoinTransaction.Reason.WITHDRAWAL,
            reference_id=f"withdrawal:{withdrawal.id}",
        ).first()
        if debit_txn is None:
            raise MigrationRowError(
                f"CoinWithdrawal {withdrawal.pk}: no matching debit "
                f"CoinTransaction (reason=WITHDRAWAL, reference_id="
                f"withdrawal:{withdrawal.pk}) — cannot determine historical "
                "balance_after. Skipped, not guessed."
            )

        new_status = WITHDRAWAL_STATUS_MAP.get(withdrawal.status)
        if new_status is None:
            raise MigrationRowError(
                f"CoinWithdrawal {withdrawal.pk}: unrecognized status "
                f"{withdrawal.status!r} — not in WITHDRAWAL_STATUS_MAP."
            )

        refund_txn = None
        if withdrawal.status in (CoinWithdrawal.Status.REJECTED, CoinWithdrawal.Status.CANCELLED):
            refund_txn = CoinTransaction.objects.filter(
                user_id=withdrawal.user_id,
                reason=CoinTransaction.Reason.WITHDRAWAL_REVERSED,
                reference_id=f"withdrawal:{withdrawal.id}",
            ).first()
            if refund_txn is None:
                raise MigrationRowError(
                    f"CoinWithdrawal {withdrawal.pk}: status is "
                    f"{withdrawal.status} but no matching refund "
                    "CoinTransaction (reason=WITHDRAWAL_REVERSED) was found "
                    "— cannot determine historical balance_after for the "
                    "refund. Skipped, not guessed."
                )

        if dry_run:
            return "created"

        failure_reason = ""
        if withdrawal.status == CoinWithdrawal.Status.CANCELLED:
            failure_reason = withdrawal.admin_note or "Cancelled by user (liveclass)."
        elif withdrawal.status == CoinWithdrawal.Status.REJECTED:
            failure_reason = withdrawal.admin_note

        with transaction.atomic():
            debit_entry = CoinLedger.objects.create(
                user_id=withdrawal.user_id,
                transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REQUESTED,
                amount=-withdrawal.coins,
                balance_after=debit_txn.balance_after,
                reference=debit_reference,
                description="Migrated from liveclass.CoinWithdrawal (request)",
                metadata={
                    "migrated_from": "liveclass.CoinWithdrawal",
                    "source_id": withdrawal.pk,
                    "source_coin_transaction_id": debit_txn.pk,
                },
            )
            CoinLedger.objects.filter(pk=debit_entry.pk).update(created_at=withdrawal.requested_at)

            refund_entry = None
            if refund_txn is not None:
                refund_entry = CoinLedger.objects.create(
                    user_id=withdrawal.user_id,
                    transaction_type=CoinLedger.TransactionType.WITHDRAWAL_REJECTED,
                    amount=withdrawal.coins,
                    balance_after=refund_txn.balance_after,
                    reference=f"liveclass_coin_withdrawal_refund:{withdrawal.pk}",
                    description="Migrated from liveclass.CoinWithdrawal (refund)",
                    metadata={
                        "migrated_from": "liveclass.CoinWithdrawal",
                        "source_id": withdrawal.pk,
                        "source_coin_transaction_id": refund_txn.pk,
                    },
                )
                backdate = withdrawal.reviewed_at or withdrawal.requested_at
                CoinLedger.objects.filter(pk=refund_entry.pk).update(created_at=backdate)

            req = CoinWithdrawalRequest.objects.create(
                user_id=withdrawal.user_id,
                coins=withdrawal.coins,
                payout_method=withdrawal.payout_method,
                payout_details=withdrawal.payout_details,
                status=new_status,
                failure_reason=failure_reason,
                debit_ledger_entry=debit_entry,
                refund_ledger_entry=refund_entry,
            )
            backdated_updated_at = withdrawal.paid_at or withdrawal.reviewed_at or withdrawal.requested_at
            CoinWithdrawalRequest.objects.filter(pk=req.pk).update(
                created_at=withdrawal.requested_at, updated_at=backdated_updated_at
            )

        rollback_entries.append({
            "table": "CoinWithdrawalRequest", "id": req.pk,
            "debit_ledger_entry_id": debit_entry.pk,
            "refund_ledger_entry_id": refund_entry.pk if refund_entry else None,
            "source": "liveclass.CoinWithdrawal", "source_id": withdrawal.pk,
        })
        return "created"

    # ------------------------------------------------------------------
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        rollback_log_path = options["rollback_log"]

        counts = {"purchase_created": 0, "purchase_skipped": 0,
                  "withdrawal_created": 0, "withdrawal_skipped": 0}
        errors = []
        rollback_entries = []

        purchases = CoinPurchase.objects.filter(
            status=CoinPurchase.Status.SUCCESS
        ).order_by("created_at").iterator()
        for purchase in purchases:
            try:
                with transaction.atomic():
                    result = self._migrate_purchase(purchase, dry_run, rollback_entries)
            except MigrationRowError as exc:
                errors.append(str(exc))
                continue
            counts["purchase_created" if result == "created" else "purchase_skipped"] += 1

        withdrawals = CoinWithdrawal.objects.all().order_by("requested_at").iterator()
        for withdrawal in withdrawals:
            try:
                with transaction.atomic():
                    result = self._migrate_withdrawal(withdrawal, dry_run, rollback_entries)
            except MigrationRowError as exc:
                errors.append(str(exc))
                continue
            counts["withdrawal_created" if result == "created" else "withdrawal_skipped"] += 1

        if not dry_run and rollback_entries:
            with open(rollback_log_path, "a") as f:
                for entry in rollback_entries:
                    f.write(json.dumps(entry) + "\n")

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            f"{prefix}Purchases: {counts['purchase_created']} created, "
            f"{counts['purchase_skipped']} already migrated"
        )
        self.stdout.write(
            f"{prefix}Withdrawals: {counts['withdrawal_created']} created, "
            f"{counts['withdrawal_skipped']} already migrated"
        )
        if errors:
            self.stdout.write(self.style.WARNING(f"{len(errors)} row(s) skipped:"))
            for e in errors:
                self.stdout.write(self.style.WARNING(f"  - {e}"))
        if not dry_run:
            self.stdout.write(
                f"Rollback log: {rollback_log_path} ({len(rollback_entries)} row(s) logged)"
            )