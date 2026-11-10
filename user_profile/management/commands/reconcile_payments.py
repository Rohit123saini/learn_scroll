"""P-G2: settle PENDING online payments whose webhook was lost (coin top-ups + campus fees).

    python manage.py reconcile_payments --dry-run      # report only, changes nothing
    python manage.py reconcile_payments                # settle / expire (same code the 15-min Celery task runs)
    python manage.py reconcile_payments --only coins   # or: --only fees
"""
import json

from django.core.management.base import BaseCommand

from common import payment_reconcile as pr


class Command(BaseCommand):
    help = "Ask Razorpay about PENDING coin purchases / campus fee payments and settle the ones that were paid."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report what would happen, write nothing.")
        parser.add_argument("--only", choices=["coins", "fees"], help="Only one kind.")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        if opts.get("only") == "coins":
            out = {"coin_purchase": pr.reconcile_coin_purchases(dry_run=dry)}
        elif opts.get("only") == "fees":
            out = {"campus_fee": pr.reconcile_fee_payments(dry_run=dry)}
        else:
            out = pr.run_all(dry_run=dry)
        self.stdout.write(json.dumps(out, indent=2, default=str))
