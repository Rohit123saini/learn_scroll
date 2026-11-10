"""`python manage.py feed_retention [--dry-run] [--only post_events|seen|pii]` - H10, see post/feed_retention.py.

Runs the same prune / scrub the nightly Celery jobs run (handy for the first big backlog: run it by hand off-peak,
repeat until every table says complete=True)."""
import json

from django.core.management.base import BaseCommand

from post import feed_retention


class Command(BaseCommand):
    help = "Prune PostEvent, 'seen' PostView rows and scrub old PostView PII (time-budgeted, batched)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only count what would be touched")
        parser.add_argument("--only", choices=["post_events", "seen", "pii"], help="run a single job")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        if opts["only"]:
            fn = {"post_events": feed_retention.prune_post_events, "seen": feed_retention.prune_seen_post_views,
                  "pii": feed_retention.scrub_post_view_pii}[opts["only"]]
            results = {opts["only"]: fn(dry_run=dry)}
        else:
            results = feed_retention.run_all(dry_run=dry)
        if opts["json"]:
            self.stdout.write(json.dumps(results, indent=2, default=str))
            return
        for name, r in results.items():
            if "error" in r:
                self.stdout.write(self.style.ERROR(f"{name}: FAILED {r['error']}"))
                continue
            verb = "would touch" if dry else "touched"
            state = "" if r.get("complete", True) else "  (time budget hit - run again)"
            self.stdout.write(f"{name:<18} {verb} {r['rows']:>9} rows  cutoff {r['cutoff']}{state}")
