#!/usr/bin/env python3
"""
loadtest/sizing.py — Redis / Celery / ASGI / Postgres sizing model for T3.6.

A transparent back-of-envelope calculator, NOT an oracle. Every number it prints
is either (a) arithmetic on the inputs you give it or (b) an ASSUMPTION listed in
`ASSUMPTIONS` below. The workflow is:

  1. run it with defaults to get a STARTING plan for N concurrent users;
  2. run the load test (locustfile.py / k6.js) and read the real numbers
     (Locust CSV, `redis-cli INFO`, `celery inspect stats`, Postgres
     `pg_stat_activity`);
  3. re-run with `--measured-*` style overrides (the plain flags below) and
     compare — wherever model and measurement disagree by >30 %, trust the
     measurement and update the assumption.

    python loadtest/sizing.py                         # 1,000 concurrent users, defaults
    python loadtest/sizing.py --users 2500 --chat-users 600 --room-size 300
    python loadtest/sizing.py --tasks-per-sec 40 --avg-task-seconds 0.8 --json

No third-party imports; no Django.
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass

# --- assumptions that are NOT derived from this repo's code -------------------
ASSUMPTIONS = {
    "ws_per_asgi_proc": "800 sockets per daphne/uvicorn process (single event loop; verify with the 1k run)",
    "db_threads_per_asgi_proc": "8 sync_to_async DB threads per process, each holding a persistent connection (CONN_MAX_AGE=60)",
    "celery_child_mb": "200 MB RSS per Celery prefork child",
    "asgi_proc_mb": "250 MB RSS per ASGI process + ~40 KB per socket",
    "redis_overhead": "x1.5 over raw payload bytes for Redis structures/fragmentation",
    "channel_ops_per_delivery": "~3 Redis commands per group_send delivery (RPUSH + EXPIRE + the consumer's pop) — channels_redis internals, approximate",
    "channels_capacity": "channels_redis default capacity=100 messages per channel; a client slower than the room drops frames (ChannelFull)",
    "celery_result_ttl": "Celery default result_expires = 24 h; NO override in settings.py (verified) and no ignore_result anywhere",
}


@dataclass
class Inputs:
    users: int = 1000                  # peak concurrent signed-in users (each holds 1 inbox WebSocket)
    chat_users: int = 250              # of which actively in a chat room
    room_size: int = 250               # members receiving each message (worst case: one big room)
    chat_msgs_per_user_per_min: float = 2.5
    live_users: int = 150              # in a live classroom session
    feed_users: int = 600
    feed_think_seconds: float = 3.0    # between feed requests per user
    tasks_per_sec: float = 25.0        # Celery tasks/s at peak (push fan-out, notifications, signals) — MEASURE THIS
    avg_task_seconds: float = 0.5
    target_utilization: float = 0.6    # keep workers <= 60 % busy at peak
    celery_concurrency_per_proc: int = 4   # deploy/systemd/learnscroll-celery-worker.service
    msg_bytes: int = 700               # one chat frame in Redis
    task_bytes: int = 1500             # one serialized task message
    result_bytes: int = 600            # one stored result
    ignore_results: bool = False       # True if CELERY_TASK_IGNORE_RESULT is set
    result_ttl_seconds: int = 86400
    broker_burst_seconds: int = 60     # how long a worker outage/backlog you want to absorb
    channel_expiry_seconds: int = 60
    ws_per_asgi_proc: int = 800
    db_threads_per_asgi_proc: int = 8
    cpu_cores_per_asgi_proc: float = 1.0


def _ceil(x: float) -> int:
    return int(math.ceil(x - 1e-9))


def size(i: Inputs) -> dict:
    out: dict = {"inputs": asdict(i), "assumptions": ASSUMPTIONS}

    # ---- websockets / ASGI -----------------------------------------------
    ws_conns = i.users + i.chat_users + i.live_users           # inbox + chat + live-session sockets
    asgi_procs = max(2, _ceil(ws_conns / i.ws_per_asgi_proc))   # >=2 so one crash isn't an outage
    out["asgi"] = {
        "websocket_connections": ws_conns,
        "processes": asgi_procs,
        "cpu_cores": _ceil(asgi_procs * i.cpu_cores_per_asgi_proc),
        "ulimit_nofile_each": _ceil((ws_conns / asgi_procs) * 2 + 1024),
        "ram_mb_total": _ceil(asgi_procs * 250 + ws_conns * 0.04),
    }

    # ---- chat fan-out through the Redis channel layer -----------------------
    msgs_per_s = i.chat_users * i.chat_msgs_per_user_per_min / 60.0
    deliveries_per_s = msgs_per_s * i.room_size
    out["chat_fanout"] = {
        "messages_per_sec": round(msgs_per_s, 2),
        "deliveries_per_sec": round(deliveries_per_s, 1),
        "redis_ops_per_sec": _ceil(deliveries_per_s * 3 + msgs_per_s * 2),
        "worst_case_channel_mb": round(deliveries_per_s * i.channel_expiry_seconds * i.msg_bytes * 1.5 / 1e6, 1),
        "group_membership_mb": round(ws_conns * 250 * 1.5 / 1e6, 2),
    }

    # ---- HTTP -------------------------------------------------------------
    out["http"] = {"peak_rps": round(i.feed_users / max(i.feed_think_seconds, 0.1) + i.live_users * 0.02, 1)}

    # ---- Celery -------------------------------------------------------------
    busy_workers = i.tasks_per_sec * i.avg_task_seconds                # Little's law: L = lambda * W
    needed = max(1, _ceil(busy_workers / i.target_utilization))
    procs = max(2, _ceil(needed / i.celery_concurrency_per_proc))      # >=2 worker processes for HA
    result_mb = 0.0 if i.ignore_results else i.tasks_per_sec * i.result_ttl_seconds * i.result_bytes * 1.5 / 1e6
    out["celery"] = {
        "busy_workers_at_peak": round(busy_workers, 1),
        "worker_slots_needed": needed,
        "worker_processes_at_current_concurrency": procs,
        "current_deploy_slots": i.celery_concurrency_per_proc,  # one unit, concurrency=4
        "ram_mb_total": procs * i.celery_concurrency_per_proc * 200,
        "broker_burst_mb": round(i.tasks_per_sec * i.broker_burst_seconds * i.task_bytes * 1.5 / 1e6, 1),
        "result_backend_mb_steady_state": round(result_mb, 1),
    }

    # ---- Postgres ------------------------------------------------------------
    pg = asgi_procs * i.db_threads_per_asgi_proc + procs * i.celery_concurrency_per_proc + 5
    out["postgres"] = {
        "max_connections_needed": pg,
        "pgbouncer_recommended": pg > 150,
    }

    # ---- Redis roll-up --------------------------------------------------------
    steady_mb = (out["chat_fanout"]["group_membership_mb"] + out["celery"]["broker_burst_mb"]
                 + out["celery"]["result_backend_mb_steady_state"] + out["chat_fanout"]["worst_case_channel_mb"])
    out["redis"] = {
        "ops_per_sec_channel_layer": out["chat_fanout"]["redis_ops_per_sec"],
        "ops_per_sec_celery": _ceil(i.tasks_per_sec * 6),   # publish + fetch + ack (+ result set) ~6 cmds
        "memory_mb_excluding_cache": round(steady_mb, 1),
        "memory_mb_recommended_maxmemory": _ceil(max(256, steady_mb * 2)),  # 2x headroom
        "needs_separate_instance_for_cache": True,
    }

    out["warnings"] = _warnings(i, out)
    return out


def _warnings(i: Inputs, o: dict) -> list:
    w = []
    if not i.ignore_results:
        w.append(f"Result backend keeps every task result for {i.result_ttl_seconds // 3600} h "
                 f"(~{o['celery']['result_backend_mb_steady_state']} MB at this rate). Set "
                 f"CELERY_TASK_IGNORE_RESULT = True (nothing in the repo reads results) or CELERY_RESULT_EXPIRES = 3600.")
    if o["celery"]["worker_slots_needed"] > i.celery_concurrency_per_proc:
        w.append(f"Peak needs ~{o['celery']['worker_slots_needed']} worker slots; the shipped unit has "
                 f"concurrency={i.celery_concurrency_per_proc} -> run more worker units/hosts or raise --concurrency.")
    if i.room_size > 100:
        w.append("room_size > 100: channels_redis' default capacity=100 per channel means a slow client silently loses "
                 "frames under bursts; consider capacity/group_expiry tuning or splitting very large rooms.")
    if o["postgres"]["pgbouncer_recommended"]:
        w.append("Postgres connections > 150 -> put PgBouncer (transaction pooling) in front, or lower db_threads_per_asgi_proc.")
    w.append("Redis eviction conflict: the channel layer + broker need maxmemory-policy `noeviction`; the Django cache wants "
             "`allkeys-lru`. They share one REDIS_URL today — use two instances (or at minimum separate DB numbers + monitoring).")
    return w


def render(o: dict) -> str:
    rows = []

    def sec(title, d):
        rows.append(f"\n== {title}")
        for k, v in d.items():
            rows.append(f"  {k:<42} {v}")

    for title in ("asgi", "chat_fanout", "http", "celery", "postgres", "redis"):
        sec(title.upper(), o[title])
    rows.append("\n== WARNINGS")
    rows += [f"  - {x}" for x in o["warnings"]]
    rows.append("\n== ASSUMPTIONS (replace with measured values after the load test)")
    rows += [f"  - {k}: {v}" for k, v in o["assumptions"].items()]
    return "\n".join(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for f, default in asdict(Inputs()).items():
        flag = "--" + f.replace("_", "-")
        if isinstance(default, bool):
            p.add_argument(flag, action="store_true", default=default)
        else:
            p.add_argument(flag, type=type(default), default=default)
    p.add_argument("--json", action="store_true")
    args = vars(p.parse_args(argv))
    as_json = args.pop("json")
    result = size(Inputs(**args))
    print(json.dumps(result, indent=2) if as_json else render(result))


if __name__ == "__main__":
    main()
