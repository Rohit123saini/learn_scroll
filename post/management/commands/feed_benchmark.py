"""Home-feed latency + query-count benchmark (T1 item 8).

    python manage.py feed_benchmark --username alice --runs 30 --page-size 20 [--target-ms 300] [--cold]

Calls the REAL view (`HomeFeedView`) in-process as the given user, so it measures what the
app sees minus the network. `--cold` bypasses the candidate cache (?refresh=1 every run);
without it the first run is cold and the rest hit the 30 s candidate cache. Reports p50 /
p95 / max and the number of SQL queries of the cold and the warm request (paging with
the returned cursor is measured as well). Exit code 1 when p95 > --target-ms, so it can
gate a deploy. Run it against production-sized data - an empty dev DB proves nothing.
"""
import time

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(pct / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[k]


class Command(BaseCommand):
    help = "p50/p95 latency and SQL query count of GET /post/feed/ for one user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--runs", type=int, default=30)
        parser.add_argument("--page-size", type=int, default=20)
        parser.add_argument("--target-ms", type=float, default=300.0)
        parser.add_argument("--cold", action="store_true", help="Bypass the candidate cache on every run.")

    def handle(self, *args, **opts):
        from post.views import HomeFeedView

        User = get_user_model()
        try:
            user = User.objects.get(username=opts["username"])
        except User.DoesNotExist:
            raise CommandError("no such user")
        factory, view = APIRequestFactory(), HomeFeedView.as_view()

        def call(params):
            request = factory.get("/post/feed/", params)
            force_authenticate(request, user=user)
            with CaptureQueriesContext(connection) as ctx:
                t0 = time.perf_counter()
                response = view(request)
                response.render()
                ms = (time.perf_counter() - t0) * 1000
            return response, ms, len(ctx)

        times, first_q, warm_q = [], None, None
        for i in range(max(1, opts["runs"])):
            params = {"page_size": opts["page_size"]}
            if opts["cold"]:
                params["refresh"] = 1
            response, ms, queries = call(params)
            if response.status_code != 200:
                raise CommandError(f"feed returned {response.status_code}")
            times.append(ms)
            if i == 0:
                first_q = queries
            elif warm_q is None:
                warm_q = queries

        # page 2 through the cursor of the last response
        page2_ms = page2_q = None
        nxt = (response.data or {}).get("next")
        if nxt:
            from urllib.parse import parse_qs, urlparse

            cursor = parse_qs(urlparse(nxt).query).get("cursor", [None])[0]
            if cursor:
                _, page2_ms, page2_q = call({"page_size": opts["page_size"], "cursor": cursor})

        p95 = percentile(times, 95)
        self.stdout.write(f"runs={len(times)} page_size={opts['page_size']} cold_only={opts['cold']}")
        self.stdout.write(f"p50={percentile(times, 50):.1f}ms p95={p95:.1f}ms max={max(times):.1f}ms")
        self.stdout.write(f"queries: first(cold)={first_q} warm={warm_q}")
        if page2_ms is not None:
            self.stdout.write(f"page 2 (cursor): {page2_ms:.1f}ms, {page2_q} queries")
        ok = p95 <= opts["target_ms"]
        self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(
            f"{'PASS' if ok else 'FAIL'}: p95 {p95:.1f}ms vs target {opts['target_ms']:.0f}ms"))
        if not ok:
            raise SystemExit(1)
