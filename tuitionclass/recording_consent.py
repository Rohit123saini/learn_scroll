"""
tuitionclass/recording_consent.py  -  X12 RECORDING NOTICE + ACKNOWLEDGE

The consent side of recording a live class. The TEXT and the rules live in live_safety.py; this module is the part that
touches rows and the live room.

    current_participant()      the person's current seat (or None)
    notice_for()               what the app shows (GET recording-notice/) - same payload as in the join / token reply
    acknowledge()              POST recording-notice/ack/ - stamps recording_notice_ack_at on EVERY row of this person
                               in the session (a rejoin makes a new row; the stamp must survive it) and, for a minor who
                               was listen-only because of the recording, switches their mic / camera permission on in the
                               LIVE room - no rejoin needed
    carry_ack_to_new_row()     join hook: a new row inherits an earlier acknowledgement of the same session
    on_recording_started()     start hook (manual Record + auto-record): minors already in the room who have not
                               acknowledged lose publish permission at once and get a `recording.notice_required` event
                               on their own channel. Everyone else only gets the room-wide notice in `recording.started`.

An adult is never blocked by the notice - it is informational for them (they may stay, or leave, or keep devices off).
A minor can still watch, listen and CHAT while the notice is unacknowledged; only mic / camera wait for it.

Every function that talks to LiveKit or the channel layer is best-effort: a failure is logged and never turns a join, a
start-recording or an acknowledge into an error.
"""

import logging

from django.utils import timezone
from rest_framework.exceptions import NotFound

from . import live_safety

logger = logging.getLogger(__name__)


def current_participant(session, user):
    return session.participants.filter(user=user, left_at__isnull=True).order_by("-joined_at").first()


def notice_for(session, user):
    return live_safety.notice_payload(session, user, current_participant(session, user))


def carry_ack_to_new_row(session, user, participant):
    """A rejoin must not ask a minor to acknowledge the same recording again. Best effort."""
    try:
        if participant.recording_notice_ack_at is not None:
            return
        prior = (
            session.participants.filter(user=user, recording_notice_ack_at__isnull=False)
            .exclude(pk=participant.pk).values_list("recording_notice_ack_at", flat=True).first()
        )
        if prior is not None:
            participant.recording_notice_ack_at = prior
            participant.save(update_fields=["recording_notice_ack_at"])
    except Exception:
        logger.exception("carry_ack_to_new_row failed (session %s user %s)", getattr(session, "id", None),
                         getattr(user, "id", None))


def acknowledge(session, user) -> dict:
    """Record that `user` saw the notice. Idempotent. Returns the fresh notice payload."""
    from .livekit_utils import set_participants_can_publish
    from .realtime import broadcast_to_user
    from .views.access import _resolve_session_roles
    from .views.sessions import _stage_can_publish

    participant = current_participant(session, user)
    if participant is None:
        raise NotFound("Join the class first.")

    now = timezone.now()
    session.participants.filter(user=user, recording_notice_ack_at__isnull=True).update(recording_notice_ack_at=now)
    participant.refresh_from_db()

    # Lift the listen-only limit in the live room, unless something else (stage-only mode and not on stage) still
    # forbids publishing. The next token carries the same answer (_stage_can_publish), so a failure here only means
    # the person has to wait for their next reconnect.
    if live_safety.recording_running(session) and live_safety.is_minor(user):
        livekit_role, _participant_role = _resolve_session_roles(session, user)
        if _stage_can_publish(session, livekit_role, user) is not False:
            try:
                set_participants_can_publish(str(session.room_id), [user.id], True)
            except Exception:
                logger.exception("acknowledge: could not enable publish for %s in session %s", user.id, session.id)

    payload = live_safety.notice_payload(session, user, participant)
    broadcast_to_user(user.id, "recording.notice_acked", {"session_id": session.id, "notice": payload})
    return payload


def on_recording_started(session):
    """Called right after a recording (manual or automatic) was marked running on `session`. Returns how many minors
    were put on listen-only. Never raises."""
    try:
        from .livekit_utils import set_participants_can_publish
        from .models import SessionParticipant
        from .realtime import broadcast_to_user

        minors = (
            session.participants.filter(
                left_at__isnull=True, recording_notice_ack_at__isnull=True,
                user__date_of_birth__gt=live_safety._minor_cutoff(),
            )
            .exclude(role=SessionParticipant.Role.HOST)
            .select_related("user")
        )
        locked = []
        for p in minors:
            if live_safety.is_minor(p.user):
                locked.append(p.user_id)
        if not locked:
            return 0
        try:
            set_participants_can_publish(str(session.room_id), locked, False)
        except Exception:
            logger.exception("recording start: could not lock publish of %d minors (session %s)", len(locked), session.id)
        for uid in locked:
            payload = live_safety.notice_payload(session, next(p.user for p in minors if p.user_id == uid))
            broadcast_to_user(uid, "recording.notice_required", {"session_id": session.id, "notice": payload})
        return len(locked)
    except Exception:
        logger.exception("recording_consent.on_recording_started failed (session %s)", getattr(session, "id", None))
        return 0
