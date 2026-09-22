# testseries/live.py
"""
LiveKit glue for **live-video tests** and **proctored attempts**.

Why a separate module (and not `liveclass.livekit_utils`)?
    Golden rule of this codebase: `testseries` never imports `liveclass`
    (or `campus`). We therefore talk to the SAME LiveKit project directly
    through the official SDK, using the SAME environment variables and the
    SAME call shapes `liveclass/livekit_utils.py` already uses in
    production — nothing new to provision:

        LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_WS_URL (or LIVEKIT_URL)
        LIVEKIT_EGRESS_S3_BUCKET / _REGION / _ACCESS_KEY / _SECRET [/ _ENDPOINT]

Two room kinds
    ts-live-<series_id>      the host's live room. Creator publishes; every
                             student joins as a SUBSCRIBE-ONLY viewer while
                             they take the test. Recorded if series.record_live.
    ts-proctor-<attempt_id>  one candidate's camera room. The student
                             publishes; nobody else can be seen by them.
                             Always recorded (that is the point of proctoring).

Recording lifecycle
    start_recording() -> egress_id   (kept on `TestRecording`)
    stop_recording(egress_id)
    LiveKit uploads the MP4 to S3 and calls our webhook
    (`POST /testseries/livekit-webhook/`), which fills `TestRecording.url`.
    Point your LiveKit project's webhook at that URL *in addition to* the
    existing liveclass one (LiveKit supports several webhook URLs).

Everything is validated lazily — an unconfigured LiveKit never breaks
`manage.py check` / `migrate` / `test`, exactly like `liveclass`.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import timedelta
from typing import Optional

from asgiref.sync import async_to_sync
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_TTL = timedelta(hours=4)
EMPTY_ROOM_TIMEOUT_SECS = 10 * 60


class TestLiveError(APIException):
    """Predictable failure for the view layer (LiveKit down / not configured)."""

    status_code = 503
    default_detail = "Live video is temporarily unavailable. Please try again in a few minutes."
    default_code = "live_unavailable"

    # Not a test case, despite the name — keeps pytest / unittest collectors quiet.
    __test__ = False


class Role:
    HOST = "host"            # publish + room admin + start/stop recording
    VIEWER = "viewer"        # subscribe only (students watching the host, creator watching a candidate)
    CANDIDATE = "candidate"  # publish only (proctored student's camera + mic)


def _env(name: str) -> Optional[str]:
    return os.getenv(name)


def _credentials() -> tuple[str, str, str]:
    key, secret = _env("LIVEKIT_API_KEY"), _env("LIVEKIT_API_SECRET")
    url = _env("LIVEKIT_WS_URL") or _env("LIVEKIT_URL")
    if not (key and secret and url):
        logger.error("LiveKit misconfigured — need LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_WS_URL.")
        raise TestLiveError()
    return key, secret, url


def _egress_s3():
    bucket = _env("LIVEKIT_EGRESS_S3_BUCKET")
    region = _env("LIVEKIT_EGRESS_S3_REGION")
    access = _env("LIVEKIT_EGRESS_S3_ACCESS_KEY")
    secret = _env("LIVEKIT_EGRESS_S3_SECRET")
    if not (bucket and region and access and secret):
        logger.error("LiveKit recording misconfigured — need the LIVEKIT_EGRESS_S3_* variables.")
        raise TestLiveError("Recording is not set up on this server yet.")
    return bucket, region, access, secret, _env("LIVEKIT_EGRESS_S3_ENDPOINT") or None


def live_room_name(series_id) -> str:
    return f"ts-live-{series_id}"


def proctor_room_name(attempt_id) -> str:
    return f"ts-proctor-{attempt_id}"


def livekit_url() -> str:
    """The wss:// URL the mobile client connects to (returned with the token)."""
    return _credentials()[2]


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------
def _grants(role: str):
    from livekit import api  # lazy: SDK is only needed once video is actually used

    if role == Role.HOST:
        return api.VideoGrants(
            room_join=True, can_publish=True, can_subscribe=True, can_publish_data=True,
            can_update_own_metadata=True, room_admin=True, room_record=True,
        )
    if role == Role.CANDIDATE:
        # Sends camera/mic; receives nothing; cannot chat; cannot see who else is in.
        return api.VideoGrants(
            room_join=True, can_publish=True, can_subscribe=False, can_publish_data=False,
            can_update_own_metadata=False, room_admin=False, room_record=False,
        )
    # VIEWER: watches only. hidden => not shown as a tile to the others.
    return api.VideoGrants(
        room_join=True, can_publish=False, can_subscribe=True, can_publish_data=False,
        can_update_own_metadata=False, room_admin=False, room_record=False, hidden=True,
    )


def issue_token(
    *, room_name: str, user_id, user_name: str, role: str, ttl: timedelta = DEFAULT_TOKEN_TTL
) -> str:
    """Signed JWT the client uses to join `room_name`. A student's token can
    never publish into the host room or moderate it, even if inspected."""
    from livekit import api

    key, secret, _ = _credentials()
    grants = _grants(role)
    grants.room = room_name
    return (
        api.AccessToken(key, secret)
        .with_identity(str(user_id))
        .with_name(user_name)
        .with_grants(grants)
        .with_ttl(ttl)
        .to_jwt()
    )


# ---------------------------------------------------------------------------
# Rooms + recording (LiveKit server API is async; views are sync)
# ---------------------------------------------------------------------------
async def _client():
    from livekit import api

    key, secret, url = _credentials()
    return api.LiveKitAPI(url, key, secret)


async def _ensure_room_async(room_name: str, max_participants: int = 0) -> None:
    from livekit import api

    lk = await _client()
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name, empty_timeout=EMPTY_ROOM_TIMEOUT_SECS, max_participants=max_participants
            )
        )
    except Exception as exc:  # already-exists is fine; anything else is surfaced
        if "already exists" in str(exc).lower():
            return
        logger.exception("LiveKit: create_room failed for %s", room_name)
        raise TestLiveError("Couldn't start the live room. Please try again.") from exc
    finally:
        await lk.aclose()


async def _end_room_async(room_name: str) -> None:
    from livekit import api

    lk = await _client()
    try:
        await lk.room.delete_room(api.DeleteRoomRequest(room=room_name))
    except Exception:
        # Ending an already-closed room must not fail the request.
        logger.warning("LiveKit: delete_room failed/ignored for %s", room_name, exc_info=True)
    finally:
        await lk.aclose()


async def _start_recording_async(room_name: str) -> str:
    from livekit import api

    bucket, region, access, secret, endpoint = _egress_s3()
    lk = await _client()
    try:
        output = api.EncodedFileOutput(
            file_type=api.EncodedFileType.MP4,
            filepath=f"testseries-recordings/{room_name}/{uuid.uuid4()}.mp4",
            s3=api.S3Upload(bucket=bucket, region=region, access_key=access, secret=secret, endpoint=endpoint),
        )
        res = await lk.egress.start_room_composite_egress(
            api.RoomCompositeEgressRequest(room_name=room_name, layout="grid", file_outputs=[output])
        )
        return res.egress_id
    except TestLiveError:
        raise
    except Exception as exc:
        logger.exception("LiveKit: start recording failed for %s", room_name)
        raise TestLiveError("Couldn't start recording. Please try again.") from exc
    finally:
        await lk.aclose()


async def _stop_recording_async(egress_id: str) -> None:
    from livekit import api

    lk = await _client()
    try:
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
    except Exception as exc:
        logger.exception("LiveKit: stop recording failed for egress %s", egress_id)
        raise TestLiveError("Couldn't stop the recording right now. Please try again.") from exc
    finally:
        await lk.aclose()


ensure_room = async_to_sync(_ensure_room_async)
end_room = async_to_sync(_end_room_async)
start_recording = async_to_sync(_start_recording_async)
stop_recording = async_to_sync(_stop_recording_async)


def verify_webhook(body: bytes, auth_header: str):
    """Signature-verified LiveKit webhook event (raises `TestLiveError` 401)."""
    from livekit import api

    key, secret, _ = _credentials()
    try:
        return api.WebhookReceiver(key, secret).receive(body.decode("utf-8"), auth_header)
    except Exception as exc:
        logger.warning("LiveKit webhook signature verification failed: %s", exc)
        err = TestLiveError("Invalid webhook signature.")
        err.status_code = 401
        raise err from exc
