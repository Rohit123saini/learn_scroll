# Message calls + study rooms: load & capacity plan (X2)

> Plan only - **nothing was run** while writing it (read-only round: code was read, `py_compile` only; no test, no
> migration, no seed). Run on **staging** (Postgres, real Redis, `DEBUG=False`, several ASGI workers, Celery worker + beat,
> a LiveKit node you can lose). It extends `README.md` (T3.6) and `LIVE_CLASS_PLAN.md` (X12, tuitionclass only): same
> harnesses, a new fixture command, new scenarios. Anything "expected" below comes from reading the code - the run decides.

## 1. What is tested, and what is not

| Layer | Tool | Scenarios |
|---|---|---|
| Django REST + Channels + Redis + Postgres (call state, token minting, study-room join/state/hand, WS fan-out) | `locustfile.py` with `CALLS_ONLY=1` (full set), `k6.js` with `-e CALLS=1` (CL1 + SR1/SR2 only) | CL1-CL7, SR1-SR6, WH1 |
| LiveKit **media plane** (audio/video tracks, SFU CPU, NIC, TURN) | `lk load-test` (LiveKit CLI) against a staging node | M-series, section 6 |
| Node count / ports / TURN / webhook rate arithmetic | `livekit_sizing.py` | starting plan, redo after section 6 |

Locust and k6 **do not publish media**. They get a LiveKit token from Django and stop there (the token mint, the
`list_room_identities` cap check and the webhooks are the Django-side cost). Media cost is measured separately.

## 2. Fixtures

```bash
# staging DB only; refuses without --confirm-staging. Everything is prefixed lt_cs_ / "LOADTEST ..."
python manage.py seed_calls_loadtest --confirm-staging --out loadtest/calls_fixtures.json
#   defaults: 200 pairs, 10 busy sets x 3 callers, 1 group of 51 (cap 50 + 1 probe), 20 study-room groups x 50
python manage.py seed_calls_loadtest --reset-state --confirm-staging     # between two runs: end leftover calls / rooms
python manage.py seed_calls_loadtest --cleanup --confirm-staging         # remove everything it made
```

* Pairs are **disjoint** on purpose: a user can be in one call at a time (`call_state.busy_status`); two VUs on one account turn every call into `409 already_in_call`.
* Study-room groups are **private** by default. A public group answers the first join with `403 rules_not_accepted` and puts new sessions behind a waiting room (`STUDY_ROOM_RULES_REQUIRED_PUBLIC`, `STUDY_ROOM_PUBLIC_GROUP_WAITING_ROOM`); pass `--public` to test exactly that (SR6).
* Accounts get an **adult** date of birth: minor-safety rules (DM study room block, masked names, recording notice) would change what the endpoints return and skew the numbers. Test those separately.
* Tokens are minted through `login.token_issuance` (no 1,500 logins from the injector). `loadtest/calls_fixtures.json` is secret-ish: git-ignore it like `fixtures.json`.
* Seed enough: `-u` above the fixture count makes the extra users idle (a `[calls] ... idle` line is printed once).

## 3. Scenarios

### 3.1 Message calls (`CALLS_MIX=pairs:..,busy:..,group:..`)

| ID | Scenario | Load | Pass |
|---|---|---|---|
| **CL1** | 1:1 call lifecycle, steady | 200 pairs = 200 concurrent calls (400 participants), accept 70 % | initiate p95 < 800 ms, accept p95 < 600 ms, end p95 < 600 ms, errors < 1 %; each response carries a `livekit_token` |
| **CL2** | Non-happy endings | same run: 10 % reject, 10 % caller cancels, 10 % nobody answers | no 5xx; the unanswered ones are ended by the Celery sweeper after `CALL_RING_TIMEOUT_SECONDS` (45 s) - MISSED row + push once; `Call` rows left `ringing`/`ongoing` after the run = 0 |
| **CL3** | A->B and C->B at the same instant | 10 busy sets x 3 callers, released together each round | **exactly one** 200 per set, the rest `409 callee_busy`. Two 200s = `call_state.lock_users` race is open (the `CHECK CL3` row fails the run) |
| **CL4** | Group-call cap | one 51-member group; members accept (<= `CALL_GROUP_RING_CAP` = 10 ringing) or join inside `GROUP_JOIN_WINDOW` | cap-1 joins succeed, the rest `409 call_full`; more than cap-1 successes = `join_call` overfills (`CHECK CL4`) |
| **CL5** | Ring latency | the callee holds `ws/chat/<conv>/` | caller presses call -> callee socket gets `incoming_call`: p95 < 1500 ms, lost < 2 % (`WS call ring event`) |
| **CL6** | Telemetry + token refresh | 50 % of calls report connect + summary, 10 % refresh the token | `429` is a **designed** answer (`call_telemetry` 120/hour/user, refresh 30/hour/user) and is counted in a `CODE` row, not a failure; no 5xx |
| **CL7** | Per-IP initiate throttle (`call_initiate_ip` 20/min **per IP**) | `CALLS_SPOOF_XFF=0 CALLS_EXPECT_429=1`, 200 pairs from one injector IP | 429 `throttled` appears for most initiates, with `Retry-After`, no 5xx. **This is a finding, not just a test**: see section 5.1 |

### 3.2 Study rooms (`CALLS_MIX=rooms:..`)

| ID | Scenario | Load | Pass |
|---|---|---|---|
| **SR1** | Join storm | `rooms_storm`: 20 rooms x 50 inside `STUDY_JOIN_WINDOW` = 10 s (= ~100 joins/s) | join p95 < 2000 ms, errors < 1 %, WS connect p95 < 1000 ms, no 5xx; LiveKit API call rate (below) does not push join p95 over the gate |
| **SR2** | Steady 50-person rooms | `rooms_1k`: 20 rooms x 50 = 1,000 members, hold 4-10 min, rejoin 20-90 s later; `STUDY_JOIN_WINDOW=300` | same gates; `GET room/` p95 < 800 ms at 1,000 pollers / 30 s |
| **SR3** | Whiteboard `draw_point` relay | 2 drawers per room, 30 Hz while drawing (8 s on / 12 s off); try 60 and 120 Hz = the unthrottled Flutter client | relay latency (`WS study draw relay`) p95 < 300 ms; frames lost < 1 % (`CHECK SR3`); ASGI CPU and Redis ops/s stay below the alarm line. 1 drawer at 100 Hz = 4,900 deliveries/s per room |
| **SR4** | Board save contention | 3 writers PUT `state/` every 20 s, 100 KB each (cap is 2 MB) | PUT p95 < 1000 ms, errors < 0.5 %; `409 version_conflict` is **designed** (counted, not a failure); no 413/428/5xx |
| **SR5** | Everything else a member does | `room/` poll 30 s, raise-hand toggles 60-180 s, token refresh 20 % | hand `429` (30/min) counted, not a failure; no 5xx |
| **SR6** | Public group gate | seed with `--public`: first join -> `403 rules_not_accepted` -> `POST rules/` -> `202 waiting_room` -> host `admit_all` | every member admitted inside `STUDY_WAIT_SECONDS` (90 s); the poll of `join/` every 4 s from 49 members does not move host-side latency |

### 3.3 LiveKit -> Django webhooks (WH1)

`STUDY_WEBHOOK=1` (needs `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` in the injector's env, the same values as staging): each
simulated member also POSTs a signed `participant_joined` / `participant_left` to `/message/livekit/webhook/` (HS256 JWT
with the body `sha256`, as LiveKit signs it). Pass: p95 < 500 ms, errors < 0.5 %, presence rows (`study_room_presence`)
consistent after the run, **a replayed event id does nothing the second time**. Only use this against staging; the webhook
URL is open to anyone who can sign.

## 4. Run

```bash
pip install -r loadtest/requirements.txt          # locust + websocket-client
# 0) smoke FIRST (10 users). If it is not green, do not run 1k.
CALLS_ONLY=1 PROFILE=calls_smoke CALLS_MIX=pairs:50,busy:10,group:1,rooms:39 \
  locust -f loadtest/locustfile.py --headless --host https://staging.example.com --csv loadtest/out/calls_smoke

# 1) calls: 200 concurrent 1:1 calls + CL3 + CL4
CALLS_ONLY=1 PROFILE=calls_200 CALLS_MIX=pairs:200,busy:30,group:1 \
  locust -f loadtest/locustfile.py --headless --host https://staging.example.com --csv loadtest/out/cl --html loadtest/out/cl.html

# 2) study rooms steady, then the storm
CALLS_ONLY=1 PROFILE=rooms_1k    STUDY_JOIN_WINDOW=300 CALLS_MIX=rooms:1000 locust -f loadtest/locustfile.py --headless --host ... --csv loadtest/out/sr2
CALLS_ONLY=1 PROFILE=rooms_storm STUDY_JOIN_WINDOW=10  CALLS_MIX=rooms:1000 locust -f loadtest/locustfile.py --headless --host ... --csv loadtest/out/sr1

# 3) unthrottled drawing (the Flutter client has no throttle)
CALLS_ONLY=1 PROFILE=rooms_1k STUDY_DRAWERS=2 STUDY_DRAW_HZ=100 CALLS_MIX=rooms:1000 locust ...

# 4) k6 (REST + WS-connect side only)
k6 run -e BASE=https://staging.example.com -e CALLS=1 -e PROFILE=calls_smoke loadtest/k6.js
k6 run -e BASE=https://staging.example.com -e CALLS=1 -e PROFILE=rooms_storm  loadtest/k6.js

# 5) clean up between runs and at the end
python manage.py seed_calls_loadtest --reset-state --confirm-staging
python manage.py seed_calls_loadtest --cleanup --confirm-staging
```

Profiles (`PROFILE=`): `calls_smoke` 10 users | `calls_200` ramp to 200 over 3 min, hold 12 min | `rooms_1k` 500 -> 1,000 over 4 min, hold 16 min | `rooms_storm` 1,000 users in 20 s, hold 9 min.

`CALLS_THRESHOLDS`: Locust builds the gate from the classes that actually run (`_calls_thresholds()`), exits non-zero on a
breach, and prints `no samples recorded` for a scenario that did not run. CSV rows: one per endpoint name, plus `CODE` rows
(counters of designed answers) and `CHECK` rows (correctness verdicts: CL3, CL4, SR3).

Knobs (env): `CALLS_MIX`, `CALLS_SPOOF_XFF` (1), `CALLS_EXPECT_429` (0), `CALL_OUTCOMES` (70,10,10,10), `CALL_HOLD_MIN/MAX` (20/60 s),
`CALL_MIN_CYCLE` (7 s - `call_initiate` is 10/min per user), `CALL_NOANSWER_WAIT` (55 s), `CALL_RING_WS`, `GROUP_CALL_CAP` (50),
`GROUP_JOIN_WINDOW`, `STUDY_ROOMS`, `STUDY_ROOM_USERS`, `STUDY_JOIN_WINDOW`, `STUDY_HOLD_MIN/MAX`, `STUDY_DRAWERS`,
`STUDY_DRAW_HZ`, `STUDY_DRAW_ON/OFF`, `STUDY_LAT_SAMPLE`, `STUDY_MAX_LOSS`, `STUDY_BOARD_WRITERS/EVERY/KB`, `STUDY_ROOM_POLL`,
`STUDY_WAIT_SECONDS`, `STUDY_WEBHOOK`.

**Injector caveats.** One injector box saturates before the system does (~2-3k sockets per core). SR3 at 100 Hz makes each
receiver parse 4,900 frames/s per drawer-room: use `locust --master/--worker` and **NTP-sync every injector** - draw latency
is `receiver clock - sender timestamp`, which is only meaningful on one host or with synced clocks. k6 `SharedArray` fixtures
are per VU id, so `-u` above the fixture count idles the extra VUs.

## 5. What reading the code already says (check each in the run)

1. **`call_initiate_ip` = 20/min per IP** (`settings.py`). A college / school WiFi or a mobile CGNAT puts many real users behind one IP: the 21st call attempt in a minute gets 429 for everybody there. Also, with `NUM_PROXIES` unset, a client-supplied `X-Forwarded-For` dodges the limit (the harness uses that on purpose - `CALLS_SPOOF_XFF`). Decide: raise the IP limit, or key it by user only; set `NUM_PROXIES` in production. CL7 measures how bad it is.
2. **`draw_point` is unthrottled** (Flutter sends on every touch-move) and `group_send`s to every socket in the room; only note ops / drag are limited per user. SR3 gives the real cost; the usual fix is a client throttle (~30 Hz) plus a server per-user cap.
3. **`StudyRoomJoinView` and `StudyRoomStateView` (PUT, 2 MB, row lock) have no throttle.** SR1 / SR4.
4. A **non-host join makes a LiveKit `list_room_identities` API call inside the request** (cap check): a join storm is also a LiveKit API storm. Watch join p95 vs the LiveKit API rate in SR1.
5. The join response lists **every member of the group, not of the room**: payload grows with group size (a 1,000-member group with 50 in the room). Compare response bytes in SR1 on a 50-member and a large group.
6. **Every LiveKit participant join/leave = a Django webhook POST** (row lock). 1,000 members = 2,000 POSTs per visit cycle; in a 10 s storm that is a burst. WH1.
7. **`rtc.port_range` 50000-60000 = 10,001 UDP ports** and TURN relay 30000-40000. At 2 PeerConnections per participant one node tops out near 5,000 participants *before* CPU or NIC does (unless `rtc.udp_port` single-port mux is set). `livekit_sizing.py` prints this as `by_udp_ports`.
8. **Harness bug found and fixed in X2:** `ChatWsUser._read_loop` (`locustfile.py`) used to stop at the first 10 s of silence, so the 15-30 s send cadence would have shown "WS chat echo" mostly *lost*. It now treats the read timeout as idle. (Earlier `chat echo` numbers from any run of the old file are not trustworthy.)
9. `deploy/LIVE_MEDIA_CAPACITY.md` refers to scenarios **M2 / M3** that `LIVE_CLASS_PLAN.md` never defined: they are defined in section 6 here (M2 = SFU node subscribers, M3 = egress).

## 6. Media plane + node sizing

Starting plan (arithmetic, assumptions printed with it):

```bash
python loadtest/livekit_sizing.py                                           # 200 calls + 2 group calls + 20 x 50 rooms
python loadtest/livekit_sizing.py --calls 500 --rooms 60 --group-calls 5 --recorded-rooms 5
python loadtest/livekit_sizing.py --rooms 100 --room-size 50 --join-window 10 --json
python -m unittest loadtest.tests_livekit_sizing
```

It reports forwarded Mbps in/out, vCPU, nodes by CPU / NIC / UDP ports / TURN ports, the binding limit, +1 spare node,
whether the biggest single room fits one node (rooms never span nodes), egress workers, and the Django-side rates (webhooks/s,
study joins/s, `list_room_identities` calls/s). `mbps_per_core` is the one number that must come from a measurement.

Measure one node with the LiveKit CLI (flag names: check `lk load-test --help` for your CLI version):

| ID | What | How | Read |
|---|---|---|---|
| **M1** | 1:1 calls on one node | N rooms of 2 publishers (audio + video), ramp N until CPU 70 % | max concurrent 1:1 calls per node -> `--mbps-per-core` |
| **M2** | 50-person study room | 1 room: 1 video + 2 audio publishers, 49 subscribers; then 10 rooms; then 20 | SFU CPU, egress Mbps, subscriber packet loss; max rooms per node |
| **M3** | Composite egress | start recordings in parallel until the egress node saturates | slots per node -> `--egress-slots-per-node` |
| **M4** | Port ceiling | participants until `ice` failures / refused allocations | real cap vs the 10,001-port arithmetic |
| **M5** | TURN | force relay (block UDP from a test client) and repeat M2 | relayed Mbps / CPU; TURN share of real users from LiveKit stats |
| **M6** | Node loss | kill the node during M2 | clients reconnect time; `room_finished` -> call / presence cleanup (C1 sweeper, S5 sweeper) |

Fill the CAPACITY table in `deploy/LIVE_MEDIA_CAPACITY.md` section 4 from M2 / M3 / M5, and rerun `livekit_sizing.py` with
the measured values (`--mbps-per-core`, `--cores-per-node`, `--nic-gbps`, `--turn-share`).

## 7. Failure injection

* **Redis down 60 s** during SR2: chat/draw relay stops (channel layer is Redis) - clients reconnect; REST (join, state PUT) must keep working; no 5xx storm after Redis returns; mute/limiter caches fall back (see LIVE_CLASS_PLAN failure notes).
* **LiveKit unreachable** during CL1 / SR1: `initiate` / `join` return a clear error (`503` LiveKit not configured / upstream error), the call row is not left `ringing` forever (sweeper), and a study-room join does not hang the ASGI worker (check the timeout on `list_room_identities`).
* **Celery stopped** during CL2: no-answer calls stay `ringing` until the worker is back, then the sweeper ends them in one pass, once (no duplicate MISSED push).
* **ASGI worker killed** during SR3: sockets reconnect; drawers resume; a lost relay window is visible in `CHECK SR3`, not silent.
* **Webhook replay x3** (WH1): same event id three times = one presence row / one attendance credit.
* **Postgres connection cap**: run SR1 with `CONN_MAX_AGE` as in production and read `pg_stat_activity`; the join storm must not exhaust connections.

## 8. Read these during every run

`pg_stat_activity` (connections by state), `pg_stat_statements` (join, state PUT, call lock queries), row-lock waits on `StudyRoomState` and
the call rows, `redis-cli INFO` (ops/s, clients, memory) and `--latency`, channels group size / `ChannelFull` log lines, Celery
`inspect stats` + queue length, ASGI process CPU / RSS / fds, LiveKit dashboard (rooms, participants, subscriber loss, node CPU,
egress), and the injector's own CPU. 429s in the `CODE` rows are throttles working as designed; 429 in a non-`CODE` row means the
injector is mis-configured (shared account, no XFF) - not a capacity finding.

## 9. Not covered (deliberately)

Media quality (MOS, jitter) and real-device battery; push delivery to FCM/APNs (mocked on staging); login / OTP; call recording
(C16 / X12 plans); minor-safety paths (separate fixtures); tuitionclass live sessions (`LIVE_CLASS_PLAN.md`).
