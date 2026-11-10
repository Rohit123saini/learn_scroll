# Live class media server / TURN capacity + cost per live-hour (X12)

> Status: **a planning model, not a measurement.** Nothing here was run. Every number marked *(assumption)* must be
> replaced with a value from the staging load test (`loadtest/LIVE_CLASS_PLAN.md`) and from your LiveKit / cloud bill before it
> is used for budgeting. Prices are deliberately NOT written down: they change, so put today's price in the CAPACITY table below.

## 1. What the code does that drives media cost (verified in this repo)

| Fact | Where | Consequence |
|---|---|---|
| Media runs on **LiveKit** (SFU). Config is env only: `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_WS_URL` / `LIVEKIT_URL` | `tuitionclass/livekit_utils.py` | Same code works on LiveKit Cloud or a self-hosted cluster; only the env changes. |
| By default a student token **can publish** (camera / mic / screen share allowed). It becomes **subscribe-only** only in a **stage-only** class: the host switches it on per class, or the platform default `LIVE_STAGE_ONLY_DEFAULT=1` turns it on when a class goes LIVE (`views/sessions.py::_perform_join`). In stage-only a student publishes only while ON STAGE (hand-raise / guest invite) | `_grants_for_role`, `_stage_can_publish`, `views/sessions.py` | **Sizing depends on this switch.** Stage-only: 1 teacher (+ screen share) publishing, N students subscribing = tiny uplink, **downlink = N x (teacher video + audio + shared screen)**. Default (not stage-only): up to N publishers are *possible*, so the SFU can also receive N uplinks and fan every published track out - size for the worst case or turn `LIVE_STAGE_ONLY_DEFAULT=1` on. Downlink is still what you pay for. |
| X12: a **minor with an unacknowledged recording notice is listen-only** (no mic / camera) | `live_safety.recording_blocks_publish`, `recording_consent.py` | Lowers publisher count in classes with minors; never raises it. |
| `max_participants` is set per room from `Classroom.max_participants` | `views/sessions.py` -> `ensure_room` | Natural cap per room: it is also the cap of one SFU room's fan-out. |
| Recording = **RoomComposite egress**, `grid` layout, one **MP4** uploaded to **S3** (`LIVEKIT_EGRESS_S3_*`) | `start_room_recording` | A composite egress is a headless Chrome + encoder: **CPU-heavy, about one dedicated egress worker slot per recorded room** *(assumption: 2-4 vCPU)*. It is billed/sized per recorded minute, independent of how many students watch. |
| X12 auto-record starts the egress by itself once the host is in (`recording_auto.py`) | `recording_auto.py` | A classroom with `recording_auto` on = **every** live class pays the egress cost, also when only 3 students come. Count `recording_auto` classrooms when sizing egress. |
| Replay is a plain S3/CDN file, not LiveKit | `recording_views.py`, `recording_save.py` | Replay views cost storage + CDN egress, nothing on the SFU. |
| Saved clips (lecture / reel / post) are re-encoded by Celery | `recording_save.py`, `tasks.save_recording_clip` | CPU in the **Celery worker** (4 slots in `deploy/systemd`), not on the media server. Size the worker separately. |

## 2. The model

Per live class, per hour:

```
downlink_Mbps  = students_subscribed x (teacher_video_kbps + teacher_audio_kbps + screenshare_kbps) / 1000
sfu_egress_GB  = downlink_Mbps x 3600 / 8 / 1000                 # data leaving the SFU toward students
recording_cost = egress_worker_hours x price_per_egress_hour     # 1.0 hour per recorded hour
storage_GB     = recording_Mbps x 3600 / 8 / 1000                # added to S3 per recorded hour
turn_GB        = sfu_egress_GB x turn_share                      # the part of media that has to be RELAYED
```

Inputs (all *(assumption)* until measured): teacher video 600-1200 kbps (720p simulcast layers decide what each student
gets), audio 32-48 kbps, screen share 300-800 kbps (mostly static slides), recording 2-4 Mbps, **turn_share 10-20 %**
(students on mobile data / strict NAT / carrier-grade NAT; India mobile networks are on the high side).

Worked example, **to be redone with measured values**: 100 students, 1 teacher video 900 kbps + audio 40 + slides 400
= 1.34 Mbps per student -> 134 Mbps downlink -> ~60 GB / hour leaves the SFU; at 15 % TURN share ~9 GB / hour is relayed.

## 3. TURN

* LiveKit's own server includes a TURN service; without it, students behind symmetric NAT cannot join at all (they would see
  "connected" but never receive video). **TURN/TLS on 443** matters most - school and office networks block UDP.
* TURN traffic is **relayed bytes**, so it is billed like normal egress. It is the one cost that grows when your users are on
  bad networks, not when you have more of them.
* Self-hosted: open UDP 3478 (TURN/UDP), TCP 5349 / 443 (TURN/TLS) and the WebRTC UDP port range on the firewall, and give
  TURN a real certificate for its own hostname.

## 4. CAPACITY table (fill in after the staging run)

> Scenarios **M2 / M3 / M5** below are defined in `loadtest/CALLS_STUDYROOM_PLAN.md` section 6 (X2), together with the calls / study-room
> node sizing (`loadtest/livekit_sizing.py`). `LIVE_CLASS_PLAN.md` does not define them.

| Item | Value to measure | Where it comes from |
|---|---|---|
| Max subscribers one SFU node holds at 720p before CPU > 70 % | ______ | `loadtest/CALLS_STUDYROOM_PLAN.md`, scenario M2 |
| Max concurrent composite egress per egress node | ______ | scenario M3 |
| Downlink per student (kbps, measured at p50 / p95) | ______ / ______ | LiveKit stats |
| TURN share of sessions | ______ % | LiveKit "connection type" stats |
| Cloud / LiveKit price per downlink GB, per egress hour, per stored GB | ______ | the bill, today |
| **Cost per live hour at 100 students, recorded** | = downlink GB x price + egress hour x price + storage GB x price | the model above |

## 5. Decisions this should feed

0. **Listener mode (stage-only) default.** `LIVE_STAGE_ONLY_DEFAULT` is **0** (old behaviour). Decide it before the capacity run: the numbers in section 4 are only valid for the mode you measured (measure both if the host can switch it).
1. **Auto-record default.** The cheapest way to cut the bill is not to record every class: keep `recording_auto` opt-in per
   classroom (it already is) and show teachers that a recorded hour costs an egress hour.
2. **Class size cap.** `Classroom.max_participants` is also the SFU fan-out cap. Set the platform maximum from the "max
   subscribers per node" measurement, not from the UI.
3. **Egress node count** = peak simultaneous recorded classes (the evening peak of auto-record classrooms), not average.
4. **Simulcast on the teacher's camera** so weak phones receive the low layer instead of forcing TURN retries.
5. **Alert** on: egress queue (a class that should record but did not start), TURN share jump, SFU CPU > 70 %.
