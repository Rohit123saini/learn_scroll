"""
message/views/study_room.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .. import sticky_notes
from ..attendance_utils import compute_attendance_stats
from .. import study_room_ops   # X1 — client telemetry counters (separate from call_ops)
from .. import study_room_presence
from ..cache_utils import get_group_role_cached
from ..livekit_utils import (
    bounded_ttl,
    ensure_study_room,
    generate_study_room_token,
    livekit_configured,
    study_room_max_duration,
    study_room_room_metadata,
    study_room_token_ttl,
    token_meta,
)
from ..models import (
    Conversation,
    ConversationType,
    StudyRoomAttendance,
    StudyRoomNote,
    StudyRoomParticipant,
    StudyRoomSession,
    StudyRoomState,
    StudyRoomStateSnapshot,
)
from .. import study_room_events as sr_events
from .. import study_room_roles as sr_roles
from .. import study_room_safety as sr_safety   # S8 — rules gate / minor masking / block
from ..minor_safety import MINOR_STUDY_ROOM_BLOCKED_MESSAGE, dm_pair_blocked_for_minor
from .. import study_room_state_ops as state_ops
from ..study_room_session import resolve_session
from ..throttles import (
    LiveKitTokenRefreshThrottle,
    StudyRoomActivityThrottle,
    StudyRoomJoinThrottle,
    StudyRoomStateThrottle,
    StudyRoomTelemetryThrottle,
)
from ..user_display import get_display_name, get_profile_photo_url

from ._common import LIVEKIT_WS_URL, User, is_blocked_pair

logger = logging.getLogger(__name__)


# 🔥 C13 — study-room token chain: "is user ka is room me continuous session kab shuru hua".
# Join isse reset karta hai; `refresh-token` isi se hard cap (`LIVEKIT_STUDY_ROOM_MAX_SECONDS`) nikalta hai.
# 🔥 S2 leftover — ab ye DB me hai (`StudyRoomParticipant.chain_started_at`), cache me nahi: cache flush /
# per-worker LocMem se hard cap reset nahi hota. Cache sirf legacy rooms (koi `StudyRoomSession` row nahi)
# ka fallback hai; wahan evict ho jaye to chain naye sire se shuru (fail-open, user ko drop nahi).
def _chain_key(room_name, user_id):
    return f"studyroom:chain_start:{room_name}:{user_id}"


def _chain_ttl_seconds():
    return int(study_room_max_duration().total_seconds()) + 3600


def _set_chain_start(room_name, user_id, started_at, session=None):
    """DB row (`session` + user) me likho; row/session na ho to legacy cache fallback."""
    if session is not None:
        try:
            updated = StudyRoomParticipant.objects.filter(session=session, user_id=user_id).update(
                chain_started_at=started_at,
            )
            if updated:
                return
        except Exception:
            logger.exception("study room chain start db set failed room=%s", room_name)
    try:
        cache.set(_chain_key(room_name, user_id), started_at.timestamp(), timeout=_chain_ttl_seconds())
    except Exception:
        logger.exception("study room chain start cache set failed room=%s", room_name)


def _get_chain_start(room_name, user_id, session=None):
    if session is not None:
        try:
            ts = (
                StudyRoomParticipant.objects.filter(session=session, user_id=user_id)
                .values_list('chain_started_at', flat=True).first()
            )
        except Exception:
            logger.exception("study room chain start db get failed room=%s", room_name)
            ts = None
        if ts is not None:
            return ts
    try:
        ts = cache.get(_chain_key(room_name, user_id))
    except Exception:
        ts = None
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), tz=dt_timezone.utc)


# 🔥 S1 — join aur refresh dono ke liye ek hi "is user ko is study room me aane dena hai?" rule.
# Pehle `StudyRoomJoinView` sirf `memberships__user=request.user` dekhta tha (left_at ignore), isliye
# group chhodne / remove / ban hone ke baad bhi LiveKit token mil jata tha, aur DM me block ya minor
# rule ka koi check nahi tha. Return: None (allowed) ya (detail, code, http_status).
def _study_room_access_denied(conversation, user):
    if conversation.type == ConversationType.GROUP:
        group = getattr(conversation, 'group_detail', None)
        if group:
            # Ban => `left_at` bhi set hota hai (services.py), phir bhi cached role se dobara check:
            # koi stale/race path ho to bhi banned user ko token na mile.
            member = get_group_role_cached(group.id, user.id)
            if member is not None and member.get("is_banned"):
                return ("Aap is group me ban hain.", "banned", 403)
            # 🔥 S8 — public group ke room me aane se pehle (aur rules badalne ke baad refresh par bhi) group ke
            # rules accept karne padte hain. Group admin / mod exempt; private group me rule nahi lagta.
            gate = sr_safety.rules_gate_denial(group, user)
            if gate:
                return gate
            # 🔥 S4 — `study_room_permission` ab yahan 403 nahi deta: `admins_only` group me baaki members
            # room me VIEWER role se aate hain (dekh / sun sakte hain, mic-camera-screen-board nahi) —
            # `study_room_roles.default_role`. Pehle wo room me aa hi nahi paate the.
        return None

    # 1-1 (DM) study room: dusra ACTIVE participant dhundo (left_at set wale ignore).
    other_ids = list(
        conversation.memberships.filter(left_at__isnull=True)
        .exclude(user_id=user.id).values_list('user_id', flat=True)
    )
    for other_id in other_ids:
        if is_blocked_pair(user.id, other_id):
            return ("Block hone ki wajah se study room join nahi ho sakta.", "blocked", 403)
    if other_ids:
        for other in User.objects.filter(id__in=other_ids):
            if dm_pair_blocked_for_minor(user, other):
                return (MINOR_STUDY_ROOM_BLOCKED_MESSAGE, "minor_safety", 403)
    return None


# ======================================================================
# STUDY ROOM
# ------------------------------------------------------------
# 🔥 NAYA — Google Meet-style persistent room. `CallInitiateView`/
# `CallActionView` se ALAG hai: koi `CallSession` nahi banta, koi
# ringing/accept-reject nahi, koi push notification nahi. Room-name
# seedha `conversation_id` se derive hota hai (`study_<conversation_id>`)
# taaki jo bhi study room khole wo sabke saath ek hi persistent LiveKit
# room me mile — jaise Meet link kholte hi ho jaata hai.
# ======================================================================
class StudyRoomJoinView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomJoinThrottle]   # X2

    def post(self, request, conversation_id):
        # 🔥 S1 — sirf ACTIVE member (`left_at IS NULL`). Dono conditions ek hi `.filter()` me hain,
        # isliye same membership row pe lagti hain (left/removed user ko 404, token nahi).
        conversation = Conversation.objects.filter(
            id=conversation_id,
            memberships__user=request.user,
            memberships__left_at__isnull=True,
        ).first()
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        # 🔥 NAYA — group ka study_room_permission enforce karo.
        # 🔥 S1 — + ban check, DM me block (`is_blocked_pair`) aur minor-safety (`minor_safety.py`).
        denied = _study_room_access_denied(conversation, request.user)
        if denied:
            detail, code, http_status = denied
            return Response({"detail": detail, "code": code}, status=http_status)

        # 🔥 FIX — `call_api_service.dart.joinStudyRoom(newSession: true)`
        # sends `{"new_session": true}` specifically so the "Study Room"
        # icon always gets a brand-new LiveKit room + fresh whiteboard,
        # while the invite-card "join" flow sends `new_session: false` to
        # land in whatever session is already running. This view used to
        # ignore `request.data` entirely and always derive the same
        # `study_<conversation_id>` room name, so "new session" silently
        # behaved identically to "join existing" — nobody ever actually
        # got a fresh room.
        #
        # A new session gets its own room name (`study_<conversation_id>_
        # <suffix>`), a genuinely separate LiveKit room from whatever was
        # running before. (S2: the "current session" pointer used to live in
        # cache with a 7 day TTL; it is now a `StudyRoomSession` DB row — see
        # `study_room_session.py` — so a cache flush / per-worker LocMem can no
        # longer split people into different rooms.)
        # 🔥 C4 — config missing ho to session reset / notes purge se PEHLE 503.
        if not livekit_configured():
            logger.error("LiveKit not configured — study room join refused conv=%s", conversation_id)
            return Response(
                {"detail": "Study room abhi available nahi hai. Thodi der baad try karo."},
                status=503,
            )

        # 🔥 S2 — current session ab DB (`StudyRoomSession`) me hai, cache me nahi: Redis flush / LocMem
        # multi-worker me bhi sab ek hi room me aate hain. `new_session` = purana band + naya row.
        new_session = bool(request.data.get('new_session', False))
        # 🔥 S4 — public group ke naye session me waiting room default on (`new_session_defaults`).
        session, started_new = resolve_session(
            conversation, request.user, new_session=new_session,
            extra_defaults=sr_roles.new_session_defaults(conversation),
        )
        room_name = session.room_name
        if started_new:
            # 🔥 S4 — LiveKit room max_participants (hard cap) + settings metadata ke saath pehle se banao.
            # Best-effort (LiveKit down ho to join phir bhi chalta hai; DB cap `evaluate_join` me hai).
            ensure_study_room(
                room_name, sr_roles.effective_max_participants(session),
                study_room_room_metadata(sr_roles.room_settings_payload(session)),
            )
        if new_session and started_new:
            # 🔥 NAYA — fresh session = fresh board, sticky notes samet.
            # Best-effort: purge fail hone se join block nahi hona chahiye.
            try:
                sticky_notes.purge_room_notes(conversation.id)
            except Exception:
                logger.exception(f"purge_room_notes failed conv={conversation_id}")

        # 🔥 S4 — lock / waiting room / max participants / removed — token dene se PEHLE.
        # Host / co-host in teeno se exempt hain (`study_room_roles.evaluate_join`).
        decision = sr_roles.evaluate_join(conversation, session, request.user)
        if decision.kind == 'denied':
            detail, code, http_status = decision.denial
            return Response({"detail": detail, "code": code, "room_settings": sr_roles.room_settings_payload(session)},
                            status=http_status)
        if decision.kind == 'waiting':
            # Managers ko ek baar (30s me) "X andar aana chahta hai" — client har kuch sec poll karta hai.
            try:
                if cache.add(f"studyroom:wait_notify:{session.id}:{request.user.id}", 1, timeout=30):
                    sr_events.broadcast(
                        conversation.id, sr_events.EVT_WAITING_REQUEST,
                        # S8 — push sab managers ko jaata hai (viewer-neutral) => public group ka minor masked;
                        # group admin/mod `room/` waiting list se real naam dekhte hain.
                        {"userId": str(request.user.id),
                         **_waiting_identity(conversation, request.user, request)},
                        manager_only=True,
                    )
            except Exception:
                logger.exception("waiting room notify failed conv=%s", conversation_id)
            return Response({
                "status": "waiting",
                "code": "waiting_room",
                "detail": "Host aapko andar aane dega — thoda wait karo.",
                "room_settings": sr_roles.room_settings_payload(session),
            }, status=202)
        role = decision.role
        participant_row = decision.row

        # 🔥 S8 — LiveKit token ka naam SAB ko dikhta hai: public group me minor ka naam masked (first name).
        user_name = sr_safety.token_display_name(conversation, request.user)
        # 🔥 FIX — study rooms are long-running Meet-style sessions, unlike
        # calls; give them a much longer-lived token (8h) so an active
        # session doesn't get silently disconnected when the default 2h
        # call-token TTL runs out. Frontend should still re-join/refresh on
        # reconnect for sessions that outlive even this.
        # 🔥 C13 — TTL settings-driven (default wahi 8h); join = naye continuous session ka start,
        # hard cap (default 12h) isi se. Token ka TTL cap se aage nahi jaata.
        joined_at = timezone.now()
        session_ends_at = joined_at + study_room_max_duration()
        token_ttl = bounded_ttl(study_room_token_ttl(), session_ends_at, now=joined_at)
        try:
            # 🔥 S4 — role-based grants (host / co_host / participant / viewer) + share_policy.
            token = generate_study_room_token(
                room_name=room_name,
                user_id=request.user.id,
                user_name=user_name,
                role=role,
                share_policy=session.share_policy,
                ttl=token_ttl,
            )
        except RuntimeError:
            # 🔥 C4 — LiveKit config missing: 500 nahi, clean 503
            logger.exception("LiveKit not configured — study room join refused conv=%s", conversation_id)
            return Response(
                {"detail": "Study room abhi available nahi hai. Thodi der baad try karo."},
                status=503,
            )

        _set_chain_start(room_name, request.user.id, joined_at, session=session)   # C13 (S2: DB)
        try:
            sr_roles.mark_joined(participant_row)   # S4 — last token issue
        except Exception:
            logger.exception("study room mark_joined failed conv=%s", conversation_id)

        # 🔥 NAYA — conversation ke saare members (naam + profile photo)
        # yahin se bhej dete hain, taaki join hote hi turant sabke naam/
        # photo dikh jayein — socket 'user_joined' handshake ka wait nahi
        # karna padta (wo sirf backup/late-joiner sync ke liye rehta hai).
        participants = []
        # 🔥 S4 — session ki participant rows ek hi query me (role override / hand / removed).
        rows_by_user = {
            r.user_id: r for r in StudyRoomParticipant.objects.filter(session=session)
        }
        # 🔥 S1 — left/removed members participants list me nahi dikhte.
        # 🔥 S8 — public group: minor ka naam / photo adult strangers se masked (group admin/mod aur doosre minors ko
        # real). Ek hi query (`minors_among`); `masked` flag + `masked_user_ids` client ko bhi diye jaate hain
        # (socket `user_joined` se aane wale naam ko wo isi se override karta hai).
        memberships = list(conversation.memberships.select_related('user').filter(left_at__isnull=True))
        masked_ids = sr_safety.masked_user_ids(request.user, conversation, [m.user_id for m in memberships])
        for membership in memberships:
            u = membership.user
            prow = rows_by_user.get(u.id)
            if prow is not None and prow.status in (
                StudyRoomParticipant.Status.REMOVED, StudyRoomParticipant.Status.DENIED,
                StudyRoomParticipant.Status.WAITING,
            ):
                continue   # removed / denied / waiting room wale room ki list me nahi
            person = sr_safety.person_fields(u, u.id in masked_ids, request=request)
            participants.append({
                "user_id": str(u.id),
                "display_name": person["display_name"],
                "avatar_url": person["avatar_url"],
                "masked": person["masked"],                                            # S8
                "role": sr_roles.effective_role(conversation, session, u.id, prow),   # S4
                "hand_raised": bool(prow and prow.hand_raised),                        # S4
            })

        # 🔥 NAYA — Feature 6: attendance log. Ek row = "is user ne is
        # conversation ke study room me is calendar-din join kiya" — same
        # din dobara join (disconnect-reconnect) `get_or_create` ki wajah
        # se koi duplicate row nahi banata (model's `UniqueConstraint`
        # se bhi doubly guarded). Best-effort: agar ye kabhi fail ho
        # (race condition / DB hiccup), study room join khud block nahi
        # hona chahiye — sirf streak thoda stale rahega.
        try:
            StudyRoomAttendance.objects.get_or_create(
                conversation=conversation,
                user=request.user,
                attended_date=timezone.localdate(),
                # S5 — presence (webhook) ON ho to is din ke minutes track hote hain aur streak "kam se kam N minute"
                # se chalti hai; OFF ho to purana behaviour (join hi kaafi).
                defaults={"session_id": room_name, "presence_tracked": study_room_presence.presence_enabled()},
            )
        except Exception:
            logger.exception(
                f"StudyRoomAttendance log failed user={request.user.id} conv={conversation_id}"
            )

        # 🔧 FIX — `study_room_call_manager`/`study_room_screen.dart` reads
        # `data['session_id']` to key class-transcript recording (Feature
        # 3), but this response never actually included that key — only
        # `room_name` (which IS the session id, per the model's own
        # docstring: "session_id: study room ka channel_name reuse karna
        # best hai"). Transcript recording was silently never starting
        # because of this one missing key. Exposing `room_name` as
        # `session_id` too (keeping `room_name` for anything else already
        # reading it) fixes this with no other behavior change.
        return Response({
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": token,
            "room_name": room_name,
            "session_id": room_name,
            "participants": participants,
            # 🔥 S4 — client UI (host controls, disabled buttons) isi se chalta hai.
            "role": role,
            "permissions": sr_roles.grants_for(role, session.share_policy),
            "room_settings": sr_roles.room_settings_payload(session),
            "hand_raised": bool(participant_row.hand_raised),
            # 🔥 S8 — client hints: minor ka camera default OFF (public group), apni identity masked hai ya nahi,
            # kin users ko block kiya / jinhone block kiya (tiles + events hide), rules version.
            "privacy": sr_safety.room_privacy_hints(conversation, request.user),
            "masked_user_ids": sorted(str(i) for i in masked_ids),
            "blocked_user_ids": sorted(str(i) for i in sr_safety.block_pair_ids(request.user.id)),
            "rules_version": getattr(sr_safety.group_for(conversation), 'room_rules_version', None),
            **token_meta(token_ttl, session_ends_at=session_ends_at, now=joined_at),   # C13
        })


def _waiting_identity(conversation, user, request):
    """Waiting-room push ka naam / photo — public group ka minor masked (viewer-neutral)."""
    fields = sr_safety.person_fields(user, sr_safety.masked_for_managers(conversation, user), request=request)
    return {"displayName": fields["display_name"], "avatarUrl": fields["avatar_url"], "masked": fields["masked"]}


class StudyRoomTokenRefreshView(APIView):
    """
    🔥 C13 — POST /message/study-room/<conversation_id>/refresh-token/   {"room_name": "<session_id>"}

    Study room ke chalte session me LiveKit token expire hone se pehle naya token (client silent
    refresh). `room_name` = join response ka `room_name`/`session_id` (study_<conv>[_<suffix>]).
    Naya session / state / notes/ attendance side-effects NAHI — sirf token.

    Response: {livekit_url, livekit_token, room_name, session_id, token_expires_at,
               token_ttl_seconds, session_ends_at, warn_before_seconds, server_time}
    Errors (`{detail, code}`): 400 `invalid_room`; 404 conversation; 403 not a member / group
    permission / `rules_not_accepted` (S8: public group ke rules naye version par dobara accept karo);
    410 `max_duration_reached`; 503 LiveKit config missing.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [LiveKitTokenRefreshThrottle]

    def post(self, request, conversation_id):
        # Join se strict: refresh sirf abhi-active member ke liye (left_at set => 404).
        conversation = Conversation.objects.filter(
            id=conversation_id,
            memberships__user=request.user,
            memberships__left_at__isnull=True,
        ).first()
        if not conversation:
            return Response({"detail": "Conversation not found", "code": "not_found"}, status=404)

        # 🔥 S1 — join jaisa hi rule (ban / block / minor-safety), taaki block ya rule badalne ke baad
        # chalte session ka token refresh bhi na mile.
        denied = _study_room_access_denied(conversation, request.user)
        if denied:
            detail, code, http_status = denied
            return Response({"detail": detail, "code": code}, status=http_status)

        room_name = str(request.data.get('room_name') or '').strip()
        # Sirf isi conversation ke study room ka token milega: study_<conv> ya study_<conv>_<12 hex>.
        if not re.fullmatch(rf"study_{re.escape(str(conversation_id))}(?:_[0-9a-f]{{12}})?", room_name):
            return Response({"detail": "room_name galat hai.", "code": "invalid_room"}, status=400)

        if not livekit_configured():
            return Response(
                {"detail": "Study room abhi available nahi hai. Thodi der baad try karo.", "code": "unavailable"},
                status=503,
            )

        # 🔥 S4 — removed / denied / waiting-room wale ko refresh se bhi token nahi; role + share_policy
        # fresh DB se (host ne role / share policy badli ho to naya token wahi rights deta hai).
        session = StudyRoomSession.all_objects.filter(conversation=conversation, room_name=room_name).first()
        # 🔥 S2 leftover — host ne "End Session for Everyone" kiya (ya naya session shuru hua) to purane room ka
        # token ab refresh nahi hota: 410 `session_ended` (client keeper 410 par retry band karta hai).
        if session is not None and session.ended_at is not None:
            return Response(
                {"detail": "Ye study room session khatam ho chuka hai. Dobara join karo.", "code": "session_ended"},
                status=410,
            )
        role, share_policy, sr_denied = sr_roles.token_context(conversation, session, request.user)
        if sr_denied:
            detail, code, http_status = sr_denied
            return Response({"detail": detail, "code": code}, status=http_status)

        now = timezone.now()
        chain_start = _get_chain_start(room_name, request.user.id, session=session)
        if chain_start is None:
            # legacy room / row me chain nahi — chain ab se; user ko drop nahi karna.
            chain_start = now
            _set_chain_start(room_name, request.user.id, chain_start, session=session)
        session_ends_at = chain_start + study_room_max_duration()
        if now >= session_ends_at:
            return Response(
                {"detail": "Study room session ki maximum duration poori ho gayi. Dobara join karo.",
                 "code": "max_duration_reached"},
                status=410,
            )

        token_ttl = bounded_ttl(study_room_token_ttl(), session_ends_at, now=now)
        try:
            token = generate_study_room_token(
                room_name=room_name,
                user_id=request.user.id,
                user_name=sr_safety.token_display_name(conversation, request.user),   # S8 — minor masked
                role=role,
                share_policy=share_policy,
                ttl=token_ttl,
            )
        except RuntimeError:
            logger.exception("LiveKit not configured — study room token refresh refused conv=%s", conversation_id)
            return Response(
                {"detail": "Study room abhi available nahi hai. Thodi der baad try karo.", "code": "unavailable"},
                status=503,
            )
        return Response({
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": token,
            "room_name": room_name,
            "session_id": room_name,
            "role": role,
            "permissions": sr_roles.grants_for(role, share_policy),
            **token_meta(token_ttl, session_ends_at=session_ends_at, now=now),
        })


def _truthy(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y')


def _state_access(request, conversation_id, write=False):
    """
    (access, error_response). Active member. S4: `study_room_permission` ab role se juda hai —
      * `write=False` (GET / history / restore-list): `admins_only` group ke viewers bhi dekh sakte hain;
      * `write=True` (PUT board): role ke hisaab se (`can_edit_board`) — viewer nahi, `share_policy=host_only`
        me participant nahi; removed / waiting room wale ko 403.
    """
    access = sticky_notes.check_room_access(conversation_id, request.user, write=False)
    if access.conversation is None:
        return None, Response({"detail": "Conversation not found", "code": "not_found"}, status=404)
    if not access.allowed:
        return None, Response({"detail": "Study room access allowed nahi hai.", "code": "forbidden"}, status=403)
    role, policy = sr_roles.role_and_policy(access.conversation, request.user.id)
    if role is None:
        return None, Response({"detail": "Aap is study room me nahi hain.", "code": "removed"}, status=403)
    if write and not sr_roles.can_edit_board(role, policy):
        code = 'view_only' if role == sr_roles.Role.VIEWER else 'host_only_share'
        msg = ("Aap sirf dekh sakte hain." if code == 'view_only'
               else "Host ne whiteboard sirf host / co-hosts ke liye rakha hai.")
        return None, Response({"detail": msg, "code": code}, status=403)
    return access, None


def _with_etag(response, version):
    response["ETag"] = f'"{version}"'
    return response


def _parse_base_version(request):
    """`base_version` (body) ya `If-Match` header. (value|None, error_response|None)."""
    raw = None
    data = request.data if hasattr(request.data, 'get') else {}
    if data.get('base_version') is not None:
        raw = data.get('base_version')
    elif request.META.get('HTTP_IF_MATCH'):
        raw = request.META['HTTP_IF_MATCH'].strip()
        if raw.startswith('W/'):
            raw = raw[2:]
        raw = raw.strip('"')
    if raw is None or raw == '':
        return None, None
    try:
        value = int(raw)
        if value < 0:
            raise ValueError
        return value, None
    except (TypeError, ValueError):
        return None, Response({"detail": "base_version galat hai.", "code": "invalid_version"}, status=400)


class StudyRoomStateView(APIView):
    """
    GET    -> poora saved whiteboard (`{"pages": [...], "version": N}`) + `ETag: "N"`. Kabhi save nahi hua to
              `{"pages": [], "version": 0}` (404 nahi).
    PUT    -> board save. S3: `base_version` (body) ya `If-Match` header zaroori (board pehle se saved ho to):
              mismatch => 409 `version_conflict` (+ current `version` / `state`); version missing => 428
              `version_required`; payload > STUDY_ROOM_STATE_MAX_BYTES (default 2 MB) => 413. Success:
              `{"detail": "saved", "version": N+1}` + ETag. Overwrite se pehle purana board history me (rate-limited).
    DELETE -> board + sticky notes clear. S3: sirf host / moderator (group admin/mod, session host; DM me dono),
              `?confirm=true` (ya body `confirm`) zaroori, aur clear se pehle snapshot — undo window
              (STUDY_ROOM_UNDO_WINDOW_SECONDS, default 24h) me `state/restore/` se wapas.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomStateThrottle]   # X2

    def get(self, request, conversation_id):
        access, err = _state_access(request, conversation_id)
        if err:
            return err
        row = StudyRoomState.objects.filter(conversation=access.conversation).first()
        version = row.version if row else 0
        body = dict(row.state) if row and isinstance(row.state, dict) and row.state else {"pages": []}
        body["version"] = version
        return _with_etag(Response(body), version)

    def put(self, request, conversation_id):
        access, err = _state_access(request, conversation_id, write=True)
        if err:
            return err
        conversation = access.conversation
        limit = state_ops.max_state_bytes()

        # Body parse hone se pehle declared size dekho (unbounded JSON).
        declared = request.META.get('CONTENT_LENGTH')
        try:
            if declared and int(declared) > limit:
                return Response({"detail": "Board bahut bada hai.", "code": "payload_too_large", "max_bytes": limit}, status=413)
        except ValueError:
            pass

        payload = request.data
        if not hasattr(payload, 'items') or not isinstance(payload.get('pages'), list):
            return Response({"detail": "`pages` list zaroori hai.", "code": "invalid_state"}, status=400)
        if len(payload['pages']) > state_ops.max_pages():
            return Response({"detail": "Pages bahut zyada hain.", "code": "too_many_pages"}, status=400)
        base_version, verr = _parse_base_version(request)
        if verr:
            return verr
        # Meta keys board ke andar store nahi hote.
        state = {k: v for k, v in dict(payload).items() if k not in ('version', 'base_version')}
        # chunked body (CONTENT_LENGTH nahi) ke liye parse ke baad ka size.
        if state_ops._state_size(state) > limit:
            return Response({"detail": "Board bahut bada hai.", "code": "payload_too_large", "max_bytes": limit}, status=413)

        try:
            with transaction.atomic():
                row = StudyRoomState.objects.select_for_update().filter(conversation=conversation).first()
                current = row.version if row else 0
                if base_version is None and current > 0 and state_ops.require_version():
                    return _with_etag(Response({
                        "detail": "Board ka version bhejo (`base_version` ya `If-Match`). Pehle GET se latest lo.",
                        "code": "version_required", "version": current,
                    }, status=428), current)
                if base_version is not None and base_version != current:
                    cur_state = dict(row.state) if row and isinstance(row.state, dict) and row.state else {"pages": []}
                    return _with_etag(Response({
                        "detail": "Board kisi aur ne badal diya hai.",
                        "code": "version_conflict", "version": current, "state": cur_state,
                    }, status=409), current)
                if row is None:
                    new_version = 1
                    StudyRoomState.objects.create(
                        conversation=conversation, state=state, version=new_version, updated_by=request.user,
                    )
                else:
                    # T2.4 - snapshot best-effort + ALAG savepoint: fail hone par (e.g. "database is locked") sirf
                    # savepoint rollback hota hai, bahar ka atomic block toot'ta nahi (pehle TransactionManagementError).
                    try:
                        with transaction.atomic():
                            state_ops.maybe_snapshot_before_overwrite(conversation, row, request.user)
                    except Exception:
                        logger.exception(f"study room snapshot failed conv={conversation_id}")
                    new_version = current + 1
                    row.state = state
                    row.version = new_version
                    row.updated_by = request.user
                    row.save(update_fields=['state', 'version', 'updated_by', 'updated_at'])
        except IntegrityError:
            # Do request ek saath pehli baar create karne gaye — haarne wale ko conflict.
            fresh = StudyRoomState.objects.filter(conversation=conversation).first()
            current = fresh.version if fresh else 0
            return _with_etag(Response({
                "detail": "Board kisi aur ne badal diya hai.", "code": "version_conflict", "version": current,
            }, status=409), current)
        return _with_etag(Response({"detail": "saved", "version": new_version}), new_version)

    # 🔥 NAYA — `endStudyRoomState()` (frontend) is par depend karta hai
    # taaki room close hote hi poora whiteboard clear ho jaaye.
    # 🔥 S3 — host/mod only + confirm + snapshot (undo).
    def delete(self, request, conversation_id):
        access, err = _state_access(request, conversation_id)
        if err:
            return err
        conversation = access.conversation
        if not state_ops.can_manage_room(conversation, request.user):
            return Response(
                {"detail": "Board sirf host ya moderator clear kar sakte hain.", "code": "host_required"}, status=403,
            )
        data = request.data if hasattr(request.data, 'get') else {}
        if not (_truthy(request.query_params.get('confirm')) or _truthy(data.get('confirm'))):
            return Response(
                {"detail": "Board clear karne ke liye confirm=true bhejo.", "code": "confirm_required"}, status=400,
            )

        snapshot = None
        with transaction.atomic():
            sticky_notes._lock_room(conversation.id)
            row = StudyRoomState.objects.select_for_update().filter(conversation=conversation).first()
            notes = state_ops.snapshot_notes(conversation.id)
            has_board = bool(row and isinstance(row.state, dict) and row.state.get('pages'))
            if has_board or notes:
                snapshot = state_ops.create_snapshot(
                    conversation, request.user, state_ops.REASON_DELETE,
                    row.state if row else {}, row.version if row else 0, notes=notes,
                )
            new_version = (row.version if row else 0) + 1
            if row is None:
                StudyRoomState.objects.create(
                    conversation=conversation, state={}, version=new_version, updated_by=request.user,
                )
            else:
                row.state = {}
                row.version = new_version
                row.updated_by = request.user
                row.save(update_fields=['state', 'version', 'updated_by', 'updated_at'])
            # 🔥 NAYA — session end => sticky notes bhi (tombstones samet) saaf.
            sticky_notes.purge_room_notes(conversation.id)
            # 🔥 S2 leftover — DELETE = "End Session for Everyone": DB session bhi band (ended_at), taaki agla join naya
            # room banaye aur purane room ka token refresh (410 session_ended) na ho. Idempotent (already ended => 0 rows).
            StudyRoomSession.objects.filter(conversation=conversation, ended_at__isnull=True).update(
                ended_at=timezone.now(),
            )

        body = {"detail": "cleared", "version": new_version, "snapshot_id": None, "undo_until": None}
        if snapshot is not None:
            body["snapshot_id"] = str(snapshot.id)
            body["undo_until"] = (snapshot.created_at + state_ops.undo_window()).isoformat()
        return _with_etag(Response(body), new_version)


class StudyRoomStateHistoryView(APIView):
    """
    GET /message/study-room/<conversation_id>/state/history/   (S3)
    Naye se purane snapshots (metadata only, board content nahi): id, version, reason, created_at,
    created_by, size_bytes, has_notes, restorable. Host/moderator only.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        access, err = _state_access(request, conversation_id)
        if err:
            return err
        if not state_ops.can_manage_room(access.conversation, request.user):
            return Response({"detail": "Sirf host ya moderator.", "code": "host_required"}, status=403)
        now = timezone.now()
        snaps = StudyRoomStateSnapshot.objects.filter(conversation=access.conversation).order_by('-created_at')[:50]
        return Response({
            "snapshots": [{
                "id": str(sn.id),
                "version": sn.version,
                "reason": sn.reason,
                "created_at": sn.created_at.isoformat(),
                "created_by": str(sn.created_by_id) if sn.created_by_id else None,
                "size_bytes": sn.size_bytes,
                "has_notes": sn.notes is not None,
                "restorable": state_ops.snapshot_restorable(sn, now),
            } for sn in snaps],
            "undo_window_seconds": int(state_ops.undo_window().total_seconds()),
        })


class StudyRoomStateRestoreView(APIView):
    """
    POST /message/study-room/<conversation_id>/state/restore/   {"snapshot_id": "<uuid>"}   (S3)
    Snapshot se board (+ snapshot me notes ho to sticky notes) wapas. Host/moderator only. Pehle current board ka
    backup snapshot banta hai (restore bhi undo ho sakta hai). 404 snapshot; 410 `undo_expired`.
    Response: {"detail": "restored", "version": N, "notes_restored": k}.
    Restore ke baad room ke sabhi sockets ko `board_restored` study_room_event jaata hai (client board + notes GET).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, conversation_id):
        access, err = _state_access(request, conversation_id)
        if err:
            return err
        conversation = access.conversation
        if not state_ops.can_manage_room(conversation, request.user):
            return Response({"detail": "Sirf host ya moderator.", "code": "host_required"}, status=403)
        try:
            snapshot_id = uuid.UUID(str(request.data.get('snapshot_id')))
        except (ValueError, TypeError, AttributeError):
            return Response({"detail": "snapshot_id galat hai.", "code": "invalid_snapshot"}, status=400)
        snapshot = StudyRoomStateSnapshot.objects.filter(id=snapshot_id, conversation=conversation).first()
        if snapshot is None:
            return Response({"detail": "Snapshot nahi mila.", "code": "not_found"}, status=404)
        if not state_ops.snapshot_restorable(snapshot):
            return Response({"detail": "Undo window khatam ho gayi.", "code": "undo_expired"}, status=410)
        new_version, notes_restored = state_ops.restore_snapshot(conversation, snapshot, request.user)
        # 🔥 S3 leftover — restore ke baad baaki connected clients ko board + notes dobara GET karne ka signal
        # (pehle WS broadcast nahi tha, unka board stale rehta tha). Best-effort.
        sr_events.broadcast(
            conversation.id, sr_events.EVT_BOARD_RESTORED,
            {'version': new_version, 'byUserId': str(request.user.id), 'notesRestored': notes_restored},
        )
        return _with_etag(
            Response({"detail": "restored", "version": new_version, "notes_restored": notes_restored}), new_version,
        )


class StudyRoomNotesView(APIView):
    """
    GET /message/study-room/<conversation_id>/notes/[?page_id=<id>]

    Room khulne / rejoin / socket-reconnect pe saare (non-deleted) sticky
    notes wapas deta hai — z-order me (neeche se upar). Writes REST se nahi,
    WebSocket (`note_*` study_room_event) se hote hain — see sticky_notes.py.

    Response: {
        "notes": [ {id, pageId, userId, x, y, width, height, color, text,
                    zIndex, updatedAt, fieldTs}, ... ],
        "count": 3,
        "server_time_ms": 1790000000000,   # client isse apna clock offset nikalta hai
        "limits": {"max_notes": 200, "max_text_length": 2000, ...}
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        # S4 — sirf padhna: `admins_only` group ke viewers bhi notes dekh sakte hain; removed / waiting nahi.
        access = sticky_notes.check_room_access(conversation_id, request.user, write=False)
        if access.conversation is None:
            return Response({"detail": "Conversation not found"}, status=404)
        if not access.allowed:
            return Response({"detail": "Study room access allowed nahi hai."}, status=403)
        sr_role, _sr_policy = sr_roles.role_and_policy(access.conversation, request.user.id)
        if sr_role is None:
            return Response({"detail": "Aap is study room me nahi hain.", "code": "removed"}, status=403)

        # Deploy se pehle ke snapshot-JSON notes ek baar rows me aa jaate hain.
        try:
            sticky_notes.import_legacy_notes(access.conversation)
        except Exception:
            logger.exception(f"import_legacy_notes failed conv={conversation_id}")

        qs = StudyRoomNote.objects.filter(room=access.conversation)
        page_id = request.query_params.get('page_id')
        if page_id:
            qs = qs.filter(page_id=page_id)
        notes = [sticky_notes.note_to_wire(n) for n in qs]
        return Response({
            "notes": notes,
            "count": len(notes),
            "server_time_ms": sticky_notes.server_time_ms(),
            "limits": {
                "max_notes": sticky_notes.MAX_NOTES_PER_ROOM,
                "max_text_length": sticky_notes.MAX_TEXT_LENGTH,
                "min_width": sticky_notes.MIN_WIDTH, "max_width": sticky_notes.MAX_WIDTH,
                "min_height": sticky_notes.MIN_HEIGHT, "max_height": sticky_notes.MAX_HEIGHT,
            },
        })


# ======================================================================
# 🔥 NAYA — Feature 6: attendance / consistency streak
# ------------------------------------------------------------
# `StudyRoomAttendance` rows are logged in `StudyRoomJoinView.post()`
# above, one per (conversation, user, calendar day). Streak = how many
# CONSECUTIVE calendar days (ending today or yesterday — see below) this
# user has at least one attendance row for, in this conversation.
# ======================================================================
class StudyRoomStreakView(APIView):
    """
    GET /message/study-room/<conversation_id>/streak/
    Response: {
        "current_streak": 7,
        "longest_streak": 12,
        "total_classes_attended": 34,
        "last_attended": "2026-09-04"   (ISO date, or null if never)
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        # 🔥 S1 leftover — sirf ACTIVE member (left / removed user apni stats bhi nahi dekhta). Dono conditions ek hi
        # `.filter()` me => same membership row.
        conversation = Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user, memberships__left_at__isnull=True,
        ).first()
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        # 🔧 Refactor (Parent Mode session) — calc moved to
        # `attendance_utils.compute_attendance_stats` so `ParentDashboardView`
        # (views_parent.py) can reuse the exact same logic instead of a
        # second copy. Response shape unchanged.
        return Response(compute_attendance_stats(conversation, request.user))


# ======================================================================
# 🔥 X1 — STUDY ROOM OBSERVABILITY (message/study_room_ops.py; separate counters from call_ops)
# ======================================================================
def _telemetry_truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


class StudyRoomTelemetryView(APIView):
    """
    POST /message/study-room/<conversation_id>/telemetry/    (ACTIVE member of that conversation only)

      {"kind": "connect", "attempt": "k3j2h4g5f6", "ok": true,  "setup_ms": 2100}
      {"kind": "connect", "attempt": "k3j2h4g5f6", "ok": false, "reason": "ice_failed"}   # reason in call_ops.FAIL_REASONS
      {"kind": "summary", "attempt": "k3j2h4g5f6", "reconnects": 2, "reconnect_ok": 2, "reconnect_fail": 0, "duration_s": 1800}

    `attempt` = client-made id of ONE join (6-64 chars of [A-Za-z0-9_-]). One `connect` and one `summary` are counted per
    (conversation, user, attempt); a retry / double send answers `{"counted": false}`. Values are clamped, nothing
    free-text is stored, no DB write. Errors: 400 `invalid_kind` / `invalid_attempt`; 404 `not_found` (not an active member).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomTelemetryThrottle]

    def post(self, request, conversation_id):
        data = request.data if hasattr(request.data, 'get') else {}
        kind = str(data.get('kind') or '').strip().lower()
        if kind not in ('connect', 'summary'):
            return Response({"detail": "'kind' must be 'connect' or 'summary'.", "code": "invalid_kind"}, status=400)
        attempt = study_room_ops.clean_attempt(data.get('attempt'))
        if attempt is None:
            return Response({"detail": "'attempt' is missing or invalid.", "code": "invalid_attempt"}, status=400)
        is_member = Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user, memberships__left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"detail": "Conversation not found", "code": "not_found"}, status=404)

        if kind == 'connect':
            counted = study_room_ops.record_connect(
                conversation_id, request.user.id, attempt,
                ok=_telemetry_truthy(data.get('ok')), setup_ms=data.get('setup_ms'), reason=data.get('reason'),
            )
        else:
            counted = study_room_ops.record_summary(
                conversation_id, request.user.id, attempt,
                reconnects=data.get('reconnects'), reconnect_ok=data.get('reconnect_ok'),
                reconnect_fail=data.get('reconnect_fail'), duration_s=data.get('duration_s'),
            )
        return Response({"counted": bool(counted)})


class StudyRoomActivityView(APIView):
    """
    🔥 S5 leftover — POST /message/study-room/<conversation_id>/activity/    (ACTIVE member only, body ignore)

    Client active hone par (touch / mic / camera / screen share) ~har 60s bhejta hai; idle baithe user ka time study-time /
    streak minutes me nahi jaata (`study_room_presence.record_activity`). Response `{"tracked": bool}` — False: abhi koi open
    presence interval nahi (webhook late / presence OFF) — client chup-chaap dobara bhejta rahe. Errors: 404 `not_found`.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomActivityThrottle]

    def post(self, request, conversation_id):
        is_member = Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user, memberships__left_at__isnull=True,
        ).exists()
        if not is_member:
            return Response({"detail": "Conversation not found", "code": "not_found"}, status=404)
        result = study_room_presence.record_activity(conversation_id, request.user.id)
        return Response({"tracked": result is not None})


from .calls import CallOpsAPIView  # noqa: E402  (same staff / scrape-token access rules as GET /message/calls/ops/)


class StudyRoomOpsAPIView(CallOpsAPIView):
    """
    GET /message/study-room/ops/?minutes=15     -> JSON (connect-success %, setup ms, reconnects, session length, reasons)
    GET /message/study-room/ops/?format=prom    -> Prometheus text (learnscroll_studyroom_*)

    Access identical to CallOpsAPIView (staff JWT or `Authorization: Bearer <CALL_OPS_SCRAPE_TOKEN>`). Numbers only.
    """

    def get(self, request):
        if not self._allowed(request):
            return Response({"success": False, "message": "Staff only."}, status=403)
        if request.query_params.get('format') == 'prom':
            return HttpResponse(study_room_ops.prometheus_text(), content_type='text/plain; version=0.0.4; charset=utf-8')
        try:
            minutes = min(1440, max(1, int(request.query_params.get('minutes', 15))))
        except (TypeError, ValueError):
            minutes = 15
        return Response({"success": True, "data": study_room_ops.summarize_last(minutes)})
