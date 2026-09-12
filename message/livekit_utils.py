# message/livekit_utils.py - FIX
import asyncio
import logging
import os
import uuid
from datetime import timedelta
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
LIVEKIT_WS_URL = os.getenv("LIVEKIT_WS_URL", "ws://10.93.221.189:7880")
LIVEKIT_HTTP_URL = os.getenv("LIVEKIT_URL") or (
    LIVEKIT_WS_URL.replace("wss://", "https://").replace("ws://", "http://")
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
def generate_livekit_token(room_name: str, user_id, user_name: str, ttl: timedelta = timedelta(hours=2)) -> str:
    api_key, api_secret = _get_livekit_credentials()
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