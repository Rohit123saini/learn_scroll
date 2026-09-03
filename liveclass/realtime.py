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

    NEW (realtime fix pass) — PER-USER CHANNEL (`broadcast_to_user()`):
    a second, narrower gap of the same shape. `views.py`'s
    `_safe_broadcast_to_user()` (used by ClassJoinRequestViewSet's
    create/accept/reject and ClassroomStaffViewSet's perform_create) has
    called `from .realtime import broadcast_to_user` since Flutter Phase
    1/Phase 4, but — unlike `broadcast_to_session` above — that one
    never raised ImportError, because `_safe_broadcast_to_user()` wraps
    the whole thing in a try/except that only logs. So instead of a
    boot-time crash, this one just silently never fired: every join-
    request badge push and staff-promotion push was a no-op, degrading
    to "works fine on manual refresh/reopen, never updates live" with
    nothing in the logs loud enough to notice quickly. `broadcast_to_
    user()` below (plus `UserConsumer` in consumers.py, and the
    `ws/liveclass/user/` route already in routing.py) closes that gap.

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

NEW (Pass 10) — MISSED-EVENT CATCH-UP ON RECONNECT:
    A Channels group only delivers to sockets connected AT THE MOMENT
    `group_send()` runs. A student on a flaky mobile connection (this is
    a Flutter app — mid-class WiFi/mobile-data drops of a few seconds are
    the normal case, not the edge case) who reconnects a moment later
    used to just permanently miss every chat message, poll update, and
    hand-raise that happened in that gap — the socket had no way to ask
    "what did I miss?", and REST has no equivalent endpoint either
    (chat/poll list views aren't scoped to "since my last event").

    `broadcast_to_session()` now ALSO appends every event it sends to a
    short, capped, auto-expiring replay buffer per session (backed by
    the same Redis `django-redis` is already required to run against —
    see §10 of the audit doc — via its raw client, so this reuses
    existing infra rather than adding a new dependency). `consumers.py`
    reads that buffer on connect via `get_missed_events()` and replays
    anything the client hasn't seen yet, keyed by a `?since=<unix ts>`
    query param the client remembers from the last event/ack it got.

    This is a COMFORT feature, not a correctness guarantee: the buffer
    is capped (last `_HISTORY_MAX_EVENTS`) and short-lived
    (`_HISTORY_TTL_SECONDS`), and both history functions below degrade
    to a silent no-op (return False / []) on any cache backend without
    raw Redis access (e.g. local dev on `LocMemCache`) or any Redis
    error — a missed catch-up should never be the reason a chat message
    fails to send or a connection fails to open.

NEW (Pass 11) — LIVE PRESENCE ("who's actually connected right now"):
    Nothing before this pass could answer "is this student's connection
    actually open right now" — `SessionParticipant.left_at IS NULL` is
    the closest REST equivalent, but that's "hasn't formally left this
    session" (set on kick/leave), not "has a live socket" (a dropped
    WiFi connection updates neither). A host wanting to see who's
    genuinely online in real time had nothing to look at.

    `mark_present()`/`mark_absent()` keep a per-session Redis HASH of
    `user_id -> open-connection count` (a count, not a flag/set, so the
    same student on two devices — phone + a browser tab — doesn't get
    marked "left" the moment ONE of those closes; see each function's
    own docstring). `consumers.py` calls `mark_present()` on connect
    and `mark_absent()` on disconnect, broadcasting `presence.joined`/
    `presence.left` only on the 0->1 / 1->0 transitions — a second
    device connecting/disconnecting updates the count silently, since
    the user was already known to be present. `get_present_user_ids()`
    lets a freshly-connecting client ask "who's here already" via a
    `presence.snapshot` event, instead of only learning about people
    who join AFTER it does.

    Same degrade-gracefully contract as the history functions above:
    no raw Redis client available, or any Redis error, means presence
    just silently stops updating — never a failed connection, never
    breaks chat/poll/hand-raise, which don't depend on it at all.

NEW (Pass 12) — WS-CONNECT RATE LIMITING: flagged since Pass 9 (§12 item
    17 of the audit doc) as the one realtime call site in this app with
    no throttle at all — every REST write already goes through DRF's
    ScopedRateThrottle (session_join, chat_message_create, etc.), but
    `SessionConsumer.connect()` had no WebSocket equivalent, so a client
    stuck in a rapid reconnect loop (a buggy client retry, or someone
    deliberately hammering it) could open unbounded connect() calls per
    second — each one a DB query (`_session_and_access`) plus a
    `mark_present()` Redis round-trip, unlike a rejected REST request.

    `check_connect_rate_limit(user_id)` is a simple fixed-window counter
    (INCR + EXPIRE on the window's first hit, one round-trip on every
    later hit) — deliberately not a token bucket: a WebSocket connect
    isn't a smooth request stream the way REST traffic is, it's bursty by
    nature (a page load opens one connection, a network drop causes a
    flurry of near-simultaneous reconnect attempts across a student's
    open tabs), so a plain "N connects per window" cap models the actual
    failure mode (a reconnect LOOP, not a reconnect BURST) without
    punishing the normal multi-tab/one-bad-network-blip case as harshly
    as a bucket that never lets a burst refill.

    Same degrade-gracefully contract as everything else in this file: no
    raw Redis client, or any Redis error, means this returns True (allow)
    rather than failing the connection — a rate limiter that can silently
    fail closed and lock every student out during a Redis blip is worse
    than one that fails open during that same blip, same trade-off DRF's
    own cache-backed throttles make.
"""

import json
import logging
import time

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Last N events per session — enough to ride out a real network blip
# without letting one wildly-active session (rapid-fire chat/poll
# updates) blow up Redis memory. Not meant to replace a full read of
# ChatMessageViewSet's/LivePollViewSet's own paginated REST endpoints if
# a client's been gone longer than this covers.
_HISTORY_MAX_EVENTS = 50
# 15 minutes: comfortably longer than any real reconnect gap (a lift, a
# tunnel, a spotty WiFi handoff), short enough that a buffer for a class
# that ended hours ago just expires on its own instead of needing an
# explicit cleanup task.
_HISTORY_TTL_SECONDS = 15 * 60
# Refreshed on every mark_present()/mark_absent() call, so any session
# with ongoing activity never expires mid-class; a session that's
# genuinely abandoned (crash, or every socket dies without a clean
# disconnect) self-cleans instead of leaking a Redis key forever. Well
# above any realistic single-session length.
_PRESENCE_TTL_SECONDS = 6 * 60 * 60
# NEW (Pass 12): generous enough that a legitimate flurry of reconnects
# (a student's phone hopping between WiFi and mobile data mid-class,
# each retry from a Flutter socket client) sails through, tight enough
# that a genuine reconnect-spam loop gets capped well before it turns
# into a meaningful DB/Redis load spike. Per-user, not per-session — a
# host who teaches back-to-back sessions and a student bouncing between
# two classroom tabs both stay under one shared budget rather than each
# session resetting it, which is what actually bounds the DB-query rate
# this exists to protect.
_WS_CONNECT_LIMIT = 20
_WS_CONNECT_WINDOW_SECONDS = 60


def _group_name(session_id) -> str:
    return f"session.{session_id}"


def _history_key(session_id) -> str:
    return f"liveclass:session_history:{session_id}"


def _presence_key(session_id) -> str:
    return f"liveclass:session_presence:{session_id}"


def _raw_redis_client(write: bool):
    """Returns django-redis's underlying redis-py client, or None on any
    backend/config that doesn't expose one (e.g. LocMemCache in local
    dev without Redis running) — the one place both history functions
    below share this degrade-gracefully check, so they can't drift on
    what "no Redis available" means between them."""
    try:
        return cache.client.get_client(write=write)
    except AttributeError:
        # Not django-redis at all — cache.client has no get_client().
        return None
    except Exception:
        logger.debug("No raw Redis client available for event-history replay buffer.", exc_info=True)
        return None


def _record_history(session_id, event_type: str, payload: dict, ts: float) -> None:
    """Best-effort append to the capped, TTL'd replay buffer described in
    the module docstring above. Uses RPUSH+LTRIM+EXPIRE in one pipeline
    so trimming can never race a concurrent append into losing the trim
    (two separate round-trips would let another writer's RPUSH land
    between this call's own RPUSH and LTRIM). NEVER raises — same
    contract as broadcast_to_session() itself.
    """
    client = _raw_redis_client(write=True)
    if client is None:
        return
    try:
        entry = json.dumps({"event": event_type, "payload": payload, "ts": ts}, default=str)
        key = _history_key(session_id)
        pipe = client.pipeline()
        pipe.rpush(key, entry)
        pipe.ltrim(key, -_HISTORY_MAX_EVENTS, -1)
        pipe.expire(key, _HISTORY_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.exception(
            "Failed to record event history for session %s (event=%s) — a reconnecting "
            "client just won't get this one back via catch-up.", session_id, event_type,
        )


def get_missed_events(session_id, since) -> list[dict]:
    """Returns buffered events for this session with `ts > since`, oldest
    first — `since` may be None (a client's very first connect, or one
    that never recorded a `server_time`/event `ts`), in which case every
    buffered event still in the window is returned. Called from
    consumers.py on connect; NEVER raises, returns [] on any failure so
    a Redis blip degrades to "no catch-up this time", never a failed
    WebSocket handshake.
    """
    client = _raw_redis_client(write=False)
    if client is None:
        return []
    try:
        raw_entries = client.lrange(_history_key(session_id), 0, -1)
    except Exception:
        logger.exception("Failed to read event history for session %s — skipping catch-up replay.", session_id)
        return []

    events = []
    for raw in raw_entries:
        try:
            entry = json.loads(raw)
        except (TypeError, ValueError):
            continue  # a corrupt/foreign entry in the key — skip, don't fail the whole replay
        if since is None or entry.get("ts", 0) > since:
            events.append(entry)
    return events


def mark_present(session_id, user_id) -> bool:
    """Records one more open connection for `user_id` in this session.
    Returns True only on the 0->1 transition (this is genuinely their
    FIRST live connection right now) — the caller should broadcast
    `presence.joined` only in that case, so a second tab/device
    connecting doesn't re-announce someone already known to be present.

    Uses HINCRBY, which is atomic in Redis on its own — two concurrent
    connects for the same user can't race each other into an
    inconsistent count the way a read-then-write would. Best-effort:
    returns False (never raises) if no raw Redis client is available or
    the call fails, same as every other function in this file — a
    presence-tracking hiccup should never block a connection.
    """
    client = _raw_redis_client(write=True)
    if client is None:
        return False
    try:
        key = _presence_key(session_id)
        new_count = client.hincrby(key, str(user_id), 1)
        client.expire(key, _PRESENCE_TTL_SECONDS)
        return new_count == 1
    except Exception:
        logger.exception("Failed to mark user %s present for session %s.", user_id, session_id)
        return False


def mark_absent(session_id, user_id) -> bool:
    """Mirror of mark_present() for disconnect. Returns True only on the
    ->0 transition (their LAST live connection just closed) — the
    caller should broadcast `presence.left` only in that case. Clamps
    at 0 and deletes the field entirely once a user has none left
    (rather than letting it drift negative, which a disconnect() firing
    without a matching prior connect — e.g. a connection that dropped
    before mark_present() ever ran — could otherwise cause).
    """
    client = _raw_redis_client(write=True)
    if client is None:
        return False
    try:
        key = _presence_key(session_id)
        field = str(user_id)
        new_count = client.hincrby(key, field, -1)
        if new_count <= 0:
            client.hdel(key, field)
            return True
        client.expire(key, _PRESENCE_TTL_SECONDS)
        return False
    except Exception:
        logger.exception("Failed to mark user %s absent for session %s.", user_id, session_id)
        return False


def get_present_user_ids(session_id) -> list[int]:
    """Everyone with at least one open connection to this session right
    now, for a freshly-connecting client's `presence.snapshot` — so it
    learns who's ALREADY here, not just who joins/leaves after it does.
    Best-effort: returns [] on any failure, same contract as
    get_missed_events() above.
    """
    client = _raw_redis_client(write=False)
    if client is None:
        return []
    try:
        raw = client.hgetall(_presence_key(session_id))
    except Exception:
        logger.exception("Failed to read presence for session %s.", session_id)
        return []
    user_ids = []
    for field, count in raw.items():
        try:
            field = field.decode() if isinstance(field, bytes) else field
            count = int(count)
        except (TypeError, ValueError):
            continue
        if count > 0:
            user_ids.append(int(field))
    return user_ids


def _connect_rate_key(user_id) -> str:
    return f"liveclass:ws_connect_rl:{user_id}"


def check_connect_rate_limit(user_id) -> bool:
    """Returns True if this connect attempt is allowed, False if `user_id`
    has hit `_WS_CONNECT_LIMIT` connects within the last
    `_WS_CONNECT_WINDOW_SECONDS`. Call this from consumers.py's connect()
    BEFORE the DB session/access lookup — a rate-limited attempt should
    cost one Redis round-trip, not one Redis round-trip plus one DB query.

    Fixed-window, not sliding: `INCR` on a key that `EXPIRE`s
    `_WS_CONNECT_WINDOW_SECONDS` after its first hit in a window. Only
    the INCR that creates the key sets the expiry (an INCR on an
    already-expiring key would keep pushing the window back forever,
    turning a 60s window into an unbounded one for anyone who stays just
    under the limit) — see the `new_count == 1` check below.

    Best-effort, same as every function in this file: any Redis error or
    a backend with no raw client degrades to True (allow) rather than
    raising or blocking the connection — see the module docstring's
    "WS-CONNECT RATE LIMITING" section for why failing open is the right
    default here, matching DRF's own cache-backed throttle behavior.
    """
    client = _raw_redis_client(write=True)
    if client is None:
        return True
    try:
        key = _connect_rate_key(user_id)
        new_count = client.incr(key)
        if new_count == 1:
            client.expire(key, _WS_CONNECT_WINDOW_SECONDS)
        return new_count <= _WS_CONNECT_LIMIT
    except Exception:
        logger.exception("Failed to check WS connect rate limit for user %s — allowing connection.", user_id)
        return True


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
        "recording.started", "recording.stopped", "recording.ready",
        "participant.kicked", "presence.joined", "presence.left".
    payload: JSON-serializable dict — already-serialized data (the same
        serializer output the REST response itself returned), so a
        listening client can update its UI without an extra fetch.

    Also appends this event to the session's short replay buffer (see
    module docstring, "MISSED-EVENT CATCH-UP") — one `ts` timestamp is
    generated here and used for BOTH the live group_send and the history
    entry, so a client that received this event live and one that only
    ever sees it via catch-up replay agree on the same `ts` to remember
    as their new "since" watermark.
    """
    ts = time.time()
    # Recorded regardless of whether the live group_send below succeeds —
    # a channel-layer hiccup shouldn't also cost a reconnecting client
    # their catch-up copy of this same event.
    _record_history(session_id, event_type, payload, ts)

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
                "ts": ts,
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


def _user_group_name(user_id) -> str:
    return f"user.{user_id}"


def broadcast_to_user(user_id, event_type: str, payload: dict) -> bool:
    """Per-user counterpart to broadcast_to_session() above — pushes to
    ONE user's own WebSocket channel (see UserConsumer in consumers.py)
    instead of everyone in a live session's group. This is what
    views.py's `_safe_broadcast_to_user()` calls (join_request.created,
    join_request.decided, staff.added — see its call sites in
    ClassJoinRequestViewSet/ClassroomStaffViewSet) — those events aren't
    tied to any one live session at all (a join request can be raised
    with no session running), so a session-scoped group is the wrong
    shape and this function was the missing piece: `_safe_broadcast_to_
    user()` already existed and already called `from .realtime import
    broadcast_to_user`, but no such name was defined here, so every one
    of those calls silently no-op'd (caught by that function's own
    try/except) instead of actually pushing anything.

    Same best-effort contract as broadcast_to_session(): NEVER raises —
    the request that triggered this (accepting a join request, adding
    staff) must succeed even if the channel layer is down. Same envelope
    shape too ("event"/"payload"/"ts"), dispatched via a "user.event"
    message type to UserConsumer.user_event — the per-user mirror of
    "session.event" -> SessionConsumer.session_event, same Channels
    type->method-name convention. Group name "user.<id>" (dot, matching
    "session.<id>" above) — NOT "user:<id>", same ASCII-only constraint
    noted in the module docstring.

    Deliberately does NOT append to a replay/history buffer the way
    broadcast_to_session() does: UserConsumer.connect() has no `?since=`
    catch-up (nothing currently needs one — every event pushed through
    here already has a REST-backed source of truth a full `_load()`/
    reopen will re-derive correctly, which is exactly the fallback
    `_safe_broadcast_to_user()`'s callers already document), so a replay
    buffer here would just be unused complexity.

    event_type: dotted event name, e.g. "join_request.created",
        "join_request.decided", "staff.added".
    payload: JSON-serializable dict — same already-serialized shape the
        REST response for the triggering action used.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning(
            "No channel layer configured — dropping realtime event %r for user %s.",
            event_type, user_id,
        )
        return False
    try:
        async_to_sync(channel_layer.group_send)(
            _user_group_name(user_id),
            {
                "type": "user.event",  # dispatched to UserConsumer.user_event
                "event": event_type,
                "payload": payload,
                "ts": time.time(),
            },
        )
        return True
    except Exception:
        logger.exception(
            "Realtime broadcast failed (user=%s, event=%s) — the connected "
            "client, if any, will miss this push and rely on its next REST "
            "call/reopen.",
            user_id, event_type,
        )
        return False