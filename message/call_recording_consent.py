"""
message/call_recording_consent.py  -  C16 CALL RECORDING CONSENT + NOTICE + MINOR RULE

`CallRecordingView` (host-only) records the whole call room, but until now nobody else was told, and a
minor could sit in a recorded call. This module is the call-side twin of `tuitionclass/recording_consent.py`
(same naming, same "best effort, never turn a call action into an error" rule) and holds ALL of C16's rules:

    notice_for()          what the app shows (GET calls/<id>/recording-notice/, and the `recording_notice` key in the
                          accept / join / refresh-token replies while a recording runs)
    acknowledge()         POST calls/<id>/recording-notice/ack/ - "I understand", logged once per recording
    minors_in_call()      active participants (and the host) with a DOB under ADULT_AGE
    join_blocked_for()    True when `user` is a minor and a recording is running  (call_state.accept_call / join_call)
    on_started() / on_stopped() / on_blocked_*()   consent-log rows + the broadcast payload

RULES
    1. Host-only start/stop stays as it was.
    2. A recording NEVER contains a minor: start is refused (409 `recording_minor_blocked`) while the host or any
       active participant is a minor, and a minor cannot accept / join while a recording runs (same 409 code).
       They can join as soon as the host stops the recording. Users with NO date of birth are "unknown" and are
       treated like adults (login/age.py rule) - nobody is locked out on a guess.
    3. Everyone else is NOTIFIED, not blocked: "Recording on" banner + sound in the app, the notice text, and an
       "I understand" acknowledgement that is logged. An adult who does not agree can leave the call or keep
       devices off - same as in a live class.
    4. Consent log = `CallRecordingConsent` rows (append-only). One recording = one `egress_id`.

`notice_shown` means "the server delivered the notice to this person" (broadcast at start, or the join / refresh reply),
not "the screen was looked at" - only `acknowledged` is the person's own action.
"""

import logging

from .models import CallParticipant, CallRecordingConsent, CallStatus

logger = logging.getLogger(__name__)

NOTICE_VERSION = "2026-10-call"

NOTICE_EN = {
    "title": "This call is being recorded",
    "body": (
        "The audio, video and screen share of everyone in this call are recorded while the REC badge is on. "
        "If you do not want to be recorded, leave the call or keep your camera and microphone off."
    ),
}
NOTICE_HI = {
    "title": "यह कॉल रिकॉर्ड हो रही है",
    "body": (
        "जब तक REC का निशान चालू है, इस कॉल में मौजूद सभी का वीडियो, आवाज़ और स्क्रीन शेयर रिकॉर्ड होता है। "
        "अगर आप रिकॉर्ड नहीं होना चाहते, तो कॉल छोड़ दें या कैमरा और माइक बंद रखें।"
    ),
}

MINOR_BLOCK_CODE = "recording_minor_blocked"
MINOR_START_MESSAGE_EN = "Recording cannot start while a minor is in the call."
MINOR_JOIN_MESSAGE_EN = "This call is being recorded, so you can join once the recording stops."


# ---------------------------------------------------------------------------
# minors
# ---------------------------------------------------------------------------
def _minor_cutoff():
    from login.age import minor_cutoff_date

    return minor_cutoff_date()


def is_minor_user(user) -> bool:
    """DOB on file and under ADULT_AGE (login/age.py). Unknown DOB -> False. Never raises."""
    try:
        return bool(getattr(user, "is_minor", False))
    except Exception:
        logger.exception("is_minor_user failed for user %s", getattr(user, "id", None))
        return False


def minors_in_call(call) -> list:
    """User ids of the host / active participants who are minors (sorted, unique). Raises on DB trouble: the
    callers decide (start recording fails CLOSED - a recording we cannot vet is not started)."""
    ids = set(
        CallParticipant.objects.filter(
            call=call, status=CallStatus.ONGOING, left_at__isnull=True,
            user__date_of_birth__gt=_minor_cutoff(),
        ).values_list("user_id", flat=True)
    )
    caller = call.caller
    if is_minor_user(caller):
        ids.add(caller.id)
    return sorted(ids, key=str)


def join_blocked_for(call, user_id) -> bool:
    """True when `user_id` is a minor and a recording is running on `call`. Called by call_state.accept_call /
    join_call INSIDE their row lock, so it cannot race with the host's start (which re-checks under the same lock).
    A lookup error is logged and means "not blocked" - the host's start-time check already kept minors out."""
    if not getattr(call, "is_recording", False):
        return False
    try:
        from .views._common import User

        user = User.objects.filter(pk=user_id).only("id", "date_of_birth").first()
        blocked = user is not None and is_minor_user(user)
    except Exception:
        logger.exception("join_blocked_for failed (call %s user %s)", getattr(call, "id", None), user_id)
        return False
    if blocked:
        log(call, user, CallRecordingConsent.Event.BLOCKED_MINOR_JOIN)
    return blocked


# ---------------------------------------------------------------------------
# consent log
# ---------------------------------------------------------------------------
def log(call, user, event, *, once=False, **meta):
    """Append one consent-log row. `once=True`: skip when this person already has this event for THIS recording
    (same egress id) - notice_shown / acknowledged must not pile up on every token refresh. Returns True when a
    row was written. Best effort, never raises."""
    try:
        egress_id = call.recording_egress_id or ""
        if once and user is not None and CallRecordingConsent.objects.filter(
            call=call, user=user, event=event, egress_id=egress_id,
        ).exists():
            return False
        CallRecordingConsent.objects.create(
            call=call, user=user, event=event, egress_id=egress_id,
            notice_version=NOTICE_VERSION, meta=meta or {},
        )
        return True
    except Exception:
        logger.exception("call recording consent log failed (call %s event %s)", getattr(call, "id", None), event)
        return False


def has_acknowledged(call, user) -> bool:
    try:
        return CallRecordingConsent.objects.filter(
            call=call, user=user, event=CallRecordingConsent.Event.ACKNOWLEDGED,
            egress_id=call.recording_egress_id or "",
        ).exists()
    except Exception:
        logger.exception("has_acknowledged failed (call %s)", getattr(call, "id", None))
        return False


# ---------------------------------------------------------------------------
# notice
# ---------------------------------------------------------------------------
def _text_payload() -> dict:
    return {
        "version": NOTICE_VERSION,
        "title_en": NOTICE_EN["title"], "body_en": NOTICE_EN["body"],
        "title_hi": NOTICE_HI["title"], "body_hi": NOTICE_HI["body"],
    }


def broadcast_payload(call) -> dict:
    """Room-wide text for the `recording_started` event (no per-person flags). `sound` tells the app to play the
    short \"recording on\" tone together with the banner."""
    return {
        **_text_payload(),
        "recording": True,
        "started_at": call.recording_started_at.isoformat() if call.recording_started_at else None,
        "host_id": str(call.caller_id),
        "sound": True,
    }


def notice_for(call, user, *, log_shown=False) -> dict:
    """Per-person notice: what the banner / dialog shows. `show` is False when no recording runs. The host sees
    the banner but never needs to acknowledge their own recording (`ack_required` False)."""
    running = bool(call.is_recording)
    is_host = call.caller_id == user.id
    acked = has_acknowledged(call, user) if running else False
    if running and log_shown and not is_host:
        log(call, user, CallRecordingConsent.Event.NOTICE_SHOWN, once=True)
    return {
        **_text_payload(),
        "show": running,
        "recording": running,
        "sound": running,
        "is_host": is_host,
        "acknowledged": acked or is_host,
        "ack_required": bool(running and not is_host and not acked),
        "started_at": call.recording_started_at.isoformat() if (running and call.recording_started_at) else None,
    }


def attach_notice(call, user, payload: dict) -> dict:
    """Adds `recording_notice` to an accept / join / refresh-token reply when a recording runs (late joiners and
    reconnects get the banner without waiting for a WS event). Never raises, never removes keys."""
    try:
        if call is not None and call.is_recording:
            payload["recording_notice"] = notice_for(call, user, log_shown=True)
        else:
            payload["recording_notice"] = None
    except Exception:
        logger.exception("attach_notice failed (call %s)", getattr(call, "id", None))
    return payload


def acknowledge(call, user) -> dict:
    """Record that `user` saw the notice. Idempotent. Raises PermissionError when `user` is not in the call."""
    in_call = CallParticipant.objects.filter(
        call=call, user=user, status=CallStatus.ONGOING, left_at__isnull=True,
    ).exists() or call.caller_id == user.id
    if not in_call:
        raise PermissionError("not_in_call")
    if call.is_recording:
        log(call, user, CallRecordingConsent.Event.ACKNOWLEDGED, once=True)
    return notice_for(call, user)


# ---------------------------------------------------------------------------
# start / stop hooks (CallRecordingView)
# ---------------------------------------------------------------------------
def on_started(call, host) -> int:
    """After the recording was marked running: log `started`, and `notice_shown` for everyone in the room right now
    (the `recording_started` broadcast carries the notice). Returns how many people were notified. Never raises."""
    try:
        log(call, host, CallRecordingConsent.Event.STARTED)
        ids = list(
            CallParticipant.objects.filter(call=call, status=CallStatus.ONGOING, left_at__isnull=True)
            .exclude(user_id=host.id).values_list("user_id", flat=True)
        )
        if ids:
            from .views._common import User

            for u in User.objects.filter(pk__in=ids):
                log(call, u, CallRecordingConsent.Event.NOTICE_SHOWN, once=True)
        return len(ids)
    except Exception:
        logger.exception("call_recording_consent.on_started failed (call %s)", getattr(call, "id", None))
        return 0


def on_stopped(call, host) -> None:
    log(call, host, CallRecordingConsent.Event.STOPPED)


def on_blocked_start(call, host, minor_ids) -> None:
    log(call, host, CallRecordingConsent.Event.BLOCKED_MINOR_START, minor_count=len(minor_ids))


def block_payload(detail: str) -> dict:
    return {"detail": detail, "code": MINOR_BLOCK_CODE}
