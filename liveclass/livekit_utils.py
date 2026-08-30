# liveclass/livekit_utils.py
"""
LiveKit integration for the `liveclass` app.

Responsibilities:
    1. Token issuance — role-aware JWTs clients use to connect to a room.
    2. Room lifecycle — create/end a room, list/remove participants, all via
       LiveKit's server-side Room Service.

Design notes:
    - Credentials come from environment variables only (never hardcoded),
      same convention as `message/livekit_utils.py`.
    - Grants are role-based: HOST / CO_HOST get moderation power (mute/kick,
      recording control); STUDENT gets standard publish/subscribe rights.
      This keeps a student's token from ever being able to remove the
      teacher or another student, even if the token were inspected client-side.
    - LiveKit's Python server SDK (`livekit.api.LiveKitAPI`) is async under
      the hood (httpx). Django views in this project are sync, so each
      room-service call is wrapped with `async_to_sync` (ships with Django
      via asgiref) — callers below just do `ensure_room(...)`, no asyncio
      boilerplate needed at the call site.
    - Every room-service call is wrapped so LiveKit-side failures surface as
      one predictable `LiveKitError`, instead of leaking httpx/twirp
      exceptions into the view layer.

NOTE ON SDK METHOD NAMES:
    `create_room` / `delete_room` / `list_participants` / `remove_participant`
    request/response class names match `livekit-server-sdk-python` as of the
    versions this was written against. Pin/check your installed
    `livekit-api` version — request classes have moved between packages in
    past SDK versions (`livekit.api` vs `livekit.protocol.room`).
    `get_participant` / `mute_published_track` / `MuteRoomTrackRequest` /
    `TrackSource` (used by `set_participant_audio_muted` below) come from
    the same package family — check the same CHANGELOG if those get
    renamed in a future SDK bump.
"""

import logging
import os
import uuid
from datetime import timedelta
from typing import Optional

from asgiref.sync import async_to_sync
from livekit import api
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Credentials — .env / environment variables se aayenge, hardcoded kabhi nahi.
# ---------------------------------------------------------------------------
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
# Project's .env uses LIVEKIT_WS_URL — LIVEKIT_URL kept as a fallback alias in
# case some other environment (or the `message` app) sets it under that name.
LIVEKIT_URL = os.getenv("LIVEKIT_WS_URL") or os.getenv("LIVEKIT_URL")

# FEATURE (recording): egress uploads straight to S3 (or an S3-compatible
# endpoint — MinIO, DigitalOcean Spaces, Cloudflare R2, etc, via
# LIVEKIT_EGRESS_S3_ENDPOINT) rather than LiveKit's local disk, since a
# local file doesn't survive a container restart/redeploy and doesn't give
# students a stable recording_url without standing up your own file server
# on top of it. Only required once a recording is actually started — see
# `_check_egress_credentials()` below — so an unconfigured bucket doesn't
# block join/mute/kick/etc, which have nothing to do with recording.
LIVEKIT_EGRESS_S3_BUCKET = os.getenv("LIVEKIT_EGRESS_S3_BUCKET")
LIVEKIT_EGRESS_S3_REGION = os.getenv("LIVEKIT_EGRESS_S3_REGION")
LIVEKIT_EGRESS_S3_ACCESS_KEY = os.getenv("LIVEKIT_EGRESS_S3_ACCESS_KEY")
LIVEKIT_EGRESS_S3_SECRET = os.getenv("LIVEKIT_EGRESS_S3_SECRET")
# Optional — leave unset for real AWS S3; set for S3-compatible providers.
LIVEKIT_EGRESS_S3_ENDPOINT = os.getenv("LIVEKIT_EGRESS_S3_ENDPOINT")


def _check_credentials() -> None:
    """Validated lazily, on first actual LiveKit call, NOT at import time.

    NOTE (fix): this used to raise RuntimeError at module import. Since
    views.py imports this module, and urls.py imports views.py, that meant
    ANY Django management command that touches the URL resolver — `check`,
    `migrate`, `test`, `createsuperuser`, `collectstatic`, `shell`, even
    `runserver` — crashed the entire process the moment LIVEKIT_* env vars
    were unset, even for commands that never touch LiveKit at all. That's a
    hard outage for local dev, CI, and any deploy step that runs before
    secrets are provisioned. Failing fast is still the right instinct, but
    it should fail only when a LiveKit call is actually attempted.
    """
    missing = [
        name
        for name, value in (
            ("LIVEKIT_API_KEY", LIVEKIT_API_KEY),
            ("LIVEKIT_API_SECRET", LIVEKIT_API_SECRET),
            ("LIVEKIT_WS_URL / LIVEKIT_URL", LIVEKIT_URL),
        )
        if not value
    ]
    if missing:
        # NOTE (fix — internal detail was leaking to end users): views.py's
        # join()/token()/kick() actions all return this exception's message
        # verbatim as the API response's "detail" field
        # (Response({"detail": str(exc)}, status=503)) — i.e. straight to
        # whatever student/teacher happened to trigger the call. The
        # previous message was Hinglish internal-ops phrasing that named
        # the exact missing .env variable names — fine for a server log,
        # not something an end user should see when a class fails to load.
        # Full detail still goes to the server log; the client only gets a
        # generic, professional message.
        logger.error("LiveKit misconfigured — missing: %s", ", ".join(missing))
        raise LiveKitError(
            "The live class service is temporarily unavailable. Please try again in a few minutes."
        )

DEFAULT_TOKEN_TTL = timedelta(hours=4)          # live class lambi chal sakti hai
DEFAULT_EMPTY_ROOM_TIMEOUT_SECS = 10 * 60       # sab chale jayein to 10 min me room auto-close


def _check_egress_credentials() -> None:
    """Same lazy-validation reasoning as `_check_credentials()` above — an
    unconfigured recording backend shouldn't take down join/mute/kick/etc,
    so this is only checked from the two recording functions below, not at
    import time or from `_check_credentials()`.
    """
    missing = [
        name
        for name, value in (
            ("LIVEKIT_EGRESS_S3_BUCKET", LIVEKIT_EGRESS_S3_BUCKET),
            ("LIVEKIT_EGRESS_S3_REGION", LIVEKIT_EGRESS_S3_REGION),
            ("LIVEKIT_EGRESS_S3_ACCESS_KEY", LIVEKIT_EGRESS_S3_ACCESS_KEY),
            ("LIVEKIT_EGRESS_S3_SECRET", LIVEKIT_EGRESS_S3_SECRET),
        )
        if not value
    ]
    if missing:
        logger.error("LiveKit recording misconfigured — missing: %s", ", ".join(missing))
        raise LiveKitError("Recording storage isn't configured yet. Please contact support.")


class LiveKitError(APIException):
    """Room-service call fail hua — caller ko clean, standardised error
    dikhane ke liye.

    NOTE (fix — was a bare Exception, error envelope was inconsistent):
    every raise site below used to get caught in views.py with
    `except LiveKitError as exc: return Response({"detail": str(exc)},
    status=503)` — a manually-built Response that never passed through
    `liveclass_exception_handler` (exceptions.py), since that handler only
    runs for RAISED exceptions DRF recognises, not Responses views return
    directly. That meant every LiveKit failure (join/mute/kick/recording/
    breakout/webhook) came back missing the "code" field the rest of the
    API promises on every error. Subclassing DRF's own APIException fixes
    that in one place: callers can now just `raise LiveKitError(...)` (or
    let it propagate) and DRF's dispatch machinery + our custom handler
    take care of turning it into the standard {"detail", "code"} envelope
    — no manual Response building at any call site.

    Defaults to 503 (Service Unavailable) — the right code for "the
    LiveKit call itself failed" (misconfiguration, network/API error).
    Pass `status_code=` for call sites where a different status is
    correct in a way a caller might reasonably need to distinguish from a
    generic 503 — right now, only `verify_webhook_event()`'s bad
    signature does this (401, this is an auth failure, not an
    unavailability).
    """

    status_code = 503
    default_detail = "The live class service is temporarily unavailable. Please try again."
    default_code = "livekit_error"

    def __init__(self, detail=None, status_code=None):
        super().__init__(detail=detail)
        if status_code is not None:
            # Instance attribute shadows the class attribute — DRF's
            # exception_handler reads `exc.status_code` off the instance,
            # so this is all that's needed to vary status per raise site.
            self.status_code = status_code


class ParticipantRole:
    HOST = "host"
    CO_HOST = "co_host"
    STUDENT = "student"


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------
def _grants_for_role(role: str) -> api.VideoGrants:
    """Role → permission matrix. Host/co-host = moderation power; student = nahi."""
    is_host = role in (ParticipantRole.HOST, ParticipantRole.CO_HOST)
    return api.VideoGrants(
        room_join=True,
        can_publish=True,
        can_subscribe=True,
        can_publish_data=True,
        can_update_own_metadata=True,
        room_admin=is_host,    # mute/remove others, room control
        room_record=is_host,   # start/stop recording
    )


def generate_livekit_token(
    room_name: str,
    user_id,
    user_name: str,
    role: str = ParticipantRole.STUDENT,
    ttl: timedelta = DEFAULT_TOKEN_TTL,
    metadata: Optional[str] = None,
) -> str:
    """Signed JWT return karta hai jisse client `room_name` join karega."""
    _check_credentials()
    grants = _grants_for_role(role)
    grants.room = room_name

    token = (
        api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(str(user_id))
        .with_name(user_name)
        .with_grants(grants)
        .with_ttl(ttl)
    )
    if metadata:
        token = token.with_metadata(metadata)

    return token.to_jwt()





# ---------------------------------------------------------------------------
# Room lifecycle (server-side room service — async client, sync wrappers)
# ---------------------------------------------------------------------------
def _client() -> api.LiveKitAPI:
    _check_credentials()
    return api.LiveKitAPI(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)


async def _ensure_room_async(
    room_name: str,
    max_participants: int = 0,
    empty_timeout: int = DEFAULT_EMPTY_ROOM_TIMEOUT_SECS,
    metadata: Optional[str] = None,
) -> None:
    """Room create karo agar exist nahi karta — idempotent (already-exists is fine)."""
    lk = _client()
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=empty_timeout,
                max_participants=max_participants,
                metadata=metadata or "",
            )
        )
    except Exception as exc:
        logger.exception("LiveKit: room create failed for %s", room_name)
        raise LiveKitError("Couldn't start the live class room. Please try again.") from exc
    finally:
        await lk.aclose()


async def _end_room_async(room_name: str) -> None:
    """Room forcibly band karo — sabhi connected participants disconnect ho jayenge."""
    lk = _client()
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception as exc:
        logger.exception("LiveKit: room delete failed for %s", room_name)
        raise LiveKitError("Couldn't close the live class room. Please try again.") from exc
    finally:
        await lk.aclose()


async def _remove_participant_async(room_name: str, identity: str) -> None:
    """Ek specific participant ko room se nikaalo (host action, e.g. disruptive student)."""
    lk = _client()
    try:
        await lk.room.remove_participant(
            api.RoomParticipantIdentity(room=room_name, identity=identity)
        )
    except Exception as exc:
        logger.exception("LiveKit: remove participant %s from %s failed", identity, room_name)
        raise LiveKitError("Couldn't remove this participant right now. Please try again.") from exc
    finally:
        await lk.aclose()


async def _set_participant_audio_muted_async(room_name: str, identity: str, muted: bool = True) -> None:
    """Force-mute (muted=True) or release a force-mute (muted=False) on a
    participant's currently published microphone track — WITHOUT removing
    them from the room (see `_remove_participant_async` above for that).

    LiveKit's mute API is track-scoped, not participant-scoped, so there's
    no single "mute this whole person" call — this looks up the identity's
    live `ParticipantInfo` first to find their microphone track's SID, then
    mutes/unmutes that specific track. Both calls share one `LiveKitAPI`
    client/connection rather than opening two (one per `_client()` call
    elsewhere in this file), since they always happen back-to-back here.

    No currently-published microphone track (camera-only join, or mic
    already off) is treated as a no-op rather than an error — muting
    someone who isn't sending audio anyway is a harmless host action, not
    a failure worth surfacing as a 503 to the teacher.
    """
    lk = _client()
    try:
        participant = await lk.room.get_participant(
            api.RoomParticipantIdentity(room=room_name, identity=identity)
        )
        mic_track = next(
            (t for t in participant.tracks if t.source == api.TrackSource.SOURCE_MICROPHONE),
            None,
        )
        if mic_track is None:
            return
        await lk.room.mute_published_track(
            api.MuteRoomTrackRequest(
                room=room_name,
                identity=identity,
                track_sid=mic_track.sid,
                muted=muted,
            )
        )
    except Exception as exc:
        logger.exception(
            "LiveKit: mute(%s) participant %s in %s failed", muted, identity, room_name
        )
        raise LiveKitError(
            "Couldn't update this participant's microphone right now. Please try again."
        ) from exc
    finally:
        await lk.aclose()


async def _list_participants_async(room_name: str):
    lk = _client()
    try:
        res = await lk.room.list_participants(api.ListParticipantsRequest(room=room_name))
        return res.participants
    except Exception as exc:
        logger.exception("LiveKit: list participants failed for %s", room_name)
        raise LiveKitError("Couldn't load the participant list right now. Please try again.") from exc
    finally:
        await lk.aclose()


# ---------------------------------------------------------------------------
# Recording (LiveKit Egress) — gap fix: Classroom.recording_enabled and
# ClassSession.recording_url already existed as a setting + a place to
# store the final link, but nothing anywhere ever called LiveKit's Egress
# API, so recording_url could never actually get populated. These two
# functions are that missing engine call; the webhook verifier below is
# how the actual finished-file URL gets back to us once LiveKit uploads it
# (see LiveKitWebhookView in views.py).
# ---------------------------------------------------------------------------
async def _start_room_recording_async(room_name: str) -> str:
    """Starts a LiveKit Room Composite Egress (records the room's default
    grid layout to a single MP4, uploaded to S3) and returns the egress_id
    the caller must hold onto — to stop it later via
    `_stop_room_recording_async`, and to match the eventual `egress_ended`
    webhook back to the right ClassSession.
    """
    _check_egress_credentials()
    lk = _client()
    try:
        file_output = api.EncodedFileOutput(
            file_type=api.EncodedFileType.MP4,
            filepath=f"recordings/{room_name}/{uuid.uuid4()}.mp4",
            s3=api.S3Upload(
                bucket=LIVEKIT_EGRESS_S3_BUCKET,
                region=LIVEKIT_EGRESS_S3_REGION,
                access_key=LIVEKIT_EGRESS_S3_ACCESS_KEY,
                secret=LIVEKIT_EGRESS_S3_SECRET,
                endpoint=LIVEKIT_EGRESS_S3_ENDPOINT or None,
            ),
        )
        res = await lk.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(
                room_name=room_name,
                layout="grid",
                file_outputs=[file_output],
            )
        )
        return res.egress_id
    except Exception as exc:
        logger.exception("LiveKit: start recording failed for %s", room_name)
        raise LiveKitError("Couldn't start recording this class. Please try again.") from exc
    finally:
        await lk.aclose()


async def _stop_room_recording_async(egress_id: str) -> None:
    """Stops an in-progress egress job. This only guarantees the recording
    STOPS — the finished file still uploads asynchronously; its final
    location comes back later via the `egress_ended` webhook, not from
    this call's response.
    """
    lk = _client()
    try:
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    except Exception as exc:
        logger.exception("LiveKit: stop recording failed for egress %s", egress_id)
        raise LiveKitError("Couldn't stop the recording right now. Please try again.") from exc
    finally:
        await lk.aclose()


def verify_webhook_event(body: bytes, auth_header: str) -> api.WebhookEvent:
    """Verifies an incoming LiveKit webhook's signature and parses it into
    a `WebhookEvent`. Raises `LiveKitError` (mapped to a 401 by
    `LiveKitWebhookView`) on a missing/invalid signature — this signature
    check is the ONLY thing standing between "LiveKit told us a recording
    finished" and "anyone who found the URL told us a recording finished",
    since that view intentionally skips normal Django user auth (LiveKit's
    server has no Django session/token to send). Sync, not async — this is
    pure local signature verification, no network call, so it doesn't need
    an `async_to_sync` wrapper like the room-service functions above.
    """
    _check_credentials()
    try:
        receiver = api.WebhookReceiver(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        return receiver.receive(body.decode("utf-8"), auth_header)
    except Exception as exc:
        logger.warning("LiveKit webhook signature verification failed: %s", exc)
        raise LiveKitError("Invalid webhook signature.", status_code=401) from exc


# Views yeh sync versions call karenge — asyncio ka jhanjhat nahi.
ensure_room = async_to_sync(_ensure_room_async)
end_room = async_to_sync(_end_room_async)
remove_participant = async_to_sync(_remove_participant_async)
set_participant_audio_muted = async_to_sync(_set_participant_audio_muted_async)
list_participants = async_to_sync(_list_participants_async)
start_room_recording = async_to_sync(_start_room_recording_async)
stop_room_recording = async_to_sync(_stop_room_recording_async)