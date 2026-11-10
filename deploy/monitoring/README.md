# Home feed - SLO, dashboard, alerts (H10)

## SLO
| SLI | Target | How it is measured |
|---|---|---|
| Availability | **>= 99.5 %** | `1 - 5xx / requests` of `GET /post/feed/` (any page). A *degraded* (fallback) answer is a 200 and counts as available - but is tracked on its own. An empty page produced because even the fallback failed counts as an error. |
| Latency | **p95 <= 800 ms** | fixed histogram (50/100/200/400/800/1600/3200 ms). The SLO holds when >= 95 % of requests are <= 800 ms. |

Numbers live in 5-minute Redis buckets (`post/feed_ops.py`, keys `feedops:*`, 40 days). Blind spots (not seen by Django):
a dead gunicorn, an LB 502/504 before the view, a timeout in the proxy. Keep an external uptime probe on `/post/feed/`.

## What is wired
| Piece | Where |
|---|---|
| Per-request counters (latency, outcome, events) | `post/feed_ops.py`, recorded in `HomeFeedView.list` |
| Fast-burn alert (last 15 min; Sentry message + ERROR log) | celery beat `post-feed-slo-watch` every 5 min -> `post.tasks.feed_slo_watch` |
| Daily quality + SLO verdict (CTR, dwell, quick-skip, show-fewer, availability, p95) | celery beat `post-feed-daily-metrics` 02:45 UTC -> `post.tasks.feed_daily_metrics` (`summary["ops"]`) |
| Prometheus endpoint | `GET /post/feed/ops/?format=prom` (Bearer `FEED_OPS_SCRAPE_TOKEN`, or a staff JWT) |
| JSON for humans | `GET /post/feed/ops/?minutes=15` (staff) · `python manage.py feed_slo --minutes 60` |
| Grafana | import `grafana_feed_dashboard.json` (datasource variable `DS_PROMETHEUS`) |
| Prometheus | `prometheus_scrape.example.yml` + `prometheus_feed_rules.yml` |

Set `FEED_OPS_SCRAPE_TOKEN` (e.g. `python -c "import secrets;print(secrets.token_urlsafe(32))"`) in the production env; empty = token access off.

## Runbook

### FeedAvailabilityBelowSLO
1. `python manage.py feed_slo --minutes 60` - which `events` are non-zero (`pool_error`, `pool_timeout`, `fallback_failed`)?
2. Sentry: look for the `feed: ranking failed` / `feed: unexpected error` errors (full traceback is logged).
3. `pool_timeout` -> a ranking query got slow (`manage.py feed_explain_analyze`, `loadtest/sql/feed_candidate_indexes.sql`). Raising
   `FEED_POOL_TIMEOUT_MS` only delays the fallback; fix the query / index.
4. Users are NOT getting 500s from ranking failures any more: they get the fallback feed. Real 5xx here means something outside
   ranking (serializer, DB down, auth).

### FeedP95AboveSLO
Check `latency_histogram_ms` in `feed_slo --json`, Postgres slow-query log, and whether Redis is down (then every page-2 is a
rebuild: see FeedRedisSnapshotDown). `FEED_LOADTEST_STATS` (staging only) shows queries per page.

### FeedServingFallback
Ranking is failing/timing out for a share of users. The fallback feed is "following by recency + recent public" - safe but dull.
Find the cause as above. A switch to turn ranking off deliberately does not exist on purpose; fix forward.

### FeedRedisSnapshotDown
Redis (cache) is failing. Effects, all fail-soft: page 1 is built per request (no 30 s candidate cache, no stampede lock), snapshots are
not frozen (page 2 rebuilds with the cursor's offsets - posts may shift slightly), the feed throttle **fails open** (no rate limit
while Redis is down), SLO counters fall back to per-process memory. Restore Redis; nothing needs replaying. Expect higher DB load
meanwhile - watch p95.

### FeedFallbackFailed
Even the simple queries failed - almost certainly the database. Users see an empty feed (HTTP 200, `X-Feed-Degraded: 1`).

## Retention (see `post/feed_retention.py`)
Nightly: `post-prune-old-events` 03:15 (PostEvent 30 d) · `post-prune-seen-views` 03:20 (seen markers older than 60 d, never below
seen window + 7 d) · `post-scrub-view-pii` 03:25 (ip / user agent of PostView rows older than 90 d). First big backlog:
`python manage.py feed_retention --dry-run`, then run it by hand off-peak until every line says complete.
Partitioning: `PARTITIONING.md`.

---

# Calls - SLO, metrics, alerts (X1)

## SLO
| SLI | Target | Where it comes from |
|---|---|---|
| Connect-success | **>= 95 %** | client: `connected / (connected + failed)` per participant's first connect attempt of a call |
| Setup time | p95 <= 5 s (ticket) | client: tap (initiate / accept) -> LiveKit room connected, ms histogram (0.5/1/2/3/5/10 s) |
| Ring -> answer rate | info only | server: `answered / (answered + rejected + missed + cancelled)`; `busy` tracked separately |
| Ring -> answer time | info only | server: seconds from `started_at` to first accept (3/6/10/15/20/30/45 s) |
| Reconnects | info / ticket | client: reconnect cycles per call, recovered vs lost |
| Failure reason | - | client classifies the connect error into: `timeout ice_failed signal_ws token_auth permission network_offline server other` |

## What is wired
| Piece | Where |
|---|---|
| Counters (5-min Redis buckets, no migration) | `message/call_ops.py`, keys `callops:*` (40 d) |
| Server hooks | ring outcome in `views/calls.py::_dispatch_result` (accept / reject / cancel), `busy` + `initiated` in `CallInitiateView`, `missed` in `tasks._finish_stale_call` |
| Client hooks | `lib/message/services/call_telemetry.dart` <- `CallManager` (connect ok/fail + setup ms + failure reason, reconnect cycles) -> `POST /message/calls/<id>/telemetry/`. Study-room calls are NOT instrumented (1:1 / group `CallManager` only). |
| Sentry breadcrumbs | client (done): `CrashReportingService.addBreadcrumb('call', ...)` (start, connect ok/fail, reconnecting/reconnected, disconnect, summary); server (done): `sentry_sdk.add_breadcrumb` per ring / connect event |
| Fast-burn alert (Sentry message + ERROR log) | celery beat `message-call-slo-watch` every 5 min -> `message.call_slo_watch` (last 15 min, >= 20 attempts) |
| Prometheus endpoint | `GET /message/calls/ops/?format=prom` (Bearer `CALL_OPS_SCRAPE_TOKEN`, falls back to `FEED_OPS_SCRAPE_TOKEN`, or a staff JWT) |
| JSON | `GET /message/calls/ops/?minutes=15` (staff) · `python manage.py call_slo --minutes 60` |
| Alert rules | `prometheus_call_rules.yml` (+ `learnscroll-call-ops` job in `prometheus_scrape.example.yml`) |
| Grafana | import `grafana_calls_dashboard.json` (datasource variable `DS_PROMETHEUS`): connect-success vs 95 % SLO, failure reasons, setup time, ring -> answer, reconnects, scrape health |

Env: `CALL_SLO_CONNECT_SUCCESS_PCT` (95), `CALL_SLO_ALERT_WINDOW_MINUTES` (15), `CALL_SLO_ALERT_MIN_ATTEMPTS` (20), `CALL_OPS_SCRAPE_TOKEN`.

## Blind spots (keep other probes)
Connect numbers are reported by the app: a client that is killed before it can POST, a ring push that never reached the callee, or a
LiveKit server that is completely down before the client even receives a token do not show up in the connect-success %. Keep the
LiveKit / uptime probe and watch `learnscroll_call_ring_outcomes{outcome="missed"}` for a sudden jump.

## Runbook

### CallConnectSuccessBelow95
1. `python manage.py call_slo --minutes 60` - `connect.failure_reasons`: which reason dominates?
2. `ice_failed` / `timeout` -> UDP / TURN path: check `deploy/livekit/livekit.yaml` (`rtc.tcp_port`, `turn`), firewall for UDP 50000-60000 (or the configured range), TURN/TLS on 443.
3. `signal_ws` -> LiveKit WSS unreachable: Caddy / certificate / DNS (`deploy/livekit/Caddyfile`), `LIVEKIT_URL` in the app build.
4. `token_auth` -> clock skew on the API server, token TTL (`CALL_TOKEN_TTL`), or a deploy that rotated `LIVEKIT_API_KEY/SECRET`.
5. `server` -> LiveKit node overloaded / room full (see `LIVE_MEDIA_CAPACITY.md`), check LiveKit logs.
6. `permission` / `network_offline` are user-side: they are in the number on purpose, but a spike right after an app release = a permission-flow regression.
7. Sentry: filter breadcrumbs `category:call` on the latest `CALL SLO ALERT` event and on `CallManager.connect` errors.

### CallSetupSlow
`setup_ms.histogram` in the JSON: slow only for callers (ring + token) or also callees? Check LiveKit region / TURN fallback (TCP) usage and `/message/calls/initiate/` latency.

### CallIceFailuresDominant
Same as step 2-3 above; a new ISP / carrier blocking UDP shows up as ice_failed + timeout together.

### CallReconnectsHigh
Rising `reconnects_per_call` with a flat connect-success = unstable networks or a LiveKit node restarting; check `recovered_pct` (lost reconnects become dropped calls).

### CallAnswerRateLow
Check push delivery (FCM / VoIP / CallKit) for the ring (`push_utils.send_incoming_call_push`), the `ring_timeout` sweeper (`CALL_RING_TIMEOUT_SECONDS`) and the `busy` share.

---

# Notifications (N10)

Counters per notification type (`core/notification_ops.py`, Redis keys `notifops:*`): **sent** (handed to FCM per token),
**delivered** (accepted by FCM - not a device receipt), **failed** (final, by FCM error code), **retried**, **skipped** (by
reason: push_disabled, type_muted, channel_off, focus, low_priority, dnd, quiet_hours, scope_muted, reminder_cap,
reminder_collapsed, dedupe, no_tokens) and **opened** (the app reports it, surface push / banner / list).

| What | Where |
|---|---|
| Prometheus endpoint | `GET /core/notification-ops/?format=prom` (Bearer `NOTIFICATION_OPS_SCRAPE_TOKEN` or `FEED_OPS_SCRAPE_TOKEN`, or a staff JWT) |
| JSON for humans | `GET /core/notification-ops/?minutes=60&by_type=1` (staff) |
| SLO alert (log ERROR + Sentry) | celery beat `core-notification-slo-watch` every 10 min -> `core.notification_slo_watch` |
| Prometheus | `prometheus_scrape.example.yml` (job `learnscroll-notification-ops`) + `prometheus_notification_rules.yml` |

SLO: **push delivery success >= 97 %** = delivered / (delivered + failed), dead tokens (uninstalled apps) excluded; judged
only with >= 50 pushes in the window. Counters are cumulative (use `rate()` / `increase()`); a Redis flush just restarts them at 0.

## notifications-n10
"User says the push never came" - open `?minutes=1440&by_type=1`, find the type: a big `skipped_by_reason` entry
(`quiet_hours`, `scope_muted`, `reminder_cap` ...) is the user's own setting working; `no_tokens` = no registered device;
`failed_by_code` `unregistered` = dead token (app removed); `unavailable` / `fcm_down` = FCM or our credentials.
`not_allowed` / `error` should stay near zero - if not, a skip branch is missing a reason.


---

# Product analytics + experiments (X3)

Privacy-safe funnel numbers (settings changed, notification opened, profile completed) and notification-copy A/B results
(`core/product_analytics.py`, `core/experiments.py`). **Aggregate counters only** - no user id, device id, IP or free text is
stored, values of settings are never recorded (only the field NAME), and nothing is counted unless the user's latest
`analytics` consent is *granted* (minors never). So every number on the dashboard is "of the users who agreed".

| What | Where |
|---|---|
| Prometheus endpoint | `GET /core/analytics/product/?format=prom` (Bearer `PRODUCT_ANALYTICS_SCRAPE_TOKEN`, empty = `FEED_OPS_SCRAPE_TOKEN`, or a staff JWT) |
| JSON for humans | `GET /core/analytics/product/?days=7` (staff) - funnels + per-variant open rate |
| Grafana | import `grafana_product_dashboard.json` (uid `learnscroll-product-x3`, datasource variable `DS_PROMETHEUS`, extra variable `experiment`) |
| Prometheus | `prometheus_scrape.example.yml` (job `learnscroll-product-analytics`) + `prometheus_product_rules.yml` |
| App side | `lib/services/analytics_service.dart` (reads `GET /core/analytics/config/`, sends only `settings_opened`, `profile_edit_opened`, `experiment_exposure`) |

Metrics: `learnscroll_product_{settings_opened,settings_changed,notification_opened,notification_variant,profile_edit_opened,
profile_field_added,profile_progress,profile_completed,experiment_exposure}_total`, `learnscroll_product_dropped_total{reason}`,
`learnscroll_product_analytics_enabled`, `learnscroll_experiment_running{experiment,kind}`. Counters are cumulative cache
counters (Redis in prod): use `increase()` / `rate()`; a Redis flush restarts them at 0 (Prometheus handles that as a reset).

Dashboard rows: **collection health** (switch, skipped-for-no-consent, invalid) -> **settings** (visits vs changes, top changed
fields, by section) -> **notifications** (opens by surface/type, copy-experiment open rate by variant) -> **profile funnel**
(edit opened -> field added -> completed, completeness buckets) -> **experiments** (running, flag exposures).

## product-analytics-x3
- **Dashboard empty / tiny numbers:** check `Skipped: no consent`. Until users switch on *Settings -> Data & privacy -> Analytics*
  (or an in-app consent prompt exists), almost nothing is counted - by design. Not a bug.
- **`ProductAnalyticsInvalidValues`:** a value outside the fixed vocabulary was dropped. Usually a new settings field/section or
  notification type that `product_analytics.py` does not know yet, or an old app sending something else. Nothing is stored for it.
- **Running a copy A/B:** set `EXPERIMENTS_ON=notif_copy_streak_at_risk_v1` (or `rollout_pct` in `settings.EXPERIMENTS`), edit the
  `b` copy first, wait for a few hundred pushes per variant, then read *Copy experiment - open rate by variant*. Kill switch =
  remove it from `EXPERIMENTS_ON` (assignment is deterministic, nothing to clean up). Security / payments / child-safety
  notifications and minors are never part of a copy test.
- **Opted-out user still seen?** Withdrawal takes effect at once (consent cache is invalidated on the change). Counters hold no ids,
  so there is nothing per person to delete or export.

---

# Backups & restore drill - alerts (PC2)

Three independent layers, so one broken layer never hides a failed backup:

| Layer | Fires when | Where |
|---|---|---|
| Sentry Crons (`SENTRY_DSN`) | a script exits non-zero (**including `die`** - PC2 fix: before, only `set -e` failures alerted), or a run does not check in within the monitor's schedule + grace | `deploy/backup/lib.sh` `run_guarded` |
| Django `check_system_health --alert` every 5 min | newest Postgres/media backup older than `BACKUP_MAX_AGE_HOURS` (26 h), last drill failed or older than `DRILL_MAX_AGE_DAYS` (8 d), dump not off-site | `core/ops_health.py`, `learnscroll-system-health.timer` |
| Prometheus rules (this folder) | no success for 26 h / last run failed / dump shrank below 50 % / not off-site / drill failed or late / metrics missing | `prometheus_backup_rules.yml` + node_exporter textfile collector |

Setup for the third layer: `NODE_EXPORTER_TEXTFILE_DIR=/var/lib/node_exporter/textfile_collector` in `/srv/learnscroll/.env` (directory writable by the
`learnscroll` user, node_exporter started with `--collector.textfile.directory=` pointing at it), add `prometheus_backup_rules.yml` to `rule_files`, reload.
Check: after one manual `backup_postgres.sh` run `cat $NODE_EXPORTER_TEXTFILE_DIR/learnscroll_backup_postgres.prom` shows `learnscroll_backup_last_run_ok{backup="postgres"} 1`.
What to do when an alert fires: `deploy/RESTORE_DRILL_CHECKLIST.md#when-an-alert-fires`.
