# Redis / Celery / ASGI / Postgres sizing (T3.6)

`python loadtest/sizing.py` turns load-test inputs into a plan. This document says **what in the codebase drives the
numbers**, what is *measured vs assumed*, and the configuration changes the findings point to.

> Status: the model is deterministic arithmetic (unit-tested in `tests_sizing.py`). The **inputs that matter most
> (`tasks_per_sec`, `avg_task_seconds`, sockets per ASGI process) are assumptions until you run the 1k test** — replace
> them with the measured values and re-run. Where model and measurement differ by >30 %, trust the measurement.

## 1. Facts verified in this repo

| Fact | Where | Consequence |
|---|---|---|
| One `REDIS_URL` feeds the **channel layer** and the **Django cache**; the Celery broker/result URL defaults to the same Redis, db 0 (`env.production.example`) | `settings.py` (CHANNEL_LAYERS, CACHES, CELERY_BROKER_URL) | Eviction-policy conflict: broker + channel layer need `noeviction`, the cache wants `allkeys-lru`. On one instance you must pick one — `allkeys-lru` can evict queued tasks / in-flight chat frames; `noeviction` lets the cache fill Redis and start failing writes. **Use two instances.** |
| No `result_expires` / `ignore_result` anywhere; **no code reads a task result** (`AsyncResult`/`.get()` grep is empty) | `settings.py`, all `tasks.py` | Every task result is stored for Celery's default **24 h** — at 25 tasks/s that is ~1.9 GB of Redis for data nobody reads. |
| No `task_routes`; a single default queue; 43 beat entries (4 every minute) and ~100 `@shared_task`s (41 in `tuitionclass`) | `settings.py`, `*/tasks.py` | Latency-sensitive push/notification tasks queue behind sweeps and reports. |
| `CELERY_TASK_ACKS_LATE = True`, prefetch multiplier **not set** (default 4), no time limits | `settings.py` | With acks-late a prefetching child holds 4 messages hostage behind a slow task; no `soft_time_limit` means one hung task pins a slot forever. |
| Worker unit: `--concurrency=4 --max-tasks-per-child=500`, a single unit | `deploy/systemd/learnscroll-celery-worker.service` | 4 slots total. |
| Broker failure → `core.async_utils` runs the task **inline in the web request** (2 s connect timeout) | `settings.py` comment | A Redis brownout converts into slow HTTP/WS handlers rather than a queue backlog — Redis headroom is also web-tier headroom. |
| WS send limiter: 60 msg/min/user (cache-backed) | `message/throttles.py` | Load scripts send 1 per 15–30 s; the limiter's cache hit rate is part of Redis load. |
| `session_join` throttle 20/min/user; join takes a row lock on the session (`select_for_update`) | `tuitionclass/views/sessions.py` | Joins to **one** session serialize on that lock — a 150-user join storm is a lock-contention test, not just throughput. |
| `CONN_MAX_AGE = 60`, Channels consumers use `database_sync_to_async` | `settings.py`, `message/consumers.py` | Each sync_to_async thread keeps a connection — Postgres connections scale with *processes × threads*, not with users. |

## 2. Model (defaults = 1,000 concurrent users)

Defaults: 1,400 sockets (1 inbox per user + 250 chat + 150 live), 250 users in one chat room at 2.5 msg/user/min,
600 feed users at one request / 3 s, 25 Celery tasks/s at 0.5 s each.

| Area | Result | Notes |
|---|---|---|
| ASGI | ≥ 2 processes (≈ 800 sockets each), `ulimit -n` ≈ 2.4k each, ~0.6 GB | Assumption: 800 sockets/process — **measure**. |
| Chat fan-out | 10.4 msg/s → **2,600 deliveries/s** → ~7.8k Redis ops/s | One huge room is the worst case; typical rooms are far smaller. |
| Celery | 12.5 busy slots → **21 slots** at 60 % utilisation → ~6 procs × 4 | Deployed: 4 slots. At 25 tasks/s the current unit saturates ≈ 8 tasks/s (4 / 0.5 s). |
| Redis memory (channel + broker + results) | ~2.1 GB, **1.9 GB of it task results** | → 0.2 GB with `ignore_result` / 1 h expiry. |
| Postgres | ~45 connections | PgBouncer recommended past ~150. |

## 3. Recommended changes (not applied — review first)

**Celery (`settings.py`)**
```python
CELERY_TASK_IGNORE_RESULT = True            # nothing reads results (verified); or CELERY_RESULT_EXPIRES = 3600
CELERY_WORKER_PREFETCH_MULTIPLIER = 1       # pairs with acks_late; fair dispatch
CELERY_TASK_SOFT_TIME_LIMIT = 120           # per-task overrides for known long jobs
CELERY_TASK_TIME_LIMIT = 150
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_connect_timeout": 2, "visibility_timeout": 3600}
CELERY_TASK_ROUTES = {                      # names are examples — map your real modules
    "core.tasks.*": {"queue": "realtime"},
    "message.tasks.*": {"queue": "realtime"},
    "tuitionclass.tasks.*": {"queue": "default"},
    "copyrights.tasks.*": {"queue": "bulk"},
    "leaderboard.tasks.*": {"queue": "bulk"},
}
```
Then run workers per queue (copy `learnscroll-celery-worker.service`, change `-Q`/`--concurrency`):
`-Q realtime --concurrency=8` · `-Q default --concurrency=4` · `-Q bulk --concurrency=2`. Keep **exactly one** beat.
Check `CELERY_BEAT_SCHEDULE` entries still resolve after routing (the `check_config_drift` gate covers scheduling).

**Redis — two instances**

| | A: broker + channel layer | B: cache |
|---|---|---|
| `maxmemory-policy` | `noeviction` | `allkeys-lru` |
| `maxmemory` | model value ×2 (≈ 1 GB once results are off) | what the cache needs |
| persistence | AOF `everysec` (queued tasks survive a restart) | none |
| env | `REDIS_URL`, `CELERY_BROKER_URL` | new `CACHE_REDIS_URL` (point `CACHES.LOCATION` at it) |

Both: `maxclients` ≥ 2 × the `connected_clients` peak seen in the test, `tcp-keepalive 60`, alert on `used_memory/maxmemory > 0.8`
and on `rejected_connections > 0`.

**channels_redis** — for rooms > 100 members raise `capacity` (default 100 frames/channel) and keep `group_expiry` short:
`"CONFIG": {"hosts": [REDIS_URL], "capacity": 500, "expiry": 30}`. Re-run the chat scenario and watch the *lost echo* rate.

**ASGI** — `daphne -b 0.0.0.0 -p 8000 LearnScroll.asgi:application` per process (≥ 2 behind nginx with sticky-less round robin;
websocket `proxy_read_timeout` ≥ the ping interval), `LimitNOFILE=65536` in the unit.

## 4. How to calibrate after a 1k run

| Measure | Command | Feed into `sizing.py` |
|---|---|---|
| Celery tasks/s & duration | `celery -A LearnScroll inspect stats` + Flower, or `redis-cli LLEN celery` slope | `--tasks-per-sec`, `--avg-task-seconds` |
| Sockets per ASGI proc before p95 echo breaks | Locust chat echo vs. `ss -s` / process RSS | `--ws-per-asgi-proc` |
| Real Redis memory/ops | `redis-cli INFO memory`, `INFO stats` (`instantaneous_ops_per_sec`) | compare with the `redis` block |
| DB connections | `pg_stat_activity` | `--db-threads-per-asgi-proc` |

Pass criterion for the plan: at 1k users all `THRESHOLDS` hold, Celery queue length returns to ~0 within 30 s of the peak,
Redis `used_memory` is flat during the 15-minute hold (a rising line = leak or unbounded results), and no ASGI process
exceeds 70 % CPU.
