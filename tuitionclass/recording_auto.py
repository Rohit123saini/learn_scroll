"""
tuitionclass/recording_auto.py  (X12 - auto recording)

Starts the LiveKit egress recording by itself for classrooms that opted in (Classroom.recording_auto), and keeps the
bookkeeping the rest of the recording features lean on (recording_started_at, the "Class started" chapter).

Triggers (called from LiveKitWebhookView):
  room_started         the room was just created (first join -> ensure_room)
  participant_joined   someone joined; this is the second chance for the case "a student opened the room before the
                       teacher did" - the recording only starts once the HOST is in, so the video does not open with
                       minutes of an empty room

Rules (all enforced under a row lock, so two webhooks racing can never start two egress jobs):
  * Classroom.recording_enabled AND Classroom.recording_auto both on - otherwise nothing happens;
  * the session is LIVE and a HOST participant is currently in the room;
  * `auto_record_attempted_at` is stamped on the FIRST attempt (success or failure) - a retry of the same webhook, every
    later join, or a teacher who stopped the recording by hand never triggers a second automatic recording;
  * a session that already has an egress (teacher pressed Record first), or already had one that was stopped, is left alone.

The function never raises (a webhook must always answer 200) and returns a short status string for logs / tests.
Webhook events must be enabled on the LiveKit project (room_started, participant_joined, egress_started, egress_ended -
LiveKit sends all of them by default).
"""
import logging
import uuid

from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


def _session_for_room(room_name):
    from .models import ClassSession

    try:
        room_uuid = uuid.UUID(str(room_name))
    except (ValueError, AttributeError, TypeError):
        return None
    return ClassSession.objects.filter(room_id=room_uuid).first()


def should_auto_record(session, host_present):
    """Pure rule check -> (ok, reason). `session` needs .classroom, .status, .egress_id, .auto_record_attempted_at."""
    from .models import ClassSession

    classroom = session.classroom
    if not (classroom.recording_enabled and classroom.recording_auto):
        return False, "off"
    if session.status != ClassSession.Status.LIVE:
        return False, "not-live"
    if session.egress_id:
        return False, "already-recording"
    if getattr(session, "recording_started_at", None) is not None:
        return False, "already-recorded"     # the teacher recorded (and stopped) by hand earlier - leave it alone
    if session.auto_record_attempted_at is not None:
        return False, "already-attempted"
    if not host_present:
        return False, "waiting-for-host"
    return True, "ok"


def maybe_auto_start(room_name, trigger=""):
    """Start the recording of the session whose LiveKit room is `room_name` when the rules above allow. Never raises."""
    try:
        return _maybe_auto_start(room_name, trigger)
    except Exception:
        logger.exception("auto-record: unexpected failure (room=%s trigger=%s)", room_name, trigger)
        return "error"


def _maybe_auto_start(room_name, trigger):
    from .livekit_utils import LiveKitError, start_room_recording, stop_room_recording
    from .models import ClassSession, SessionParticipant
    from .realtime import broadcast_to_session
    from .recording_tools import reset_timeline, seed_start_chapter

    probe = _session_for_room(room_name)
    if probe is None:
        return "unknown-room"          # not a tuition session (a campus room, a call) - not ours to record
    if (not probe.classroom.recording_auto or probe.auto_record_attempted_at is not None or probe.egress_id
            or probe.recording_started_at is not None):
        return "skip"                  # cheap exit: every student join lands here after the first attempt

    with transaction.atomic():
        session = ClassSession.objects.select_for_update().select_related("classroom").get(pk=probe.pk)
        host_present = session.participants.filter(
            role=SessionParticipant.Role.HOST, left_at__isnull=True, kicked_at__isnull=True,
        ).exists()
        ok, reason = should_auto_record(session, host_present)
        if not ok:
            return reason
        session.auto_record_attempted_at = timezone.now()
        session.save(update_fields=["auto_record_attempted_at"])

    # The LiveKit call stays OUTSIDE the lock (no DB row held open across a network call).
    try:
        egress_id = start_room_recording(str(session.room_id))
    except LiveKitError:
        logger.warning("auto-record: egress start failed for session %s (trigger=%s)", session.id, trigger)
        return "failed"

    now = timezone.now()
    claimed = ClassSession.objects.filter(pk=session.pk, egress_id="").update(
        egress_id=egress_id, last_egress_id=egress_id, recording_started_at=now, recording_duration_seconds=None,
    )
    if not claimed:
        # The teacher pressed Record in the same instant and won the race: ours is a duplicate egress - stop it.
        try:
            stop_room_recording(egress_id)
        except LiveKitError:
            logger.warning("auto-record: could not stop the duplicate egress %s of session %s", egress_id, session.id)
        return "lost-race"

    session.egress_id, session.last_egress_id, session.recording_started_at = egress_id, egress_id, now
    reset_timeline(session)
    seed_start_chapter(session)
    try:
        # X12: the room-wide notice text rides on the event (same as the manual Record button)...
        from .live_safety import broadcast_notice_payload

        broadcast_to_session(
            session.id, "recording.started",
            {"egress_id": egress_id, "auto": True, "notice": broadcast_notice_payload(session)},
        )
    except Exception:
        logger.exception("auto-record: recording.started broadcast failed for session %s", session.id)
    # ...and a minor who is already in the room and has not acknowledged goes listen-only at once (never raises).
    from .recording_consent import on_recording_started

    on_recording_started(session)
    logger.info("auto-record: started egress %s for session %s (trigger=%s)", egress_id, session.id, trigger)
    return "started"


def session_for_egress(egress_id):
    """The ClassSession an egress belongs to: the one it is running on right now (`egress_id`) or, after a manual stop
    cleared that, the one whose recording it produced (`last_egress_id`). None for a campus / unknown egress."""
    from django.db.models import Q

    from .models import ClassSession

    if not egress_id:
        return None
    return ClassSession.objects.filter(Q(egress_id=egress_id) | Q(last_egress_id=egress_id)).select_related("classroom").first()


def note_egress_started(egress_info):
    """`egress_started` webhook: LiveKit's own start moment is the true 0:00 of the video (the egress takes a few seconds
    to warm up after the API call) - it replaces the stamp made when the request was sent, and any marker already made
    in those first seconds moves back by the same amount so it still points at the right frame."""
    try:
        from datetime import datetime, timezone as dt_timezone

        from .models import ClassSession
        from .recording_tools import restamp_chapters

        started_ns = int(getattr(egress_info, "started_at", 0) or 0)
        if started_ns <= 0:
            return "no-timestamp"
        started = datetime.fromtimestamp(started_ns / 1_000_000_000, tz=dt_timezone.utc)
        # egress_info.started_at is wall clock on LiveKit's server; never move the marker into the future or by an hour.
        now = timezone.now()
        if started > now or (now - started).total_seconds() > 3600:
            return "implausible"
        session = session_for_egress(egress_info.egress_id)
        if session is None:
            return "unknown-egress"
        old = session.recording_started_at
        ClassSession.objects.filter(pk=session.pk).update(recording_started_at=started)
        if old is not None and started > old:
            restamp_chapters(session, int((started - old).total_seconds()))
        return "updated"
    except Exception:
        logger.exception("auto-record: egress_started handling failed")
        return "error"


def egress_file_seconds(file_result):
    """FileInfo.duration is nanoseconds -> whole seconds (0 when unknown)."""
    try:
        ns = int(getattr(file_result, "duration", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(ns // 1_000_000_000, 0)
