# Live class load test plan (X12: chat safety, moderation, recording notice)

> Plan only - **nothing was run** while writing it. Run on **staging** (Postgres, real Redis, `DEBUG=False`, several ASGI
> workers, Celery worker + beat, LiveKit sandbox project). It extends `README.md` (T3.6); same harnesses, same fixtures
> (`seed_loadtest --confirm-staging`). The existing `LiveJoinUser` already covers join + WebSocket; this plan adds what X12
> put on the **chat hot path** and on the **moderation endpoints**.

## 1. What changed on the hot path (why this needs its own test)

`POST /tuitionclass/chat-messages/` (`views/live_chat.py::perform_create`) now does, per message, in this order:

| Step | Cost | Fails open? |
|---|---|---|
| `live_abuse.ensure_can_chat` - mute lookup | **1 DB query** (`session.participants` by user, `chat_muted_until > now`) | no (a DB error is a 500, same as before) |
| `_enforce_slow_mode` (existing) | 1 cache op | yes |
| `check_flood` / `check_duplicate` | 2-3 cache ops | yes |
| `is_host_tier` + `live_abuse.exempt` | up to 2 small DB lookups (staff row) | exempt: yes |
| `filter_chat_text` | CPU only (regex over the word list) | yes |
| `strict_contact_rule` -> `minors_present` | 1 cache get; on a miss 1 DB `exists()` (cached 60 s) | yes |
| save + `broadcast_to_session` (existing) | 1 insert + 1 channel-layer send | - |

The mute lookup and the staff lookups are the new per-message DB cost. The test must show they do not move chat p95.

## 2. Scenarios

| ID | Scenario | Load | Pass |
|---|---|---|---|
| **C1** | Normal class chat, one room | 250 students, 1 message / 20-40 s each, 1 teacher | `POST chat-messages` p95 < 400 ms, errors < 0.5 %, DB queries per message <= 6 (django-debug / `pg_stat_statements`) |
| **C2** | Chat flood from a few bad actors inside C1 | 5 users x 20 msg/s for 60 s | the 5 get 429 `chat_flood` (then auto-mute 403 `chat_muted`); everyone else's p95 unchanged (+/-10 %); no 5xx |
| **C3** | Duplicate + word-filter storm | 20 users repeat the same line / a blocked word | 409 `duplicate_message` / 400 `message_blocked`; audit rows written (`LiveModerationEvent`) without slowing C1; strikes -> auto-mute at the 3rd |
| **C4** | Minor in the room, contact info | 1 minor joins; 20 users post phone numbers / links | every one of them gets 400 `contact_info_blocked`, teacher's links still go through |
| **M1** | Moderation burst | 1 moderator, 60 actions/min (warn / mute / kick) + 200 reports in 5 min from 100 users | throttle scopes (`live_report` 10/min, `live_mod_action` 60/min) answer 429 with `retry_after`, not 500; report budget (`LIVE_REPORT_MAX_PER_SESSION` 5, `..._PER_HOUR` 20) holds; queue GET p95 < 500 ms at 500 open reports |
| **R1** | Recording-notice storm | recording starts with 150 students in the room, 30 of them minors | `on_recording_started` locks the 30 in one LiveKit call; all 30 `POST recording-notice/ack/` inside 30 s: p95 < 500 ms; no student blocked by the notice; adults never gated |
| **R2** | Recording-ready push | one class of 5,000 attendees ends | `notify_recording_ready` runs once per egress (re-deliver the webhook 3x: still one push), bell rows for all, push only for N9-allowed users, task < 60 s, Celery queue depth recovers < 2 min |

## 3. Failure injection (the "fails open" claims)

* Stop Redis for 60 s during C1: chat must keep working (flood/duplicate/strike guards step aside), only slow mode and the
  scope throttle fall back. Expect log lines `live_abuse: cache counter failed`, no 5xx.
* Make LiveKit unreachable during a kick and a recording start: kick returns the LiveKit error (unchanged); recording start,
  notice and ack must **not** turn into errors (`recording_consent` is best-effort).
* Make `user_profile.services.file_report` raise: a classroom report must still be saved (201), only the platform mirror is skipped.

## 4. How to run (extend the existing files, do not fork them)

* `locustfile.py` (written in X12 L-D #6, **never run**): `LiveChatUser` (C1), `LiveAbuser` (C2/C3/C4, ~2 % of users), `LiveModerator`
  + `LiveReporter` (M1). They only spawn with `LIVE_ONLY=1` (the old feed / chat-ws / join mix is switched off then) and have their
  own gate `LIVE_THRESHOLDS`:
  `LIVE_ONLY=1 PROFILE=baseline locust -f loadtest/locustfile.py --headless --host https://staging.example.com --csv loadtest/out/live1`
  Knobs: `LIVE_CHAT_MIN/MAX` (C1 cadence), `LIVE_ABUSE_RATE` (msg/s per abuser, default 20), `LIVE_MOD_RATE` (default 1/s = the
  60/min throttle), `LIVE_REPORTER_WEIGHT` (0 = off), `LIVE_BLOCKED_WORD` (must be in the word list), `LIVE_MOD_MUTE=1` (moderator also
  mutes random students - off by default because it would mute the C1 chatters). The CSV has one `CODE` row per (abuse kind, status).
* `k6.js`: `-e LIVE_CHAT=1` adds C1 (REST chat) + C2 (60 s spike, `ABUSE_RPS`, default 100/s, abusers use the upper half of the
  accounts). Run it twice - with and without `-e LIVE_ABUSE_OFF=1` - and compare `live_chat_normal` p95 (C2 pass: within +/-10 %).
* **Not scripted:** R1 (recording-notice ack storm) and R2 (recording-ready push) need a LiveKit egress start / webhook replay; do
  those by hand on staging. C3's "strike -> auto-mute at the 3rd" is visible in the `CODE` rows (403 chat_muted appears) but not asserted.
* Read these during the run: `pg_stat_activity` (connections), `pg_stat_statements` (mute lookup), `redis-cli INFO` (ops/s),
  Celery `inspect stats`, LiveKit dashboard (subscribers, egress), `LiveModerationEvent` row growth per minute.

## 5. Things to look at in the numbers

1. **Mute lookup** - the cache is already in (X12 L-D #4: `live_abuse.chat_mute_until`, `LIVE_MUTE_CACHE_SECONDS`, default 20 s, 0 = off;
   `apply_chat_mute` / `clear_chat_mute` refresh it at once). Run C1 once with `LIVE_MUTE_CACHE_SECONDS=0` and once with the default and
   compare p95 and `pg_stat_statements` calls for the mute query - that is the real measurement of whether the cache was needed.
2. **Audit growth**: C2/C3 write one `LiveModerationEvent` per blocked line. 100 abusers x 3 lines/s would be 300 rows/s. The
   strike -> mute step caps this for real users, but a bot with a fresh account per minute is not capped. Decide a retention
   job (e.g. delete `filter_block`/`filter_mask` rows older than 30 days) if the table grows faster than expected.
3. **`moderator_ids` / `is_host_tier`** hit the staff table per call; fine at this load, but confirm in `pg_stat_statements`.
