# liveclass/consumers.py
"""
One consumer, one job: a client connects to ws/session/<id>/, gets
dropped into that session's Channels group, and receives everything
views.py pushes via realtime.broadcast_to_session() for that session —
chat.message / chat.message_deleted, poll.created / poll.updated /
poll.closed, hand.raised / hand.lowered, recording.started / stopped.

DELIBERATELY NOT a place where clients WRITE data. Every one of those
events already has a validated, permission-checked, throttled REST
endpoint (ChatMessageViewSet.create, LivePollViewSet.vote, ClassSession
.raise_hand, etc.) — duplicating that logic here would mean two code
paths to keep in sync, and DRF's ScopedRateThrottle/serializer validation
has no WebSocket equivalent for free. This consumer accepts exactly one
inbound client message type ("ping") for keepalive/RTT and otherwise
only pushes; everything else a client sends is echoed back as an error
telling it which REST endpoint to use instead.

ACCESS CONTROL: the same _has_room_access(classroom, user) gate that
already governs ChatMessageViewSet/LivePollViewSet list/create and
ClassSessionViewSet.join/token — reused here, not reimplemented, so a
future change to who's allowed into a session's realtime data can never
drift between the REST and WebSocket paths (import straight from
views.py rather than re-deriving the rule).
"""

import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import ClassSession

logger = logging.getLogger(__name__)


@database_sync_to_async
def _session_and_access(session_id, user):
    """One query, off the event loop — mirrors
    ChatMessageViewSet._session_or_none + _has_room_access, done together
    since a consumer only needs the boolean, not two round-trips."""
    from .views import _has_room_access  # local import: avoids loading the

    # full views.py module graph before Django app registry is ready
    # during ASGI startup; safe once the app is booted (see ws_auth.py's
    # asgi.py wiring note — django.setup() runs before this is ever hit).
    session = ClassSession.objects.filter(pk=session_id).select_related("classroom").first()
    if session is None:
        return None, False
    return session, _has_room_access(session.classroom, user)


class SessionConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        user = self.scope.get("user")

        if user is None or not user.is_authenticated:
            # Mirrors IsAuthenticated on every REST viewset in this app —
            # ws_auth.JWTAuthMiddleware already resolved (or failed to
            # resolve) the user before this ever runs.
            await self.close(code=4401)  # 4401: app-defined "unauthenticated"
            return

        session, has_access = await _session_and_access(self.session_id, user)
        if session is None:
            await self.close(code=4404)
            return
        if not has_access:
            # Same message a REST 403 on this session would give — see
            # _has_room_access call sites in views.py.
            await self.close(code=4403)
            return

        self.group_name = f"session.{self.session_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connection.ack", "payload": {"session_id": int(self.session_id)}})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Only inbound message this consumer understands — everything
        # else (chat send, poll vote, hand raise) goes through REST; see
        # module docstring for why that's deliberate, not an oversight.
        if content.get("type") == "ping":
            await self.send_json({"event": "pong", "payload": {}})
            return
        await self.send_json(
            {
                "event": "error",
                "payload": {
                    "detail": (
                        "This socket is read-only. Send chat messages, poll "
                        "votes, and hand-raise toggles through the REST API "
                        "— you'll receive the resulting event back here."
                    ),
                },
            }
        )

    # Dispatched by Channels when realtime.broadcast_to_session() calls
    # channel_layer.group_send(..., {"type": "session.event", ...}) — the
    # "type" value there maps to this method name (session.event ->
    # session_event) by Channels' own naming convention.
    async def session_event(self, message):
        await self.send_json({"event": message["event"], "payload": message["payload"]})