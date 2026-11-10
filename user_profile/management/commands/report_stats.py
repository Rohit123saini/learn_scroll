"""
python manage.py report_stats [--days 30]

Audit item 19: the report thresholds (REPORT_HOLD_THRESHOLD_SEVERE / _ANY) were guesses.
This command shows what REAL reports did so you can tune them:

  * how many reports per reason, and what share ended up actioned / dismissed
  * of all auto-holds, how many staff later confirmed (actioned) vs. overturned (dismissed)
    -> the "precision" of the current thresholds
  * appeals: how many holds were appealed and how many appeals were accepted
  * brigading: reports / targets where the brigading guard fired

Rule of thumb printed at the end: precision < 60% -> raise the threshold (too many wrong holds);
precision > 90% and many reports still waiting for a person -> you can lower it.
Needs at least ~30 decided holds before the numbers mean anything - it says so when it doesn't have them.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.utils import timezone

from user_profile.models import AutoModerationFlag, ContentReport, ModerationAction, ModerationAppeal


def _pct(a, b):
    return f"{(100.0 * a / b):.0f}%" if b else "-"


class Command(BaseCommand):
    help = "Report volume, hold precision, appeals and brigading stats - for tuning REPORT_* thresholds."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **opts):
        since = timezone.now() - timedelta(days=opts["days"])
        out = self.stdout.write
        reports = ContentReport.objects.filter(created_at__gte=since)
        out(f"=== Reports in the last {opts['days']} days: {reports.count()} ===")
        out(f"{'reason':<15}{'total':>7}{'open':>7}{'actioned':>10}{'dismissed':>11}{'action rate':>13}")
        rows = reports.values("reason").annotate(
            n=Count("id"), o=Count("id", filter=Q(status__in=("open", "reviewed"))),
            a=Count("id", filter=Q(status="actioned")), d=Count("id", filter=Q(status="dismissed")),
        ).order_by("-n")
        for r in rows:
            out(f"{r['reason']:<15}{r['n']:>7}{r['o']:>7}{r['a']:>10}{r['d']:>11}{_pct(r['a'], r['a'] + r['d']):>13}")

        flags = AutoModerationFlag.objects.filter(source="reports", created_at__gte=since, text_hash="user-reports")
        actioned = flags.filter(status="actioned").count()
        dismissed = flags.filter(status="dismissed").count()
        waiting = flags.filter(status__in=("open", "reviewed")).count()
        decided = actioned + dismissed
        out("")
        out(f"=== Auto-holds from reports: {flags.count()} (confirmed {actioned}, overturned {dismissed}, waiting {waiting}) ===")
        out(f"precision of current thresholds: {_pct(actioned, decided)}  (decided: {decided})")

        brigade = AutoModerationFlag.objects.filter(source="reports", created_at__gte=since, text_hash="user-reports-brigade")
        out("")
        out(f"=== Brigading guard fired on {brigade.count()} target(s) "
            f"(confirmed real {brigade.filter(status='actioned').count()}, "
            f"confirmed brigade {brigade.filter(status='dismissed').count()}, "
            f"waiting {brigade.filter(status__in=('open', 'reviewed')).count()}) ===")
        out(f"reports marked brigading_suspected: {reports.filter(brigading_suspected=True).count()}")

        holds = ModerationAction.objects.filter(created_at__gte=since, action__in=("auto_hold", "hide"))
        appealed = ModerationAppeal.objects.filter(created_at__gte=since)
        acc = appealed.filter(status="accepted").count()
        out("")
        out(f"=== Appeals: {appealed.count()} on {holds.count()} hold/hide decisions "
            f"(accepted {acc}, rejected {appealed.filter(status='rejected').count()}, open {appealed.filter(status='open').count()}) ===")

        out("")
        if decided < 30:
            out("Not enough decided holds yet (<30) - don't change thresholds, keep collecting.")
        elif decided and actioned / decided < 0.6:
            out("Precision < 60%: too many wrong holds -> RAISE REPORT_HOLD_THRESHOLD_SEVERE / _ANY by 1-2.")
        elif decided and actioned / decided > 0.9 and waiting > 0:
            out("Precision > 90% with reports still waiting: you can LOWER the thresholds by 1.")
        else:
            out("Precision looks reasonable - keep the current thresholds and re-check next month.")
