# message/livekit_utils.py - FIX
import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from typing import Optional, Tuple

from livekit import api

logger = logging.getLogger(__name__)

# Ab hardcoded nahi - .env / environment variables se aayega
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# 🔥 TASK 21 — Egress (recording start/stop) calls hit LiveKit's HTTP
# API, unlike `generate_livekit_token` above which never talks to the
# server at all (a JWT is signed locally). That HTTP API needs an
# http(s):// base URL — the ws:// / wss:// URL clients use to *join* a
# room (`LIVEKIT_WS_URL`, in views.py) won't work here. Most deployments
# run both off the same LiveKit host, so rather than force a second URL
# env var onto every deployment, default to deriving it from
# `LIVEKIT_WS_URL` (ws:// -> http://, wss:// -> https://) and only
# require `LIVEKIT_URL` to be set explicitly if that assumption is wrong
# (e.g. egress sits behind a different ingress than the media server).
# 🔥 C4 — pehle default `ws://10.93.221.189:7880` (LAN IP, plain ws) tha, jo
# production me chupchap galat/blocked config ban jaata tha (iOS ATS aur
# Android cleartext policy ws:// ko block karti hain, aur LAN IP bahar se
# reachable hi nahi). Ab:
#   * LIVEKIT_WS_URL (client ke join karne wala URL) — production me `wss://<domain>`
#     hona ZAROORI hai (settings.py boot par validate karta hai).
#   * DEBUG=True me hi local fallback `ws://127.0.0.1:7880` milta hai.
#   * Production me unset ho to "" — `generate_livekit_token()` clean
#     RuntimeError (views -> 503) deta hai, poora app boot hota rehta hai.
_DEBUG = os.getenv("DEBUG", "False") == "True"
_DEV_WS_FALLBACK = "ws://127.0.0.1:7880"
_ws_env = (os.getenv("LIVEKIT_WS_URL") or "").strip()
if not _ws_env:
    # purana alias — sirf tab jab ws(s):// shape ka ho (LIVEKIT_URL kuch
    # jagah HTTP API URL ke liye use hota hai, use client URL mat banao)
    _alias = (os.getenv("LIVEKIT_URL") or "").strip()
    if _alias.startswith(("ws://", "wss://")):
        _ws_env = _alias
LIVEKIT_WS_URL = _ws_env or (_DEV_WS_FALLBACK if _DEBUG else "")


def _to_http(url: str) -> str:
    return (url or "").replace("wss://", "https://").replace("ws://", "http://")


# Server-to-server (egress / delete_room) HTTP API URL. Priority:
#   LIVEKIT_HTTP_URL (explicit, naya) > LIVEKIT_URL (purana) > LIVEKIT_WS_URL se derive.
# Production me ye aksar internal URL hota hai (e.g. http://livekit:7880),
# isliye client wss URL se alag set kar sako.
LIVEKIT_HTTP_URL = _to_http(
    (os.getenv("LIVEKIT_HTTP_URL") or os.getenv("LIVEKIT_URL") or LIVEKIT_WS_URL or "").strip()
)

# 🔥 TASK 21 — where egress writes the finished recording. LiveKit's
# egress service supports S3/GCS/Azure uploads or a local filepath (only
# useful if egress has a shared volume with wherever you serve files
# from). Bucket name is the one thing this app needs to know (to build
# `recording_url` back out of what egress returns); the actual
# credentials/region for the upload are LiveKit *server*-side config
# (its own egress.yaml / env), not something this Django app holds.
# If unset, recording still works but egress falls back to writing to
# local disk on the egress worker — fine for local dev, not for prod.
LIVEKIT_EGRESS_S3_BUCKET = os.getenv("LIVEKIT_EGRESS_S3_BUCKET")
LIVEKIT_EGRESS_PUBLIC_BASE_URL = os.getenv("LIVEKIT_EGRESS_PUBLIC_BASE_URL")

# 🔥 FIX (production readiness) — pehle ye check MODULE IMPORT time pe
# RuntimeError raise karta tha. `views.py` (jo `urls.py` -> Django startup
# pe hi import hoti hai) `livekit_utils` import karta hai — matlab agar
# LIVEKIT env vars set nahi hain to poora Django process boot hi nahi hota,
# CHAHE koi bhi call/study-room feature use na kar raha ho (plain text chat
# bhi crash). Ek unrelated integration ki missing config se poora app down
# ho jaana single-point-of-failure hai. Ab check LAZY hai — sirf tab fire
# hota hai jab actually koi token generate karne ki koshish ho (call
# initiate / study-room join), aur us waqt bhi clean 503-able error deta
# hai jise view apne except me pakad sakti hai, poore process ko nahi le
# jaata.
def _get_livekit_credentials():
    if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET:
        raise RuntimeError(
            "LIVEKIT_API_KEY ya LIVEKIT_API_SECRET set nahi hai. "
            ".env file check karo ya environment variables set karo."
        )
    return LIVEKIT_API_KEY, LIVEKIT_API_SECRET


# 🔥 FIX — TTL pehle hardcoded 2 ghante tha for EVERY token, calls ke liye
# aur study-room join ke liye bhi same function use hota hai
# (`StudyRoomJoinView`). Study room ek persistent Google-Meet-jaisa room
# hai jo 2 ghante se zyada aasani se chal sakta hai — token expire hote hi
# LiveKit connection bina kisi warning ke drop ho jaata, user ko lagta app
# crash ho gaya. Ab caller (calls vs study-room) apni zaroorat ke hisaab
# se TTL pass kar sakta hai; default 2 hours calls ke liye pehle jaisa hi
# rakha hai taaki behavior na badle jahan explicitly override nahi kiya.
def livekit_configured() -> bool:
    """🔥 C4 — join/token ke liye zaroori config (key, secret, client WS URL) maujood hai?
    Views side-effects (session reset, notes purge) se PEHLE ise check karte hain."""
    return bool(LIVEKIT_API_KEY and LIVEKIT_API_SECRET and LIVEKIT_WS_URL)


def generate_livekit_token(room_name: str, user_id, user_name: str, ttl: timedelta = timedelta(hours=2)) -> str:
    api_key, api_secret = _get_livekit_credentials()
    # 🔥 C4 — client ko dene ke liye join URL hona hi chahiye; warna token
    # bina URL ke bekaar hai. Ek hi clean error (views -> 503).
    if not LIVEKIT_WS_URL:
        raise RuntimeError(
            "LIVEKIT_WS_URL set nahi hai (production me wss://<livekit-domain>). "
            "deploy/livekit/README.md dekho."
        )
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(str(user_id))
        .with_name(user_name)
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_ttl(ttl)
    )
    return token.to_jwt()


# ======================================================================
# 🔥 C13 — token expiry: refresh + max-duration warning
# ----------------------------------------------------------------------
# LiveKit token ek fixed TTL ke baad expire hota hai (calls 2h, study room
# 8h). Reconnect / resume ke waqt SDK wahi purana token dobara bhejta hai —
# expire ho chuka ho to connection bina kisi warning ke gir jaata tha.
# Ab:
#   * client expiry se pehle `refresh-token` endpoint se naya token leta hai
#     (silent), aur reconnect ke liye `room.engine.token` update karta hai;
#   * har token-response me expiry metadata aata hai (`token_expires_at`,
#     `session_ends_at`, `warn_before_seconds`) taaki client "5 min me session
#     khatam" warning dikha sake;
#   * session ki hard cap ke baad refresh 410 `max_duration_reached` deta hai,
#     aur refresh kiye token ka TTL cap se aage jaata hi nahi (clip hota hai).
# Saare knobs settings.py me (LIVEKIT_*), getattr(...) default ke saath.
# ======================================================================
def _setting_int(name: str, default: int) -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, name, default))
    except Exception:
        return default


def call_token_ttl() -> timedelta:
    """Call token ka normal TTL (default 2h — pehle jaisa)."""
    return timedelta(seconds=max(_setting_int("LIVEKIT_CALL_TOKEN_TTL_SECONDS", 2 * 3600), 60))


def study_room_token_ttl() -> timedelta:
    """Study-room token ka normal TTL (default 8h — pehle jaisa)."""
    return timedelta(seconds=max(_setting_int("LIVEKIT_STUDY_ROOM_TOKEN_TTL_SECONDS", 8 * 3600), 60))


def study_room_max_duration() -> timedelta:
    """Ek user ke ek continuous study-room session (join se) ki hard cap (default 12h)."""
    return timedelta(seconds=max(_setting_int("LIVEKIT_STUDY_ROOM_MAX_SECONDS", 12 * 3600), 600))


def session_warn_seconds() -> int:
    """Hard cap se itna pehle client warning dikhaye (default 300s = 5 min)."""
    return max(_setting_int("LIVEKIT_SESSION_WARN_SECONDS", 300), 30)


_MIN_TOKEN_TTL = timedelta(seconds=60)


def bounded_ttl(ttl: timedelta, session_ends_at: Optional[datetime], now: Optional[datetime] = None) -> timedelta:
    """Token ka TTL session ki hard cap se aage na jaaye. Cap nikal chuki ho / bahut paas ho
    to bhi kam se kam 60s (token bekaar na ho); cap-expiry ka faisla caller (410) karta hai."""
    if session_ends_at is None:
        return ttl
    now = now or datetime.now(dt_timezone.utc)
    remaining = session_ends_at - now
    return max(min(ttl, remaining), _MIN_TOKEN_TTL)


def token_meta(ttl: timedelta, session_ends_at: Optional[datetime] = None,
               now: Optional[datetime] = None) -> dict:
    """Token-response me merge hone wala expiry metadata (additive — purani keys nahi badalti).

    `token_expires_at`   — is token ki expiry (ISO, UTC). Client isse ~10 min pehle refresh kare.
    `session_ends_at`    — hard cap (ISO) ya None; client isse `warn_before_seconds` pehle warning de.
    `server_time`        — client apna clock-skew nikalne ke liye.
    """
    now = now or datetime.now(dt_timezone.utc)
    return {
        "token_expires_at": (now + ttl).isoformat(),
        "token_ttl_seconds": int(ttl.total_seconds()),
        "session_ends_at": session_ends_at.isoformat() if session_ends_at else None,
        "warn_before_seconds": session_warn_seconds(),
        "server_time": now.isoformat(),
    }


class EgressError(RuntimeError):
    """
    Raised when a LiveKit Egress start/stop call itself fails (bad
    response, network error, room not found, etc) — deliberately
    separate from the `RuntimeError` `_get_livekit_credentials()` raises
    for missing config, so callers (views.py's `CallRecordingView`) can
    tell "recording isn't configured on this deployment" (503, config
    problem) apart from "LiveKit rejected/failed the request" (502,
    transient — worth a retry) without string-matching an error message.
    """


def _run_async(coro):
    """
    The LiveKit server SDK's Egress/Room service client is async-only
    (it's plain HTTP under the hood via httpx.AsyncClient) — unlike
    `AccessToken` above, which just signs a JWT locally and needs no
    event loop. Every caller here is a synchronous DRF view, so bridge
    with `asyncio.run()` rather than pushing async/await onto the view
    layer for what's otherwise a single blocking HTTP round-trip.
    """
    return asyncio.run(coro)


async def _start_room_composite_egress(room_name: str, output_filepath: str) -> str:
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        file_output = api.EncodedFileOutput(
            file_type=api.EncodedFileType.MP4,
            filepath=output_filepath,
        )
        # S3 upload only if a bucket is configured (see module-level
        # comment) — otherwise egress falls back to whatever local/
        # default output its own server config specifies.
        if LIVEKIT_EGRESS_S3_BUCKET:
            file_output.s3 = api.S3Upload(bucket=LIVEKIT_EGRESS_S3_BUCKET)

        req = api.RoomCompositeEgressRequest(
            room_name=room_name,
            layout="speaker",
            audio_only=False,
            file_outputs=[file_output],
        )
        info = await lkapi.egress.start_room_composite_egress(req)
        return info.egress_id
    finally:
        await lkapi.aclose()


async def _create_room(room_name: str, max_participants: int, empty_timeout: Optional[int]):
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        kwargs = {'name': room_name, 'max_participants': int(max_participants)}
        if empty_timeout:
            kwargs['empty_timeout'] = int(empty_timeout)
        return await lkapi.room.create_room(api.CreateRoomRequest(**kwargs))
    finally:
        await lkapi.aclose()


def ensure_call_room(room_name: str, max_participants: int, empty_timeout: Optional[int] = None,
                     timeout: float = 3.0) -> bool:
    """
    C11 — group call ka LiveKit room PEHLE se `max_participants` ke saath banao.

    LiveKit room tab tak auto-create hota hai jab pehla banda join kare (tab koi cap nahi
    hota), isliye group call initiate par caller ko token dene se PEHLE ye chalao.
    Best-effort: LiveKit down / slow / unconfigured ho to False (call phir bhi chalti hai,
    sirf LiveKit-level cap nahi lagta; `call_state.join_call` ka DB cap phir bhi rehta hai).
    Kabhi raise nahi karta; `timeout` sec se zyada request ko rokta nahi.
    """
    if not room_name or not max_participants:
        return False

    async def _run():
        return await asyncio.wait_for(
            _create_room(room_name, max_participants, empty_timeout), timeout=timeout,
        )

    try:
        _run_async(_run())
        return True
    except Exception:
        logger.warning("LiveKit create_room(%s, max=%s) failed (non-fatal)", room_name, max_participants,
                       exc_info=True)
        return False


async def _delete_room(room_name: str):
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        return await lkapi.room.delete_room(api.DeleteRoomRequest(room=room_name))
    finally:
        await lkapi.aclose()


def delete_room(room_name: str) -> bool:
    """Best-effort: close a LiveKit room so everyone in it is disconnected
    right now (used when a block ends an ongoing call). Never raises —
    LiveKit being down/unconfigured just means the call UI closes via the
    socket event instead. Returns True if LiveKit accepted the request."""
    if not room_name:
        return False
    try:
        _run_async(_delete_room(room_name))
        return True
    except Exception:
        logger.warning("LiveKit delete_room(%s) failed (non-fatal)", room_name, exc_info=True)
        return False


async def _remove_participant(room_name: str, identity: str):
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        return await lkapi.room.remove_participant(api.RoomParticipantIdentity(room=room_name, identity=str(identity)))
    finally:
        await lkapi.aclose()


def remove_participant(room_name: str, identity) -> bool:
    """C16 — ek participant ko LiveKit room se abhi nikaalo (recording chal rahi ho aur minor purane valid token
    se seedha room me aa jaye). Best-effort, kabhi raise nahi karta; True agar LiveKit ne request maan li."""
    if not room_name or not identity:
        return False
    try:
        _run_async(_remove_participant(room_name, identity))
        return True
    except Exception:
        logger.warning("LiveKit remove_participant(%s, %s) failed (non-fatal)", room_name, identity, exc_info=True)
        return False


async def _stop_egress(egress_id: str):
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        return await lkapi.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    finally:
        await lkapi.aclose()


def _build_recording_url(output_filepath: str) -> Optional[str]:
    """Best-effort public URL for a finished recording, from wherever
    `LIVEKIT_EGRESS_PUBLIC_BASE_URL` says finished files are served —
    e.g. a CloudFront domain in front of the S3 bucket above. Returns
    None (not a guess) if that isn't configured, rather than fabricating
    a URL that 404s."""
    if not LIVEKIT_EGRESS_PUBLIC_BASE_URL:
        return None
    return f"{LIVEKIT_EGRESS_PUBLIC_BASE_URL.rstrip('/')}/{output_filepath.lstrip('/')}"


def start_room_recording(room_name: str) -> Tuple[str, str]:
    """
    Start LiveKit server-side room-composite recording (all participants
    mixed into one file) for `room_name`, via LiveKit's Egress API.

    Returns (egress_id, output_filepath):
      - `egress_id` — LiveKit's id for this recording job. Store it
        (`CallSession.recording_egress_id`) — there's no "stop the
        recording for room X" call, only "stop egress job Y", so this
        is what `stop_room_recording()` needs later.
      - `output_filepath` — where the file will land once egress
        finishes muxing (not immediately — this call only *starts* the
        job).

    Raises `RuntimeError` if LIVEKIT_API_KEY/SECRET aren't configured
    (same lazy config-check as `generate_livekit_token`), or
    `EgressError` if LiveKit itself rejects/fails the start request.
    Callers should catch both and respond with a clean error rather than
    a raw 500.
    """
    output_filepath = f"recordings/{room_name}-{uuid.uuid4().hex}.mp4"
    try:
        egress_id = _run_async(_start_room_composite_egress(room_name, output_filepath))
    except RuntimeError:
        raise  # missing credentials — let this surface as-is
    except Exception as exc:
        logger.exception("LiveKit egress start failed for room=%s", room_name)
        raise EgressError(f"LiveKit egress start failed: {exc}") from exc
    return egress_id, output_filepath


def stop_room_recording(egress_id: str, output_filepath: Optional[str] = None) -> Optional[str]:
    """
    Stop an in-progress recording started by `start_room_recording()`.

    Returns a best-effort `recording_url` if `LIVEKIT_EGRESS_PUBLIC_BASE_URL`
    is configured and `output_filepath` was passed (the caller should
    have it saved from the start call) — otherwise returns None.

    ⚠️ This is best-effort, not authoritative: `stop_egress` returning
    successfully means LiveKit *accepted* the stop request, not that the
    file has finished uploading/muxing on the egress worker. The
    correct long-term source of truth is LiveKit's `egress_ended`
    webhook (not wired into this app yet — no webhook endpoint exists
    for it here), which reports the real final file location once
    upload actually completes. Until that's added, a `recording_url`
    returned here may occasionally 404 for a few seconds after "stop".

    Raises `RuntimeError` if LiveKit credentials aren't configured, or
    `EgressError` if the stop call itself fails.
    """
    try:
        _run_async(_stop_egress(egress_id))
    except RuntimeError:
        raise
    except Exception as exc:
        logger.exception("LiveKit egress stop failed for egress_id=%s", egress_id)
        raise EgressError(f"LiveKit egress stop failed: {exc}") from exc

    return _build_recording_url(output_filepath) if output_filepath else None


class WebhookVerificationError(Exception):
    """LiveKit webhook ka signature missing/invalid (view -> 401)."""


def verify_webhook_event(body: bytes, auth_header: str):
    """🔥 C2 — LiveKit webhook signature verify + parse (local, no network).

    Raises `RuntimeError` agar API key/secret configured nahi (view -> 503),
    `WebhookVerificationError` agar signature bad/missing (view -> 401).
    """
    api_key, api_secret = _get_livekit_credentials()
    if not auth_header:
        raise WebhookVerificationError("missing Authorization header")
    try:
        # livekit-api >= 0.7: WebhookReceiver(TokenVerifier(key, secret));
        # purane/alag SDK builds me WebhookReceiver(key, secret) — dono try.
        try:
            receiver = api.WebhookReceiver(api.TokenVerifier(api_key, api_secret))
        except TypeError:
            receiver = api.WebhookReceiver(api_key, api_secret)
        return receiver.receive(body.decode("utf-8"), auth_header)
    except Exception as exc:
        logger.warning("LiveKit webhook signature verification failed: %s", exc)
        raise WebhookVerificationError(str(exc)) from exc


# ======================================================================
# 🔥 S4 — study room: role-based token grants + RoomService moderation
# ----------------------------------------------------------------------
# Pehle `generate_livekit_token` sabko same grants deta tha (publish + data). Ab study room ke liye
# `generate_study_room_token(... role=..., share_policy=...)`:
#   host / co_host : mic + camera + screen share, data
#   participant    : mic + camera (+ screen share sirf jab share_policy == 'everyone'), data
#   viewer         : sirf subscribe (publish / data nahi)
# Server-side moderation (mute / remove / permission update / room metadata) LiveKit RoomService se.
# Sab helpers best-effort + kabhi raise nahi karte (None / False return) — view 502 deta hai.
# ======================================================================
import json as _json

ROLE_HOST = "host"
ROLE_CO_HOST = "co_host"
ROLE_PARTICIPANT = "participant"
ROLE_VIEWER = "viewer"
MANAGER_ROLES = frozenset({ROLE_HOST, ROLE_CO_HOST})

SHARE_EVERYONE = "everyone"
SHARE_HOST_ONLY = "host_only"

_SRC_CAMERA = "camera"
_SRC_MIC = "microphone"
_SRC_SCREEN = "screen_share"
_SRC_SCREEN_AUDIO = "screen_share_audio"


def study_room_grants(role: str, share_policy: str = SHARE_EVERYONE) -> dict:
    """Role + share policy -> LiveKit grant flags (pure function; token aur update_participant dono use karte hain)."""
    if role == ROLE_VIEWER:
        return {"can_publish": False, "can_publish_data": False, "can_subscribe": True, "sources": []}
    if role in MANAGER_ROLES or share_policy != SHARE_HOST_ONLY:
        sources = [_SRC_CAMERA, _SRC_MIC, _SRC_SCREEN, _SRC_SCREEN_AUDIO]
    else:
        sources = [_SRC_CAMERA, _SRC_MIC]
    return {"can_publish": True, "can_publish_data": True, "can_subscribe": True, "sources": sources}


def study_room_room_metadata(settings_dict: dict) -> str:
    return _json.dumps(settings_dict, separators=(",", ":"), sort_keys=True)


def generate_study_room_token(room_name: str, user_id, user_name: str, role: str = ROLE_PARTICIPANT,
                              share_policy: str = SHARE_EVERYONE, ttl: timedelta = timedelta(hours=8)) -> str:
    api_key, api_secret = _get_livekit_credentials()
    if not LIVEKIT_WS_URL:
        raise RuntimeError(
            "LIVEKIT_WS_URL set nahi hai (production me wss://<livekit-domain>). "
            "deploy/livekit/README.md dekho."
        )
    g = study_room_grants(role, share_policy)
    grant_kwargs = dict(
        room_join=True,
        room=room_name,
        can_publish=g["can_publish"],
        can_subscribe=g["can_subscribe"],
        can_publish_data=g["can_publish_data"],
    )
    try:
        grants = api.VideoGrants(can_publish_sources=g["sources"], **grant_kwargs)
    except TypeError:
        # Purana livekit-api jisme `can_publish_sources` nahi hai — source-level restriction nahi lagegi
        # (viewer ka can_publish=False phir bhi kaam karta hai); WS layer share_policy phir bhi enforce karta hai.
        logger.warning("livekit VideoGrants has no can_publish_sources — share_policy token-level enforce nahi hoga")
        grants = api.VideoGrants(**grant_kwargs)
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(str(user_id))
        .with_name(user_name)
        .with_metadata(_json.dumps({"role": role}))
        .with_grants(grants)
        .with_ttl(ttl)
    )
    return token.to_jwt()


async def _with_api(fn):
    api_key, api_secret = _get_livekit_credentials()
    lkapi = api.LiveKitAPI(LIVEKIT_HTTP_URL, api_key, api_secret)
    try:
        return await fn(lkapi)
    finally:
        await lkapi.aclose()


def _run_best_effort(fn, label: str, timeout: float = 4.0):
    """(ok, result). Kabhi raise nahi karta."""
    async def _go():
        return await asyncio.wait_for(_with_api(fn), timeout=timeout)
    try:
        return True, _run_async(_go())
    except Exception:
        logger.warning("LiveKit %s failed (non-fatal)", label, exc_info=True)
        return False, None


def ensure_study_room(room_name: str, max_participants: int, metadata: str = "") -> bool:
    """Study room ka LiveKit room max_participants (hard cap) + metadata ke saath pehle se banao. Best-effort."""
    async def _fn(lkapi):
        kwargs = {"name": room_name, "max_participants": int(max_participants)}
        if metadata:
            kwargs["metadata"] = metadata
        return await lkapi.room.create_room(api.CreateRoomRequest(**kwargs))
    return _run_best_effort(_fn, f"ensure_study_room({room_name})")[0]


def update_room_metadata(room_name: str, metadata: str) -> bool:
    async def _fn(lkapi):
        return await lkapi.room.update_room_metadata(api.UpdateRoomMetadataRequest(room=room_name, metadata=metadata))
    return _run_best_effort(_fn, f"update_room_metadata({room_name})")[0]


def list_room_identities(room_name: str):
    """Room me abhi connected participant identities (set) ya None (LiveKit unreachable / room nahi)."""
    async def _fn(lkapi):
        return await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
    ok, res = _run_best_effort(_fn, f"list_participants({room_name})", timeout=3.0)
    if not ok or res is None:
        return None
    return {str(p.identity) for p in res.participants}


def mute_microphones(room_name: str, identities=None, exclude=()):
    """
    `identities` (set of str) ya None (= sab) ke microphone tracks server-side mute karo (`mute_published_track`).
    Returns muted count, ya None agar LiveKit call fail hui. Participant khud dobara unmute kar sakta hai.
    """
    exclude = {str(i) for i in exclude}
    wanted = None if identities is None else {str(i) for i in identities}

    async def _fn(lkapi):
        res = await lkapi.room.list_participants(api.ListParticipantsRequest(room=room_name))
        muted = 0
        for p in res.participants:
            ident = str(p.identity)
            if ident in exclude or (wanted is not None and ident not in wanted):
                continue
            for t in p.tracks:
                is_mic = getattr(t, "source", None) == getattr(api, "TrackSource", None).MICROPHONE \
                    if hasattr(api, "TrackSource") else False
                is_audio = getattr(t, "type", None) == getattr(api, "TrackType", None).AUDIO \
                    if hasattr(api, "TrackType") else False
                if (is_mic or is_audio) and not t.muted:
                    await lkapi.room.mute_published_track(api.MuteRoomTrackRequest(
                        room=room_name, identity=ident, track_sid=t.sid, muted=True,
                    ))
                    muted += 1
        return muted

    ok, res = _run_best_effort(_fn, f"mute_microphones({room_name})")
    return res if ok else None


def update_participant_permissions(room_name: str, identity, role: str, share_policy: str = SHARE_EVERYONE) -> bool:
    """Chalte session me participant ke publish rights badlo (role change / share_policy change). Best-effort."""
    g = study_room_grants(role, share_policy)

    async def _fn(lkapi):
        ts = getattr(api, "TrackSource")
        src_map = {
            _SRC_CAMERA: ts.CAMERA, _SRC_MIC: ts.MICROPHONE,
            _SRC_SCREEN: ts.SCREEN_SHARE, _SRC_SCREEN_AUDIO: ts.SCREEN_SHARE_AUDIO,
        }
        perm = api.ParticipantPermission(
            can_subscribe=g["can_subscribe"],
            can_publish=g["can_publish"],
            can_publish_data=g["can_publish_data"],
            can_publish_sources=[src_map[s] for s in g["sources"]],
        )
        return await lkapi.room.update_participant(api.UpdateParticipantRequest(
            room=room_name, identity=str(identity), metadata=_json.dumps({"role": role}), permission=perm,
        ))
    return _run_best_effort(_fn, f"update_participant({room_name}, {identity})")[0]
