"""
loadtest/locustfile_pc6.py  (P-C6)

The four launch-gate scenarios of LOAD_TEST_PLAN.md in ONE file, each runnable alone or all together:

    feed    1,000 feed users   GET /post/feed/ (page 1, page 2, page 3+) + following feed
    live      200 live viewers POST /tuitionclass/sessions/<id>/join/ + ws/tuitionclass/session/<id>/ held open
    upload     50 uploaders    burst of resumable uploads: init -> chunks -> complete (a real image post is created)
    login     100 logins/s-ish burst of POST /login/ (real password hashing) + first feed page after login

Run (STAGING ONLY - see LOAD_TEST_PLAN.md section 4 for the full checklist):

    python manage.py seed_loadtest --users 1500 --confirm-staging --out loadtest/fixtures.json
    SCENARIO=all PROFILE=gate locust -f loadtest/locustfile_pc6.py --headless --host https://staging.example.com \\
        --csv loadtest/out/pc6_gate --html loadtest/out/pc6_gate.html

Env (all optional):
    SCENARIO        feed | live | upload | login | all           (default all)
    PROFILE         smoke | gate | live_storm | login_burst | upload_burst | soak   (default smoke)
    PC6_FEED / PC6_LIVE / PC6_UPLOAD / PC6_LOGIN   user counts per scenario (defaults 1000 / 200 / 50 / 100);
                    they are also the Locust weights, so the PROFILE's total user target splits exactly like this.
    FIXTURES        path of fixtures.json (default loadtest/fixtures.json)  -> needs >= sum of the counts + 1 users
    WS_BASE         wss://... if the sockets are NOT on --host
    LIVE_HOLD_MIN / LIVE_HOLD_MAX     seconds a viewer stays in the class (default 600 / 900)
    UPLOAD_PX       image side in pixels (default 512 -> ~0.8 MB incompressible PNG = 4 chunks of 256 KB)
    UPLOAD_KIND     post | story            (default post)
    LOGIN_PASSWORD  the password seed_loadtest used (default below)
    LOGIN_REPEAT_S  a login VU logs in again every N seconds (default 6 -> 100 VUs = ~17 logins/s; 3 -> ~33/s; 2 -> ~50/s)
    FEED_READ_MIN/MAX, FEED_WAIT_MIN/MAX   feed pacing (defaults 2-6 s between pages, 5-15 s between scrolls, see below)
    LOADTEST_XFF=1  (default ON) send a stable fake X-Forwarded-For per account. Without it every request comes from the
                    injector's ONE IP and the anonymous 20/min throttle on POST /login/ (a real protection) hides the
                    capacity of the login path. Only meaningful if the staging proxy sets NUM_PROXIES / trusts XFF the same
                    way production does - check it, or the number you get is not production's.

Thresholds (release gate) are in THRESHOLDS below; Locust exits 1 when one is breached.
Written by reading the code only - nothing was executed.
"""
import hashlib
import json
import os
import random
import struct
import threading
import time
import uuid
import zlib
from pathlib import Path
from urllib.parse import urlparse

from locust import HttpUser, LoadTestShape, between, events, task

try:
    import websocket  # websocket-client
except ImportError:  # only the live scenario needs it
    websocket = None

SCENARIO = os.environ.get("SCENARIO", "all").strip().lower()
PROFILE = os.environ.get("PROFILE", "smoke").strip().lower()
FIXTURES = Path(os.environ.get("FIXTURES", Path(__file__).with_name("fixtures.json")))
WS_BASE = os.environ.get("WS_BASE", "")

C_FEED = int(os.environ.get("PC6_FEED", 1000))
C_LIVE = int(os.environ.get("PC6_LIVE", 200))
C_UPLOAD = int(os.environ.get("PC6_UPLOAD", 50))
C_LOGIN = int(os.environ.get("PC6_LOGIN", 100))

FEED_PAGES = int(os.environ.get("FEED_PAGES", 4))
LIVE_HOLD = (float(os.environ.get("LIVE_HOLD_MIN", 600)), float(os.environ.get("LIVE_HOLD_MAX", 900)))
UPLOAD_PX = int(os.environ.get("UPLOAD_PX", 512))
UPLOAD_KIND = os.environ.get("UPLOAD_KIND", "post")
UPLOAD_CHUNK = 256 * 1024  # = MIN_CHUNK_SIZE of post/upload_sessions.py: the smallest legal chunk -> most requests per MB
LOGIN_PASSWORD = os.environ.get("LOGIN_PASSWORD", "LoadTest!2026-not-a-secret")  # = PASSWORD of seed_loadtest.py
LOGIN_REPEAT_S = float(os.environ.get("LOGIN_REPEAT_S", 6))   # 100 VUs / 6 s = ~17 logins/s = 1,000 logins in a minute
# Feed pacing. Defaults = a person reading (~0.19 req/s per user -> ~190 req/s at 1,000 users). FEED_READ_* is the pause
# between pages inside one scroll, FEED_WAIT_* the pause between scrolls. Stress run: FEED_READ_MIN=0.5 FEED_READ_MAX=2
# FEED_WAIT_MIN=1.5 FEED_WAIT_MAX=4 (~0.65 req/s per user -> ~650 req/s).
FEED_READ = (float(os.environ.get("FEED_READ_MIN", 2)), float(os.environ.get("FEED_READ_MAX", 6)))
FEED_WAIT = (float(os.environ.get("FEED_WAIT_MIN", 5)), float(os.environ.get("FEED_WAIT_MAX", 15)))
SPOOF_XFF = os.environ.get("LOADTEST_XFF", "1").lower() in ("1", "true", "yes")


def _on(name):
    return SCENARIO in ("all", name)


# Request-name constants (Locust "name" -> threshold key)
N_FEED_P1 = "GET /post/feed/"
N_FEED_P2 = "GET /post/feed/ [page 2]"
N_FEED_P3 = "GET /post/feed/ [page 3+]"
N_LIVE_JOIN = "POST /tuitionclass/sessions/[id]/join/"
N_LIVE_WS = "WS live connect"
N_LIVE_DROP = "WS live held"           # one sample per viewer per class: success = stayed connected for the whole hold
N_UP_INIT = "POST /post/uploads/ [init]"
N_UP_CHUNK = "POST /post/uploads/[id]/chunk/"
N_UP_DONE = "POST /post/uploads/[id]/complete/"
N_LOGIN = "POST /login/"
N_LOGIN_FEED = "GET /post/feed/ [after login]"

# Pass/fail gate. p95 in ms, fail_ratio as a fraction. Each key is only enforced when its scenario ran.
THRESHOLDS = {
    "feed": {
        N_FEED_P1: {"p95_ms": 800, "fail_ratio": 0.01},
        N_FEED_P2: {"p95_ms": 800, "fail_ratio": 0.01},
    },
    "live": {
        N_LIVE_JOIN: {"p95_ms": 1500, "fail_ratio": 0.01},
        N_LIVE_WS: {"p95_ms": 1000, "fail_ratio": 0.01},
        N_LIVE_DROP: {"p95_ms": 10 ** 9, "fail_ratio": 0.01},   # no latency limit: only "did the socket survive" (<= 1 % dropped)
    },
    "upload": {
        N_UP_INIT: {"p95_ms": 500, "fail_ratio": 0.01},
        N_UP_CHUNK: {"p95_ms": 1500, "fail_ratio": 0.01},
        N_UP_DONE: {"p95_ms": 5000, "fail_ratio": 0.02},
    },
    "login": {
        N_LOGIN: {"p95_ms": 1500, "fail_ratio": 0.01},
        N_LOGIN_FEED: {"p95_ms": 1200, "fail_ratio": 0.01},   # cold first feed (no seen-ids yet, pools built) right after login
    },
}

# --------------------------------------------------------------------------
# fixtures + account allocation (each scenario gets its OWN slice of the seeded users, so nobody shares an account)
# --------------------------------------------------------------------------
_fx = None
_lock = threading.Lock()
_next = {"feed": 0, "live": 0, "upload": 0, "login": 0}
_SLICE = {}  # filled lazily: name -> (start, size)


def _fixtures():
    global _fx
    if _fx is None:
        if not FIXTURES.exists():
            raise SystemExit(f"{FIXTURES} not found - run `manage.py seed_loadtest --users 1500 --confirm-staging` first.")
        _fx = json.loads(FIXTURES.read_text(encoding="utf-8"))
        need = 1 + C_FEED + C_LIVE + C_UPLOAD + C_LOGIN  # users[0] is the teacher / session host
        if len(_fx["users"]) < need and SCENARIO == "all":
            print(f"WARNING: fixtures have {len(_fx['users'])} users but SCENARIO=all needs {need}; accounts will be shared "
                  f"(429s and skewed numbers). Re-seed with --users {need}.")
        start = 1
        for name, n in (("feed", C_FEED), ("live", C_LIVE), ("upload", C_UPLOAD), ("login", C_LOGIN)):
            _SLICE[name] = (start, n)
            start += n
    return _fx


def account(scenario):
    fx = _fixtures()
    users = fx["users"]
    start, size = _SLICE[scenario]
    with _lock:
        i = _next[scenario]
        _next[scenario] += 1
    return users[(start + i % max(size, 1)) % len(users)]


def _xff(acct):
    n = zlib.crc32(str(acct["user_id"]).encode())
    return f"100.{64 + (n >> 22) % 64}.{(n >> 8) & 255}.{n & 255}"   # 100.64.0.0/10, stable per account


def _hdrs(acct, bearer=True):
    h = {}
    if bearer:
        h["Authorization"] = f"Bearer {acct['access']}"
    if SPOOF_XFF:
        h["X-Forwarded-For"] = _xff(acct)
    return h


def _fire(env, kind, name, started, length=0, exc=None):
    env.events.request.fire(request_type=kind, name=name, response_time=(time.perf_counter() - started) * 1000,
                            response_length=length, exception=exc, context={})


def _ws_url(host, path, acct):
    """One-time ticket (X6b / P-B2): the JWT never goes into the URL. A failure here is an error, not a fallback."""
    import requests

    base = (WS_BASE or host.replace("https://", "wss://").replace("http://", "ws://")).rstrip("/")
    r = requests.post(f"{host.rstrip('/')}/login/auth/ws-ticket/", headers=_hdrs(acct), timeout=10)
    if r.status_code == 200 and r.json().get("ticket"):
        return f"{base}{path}?ticket={r.json()['ticket']}"
    raise RuntimeError(f"ws-ticket answered HTTP {r.status_code}")


# --------------------------------------------------------------------------
# 1) FEED  - 1,000 users
# --------------------------------------------------------------------------
class Pc6FeedUser(HttpUser):
    abstract = not _on("feed")
    weight = C_FEED
    wait_time = between(*FEED_WAIT)

    def on_start(self):
        self.acct = account("feed")
        self.client.headers.update(_hdrs(self.acct))

    @task(5)
    def scroll_feed(self):
        url = "/post/feed/"
        for page in range(FEED_PAGES):
            name = (N_FEED_P1, N_FEED_P2, N_FEED_P3)[min(page, 2)]
            with self.client.get(url, name=name, catch_response=True) as r:
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
            p = urlparse(nxt)
            url = f"{p.path}?{p.query}"   # keep path+query: --host stays the injector's view of the system
            time.sleep(random.uniform(*FEED_READ))

    @task(1)
    def following_feed(self):
        self.client.get("/post/feed/?source=following", name="GET /post/feed/?source=following")


# --------------------------------------------------------------------------
# 2) LIVE VIEWERS - 200 students in ONE class
# --------------------------------------------------------------------------
class Pc6LiveViewer(HttpUser):
    abstract = not _on("live")
    weight = C_LIVE
    wait_time = between(1, 2)

    def on_start(self):
        if websocket is None:
            raise SystemExit("pip install websocket-client")
        self.acct = account("live")
        self.client.headers.update(_hdrs(self.acct))
        self.ws = None
        self.joined = False
        self.frames = 0

    def on_stop(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass

    @task
    def attend_class(self):
        sid = _fixtures()["session_id"]
        if not self.joined:
            with self.client.post(f"/tuitionclass/sessions/{sid}/join/", name=N_LIVE_JOIN, catch_response=True) as r:
                if r.status_code in (200, 201):
                    self.joined = True
                    r.success()
                else:
                    r.failure("throttled (session_join)" if r.status_code == 429 else f"HTTP {r.status_code}: {r.text[:120]}")
                    time.sleep(5)
                    return
            t0 = time.perf_counter()
            try:
                self.ws = websocket.create_connection(_ws_url(self.host, f"/ws/tuitionclass/session/{sid}/", self.acct), timeout=10)
                _fire(self.environment, "WS", N_LIVE_WS, t0)
            except Exception as exc:
                _fire(self.environment, "WS", N_LIVE_WS, t0, exc=exc)
                self.ws = None
                self.joined = False
                time.sleep(5)
                return
        # sit in the class for the whole hold; an unexpected close BEFORE the hold ends = a dropped viewer
        hold_start = time.perf_counter()
        end = time.time() + random.uniform(*LIVE_HOLD)
        dropped = None
        while time.time() < end and self.ws is not None:
            try:
                self.ws.settimeout(5)
                if self.ws.recv() is not None:
                    self.frames += 1
            except websocket.WebSocketTimeoutException:
                try:
                    self.ws.ping()
                except Exception as exc:
                    dropped = exc
                    break
            except Exception as exc:
                dropped = exc
                break
        _fire(self.environment, "WS", N_LIVE_DROP, hold_start, exc=dropped)
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.ws = None
            self.joined = False


# --------------------------------------------------------------------------
# 3) UPLOAD BURST - resumable upload: init -> N chunks -> complete (creates a real post / story)
# --------------------------------------------------------------------------
def _png(px):
    """A valid, INCOMPRESSIBLE (random pixels) RGB PNG, stdlib only. Fresh bytes per call -> no content-dedupe shortcuts."""
    raw = b"".join(b"\x00" + os.urandom(px * 3) for _ in range(px))   # filter byte 0 + random row
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", px, px, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


class Pc6UploadUser(HttpUser):
    abstract = not _on("upload")
    weight = C_UPLOAD
    wait_time = between(2, 6)

    def on_start(self):
        self.acct = account("upload")
        self.client.headers.update(_hdrs(self.acct))

    @task
    def upload_one(self):
        data = _png(UPLOAD_PX)
        total = (len(data) + UPLOAD_CHUNK - 1) // UPLOAD_CHUNK
        kind = UPLOAD_KIND if UPLOAD_KIND in ("post", "story") else "post"
        body = {
            "idempotency_key": "lt-" + uuid.uuid4().hex,
            "kind": kind,
            "chunk_size": UPLOAD_CHUNK,
            "payload": {"content": "load test upload", "category": "tech"} if kind == "post" else {},
            "files": [{"role": "media", "name": "lt.png", "size": len(data), "total_chunks": total, "content_type": "image/png"}],
        }
        with self.client.post("/post/uploads/", json=body, name=N_UP_INIT, catch_response=True) as r:
            if r.status_code not in (200, 201):
                r.failure(f"HTTP {r.status_code}: {r.text[:150]}")
                return
            try:
                sid = r.json()["session_id"]
            except (ValueError, KeyError):
                r.failure("init without session_id")
                return
            r.success()
        for i in range(total):
            part = data[i * UPLOAD_CHUNK:(i + 1) * UPLOAD_CHUNK]
            with self.client.post(f"/post/uploads/{sid}/chunk/", name=N_UP_CHUNK, catch_response=True,
                                  data={"file_index": 0, "chunk_index": i, "chunk_hash": hashlib.md5(part).hexdigest()},
                                  files={"chunk": ("chunk", part, "application/octet-stream")}) as r:
                if r.status_code != 200:
                    r.failure(f"HTTP {r.status_code}: {r.text[:150]}")
                    self.client.delete(f"/post/uploads/{sid}/", name="DELETE /post/uploads/[id]/ [cleanup]")
                    return
                r.success()
        with self.client.post(f"/post/uploads/{sid}/complete/", json={}, name=N_UP_DONE, catch_response=True) as r:
            if r.status_code in (200, 201):
                r.success()
            else:
                r.failure(f"HTTP {r.status_code}: {r.text[:150]}")


# --------------------------------------------------------------------------
# 4) LOGIN BURST - real password verification (PBKDF2/Argon2 = CPU) + first feed page
# --------------------------------------------------------------------------
class Pc6LoginUser(HttpUser):
    abstract = not _on("login")
    weight = C_LOGIN
    wait_time = between(LOGIN_REPEAT_S * 0.8, LOGIN_REPEAT_S * 1.2)

    def on_start(self):
        self.acct = account("login")

    @task
    def login_then_open_feed(self):
        with self.client.post("/login/", json={"username": self.acct["username"], "password": LOGIN_PASSWORD},
                              headers=_hdrs(self.acct, bearer=False), name=N_LOGIN, catch_response=True) as r:
            if r.status_code != 200:
                r.failure("throttled / locked (429)" if r.status_code == 429 else f"HTTP {r.status_code}: {r.text[:150]}")
                return
            try:
                access = (r.json().get("token") or {}).get("access")
            except ValueError:
                access = None
            if not access:
                r.failure("200 but no token.access (2FA / captcha challenge on a load-test account?)")
                return
            r.success()
        hdrs = _hdrs(self.acct, bearer=False)
        hdrs["Authorization"] = f"Bearer {access}"
        self.client.get("/post/feed/", headers=hdrs, name=N_LOGIN_FEED)


# --------------------------------------------------------------------------
# Profiles: (end_second, total_users, spawn_rate)
# --------------------------------------------------------------------------
_TOTAL = (C_FEED if _on("feed") else 0) + (C_LIVE if _on("live") else 0) + (C_UPLOAD if _on("upload") else 0) + (C_LOGIN if _on("login") else 0)


def _profile(name):
    t = max(_TOTAL, 1)
    return {
        # 10 users, 1 minute: if this is not green do not run anything bigger
        "smoke": [(60, min(10, t), 5)],
        # the release gate: ramp over 8 min (the live join storm is spawn_rate-limited), hold 15 min, ramp down
        "gate": [(120, t // 5, 20), (300, t // 2, 25), (480, t, 30), (1380, t, 30), (1500, 0, 100)],
        # class starts: everybody joins within ~10 s (use SCENARIO=live)
        "live_storm": [(10, t, max(t // 10, 1)), (900, t, 50), (960, 0, 100)],
        # 8 a.m. login wave: full count within 5 s, hold 3 min (use SCENARIO=login)
        "login_burst": [(5, t, max(t // 5, 1)), (185, t, 50), (200, 0, 100)],
        # everybody posts at once after a test / event (use SCENARIO=upload)
        "upload_burst": [(10, t, max(t // 10, 1)), (310, t, 50), (330, 0, 100)],
        # leak hunt: 30 % of the gate load for 4 h
        "soak": [(300, int(t * 0.3), 10), (4 * 3600, int(t * 0.3), 10)],
    }[name]


class Pc6Shape(LoadTestShape):
    stages = _profile(PROFILE if PROFILE in ("smoke", "gate", "live_storm", "login_burst", "upload_burst", "soak") else "smoke")

    def tick(self):
        t = self.get_run_time()
        for end, users, rate in self.stages:
            if t < end:
                return users, rate
        return None


# --------------------------------------------------------------------------
# Pass / fail gate
# --------------------------------------------------------------------------
@events.quitting.add_listener
def _enforce(environment, **_):
    bad = []
    for scenario, gate in THRESHOLDS.items():
        if not _on(scenario):
            continue
        for key, limit in gate.items():
            method = next((m for m in ("GET", "POST", "PUT", "DELETE") if key.startswith(m + " ")), "WS")
            entry = environment.stats.entries.get((key, method))
            if entry is None or entry.num_requests == 0:
                bad.append(f"[{scenario}] {key}: no samples recorded (scenario did not run?)")
                continue
            p95 = entry.get_response_time_percentile(0.95)
            ratio = entry.num_failures / max(entry.num_requests, 1)
            if p95 is not None and p95 > limit["p95_ms"]:
                bad.append(f"[{scenario}] {key}: p95 {p95:.0f} ms > {limit['p95_ms']} ms")
            if ratio > limit["fail_ratio"]:
                bad.append(f"[{scenario}] {key}: failures {ratio:.2%} > {limit['fail_ratio']:.2%}")
    if bad:
        print("\nPC6 LOAD TEST THRESHOLDS BREACHED:\n  - " + "\n  - ".join(bad))
        environment.process_exit_code = 1
    else:
        print("\nPC6: all load-test thresholds met.")
