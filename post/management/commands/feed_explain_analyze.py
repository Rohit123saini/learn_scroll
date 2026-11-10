"""H2: EXPLAIN (ANALYZE, BUFFERS) the feed's slow-query suspects on a real (staging) Postgres.

    python manage.py feed_explain_analyze --username lt_user_0001 --confirm-staging [--out loadtest/out] [--top 8]

What it does (read-only: only SELECTs are executed, each wrapped in a transaction that is rolled back):

  1. get_seen_post_ids(user)                         -> the PostView lookup
  2. _home_base_qs(user)                             -> the page fetch (`id IN (...)`) and the plain public/newest scan
  3. build_pool_ids(user, ...)                       -> every statement the pool build runs (following / trending /
                                                        recommended / exploration / signal loaders); the slowest --top
                                                        are explained

It runs the REAL functions with the real arguments the view passes, captures the SQL Django sent, then re-runs each
statement as `EXPLAIN (ANALYZE, BUFFERS)`. So the plan is for exactly the query the app issues (including the big
`id IN (<seen ids>)` literals), not for a hand-written approximation.

Output in --out (default loadtest/out):
    explain_NN_<label>.txt     SQL (long literal lists elided) + full plan
    explain_summary.json       per statement: exec/planning ms, seq scans, sort spills, heap fetches, buffers, advice

Use the summary to decide whether an index from loadtest/sql/feed_candidate_indexes.sql is really needed:
a Seq Scan on `posts` / `post_views` that removes many rows, or a Sort that spills to disk, is the signal.
"""
import json
import os
import re
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32}")
BIG_TABLES = ("posts", "post_views", "post_likes", "post_hides", "user_profile_follow")  # watch these for seq scans


def parse_plan(text: str) -> dict:
    """Pull the numbers that matter out of EXPLAIN ANALYZE text output (pure; unit-testable)."""
    def num(pattern, cast=float):
        m = re.search(pattern, text)
        return cast(m.group(1)) if m else None

    seq_scans = []
    for m in re.finditer(r"Seq Scan on (\w+)(?: \w+)?\s+\(cost=[^)]*rows=(\d+)[^)]*\)(?: \(actual time=[^)]*rows=(\d+))?", text):
        seq_scans.append({"table": m.group(1), "est_rows": int(m.group(2)), "actual_rows": int(m.group(3) or 0)})
    removed = sum(int(x.replace(",", "")) for x in re.findall(r"Rows Removed by Filter: ([\d,]+)", text))
    buffers = re.search(r"Buffers: shared (?:hit=(\d+))?(?: ?read=(\d+))?", text)
    return {
        "execution_ms": num(r"Execution Time: ([\d.]+) ms"),
        "planning_ms": num(r"Planning Time: ([\d.]+) ms"),
        "seq_scans": seq_scans,
        "rows_removed_by_filter": removed,
        "sort_spilled_to_disk": "Sort Method: external" in text,
        "heap_fetches": sum(int(x) for x in re.findall(r"Heap Fetches: (\d+)", text)),
        "shared_hit_blocks": int(buffers.group(1) or 0) if buffers else None,
        "shared_read_blocks": int(buffers.group(2) or 0) if buffers else None,
    }


def advise(plan: dict, sql: str) -> list:
    """Human hints from a parsed plan. They are hints, not verdicts - always read the plan itself."""
    out = []
    for s in plan["seq_scans"]:
        if s["table"] in BIG_TABLES and (s["actual_rows"] > 10_000 or plan["rows_removed_by_filter"] > 50_000):
            out.append(f"Seq Scan on {s['table']} ({s['actual_rows']} rows read, {plan['rows_removed_by_filter']} removed by filter) "
                       f"-> index candidate, see loadtest/sql/feed_candidate_indexes.sql")
    if plan["sort_spilled_to_disk"]:
        out.append("Sort spilled to disk -> the ranked pool is bigger than work_mem; cap the pool earlier or raise work_mem for this role")
    if plan["heap_fetches"] > 5_000:
        out.append(f"{plan['heap_fetches']} heap fetches -> an index-only scan is not paying off (VACUUM, or a covering INCLUDE index)")
    n_uuid = len(UUID_RE.findall(sql))
    if n_uuid > 500:
        out.append(f"{n_uuid} id literals inlined in one statement (seen ids) -> parse/plan cost scales with the seen cap; "
                   f"consider `= ANY(%s)` with an array parameter or a smaller FEED_SEEN_CAP")
    if plan["shared_read_blocks"] and plan["shared_hit_blocks"] is not None and plan["shared_read_blocks"] > plan["shared_hit_blocks"]:
        out.append("more blocks read from disk than from shared_buffers -> run it a 2nd time (cold cache) before judging")
    return out


def short_sql(sql: str, limit: int = 3500) -> str:
    """Elide runs of uuid literals so the file stays readable."""
    n = len(UUID_RE.findall(sql))
    if n > 20:
        sql = re.sub(r"(?:'?(?:%s)'?(?:::uuid)?,\s*){20,}" % UUID_RE.pattern, f"'<...{n} id literals...>', ", sql)
    return sql if len(sql) <= limit else sql[:limit] + f"\n... [{len(sql) - limit} more chars]"


class Command(BaseCommand):
    help = "EXPLAIN (ANALYZE, BUFFERS) _home_base_qs, build_pool_ids and get_seen_post_ids for one user (Postgres only)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--out", default="loadtest/out")
        parser.add_argument("--top", type=int, default=8, help="explain the N slowest statements of build_pool_ids")
        parser.add_argument("--confirm-staging", action="store_true")
        parser.add_argument("--i-know-this-is-production", action="store_true")

    def handle(self, *args, **opts):
        if not opts["confirm_staging"]:
            raise CommandError("Refusing to run without --confirm-staging (EXPLAIN ANALYZE really executes the queries).")
        env = (os.environ.get("DJANGO_ENV") or os.environ.get("ENVIRONMENT") or "").lower()
        if env in ("production", "prod") and not opts["i_know_this_is_production"]:
            raise CommandError("Environment looks like production. Use a staging database.")
        if connection.vendor != "postgresql":
            raise CommandError(f"Needs PostgreSQL (this DB is {connection.vendor}) - EXPLAIN ANALYZE output is only meaningful there.")

        from post import feed_mix
        from post.views import _home_base_qs, _video_and_velocity_boost
        from user_profile.models import Follow

        User = get_user_model()
        try:
            user = User.objects.get(username=opts["username"])
        except User.DoesNotExist:
            raise CommandError("no such user")

        following_ids = set(
            Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True)
        )
        groups = []  # (label, [captured query dicts])

        # 1) get_seen_post_ids ------------------------------------------------
        with CaptureQueriesContext(connection) as ctx:
            seen_ids = feed_mix.get_seen_post_ids(user)
        groups.append(("get_seen_post_ids", list(ctx.captured_queries)))

        # 2) _home_base_qs ----------------------------------------------------
        page_ids = list(_home_base_qs(user).filter(visibility="public").order_by("-created_at").values_list("id", flat=True)[:20])
        with CaptureQueriesContext(connection) as ctx:
            list(_home_base_qs(user).filter(id__in=page_ids))  # HomeFeedView._serialize_slices page fetch
        groups.append(("home_base_qs_page_fetch", list(ctx.captured_queries)))
        with CaptureQueriesContext(connection) as ctx:
            list(_home_base_qs(user).filter(visibility="public").order_by("-created_at")[:60])  # plain newest-public scan
        groups.append(("home_base_qs_public_newest", list(ctx.captured_queries)))

        # 3) build_pool_ids ---------------------------------------------------
        ratios = feed_mix.get_ratios(None)
        with CaptureQueriesContext(connection) as ctx:
            feed_mix.build_pool_ids(
                user, _home_base_qs(user), following_ids, _video_and_velocity_boost,
                seen_ids=seen_ids, ratios=ratios, meta={},
            )
        pool_queries = [q for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith("SELECT")]
        pool_queries.sort(key=lambda q: float(q.get("time") or 0), reverse=True)
        self.stdout.write(f"build_pool_ids ran {len(ctx.captured_queries)} statements; explaining the slowest {opts['top']}.")
        groups.append(("build_pool_ids", pool_queries[: max(1, opts["top"])]))

        out_dir = Path(opts["out"])
        out_dir.mkdir(parents=True, exist_ok=True)
        summary, n = [], 0
        for label, queries in groups:
            for q in queries:
                sql = q["sql"]
                if not sql.lstrip().upper().startswith("SELECT"):
                    continue
                n += 1
                entry = {"n": n, "group": label, "django_ms": round(float(q.get("time") or 0) * 1000, 2)}
                try:
                    with transaction.atomic():
                        with connection.cursor() as cur:
                            cur.execute("SET LOCAL statement_timeout = 60000")
                            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + sql)
                            plan_text = "\n".join(row[0] for row in cur.fetchall())
                except Exception as exc:  # one bad statement must not lose the rest
                    plan_text, entry["error"] = f"EXPLAIN failed: {exc}", str(exc)[:300]
                    plan = {"execution_ms": None, "seq_scans": [], "rows_removed_by_filter": 0, "sort_spilled_to_disk": False,
                            "heap_fetches": 0, "shared_hit_blocks": None, "shared_read_blocks": None, "planning_ms": None}
                else:
                    plan = parse_plan(plan_text)
                entry.update(plan)
                entry["advice"] = advise(plan, sql)
                entry["file"] = f"explain_{n:02d}_{label}.txt"
                summary.append(entry)
                (out_dir / entry["file"]).write_text(
                    f"-- group: {label}\n-- user: {user.username} (follows {len(following_ids)}, seen ids {len(seen_ids)})\n\n"
                    f"{short_sql(sql)}\n\n{plan_text}\n", encoding="utf-8")
                self.stdout.write(f"  [{n:02d}] {label:<28} exec={entry['execution_ms']} ms  seq={[s['table'] for s in plan['seq_scans']]}"
                                  f"{'  ADVICE: ' + ' | '.join(entry['advice']) if entry['advice'] else ''}")

        (out_dir / "explain_summary.json").write_text(json.dumps({
            "user": user.username, "following": len(following_ids), "seen_ids": len(seen_ids), "statements": summary,
        }, indent=2), encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"{n} plans written to {out_dir}/ (explain_summary.json + explain_NN_*.txt)"))
