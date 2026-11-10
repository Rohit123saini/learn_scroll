"""
message/views/study_room_controls.py

S4 — study room host / moderation controls (REST). Realtime push `study_room_events.broadcast` se
(`study_room_event` WS action), LiveKit server-side kaam `livekit_utils` (RoomService) se.

Endpoints (sab `/message/study-room/<conversation_id>/...`, active session zaroori):
  GET   room/       room settings + meri role/permissions + roster; manager ko waiting list + raised hands
  POST  settings/   (manager) is_locked, waiting_room_enabled, max_participants, share_policy
  POST  mute/       (manager) {user_id} ya {all: true}  — server-side mic mute (participant khud unmute kar sakta hai)
  POST  remove/     (manager) {user_id} — room se nikaalo + is session me wapas aane se roko
  POST  admit/      (manager) {user_id, action: admit|deny} ya {action: admit_all}  — waiting room
  POST  role/       (manager) {user_id, role: co_host|participant|viewer}
  POST  hand/       (member) {raised: bool}; manager {user_id} ya {all: true} se haath neeche karwa sakta hai
  POST  report/     (member) {user_id, reason, details, block?}  — StudyRoomReport (admin me review); `block: true` => saath me block
  GET   rules/      (member) group ke room rules (default + custom), version, accepted?   (S8)
  POST  rules/      (member) {version?}  — rules accept (version diya ho aur purana ho => 409 `rules_version_changed`)   (S8)
  POST  block/      (member) {user_id}   — room se block (dono taraf events / tiles hide)   (S8)
  DELETE block/     (member) {user_id}   — unblock   (S8)

Error body hamesha `{detail, code}` (study_room.py jaisa). LiveKit down ho to DB state phir bhi save hoti hai;
jahan LiveKit call hi kaam hai (mute) wahan 502 `livekit_unavailable`.
"""

import logging
import uuid
from datetime import timedelta

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .. import livekit_utils as lk
from .. import study_room_events as sr_events
from .. import study_room_roles as sr_roles
from .. import study_room_safety as sr_safety   # S8
from ..models import (
    Conversation,
    ConversationParticipant,
    StudyRoomParticipant,
    StudyRoomReport,
    StudyRoomSession,
)
from ..study_room_session import get_active_session
from ..throttles import (
    StudyRoomBlockThrottle,
    StudyRoomHandThrottle,
    StudyRoomModerationThrottle,
    StudyRoomReportThrottle,
    StudyRoomRulesThrottle,
)
from ..user_display import get_display_name, get_profile_photo_url
from ._common import User
from .study_room import _study_room_access_denied

logger = logging.getLogger(__name__)

Role = StudyRoomParticipant.Role
Status = StudyRoomParticipant.Status
SharePolicy = StudyRoomSession.SharePolicy

REPORT_DEDUPE_WINDOW = timedelta(hours=24)
MAX_REPORT_DETAILS = 1000
MAX_ROSTER = 300


def _err(detail, code, http_status):
    return Response({"detail": detail, "code": code}, status=http_status)


def _truthy(value) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y')


class _Ctx:
    """Request ka resolved context: conversation, session, meri role."""
    __slots__ = ('conversation', 'session', 'role', 'share_policy', 'row')


def _context(request, conversation_id, manager_only=False):
    """
    (ctx, error_response). Active member + ban/block rules (join jaisa) + active session + role.
    Removed / waiting-room wale user ka role `None` => 403 (controls ya room info nahi).
    """
    conversation = Conversation.objects.filter(
        id=conversation_id,
        memberships__user=request.user,
        memberships__left_at__isnull=True,
    ).first()
    if conversation is None:
        return None, _err("Conversation not found", "not_found", 404)

    denied = _study_room_access_denied(conversation, request.user)
    if denied:
        detail, code, http_status = denied
        return None, _err(detail, code, http_status)

    session = get_active_session(conversation.id)
    if session is None:
        return None, _err("Abhi koi study room session chal nahi raha.", "no_active_session", 404)

    row = StudyRoomParticipant.objects.filter(session=session, user=request.user).first()
    role, share_policy = sr_roles.role_and_policy(conversation, request.user.id)
    if role is None:
        return None, _err("Aap is study room me nahi hain.", "removed", 403)
    if manager_only and not sr_roles.is_manager(role):
        return None, _err("Ye action sirf host ya co-host kar sakte hain.", "host_required", 403)

    ctx = _Ctx()
    ctx.conversation, ctx.session, ctx.role, ctx.share_policy, ctx.row = conversation, session, role, share_policy, row
    return ctx, None


def _parse_user_id(raw):
    # 🔧 S8 FIX — pehle yahan `uuid.UUID(str(raw))` tha, jabki is project ka `User` pk integer (BigAutoField) hai:
    # har integer user_id "user_id galat hai." (400 invalid_user) ban jata tha, isliye mute / remove / role /
    # admit / report real data par chalte hi nahi. Ab User model ke apne pk field se parse hota hai (int ya UUID,
    # jo bhi pk ho) — `str` / `int` dono input chalte hain, bool / khali / junk reject.
    if raw is None or isinstance(raw, bool) or str(raw).strip() == '':
        return None, _err("user_id galat hai.", "invalid_user", 400)
    try:
        return User._meta.pk.to_python(str(raw).strip()), None
    except (DjangoValidationError, ValueError, TypeError, AttributeError):
        return None, _err("user_id galat hai.", "invalid_user", 400)


def _target(ctx, raw_user_id):
    """
    (target_user, row, role, error). Target conversation ka ACTIVE member hona chahiye; session row
    banti hai (pehle kabhi join nahi kiya to bhi — pre-emptive remove / role override ke liye).
    """
    uid, err = _parse_user_id(raw_user_id)
    if err:
        return None, None, None, err
    if not ConversationParticipant.objects.filter(
        conversation=ctx.conversation, user_id=uid, left_at__isnull=True,
    ).exists():
        return None, None, None, _err("Ye user is conversation me nahi hai.", "not_member", 404)
    user = User.objects.filter(id=uid).first()
    if user is None:
        return None, None, None, _err("User nahi mila.", "not_found", 404)
    row, _ = StudyRoomParticipant.objects.get_or_create(
        session=ctx.session, user=user, defaults={'status': Status.ADMITTED},
    )
    role = sr_roles.effective_role(ctx.conversation, ctx.session, user.id, row)
    return user, row, role, None


def _moderate_check(ctx, request, user, role):
    ok, code = sr_roles.can_moderate(ctx.conversation, ctx.session, request.user, ctx.role, user.id, role)
    if ok:
        return None
    messages = {
        'cannot_target_self': "Ye action apne upar nahi ho sakta.",
        'insufficient_role': "Aapke role se is user par ye action allowed nahi hai.",
        'group_staff_protected': "Group admin / moderator par sirf group admin hi action le sakta hai.",
    }
    return _err(messages.get(code, "Allowed nahi hai."), code, 403)


def _roster(ctx, viewer=None, limit=MAX_ROSTER):
    """Admitted participants (role + hand). Sirf jinki session row hai (jo kabhi aaye / host ne chhua).
    S8: `viewer` diya ho to public group ke minors ka naam viewer ke hisaab se masked (group admin/mod ko real)."""
    rows = list(
        StudyRoomParticipant.objects.filter(session=ctx.session, status=Status.ADMITTED)
        .select_related('user')[:limit]
    )
    masked = set()
    if viewer is not None:
        masked = {str(i) for i in sr_safety.masked_user_ids(viewer, ctx.conversation, [r.user_id for r in rows])}
    out = []
    for r in rows:
        out.append({
            "user_id": str(r.user_id),
            "display_name": sr_safety.person_fields(r.user, str(r.user_id) in masked)["display_name"],
            "masked": str(r.user_id) in masked,
            "role": sr_roles.effective_role(ctx.conversation, ctx.session, r.user_id, r),
            "hand_raised": r.hand_raised,
            "hand_raised_at": r.hand_raised_at.isoformat() if r.hand_raised_at else None,
            "joined": r.joined_at is not None,
        })
    return out


def _sync_metadata(session):
    """LiveKit room metadata me settings (best-effort). True/False."""
    return lk.update_room_metadata(
        session.room_name, lk.study_room_room_metadata(sr_roles.room_settings_payload(session)),
    )


def _push_permissions(ctx, identities_roles, share_policy):
    """Chalte session me in (identity -> role) ke publish rights LiveKit pe badlo. Updated count."""
    done = 0
    for ident, role in identities_roles:
        if lk.update_participant_permissions(ctx.session.room_name, ident, role, share_policy):
            done += 1
    return done


# ----------------------------------------------------------------------
# GET room/
# ----------------------------------------------------------------------
class StudyRoomRoomInfoView(APIView):
    """
    GET /message/study-room/<conversation_id>/room/
    {room_settings, role, permissions, hand_raised, roster[, waiting, hands]}   (waiting + hands sirf manager ko)
    Client reconnect / screen resume / WS event miss hone par state yahin se resync karta hai.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, conversation_id):
        ctx, err = _context(request, conversation_id)
        if err:
            return err
        body = {
            "room_settings": sr_roles.room_settings_payload(ctx.session),
            "role": ctx.role,
            "permissions": sr_roles.grants_for(ctx.role, ctx.share_policy),
            "hand_raised": bool(ctx.row and ctx.row.hand_raised),
            "roster": _roster(ctx, viewer=request.user),
            # 🔥 S8 — block (dono direction) + privacy hints: reconnect / resume par client tiles dobara hide kare.
            "blocked_user_ids": sorted(str(i) for i in sr_safety.block_pair_ids(request.user.id)),
            "privacy": sr_safety.room_privacy_hints(ctx.conversation, request.user),
        }
        if sr_roles.is_manager(ctx.role):
            waiting = list(
                StudyRoomParticipant.objects.filter(session=ctx.session, status=Status.WAITING)
                .select_related('user').order_by('updated_at')[:MAX_ROSTER]
            )
            waiting_masked = {str(i) for i in sr_safety.masked_user_ids(
                request.user, ctx.conversation, [w.user_id for w in waiting])}
            body["waiting"] = []
            for w in waiting:
                person = sr_safety.person_fields(w.user, str(w.user_id) in waiting_masked, request=request)
                body["waiting"].append({
                    "user_id": str(w.user_id),
                    "display_name": person["display_name"],
                    "avatar_url": person["avatar_url"],
                    "masked": person["masked"],
                    "since": w.updated_at.isoformat(),
                })
            hands = [r for r in body["roster"] if r["hand_raised"]]
            hands.sort(key=lambda r: r["hand_raised_at"] or "")
            body["hands"] = hands
        return Response(body)


# ----------------------------------------------------------------------
# POST settings/
# ----------------------------------------------------------------------
class StudyRoomSettingsView(APIView):
    """
    POST|PATCH /message/study-room/<conversation_id>/settings/   (host / co-host)
    Body (sab optional): {is_locked: bool, waiting_room_enabled: bool, max_participants: int|null,
                          share_policy: "everyone"|"host_only"}
      * is_locked           — naye log nahi aa sakte (jo andar hain wo rehte hain); manager exempt.
      * waiting_room_enabled — naye log waiting room me; off karte hi waiting wale sab admit.
      * max_participants    — 2..STUDY_ROOM_MAX_PARTICIPANTS_LIMIT; null = default. LiveKit room cap
                              room banate waqt (default cap) lagta hai; baad me badla hua cap join-gate
                              (`room_full` 409) lagata hai — LiveKit API se cap live nahi badalta.
      * share_policy        — host_only: participant ka screen share (token + live permission) aur
                              whiteboard / notes / presentation (WS + REST gate) band.
    Response: {room_settings, livekit_synced}
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomModerationThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id, manager_only=True)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}

        updates = {}
        if 'is_locked' in data:
            updates['is_locked'] = _truthy(data.get('is_locked'))
        if 'waiting_room_enabled' in data:
            updates['waiting_room_enabled'] = _truthy(data.get('waiting_room_enabled'))
        if 'share_policy' in data:
            value = str(data.get('share_policy') or '').strip()
            if value not in SharePolicy.values:
                return _err("share_policy galat hai (everyone / host_only).", "invalid_share_policy", 400)
            updates['share_policy'] = value
        if 'max_participants' in data:
            raw = data.get('max_participants')
            if raw in (None, '', 'null'):
                updates['max_participants'] = None
            else:
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    return _err("max_participants number hona chahiye.", "invalid_max_participants", 400)
                limit = sr_roles.max_participants_limit()
                if value < 2 or value > limit:
                    return _err(f"max_participants 2 se {limit} ke beech hona chahiye.",
                                "invalid_max_participants", 400)
                updates['max_participants'] = value
        if not updates:
            return _err("Koi setting nahi bheji.", "nothing_to_update", 400)

        auto_admitted = []
        with transaction.atomic():
            session = StudyRoomSession.objects.select_for_update().get(pk=ctx.session.pk)
            if session.ended_at is not None:
                return _err("Session khatam ho chuka hai.", "no_active_session", 404)
            old_policy = session.share_policy
            for k, v in updates.items():
                setattr(session, k, v)
            session.save(update_fields=list(updates.keys()) + ['updated_at'])
            # Waiting room band => jo waiting me the unhe admit (warna atke rehte).
            if updates.get('waiting_room_enabled') is False:
                waiting = list(StudyRoomParticipant.objects.select_for_update().filter(
                    session=session, status=Status.WAITING))
                for w in waiting:
                    w.status = Status.ADMITTED
                    w.decided_by = request.user
                    w.save(update_fields=['status', 'decided_by', 'updated_at'])
                    auto_admitted.append(str(w.user_id))
        ctx.session = session

        synced = _sync_metadata(session)

        # share_policy badli => abhi connected participants ke publish rights live badlo.
        if 'share_policy' in updates and session.share_policy != old_policy:
            identities = lk.list_room_identities(session.room_name)
            if identities:
                rows = {str(r.user_id): r for r in StudyRoomParticipant.objects.filter(session=session)}
                pairs = []
                for ident in list(identities)[:MAX_ROSTER]:
                    role = sr_roles.effective_role(ctx.conversation, session, ident, rows.get(ident))
                    if not sr_roles.is_manager(role) and role != Role.VIEWER:
                        pairs.append((ident, role))
                pushed = _push_permissions(ctx, pairs, session.share_policy)
                synced = synced and pushed == len(pairs)

        payload = sr_roles.room_settings_payload(session)
        sr_events.broadcast(ctx.conversation.id, sr_events.EVT_SETTINGS_CHANGED, {
            "settings": payload, "changedBy": str(request.user.id),
        })
        if auto_admitted:
            sr_events.broadcast(
                ctx.conversation.id, sr_events.EVT_WAITING_DECISION,
                {"status": "admitted", "userIds": auto_admitted}, target_user_ids=auto_admitted,
            )
        return Response({"room_settings": payload, "livekit_synced": bool(synced)})

    patch = post


# ----------------------------------------------------------------------
# POST mute/
# ----------------------------------------------------------------------
class StudyRoomMuteView(APIView):
    """
    POST /message/study-room/<conversation_id>/mute/   (host / co-host)
    Body: {"user_id": "<uuid>"}  ya  {"all": true}
    LiveKit RoomService `mute_published_track` se mic server-side mute. `all` me khud aur baaki
    host / co-hosts mute nahi hote. Participant dobara unmute kar sakta hai (soft mute); permanent
    chup karna ho to role `viewer` (publish rights hat jaate hain).
    Response: {"muted": N}; 502 `livekit_unavailable` agar LiveKit call fail hui.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomModerationThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id, manager_only=True)
        if err:
            return err
        if not lk.livekit_configured():
            return _err("Study room abhi available nahi hai.", "unavailable", 503)
        data = request.data if hasattr(request.data, 'get') else {}

        if _truthy(data.get('all')):
            identities = lk.list_room_identities(ctx.session.room_name)
            if identities is None:
                return _err("LiveKit se baat nahi ho paayi. Thodi der baad try karo.", "livekit_unavailable", 502)
            rows = {str(r.user_id): r for r in StudyRoomParticipant.objects.filter(session=ctx.session)}
            wanted = {
                ident for ident in identities
                if ident != str(request.user.id)
                and not sr_roles.is_manager(
                    sr_roles.effective_role(ctx.conversation, ctx.session, ident, rows.get(ident)))
            }
            muted = lk.mute_microphones(ctx.session.room_name, identities=wanted) if wanted else 0
            if muted is None:
                return _err("LiveKit se baat nahi ho paayi. Thodi der baad try karo.", "livekit_unavailable", 502)
            sr_events.broadcast(ctx.conversation.id, sr_events.EVT_MUTED,
                                {"all": True, "by": str(request.user.id)}, target_user_ids=list(wanted))
            return Response({"muted": muted, "scope": "all"})

        user, _row, role, terr = _target(ctx, data.get('user_id'))
        if terr:
            return terr
        cerr = _moderate_check(ctx, request, user, role)
        if cerr:
            return cerr
        muted = lk.mute_microphones(ctx.session.room_name, identities={str(user.id)})
        if muted is None:
            return _err("LiveKit se baat nahi ho paayi. Thodi der baad try karo.", "livekit_unavailable", 502)
        sr_events.broadcast(ctx.conversation.id, sr_events.EVT_MUTED,
                            {"userIds": [str(user.id)], "by": str(request.user.id)}, target_user_ids=[user.id])
        return Response({"muted": muted, "scope": "user"})


# ----------------------------------------------------------------------
# POST remove/
# ----------------------------------------------------------------------
class StudyRoomRemoveView(APIView):
    """
    POST /message/study-room/<conversation_id>/remove/   {"user_id": "<uuid>"}   (host / co-host)
    Participant ko LiveKit room se abhi nikaalta hai (`remove_participant`) aur is session me wapas aane se
    rokta hai (row status `removed` — join aur refresh-token dono 403 `removed_from_room`; WS events bhi
    band). Naya session (new_session) fresh start hai. Group admin / moderator ko sirf group admin hata sakta
    hai; host ko koi nahi. Host `admit/` se wapas aane de sakta hai.
    Response: {"removed": true, "kicked": bool}  (`kicked` False => LiveKit call fail; DB me phir bhi removed)
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomModerationThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id, manager_only=True)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}
        user, row, role, terr = _target(ctx, data.get('user_id'))
        if terr:
            return terr
        cerr = _moderate_check(ctx, request, user, role)
        if cerr:
            return cerr

        row.status = Status.REMOVED
        row.hand_raised = False
        row.hand_raised_at = None
        row.decided_by = request.user
        row.save(update_fields=['status', 'hand_raised', 'hand_raised_at', 'decided_by', 'updated_at'])

        kicked = lk.remove_participant(ctx.session.room_name, str(user.id))
        sr_events.broadcast(
            ctx.conversation.id, sr_events.EVT_REMOVED,
            {"userId": str(user.id), "by": str(request.user.id)}, target_user_ids=[user.id],
        )
        sr_events.broadcast(
            ctx.conversation.id, sr_events.EVT_HAND,
            {"userId": str(user.id), "raised": False,
             "displayName": sr_safety.person_fields(user, sr_safety.masked_for_managers(ctx.conversation, user))["display_name"]},
        )
        return Response({"removed": True, "kicked": bool(kicked)})


# ----------------------------------------------------------------------
# POST admit/
# ----------------------------------------------------------------------
class StudyRoomAdmitView(APIView):
    """
    POST /message/study-room/<conversation_id>/admit/   (host / co-host)
      {"user_id": "<uuid>", "action": "admit"}  — waiting / denied / removed user ko andar aane do
      {"user_id": "<uuid>", "action": "deny"}   — waiting room wale ko decline (join 403 `denied_by_host`)
      {"action": "admit_all"}                   — waiting room ke sab log
    Admit ke baad user ka client `join/` dobara bulata hai (waiting poll) — lock / cap tab bhi lagte hain.
    Response: {"status": "admitted"|"denied", "user_ids": [...]}
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomModerationThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id, manager_only=True)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}
        action = str(data.get('action') or 'admit').strip().lower()
        if action not in ('admit', 'deny', 'admit_all'):
            return _err("action galat hai (admit / deny / admit_all).", "invalid_action", 400)

        if action == 'admit_all':
            with transaction.atomic():
                waiting = list(StudyRoomParticipant.objects.select_for_update().filter(
                    session=ctx.session, status=Status.WAITING))
                for w in waiting:
                    w.status = Status.ADMITTED
                    w.decided_by = request.user
                    w.save(update_fields=['status', 'decided_by', 'updated_at'])
            ids = [str(w.user_id) for w in waiting]
            if ids:
                sr_events.broadcast(ctx.conversation.id, sr_events.EVT_WAITING_DECISION,
                                    {"status": "admitted", "userIds": ids}, target_user_ids=ids)
            return Response({"status": "admitted", "user_ids": ids})

        user, row, role, terr = _target(ctx, data.get('user_id'))
        if terr:
            return terr
        if user.id == request.user.id:
            return _err("Ye action apne upar nahi ho sakta.", "cannot_target_self", 403)

        if action == 'deny':
            if row.status != Status.WAITING:
                return _err("Ye user waiting room me nahi hai.", "not_waiting", 409)
            new_status = Status.DENIED
        else:
            new_status = Status.ADMITTED
            # Pehle se room me hi hai (admitted) — kuch karna nahi.
            if row.status == Status.ADMITTED:
                return Response({"status": "admitted", "user_ids": [str(user.id)]})
            # Removed user ko wapas lena group staff / host ka faisla hai, par role-order phir bhi lagta hai.
            if row.status == Status.REMOVED:
                cerr = _moderate_check(ctx, request, user, role)
                if cerr:
                    return cerr

        row.status = new_status
        row.decided_by = request.user
        row.save(update_fields=['status', 'decided_by', 'updated_at'])
        sr_events.broadcast(
            ctx.conversation.id, sr_events.EVT_WAITING_DECISION,
            {"status": new_status, "userIds": [str(user.id)]}, target_user_ids=[user.id],
        )
        return Response({"status": new_status, "user_ids": [str(user.id)]})


# ----------------------------------------------------------------------
# POST role/
# ----------------------------------------------------------------------
class StudyRoomRoleView(APIView):
    """
    POST /message/study-room/<conversation_id>/role/   {"user_id": "<uuid>", "role": "co_host|participant|viewer"}
      * host: kisi ko bhi co_host / participant / viewer (host ko nahi).
      * co_host: sirf participant <-> viewer (naya co-host nahi bana sakta, co-host ko nahi chhu sakta).
      * group admin / moderator ka role yahan se nahi badalta (hamesha co-host) — lockout se bachav.
    Chalte room me LiveKit participant permission + metadata live update hoti hai (viewer => publish band).
    Response: {"user_id", "role", "permissions", "livekit_synced"}
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomModerationThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id, manager_only=True)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}
        new_role = str(data.get('role') or '').strip()
        if new_role not in (Role.CO_HOST, Role.PARTICIPANT, Role.VIEWER):
            return _err("role galat hai (co_host / participant / viewer).", "invalid_role", 400)

        user, row, role, terr = _target(ctx, data.get('user_id'))
        if terr:
            return terr
        cerr = _moderate_check(ctx, request, user, role)
        if cerr:
            return cerr
        if sr_roles.is_group_staff(ctx.conversation, user.id):
            return _err("Group admin / moderator ka room role fixed hai (co-host).", "group_staff_role_fixed", 400)
        if new_role == Role.CO_HOST and ctx.role != Role.HOST:
            return _err("Co-host sirf host bana sakta hai.", "host_required", 403)

        default = sr_roles.default_role(ctx.conversation, ctx.session, user.id)
        row.role_override = None if new_role == default else new_role
        row.save(update_fields=['role_override', 'updated_at'])

        synced = True
        if row.status == Status.ADMITTED:
            identities = lk.list_room_identities(ctx.session.room_name)
            if identities and str(user.id) in identities:
                synced = lk.update_participant_permissions(
                    ctx.session.room_name, str(user.id), new_role, ctx.session.share_policy)
        perms = sr_roles.grants_for(new_role, ctx.session.share_policy)
        sr_events.broadcast(ctx.conversation.id, sr_events.EVT_ROLE_CHANGED, {
            "userId": str(user.id), "role": new_role, "permissions": perms, "by": str(request.user.id),
        })
        return Response({"user_id": str(user.id), "role": new_role, "permissions": perms,
                         "livekit_synced": bool(synced)})


# ----------------------------------------------------------------------
# POST hand/
# ----------------------------------------------------------------------
class StudyRoomHandView(APIView):
    """
    POST /message/study-room/<conversation_id>/hand/
      {"raised": true|false}                 — apna haath (viewer bhi — bolne ki ijazat maangne ke liye)
      {"user_id": "<uuid>"}                  — (manager) us ka haath neeche
      {"all": true}                          — (manager) sab ke haath neeche
    Realtime: `hand_raised` {userId, raised, displayName} (sab ko; manager list isi se banata hai).
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomHandThrottle]

    def post(self, request, conversation_id):
        ctx, err = _context(request, conversation_id)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}

        if _truthy(data.get('all')) or data.get('user_id'):
            if not sr_roles.is_manager(ctx.role):
                return _err("Dusron ka haath sirf host / co-host neeche kar sakte hain.", "host_required", 403)
            if _truthy(data.get('all')):
                ids = list(StudyRoomParticipant.objects.filter(session=ctx.session, hand_raised=True)
                           .values_list('user_id', flat=True))
                StudyRoomParticipant.objects.filter(session=ctx.session, hand_raised=True).update(
                    hand_raised=False, hand_raised_at=None, updated_at=timezone.now())
                sr_events.broadcast(ctx.conversation.id, sr_events.EVT_HAND,
                                    {"all": True, "raised": False, "userIds": [str(i) for i in ids]})
                return Response({"raised": False, "lowered": len(ids)})
            user, row, _role, terr = _target(ctx, data.get('user_id'))
            if terr:
                return terr
            row.hand_raised = False
            row.hand_raised_at = None
            row.save(update_fields=['hand_raised', 'hand_raised_at', 'updated_at'])
            sr_events.broadcast(ctx.conversation.id, sr_events.EVT_HAND, {
                "userId": str(user.id), "raised": False, "byHost": True,
                "displayName": sr_safety.person_fields(user, sr_safety.masked_for_managers(ctx.conversation, user))["display_name"],
            })
            return Response({"raised": False, "lowered": 1})

        raised = _truthy(data.get('raised', True))
        row = ctx.row
        if row is None:
            row, _ = StudyRoomParticipant.objects.get_or_create(
                session=ctx.session, user=request.user, defaults={'status': Status.ADMITTED})
        row.hand_raised = raised
        row.hand_raised_at = timezone.now() if raised else None
        row.save(update_fields=['hand_raised', 'hand_raised_at', 'updated_at'])
        sr_events.broadcast(ctx.conversation.id, sr_events.EVT_HAND, {
            "userId": str(request.user.id), "raised": raised,
            "displayName": sr_safety.person_fields(
                request.user, sr_safety.masked_for_managers(ctx.conversation, request.user))["display_name"],
        })
        return Response({"raised": raised})


# ----------------------------------------------------------------------
# POST report/
# ----------------------------------------------------------------------
def _active_member_conversation(request, conversation_id):
    """Conversation jisme caller ACTIVE member hai (room se removed ho tab bhi) ya None."""
    return Conversation.objects.filter(
        id=conversation_id, memberships__user=request.user, memberships__left_at__isnull=True,
    ).first()


def _active_member_target(conversation, uid):
    """(User, err). Target conversation ka ACTIVE member hona chahiye."""
    if not ConversationParticipant.objects.filter(conversation=conversation, user_id=uid, left_at__isnull=True).exists():
        return None, _err("Ye user is conversation me nahi hai.", "not_member", 404)
    user = User.objects.filter(id=uid).first()
    if user is None:
        return None, _err("User nahi mila.", "not_found", 404)
    return user, None


class StudyRoomReportView(APIView):
    """
    POST /message/study-room/<conversation_id>/report/   {"user_id", "reason", "details"?, "block"?}
    reason: harassment | inappropriate | spam | unsafe_minor | other. Reporter conversation ka ACTIVE member
    hona chahiye (room se removed ho tab bhi report kar sakta hai). Same reporter -> same user, 24h me ek
    open report (dobara bhejne par 200 `duplicate`). Review: Django admin (`StudyRoomReport`).

    S8:
      * `block: true` => report ke saath hi us user ko block (duplicate report par bhi block lagta hai).
      * `STUDY_ROOM_REPORT_AUTO_REMOVE_THRESHOLD` (default 0 = off): window (`..._WINDOW_MINUTES`, 60) me itne ALAG
        reporters => user chalte session se removed (host / co-host / group staff kabhi nahi). Response me `auto_removed`.
    Response 201: {"id", "status": "open", "blocked": bool, "auto_removed": bool}
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomReportThrottle]

    def post(self, request, conversation_id):
        conversation = _active_member_conversation(request, conversation_id)
        if conversation is None:
            return _err("Conversation not found", "not_found", 404)
        data = request.data if hasattr(request.data, 'get') else {}

        uid, perr = _parse_user_id(data.get('user_id'))
        if perr:
            return perr
        if uid == request.user.id:
            return _err("Aap khud ko report nahi kar sakte.", "cannot_target_self", 400)
        reason = str(data.get('reason') or '').strip()
        if reason not in StudyRoomReport.Reason.values:
            return _err("reason galat hai.", "invalid_reason", 400)
        target, terr = _active_member_target(conversation, uid)
        if terr:
            return terr
        details = str(data.get('details') or '').strip()[:MAX_REPORT_DETAILS]

        blocked = False
        if _truthy(data.get('block')):
            sr_safety.block_user(request.user, target)
            _notify_block_changed(conversation.id, request.user.id, target.id, True)
            blocked = True

        session = get_active_session(conversation.id)
        since = timezone.now() - REPORT_DEDUPE_WINDOW
        existing = StudyRoomReport.objects.filter(
            conversation=conversation, reporter=request.user, reported_user_id=uid,
            status=StudyRoomReport.Status.OPEN, created_at__gte=since,
        ).first()
        if existing is not None:
            return Response({"id": str(existing.id), "status": existing.status, "duplicate": True,
                             "blocked": blocked, "auto_removed": False})

        report = StudyRoomReport.objects.create(
            conversation=conversation, session=session, reporter=request.user,
            reported_user_id=uid, reason=reason, details=details,
        )
        if reason == StudyRoomReport.Reason.UNSAFE_MINOR:
            logger.warning("study room child-safety report id=%s conv=%s reported=%s",
                           report.id, conversation.id, uid)
        auto_removed = sr_safety.maybe_auto_remove(conversation, uid)
        return Response({"id": str(report.id), "status": report.status, "blocked": blocked,
                         "auto_removed": auto_removed}, status=201)


# ----------------------------------------------------------------------
# GET / POST rules/   (S8)
# ----------------------------------------------------------------------
class StudyRoomRulesView(APIView):
    """
    GET  /message/study-room/<conversation_id>/rules/
         {group_id, group_name, is_public, version, default_rules[{id,en,hi}], custom_rules, accepted, required,
          can_edit, join_approval_required, privacy{...}}
         `required` = is user ko room me aane se pehle accept karna hai (public group + non-staff + setting on).
    POST /message/study-room/<conversation_id>/rules/   {"version": N?}
         Rules accept (idempotent). `version` bheja ho aur ab purana hai (admin ne beech me rules badle) =>
         409 `rules_version_changed` + naya payload — client naye rules dikhaye.
    Sirf group conversation ke ACTIVE member; DM me 404 `not_group`.
    """
    permission_classes = [IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [StudyRoomRulesThrottle()]
        return super().get_throttles()

    def _group(self, request, conversation_id):
        conversation = _active_member_conversation(request, conversation_id)
        if conversation is None:
            return None, _err("Conversation not found", "not_found", 404)
        group = sr_safety.group_for(conversation)
        if group is None:
            return None, _err("Ye group ka study room nahi hai.", "not_group", 404)
        return group, None

    def get(self, request, conversation_id):
        group, err = self._group(request, conversation_id)
        if err:
            return err
        return Response(sr_safety.rules_payload(group, request.user))

    def post(self, request, conversation_id):
        group, err = self._group(request, conversation_id)
        if err:
            return err
        data = request.data if hasattr(request.data, 'get') else {}
        version = data.get('version')
        if version is not None and str(version).strip() != '':
            try:
                version = int(version)
            except (TypeError, ValueError):
                return _err("version galat hai.", "invalid_version", 400)
        else:
            version = None
        row, matched = sr_safety.accept_rules(group, request.user, version=version)
        if not matched:
            body = sr_safety.rules_payload(group, request.user)
            body.update({"detail": "Rules badal gaye hain. Naye rules padh kar dobara accept karo.",
                         "code": "rules_version_changed"})
            return Response(body, status=409)
        body = sr_safety.rules_payload(group, request.user)
        body["accepted_at"] = row.accepted_at.isoformat()
        return Response(body)


# ----------------------------------------------------------------------
# POST / DELETE block/   (S8)
# ----------------------------------------------------------------------
def _notify_block_changed(conversation_id, me_id, other_id, is_blocked):
    """
    Block / unblock ke baad dono users ke OPEN room sockets ko `room_block_changed` (consumer ka block cache turant
    clear + client tiles refresh). Alag-alag targeted event: har ek ko `userId` = doosra banda. Best-effort.
    """
    try:
        for target, other in ((me_id, other_id), (other_id, me_id)):
            sr_events.broadcast(conversation_id, sr_events.EVT_BLOCK_CHANGED,
                                {"userId": str(other), "blocked": bool(is_blocked)}, target_user_ids=[target])
    except Exception:
        logger.exception("room_block_changed broadcast failed conv=%s", conversation_id)


class StudyRoomBlockView(APIView):
    """
    POST   /message/study-room/<conversation_id>/block/   {"user_id"}   — us user ko block (existing BlockedUser)
    DELETE /message/study-room/<conversation_id>/block/   {"user_id"} (ya ?user_id=)  — unblock
    Block ke baad: dono taraf room events (draw / chat / hand / user_joined) hide (WS consumer filter) aur client
    us user ke tile (video / audio) hide karta hai. Block global hai (DM / follow bhi) — wahi `BlockedUser` table.
    LiveKit media server-side per-viewer filter nahi hota => wo client-side hide hai (documented limit).
    Group admin / moderator ko bhi block kar sakte ho (apni safety) — wo room me moderate karte rahenge.
    Caller conversation ka ACTIVE member; target bhi ACTIVE member (unblock me target ko member hona zaroori nahi).
    Response: {"blocked": bool, "user_id", "blocked_user_ids": [...]}  (blocked_user_ids = dono direction)
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [StudyRoomBlockThrottle]

    def post(self, request, conversation_id):
        conversation = _active_member_conversation(request, conversation_id)
        if conversation is None:
            return _err("Conversation not found", "not_found", 404)
        data = request.data if hasattr(request.data, 'get') else {}
        uid, perr = _parse_user_id(data.get('user_id'))
        if perr:
            return perr
        if uid == request.user.id:
            return _err("Aap khud ko block nahi kar sakte.", "cannot_target_self", 400)
        target, terr = _active_member_target(conversation, uid)
        if terr:
            return terr
        sr_safety.block_user(request.user, target)
        _notify_block_changed(conversation.id, request.user.id, target.id, True)
        return Response({"blocked": True, "user_id": str(target.id),
                         "blocked_user_ids": sorted(str(i) for i in sr_safety.block_pair_ids(request.user.id))})

    def delete(self, request, conversation_id):
        conversation = _active_member_conversation(request, conversation_id)
        if conversation is None:
            return _err("Conversation not found", "not_found", 404)
        data = request.data if hasattr(request.data, 'get') else {}
        raw = data.get('user_id') if data.get('user_id') is not None else request.query_params.get('user_id')
        uid, perr = _parse_user_id(raw)
        if perr:
            return perr
        sr_safety.unblock_user(request.user, uid)
        _notify_block_changed(conversation.id, request.user.id, uid, False)
        return Response({"blocked": False, "user_id": str(uid),
                         "blocked_user_ids": sorted(str(i) for i in sr_safety.block_pair_ids(request.user.id))})
