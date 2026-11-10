#!/usr/bin/env python3
"""
loadtest/livekit_sizing.py - LiveKit (SFU + TURN) node sizing for message calls and study rooms (X2).

Same spirit as sizing.py: a transparent back-of-envelope model, NOT an oracle and NOT a measurement. Every output is
arithmetic on the inputs or on an ASSUMPTION listed in `ASSUMPTIONS`. Nothing here was run against a LiveKit node.

Workflow:
  1. run it with defaults -> a STARTING node count for the peak you describe;
  2. measure one node with `lk load-test` (CALLS_STUDYROOM_PLAN.md section 6) and read CPU / NIC / packet loss;
  3. feed the measurement back with --mbps-per-core / --cores-per-node / --nic-gbps and compare. Where model and
     measurement disagree by > 30 %, trust the measurement.

    python loadtest/livekit_sizing.py                                  # 200 1:1 calls + 20 x 50-person rooms
    python loadtest/livekit_sizing.py --calls 500 --rooms 60 --room-size 50 --group-calls 5
    python loadtest/livekit_sizing.py --rooms 100 --room-size 50 --join-window 10 --json
    python loadtest/livekit_sizing.py --mbps-per-core 220 --cores-per-node 16      # after a measurement

No third-party imports; no Django.

What it models (all in "forwarded Mbps": what the SFU receives = ingress, what it sends = egress):
  * 1:1 calls     : 2 participants, each publishes mic (+ camera on video calls) and receives the other side.
  * group calls   : g participants (server cap CALL_GROUP_MAX_PARTICIPANTS = 50), every mic is forwarded, only `tiles`
                    video tiles are subscribed per participant (the Flutter grid shows a few).
  * study rooms   : s participants, a few publishers (host camera + mics + optional screen share), everybody subscribes.
  * UDP port cap : livekit.yaml rtc.port_range 50000-60000 = 10,001 ports; each PeerConnection takes one.
  * TURN         : the share of participants that must be relayed; relay range 30000-40000 = 10,001 ports.
  * Django side  : webhook POSTs (2 per participant visit) and, on a study-room join of a non-host, one LiveKit
                   `list_room_identities` API call - both are rates, so a join storm shows up as a req/s number.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass

# --- assumptions that are NOT derived from this repo's code ---------------------------------------------------------
ASSUMPTIONS = {
    "audio_kbps": "32 kbps per Opus mic track (voice, DTX on) - measure with `lk load-test` / LiveKit stats",
    "video_kbps": "700 kbps per phone camera at 540p-720p simulcast top layer; the low layer is video_low_kbps",
    "video_low_kbps": "150 kbps for the lowest simulcast layer (what a small grid tile receives)",
    "screen_kbps": "400 kbps for slides / whiteboard screen share (mostly static)",
    "mbps_per_core": "120 forwarded Mbps (ingress + egress) per vCPU at <= target utilisation - THE number to replace with a measurement",
    "cpu_per_participant": "0.002 vCPU per connected participant (signalling, DTLS/SRTP keep-alive, stats) on top of the media cost",
    "cores_per_node": "8 vCPU per LiveKit node (deploy/livekit/README.md section 6 talks about an 8-core node)",
    "nic_gbps": "10 Gbps NIC on the node; only nic_util of it is planned for media",
    "pcs_per_participant": "2 PeerConnections (publisher + subscriber) -> 2 server UDP ports each, unless rtc.udp_port (single-port mux) is set",
    "turn_share": "15 % of participants relayed through TURN (college / school WiFi, carrier-grade NAT) - measure from LiveKit 'connection type'",
    "egress_slot": "one RoomComposite egress = 1 egress worker slot (headless Chrome + encoder, ~2-4 vCPU) - see LIVE_MEDIA_CAPACITY.md",
    "webhook_per_visit": "2 Django webhook POSTs per participant visit (participant_joined + participant_left); room_started / room_finished extra per room",
    "rooms_cannot_span_nodes": "a LiveKit room lives on ONE node; a single room must fit one node (the model checks the biggest room)",
}


@dataclass
class Inputs:
    # ---- workload at peak ----
    calls: int = 200                   # concurrent 1:1 calls (CL1 / calls_200 profile = 200 pairs)
    call_video_share: float = 0.5      # fraction of 1:1 calls that are video calls
    group_calls: int = 2               # concurrent group calls
    group_size: int = 50               # participants per group call (cap is CALL_GROUP_MAX_PARTICIPANTS = 50)
    group_video_tiles: int = 4         # video tiles each participant of a group call subscribes
    group_video_publishers: int = 4    # participants with the camera on
    rooms: int = 20                    # concurrent study rooms
    room_size: int = 50                # participants per study room
    room_mic_publishers: int = 3       # open mics (host + 1-2 people talking)
    room_video_publishers: int = 1     # cameras on (host)
    room_screenshare: float = 0.2      # fraction of rooms with a screen share running
    room_low_layer_share: float = 0.5  # fraction of room subscribers that receive the LOW simulcast layer
    # ---- bitrates (kbps) ----
    audio_kbps: float = 32.0
    video_kbps: float = 700.0
    video_low_kbps: float = 150.0
    screen_kbps: float = 400.0
    # ---- node ----
    cores_per_node: int = 8
    mbps_per_core: float = 120.0
    cpu_per_participant: float = 0.002
    target_utilization: float = 0.6
    nic_gbps: float = 10.0
    nic_util: float = 0.5
    port_range_start: int = 50000
    port_range_end: int = 60000
    pcs_per_participant: int = 2
    turn_share: float = 0.15
    turn_relay_start: int = 30000
    turn_relay_end: int = 40000
    # ---- recording ----
    recorded_rooms: int = 5            # rooms with a composite egress running at the same time (study rooms are NOT auto-recorded; live classes are)
    egress_slots_per_node: int = 4
    # ---- Django side ----
    join_window_seconds: float = 10.0  # SR1: how long the join storm of one room lasts
    avg_visit_minutes: float = 25.0    # steady-state: how long a participant stays (webhook rate)
    spare_nodes: int = 1               # N+1: one node may fail without an outage


def _ceil(x: float) -> int:
    return int(math.ceil(x - 1e-9))


def _kbps_to_mbps(k: float) -> float:
    return k / 1000.0


def _one_to_one(i: Inputs) -> dict:
    """Per call: 2 participants. Each publishes mic (+ camera on video calls) and subscribes to the other one."""
    per_side = i.audio_kbps + i.call_video_share * i.video_kbps
    ingress = i.calls * 2 * per_side
    egress = i.calls * 2 * per_side            # the SFU forwards each side's tracks to the other side
    return {"participants": i.calls * 2, "ingress_mbps": _kbps_to_mbps(ingress), "egress_mbps": _kbps_to_mbps(egress),
            "biggest_unit_egress_mbps": _kbps_to_mbps(2 * per_side), "biggest_unit_participants": 2 if i.calls else 0}


def _group_calls(i: Inputs) -> dict:
    g = max(i.group_size, 0)
    if i.group_calls <= 0 or g == 0:
        return {"participants": 0, "ingress_mbps": 0.0, "egress_mbps": 0.0, "biggest_unit_egress_mbps": 0.0,
                "biggest_unit_participants": 0}
    pv = min(i.group_video_publishers, g)
    tiles = min(i.group_video_tiles, max(g - 1, 0))
    up = g * i.audio_kbps + pv * i.video_kbps
    # each participant receives every OTHER mic (the SFU forwards all audio) and `tiles` low-layer video tiles
    per_listener = (g - 1) * i.audio_kbps + tiles * i.video_low_kbps
    down = g * per_listener
    return {"participants": g * i.group_calls, "ingress_mbps": _kbps_to_mbps(up * i.group_calls),
            "egress_mbps": _kbps_to_mbps(down * i.group_calls), "biggest_unit_egress_mbps": _kbps_to_mbps(down),
            "biggest_unit_participants": g}


def _study_rooms(i: Inputs) -> dict:
    s = max(i.room_size, 0)
    if i.rooms <= 0 or s == 0:
        return {"participants": 0, "ingress_mbps": 0.0, "egress_mbps": 0.0, "biggest_unit_egress_mbps": 0.0,
                "biggest_unit_participants": 0}
    mics = min(i.room_mic_publishers, s)
    cams = min(i.room_video_publishers, s)
    shares = i.room_screenshare                      # expected screen shares per room (0..1)
    up = mics * i.audio_kbps + cams * i.video_kbps + shares * i.screen_kbps
    low = i.room_low_layer_share
    per_sub_video = cams * (low * i.video_low_kbps + (1 - low) * i.video_kbps)
    per_sub = mics * i.audio_kbps + per_sub_video + shares * i.screen_kbps
    down = s * per_sub                               # (the publishers do not receive their own track: <= 1/s error, ignored)
    return {"participants": s * i.rooms, "ingress_mbps": _kbps_to_mbps(up * i.rooms),
            "egress_mbps": _kbps_to_mbps(down * i.rooms), "biggest_unit_egress_mbps": _kbps_to_mbps(down),
            "biggest_unit_participants": s}


def size(i: Inputs) -> dict:
    out: dict = {"inputs": asdict(i), "assumptions": ASSUMPTIONS, "warnings": []}
    w = out["warnings"]

    parts = {"calls_1to1": _one_to_one(i), "group_calls": _group_calls(i), "study_rooms": _study_rooms(i)}
    out["workload"] = {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in parts.items()}

    participants = sum(p["participants"] for p in parts.values())
    ingress = sum(p["ingress_mbps"] for p in parts.values())
    egress = sum(p["egress_mbps"] for p in parts.values())
    biggest_unit_egress = max(p["biggest_unit_egress_mbps"] for p in parts.values())
    biggest_unit_participants = max(p["biggest_unit_participants"] for p in parts.values())

    # ---- CPU ----
    cpu_media = (ingress + egress) / max(i.mbps_per_core, 1e-9)
    cpu_conn = participants * i.cpu_per_participant
    cpu_cores = cpu_media + cpu_conn
    usable_cores = i.cores_per_node * i.target_utilization
    nodes_cpu = _ceil(cpu_cores / usable_cores) if participants else 0

    # ---- NIC ----
    nic_mbps = i.nic_gbps * 1000.0 * i.nic_util
    nodes_nic = _ceil(max(ingress, egress) / nic_mbps) if participants else 0

    # ---- UDP ports ----
    ports_available = max(i.port_range_end - i.port_range_start + 1, 0)
    pcs = participants * i.pcs_per_participant
    nodes_ports = _ceil(pcs / ports_available) if participants and ports_available else 0
    out["udp_ports"] = {"peer_connections": pcs, "ports_per_node": ports_available,
                        "max_participants_per_node_by_ports": ports_available // max(i.pcs_per_participant, 1)}

    # ---- TURN ----
    relayed = participants * i.turn_share
    turn_ports = i.turn_relay_end - i.turn_relay_start + 1
    turn_allocs = relayed * i.pcs_per_participant
    turn_mbps = (ingress + egress) * i.turn_share
    nodes_turn = _ceil(turn_allocs / turn_ports) if participants and turn_ports > 0 else 0
    out["turn"] = {"relayed_participants": round(relayed, 1), "relay_allocations": round(turn_allocs, 1),
                   "relay_ports_per_node": turn_ports, "relayed_mbps": round(turn_mbps, 1)}

    needed = max(nodes_cpu, nodes_nic, nodes_ports, nodes_turn, 1 if participants else 0)
    binding = max((("cpu", nodes_cpu), ("nic", nodes_nic), ("udp_ports", nodes_ports), ("turn_ports", nodes_turn)),
                  key=lambda kv: kv[1])[0] if participants else "-"
    total = needed + (i.spare_nodes if needed else 0)
    out["nodes"] = {"by_cpu": nodes_cpu, "by_nic": nodes_nic, "by_udp_ports": nodes_ports, "by_turn_ports": nodes_turn,
                    "needed": needed, "binding_limit": binding, "spare": i.spare_nodes if needed else 0, "total": total}
    out["load"] = {"participants": participants, "ingress_mbps": round(ingress, 1), "egress_mbps": round(egress, 1),
                   "cpu_cores_needed": round(cpu_cores, 1), "cpu_media_cores": round(cpu_media, 1),
                   "cpu_connection_cores": round(cpu_conn, 1),
                   "per_node_at_total": {"participants": round(participants / total, 1) if total else 0,
                                         "egress_mbps": round(egress / total, 1) if total else 0}}

    # ---- the biggest single room must fit one node (rooms do not span nodes) ----
    unit_cpu = biggest_unit_egress * 2 / max(i.mbps_per_core, 1e-9) + biggest_unit_participants * i.cpu_per_participant
    out["biggest_room"] = {"participants": biggest_unit_participants, "egress_mbps": round(biggest_unit_egress, 2),
                           "cpu_cores": round(unit_cpu, 2), "fits_one_node": unit_cpu <= usable_cores}
    if not out["biggest_room"]["fits_one_node"]:
        w.append("The biggest single room does not fit ONE node (rooms cannot span nodes): lower the room cap "
                 "(Classroom.max_participants / STUDY_ROOM_DEFAULT_MAX_PARTICIPANTS) or use bigger nodes.")

    # ---- recording ----
    egress_nodes = _ceil(i.recorded_rooms / max(i.egress_slots_per_node, 1)) if i.recorded_rooms else 0
    out["egress"] = {"concurrent_recordings": i.recorded_rooms, "slots_per_node": i.egress_slots_per_node,
                     "egress_nodes": egress_nodes}

    # ---- Django-side rates ----
    visit_s = max(i.avg_visit_minutes * 60.0, 1.0)
    steady_webhooks = participants * 2 / visit_s
    storm_joins_per_s = (i.room_size * i.rooms) / max(i.join_window_seconds, 1e-9) if i.rooms and i.room_size else 0.0
    rooms_started = i.rooms + i.group_calls + i.calls
    out["django"] = {
        "webhook_posts_per_sec_steady": round(steady_webhooks, 2),
        "webhook_posts_per_sec_during_join_storm": round(storm_joins_per_s, 1),
        "study_join_requests_per_sec_during_storm": round(storm_joins_per_s, 1),
        "livekit_list_room_identities_calls_per_sec_during_storm": round(max(storm_joins_per_s - i.rooms / max(i.join_window_seconds, 1e-9), 0.0), 1),
        "rooms_started_per_visit_cycle": rooms_started,
    }

    # ---- warnings ----
    if binding == "udp_ports" and nodes_ports > nodes_cpu:
        w.append("UDP port range (not CPU) decides the node count. Either widen rtc.port_range or set rtc.udp_port "
                 "(single-port mux) in livekit.yaml - then re-measure.")
    if nodes_turn > nodes_cpu:
        w.append("TURN relay ports decide the node count: split TURN onto its own node(s) or widen relay_range.")
    if i.turn_share >= 0.25:
        w.append("turn_share >= 25 %: relayed bytes cost like normal egress and TURN CPU is NOT in the CPU model - add headroom.")
    if storm_joins_per_s > 50:
        w.append(f"Join storm = ~{storm_joins_per_s:.0f} study-room joins/s. StudyRoomJoinView has no throttle and a non-host join makes "
                 "a LiveKit API call inside the request: check ASGI/DB workers (SIZING.md) before the LiveKit node count.")
    if steady_webhooks * 2 > 30:
        w.append("Webhook rate is high for a single Django endpoint that takes a row lock per event: confirm in WH1.")
    if i.mbps_per_core == 120.0:
        w.append("mbps_per_core is still the DEFAULT ASSUMPTION - replace it with a number from `lk load-test` before budgeting.")
    return out


def _fmt(o: dict) -> str:
    L = []
    a = L.append
    a("LiveKit sizing (X2) - starting plan, NOT a measurement")
    a("")
    for name, v in o["workload"].items():
        a(f"  {name:12s} participants={v['participants']:>6}  ingress={v['ingress_mbps']:>8} Mbps  egress={v['egress_mbps']:>8} Mbps")
    ld = o["load"]
    a(f"  {'TOTAL':12s} participants={ld['participants']:>6}  ingress={ld['ingress_mbps']:>8} Mbps  egress={ld['egress_mbps']:>8} Mbps")
    a("")
    n = o["nodes"]
    a(f"  nodes by cpu={n['by_cpu']}  nic={n['by_nic']}  udp ports={n['by_udp_ports']}  turn ports={n['by_turn_ports']}"
      f"   -> need {n['needed']} (binding: {n['binding_limit']}) + {n['spare']} spare = {n['total']}")
    a(f"  cpu: {ld['cpu_cores_needed']} vCPU ({ld['cpu_media_cores']} media + {ld['cpu_connection_cores']} connections); "
      f"per node at {n['total']}: {ld['per_node_at_total']['participants']} participants, {ld['per_node_at_total']['egress_mbps']} Mbps out")
    u, t = o["udp_ports"], o["turn"]
    a(f"  udp ports: {u['peer_connections']} PCs vs {u['ports_per_node']} per node (max {u['max_participants_per_node_by_ports']} participants/node)")
    a(f"  turn: {t['relayed_participants']} relayed participants, {t['relay_allocations']} allocations, {t['relayed_mbps']} Mbps relayed")
    b = o["biggest_room"]
    a(f"  biggest room: {b['participants']} participants, {b['egress_mbps']} Mbps out, {b['cpu_cores']} vCPU -> fits one node: {b['fits_one_node']}")
    e = o["egress"]
    a(f"  recording: {e['concurrent_recordings']} composite egress -> {e['egress_nodes']} egress node(s) at {e['slots_per_node']} slots")
    d = o["django"]
    a(f"  django: webhooks {d['webhook_posts_per_sec_steady']}/s steady, ~{d['webhook_posts_per_sec_during_join_storm']}/s in a join storm; "
      f"study joins ~{d['study_join_requests_per_sec_during_storm']}/s; LiveKit list_room_identities ~{d['livekit_list_room_identities_calls_per_sec_during_storm']}/s")
    if o["warnings"]:
        a("")
        a("WARNINGS")
        for w in o["warnings"]:
            a(f"  - {w}")
    a("")
    a("ASSUMPTIONS (replace with measurements)")
    for k, v in o["assumptions"].items():
        a(f"  - {k}: {v}")
    return "\n".join(L)


def main(argv=None) -> int:
    d = Inputs()
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for f, default in asdict(d).items():
        flag = "--" + f.replace("_", "-")
        p.add_argument(flag, type=type(default), default=default)
    p.add_argument("--json", action="store_true")
    ns = vars(p.parse_args(argv))
    as_json = ns.pop("json")
    out = size(Inputs(**ns))
    print(json.dumps(out, indent=2) if as_json else _fmt(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
