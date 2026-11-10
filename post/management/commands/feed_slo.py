"""`python manage.py feed_slo [--minutes 15] [--alert] [--json]` - H10, see post/feed_ops.py.

Availability / error rate / p95 / degraded rate of GET /post/feed/ over the last N minutes vs the SLO
(settings.FEED_SLO: availability >= 99.5 %, p95 <= 800 ms). --alert also logs the ERROR + sends the Sentry message
(what the 5-minute Celery job does). Exit code 1 when an SLO is breached (usable from a cron / CI smoke check)."""
import json
import sys

from django.core.management.base import BaseCommand, CommandError

from post import feed_ops


class Command(BaseCommand):
    help = "Feed SLO status (availability, p95, error / degraded rate) over the last N minutes."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=15)
        parser.add_argument("--alert", action="store_true", help="log the ERROR + Sentry message on a breach")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        if opts["minutes"] < 1:
            raise CommandError("--minutes must be >= 1")
        if opts["alert"]:
            s = feed_ops.check_and_alert(window_minutes=opts["minutes"])
            problems = s["problems"]
        else:
            s = feed_ops.summarize_last(opts["minutes"])
            problems = feed_ops.breaches(s)
        if opts["json"]:
            self.stdout.write(json.dumps(s, indent=2, default=str))
        else:
            slo = s["slo"]
            self.stdout.write(f"Feed SLO - last {opts['minutes']} min: {s['requests']} requests")
            self.stdout.write(f"  availability  {s['availability_pct']}%   (target >= {slo['availability_target_pct']}%)")
            self.stdout.write(f"  p95           <= {s['p95_ms']} ms   (target <= {slo['p95_target_ms']} ms; "
                              f"{s['within_p95_target_pct']}% within target)")
            self.stdout.write(f"  errors        {s['errors']} ({s['error_rate_pct']}%)   "
                              f"degraded {s['degraded']} ({s['degraded_rate_pct']}%)")
            ev = {k: v for k, v in s["events"].items() if v}
            if ev:
                self.stdout.write(f"  events        {ev}")
            for p in problems:
                self.stdout.write(self.style.ERROR(f"  BREACH: {p}"))
            if not problems:
                self.stdout.write(self.style.SUCCESS("  OK"))
        if problems:
            sys.exit(1)
