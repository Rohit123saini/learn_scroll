"""`python manage.py call_slo [--minutes 15] [--alert] [--json]` - X1, see message/call_ops.py.

Connect-success %, setup time, ring -> answer, reconnects and failure reasons of calls over the last N minutes vs the SLO
(settings.CALL_SLO: connect-success >= 95 %). --alert also logs the ERROR + sends the Sentry message (what the 5-minute
Celery job does). Exit code 1 when the SLO is breached."""
import json
import sys

from django.core.management.base import BaseCommand, CommandError

from message import call_ops


class Command(BaseCommand):
    help = "Call SLO status (connect-success, setup ms, ring->answer, reconnects, failure reasons) over the last N minutes."

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=15)
        parser.add_argument("--alert", action="store_true", help="log the ERROR + Sentry message on a breach")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        if opts["minutes"] < 1:
            raise CommandError("--minutes must be >= 1")
        if opts["alert"]:
            s = call_ops.check_and_alert(window_minutes=opts["minutes"])
            problems = s["problems"]
        else:
            s = call_ops.summarize_last(opts["minutes"])
            problems = call_ops.breaches(s)
        if opts["json"]:
            self.stdout.write(json.dumps(s, indent=2, default=str))
        else:
            c, r, rc, st = s["connect"], s["ring"], s["reconnect"], s["setup_ms"]
            self.stdout.write(f"Call SLO - last {opts['minutes']} min: {s['initiated']} calls started ringing")
            self.stdout.write(f"  connect-success  {c['success_pct']}%  ({c['ok']} ok / {c['failed']} failed; "
                              f"target >= {s['slo']['connect_success_target_pct']}%)")
            reasons = {k: v for k, v in c["failure_reasons"].items() if v}
            if reasons:
                self.stdout.write(f"  failure reasons  {reasons}")
            self.stdout.write(f"  setup ms         avg {st['avg']}  p50 <= {st['p50']}  p95 <= {st['p95']}  ({st['samples']} samples)")
            self.stdout.write(f"  ring outcomes    {r['outcomes']}  answer-rate {r['answer_rate_pct']}%  "
                              f"to-answer s: avg {r['to_answer_s']['avg']} p95 <= {r['to_answer_s']['p95']}")
            self.stdout.write(f"  reconnects       {rc['events']} in {rc['calls_with_reconnect']}/{rc['calls_reported']} calls, "
                              f"recovered {rc['recovered']} lost {rc['lost']}")
            for p in problems:
                self.stdout.write(self.style.ERROR(f"  BREACH: {p}"))
            if not problems:
                self.stdout.write(self.style.SUCCESS("  OK"))
        if problems:
            sys.exit(1)
