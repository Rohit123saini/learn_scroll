# LearnScroll load test plan (P-C6) — 1k feed · 200 live · upload burst · login burst

> **Status: PLAN + scripts. Nothing was run against a real server** (only a syntax check and a 15-second smoke against a
> throw-away mock HTTP server to prove the scripts' request shapes). Every capacity number below that is not marked
> *measured* is an **assumption** — the first staging run replaces it. Where model and measurement differ by > 30 %,
> trust the measurement.
>
> Builds on `README.md` (T3.6: feed / chat WS / live join, `locustfile.py`, `k6.js`), `SIZING.md`, `LIVE_CLASS_PLAN.md`,
> `CALLS_STUDYROOM_PLAN.md`. This plan adds the two things those did not cover — **upload burst** and **login burst** —
> and puts the four launch-gate scenarios in one file: **`locustfile_pc6.py`**.

## 1. Rules

1. **Staging only.** `seed_loadtest` refuses to run without `--confirm-staging`. Never point a script at production.
2. Staging must be shaped like production or the numbers are worthless: **Postgres** (not sqlite), **real Redis** (not
   LocMem), `DEBUG=False`, **≥ 2 ASGI processes**, Celery worker **+ beat** running, production-sized `posts` /
   `post_views` tables (`seed_feed_loadtest`), LiveKit pointed at a sandbox project.
3. Run **smoke first** (`PROFILE=smoke`). If 10 users are not green, do not start 1,000.
4. One injector box saturates before the system does (~2–3k sockets / a few k RPS per core). Watch the injector's CPU;
   past ~1k users distribute it (`locust --master` + `--worker`, or k6 `--execution-segment`).

## 2. Tools

| Tool | For | Where |
|---|---|---|
| **Locust** (`locustfile_pc6.py`) | the four gate scenarios, HTTP + WebSocket, CSV/HTML report, non-zero exit on a breach | `pip install -r loadtest/requirements.txt` |
| k6 (`k6.js`) | same feed / chat / live shapes, lighter injector, native WS | existing |
| `python manage.py seed_loadtest` | N users + one JWT each, a classroom + a LIVE session | existing (`--users 1500` for the combined run) |
| `seed_feed_loadtest`, `feed_stats`, `feed_explain_analyze` | feed data volume, cache hit-rate, `EXPLAIN` of the pool queries | existing (README, H2 section) |
| `loadtest/sizing.py` | turns measured rates into worker / Redis / Postgres numbers | existing (`SIZING.md`) |
| Server side | `pg_stat_statements`, `pg_stat_activity`, `redis-cli INFO`, Flower / `celery inspect`, `ss -s`, `htop`/`pidstat`, `iostat`, nginx access log (`$request_time`, `$upstream_response_time`) | staging hosts |
| `lk load-test` | LiveKit **media plane** (audio/video) — a different test, see `CALLS_STUDYROOM_PLAN.md` §6 | LiveKit CLI |

## 3. Scenarios and targets

All p95 are measured **at the injector** (includes network + queueing), so they are conservative for the server.

| ID | Scenario (`SCENARIO=`) | Load model | Requests | p95 target | Error budget |
|---|---|---|---|---|---|
| **F1** | `feed` — **1,000** feed users | each user: scroll 4 pages (2–6 s reading between pages), then 5–15 s idle; 1 in 6 cycles also opens `?source=following`. ≈ 0.19 req/s/user → **≈ 190 req/s** | `GET /post/feed/` page 1 (new session, pools built), page 2 (cursor, frozen snapshot), page 3+ — **separate rows** | page 1 < **800 ms**, page 2 < **800 ms** | < 1 % |
| **F1-stress** | same, `FEED_READ_*=0.5–2 FEED_WAIT_*=1.5–4` | ≈ 0.65 req/s/user → **≈ 650 req/s** (3× real) — find the knee, not a gate | same | report only: the user count where p95 crosses 800 ms | — |
| **L1** | `live` — **200** viewers in **one** class | all 200 `POST /tuitionclass/sessions/<id>/join/` then hold `ws/tuitionclass/session/<id>/` for 10–15 min (WS ticket auth, like the app) | join, WS connect, **WS held** (one sample per viewer per class: failure = socket dropped before the hold ended) | join < **1500 ms**, WS connect < **1000 ms** | join < 1 %, **dropped < 1 %** |
| **L1-storm** | `PROFILE=live_storm` | all 200 join inside ~10 s (class starts, teacher says "join now"). Joins to one session serialize on a row lock (`select_for_update`), so this is a **lock-contention test** | same | join p95 < 3000 ms during the storm, no 5xx | < 2 % |
| **U1** | `upload` — **50** uploaders | each uploader loops: `POST /post/uploads/` (init) → **4 × chunk** (256 KB each, md5 verified) → `complete` (creates a real image post). Incompressible 512 px PNG ≈ 0.8 MB; `UPLOAD_PX` scales it. ≈ 8 posts/s, ≈ 6 MB/s at 50 VUs | init, chunk, complete — separate rows | init < **500 ms**, chunk < **1500 ms**, complete < **5000 ms** | init/chunk < 1 %, complete < 2 % |
| **U1-burst** | `PROFILE=upload_burst` | all 50 within ~5 s (after a test / event everybody posts) | same | same; plus **Celery queue back to ~0 within 2 min** after the burst | — |
| **A1** | `login` — **100** VUs | each VU: `POST /login/` with the real password (PBKDF2), then the first `GET /post/feed/` with the new token; repeats every ~6 s ⇒ **≈ 17 logins/s = 1,000 logins/min** (a school-start wave) | login, first feed ("cold": no seen-ids yet) | login < **1500 ms**, first feed < **1200 ms** | < 1 % |
| **A1-steps** | `LOGIN_REPEAT_S=3` (≈ 33/s), `=2` (≈ 50/s) | find the logins/s where login p95 crosses 1500 ms → that number divided by CPU cores is the capacity of the hash | same | report only | — |
| **G1** | `SCENARIO=all PROFILE=gate` | F1 + L1 + U1 + A1 at once (1,350 VUs): ramp 8 min, hold 15 min, ramp down. The **release gate** | all of the above | all of the above, simultaneously | all of the above |
| **S1** | `PROFILE=soak` | 30 % of G1 for 4 h | all | no p95 drift > 20 %, memory/fd/connection counts flat | — |

Chat (250 users in one room) is **not** repeated here; it stays in `locustfile.py` (`PROFILE=1k`) — run it together with G1 when
you want the full picture (the combined Redis / Postgres load is the point).

## 4. Pre-flight checklist (before every non-smoke run)

- [ ] `python manage.py seed_loadtest --users 1500 --confirm-staging --out loadtest/fixtures.json` (needs ≥ 1 + 1000 + 200 + 50 + 100 users; the script warns when the file is too small). The file holds live JWTs — it is git-ignored, delete it afterwards.
- [ ] `seed_feed_loadtest --confirm-staging`, then `psql -c "ANALYZE;"` (a feed test on an empty table measures nothing).
- [ ] **Client IP.** `POST /login/` has no throttle of its own, so the default anonymous throttle (**20 / min per client IP**) and the login lockout guard (keyed by identifier **and** IP) apply. One injector = one IP ⇒ you would measure the throttle, not the system. The script therefore sends a stable fake `X-Forwarded-For` per account (`LOADTEST_XFF=1`, default). That only works if staging resolves the client IP the way production does (`NUM_PROXIES` set, proxy trusting XFF). **Verify once:** 25 logins from different `lt_user_*` in one minute must not return 429. Do **not** raise the throttle on production to “fix” this.
- [ ] **Hasher.** `PASSWORD_HASHERS` is not overridden (Django default PBKDF2). `seed_loadtest` hashes with the same default, so a login costs what a real one costs. Do not switch to a weaker hasher for the test.
- [ ] **Side effects of login** (`login_alerts.check_new_device`, audit rows, device/session rows): staging must use a console / file `EMAIL_BACKEND` (settings default is SMTP) and a sandbox push key, or 1,000 logins/min will mail / push real addresses (`*@loadtest.invalid` bounce) and burn the provider quota.
- [ ] **Upload storage.** Chunks are written to `MEDIA_ROOT/temp_chunks/<upload_id>/` on the **local disk of whichever web node got the request** (`post/services.py::_chunk_dir`), and `complete` reads them back. With ≥ 2 web nodes either `temp_chunks` is on a shared volume, or the chunk / complete calls are pinned to one node (sticky by session id). If neither is true, U1 will show `chunk missing` failures — that is a **deployment finding, not a script bug**. Also check free disk: 50 uploaders × 0.8 MB × ~8/s ≈ 6 MB/s of temp writes plus the final media.
- [ ] Per-user limits the script stays under: `UserRateThrottle` 100/min (feed users do ≈ 11/min), `MAX_ACTIVE_SESSIONS_PER_USER` = 20 uploads, `session_join` 20/min. **429 in the report ⇒ the injector is mis-configured (shared account / too fast), not a capacity finding.**
- [ ] Celery **worker + beat running** (the post-create path, thumbnails, notifications and moderation are Celery work — if the worker is off, U1 looks great and means nothing). Exactly one beat.
- [ ] Monitoring up *before* the run: Postgres, Redis A (broker + channels) and B (cache) separately if split, ASGI / gunicorn RSS + open fds, Celery queue lengths, disk, network, injector CPU.
- [ ] `FEED_LOADTEST_STATS=1` on the web workers only for the feed-stats run; **off** for G1/S1 (counters add overhead).

## 5. Run order

```bash
cd new_backend
pip install -r loadtest/requirements.txt
H=https://staging.example.com

# 0) smoke - every scenario, 10 users, 1 minute. Must be green.
SCENARIO=all PROFILE=smoke locust -f loadtest/locustfile_pc6.py --headless --host $H

# 1) each scenario ALONE first (isolates who breaks what)
SCENARIO=feed   PROFILE=gate        locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_feed   --html loadtest/out/pc6_feed.html
SCENARIO=live   PROFILE=live_storm  locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_live   --html loadtest/out/pc6_live.html
SCENARIO=upload PROFILE=upload_burst locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_upload --html loadtest/out/pc6_upload.html
SCENARIO=login  PROFILE=login_burst locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_login  --html loadtest/out/pc6_login.html
#    login capacity steps (find the knee):  LOGIN_REPEAT_S=3 ... then =2

# 2) the gate - everything at once (1,350 VUs)
SCENARIO=all PROFILE=gate locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_gate --html loadtest/out/pc6_gate.html
#    optionally the 250-user chat room in parallel from a second injector:  PROFILE=1k locust -f loadtest/locustfile.py ...

# 3) soak (overnight), then cleanup
SCENARIO=all PROFILE=soak locust -f loadtest/locustfile_pc6.py --headless --host $H --csv loadtest/out/pc6_soak
python manage.py seed_loadtest --cleanup --confirm-staging     # also removes the posts the uploaders created (user cascade)
rm loadtest/fixtures.json
```

Locust exits **1** when a threshold of an enabled scenario is breached (`THRESHOLDS` in `locustfile_pc6.py`, same numbers as §3) or
when a gated request has **no samples** (scenario did not run). Edit `THRESHOLDS` only to change an SLO, never to make a run pass.
Env knobs (counts, pacing, hold time, image size, `WS_BASE`) are documented at the top of the file.

## 6. Infrastructure sizing (starting point — **assumptions**, calibrate with `sizing.py` after the first run)

Plan the **test environment** to be at least the size you intend to launch with; undersized staging only proves it is undersized.
Existing facts and the Redis / Celery / ASGI recommendations are in `SIZING.md` (two Redis instances, `ignore_result`, per-queue workers,
PgBouncer past ~150 connections) — they apply unchanged. What the four scenarios add:

| Tier | Driver | Rule of thumb (assumption) | Verify by |
|---|---|---|---|
| **Web / API CPU** | feed ≈ 190 req/s + login hashing + upload chunks | **login is the CPU hog**: PBKDF2 ≈ 0.1–0.3 s of **one core** per login ⇒ 17 logins/s ≈ **2–5 cores**, 50/s ≈ **5–15 cores** of pure hashing (Django's default iteration count is high; the exact cost is what A1-steps measures). Feed adds a few ms of Python per query, DB-bound | `pidstat` per core during A1; logins/s at the knee ÷ cores = logins/s/core |
| **ASGI sockets** | 200 live viewers (+ 250 chat, + 1 inbox socket per user in a real day) | ≈ 800 sockets per process (assumption in `SIZING.md`); 200 live sockets are small, **the join storm (lock + DB) is the risk, not the socket count** | `ss -s`, RSS per process, fds vs `ulimit -n` (set `LimitNOFILE=65536`) |
| **Postgres** | feed pool queries, `session_join` row lock, 8 posts/s inserts + media rows, login session / audit rows | connections ≈ processes × threads, **not** users; PgBouncer (transaction mode) once > 150. Plan ≥ 4 vCPU / 16 GB for the gate; `shared_buffers` 25 % RAM | `pg_stat_activity` by state, `pg_stat_statements` top-10 by total time, lock waits on the session row |
| **Redis** | feed caches + snapshots, throttle counters, channel layer, Celery broker | two instances (broker/channels = `noeviction`, cache = `allkeys-lru`); watch `instantaneous_ops_per_sec`, `used_memory`, `rejected_connections` | `redis-cli INFO` every 10 s during G1 |
| **Celery** | each created post → thumbnails / moderation / feed + notification fan-out; login → alerts | with U1-burst at ~8 posts/s the **default single queue** (4 slots) is the first thing to back up; split `realtime` / `default` / `bulk` as in `SIZING.md` | queue length slope during U1-burst; must drain in < 2 min |
| **Disk / network** | upload temp chunks + final media | ≈ 6 MB/s ingress at U1; temp dir on fast local or shared volume; free space for the whole run + a margin | `iostat -x`, `df`, NIC counters |
| **Injector** | 1,350 VUs, ~190 + ~100 + ~50 req/s + 200 sockets | one 4–8 vCPU box is borderline; if injector CPU > 70 % use master/worker | injector `htop`; a hot injector shows inflated latency with a quiet server |

## 7. How to read the result

1. **Open the HTML report, sort by p95.** Rows are separate on purpose: feed page 1 vs page 2 vs 3+, `[init]` / `chunk` / `complete`,
   login vs “first feed after login”. A good aggregate hides a bad row.
2. **Failures tab first.** Group by message:
   - `429` → injector problem (shared account, fake-XFF not honoured, pacing too fast) — fix the setup, re-run; **not** a capacity result.
   - `502/503/504` or connection resets → web tier saturated or restarting (check ASGI worker recycling during the run).
   - `chunk missing` / `HTTP 404 ... upload` → chunk dir not shared across nodes (see §4).
   - `WS live held` failures → sockets dropped mid-class: look at proxy `proxy_read_timeout`, ASGI restarts, Redis channel-layer errors.
3. **Match each bad row with the server signal that explains it:**

| Symptom | Look at | Usual cause |
|---|---|---|
| login p95 grows linearly with logins/s | CPU per core | hash CPU-bound → more cores / workers (never weaker hashing) |
| feed page 1 slow, page 2 fine | `feed_stats`, `pg_stat_statements` for pool queries, Redis hit-rate | pools rebuilt per user; missing cache; seen-ids table size |
| feed slow everywhere, DB CPU high | `feed_explain_analyze` plans, query count vs `tests_feed_query_budget.py` | N+1 (see `STATUS_PC5.md`), missing index (`sql/feed_candidate_indexes.sql`) |
| join p95 spikes only in L1-storm | lock waits on the session row | `select_for_update` serialization — expected to rise; judge against the 3 s storm target |
| upload `complete` slow | Celery queue length, disk latency | assembling + post-create work inline, or worker starved |
| everything slow at once but each alone is fine | Postgres connections / Redis ops / one shared CPU | shared bottleneck — the point of G1 |

4. **Steady state, not the ramp.** Judge the hold window (minutes 8–23 of G1). Locust's “reset stats” is not used; cut the CSV history by time if the ramp pollutes the percentiles.
5. **Trend lines must be flat** during the hold: Redis `used_memory`, Postgres connections, ASGI RSS and open fds, Celery queue length, disk free. A rising line is a leak / unbounded growth even when p95 passes.
6. Record the numbers in §10 and feed `tasks/s`, `avg task seconds`, `sockets per process`, `logins/s/core` into `python loadtest/sizing.py` (`SIZING.md` §4).

## 8. Pass / fail criteria (release gate = G1 passes + each scenario alone passes)

| # | Criterion | Pass |
|---|---|---|
| 1 | Every threshold in §3 (the ones enforced by `THRESHOLDS`) | all met in the hold window |
| 2 | HTTP 5xx | **0** on feed / login / join; < 0.1 % on upload |
| 3 | Dropped live viewers (`WS live held` failures) | < 1 % |
| 4 | Celery queue length after the last upload | back to ~0 within **2 min**; no task failing in a loop |
| 5 | Resource headroom at the peak | no web/ASGI process > **70 %** CPU, Postgres CPU < 70 %, Postgres connections < 80 % of `max_connections`, Redis `used_memory` < 80 % of `maxmemory`, `rejected_connections` = 0, disk free > 20 % |
| 6 | Flat trends over the 15-min hold and the soak | no monotonic growth (memory, fds, connections, queue) ; soak p95 drift < 20 % |
| 7 | Data integrity | every `complete` that returned 201 produced exactly one post (count posts by the `lt_user_*` upload slice vs 201s); no orphan `temp_chunks` left > 1 h; login produced one session row per success |
| 8 | Injector | CPU < 70 % for the whole run (otherwise the run is void) |

**Fail = any row above not met.** Then: fix, re-run the single scenario that failed, re-run G1. Do not average a failed run with a good one.

## 9. Risks found by reading the code (check these first — they are why the scenarios exist)

1. **Login = PBKDF2 on the request thread.** Capacity is `cores ÷ hash time`; a school-start wave (1,000 logins/min) is a real peak. Mitigations if A1 fails: more web cores for the login path, stagger session refresh (the app uses refresh tokens, so repeat logins should be rare), keep `LoginGuard` + captcha as they are.
2. **Local-disk upload chunks** (`temp_chunks` under `MEDIA_ROOT`): breaks silently on > 1 web node without a shared volume or stickiness (§4).
3. **Upload `complete` runs the real create view inline** (assemble → serializers → copyright gate → DB rows). Big videos make this slow; U1 uses a small image on purpose so it measures the system, not the file size. Run a second U1 with `UPLOAD_PX=3000` (≈ 27 MB, ~100 chunks) at 5–10 VUs to see the big-file behaviour.
4. **`session_join` row lock** serializes 200 joins to one session; fine at 200 if each join is a few ms, ugly if the join does LiveKit / notification work inside the lock.
5. **Single default Celery queue, 4 slots** (`SIZING.md`): post-create tasks and login alerts compete with bulk sweeps.
6. **Feed page 1 builds pools** (heavy), page 2 reuses a frozen snapshot: a login wave makes everyone request page 1 at once (the A1 “first feed after login” row measures exactly that).
7. **Per-user throttles are protections, not bugs.** If the real app can hit them in normal use (e.g. `ws_ticket` 60/min on flaky networks reconnecting), that is a product finding — note it, do not raise limits to pass a test.

## 10. Results (fill after the staging run — until then everything is NOT MEASURED)

| Scenario | Metric | Result | Target |
|---|---|---|---|
| F1 | feed page 1 p95 / page 2 p95 / err | NOT MEASURED | < 800 / < 800 ms / < 1 % |
| F1-stress | users at p95 = 800 ms | NOT MEASURED | report |
| L1 | join p95 / WS connect p95 / dropped | NOT MEASURED | < 1500 / < 1000 ms / < 1 % |
| L1-storm | join p95 / 5xx | NOT MEASURED | < 3000 ms / 0 |
| U1 | init / chunk / complete p95, posts/s sustained | NOT MEASURED | < 500 / < 1500 / < 5000 ms |
| U1-burst | Celery drain time | NOT MEASURED | < 2 min |
| A1 | login p95 / first-feed p95 at ≈ 17 logins/s | NOT MEASURED | < 1500 / < 1200 ms |
| A1-steps | logins/s at the knee, per core | NOT MEASURED | report |
| G1 | all rows above, simultaneously; peak CPU / PG conns / Redis mem | NOT MEASURED | §8 |
| S1 | p95 drift, memory / fd / connection trend | NOT MEASURED | < 20 % / flat |

## 11. Not covered (deliberately)

LiveKit media plane (`lk load-test`, `CALLS_STUDYROOM_PLAN.md` §6) · payments / webhooks · OTP / social login (third-party limits; only password login is
load-tested) · video transcoding throughput (separate worker pool sizing) · CDN behaviour (test the origin; the CDN only helps) · multi-region.
