# Load test (T3.6) — feed · chat WebSocket · live-session join · 1k concurrent

Two equivalent harnesses (pick one; scenarios and pass/fail thresholds match):

| | file | strengths |
|---|---|---|
| Locust | `locustfile.py` | Python, easy to extend with app-specific flows, web UI, CSV for `sizing.py` |
| k6 | `k6.js` | single static binary, very efficient injector, native WebSocket |

## What is exercised

| Scenario | Traffic | Mix @ 1k |
|---|---|---|
| **Feed** | `GET /post/feed/` + 3 cursor pages (`next`), `?source=following` | 600 users (60 %) |
| **Chat WS** | `ws/chat/<conversation>/?token=` — connect, send 1 msg / 15–30 s, measure **echo latency** (message → Redis channel layer → back to the sender's socket via `client_id`) | 250 users (25 %) in **one** room = worst-case fan-out (~2.6k frames/s) |
| **Live join** | `POST /tuitionclass/sessions/<id>/join/` then `ws/tuitionclass/session/<id>/`, hold 2–10 min | 150 users (15 %) |

The 1k profile (`PROFILE=1k`): ramp 0→200→500→1000 over 8 min (30 users/s max — this is also the join-storm rate against
`session_join`/Postgres), hold 15 min, ramp down. Plus `baseline` (100 users), `smoke` (10) and `soak` (300 users, 4 h, to catch leaks).

## Pass / fail (release gate)

| Metric | Threshold |
|---|---|
| Feed p95 | < 800 ms, errors < 1 % |
| Live join p95 | < 1500 ms, errors < 1 % |
| WS connect p95 | < 1000 ms, failed < 1 % |
| Chat echo p95 | < 500 ms, lost < 2 % |

Locust exits non-zero (and k6 too) when a threshold is breached. Edit `THRESHOLDS` / `options.thresholds` to your SLOs.

## Run it

> **Staging only.** The seed command refuses to run without `--confirm-staging` and refuses a production-flagged env.
> Use a staging stack shaped like production: Postgres (not sqlite), **real Redis** (not LocMem/InMemory), `DEBUG=False`,
> multiple ASGI workers, Celery worker + beat running, LiveKit pointed at a sandbox project.

```bash
# 1) fixtures: 1,000 users, a group chat, a classroom + free passes + a LIVE session, one JWT per user
python manage.py seed_loadtest --users 1000 --confirm-staging --out loadtest/fixtures.json     # file is git-ignored

# 2a) Locust
pip install -r loadtest/requirements.txt
PROFILE=1k locust -f loadtest/locustfile.py --headless --host https://staging.example.com \
    --csv loadtest/out/run1k --html loadtest/out/run1k.html
# 2b) or k6
k6 run -e BASE=https://staging.example.com -e PROFILE=1k loadtest/k6.js

# 3) turn the measurements into a sizing plan (see SIZING.md)
python loadtest/sizing.py --users 1000 --tasks-per-sec <measured>

# 4) clean up
python manage.py seed_loadtest --cleanup --confirm-staging
```

Smoke first (`PROFILE=smoke`) — if it isn't green at 10 users, don't run 1k.

## Reading the result

* **429s** — DRF per-user scopes (e.g. `session_join` 20/min) and the 60 msg/min WS limiter are real protections; the
  scripts stay under them. 429s in the report mean the injector is mis-configured (e.g. several VUs sharing one account) — not a capacity finding.
* **One injector box saturates before the system does** (~2–3k sockets / a few k RPS per core). Watch the injector's CPU;
  for >1k users distribute (`locust --master/--worker`, or k6 `--execution-segment`). A hot injector shows up as inflated
  *injector-side* latency with a quiet server.
* **Chat echo** includes DB write (`save_message`) + channel-layer round trip — it is the best single end-to-end realtime health number.
* Server-side while running: `redis-cli INFO memory/stats/clients`, `celery -A LearnScroll inspect active/reserved/stats`,
  `SELECT count(*), state FROM pg_stat_activity GROUP BY 2`, ASGI process RSS/fds, and the Celery queue length (`LLEN celery`).

## Message calls + study rooms (X2)
`CALLS_ONLY=1` (Locust) / `-e CALLS=1` (k6) + `manage.py seed_calls_loadtest --confirm-staging` -> scenarios CL1-CL7, SR1-SR6, WH1.
Plan, pass criteria, failure injection and the media-plane tests (`lk load-test`): **`CALLS_STUDYROOM_PLAN.md`**.
LiveKit node sizing: `python loadtest/livekit_sizing.py` (tests: `loadtest.tests_livekit_sizing`). Live-class (tuitionclass) plan: `LIVE_CLASS_PLAN.md`.

## Launch gate: 1k feed · 200 live · upload burst · login burst (P-C6)
Plan, targets (p95), infra sizing, how to read the result and pass/fail criteria: **`LOAD_TEST_PLAN.md`**. Scripts: **`locustfile_pc6.py`**
(`SCENARIO=feed|live|upload|login|all`, `PROFILE=smoke|gate|live_storm|login_burst|upload_burst|soak`). Needs `seed_loadtest --users 1500`.

```bash
SCENARIO=all PROFILE=smoke locust -f loadtest/locustfile_pc6.py --headless --host https://staging.example.com
SCENARIO=all PROFILE=gate  locust -f loadtest/locustfile_pc6.py --headless --host https://staging.example.com --csv loadtest/out/pc6_gate --html loadtest/out/pc6_gate.html
```

## Not covered (deliberately)

Payments/webhooks, LiveKit media plane (audio/video; X2 plan section 6 covers it with `lk load-test`), OTP / social login (third-party limits; password login + media upload are covered by `locustfile_pc6.py`),
and the doubts hub (`/doubts/`) — add a `HttpUser` for it if it becomes a hot path.

## H2 - Feed ke asli numbers (page-1 p95, page-2 p95, DB queries/page, Redis hit-rate)

`GET /post/feed/` ka target p95 <= 800 ms tha par kabhi measure nahi hua. Neeche ki table `feed_report.py` bharta hai;
jab tak staging run nahi hota, har number **NOT MEASURED** hai (kuch estimate nahi kiya gaya).

<!-- H2:START -->
| Metric | Result | Target |
|---|---|---|
| Page-1 p95 (`GET /post/feed/`) | NOT MEASURED | <= 800 ms |
| Page-2 p95 (cursor, snapshot) | NOT MEASURED | <= 800 ms |
| DB queries / page | NOT MEASURED | within `tests_feed_query_budget.py` |
| Redis hit-rate (feed caches) | NOT MEASURED | high |
<!-- H2:END -->

### Run (staging: Postgres + real Redis, DEBUG=False, production-sized `posts` / `post_views`)

```bash
# 0) web workers: add  FEED_LOADTEST_STATS=1  to the env and restart (counters; keep it OFF in production)
# 1) data
python manage.py seed_loadtest --users 1000 --confirm-staging --out loadtest/fixtures.json
python manage.py seed_feed_loadtest --confirm-staging        # posts + follows + seen rows for lt_user_*
psql "$DATABASE_URL" -c "ANALYZE;"
# 2) smoke, then the real run (1k users, feed = 60 %; page 1 / page 2 / page 3+ are separate Locust rows)
PROFILE=smoke locust -f loadtest/locustfile.py --headless --host https://staging.example.com
python manage.py feed_stats --reset
PROFILE=1k locust -f loadtest/locustfile.py --headless --host https://staging.example.com \
    --csv loadtest/out/run1k --html loadtest/out/run1k.html
python manage.py feed_stats --json --out loadtest/out/feed_stats.json
# 3) slow queries (EXPLAIN ANALYZE of _home_base_qs / build_pool_ids / get_seen_post_ids) -> loadtest/out/explain_*.txt
python manage.py feed_explain_analyze --username lt_user_0001 --confirm-staging
# 4) fill the table above
python loadtest/feed_report.py            # exit code 2 = some number was not measured
```

### Query-count regression test
`python manage.py test post.tests_feed_query_budget -v 2` - page-1 / page-2 ceilings (`PAGE1_BUDGET`, `PAGE2_BUDGET`),
no growth with page size, page 2 never rebuilds the pools. The ceilings were set by reading the code; after the first
real run lower them to measured + ~10 %.

### Indexes
Reading the code found no clearly missing index. Candidate `CREATE INDEX CONCURRENTLY` statements, with the EXPLAIN
signal that justifies each one, are in `loadtest/sql/feed_candidate_indexes.sql` - apply only what the plans ask for.

### Per-row queries in the feed serializer (fixed in H2)
`get_best_answer` (doubt posts) and `shared_item` (shared class / test series) are now batched per page (`ViewerStateListSerializer._stamp`); a shared card still costs 1 referral-code query per distinct sharer.
The seen-ids list (up to `FEED_SEEN_CAP` = 2000) is inlined as SQL literals in the pool statements.

## Campus module at school scale (1000+ students)
See `locustfile_campus.py` + `manage.py seed_campus_loadtest` (STATUS_PERF_1K.md at project root).

> **X6b:** locustfile sockets ab `POST /login/auth/ws-ticket/` se ticket leke `?ticket=` se kholta hai (har connect par ek extra HTTP call, connect time me gina jata hai). Purana `?token=` chahiye to `LOADTEST_WS_LEGACY_TOKEN=1`.
