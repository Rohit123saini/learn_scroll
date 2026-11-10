"""
T3.10 -- wallet drift report (READ-ONLY, never changes a balance).

  python manage.py reconcile_wallets                  # summary + first 25 drifting users
  python manage.py reconcile_wallets --limit 100
  python manage.py reconcile_wallets --user-id 42
  python manage.py reconcile_wallets --fail-on-drift  # exit 1 (cron / CI)

`User.coin` is the balance. Every sanctioned change leaves a row with `balance_after` in CoinLedger or in the
legacy tuitionclass.CoinTransaction. The newest row of the two (by created_at, then id) must carry
`balance_after == User.coin`; otherwise some code path moved coins without leaving a trace (or a row was edited).
  drift       newest history row disagrees with User.coin
  unexplained User.coin > 0 but neither ledger has a single row (e.g. a signup grant written straight to the field)
"""
from django.core.management.base import BaseCommand, CommandError
from django.db.models import OuterRef, Subquery

from login.models import User
from tuitionclass.models import CoinTransaction
from user_profile.models import CoinLedger


def _latest(model, field):
    qs = model.objects.filter(user=OuterRef("pk")).order_by("-created_at", "-pk")
    return Subquery(qs.values(field)[:1])


class Command(BaseCommand):
    help = "Compare User.coin with the newest balance_after of CoinLedger / CoinTransaction (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=25, help="how many offenders to print")
        parser.add_argument("--user-id", type=int)
        parser.add_argument("--fail-on-drift", action="store_true")

    def handle(self, *args, **opts):
        users = User.objects.all()
        if opts["user_id"]:
            users = users.filter(pk=opts["user_id"])
        users = users.annotate(
            led_bal=_latest(CoinLedger, "balance_after"), led_at=_latest(CoinLedger, "created_at"),
            leg_bal=_latest(CoinTransaction, "balance_after"), leg_at=_latest(CoinTransaction, "created_at"),
        ).values("pk", "username", "coin", "led_bal", "led_at", "leg_bal", "leg_at")

        checked = drift = unexplained = 0
        offenders = []
        for u in users.iterator(chunk_size=2000):
            checked += 1
            has_led, has_leg = u["led_at"] is not None, u["leg_at"] is not None
            if not has_led and not has_leg:
                if u["coin"] > 0:
                    unexplained += 1
                    offenders.append((u, "unexplained", None, "no ledger rows at all"))
                continue
            if has_led and (not has_leg or u["led_at"] >= u["leg_at"]):
                expected, src = u["led_bal"], "CoinLedger"
            else:
                expected, src = u["leg_bal"], "CoinTransaction(legacy)"
            if expected != u["coin"]:
                drift += 1
                offenders.append((u, "drift", expected, src))

        self.stdout.write(f"users checked: {checked}   drift: {drift}   unexplained balance: {unexplained}")
        for u, kind, expected, src in offenders[: opts["limit"]]:
            self.stdout.write(f"  [{kind}] user={u['pk']} ({u['username']}) User.coin={u['coin']} newest-row balance_after={expected} via {src}")
        if len(offenders) > opts["limit"]:
            self.stdout.write(f"  ... and {len(offenders) - opts['limit']} more (raise --limit)")
        if drift == 0:
            self.stdout.write(self.style.SUCCESS("No drift between User.coin and the ledgers."))
        elif opts["fail_on_drift"]:
            raise CommandError(f"{drift} wallet(s) drifted from their ledger history")
