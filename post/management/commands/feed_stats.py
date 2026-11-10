"""H2: read / reset the staging feed counters (post/feed_stats.py).

    python manage.py feed_stats --reset                                   # right BEFORE the Locust run
    python manage.py feed_stats --json --out loadtest/out/feed_stats.json  # right AFTER it

Needs the web workers to run with FEED_LOADTEST_STATS=1, otherwise the counters stay at 0.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from post import feed_stats


class Command(BaseCommand):
    help = "Show or reset the feed cache hit-rate / SQL-per-page counters used by the H2 load test."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Zero every counter and mark the Redis baseline.")
        parser.add_argument("--json", action="store_true", help="Print JSON instead of a table.")
        parser.add_argument("--out", default="", help="Also write the JSON to this file (e.g. loadtest/out/feed_stats.json).")

    @staticmethod
    def _pct(rate):
        return "n/a" if rate is None else f"{rate * 100:.1f}%"

    def handle(self, *args, **opts):
        if opts["reset"]:
            feed_stats.reset()
            baseline = feed_stats.mark_redis_baseline()
            self.stdout.write(self.style.SUCCESS(
                "feed counters reset" + (" + Redis baseline marked." if baseline else
                                         " (Redis INFO unavailable - cache is not django-redis, server-wide rate will be skipped).")
            ))
            return

        data = feed_stats.read()
        data["redis_server_wide"] = feed_stats.redis_delta()
        data["counters_enabled_in_this_process"] = feed_stats.enabled()
        if not any(v["requests"] for v in data["queries_per_page"].values()) and not data["feed_cache_hit_rate"]:
            self.stderr.write(self.style.WARNING(
                "All counters are 0 - were the web workers started with FEED_LOADTEST_STATS=1, and did the load test hit them?"
            ))

        if opts["out"]:
            path = Path(opts["out"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.stderr.write(f"written {path}")
        if opts["json"]:
            self.stdout.write(json.dumps(data, indent=2))
            return

        self.stdout.write("Feed caches (hit = Redis had it)")
        for name, c in data["caches"].items():
            label = {"cand": "candidate cache (30 s)", "snap": "snapshot (cursor pages)"}[name]
            self.stdout.write(f"  {label:<26} hits={c['hits']:<8} misses={c['misses']:<8} hit-rate={self._pct(c['hit_rate'])}")
        self.stdout.write(f"  feed-cache hit-rate (both)  {self._pct(data['feed_cache_hit_rate'])}")
        rs = data["redis_server_wide"]
        if rs:
            self.stdout.write(f"  Redis server-wide keyspace  hits={rs['hits']} misses={rs['misses']} "
                              f"hit-rate={self._pct(rs['hit_rate'])}")
        self.stdout.write("DB queries per feed page")
        for label, q in data["queries_per_page"].items():
            avg = "n/a" if q["avg_queries"] is None else f"{q['avg_queries']:.2f}"
            self.stdout.write(f"  {label:<10} requests={q['requests']:<8} avg queries/page={avg}")
        self.stdout.write(f"  all pages  avg queries/page={data['avg_queries_all_pages']}")
