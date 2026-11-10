# pgbouncer + `CONN_MAX_AGE` (P-C3)

Code only — nothing here was run. Pehle numbers apni machine ke hisaab se badlo.

## Kab chahiye?
Postgres me har connection ~5-10 MB RAM aur ek process hai. Django har web thread + har Celery child ke liye ek connection rakhta hai
(`CONN_MAX_AGE`). Hisaab: `web workers x threads + celery concurrency x workers + beat + one-off commands`. Agar ye Postgres
`max_connections` (managed DB par aksar 100-200) ke aas-paas aane lage, ya traffic spike par `too many connections` aaye, tab pgbouncer lagao.
Chhote single-host deploy par **bina pgbouncer** theek hai: `DB_CONN_MAX_AGE=60` (default) rakho.

## Settings (env) — `settings.py` me ye sab already hain
| env | default | kab badlo |
|---|---|---|
| `DB_CONN_MAX_AGE` | `60` | seconds jab tak connection reuse ho. `0` = har request par naya connection (pgbouncer ke peeche sasta hai, bina pgbouncer ke mehenga). Kabhi "unlimited" mat karo (code `None` allow nahi karta; env me sirf whole number). |
| `DB_CONN_HEALTH_CHECKS` | `false` | `true` karo jab pgbouncer / DB restart ke baad ek-do requests `connection already closed` se fail hon. Har request me ek halka ping. |
| `DB_DISABLE_SERVER_SIDE_CURSORS` | `false` | **pgbouncer transaction mode me `true` zaroori.** Django ka `.iterator()` server-side cursor use karta hai (hamare code me `post/feed_metrics.py`, `post/creator_feedback.py`, kuch management commands) — transaction pooling me cursor agli statement par gayab. |

Thumb rule: **bina pgbouncer** `DB_CONN_MAX_AGE=60`. **pgbouncer ke saath** `60` bhi chalta hai (Django -> pgbouncer connection sasta aur persistent), par agar `max_client_conn` kam pade
to `0`/`15` karo, aur `DB_CONN_HEALTH_CHECKS=true` rakho.

## Setup (transaction pooling)
1. `deploy/pgbouncer/pgbouncer.ini.example` -> `/etc/pgbouncer/pgbouncer.ini`, `userlist.txt.example` -> `userlist.txt` (chmod 600). Docker: `deploy/docker-compose.pgbouncer.yml`.
2. `.env`: `DATABASE_URL=postgres://USER:PASS@127.0.0.1:6432/learnscroll` aur `DB_DISABLE_SERVER_SIDE_CURSORS=true`. (`release_check` port `6432` dekh kar flag na ho to warn deta hai.)
3. **Migrations seedha Postgres par chalao** (port 5432), pgbouncer se nahi: `DATABASE_URL=postgres://USER:PASS@PG_HOST:5432/learnscroll python manage.py migrate`. Lambe DDL transactions pool me phansate hain aur
   `idle_transaction_timeout` unhe kaat sakta hai. Docker compose me `MIGRATE_DATABASE_URL` isi liye hai.
4. Sizing: `default_pool_size` x (pgbouncer instances) + admin/backup/monitoring (~10) **<** Postgres `max_connections`. `max_client_conn` >= saare web threads + celery processes ka jod (upar wala hisaab) + 20% margin.
5. Dekho: `psql -p 6432 pgbouncer -c "SHOW POOLS;"` — `cl_waiting` lagataar > 0 = pool chhota (ya koi request transaction pakad ke baitha hai). `SHOW STATS;` me `avg_wait_time`.

## Code me kya audit kiya (transaction mode ke hisaab se)
- `SET LOCAL statement_timeout` (`post/feed_resilience.py`, feed_explain_analyze command) — **safe**: `transaction.atomic()` ke andar hai, aur `SET LOCAL` transaction ke saath khatam.
- Session-level `SET`, `pg_advisory_lock`, `LISTEN/NOTIFY`, `DECLARE ... WITH HOLD`: code me **nahi mile** (grep). Naya code likhte waqt inse bacho, ye transaction mode me toot jaate hain.
- Server-side cursors: `.iterator()` — upar wala flag ye toot-na rokta hai, par client-side cursor **poora result set process ki memory me laata hai** (chunk_size sirf Django ke andar ka batch hai). Bade iterators (`post/feed_metrics.py` 5000-chunk, `recompute_video_watch_stats`) ko batch/`id__gt` pagination se chalao ya unhe seedha Postgres (5432) par chalao.
- Driver `psycopg2-binary`: server-side prepared statements nahi use karta, to purane pgbouncer (<1.21) par bhi koi dikkat nahi. Agar kabhi psycopg3 par jao to `OPTIONS: {"prepare_threshold": None}` chahiye.
- Celery workers bhi pgbouncer se jud sakte hain (same `DATABASE_URL`). `--concurrency=4` ka matlab 4 client connections per worker.
- Tests (`manage.py test`) pgbouncer ke through mat chalao; seedha Postgres.

## Failure ka asar
pgbouncer down = poori app DB se kat jaati hai (single point). Health: `healthz/deep` DB check pgbouncer ke through jaata hai to wo bhi laal hoga — sahi signal. Do instances / systemd `Restart=always` rakho.
