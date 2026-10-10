"""`python manage.py feed_metrics [--days N] [--surface feed] [--json]` - see post/feed_metrics.py."""
import json
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from post import feed_metrics


class Command(BaseCommand):
    help = "Feed quality metrics (CTR, dwell, session length, show-fewer rate, diversity) per A/B variant."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=1, help="look back this many days (default 1)")
        parser.add_argument("--surface", default="feed", choices=["feed", "reels", "profile", "explore"])
        parser.add_argument("--gap-minutes", type=int, default=feed_metrics.DEFAULT_GAP_MINUTES)
        parser.add_argument("--max-rows", type=int, default=feed_metrics.DEFAULT_MAX_ROWS)
        parser.add_argument("--json", action="store_true", help="print JSON instead of a table")

    def handle(self, *args, **opts):
        if opts["days"] < 1:
            raise CommandError("--days must be >= 1")
        end = timezone.now()
        data = feed_metrics.compute_metrics(
            end - timedelta(days=opts["days"]), end, opts["surface"], opts["gap_minutes"], opts["max_rows"],
        )
        if opts["json"]:
            self.stdout.write(json.dumps(data, indent=2))
            return
        self.stdout.write(f"Feed metrics - surface={opts['surface']} last {opts['days']} day(s), experiment {data['experiment']}")
        if data["truncated"]:
            self.stdout.write(self.style.WARNING("  (row cap hit - numbers are for the first rows only; raise --max-rows)"))
        blocks = [("all", data)] + list(data.get("by_variant", {}).items())
        cols = [
            ("users", "users"), ("impressions", "impr"), ("ctr", "CTR"), ("avg_dwell_ms", "dwell ms"),
            ("long_dwell_rate", "long dwell"), ("quick_skip_rate", "quick skip"), ("avg_session_seconds", "sess s"),
            ("show_fewer_per_1000", "fewer/1k"),
        ]
        self.stdout.write("  " + f"{'variant':<12}" + "".join(f"{label:>12}" for _, label in cols)
                          + f"{'uniq auth':>12}{'same-back':>12}")
        for name, b in blocks:
            line = f"  {name:<12}" + "".join(f"{b[k]:>12}" for k, _ in cols)
            line += f"{b['diversity']['unique_author_ratio']:>12}{b['diversity']['same_author_back_to_back_rate']:>12}"
            self.stdout.write(line)
