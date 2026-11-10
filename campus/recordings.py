# campus/recordings.py
"""
RECORDED CLASSES — a live class is filmed, then shows up as a recording students
can watch again.

    teacher goes live  -->  (teacher's 1st heartbeat inside the room)  -->  RECORDING
    class ends / teacher stops                                          -->  PROCESSING
    LiveKit `egress_ended` webhook (file uploaded)                      -->  READY (+ url)

The ONE place that owns these rules, so the HTTP layer (`recording_views.py`),
live_class.end_session, the Celery sweeper and the webhook cannot drift apart.
No DRF / Celery imports; video goes through `campus.bridge` only.

Rules (deliberate, not accidents):
  * Recording films students' faces and voices, so it is OFF per campus until an
    admin turns `Campus.class_recording_enabled` on. `class_recording_auto` then
    starts it by itself on the teacher's first heartbeat (the LiveKit room only
    exists once somebody is inside it — starting at the go-live tap would fail).
  * ONLY an ONLINE class that is LIVE right now, with a room, can be recorded.
  * ONE recording per session. Start is refused while `recording` / `processing` /
    `ready`; only a `failed` / never-started one can be (re)started.
  * The automatic start is tried ONCE per session (`recording_attempted_at`): a
    misconfigured video backend must not be hit on every heartbeat. The teacher's
    manual Start always works.
  * `egress_id` is KEPT after stop — the webhook finds the session by it.
  * Who may watch (`can_watch`): READY + not hidden for the section's students and
    their linked parents; staff of the campus always (hidden ones too). A teacher /
    manager can hide a recording from students without deleting the file.
  * The URL is handed out only to people `can_watch` allows, through `playback_url`
    — the single place to put URL signing in if the bucket is made private.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import bridge
from .audit import log_action
from .models import CampusLiveSession, ClassMode
from .visibility import get_visibility

logger = logging.getLogger(__name__)

RS = CampusLiveSession.RecordingStatus

_MESSAGES = {
    "offline_class": "An offline class has no video to record.",
    "recording_off": "Class recording is turned off for this campus.",
    "not_live": "Only a class that is live right now can be recorded.",
    "no_room": "This class has no video room yet.",
    "already_recording": "This class is already being recorded.",
    "already_recorded": "This class already has a recording.",
    "not_configured": "Class recording isn't set up on this deployment yet.",
    "start_failed": "Couldn't start the recording. Make sure you are inside the class and try again.",
    "not_recording": "No recording is in progress.",
}


class RecordingError(Exception):
    def __init__(self, code: str, message: str | None = None, status_code: int = 400):
        super().__init__(message or _MESSAGES.get(code, code))
        self.code = code
        self.message = message or _MESSAGES.get(code, code)
        self.status_code = status_code


def processing_timeout() -> timedelta:
    return timedelta(minutes=int(getattr(settings, "CAMPUS_RECORDING_PROCESSING_TIMEOUT_MINUTES", 180)))


def _campus(live: CampusLiveSession):
    return live.section.school_class.campus


# --------------------------------------------------------------------------
# start / stop
# --------------------------------------------------------------------------

def start_recording(live: CampusLiveSession, user=None, *, now=None) -> CampusLiveSession:
    """-> RECORDING. Raises `RecordingError` (code in the message map above)."""
    now = now or timezone.now()
    with transaction.atomic():
        row = (
            CampusLiveSession.objects.select_for_update()
            .select_related("section__school_class__campus")
            .get(pk=live.pk)
        )
        if row.mode != ClassMode.ONLINE:
            raise RecordingError("offline_class", status_code=400)
        if not _campus(row).class_recording_enabled:
            raise RecordingError("recording_off", status_code=403)
        if row.status != CampusLiveSession.Status.LIVE:
            raise RecordingError("not_live", status_code=409)
        if not row.room_id:
            raise RecordingError("no_room", status_code=503)
        if row.recording_status == RS.RECORDING:
            raise RecordingError("already_recording", status_code=409)
        if row.recording_status in (RS.PROCESSING, RS.READY):
            raise RecordingError("already_recorded", status_code=409)
        # Claim BEFORE calling out, so two taps / two devices can never start two egresses.
        row.recording_status = RS.RECORDING
        row.recording_started_at = now
        row.recording_stopped_at = None
        row.recording_ready_at = None
        row.recording_attempted_at = now
        row.recording_egress_id = ""
        row.recording_url = ""
        row.recording_duration_seconds = None
        row.recording_error = ""
        row.save(update_fields=[
            "recording_status", "recording_started_at", "recording_stopped_at", "recording_ready_at",
            "recording_attempted_at", "recording_egress_id", "recording_url",
            "recording_duration_seconds", "recording_error",
        ])
        room_id = row.room_id
        campus_id = _campus(row).id

    try:
        egress_id = bridge.start_room_recording(room_id)
    except bridge.RecordingBackendError as exc:
        CampusLiveSession.objects.filter(pk=row.pk, recording_status=RS.RECORDING, recording_egress_id="").update(
            recording_status=RS.FAILED, recording_error=(exc.code or "start_failed")[:40], recording_started_at=None
        )
        raise RecordingError(exc.code if exc.code in _MESSAGES else "start_failed", status_code=503) from exc

    CampusLiveSession.objects.filter(pk=row.pk).update(recording_egress_id=str(egress_id)[:100])
    if user is not None:
        log_action(campus_id, user, "class_recording.start", target=row, summary=f"recording started: {row.pk}")
    row.refresh_from_db()
    return row


def maybe_auto_start(live: CampusLiveSession, user) -> bool:
    """Called from the HOST's heartbeat. Cheap on the common path (two field checks
    on the already-loaded row); never raises. True when a recording was started."""
    try:
        if live.recording_status != RS.NONE or live.recording_attempted_at is not None:
            return False
        if live.mode != ClassMode.ONLINE or live.status != CampusLiveSession.Status.LIVE:
            return False
        campus = _campus(live)
        if not (campus.class_recording_enabled and campus.class_recording_auto):
            return False
        start_recording(live, user)
        return True
    except RecordingError as exc:
        logger.warning("Auto recording not started for live session %s: %s", live.pk, exc.code)
        return False
    except Exception:
        logger.exception("Auto recording crashed for live session %s.", live.pk)
        return False


def stop_recording(live: CampusLiveSession, user=None, *, now=None, strict: bool = False):
    """RECORDING -> PROCESSING. Idempotent: a session that is not recording returns
    `(live, False)` (or raises `not_recording` when `strict`, for the manual Stop).
    A stop the video service refuses is logged and left to LiveKit (the egress ends
    when the room empties and the webhook still finishes the recording)."""
    now = now or timezone.now()
    with transaction.atomic():
        row = CampusLiveSession.objects.select_for_update().select_related("section__school_class").get(pk=live.pk)
        if row.recording_status != RS.RECORDING:
            if strict:
                raise RecordingError("not_recording", status_code=409)
            return row, False
        egress_id = row.recording_egress_id
        if egress_id:
            row.recording_status = RS.PROCESSING
        else:
            # never got an egress id (crash between claim and start): nothing to wait for
            row.recording_status = RS.FAILED
            row.recording_error = "start_failed"
        row.recording_stopped_at = now
        row.save(update_fields=["recording_status", "recording_error", "recording_stopped_at"])
        campus_id = row.section.school_class.campus_id
    if egress_id:
        try:
            bridge.stop_room_recording(egress_id)
        except bridge.RecordingBackendError:
            logger.warning("Stop refused for egress %s of live session %s; waiting for LiveKit.", egress_id, row.pk)
    if user is not None:
        log_action(campus_id, user, "class_recording.stop", target=row, summary=f"recording stopped: {row.pk}")
    row.refresh_from_db()
    return row, True


# --------------------------------------------------------------------------
# webhook
# --------------------------------------------------------------------------

def finish_recording(egress_id: str, *, url: str = "", duration_seconds=None, now=None) -> bool:
    """LiveKit says the egress ended. True when this egress belongs to a campus
    session (claimed), False when it is none of ours. Idempotent."""
    if not egress_id:
        return False
    now = now or timezone.now()
    became_ready_id = None
    with transaction.atomic():
        row = CampusLiveSession.objects.select_for_update().filter(recording_egress_id=egress_id).first()
        if row is None:
            return False
        if row.recording_status == RS.READY:
            return True
        row.recording_stopped_at = row.recording_stopped_at or now
        if url:
            row.recording_status = RS.READY
            row.recording_url = url[:600]
            row.recording_ready_at = now
            row.recording_error = ""
            if duration_seconds:
                row.recording_duration_seconds = int(duration_seconds)
            elif row.recording_started_at:
                row.recording_duration_seconds = max(
                    0, int((row.recording_stopped_at - row.recording_started_at).total_seconds())
                )
        else:
            row.recording_status = RS.FAILED
            row.recording_error = "no_file"
        row.save(update_fields=[
            "recording_status", "recording_url", "recording_ready_at", "recording_error",
            "recording_duration_seconds", "recording_stopped_at",
        ])
        if row.recording_status == RS.READY and not row.recording_hidden:
            became_ready_id = row.pk
    if became_ready_id is not None:
        announce_ready(became_ready_id)
    return True


def announce_ready(live_id) -> None:
    """Queue the "recording is ready" push (a worker sends it, once — the task claims
    `recording_notified_at`). Lazy imports: this module stays free of Celery at import
    time. Never raises — a push problem must not fail the webhook."""
    try:
        from core.async_utils import dispatch_after_commit

        from . import tasks as campus_tasks

        dispatch_after_commit(campus_tasks.send_recording_ready, str(live_id))
    except Exception:
        logger.exception("Could not queue the recording-ready push for live session %s.", live_id)


# --------------------------------------------------------------------------
# who may watch / what to show
# --------------------------------------------------------------------------

def can_manage_recording(user, live: CampusLiveSession) -> bool:
    """Start / stop / hide: exactly who may run the session (teacher, substitute,
    starter, class teacher, admin ...). Lazy import — live_class imports this module."""
    from .live_class import can_run_session

    return bool(can_run_session(user, live))


def can_watch(user, live: CampusLiveSession, request=None) -> bool:
    if live.recording_status != RS.READY or not live.recording_url:
        return False
    vis = get_visibility(user, request)
    campus_id = live.section.school_class.campus_id
    if campus_id in vis.staff_campus_ids:
        return True
    return (not live.recording_hidden) and live.section_id in vis.visible_section_ids


def playback_url(live: CampusLiveSession) -> str:
    """The playable address. Returned as stored today; if the recordings bucket is
    made private, sign it HERE (nothing else in the app reads `recording_url`)."""
    return live.recording_url


# --------------------------------------------------------------------------
# sweeper (called from live_class.sweep_live_sessions, every minute)
# --------------------------------------------------------------------------

def sweep_recordings(now=None) -> dict:
    now = now or timezone.now()
    stopped = timed_out = 0
    # a recording still "on" although its class is over (stop call lost / app died)
    leftover = CampusLiveSession.objects.filter(recording_status=RS.RECORDING).exclude(
        status=CampusLiveSession.Status.LIVE
    )
    for live in leftover:
        try:
            _, changed = stop_recording(live, now=now)
            stopped += int(changed)
        except Exception:
            logger.exception("Could not stop leftover recording for live session %s.", live.pk)
    # processing for too long: the webhook never came
    stuck = CampusLiveSession.objects.filter(
        recording_status=RS.PROCESSING, recording_stopped_at__lt=now - processing_timeout()
    )
    for live in stuck:
        updated = CampusLiveSession.objects.filter(pk=live.pk, recording_status=RS.PROCESSING).update(
            recording_status=RS.FAILED, recording_error="timeout"
        )
        timed_out += int(bool(updated))
    return {"recordings_stopped": stopped, "recordings_timed_out": timed_out}
