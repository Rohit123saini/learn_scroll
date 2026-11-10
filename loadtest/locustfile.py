"""
loadtest/locustfile.py — T3.6 load test (Locust).

Scenarios (one Locust user class each, mixed by weight):
  FeedUser      GET /post/feed/ + cursor pagination (the heaviest read path); page 1 / page 2 / page 3+ are separate
                request names so each gets its own p95 (H2)
  ChatWsUser    WebSocket /ws/chat/<conversation>/ : connect, send, measure echo latency
  LiveJoinUser  POST /tuitionclass/sessions/<id>/join/  then WebSocket
                /ws/tuitionclass/session/<id>/ and stay connected (the thundering-herd moment)

  LiveChatUser / LiveAbuser / LiveModerator / LiveReporter  (X12 L-D #6; only with LIVE_ONLY=1) -
                REST chat hot path + moderation endpoints, scenarios C1-C4 + M1 of LIVE_CLASS_PLAN.md

X2: CALLS_ONLY=1 adds message calls + study rooms (CallPairUser, CallBusyRacer, GroupCallUser, StudyRoomUser); CALLS_MIX picks them.

Profiles (env PROFILE): smoke | baseline | 1k | soak  — see PROFILES below.
`1k` ramps to 1,000 CONCURRENT users with the 60/25/15 mix (600 feed, 250 chat, 150 live).

Run (headless, CSV for the sizing script):
    pip install -r loadtest/requirements.txt
    python manage.py seed_loadtest --users 1000 --confirm-staging --out loadtest/fixtures.json
    PROFILE=1k locust -f loadtest/locustfile.py --headless --host https://staging.example.com \
        --csv loadtest/out/run1k --html loadtest/out/run1k.html

Exit code is NON-ZERO when a threshold below is breached, so this can gate a release.
Everything tunable is an env var (see CONFIG).
"""
import base64
import hashlib
import hmac
import json
import os
import random
import threading
import time
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse

import gevent
from gevent.event import Event as GEvent
from locust import HttpUser, LoadTestShape, between, constant, events, task

try:  # websocket-client (blocking, gevent-friendly once Locust monkey-patches sockets)
    import websocket
except ImportError:  # pragma: no cover
    websocket = None

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------
FIXTURES = Path(os.environ.get("FIXTURES", Path(__file__).with_name("fixtures.json")))
WS_BASE = os.environ.get("WS_BASE", "")  # e.g. wss://staging.example.com ; default derived from --host
CHAT_SEND_EVERY = (float(os.environ.get("CHAT_SEND_MIN", 15)), float(os.environ.get("CHAT_SEND_MAX", 30)))
# server limit is 60 msgs/min/user (message/throttles.py WSMessageRateLimiter) -> stay well under it
FEED_PAGES = int(os.environ.get("FEED_PAGES", 4))
LIVE_HOLD_SECONDS = (float(os.environ.get("LIVE_HOLD_MIN", 120)), float(os.environ.get("LIVE_HOLD_MAX", 600)))

# X12 L-D #6: live chat-safety scenarios. LIVE_ONLY=1 switches OFF feed/chat-ws/join users and spawns only the four live
# classes below (97 / 2 / 1 / 0 by weight; LIVE_REPORTER_WEIGHT turns reporters on). Default (0) = the old mix, untouched.
LIVE_ONLY = os.environ.get("LIVE_ONLY", "0") == "1"
LIVE_CHAT_EVERY = (float(os.environ.get("LIVE_CHAT_MIN", 20)), float(os.environ.get("LIVE_CHAT_MAX", 40)))   # C1: 1 msg / 20-40 s
LIVE_ABUSE_RATE = float(os.environ.get("LIVE_ABUSE_RATE", 20))        # C2: messages / second / abuser
LIVE_MOD_RATE = float(os.environ.get("LIVE_MOD_RATE", 1))             # M1: moderator actions / second (60/min = the throttle)
# M1: chat_mute hits RANDOM students, which would also mute the C1 chatters and break the C1 gate -> off by default.
LIVE_MOD_MUTE = os.environ.get("LIVE_MOD_MUTE", "0") == "1"
LIVE_BLOCKED_WORD = os.environ.get("LIVE_BLOCKED_WORD", "shit")       # must be in the word list (common/moderation.py)
LIVE_CONTACT_LINE = os.environ.get("LIVE_CONTACT_LINE", "call me on 9876543210 or https://example.com/x")

# H2: page 1 (new session: pools built) and page 2 (cursor, frozen snapshot) are reported SEPARATELY - they take
# completely different code paths. Pages 3+ are grouped. Needs FEED_PAGES >= 2 (default 4) for page 2 to be sampled.
FEED_PAGE_NAMES = ("GET /post/feed/", "GET /post/feed/ [page 2]", "GET /post/feed/ [page 3+]")

# Pass/fail thresholds (release gate). Keys are Locust request "name" prefixes.
THRESHOLDS = {
    "GET /post/feed/": {"p95_ms": 800, "fail_ratio": 0.01},                 # page 1
    "GET /post/feed/ [page 2]": {"p95_ms": 800, "fail_ratio": 0.01},        # page 2 (H2); same 800 ms feed target
    "POST /tuitionclass/sessions/[id]/join/": {"p95_ms": 1500, "fail_ratio": 0.01},
    "WS chat connect": {"p95_ms": 1000, "fail_ratio": 0.01},
    "WS chat echo": {"p95_ms": 500, "fail_ratio": 0.02},
    "WS live connect": {"p95_ms": 1000, "fail_ratio": 0.01},
}

# X12 L-D #6: gate for LIVE_ONLY runs (the old keys would report "no samples" because those users are off).
# "Live abuse send" has no latency limit on purpose: C2 only requires that the abusers do NOT slow everyone else, which you
# check by comparing the "[normal]" p95 of a LIVE_ONLY run with and without LiveAbuser (+/-10 %), see LIVE_CLASS_PLAN.md.
LIVE_THRESHOLDS = {
    "POST /tuitionclass/chat-messages/ [normal]": {"p95_ms": 400, "fail_ratio": 0.005},     # C1
    "POST /tuitionclass/chat-messages/ [abuse]": {"p95_ms": 100000, "fail_ratio": 0.005},   # C2-C4: only 5xx / wrong codes fail
    "POST /tuitionclass/sessions/[id]/moderation/act/": {"p95_ms": 500, "fail_ratio": 0.005},  # M1 (429 = expected, not a failure)
    "GET /tuitionclass/sessions/[id]/moderation/queue/": {"p95_ms": 500, "fail_ratio": 0.005},
}

# --------------------------------------------------------------------------
# fixtures: hand each simulated user its own seeded account
# --------------------------------------------------------------------------
_fx = None
_fx_lock = threading.Lock()
_next_index = 0


def _load_fixtures():
    global _fx
    if _fx is None:
        if not FIXTURES.exists():
            raise SystemExit(f"{FIXTURES} not found — run `manage.py seed_loadtest --confirm-staging` first.")
        _fx = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return _fx


def next_account():
    """Round-robin over seeded users (a user may be reused after a restart; never shared concurrently
    unless users_spawned > seeded users, which the 1k profile avoids by seeding >= 1000)."""
    global _next_index
    fx = _load_fixtures()
    with _fx_lock:
        acct = fx["users"][_next_index % len(fx["users"])]
        _next_index += 1
    return acct


def next_student_account():
    """Like next_account() but never the teacher (users[0] is the host in seed_loadtest)."""
    teacher = _load_fixtures()["users"][0]["user_id"]
    for _ in range(3):
        acct = next_account()
        if acct["user_id"] != teacher:
            return acct
    return acct


def _ws_url(host, path, token):
    """X6b: sockets authenticate with a one-time ticket (POST /login/auth/ws-ticket/), like the app does.
    P-B2: the server rejects `?token=` by default, so a 404 / failure is now an error, never a silent JWT-in-URL fallback.
    LOADTEST_WS_LEGACY_TOKEN=1 -> the old `?token=<JWT>` (only against a server started with WS_ALLOW_QUERY_TOKEN=true)."""
    base = WS_BASE or host.replace("https://", "wss://").replace("http://", "ws://")
    base = base.rstrip("/")
    if os.environ.get("LOADTEST_WS_LEGACY_TOKEN", "").lower() not in ("1", "true", "yes"):
        try:
            import requests
            r = requests.post(f"{host.rstrip('/')}/login/auth/ws-ticket/", headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.status_code == 200 and r.json().get("ticket"):
                return f"{base}{path}?ticket={r.json()['ticket']}"
            raise RuntimeError(f"ws-ticket answered HTTP {r.status_code}")
        except RuntimeError:
            raise
        except Exception as exc:  # network error: report it, do not silently put the JWT in a URL
            raise RuntimeError(f"ws-ticket request failed: {exc}")
    return f"{base}{path}?token={token}"


def _fire(env, kind, name, started, length=0, exc=None):
    env.events.request.fire(
        request_type=kind, name=name, response_time=(time.perf_counter() - started) * 1000,
        response_length=length, exception=exc, context={},
    )


# --------------------------------------------------------------------------
# 1) FEED
# --------------------------------------------------------------------------
class FeedUser(HttpUser):
    weight = 60
    wait_time = between(1.5, 4)

    def on_start(self):
        self.acct = next_account()
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"

    @task(5)
    def scroll_feed(self):
        """Open the feed and scroll a few pages by following the server's own `next` link."""
        url = "/post/feed/"
        for page in range(FEED_PAGES):
            with self.client.get(url, name=FEED_PAGE_NAMES[min(page, 2)], catch_response=True) as r:
                if r.status_code != 200:
                    r.failure(f"HTTP {r.status_code}")
                    return
                try:
                    nxt = r.json().get("next")
                except ValueError:
                    r.failure("non-JSON feed body")
                    return
                r.success()
            if not nxt:
                return
            # `next` is absolute; keep path+query so --host (the injector's view of the system) is used.
            p = urlparse(nxt)
            url = f"{p.path}?{p.query}"
            time.sleep(random.uniform(0.5, 2.0))  # reading time

    @task(1)
    def following_feed(self):
        self.client.get("/post/feed/?source=following", name="GET /post/feed/?source=following")


# --------------------------------------------------------------------------
# 2) CHAT WEBSOCKET
# --------------------------------------------------------------------------
class ChatWsUser(HttpUser):
    """Uses HttpUser only for the Locust plumbing (host, events); all traffic is a raw WebSocket."""
    weight = 25
    wait_time = between(1, 2)

    def on_start(self):
        if websocket is None:
            raise SystemExit("pip install websocket-client")
        self.acct = next_account()
        self.ws = None
        self.pending = {}  # client_id -> perf_counter() when sent
        self._reader = None
        self._connect()

    def on_stop(self):
        self._close()

    def _close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = None

    def _connect(self):
        fx = _load_fixtures()
        t0 = time.perf_counter()
        try:
            url = _ws_url(self.host, f"/ws/chat/{fx['conversation_id']}/", self.acct["access"])  # X6b: ticket fetch counts in the connect time
            self.ws = websocket.create_connection(url, timeout=10)
            _fire(self.environment, "WS", "WS chat connect", t0)
            self._reader = threading.Thread(target=self._read_loop, daemon=True)
            self._reader.start()
        except Exception as exc:
            _fire(self.environment, "WS", "WS chat connect", t0, exc=exc)
            self.ws = None

    def _read_loop(self):
        ws = self.ws
        while ws is not None and self.ws is ws:
            try:
                frame = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue   # X2 fix: 10 s of silence is normal, it must not kill the reader
            except Exception:
                return
            if not frame:
                return
            try:
                data = json.loads(frame)
            except ValueError:
                continue
            cid = data.get("client_id") or (data.get("message") or {}).get("client_id")
            started = self.pending.pop(cid, None) if cid else None
            if started is not None:  # our own message echoed back through the channel layer (Redis)
                _fire(self.environment, "WS", "WS chat echo", started, length=len(frame))

    @task
    def send_message(self):
        if self.ws is None:
            time.sleep(2)
            self._connect()
            return
        cid = str(uuid.uuid4())
        self.pending[cid] = time.perf_counter()
        try:
            self.ws.send(json.dumps({"type": "message", "message_type": "text", "text": f"loadtest {cid[:8]}", "client_id": cid}))
        except Exception as exc:
            self.pending.pop(cid, None)
            _fire(self.environment, "WS", "WS chat echo", time.perf_counter(), exc=exc)
            self._close()
            return
        # drop echoes that never arrive so `pending` can't grow without bound; count them as failures
        cutoff = time.perf_counter() - 15
        for old, ts in list(self.pending.items()):
            if ts < cutoff:
                self.pending.pop(old, None)
                _fire(self.environment, "WS", "WS chat echo", ts, exc=TimeoutError("no echo within 15s"))
        time.sleep(random.uniform(*CHAT_SEND_EVERY))


# --------------------------------------------------------------------------
# 3) LIVE SESSION JOIN
# --------------------------------------------------------------------------
class LiveJoinUser(HttpUser):
    weight = 15
    wait_time = between(1, 2)

    def on_start(self):
        if websocket is None:
            raise SystemExit("pip install websocket-client")
        self.acct = next_account()
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"
        self.ws = None
        self.joined = False

    def on_stop(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    @task
    def join_and_stay(self):
        fx = _load_fixtures()
        sid = fx["session_id"]
        if not self.joined:
            with self.client.post(f"/tuitionclass/sessions/{sid}/join/", name="POST /tuitionclass/sessions/[id]/join/",
                                  catch_response=True) as r:
                if r.status_code in (200, 201):
                    self.joined = True
                    r.success()
                elif r.status_code == 429:
                    r.failure("throttled (session_join)")
                    time.sleep(5)
                    return
                else:
                    r.failure(f"HTTP {r.status_code}: {r.text[:120]}")
                    time.sleep(5)
                    return
            t0 = time.perf_counter()
            try:
                self.ws = websocket.create_connection(
                    _ws_url(self.host, f"/ws/tuitionclass/session/{sid}/", self.acct["access"]), timeout=10)
                _fire(self.environment, "WS", "WS live connect", t0)
            except Exception as exc:
                _fire(self.environment, "WS", "WS live connect", t0, exc=exc)
                self.ws = None
                return
        # stay in the room: drain server events for a realistic hold time, then leave and rejoin later
        end = time.time() + random.uniform(*LIVE_HOLD_SECONDS)
        while time.time() < end and self.ws is not None:
            try:
                self.ws.settimeout(5)
                self.ws.recv()
            except websocket.WebSocketTimeoutException:
                try:
                    self.ws.ping()
                except Exception:
                    break
            except Exception:
                break
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.ws = None
            self.joined = False


# --------------------------------------------------------------------------
# 4) LIVE CHAT SAFETY + MODERATION  (X12 L-D #6; scenarios of loadtest/LIVE_CLASS_PLAN.md)
#    Only spawned with LIVE_ONLY=1. Needs fixtures from `seed_loadtest` (users[0] = teacher = host + moderator).
# --------------------------------------------------------------------------
CHAT_URL = "/tuitionclass/chat-messages/"
NORMAL_NAME = "POST /tuitionclass/chat-messages/ [normal]"
ABUSE_NAME = "POST /tuitionclass/chat-messages/ [abuse]"
# every answer the safety guards are SUPPOSED to give: 400 message_blocked / contact_info_blocked, 403 chat_muted,
# 409 duplicate_message, 429 chat_flood (+ slow mode). Anything else (5xx, 401, 404) is a real failure.
ABUSE_EXPECTED = {200, 201, 400, 403, 409, 429}


def _json_code(resp):
    try:
        return (resp.json() or {}).get("code") or ""
    except ValueError:
        return ""


class LiveChatUser(HttpUser):
    """C1: ordinary student chatting. Must stay 201 and fast while abusers hammer the same room."""
    weight = 0
    wait_time = between(*LIVE_CHAT_EVERY)

    def on_start(self):
        self.acct = next_student_account()
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"

    @task
    def send(self):
        sid = _load_fixtures()["session_id"]
        with self.client.post(CHAT_URL, json={"session": sid, "message": f"hello {uuid.uuid4().hex[:8]}"},
                              name=NORMAL_NAME, catch_response=True) as r:
            if r.status_code == 201:
                r.success()
            else:
                r.failure(f"HTTP {r.status_code} {_json_code(r)}")


class LiveAbuser(HttpUser):
    """C2 flood, C3 duplicate + blocked word, C4 contact info. Weighted ~2 % of users.
    Expected: 429 chat_flood -> 403 chat_muted after strikes, 409 duplicate_message, 400 message_blocked / contact_info_blocked."""
    weight = 0
    wait_time = constant(1.0 / max(LIVE_ABUSE_RATE, 0.1))

    def on_start(self):
        self.acct = next_student_account()
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"
        self.same_line = f"same line {uuid.uuid4().hex[:6]}"

    def _post(self, kind, text):
        sid = _load_fixtures()["session_id"]
        with self.client.post(CHAT_URL, json={"session": sid, "message": text},
                              name=ABUSE_NAME, catch_response=True) as r:
            if r.status_code in ABUSE_EXPECTED:
                r.success()
            else:
                r.failure(f"{kind}: HTTP {r.status_code} {_json_code(r)}")
            # one synthetic row per (kind, code) so the CSV shows the answer mix (0 ms: it is a counter, not a latency)
            _fire(self.environment, "CODE", f"live abuse {kind} -> {r.status_code} {_json_code(r)}".strip(), time.perf_counter())

    @task(3)
    def flood(self):
        self._post("flood", f"spam {uuid.uuid4().hex[:10]}")

    @task(2)
    def duplicate(self):
        self._post("duplicate", self.same_line)

    @task(2)
    def blocked_word(self):
        self._post("blocked_word", f"you are {LIVE_BLOCKED_WORD}")

    @task(2)
    def contact_info(self):
        self._post("contact_info", LIVE_CONTACT_LINE)


class LiveModerator(HttpUser):
    """M1: the host works the queue and acts on students. 60 actions/min is exactly the `live_mod_action` throttle, so the
    odd 429 is expected and must carry retry_after (never a 500)."""
    weight = 0
    wait_time = constant(1.0 / max(LIVE_MOD_RATE, 0.05))

    def on_start(self):
        fx = _load_fixtures()
        self.acct = fx["users"][0]
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"
        self.student_ids = [u["user_id"] for u in fx["users"][1:]] or [self.acct["user_id"]]

    @task(8)
    def act(self):
        sid = _load_fixtures()["session_id"]
        action = random.choice(["warn", "chat_mute", "chat_unmute"] if LIVE_MOD_MUTE else ["warn", "chat_unmute"])
        body = {"action": action, "user_id": random.choice(self.student_ids)}
        if action == "chat_mute":
            body["minutes"] = 1
        with self.client.post(f"/tuitionclass/sessions/{sid}/moderation/act/", json=body,
                              name="POST /tuitionclass/sessions/[id]/moderation/act/", catch_response=True) as r:
            if r.status_code == 200:
                r.success()
            elif r.status_code == 429:
                has_wait = bool(r.headers.get("Retry-After")) or "retry_after" in r.text
                r.success() if has_wait else r.failure("429 without Retry-After / retry_after")
            else:
                r.failure(f"HTTP {r.status_code} {_json_code(r)}")

    @task(2)
    def queue(self):
        sid = _load_fixtures()["session_id"]
        self.client.get(f"/tuitionclass/sessions/{sid}/moderation/queue/",
                        name="GET /tuitionclass/sessions/[id]/moderation/queue/")


class LiveReporter(HttpUser):
    """M1 (second half): many students file reports. The per-session budget (LIVE_REPORT_MAX_PER_SESSION) and the
    `live_report` throttle (10/min) answer 4xx / 429 - that is the pass condition, a 5xx is the failure."""
    weight = 0
    wait_time = between(10, 30)

    def on_start(self):
        fx = _load_fixtures()
        self.acct = next_student_account()
        self.client.headers["Authorization"] = f"Bearer {self.acct['access']}"
        self.target_ids = [u["user_id"] for u in fx["users"][1:] if u["user_id"] != self.acct["user_id"]]

    @task
    def report(self):
        sid = _load_fixtures()["session_id"]
        body = {"user_id": random.choice(self.target_ids or [self.acct["user_id"]]),
                "reason": random.choice(["spam", "harassment", "scam"]), "note": "loadtest"}
        with self.client.post(f"/tuitionclass/sessions/{sid}/moderation/report/", json=body,
                              name="POST /tuitionclass/sessions/[id]/moderation/report/", catch_response=True) as r:
            if r.status_code < 500 and r.status_code != 401:
                r.success()      # 200 / 201 / 400 (own report) / 403 / 429 (budget or throttle) are all designed answers
            else:
                r.failure(f"HTTP {r.status_code} {_json_code(r)}")


# --------------------------------------------------------------------------
# 5) MESSAGE CALLS + STUDY ROOMS  (X2; scenarios CL1-CL7 / SR1-SR6 / WH1 of loadtest/CALLS_STUDYROOM_PLAN.md)
#    Only spawned with CALLS_ONLY=1. Fixtures: `manage.py seed_calls_loadtest --confirm-staging` -> loadtest/calls_fixtures.json
#    This drives the Django + Channels + Redis + Postgres side. The LiveKit MEDIA plane (audio/video tracks) is NOT
#    simulated here - use `lk load-test` for that (plan section 6) and `livekit_sizing.py` for node sizing.
#    NOT RUN while writing (read-only round): every URL / status code / body key below was taken from message/urls.py,
#    message/views/calls.py, message/views/study_room*.py, message/call_state.py and message/consumers.py.
# --------------------------------------------------------------------------
CALLS_ONLY = os.environ.get("CALLS_ONLY", "0") == "1"
CALLS_FIXTURES = Path(os.environ.get("CALLS_FIXTURES", Path(__file__).with_name("calls_fixtures.json")))
# which classes run, as weights: "pairs:100" (default) | "busy:5" | "group:1" | "rooms:100" | "pairs:40,rooms:60"
CALLS_MIX = os.environ.get("CALLS_MIX", "pairs:100")
# call_initiate_ip is 20/min PER IP (settings.py). One injector box = one IP, so a per-VU X-Forwarded-For is sent by
# default. It only helps while NUM_PROXIES is unset on staging; with NUM_PROXIES set the real IP wins and 429s are real
# (that is exactly CL7). CALLS_EXPECT_429=1 turns 429 on initiate into an EXPECTED, counted answer (CL7).
CALLS_SPOOF_XFF = os.environ.get("CALLS_SPOOF_XFF", "1") == "1"
CALLS_EXPECT_429 = os.environ.get("CALLS_EXPECT_429", "0") == "1"

# --- 1:1 calls (CL1, CL2, CL5, CL6) ---
CALL_OUTCOME_W = [float(x) for x in os.environ.get("CALL_OUTCOMES", "70,10,10,10").split(",")]   # accept,reject,cancel,noanswer
CALL_ACCEPT_DELAY = (float(os.environ.get("CALL_ACCEPT_MIN", 1)), float(os.environ.get("CALL_ACCEPT_MAX", 4)))
CALL_HOLD = (float(os.environ.get("CALL_HOLD_MIN", 20)), float(os.environ.get("CALL_HOLD_MAX", 60)))
CALL_GAP = (float(os.environ.get("CALL_GAP_MIN", 2)), float(os.environ.get("CALL_GAP_MAX", 8)))
CALL_MIN_CYCLE = float(os.environ.get("CALL_MIN_CYCLE", 7))      # call_initiate is 10/min per user -> never below ~6 s per call
CALL_NOANSWER_WAIT = float(os.environ.get("CALL_NOANSWER_WAIT", 55))   # > CALL_RING_TIMEOUT_SECONDS (45): the sweeper ends it
CALL_RING_WS = os.environ.get("CALL_RING_WS", "1") == "1"        # callee holds ws/chat/<conv>/ and times the incoming_call frame
CALL_RING_WAIT = float(os.environ.get("CALL_RING_WAIT", 5))
CALL_TELEMETRY_P = float(os.environ.get("CALL_TELEMETRY_P", 0.5))   # throttle is 120/hour/user, so not every call
CALL_REFRESH_P = float(os.environ.get("CALL_REFRESH_P", 0.1))       # throttle is 30/hour/user

# --- group call (CL4) ---
GROUP_CAP = int(os.environ.get("GROUP_CALL_CAP", 50))            # = CALL_GROUP_MAX_PARTICIPANTS on the server
GROUP_JOIN_WINDOW = float(os.environ.get("GROUP_JOIN_WINDOW", 5))   # seconds in which the other members press accept / join (0 = same instant)
GROUP_HOLD = float(os.environ.get("GROUP_HOLD", 30))
GROUP_ROUND_GAP = float(os.environ.get("GROUP_ROUND_GAP", 30))

# --- study rooms (SR1-SR6) ---
STUDY_ROOMS = int(os.environ.get("STUDY_ROOMS", 0))             # 0 = every room in the fixtures
STUDY_ROOM_USERS = int(os.environ.get("STUDY_ROOM_USERS", 0))   # members per room that take part (0 = all, usually 50)
STUDY_JOIN_WINDOW = float(os.environ.get("STUDY_JOIN_WINDOW", 10))   # SR1 join storm = everyone inside this many seconds; 300 = SR2 steady
STUDY_HOLD = (float(os.environ.get("STUDY_HOLD_MIN", 240)), float(os.environ.get("STUDY_HOLD_MAX", 600)))
STUDY_REJOIN_GAP = (float(os.environ.get("STUDY_REJOIN_MIN", 20)), float(os.environ.get("STUDY_REJOIN_MAX", 90)))
STUDY_DRAWERS = int(os.environ.get("STUDY_DRAWERS", 2))         # members 1..N per room send draw_point (member 0 = host)
STUDY_DRAW_HZ = float(os.environ.get("STUDY_DRAW_HZ", 30))      # frames/s per drawer WHILE drawing; the Flutter client has no throttle -> try 60-120
STUDY_DRAW_ON = float(os.environ.get("STUDY_DRAW_ON", 8))       # seconds drawing ...
STUDY_DRAW_OFF = float(os.environ.get("STUDY_DRAW_OFF", 12))    # ... then seconds idle
STUDY_LAT_SAMPLE = max(int(os.environ.get("STUDY_LAT_SAMPLE", 10)), 1)   # report latency of every Nth frame (49 receivers x 30 Hz would drown Locust's stats)
STUDY_MAX_LOSS = float(os.environ.get("STUDY_MAX_LOSS", 0.01))
STUDY_BOARD_WRITERS = int(os.environ.get("STUDY_BOARD_WRITERS", 3))   # members 0..N-1 PUT the board (host is always one)
STUDY_BOARD_EVERY = float(os.environ.get("STUDY_BOARD_EVERY", 20))
STUDY_BOARD_KB = int(os.environ.get("STUDY_BOARD_KB", 100))     # payload size of one PUT (server cap is 2 MB)
STUDY_ROOM_POLL = float(os.environ.get("STUDY_ROOM_POLL", 30))  # GET room/ resync cadence
STUDY_REFRESH_P = float(os.environ.get("STUDY_REFRESH_P", 0.2))
STUDY_WAIT_SECONDS = float(os.environ.get("STUDY_WAIT_SECONDS", 90))   # SR6: how long a member polls join/ in the waiting room
STUDY_WAIT_POLL = float(os.environ.get("STUDY_WAIT_POLL", 4))
# WH1: also emulate LiveKit -> Django webhooks (participant_joined / participant_left) for every join. Needs the SAME
# LIVEKIT_API_KEY / LIVEKIT_API_SECRET as staging in the injector's environment.
STUDY_WEBHOOK = os.environ.get("STUDY_WEBHOOK", "0") == "1"

N_INIT = "POST /message/calls/initiate/ [1:1]"
N_INIT_RACE = "POST /message/calls/initiate/ [busy race]"
N_INIT_GROUP = "POST /message/calls/initiate/ [group]"
N_ACCEPT = "POST /message/calls/[id]/action/ [accept]"
N_REJECT = "POST /message/calls/[id]/action/ [reject]"
N_END = "POST /message/calls/[id]/action/ [end]"
N_CANCEL = "POST /message/calls/[id]/action/ [cancel]"
N_G_ACCEPT = "POST /message/calls/[id]/action/ [group accept]"
N_G_JOIN = "POST /message/calls/[id]/action/ [group join]"
N_G_LEAVE = "POST /message/calls/[id]/action/ [group leave]"
N_REFRESH = "POST /message/calls/[id]/refresh-token/"
N_TELEM = "POST /message/calls/[id]/telemetry/"
N_RING = "WS call ring event"
N_SR_JOIN = "POST /message/study-room/[id]/join/"
N_SR_JOIN_HOST = "POST /message/study-room/[id]/join/ [host, new session]"
N_SR_RULES = "POST /message/study-room/[id]/rules/"
N_SR_ROOM = "GET /message/study-room/[id]/room/"
N_SR_GET = "GET /message/study-room/[id]/state/"
N_SR_PUT = "PUT /message/study-room/[id]/state/"
N_SR_HAND = "POST /message/study-room/[id]/hand/"
N_SR_ADMIT = "POST /message/study-room/[id]/admit/"
N_SR_REFRESH = "POST /message/study-room/[id]/refresh-token/"
N_SR_WS = "WS study connect"
N_SR_DRAW = "WS study draw relay"
N_WEBHOOK = "POST /message/livekit/webhook/"
C_CL3 = "CHECK CL3 one winner per busy set"
C_CL4 = "CHECK CL4 group call cap"
C_SR3 = "CHECK SR3 draw frames delivered"


def _calls_thresholds():
    """Only the gates of the scenarios that actually run (a gate for a class with weight 0 would say 'no samples')."""
    mix = _parse_mix()
    t = {}
    if mix.get("pairs"):
        t[N_INIT] = {"p95_ms": 800, "fail_ratio": 0.01}
        t[N_ACCEPT] = {"p95_ms": 600, "fail_ratio": 0.01}
        t[N_END] = {"p95_ms": 600, "fail_ratio": 0.01}
        if CALL_RING_WS and websocket is not None:
            t[N_RING] = {"p95_ms": 1500, "fail_ratio": 0.02}
            t["WS call connect"] = {"p95_ms": 1000, "fail_ratio": 0.01}
    if mix.get("busy"):
        t[C_CL3] = {"p95_ms": 10 ** 9, "fail_ratio": 0.0}
    if mix.get("group"):
        t[C_CL4] = {"p95_ms": 10 ** 9, "fail_ratio": 0.0}
    if mix.get("rooms"):
        t[N_SR_JOIN] = {"p95_ms": 2000, "fail_ratio": 0.01}
        t[N_SR_WS] = {"p95_ms": 1000, "fail_ratio": 0.01}
        t[N_SR_ROOM] = {"p95_ms": 800, "fail_ratio": 0.01}
        if STUDY_BOARD_WRITERS > 0:
            t[N_SR_PUT] = {"p95_ms": 1000, "fail_ratio": 0.005}
        if STUDY_DRAWERS > 0:
            t[N_SR_DRAW] = {"p95_ms": 300, "fail_ratio": 0.02}
            t[C_SR3] = {"p95_ms": 10 ** 9, "fail_ratio": 0.0}
        if STUDY_WEBHOOK:
            t[N_WEBHOOK] = {"p95_ms": 500, "fail_ratio": 0.005}
    return t


def _parse_mix():
    out = {}
    for part in CALLS_MIX.split(","):
        part = part.strip()
        if part:
            k, _, v = part.partition(":")
            out[k.strip()] = int(v or 1)
    return out


# ---- fixtures + small helpers --------------------------------------------------------------------------------------
_cfx = None
_claims = {}


def _calls_fx():
    global _cfx
    if _cfx is None:
        if not CALLS_FIXTURES.exists():
            raise SystemExit(f"{CALLS_FIXTURES} not found - run `manage.py seed_calls_loadtest --confirm-staging` first.")
        _cfx = json.loads(CALLS_FIXTURES.read_text(encoding="utf-8"))
    return _cfx


def _claim(kind, total):
    """Hand each simulated user its OWN slot (pair / busy set / room member). A user beyond `total` gets None and idles:
    two VUs on one account would turn every call into 409 already_in_call and hide the real numbers."""
    with _fx_lock:
        i = _claims.get(kind, 0)
        _claims[kind] = i + 1
    if i >= total:
        if i == total:
            print(f"[calls] '{kind}': more users than fixtures ({total}) - the extra users idle. Seed more or lower -u.")
        return None
    return i


def _xff(acct):
    n = zlib.crc32(str(acct["user_id"]).encode())
    return f"100.{64 + (n >> 22) % 64}.{(n >> 8) & 255}.{n & 255}"   # 100.64.0.0/10 (shared address space), stable per account


def _hdr(acct, extra=None):
    h = {"Authorization": f"Bearer {acct['access']}"}
    if CALLS_SPOOF_XFF:
        h["X-Forwarded-For"] = _xff(acct)
    if extra:
        h.update(extra)
    return h


def _jbody(resp):
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


def _why(resp):
    code = _json_code(resp)
    if resp.status_code == 409:
        return f"409 {code} (a call left over from an earlier run? `seed_calls_loadtest --reset-state`)"
    if resp.status_code == 429:
        return f"429 {code or 'throttled'} (call_initiate 10/min per user, call_initiate_ip 20/min per IP - see CALLS_SPOOF_XFF / CL7)"
    if resp.status_code == 503:
        return "503 LiveKit not configured on this staging stack"
    return f"HTTP {resp.status_code} {code} {resp.text[:100]}".strip()


def _code_row(env, text):
    """A synthetic 0 ms row: a COUNTER of how often a designed answer happened (shows up in the CSV)."""
    _fire(env, "CODE", text, time.perf_counter())


def _fire_ms(env, kind, name, ms, length=0, exc=None):
    env.events.request.fire(request_type=kind, name=name, response_time=ms, response_length=length, exception=exc, context={})


def _req(user, method, path, name, acct, body=None, ok=(200,), soft=(), need=None):
    """One HTTP call with explicit expectations. ok = success statuses; soft = designed answers that are counted
    (CODE row) but are not failures (e.g. 409 version_conflict, 429 on a throttled-by-design endpoint).
    Returns (success, body, status)."""
    with user.client.request(method, path, json=body, headers=_hdr(acct), name=name, catch_response=True) as r:
        data = _jbody(r)
        if r.status_code in ok and (need is None or data.get(need)):
            r.success()
            return True, data, r.status_code
        if r.status_code in soft:
            r.success()
            _code_row(user.environment, f"{name} -> {r.status_code} {data.get('code') or ''}".strip())
            return False, data, r.status_code
        r.failure(_why(r))
        return False, data, r.status_code


class _Ws:
    """One WebSocket + a reader thread. A read timeout is NOT an error (idle sockets are normal) - that is the fix for the
    older ChatWsUser reader, which stopped at the first 10 s of silence."""

    def __init__(self, env, host, path, token, connect_name, on_frame):
        self.env, self.host, self.path, self.token = env, host, path, token
        self.connect_name, self.on_frame = connect_name, on_frame
        self.ws = None

    def open(self):
        if websocket is None:
            raise SystemExit("pip install websocket-client")
        t0 = time.perf_counter()
        try:
            self.ws = websocket.create_connection(_ws_url(self.host, self.path, self.token), timeout=10)
        except Exception as exc:
            _fire(self.env, "WS", self.connect_name, t0, exc=exc)
            self.ws = None
            return False
        _fire(self.env, "WS", self.connect_name, t0)
        threading.Thread(target=self._loop, args=(self.ws,), daemon=True).start()
        return True

    def _loop(self, ws):
        while self.ws is ws:
            try:
                frame = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                return
            if not frame:
                return
            t = time.time()
            try:
                data = json.loads(frame)
            except ValueError:
                continue
            try:
                self.on_frame(data, t)
            except Exception:
                pass

    def send(self, obj):
        try:
            self.ws.send(json.dumps(obj))
            return True
        except Exception:
            return False

    def ping(self):
        try:
            self.ws.ping()
        except Exception:
            pass

    def close(self):
        ws, self.ws = self.ws, None
        try:
            if ws:
                ws.close()
        except Exception:
            pass


# ---- CL1 / CL2 / CL5 / CL6 / CL7: 1:1 calls ------------------------------------------------------------------------
class CallPairUser(HttpUser):
    """One simulated PAIR (caller a + callee b, own private conversation). Loop: initiate -> (callee's socket gets
    `incoming_call`) -> accept | reject | caller cancels | nobody answers -> hold -> end. With 200 pairs there are at most
    200 concurrent calls (400 participants). Outcome mix: CALL_OUTCOMES=accept,reject,cancel,noanswer (default 70,10,10,10)."""
    weight = 0
    wait_time = constant(0)

    def on_start(self):
        self.ring = {}       # call_id -> wall time the incoming_call frame reached the callee's socket
        self.listener = None
        self.idx = _claim("pairs", len(_calls_fx()["pairs"]))
        if self.idx is None:
            return
        self.pair = _calls_fx()["pairs"][self.idx]
        if CALL_RING_WS:
            self.listener = _Ws(self.environment, self.host, f"/ws/chat/{self.pair['conversation_id']}/",
                                self.pair["b"]["access"], "WS call connect", self._on_frame)
            self.listener.open()

    def on_stop(self):
        if self.listener:
            self.listener.close()

    def _on_frame(self, data, t):
        if data.get("event") == "incoming_call" and data.get("call_id"):
            self.ring[str(data["call_id"])] = t

    def _wait_ring(self, call_id, t_sent):
        """CL5: time from 'caller pressed call' to 'the callee's socket received incoming_call'."""
        deadline = time.time() + CALL_RING_WAIT
        while time.time() < deadline:
            t = self.ring.pop(call_id, None)
            if t is not None:
                _fire_ms(self.environment, "WS", N_RING, (t - t_sent) * 1000.0)
                return
            time.sleep(0.05)
        _fire_ms(self.environment, "WS", N_RING, CALL_RING_WAIT * 1000.0,
                 exc=TimeoutError(f"no incoming_call frame within {CALL_RING_WAIT:g}s"))

    def _initiate(self, a, conv):
        with self.client.post("/message/calls/initiate/", json={"conversation_id": conv, "type": random.choice(("audio", "video"))},
                              headers=_hdr(a), name=N_INIT, catch_response=True) as r:
            body = _jbody(r)
            if r.status_code == 200 and body.get("call_id") and body.get("livekit_token"):
                r.success()
                return str(body["call_id"])
            if r.status_code == 429 and CALLS_EXPECT_429:
                r.success()
                _code_row(self.environment, f"call initiate -> 429 {_json_code(r)} (CL7 expected)")
                return None
            r.failure(_why(r))
            return None

    def _action(self, call_id, who, action, name, need_token=False):
        ok, _b, _s = _req(self, "POST", f"/message/calls/{call_id}/action/", name, who, body={"action": action},
                          need="livekit_token" if need_token else None)
        return ok

    def _answered(self, call_id, a, b):
        if not self._action(call_id, b, "accept", N_ACCEPT, need_token=True):
            return
        if random.random() < CALL_TELEMETRY_P:       # CL6: both sides report "connected" (counted once per call+user)
            for who in (a, b):
                _req(self, "POST", f"/message/calls/{call_id}/telemetry/", N_TELEM, who,
                     body={"kind": "connect", "ok": True, "setup_ms": random.randint(600, 3500)}, soft=(429,))
        if random.random() < CALL_REFRESH_P:         # CL6: silent LiveKit token refresh
            for who in (a, b):
                _req(self, "POST", f"/message/calls/{call_id}/refresh-token/", N_REFRESH, who, soft=(429,), need="livekit_token")
        time.sleep(random.uniform(*CALL_HOLD))
        if random.random() < CALL_TELEMETRY_P:
            for who in (a, b):
                _req(self, "POST", f"/message/calls/{call_id}/telemetry/", N_TELEM, who,
                     body={"kind": "summary", "reconnects": 0, "reconnect_ok": 0, "reconnect_fail": 0}, soft=(429,))
        self._action(call_id, random.choice((a, b)), "end", N_END)

    @task
    def one_call(self):
        if self.idx is None:
            time.sleep(30)
            return
        cycle0 = time.time()
        a, b, conv = self.pair["a"], self.pair["b"], self.pair["conversation_id"]
        outcome = random.choices(("accept", "reject", "cancel", "noanswer"), weights=CALL_OUTCOME_W)[0]
        t_sent = time.time()
        call_id = self._initiate(a, conv)
        if call_id is None:
            time.sleep(5)
            return
        if self.listener is not None and self.listener.ws is not None:
            self._wait_ring(call_id, t_sent)
        if outcome == "noanswer":
            time.sleep(CALL_NOANSWER_WAIT)          # the Celery sweeper must end it (MISSED push + bell row for b)
        else:
            time.sleep(random.uniform(*CALL_ACCEPT_DELAY))
            if outcome == "accept":
                self._answered(call_id, a, b)
            elif outcome == "reject":
                self._action(call_id, b, "reject", N_REJECT)
            else:
                self._action(call_id, a, "end", N_CANCEL)
        time.sleep(max(CALL_MIN_CYCLE - (time.time() - cycle0), 0) + random.uniform(*CALL_GAP))


# ---- CL3: A->B and C->B at the same instant -------------------------------------------------------------------------
class CallBusyRacer(HttpUser):
    """One simulated busy SET: K callers ring the SAME callee in the same instant. `call_state.lock_users` is supposed to
    serialise them: exactly ONE gets 200 (ringing), everyone else 409 `callee_busy`. Two 200s = the race is open."""
    weight = 0
    wait_time = between(8, 15)       # each caller initiates once per round -> far below 10/min

    def on_start(self):
        self.idx = _claim("busy", len(_calls_fx()["busy_sets"]))

    @task
    def race(self):
        if self.idx is None:
            time.sleep(30)
            return
        s = _calls_fx()["busy_sets"][self.idx]
        results = []

        def go(caller, conv):
            with self.client.post("/message/calls/initiate/", json={"conversation_id": conv, "type": "audio"},
                                  headers=_hdr(caller), name=N_INIT_RACE, catch_response=True) as r:
                body = _jbody(r)
                if r.status_code == 200 and body.get("call_id"):
                    r.success()
                    results.append((200, str(body["call_id"]), caller))
                elif r.status_code == 409 and _json_code(r) == "callee_busy":
                    r.success()                      # designed answer; each one also costs a missed-call push + bubble (C12)
                    results.append((409, "", caller))
                else:
                    r.failure(_why(r))
                    results.append((r.status_code, "", caller))

        t0 = time.perf_counter()
        jobs = [gevent.spawn(go, c, cv) for c, cv in zip(s["callers"], s["conversation_ids"])]
        gevent.joinall(jobs, timeout=30)
        wins = [x for x in results if x[0] == 200]
        busy = [x for x in results if x[0] == 409]
        problem = None
        if len(results) != len(jobs):
            problem = f"only {len(results)}/{len(jobs)} requests finished"
        elif len(wins) != 1:
            problem = f"{len(wins)} callers got a ringing call (expected exactly 1)"
        elif len(busy) != len(jobs) - 1:
            problem = f"{len(busy)} callee_busy answers (expected {len(jobs) - 1})"
        _fire(self.environment, "CHECK", C_CL3, t0, exc=AssertionError(problem) if problem else None)
        for _status, call_id, caller in wins:        # caller cancels its own ringing call -> the callee is free for the next round
            _req(self, "POST", f"/message/calls/{call_id}/action/", N_CANCEL, caller, body={"action": "end"})


# ---- CL4: group call with the 50-participant cap ---------------------------------------------------------------------
class GroupCallUser(HttpUser):
    """ONE leader (extra users idle). Caller starts a group call in a (cap+1)-member group; the other members press
    accept (the <= CALL_GROUP_RING_CAP ringing ones) or join (everyone else) inside GROUP_JOIN_WINDOW seconds.
    Expected: cap-1 succeed, the rest 409 `call_full`. More than cap-1 successes = `join_call` lets the room overfill."""
    weight = 0
    wait_time = constant(0)

    def on_start(self):
        g = _calls_fx().get("group_call")
        self.leader = bool(g) and _claim("group", 1) == 0

    @task
    def round(self):
        if not self.leader:
            time.sleep(60)
            return
        g = _calls_fx()["group_call"]
        caller, others = g["caller"], g["members"][1:]
        with self.client.post("/message/calls/initiate/", json={"conversation_id": g["conversation_id"], "type": "audio"},
                              headers=_hdr(caller), name=N_INIT_GROUP, catch_response=True) as r:
            body = _jbody(r)
            if r.status_code == 200 and body.get("call_id"):
                r.success()
                call_id = str(body["call_id"])
                ringing = {str(u) for u in (body.get("ringing_user_ids") or [])}
            else:
                r.failure(_why(r))
                time.sleep(GROUP_ROUND_GAP)
                return
        out = []

        def press(m):
            time.sleep(random.uniform(0, GROUP_JOIN_WINDOW))
            action = "accept" if str(m["user_id"]) in ringing else "join"
            with self.client.post(f"/message/calls/{call_id}/action/", json={"action": action}, headers=_hdr(m),
                                  name=N_G_ACCEPT if action == "accept" else N_G_JOIN, catch_response=True) as r2:
                if r2.status_code == 200:
                    r2.success()
                    out.append(200)
                elif r2.status_code == 409 and _json_code(r2) == "call_full":
                    r2.success()
                    out.append("full")
                else:
                    r2.failure(_why(r2))
                    out.append(r2.status_code)

        t0 = time.perf_counter()
        gevent.joinall([gevent.spawn(press, m) for m in others], timeout=120)
        ok, full = out.count(200), out.count("full")
        exp_ok = min(len(others), GROUP_CAP - 1)
        exp_full = len(others) - exp_ok
        problem = None
        if len(out) != len(others) or ok != exp_ok or full != exp_full:
            problem = (f"ok={ok} (expected {exp_ok}), call_full={full} (expected {exp_full}), "
                       f"other/unfinished={len(others) - ok - full}")
        _fire(self.environment, "CHECK", C_CL4, t0, exc=AssertionError(problem) if problem else None)
        time.sleep(GROUP_HOLD)

        def leave(who):
            with self.client.post(f"/message/calls/{call_id}/action/", json={"action": "end"}, headers=_hdr(who),
                                  name=N_G_LEAVE, catch_response=True) as r3:
                r3.success() if r3.status_code < 500 else r3.failure(f"HTTP {r3.status_code}")

        gevent.joinall([gevent.spawn(leave, who) for who in (*others, caller)], timeout=60)
        time.sleep(GROUP_ROUND_GAP)


# ---- SR1-SR6 + WH1: study rooms -------------------------------------------------------------------------------------
_room_ready = {}       # conversation_id -> gevent Event, set once the host has joined (the session exists)
_study_slot_list = None


def _ready_event(conv):
    with _fx_lock:
        ev = _room_ready.get(conv)
        if ev is None:
            ev = _room_ready[conv] = GEvent()
    return ev


def _study_slots():
    global _study_slot_list
    if _study_slot_list is None:
        rooms = _calls_fx()["rooms"]
        if STUDY_ROOMS:
            rooms = rooms[:STUDY_ROOMS]
        per = STUDY_ROOM_USERS or None
        _study_slot_list = [(ri, mi) for ri, r in enumerate(rooms) for mi in range(len(r["members"][:per]))]
    return _study_slot_list


def _b64u(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _livekit_jwt(body):
    """Authorization header LiveKit puts on a webhook: HS256 JWT, iss = api key, `sha256` = base64(sha256(body))."""
    key, secret = os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"]
    now = int(time.time())
    head = _b64u(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iss": key, "nbf": now - 5, "exp": now + 300, "sha256": base64.b64encode(hashlib.sha256(body).digest()).decode()}
    pay = _b64u(json.dumps(claims, separators=(",", ":")).encode())
    sig = _b64u(hmac.new(secret.encode(), head + b"." + pay, hashlib.sha256).digest())
    return (head + b"." + pay + b"." + sig).decode()


class StudyRoomUser(HttpUser):
    """One simulated MEMBER of a 50-person study room (member 0 = host). Loop: join (REST) -> socket on ws/chat/<conv>/ ->
    hold (drawers send draw_point, writers PUT the board, everybody polls room/ and toggles hand) -> leave -> rejoin.
    Receivers time every Nth draw_point frame from the embedded send time (same-host clock; with several injector
    machines the clocks must be NTP-synced or the latency is meaningless)."""
    weight = 0
    wait_time = constant(0)

    def on_start(self):
        if websocket is None:
            raise SystemExit("pip install websocket-client")
        if STUDY_WEBHOOK and not (os.environ.get("LIVEKIT_API_KEY") and os.environ.get("LIVEKIT_API_SECRET")):
            raise SystemExit("STUDY_WEBHOOK=1 needs LIVEKIT_API_KEY and LIVEKIT_API_SECRET in the injector's environment")
        slots = _study_slots()
        self.idx = _claim("rooms", len(slots))
        self.listener = None
        self.rounds = 0
        self.room_name = None
        self.recv = {}
        if self.idx is None:
            return
        r_i, m_i = slots[self.idx]
        self.room = _calls_fx()["rooms"][r_i]
        self.member = self.room["members"][m_i]
        self.conv = self.room["conversation_id"]
        self.is_host = m_i == 0
        self.is_drawer = 1 <= m_i <= STUDY_DRAWERS
        self.is_writer = m_i < STUDY_BOARD_WRITERS
        self.public = bool(_calls_fx().get("public_rooms"))
        self.hand = False
        self.board_pad = "x" * (STUDY_BOARD_KB * 1024)

    def on_stop(self):
        if self.listener:
            self.listener.close()

    # ---- REST ----
    def _join(self, new_session):
        path = f"/message/study-room/{self.conv}/join/"
        name = N_SR_JOIN_HOST if new_session else N_SR_JOIN
        deadline = time.time() + STUDY_WAIT_SECONDS
        rules_done = False
        while True:
            need_rules = waiting = False
            with self.client.post(path, json={"new_session": bool(new_session)}, headers=_hdr(self.member),
                                  name=name, catch_response=True) as r:
                body = _jbody(r)
                code = body.get("code") or ""
                if r.status_code == 200 and body.get("livekit_token"):
                    r.success()
                    self.room_name = body.get("room_name") or body.get("session_id")
                    return True
                if r.status_code == 202 and code == "waiting_room":      # SR6: public group + waiting room
                    r.success()
                    _code_row(self.environment, "study join -> 202 waiting_room")
                    waiting = True
                elif r.status_code == 403 and code == "rules_not_accepted" and not rules_done:   # SR6: rules gate
                    r.success()
                    _code_row(self.environment, "study join -> 403 rules_not_accepted")
                    need_rules = True
                else:
                    r.failure(_why(r))
                    return False
            if need_rules:
                rules_done = True
                if not _req(self, "POST", f"/message/study-room/{self.conv}/rules/", N_SR_RULES, self.member, body={})[0]:
                    return False
                continue
            if waiting:
                if time.time() > deadline:
                    _fire(self.environment, "CHECK", "CHECK SR6 admitted within wait window", time.perf_counter(),
                          exc=TimeoutError(f"still in the waiting room after {STUDY_WAIT_SECONDS:g}s"))
                    return False
                time.sleep(STUDY_WAIT_POLL)           # the real client polls join/ every few seconds
                continue

    def _webhook(self, event):
        """WH1: what LiveKit would POST to Django when this member's media connection comes up / goes down."""
        if not (STUDY_WEBHOOK and self.room_name):
            return
        body = json.dumps({
            "event": event, "id": f"EV_{uuid.uuid4().hex[:14]}", "createdAt": int(time.time()),
            "room": {"name": self.room_name, "sid": "RM_" + uuid.uuid5(uuid.NAMESPACE_URL, self.room_name).hex[:12]},
            "participant": {"identity": str(self.member["user_id"]), "name": "loadtest",
                            "sid": "PA_" + uuid.uuid4().hex[:12], "joinedAt": int(time.time())},
        }, separators=(",", ":")).encode()
        with self.client.post("/message/livekit/webhook/", data=body, name=N_WEBHOOK, catch_response=True,
                              headers={"Authorization": _livekit_jwt(body), "Content-Type": "application/webhook+json"}) as r:
            r.success() if r.status_code == 200 else r.failure(f"HTTP {r.status_code} (401 = wrong LIVEKIT_API_KEY/SECRET)")

    # ---- realtime ----
    def _on_frame(self, data, t):
        if data.get("type") != "study_room_event" or data.get("action") != "draw_point":
            return
        d = data.get("data") or {}
        sent_at, seq, src = d.get("t"), d.get("n"), d.get("s")
        if not isinstance(sent_at, (int, float)) or not isinstance(seq, int):
            return
        st = self.recv.get(src)
        if st is None:
            self.recv[src] = [seq, seq, 1]                 # [lowest seq seen, highest seq seen, frames received]
        else:
            st[0], st[1], st[2] = min(st[0], seq), max(st[1], seq), st[2] + 1
        if seq % STUDY_LAT_SAMPLE == 0:
            _fire_ms(self.environment, "WS", N_SR_DRAW, (t - sent_at) * 1000.0)

    def _check_loss(self):
        expected = sum(hi - lo + 1 for lo, hi, _n in self.recv.values())
        got = sum(n for _lo, _hi, n in self.recv.values())
        if expected >= 50:                                   # too few frames = not worth a verdict
            lost = max(expected - got, 0)
            bad = lost / expected > STUDY_MAX_LOSS
            _fire(self.environment, "CHECK", C_SR3, time.perf_counter(),
                  exc=AssertionError(f"{lost}/{expected} draw frames lost ({lost / expected:.1%} > {STUDY_MAX_LOSS:.1%})") if bad else None)

    def _board(self):
        ok, body, _s = _req(self, "GET", f"/message/study-room/{self.conv}/state/", N_SR_GET, self.member)
        if not ok:
            return
        page = {"id": "p1", "name": "Page 1", "blob": self.board_pad}
        # 409 version_conflict is the DESIGNED answer when two writers race; a real client re-merges. 5xx / 413 / 428 fail.
        _req(self, "PUT", f"/message/study-room/{self.conv}/state/", N_SR_PUT, self.member,
             body={"pages": [page], "base_version": body.get("version", 0)}, soft=(409,))

    def _hold(self):
        now = time.time()
        end = now + random.uniform(*STUDY_HOLD)
        tick = 1.0 / max(STUDY_DRAW_HZ, 0.5) if self.is_drawer else 0.5
        next_tick = now
        drawing, phase_end, seq = False, now + random.uniform(0, STUDY_DRAW_OFF), 0
        t_hand = now + random.uniform(30, 120)
        t_room = now + random.uniform(5, STUDY_ROOM_POLL)
        t_board = now + random.uniform(5, STUDY_BOARD_EVERY)
        t_ping = now + 20
        t_refresh = now + random.uniform(60, 240) if random.random() < STUDY_REFRESH_P else None
        t_admit = now + 3
        while time.time() < end and self.listener is not None and self.listener.ws is not None:
            now = time.time()
            if self.is_drawer:
                if now >= phase_end:
                    drawing = not drawing
                    phase_end = now + (STUDY_DRAW_ON if drawing else STUDY_DRAW_OFF)
                if drawing:
                    seq += 1
                    self.listener.send({"type": "study_room_event", "action": "draw_point",
                                        "data": {"t": now, "n": seq, "s": str(self.member["user_id"]), "pageId": "p1",
                                                 "x": random.random(), "y": random.random(), "c": "#222222", "w": 3}})
            if now >= t_ping:
                self.listener.ping()
                t_ping = now + 20
            if now >= t_room:
                _req(self, "GET", f"/message/study-room/{self.conv}/room/", N_SR_ROOM, self.member)
                t_room = now + STUDY_ROOM_POLL
            if now >= t_hand:
                self.hand = not self.hand
                _req(self, "POST", f"/message/study-room/{self.conv}/hand/", N_SR_HAND, self.member,
                     body={"raised": self.hand}, soft=(429,))
                t_hand = now + random.uniform(60, 180)
            if self.is_writer and now >= t_board:
                self._board()
                t_board = now + STUDY_BOARD_EVERY
            if t_refresh and now >= t_refresh and self.room_name:
                _req(self, "POST", f"/message/study-room/{self.conv}/refresh-token/", N_SR_REFRESH, self.member,
                     body={"room_name": self.room_name}, soft=(429,), need="livekit_token")
                t_refresh = None
            if self.is_host and self.public and now >= t_admit:      # SR6: host lets the waiting members in
                _req(self, "POST", f"/message/study-room/{self.conv}/admit/", N_SR_ADMIT, self.member,
                     body={"action": "admit_all"}, soft=(429,))
                t_admit = now + 4
            next_tick += tick
            time.sleep(max(next_tick - time.time(), 0))
            if next_tick < time.time() - 1:                           # fell behind (injector overloaded): do not burst to catch up
                next_tick = time.time()

    @task
    def session(self):
        if self.idx is None:
            time.sleep(60)
            return
        first = self.rounds == 0
        self.rounds += 1
        ev = _ready_event(self.conv)
        if self.is_host and first:
            ok = self._join(new_session=True)
            ev.set()          # members start even if the host join failed - they then fail too, which is the finding
        else:
            if first:
                ev.wait(timeout=90)
                time.sleep(random.uniform(0, STUDY_JOIN_WINDOW))       # SR1: this window IS the join storm
            else:
                time.sleep(random.uniform(*STUDY_REJOIN_GAP))
            ok = self._join(new_session=False)
        if not ok:
            time.sleep(10)
            return
        self._webhook("participant_joined")
        self.recv = {}
        self.listener = _Ws(self.environment, self.host, f"/ws/chat/{self.conv}/", self.member["access"], N_SR_WS, self._on_frame)
        if self.listener.open():
            self._hold()
        self._check_loss()
        self.listener.close()
        self._webhook("participant_left")


if LIVE_ONLY:
    # switch the old mix off and the live mix on (Locust picks classes by weight; weight 0 = never spawned)
    FeedUser.weight = ChatWsUser.weight = LiveJoinUser.weight = 0
    LiveChatUser.weight = 97
    LiveAbuser.weight = 2
    LiveModerator.weight = 1
    LiveReporter.weight = int(os.environ.get("LIVE_REPORTER_WEIGHT", 0))

if CALLS_ONLY:
    if LIVE_ONLY:
        raise SystemExit("LIVE_ONLY and CALLS_ONLY are exclusive")
    FeedUser.weight = ChatWsUser.weight = LiveJoinUser.weight = 0
    _mix = _parse_mix()
    CallPairUser.weight = _mix.get("pairs", 0)
    CallBusyRacer.weight = _mix.get("busy", 0)
    GroupCallUser.weight = _mix.get("group", 0)
    StudyRoomUser.weight = _mix.get("rooms", 0)


# --------------------------------------------------------------------------
# Load profiles
# --------------------------------------------------------------------------
# (duration_end_seconds, target_users, spawn_rate)
PROFILES = {
    "smoke":    [(60, 10, 5)],
    "baseline": [(120, 100, 10), (600, 100, 10)],
    # ramp slowly: spawn_rate is also the join-storm rate against session_join / Postgres
    "1k":       [(120, 200, 20), (300, 500, 25), (480, 1000, 30), (1380, 1000, 30), (1500, 0, 100)],
    "soak":     [(300, 300, 10), (4 * 3600, 300, 10)],
    # X2 (CALLS_ONLY=1)
    "calls_smoke": [(90, 10, 5)],
    "calls_200":   [(60, 100, 20), (180, 200, 20), (900, 200, 20), (960, 0, 100)],
    "rooms_1k":    [(60, 500, 50), (240, 1000, 50), (1200, 1000, 50), (1260, 0, 200)],
    "rooms_storm": [(20, 1000, 100), (600, 1000, 100), (660, 0, 200)],
}


class Shape(LoadTestShape):
    stages = PROFILES[os.environ.get("PROFILE", "smoke")]

    def tick(self):
        t = self.get_run_time()
        for end, users, rate in self.stages:
            if t < end:
                return users, rate
        return None


# --------------------------------------------------------------------------
# Pass/fail gate
# --------------------------------------------------------------------------
@events.quitting.add_listener
def _enforce_thresholds(environment, **_):
    bad = []
    gate = _calls_thresholds() if CALLS_ONLY else (LIVE_THRESHOLDS if LIVE_ONLY else THRESHOLDS)
    for key, limit in gate.items():
        method = next((m for m in ("GET", "POST", "PUT", "CHECK") if key.startswith(m + " ")), "WS")
        entry = environment.stats.entries.get((key, method))
        if entry is None or entry.num_requests == 0:
            bad.append(f"{key}: no samples recorded (scenario didn't run?)")
            continue
        p95 = entry.get_response_time_percentile(0.95)
        ratio = entry.num_failures / max(entry.num_requests, 1)
        if p95 is not None and p95 > limit["p95_ms"]:
            bad.append(f"{key}: p95 {p95:.0f}ms > {limit['p95_ms']}ms")
        if ratio > limit["fail_ratio"]:
            bad.append(f"{key}: failure ratio {ratio:.2%} > {limit['fail_ratio']:.2%}")
    if bad:
        print("\nLOAD TEST THRESHOLDS BREACHED:\n  - " + "\n  - ".join(bad))
        environment.process_exit_code = 1
    else:
        print("\nAll load-test thresholds met.")
