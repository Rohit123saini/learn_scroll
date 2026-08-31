# liveclass/consumers.py
"""
One consumer, one job: a client connects to ws/session/<id>/, gets
dropped into that session's Channels group, and receives everything
views.py pushes via realtime.broadcast_to_session() for that session —
chat.message / chat.message_deleted, poll.created / poll.updated /
poll.closed, hand.raised / hand.lowered, recording.started / stopped /
recording.ready, waitlist.promoted, participant.kicked,
presence.joined / presence.left / presence.snapshot.

NOTE (fix — recording.started/stopped/ready were promised here but never
actually sent): ClassSessionViewSet.start_recording/stop_recording and
LiveKitWebhookView's egress_ended handling now call
realtime.broadcast_to_session() for these — see views.py. This docstring
was the spec; the gap was on the sender side, not here.

NEW (Pass 10) — CATCH-UP ON RECONNECT: a client that reconnects after a
drop can pass `?since=<unix_ts>` (the `ts` from the last event or the
`connection.ack`'s `server_time` it saw) and gets everything it missed
replayed right after the ack, oldest first, each flagged
`"replayed": true` so the client can skip re-animating it. See
realtime.py's module docstring ("MISSED-EVENT CATCH-UP") for the full
design and its limits (short, capped buffer — a comfort feature, not a
guarantee).

NEW (Pass 11) — LIVE PRESENCE: connect() now marks the user present
(broadcasting `presence.joined` only on their first live connection —
a second device doesn't re-announce them) and sends a `presence.
snapshot` of everyone already here; disconnect() mirrors this with
`presence.left` on their last connection closing. See realtime.py's
module docstring ("LIVE PRESENCE") for the multi-device counting logic.

NEW (Pass 12) — WS-CONNECT RATE LIMITING: connect() now calls
realtime.check_connect_rate_limit() right after the auth check (auth
runs first since the limiter is keyed by user.id, and an unauthenticated
attempt shouldn't be able to burn a real user's budget anyway) and
before the DB session/access lookup — a rate-limited attempt costs one
Redis round-trip, not a Redis round-trip plus a wasted query. A user
over the limit gets closed with 4429 (this app's echo of HTTP 429). See
realtime.py's module docstring ("WS-CONNECT RATE LIMITING") for the
window/limit and the fail-open contract on a Redis hiccup.

NEW (Pass 12) — TYPING INDICATOR ("teacher is typing..."): the second
inbound message type this consumer understands, alongside "ping".
Deliberately transient — no DB, no REST endpoint, no realtime.py
history-buffer entry (a reconnecting client replaying a 40-second-stale
"is typing" would be actively wrong, not just late) — a client sends
`{"type": "typing"}` and every OTHER currently-connected client in the
session gets a `chat.typing` push with who's typing; the sender is
excluded from its own broadcast (see `session_event`'s `sender_channel`
check below) so it never has to filter out its own echo. No explicit
"stopped typing" event: the client is expected to auto-clear its own
"X is typing…" UI a few seconds after the last event it received for
that user, the same debounce pattern most chat UIs already use, which
means one dropped/rate-limited event just means the indicator clears a
little early — never a stuck "is typing" that never goes away.

NEW (Pass 12) — IDLE-TIMEOUT PRESENCE EVICTION: flagged as a known gap
in Pass 11's own docstring in realtime.py ("presence reflects
disconnect() firing, which an ungraceful drop... can delay") — an
airplane-mode phone or a killed app never sends a WebSocket close frame,
so the old code only noticed via the ASGI server's own transport-level
timeout, which is typically minutes, not seconds. connect() now starts
a small watchdog task that closes the socket itself (triggering the
normal disconnect()/mark_absent()/presence.left path) if no "ping" has
arrived in `_IDLE_TIMEOUT_SECONDS`. This builds on the EXISTING ping/pong
keepalive rather than adding a new inbound message type — a client that
already pings for RTT doesn't need to change anything to also get
evicted promptly on a real drop.

DELIBERATELY NOT a place where clients WRITE data. Every one of those
events already has a validated, permission-checked, throttled REST
endpoint (ChatMessageViewSet.create, LivePollViewSet.vote, ClassSession
.raise_hand, etc.) — duplicating that logic here would mean two code
paths to keep in sync, and DRF's ScopedRateThrottle/serializer validation
has no WebSocket equivalent for free. This consumer accepts exactly two
inbound client message types — "ping" (keepalive/RTT) and, as of Pass
12, "typing" (see above; still not a write to any model) — and
otherwise only pushes; everything else a client sends is echoed back as
an error telling it which REST endpoint to use instead.

ACCESS CONTROL: the same _has_room_access(classroom, user) gate that
already governs ChatMessageViewSet/LivePollViewSet list/create and
ClassSessionViewSet.join/token — reused here, not reimplemented, so a
future change to who's allowed into a session's realtime data can never
drift between the REST and WebSocket paths (import straight from
views.py rather than re-deriving the rule).
"""

import asyncio
import logging
import time
from urllib.parse import parse_qs

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import ClassSession
from .realtime import (
    broadcast_to_session,
    check_connect_rate_limit,
    get_missed_events,
    get_present_user_ids,
    mark_absent,
    mark_present,
)

logger = logging.getLogger(__name__)

# NEW (Pass 12 — idle-timeout presence eviction): how often the watchdog
# wakes up to check, and how long since the last "ping" is tolerated
# before this consumer closes its own socket. 90s tolerance, 15s check
# granularity — comfortably inside the "60-90 sec" window this was
# scoped to, with the check interval small enough that eviction never
# lags the timeout by more than one tick.
_IDLE_CHECK_INTERVAL_SECONDS = 15
_IDLE_TIMEOUT_SECONDS = 90


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

        # NEW (Pass 12 — WS-connect rate limiting): checked before the DB
        # session/access lookup below, so a client stuck in a reconnect
        # loop gets turned away with one cheap Redis round-trip instead of
        # also paying for a query every single attempt. See realtime.py's
        # module docstring ("WS-CONNECT RATE LIMITING") for the window/
        # limit and why this fails OPEN (allows the connection) rather
        # than closed on a Redis hiccup.
        allowed = await sync_to_async(check_connect_rate_limit)(user.id)
        if not allowed:
            await self.close(code=4429)  # 4429: app-defined "too many connection attempts"
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

        self.user_id = user.id  # NOTE (fix, see session_event below): kept
        # so a "participant.kicked" broadcast for THIS user can close
        # THIS specific connection without a second DB round-trip.
        self.group_name = f"session.{self.session_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        server_time = time.time()
        await self.send_json(
            {
                "event": "connection.ack",
                "payload": {"session_id": int(self.session_id)},
                # NOTE (Pass 10): a client with no prior "since" watermark
                # (its very first-ever connect to this session) stores
                # this as its baseline — nothing it missed before its own
                # first connection needs replaying, but every event from
                # here on has a `ts` it can compare against next time.
                "server_time": server_time,
            }
        )

        # NOTE (Pass 10 — catch-up on reconnect): a reconnecting client
        # sends the `ts` of the last event/ack it actually saw as
        # `?since=`. Anything broadcast to this session while it was
        # disconnected — chat, poll updates, hand raises, recording
        # events — gets replayed here, oldest first, so a brief mobile
        # network drop doesn't silently cost the student a chat message
        # or the current poll results. See realtime.py's module
        # docstring for the buffer's size/TTL limits; this degrades to
        # "no catch-up" (empty list), never a failed connection, on any
        # Redis hiccup or missing `?since=`.
        query_string = self.scope.get("query_string", b"").decode("utf-8")
        since_raw = parse_qs(query_string).get("since", [None])[0]
        since = None
        if since_raw is not None:
            try:
                since = float(since_raw)
            except ValueError:
                logger.info("Ignoring malformed ?since=%r on session %s connect.", since_raw, self.session_id)

        missed_events = await sync_to_async(get_missed_events)(self.session_id, since)
        for entry in missed_events:
            await self.send_json(
                {
                    "event": entry.get("event"),
                    "payload": entry.get("payload"),
                    "ts": entry.get("ts"),
                    "replayed": True,  # lets the client skip re-animating a toast/sound for this one
                }
            )

        # NOTE (Pass 11 — live presence): mark_present() only reports True
        # on this user's FIRST currently-open connection to this session —
        # a student with a second tab/device open doesn't trigger a
        # duplicate "joined" for everyone else. See realtime.py's
        # mark_present() docstring for the multi-device counting.
        became_present = await sync_to_async(mark_present)(self.session_id, self.user_id)
        if became_present:
            await sync_to_async(broadcast_to_session)(self.session_id, "presence.joined", {"user_id": self.user_id})

        # Snapshot AFTER marking self present, so a client always sees its
        # own id in the list it gets back — "who else is here" naturally
        # includes "and yes, you're connected too".
        present_user_ids = await sync_to_async(get_present_user_ids)(self.session_id)
        await self.send_json({"event": "presence.snapshot", "payload": {"user_ids": present_user_ids}})

        # NEW (Pass 12 — idle-timeout presence eviction): start the
        # watchdog only once the connection is fully set up (group-joined,
        # accepted, presence marked) — `_last_seen_at` is seeded here so a
        # freshly-connected client isn't immediately eligible for eviction
        # before its first "ping" ever arrives. Stored on self so
        # receive_json() can bump it and disconnect()/the watchdog itself
        # can cancel/exit cleanly.
        self._last_seen_at = time.time()
        self._idle_watchdog_task = asyncio.ensure_future(self._idle_watchdog())

    async def _idle_watchdog(self):
        """Closes THIS socket if no "ping" has arrived in
        `_IDLE_TIMEOUT_SECONDS` — see module docstring ("IDLE-TIMEOUT
        PRESENCE EVICTION"). Runs for the lifetime of the connection;
        disconnect() cancels it on any normal close so it never outlives
        its own socket. Closing here re-enters the ASGI protocol's normal
        teardown, so disconnect() (and therefore mark_absent()/
        presence.left) still fires exactly once, the same as a
        client-initiated close — this is not a second/parallel disconnect
        path.
        """
        try:
            while True:
                await asyncio.sleep(_IDLE_CHECK_INTERVAL_SECONDS)
                if time.time() - self._last_seen_at > _IDLE_TIMEOUT_SECONDS:
                    logger.info(
                        "Evicting idle WebSocket for user %s on session %s (no ping in %ss).",
                        self.user_id, self.session_id, _IDLE_TIMEOUT_SECONDS,
                    )
                    await self.close(code=4408)  # 4408: app-defined "idle timeout"
                    return
        except asyncio.CancelledError:
            # Normal path: disconnect() cancels this on any other close
            # reason (client-initiated close, kick, server shutdown) —
            # nothing to clean up here, just let the cancellation
            # propagate.
            raise

    async def disconnect(self, close_code):
        # NEW (Pass 12): stop the watchdog on EVERY disconnect path,
        # whether it was client-initiated, a kick (see session_event's
        # participant.kicked handling below), or the watchdog's own
        # eviction closing this same socket — cancelling a task that
        # already finished (the eviction case) is a safe no-op.
        if hasattr(self, "_idle_watchdog_task"):
            self._idle_watchdog_task.cancel()
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        if hasattr(self, "user_id"):
            # NOTE (Pass 11): group_discard above runs FIRST, so this
            # consumer is no longer in the group by the time the
            # broadcast below fires — a disconnecting client never
            # receives its own "presence.left" echo. left_for_good is
            # True only when this was the user's LAST open connection
            # (see mark_absent()'s own docstring for the multi-device
            # counting, mirroring mark_present() above).
            left_for_good = await sync_to_async(mark_absent)(self.session_id, self.user_id)
            if left_for_good:
                await sync_to_async(broadcast_to_session)(
                    self.session_id, "presence.left", {"user_id": self.user_id}
                )

    async def receive_json(self, content, **kwargs):
        # Only TWO inbound message types this consumer understands
        # ("ping" and, as of Pass 12, "typing") — everything else (chat
        # send, poll vote, hand raise) goes through REST; see module
        # docstring for why that's deliberate, not an oversight.
        msg_type = content.get("type")

        # NEW (Pass 12 — idle-timeout eviction): any legitimate inbound
        # message proves this connection is still alive, not just "ping"
        # specifically — but "ping" is the only one a client is expected
        # to send on a regular cadence, so it's the one the watchdog's
        # docstring/comments describe as the keepalive signal.
        if hasattr(self, "_last_seen_at"):
            self._last_seen_at = time.time()

        if msg_type == "ping":
            await self.send_json({"event": "pong", "payload": {}})
            return

        if msg_type == "typing":
            # NEW (Pass 12 — typing indicator): direct group_send, NOT
            # realtime.broadcast_to_session() — deliberately skips that
            # function's replay-history recording (see module docstring:
            # a reconnecting client replaying a stale "is typing" would
            # be actively misleading, not just late). `sender_channel` is
            # how session_event below excludes the sender from its own
            # broadcast — Channels groups otherwise fan out to every
            # member INCLUDING the sender.
            await self.channel_layer.group_send(
                self.group_name,
                {
                    "type": "session.event",
                    "event": "chat.typing",
                    "payload": {"user_id": self.user_id},
                    "ts": time.time(),
                    "sender_channel": self.channel_name,
                },
            )
            return

        await self.send_json(
            {
                "event": "error",
                "payload": {
                    "detail": (
                        "This socket is read-only. Send chat messages, poll "
                        "votes, and hand-raise toggles through the REST API "
                        "— you'll receive the resulting event back here. "
                        "\"ping\" and \"typing\" are the only messages this "
                        "socket accepts directly."
                    ),
                },
            }
        )

    # Dispatched by Channels when realtime.broadcast_to_session() calls
    # channel_layer.group_send(..., {"type": "session.event", ...}) — the
    # "type" value there maps to this method name (session.event ->
    # session_event) by Channels' own naming convention.
    async def session_event(self, message):
        # NEW (Pass 12 — typing indicator): the ONE event type in this app
        # a socket should NOT receive its own echo of. Every other event
        # here comes from realtime.broadcast_to_session() and never sets
        # sender_channel, so this is a no-op for chat/poll/hand/recording/
        # presence — only the direct group_send in receive_json() above
        # (for "typing") ever sets it.
        if message.get("sender_channel") == self.channel_name:
            return
        await self.send_json({"event": message["event"], "payload": message["payload"], "ts": message.get("ts")})

        # NOTE (fix — realtime gap): before this, ClassSessionViewSet.kick()
        # / ClassroomViewSet.ban() only blocked FUTURE REST join()/token()
        # calls — an already-open socket for the removed user kept
        # receiving every chat/poll/hand event for this session
        # indefinitely, since connect() is the only place access was ever
        # checked. Every socket in the group receives this same broadcast
        # (group_send fans out to everyone); only the one whose user_id
        # matches the kicked payload closes itself here — every other
        # connected client just gets the event above and updates its
        # participant list, same as any other push.
        if message["event"] == "participant.kicked" and message["payload"].get("user_id") == self.user_id:
            await self.close(code=4403)