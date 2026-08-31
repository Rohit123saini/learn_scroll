# liveclass/realtime.py
"""
Server -> connected-client push, backed by Django Channels' channel layer
(see CHANNEL_LAYERS in settings.py — Redis in production, in-memory for
local dev).

WHY THIS FILE EXISTS:
    views.py already calls `broadcast_to_session(session_id, event_type,
    payload)` from a dozen call sites (chat create/delete, poll create/
    vote/close, hand raise/lower, recording start/stop) — the REST layer
    stays the single source of truth for validation/permissions/DB writes,
    this just fans the already-committed change out to everyone else
    currently connected to that session, so clients don't have to poll.

    That import (`from .realtime import broadcast_to_session`) had no
    matching module in this codebase — every one of those call sites would
    raise ImportError the moment views.py is imported, which happens at
    Django app-boot (urls.py imports views.py). This wasn't a "nice to
    have" gap, it was a guaranteed failure to boot at all. This file (plus
    consumers.py / routing.py / ws_auth.py alongside it) is the fix.

HOW A MESSAGE FLOWS:
    views.py: broadcast_to_session(42, "chat.message", {...})
        -> channel_layer.group_send("session.42", {"type": "session.event", ...})
    consumers.py: SessionConsumer.session_event() (every socket currently
        in group "session.42", i.e. every client connected to
        ws/session/42/) -> sends {"event": "chat.message", "payload": {...}}
        down that client's WebSocket.

GROUP NAMING: "session.<id>" — Channels group names may only contain
    ASCII alphanumerics, hyphens, underscores, and periods (no colons),
    so this deliberately does NOT reuse a REST-style "session:42" key.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def _group_name(session_id) -> str:
    return f"session.{session_id}"


def broadcast_to_session(session_id, event_type: str, payload: dict) -> bool:
    """Best-effort — NEVER raises. Same "a realtime/notification side
    channel must never break the request that triggered it" contract as
    notifications.send_notification(): a Redis blip should degrade to a
    logged warning (clients fall back to their next REST poll/refresh),
    not a 500 on a chat message or poll vote that already committed to
    the DB successfully.

    event_type: dotted event name the client switches on, e.g.
        "chat.message", "chat.message_deleted", "poll.created",
        "poll.updated", "poll.closed", "hand.raised", "hand.lowered",
        "recording.started", "recording.stopped".
    payload: JSON-serializable dict — already-serialized data (the same
        serializer output the REST response itself returned), so a
        listening client can update its UI without an extra fetch.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        # No CHANNEL_LAYERS configured at all (shouldn't happen given
        # settings.py always sets a default, even the in-memory one) —
        # degrade rather than crash the caller.
        logger.warning(
            "No channel layer configured — dropping realtime event %r for session %s.",
            event_type, session_id,
        )
        return False
    try:
        async_to_sync(channel_layer.group_send)(
            _group_name(session_id),
            {
                "type": "session.event",  # dispatched to SessionConsumer.session_event
                "event": event_type,
                "payload": payload,
            },
        )
        return True
    except Exception:
        logger.exception(
            "Realtime broadcast failed (session=%s, event=%s) — connected "
            "clients will miss this push and rely on their next REST call.",
            session_id, event_type,
        )
        return False