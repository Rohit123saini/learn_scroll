"""
message/views/calls.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
import uuid
from datetime import timedelta
from asgiref.sync import async_to_sync
import hmac
from django.conf import settings
from django.http import HttpResponse
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from .. import call_recording_consent as recording_consent   # C16
from .. import call_ops   # X1 — call observability counters
from .. import call_state
from ..group_rules import check_group_permission
from ..livekit_utils import (
    EgressError,
    bounded_ttl,
    call_token_ttl,
    ensure_call_room,
    generate_livekit_token,
    start_room_recording,
    stop_room_recording,
    token_meta,
)
from ..models import (
    CallParticipant,
    CallSession,
    CallStatus,
    CallType,
    Conversation,
    ConversationType,
)
from ..push_utils import send_call_cancelled_push, send_incoming_call_push
from ..tasks import enqueue_call_ring_push, enqueue_missed_call
from ..serializers import CallSessionSerializer, UserMiniSerializer
from ..throttles import (
    CallInitiateIPThrottle,
    CallInitiateThrottle,
    CallOpsThrottle,
    CallTelemetryThrottle,
    LiveKitTokenRefreshThrottle,
)
from ..user_display import get_display_name, get_profile_photo_url

from ._common import LIVEKIT_WS_URL, StandardPagination, User, is_blocked_pair

logger = logging.getLogger(__name__)


# ======================================================================
# CALLS
# ======================================================================
def _truthy(value):
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


# 🔥 C13 — call ki hard cap (sweeper wali `started_at + CALL_STALE_ONGOING_SECONDS`) aur
# token-expiry metadata. Client isse silent refresh + "5 min me call khatam" warning chalata hai.
def _call_session_ends_at(call):
    return call.started_at + timedelta(seconds=call_state.call_max_duration_seconds())


def _call_token_fields(call):
    """Token-response me merge hone wali expiry keys (token ka TTL cap se clip hota hai)."""
    ends_at = _call_session_ends_at(call)
    return token_meta(bounded_ttl(call_token_ttl(), ends_at), session_ends_at=ends_at)


def _broadcast_call_events(call, events):
    """call_state.ActionResult.events ko dono groups (call_<id> + chat_<conv>) me bhejo."""
    if not events:
        return
    channel_layer = get_channel_layer()
    for ev in events:
        data = dict(ev)
        name = data.get('event')
        try:
            async_to_sync(channel_layer.group_send)(
                f'call_{call.id}', {'type': 'call_signal', 'data': data},
            )
            if call.conversation_id:
                async_to_sync(channel_layer.group_send)(
                    f'chat_{call.conversation_id}',
                    {'type': 'call_event', **data},
                )
        except Exception:
            logger.warning("call event broadcast failed (%s) call=%s", name, call.id, exc_info=True)


def _dispatch_result(result):
    """ActionResult ke side-effects: WS events + ring-cancel push (DB commit ke BAAD)."""
    if result is None or not result.changed or result.call is None:
        return
    call_ops.note_call_result(result)   # X1 — ring outcome (answered / rejected / cancelled); never raises
    _broadcast_call_events(result.call, result.events)
    if result.cancel_push_user_ids:
        try:
            send_call_cancelled_push(
                recipient_ids=result.cancel_push_user_ids,
                call_id=result.call.id,
                conversation_id=result.call.conversation_id,
            )
        except Exception:
            logger.warning("cancel push failed call=%s", result.call.id, exc_info=True)
    # C12 — jo ring me the aur ab MISSED hue (caller ne cancel kiya / group ka last banda gaya):
    # "Missed call" push + bell row; kabhi connect na hui call ka chat bubble. `cancel_push_user_ids` =
    # wahi RINGING -> MISSED user ids.
    if result.cancel_push_user_ids:
        enqueue_missed_call(result.call.id, result.cancel_push_user_ids)


class CallInitiateView(APIView):
    permission_classes = [IsAuthenticated]
    # 🔥 FIX — `CallInitiateThrottle` existed in throttles.py (calls are
    # more "costly" than a normal message: each one creates an FCM push +
    # a LiveKit room) but was never applied here.
    throttle_classes = [CallInitiateThrottle, CallInitiateIPThrottle]

    def post(self, request):
        conversation_id = request.data.get('conversation_id')
        call_type = request.data.get('type', 'audio')
        if not conversation_id:
            return Response({"detail": "conversation_id required"}, status=400)
        # C6 — `type` pehle koi bhi string DB me chala jaata tha.
        if call_type not in (CallType.AUDIO, CallType.VIDEO):
            return Response({"detail": "type 'audio' ya 'video' hona chahiye."}, status=400)
        # C7 — caller ki apni atki hui purani call ko end karke nayi shuru karne ka opt-in.
        replace_active = _truthy(request.data.get('replace_active'))

        # C6 — caller ka apna membership bhi `left_at IS NULL` hona chahiye
        # (group chhod chuka user ab bhi us conversation me call laga sakta tha).
        conversation = Conversation.objects.filter(
            id=conversation_id,
            memberships__user=request.user,
            memberships__left_at__isnull=True,
        ).first()
        if not conversation:
            return Response({"detail": "Conversation not found"}, status=404)

        # 🔥 NAYA — messages POST action jaisa hi block check yahan bhi —
        # pehle koi bhi (blocked ho ya na ho) existing 1-1 conversation me
        # call initiate kar sakta tha, sirf naya chat start karte waqt block
        # check hota tha. Group calls me skip (block group me apply nahi).
        if conversation.type != ConversationType.GROUP:
            other_id = conversation.memberships.filter(
                left_at__isnull=True
            ).exclude(user_id=request.user.id).values_list('user_id', flat=True).first()
            if other_id and is_blocked_pair(request.user.id, other_id):
                return Response(
                    {"detail": "Block hone ki wajah se call nahi ho sakti."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # S4 -- the callee limited who may call them (Settings > Privacy).
            if other_id:
                from user_profile import privacy as privacy_rules

                if not privacy_rules.can_call(other_id, request.user):
                    return Response(
                        {"detail": privacy_rules.CALL_BLOCKED_TEXT, "code": privacy_rules.CALL_BLOCKED_CODE},
                        status=status.HTTP_403_FORBIDDEN,
                    )
                # P-F16 -- same adult->minor rule as a new DM: an adult may call a minor only if the minor follows the adult.
                from django.contrib.auth import get_user_model

                from common import minor_policy

                if minor_policy.feature_enabled(minor_policy.DM):
                    from message.minor_safety import MINOR_DM_BLOCKED_MESSAGE, dm_blocked_for_minor

                    _other = get_user_model().objects.filter(pk=other_id).first()
                    if _other is not None and dm_blocked_for_minor(request.user, _other):
                        return Response(
                            {"detail": MINOR_DM_BLOCKED_MESSAGE, "code": minor_policy.MINOR_BLOCKED_CODE},
                            status=status.HTTP_403_FORBIDDEN,
                        )
        else:
            # 🔥 NAYA — group ka call_permission enforce karo.
            group = getattr(conversation, 'group_detail', None)
            if group:
                allowed, reason = check_group_permission(group, request.user.id, 'call_permission')
                if not allowed:
                    return Response({"detail": reason}, status=status.HTTP_403_FORBIDDEN)

        # 🔥 C4 — LiveKit token PEHLE banao (call row / ring push se pehle).
        # Config missing ho to 500 ya "ghost ring" nahi — seedha clean 503 aur
        # kisi ko ring nahi hota.
        caller_name = get_display_name(request.user)
        channel_name = f"call_{uuid.uuid4().hex}"
        try:
            caller_token = generate_livekit_token(
                room_name=channel_name,
                user_id=request.user.id,
                user_name=caller_name,
                ttl=call_token_ttl(),   # C13 — settings-driven (default 2h, pehle jaisa)
            )
        except RuntimeError:
            logger.exception("LiveKit not configured — call initiate refused")
            return Response(
                {"detail": "Calling service abhi available nahi hai. Thodi der baad try karo."},
                status=503,
            )

        # 🔥 FIX — was missing `left_at__isnull=True`. Without it, a user who
        # left the group/conversation (or was removed) still had a stale
        # `ConversationParticipant` row and kept getting rung for every new
        # call on that conversation.
        other_members = list(
            conversation.memberships.filter(left_at__isnull=True)
            .exclude(user=request.user).values_list('user_id', flat=True)
        )
        is_group = conversation.type == 'group'
        now = timezone.now()

        side_effects = []     # ActionResult list — commit ke baad dispatch
        early_response = None
        call = None
        busy_call = None      # C12 — 1:1 line busy row (callee ko missed-call milti hai)
        ring_ids, waiting_ids, busy_ids = [], [], []

        with transaction.atomic():
            # C7 — A->B aur C->B ek saath aaye to dono ko "free" na dikhe.
            call_state.lock_users([request.user.id, *other_members])

            # (1) Caller ki isi conversation ki purani ringing call ko saaf-suthra cancel karo
            #     (pehle sirf status=ENDED update hota tha: callee ki ring/push zinda rehti thi).
            for old_id in list(CallSession.objects.filter(
                conversation_id=conversation_id, caller=request.user,
                status__in=call_state.PRE_CONNECT_STATUSES,
            ).values_list('id', flat=True)):
                side_effects.append(call_state.end_call(old_id, request.user.id))

            # (2) Per-user ek active call: caller already kisi aur call me hai?
            mine = call_state.busy_status([request.user.id])
            if mine:
                if replace_active:
                    side_effects.extend(call_state.force_leave_stale_calls(request.user.id))
                else:
                    _kind, active_call = next(iter(mine.values()))
                    early_response = Response({
                        "detail": "Aap pehle se ek call me hain. Pehle use end karo.",
                        "code": "already_in_call",
                        "active_call_id": str(active_call.id),
                    }, status=status.HTTP_409_CONFLICT)

            if early_response is None:
                # (3) Callee busy check
                ring_ids, waiting_ids, busy_ids = call_state.split_callees(other_members)
                # C11 — bade group me sabko ring nahi: pehle `CALL_GROUP_RING_CAP` (online pehle),
                # baaki ko chat me "Join" banner. Overflow ki CallParticipant row nahi banti
                # (sweeper unhe MISSED na kare); wo `join` action se judte hain. 1:1 par no-op.
                overflow_ids = []
                if is_group:
                    ring_ids, waiting_ids, overflow_ids = call_state.apply_ring_cap(ring_ids, waiting_ids)

                if not is_group and other_members and not ring_ids and not waiting_ids:
                    # 1:1 aur callee busy -> "Line busy". Row banti hai taaki callee ko
                    # missed-call dikhe aur caller ki history me entry rahe.
                    busy_call = CallSession.objects.create(
                        type=call_type, status=CallStatus.BUSY, conversation=conversation,
                        group=getattr(conversation, 'group_detail', None), is_group_call=False,
                        caller=request.user, channel_name=channel_name, started_at=now, ended_at=now,
                    )
                    CallParticipant.objects.bulk_create(
                        [CallParticipant(call=busy_call, user=request.user, status=CallStatus.ENDED, left_at=now)]
                        + [CallParticipant(call=busy_call, user_id=uid, status=CallStatus.MISSED, left_at=now)
                           for uid in busy_ids]
                    )
                    early_response = Response({
                        "detail": "Line busy — samne wala abhi dusri call par hai.",
                        "code": "callee_busy",
                        "status": CallStatus.BUSY,
                        "call_id": str(busy_call.id),
                    }, status=status.HTTP_409_CONFLICT)
                else:
                    call = CallSession.objects.create(
                        type=call_type,
                        status=CallStatus.RINGING,
                        conversation=conversation,
                        group=getattr(conversation, 'group_detail', None),
                        is_group_call=is_group,
                        caller=request.user,
                        channel_name=channel_name,
                        started_at=now,
                    )
                    rows = [CallParticipant(call=call, user=request.user, status=CallStatus.ONGOING)]
                    rows += [CallParticipant(call=call, user_id=uid, status=CallStatus.RINGING)
                             for uid in (*ring_ids, *waiting_ids)]
                    # Group me busy members ko ring nahi; unki row BUSY (accept nahi kar sakte).
                    rows += [CallParticipant(call=call, user_id=uid, status=CallStatus.BUSY, left_at=now)
                             for uid in busy_ids]
                    CallParticipant.objects.bulk_create(rows)

        # ---- commit ho chuka: ab side-effects (network / push / WS) ----
        for res in side_effects:
            _dispatch_result(res)
        if busy_call is not None:
            # C12 — callee dusri call me tha: use "Missed call" push + bell row, chat me bubble (WhatsApp jaisa).
            enqueue_missed_call(busy_call.id, busy_ids)
            call_ops.note_ring_outcome('busy', call_id=busy_call.id)   # X1
        if early_response is not None:
            return early_response
        call_ops.note_initiated(is_group=is_group)   # X1 — a ring really started

        caller_photo = get_profile_photo_url(request.user, request=request)
        rung = [*ring_ids, *waiting_ids]

        # C11 — group room ko pehle se `max_participants` ke saath banao (LiveKit room pehle join par
        # auto-create hota hai, tab cap nahi hota). Best-effort + chhota timeout; fail => call chalti hai.
        if is_group:
            ensure_call_room(
                call.channel_name,
                call_state.group_max_participants(),
                timeout=float(getattr(settings, 'CALL_ROOM_CREATE_TIMEOUT_SECONDS', 3)),
            )

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{conversation_id}',
            {
                'type': 'call_event',
                'event': 'incoming_call',
                'call_id': str(call.id),
                'call_type': call_type,
                'caller_id': str(request.user.id),
                'caller_name': caller_name,
                'caller_photo': caller_photo,
                'conversation_id': str(conversation_id),
                'channel_name': call.channel_name,
                # C7 — chat group ke SAB sockets ko ye event jaata hai; client sirf
                # `ring_user_ids` me shamil user ko popup dikhaye (busy ko nahi).
                'ring_user_ids': [str(u) for u in rung],
                'call_waiting_user_ids': [str(u) for u in waiting_ids],
                'busy_user_ids': [str(u) for u in busy_ids],
                # C11 — ring cap ke bahar rahe members ko popup nahi; unhe Join banner. Bade group me
                # id list nahi bhejte (sirf count + flag): client `ring_user_ids` me apna id na dekhe
                # aur `is_group_call` ho to Join banner dikhaye.
                'is_group_call': bool(is_group),
                'overflow_count': len(overflow_ids),
                'joinable': bool(is_group),
            }
        )

        push_kwargs = dict(
            caller_name=caller_name, call_type=call_type, call_id=call.id,
            conversation_id=conversation_id, channel_name=call.channel_name,
        )
        if is_group and getattr(settings, 'CALL_GROUP_PUSH_ASYNC', True):
            # C11 — group: push + bell rows Celery me (chunked, commit ke baad). Request jaldi lauti.
            if ring_ids:
                enqueue_call_ring_push(call.id, ring_ids, caller_name=caller_name, caller_avatar=caller_photo)
            if waiting_ids:
                enqueue_call_ring_push(call.id, waiting_ids, caller_name=caller_name,
                                       caller_avatar=caller_photo, call_waiting=True)
        else:
            # 1:1 (ek hi user) — sync hi theek hai, queue ka delay bhi nahi.
            if ring_ids:
                send_incoming_call_push(recipient_ids=ring_ids, **push_kwargs)
            if waiting_ids:
                send_incoming_call_push(recipient_ids=waiting_ids, call_waiting=True, **push_kwargs)

        return Response({
            "call_id": str(call.id),
            "channel_name": call.channel_name,
            "type": call_type,
            "status": call.status,
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": caller_token,
            **_call_token_fields(call),   # C13
            "ringing_user_ids": [str(u) for u in rung],
            "busy_user_ids": [str(u) for u in busy_ids],
            # C11
            "is_group_call": bool(is_group),
            "overflow_count": len(overflow_ids),
            "max_participants": call_state.group_max_participants() if is_group else 2,
        })


class CallActionView(APIView):
    """
    POST /calls/<call_id>/action/  {"action": "accept" | "reject" | "end" | "join"}

    C11 — `join`: group call me bina ring ke judna (ring cap se bahar rahe members ke chat
    "Join" banner se, ya busy/miss/reject ke baad). Response `accept` jaisa (livekit token);
    409 `call_full` / `already_in_call`, 403 `not_member`, 400 `call_closed` / `not_group_call`.

    C6 — saara state logic `message/call_state.py` me (row-lock + explicit
    state machine + idempotent). Ye view sirf: auth/validation, LiveKit token,
    aur commit ke baad WS/push.
    """
    permission_classes = [IsAuthenticated]

    VALID_ACTIONS = ('accept', 'reject', 'end', 'join')

    def post(self, request, call_id):
        action = request.data.get('action')
        # 🔥 FIX — `action` was never validated against a known set. A
        # missing/typo'd value used to fall through every `if/elif` silently
        # and still broadcast a `call_<action>` signal — e.g. `call_None`.
        if action not in self.VALID_ACTIONS:
            return Response(
                {"detail": f"'action' must be one of {self.VALID_ACTIONS}."},
                status=400,
            )

        livekit_token = None
        token_fields = {}   # C13
        if action in ('accept', 'join'):
            # 🔥 C4 — token PEHLE (config missing ho to DB me ONGOING mark
            # karke call atka nahi chhodna; seedha 503). Ye sirf compute hai —
            # token tabhi return hota hai jab neeche accept_call sach me allow kare.
            call_row = CallSession.objects.filter(id=call_id).only('channel_name', 'started_at').first()
            if call_row is None:
                return Response({"detail": "Call not found"}, status=404)
            try:
                token_fields = _call_token_fields(call_row)   # C13
                livekit_token = generate_livekit_token(
                    room_name=call_row.channel_name,
                    user_id=request.user.id,
                    user_name=get_display_name(request.user),
                    ttl=timedelta(seconds=token_fields["token_ttl_seconds"]),
                )
            except RuntimeError:
                logger.exception("LiveKit not configured — call accept refused call=%s", call_id)
                return Response(
                    {"detail": "Calling service abhi available nahi hai. Thodi der baad try karo."},
                    status=503,
                )
            if action == 'join':
                result = call_state.join_call(call_id, request.user.id)
            else:
                result = call_state.accept_call(call_id, request.user.id)
        elif action == 'reject':
            result = call_state.reject_call(call_id, request.user.id)
        else:
            result = call_state.end_call(call_id, request.user.id)

        if not result.ok:
            return Response(
                {"detail": result.detail, "code": result.code,
                 "call_status": result.call.status if result.call else None},
                status=result.http_status,
            )

        _dispatch_result(result)

        payload = {
            "detail": f"call {action} done",
            "code": result.code,
            "changed": result.changed,
            "call_status": result.call.status if result.call else None,
            "livekit_url": LIVEKIT_WS_URL if (action in ('accept', 'join') and livekit_token) else None,
            "livekit_token": livekit_token if action in ('accept', 'join') else None,
            **(token_fields if livekit_token else {}),   # C13
        }
        if action in ('accept', 'join') and livekit_token:
            # C16 — recording chal rahi ho to late joiner ko banner + notice isi reply me mile.
            recording_consent.attach_notice(result.call, request.user, payload)
        return Response(payload)


class CallTokenRefreshView(APIView):
    """
    🔥 C13 — POST /message/calls/<call_id>/refresh-token/   (body nahi chahiye)

    Chalti call me LiveKit token expire hone se pehle naya token (client silent refresh).
    Response: {livekit_url, livekit_token, call_id, token_expires_at, token_ttl_seconds,
               session_ends_at, warn_before_seconds, server_time}

    Errors (`{detail, code}`): 404 `not_found`; 403 `not_in_call` (sirf call ka abhi-active
    participant); 410 `call_closed` (call terminal) / `max_duration_reached` (hard cap paar);
    503 LiveKit config missing. Naya token ka TTL hard cap se aage nahi jaata.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [LiveKitTokenRefreshThrottle]

    def post(self, request, call_id):
        call = CallSession.objects.filter(id=call_id).first()
        if call is None:
            return Response({"detail": "Call not found", "code": "not_found"}, status=404)
        if call.status in call_state.TERMINAL_STATUSES:
            return Response({"detail": "Ye call end ho chuki hai.", "code": "call_closed"}, status=410)

        in_call = CallParticipant.objects.filter(
            call=call, user=request.user, status=CallStatus.ONGOING, left_at__isnull=True,
        ).exists()
        if not in_call:
            return Response({"detail": "Aap is call me nahi hain.", "code": "not_in_call"}, status=403)

        ends_at = _call_session_ends_at(call)
        if timezone.now() >= ends_at:
            return Response(
                {"detail": "Call ki maximum duration poori ho gayi.", "code": "max_duration_reached"},
                status=410,
            )

        fields = _call_token_fields(call)
        try:
            token = generate_livekit_token(
                room_name=call.channel_name,
                user_id=request.user.id,
                user_name=get_display_name(request.user),
                ttl=timedelta(seconds=fields["token_ttl_seconds"]),
            )
        except RuntimeError:
            logger.exception("LiveKit not configured — call token refresh refused call=%s", call_id)
            return Response(
                {"detail": "Calling service abhi available nahi hai. Thodi der baad try karo.", "code": "unavailable"},
                status=503,
            )
        payload = {
            "call_id": str(call.id),
            "livekit_url": LIVEKIT_WS_URL,
            "livekit_token": token,
            **fields,
        }
        return Response(recording_consent.attach_notice(call, request.user, payload))   # C16


def _stop_egress_quietly(egress_id, output_path):
    """C16 — start ke baad recheck fail hua to jo egress ab chal gaya use band karo. Best effort."""
    try:
        stop_room_recording(egress_id, output_path)
    except Exception:
        logger.exception("could not stop just-started egress %s", egress_id)


class CallRecordingView(APIView):
    """
    POST /calls/<call_id>/recording/  {"action": "start" | "stop"}

    🔥 TASK 21 — LiveKit server-side room-composite recording, wired up
    for real: `CallSession.is_recording`/`recording_url` used to be
    fields with no start/stop code behind them anywhere (see the
    remove-then-restore history on those fields in models.py). This
    calls into `livekit_utils.start_room_recording`/`stop_room_recording`
    (LiveKit's Egress REST API) and persists the result on the call.

    Recording control is host-only (`call.caller`) — unlike
    `CallActionView`, where each participant only ever controls their
    own leg (accept/reject/end), recording affects everyone in the room,
    so this mirrors Zoom/Meet's "only the host can start/stop the
    recording" convention rather than letting any participant flip it.

    🔥 C16 — recording consent (rules + text: `message/call_recording_consent.py`):
      * `start` 409 `recording_minor_blocked` jab host ya koi active participant minor ho (recording me minor
        kabhi nahi); check do baar hota hai — egress se pehle, aur call row lock ke andar save se pehle
        (accept/join bhi wahi lock lete hain, isliye beech me judta minor chhoot nahi sakta).
      * `recording_started` WS event me `notice` + `sound` (banner + "recording on" tone); baaki participants ko
        `GET .../recording-notice/` + accept/join/refresh replies me `recording_notice` milta hai.
      * start / stop / notice / ack / block — sab `CallRecordingConsent` log me.
    """
    permission_classes = [IsAuthenticated]

    VALID_ACTIONS = ('start', 'stop')

    def post(self, request, call_id):
        rec_action = request.data.get('action')
        if rec_action not in self.VALID_ACTIONS:
            return Response(
                {"detail": f"'action' must be one of {self.VALID_ACTIONS}."},
                status=400,
            )

        call = CallSession.objects.filter(id=call_id).select_related('caller').first()
        if not call:
            return Response({"detail": "Call not found"}, status=404)

        if call.caller_id != request.user.id:
            return Response({"detail": "Sirf call host recording control kar sakta hai."}, status=403)

        if call.status != CallStatus.ONGOING:
            return Response({"detail": "Recording sirf ongoing call me start/stop ho sakti hai."}, status=400)

        if rec_action == 'start':
            if call.is_recording:
                return Response({
                    "detail": "Recording already chal rahi hai.",
                    "is_recording": True,
                    "recording_url": call.recording_url,
                })

            # C16 — minor rule, pehla check (egress shuru karne se PEHLE). DB trouble => fail CLOSED.
            try:
                minor_ids = recording_consent.minors_in_call(call)
            except Exception:
                logger.exception("recording minor check failed call=%s", call_id)
                return Response(
                    {"detail": "Recording abhi start nahi ho sakti, dobara try karo.", "code": "recording_check_failed"},
                    status=503,
                )
            if minor_ids:
                recording_consent.on_blocked_start(call, request.user, minor_ids)
                return Response(recording_consent.block_payload(recording_consent.MINOR_START_MESSAGE_EN), status=409)

            try:
                egress_id, output_path = start_room_recording(call.channel_name)
            except EgressError:
                # 🔥 NOTE: EgressError subclasses RuntimeError, so this
                # branch MUST come before the plain RuntimeError one below
                # — except clauses are checked in order, and the broader
                # type would otherwise swallow this one silently.
                logger.exception("LiveKit egress start failed for call=%s", call_id)
                return Response({"detail": "Recording start nahi ho payi, dobara try karo."}, status=502)
            except RuntimeError as exc:
                # Missing LIVEKIT_API_KEY/SECRET — same lazy-config error
                # shape `generate_livekit_token` raises elsewhere in this
                # view module.
                return Response({"detail": str(exc)}, status=503)

            # C16 — save row lock ke andar + minor recheck: accept/join (call_state) bhi isi row ko lock karte
            # hain, isliye ya to minor pehle commit hua (hum yahan dekhenge) ya hamare commit ke baad block hoga.
            blocked_response = None
            with transaction.atomic():
                locked = CallSession.objects.select_for_update().filter(id=call.id).first()
                if locked is None or locked.status != CallStatus.ONGOING:
                    blocked_response = Response(
                        {"detail": "Recording sirf ongoing call me start/stop ho sakti hai."}, status=400)
                elif locked.is_recording:
                    blocked_response = Response({
                        "detail": "Recording already chal rahi hai.",
                        "is_recording": True,
                        "recording_url": locked.recording_url,
                    })
                else:
                    try:
                        late_minor_ids = recording_consent.minors_in_call(locked)
                    except Exception:
                        logger.exception("recording minor recheck failed call=%s", call_id)
                        late_minor_ids = None
                    if late_minor_ids is None:
                        blocked_response = Response(
                            {"detail": "Recording abhi start nahi ho sakti, dobara try karo.",
                             "code": "recording_check_failed"}, status=503)
                    elif late_minor_ids:
                        recording_consent.on_blocked_start(locked, request.user, late_minor_ids)
                        blocked_response = Response(
                            recording_consent.block_payload(recording_consent.MINOR_START_MESSAGE_EN), status=409)
                    else:
                        locked.is_recording = True
                        locked.recording_egress_id = egress_id
                        locked.recording_started_at = timezone.now()
                        locked.recording_output_path = output_path
                        locked.save(update_fields=[
                            'is_recording', 'recording_egress_id', 'recording_started_at',
                            'recording_output_path', 'updated_at',
                        ])
            if blocked_response is not None:
                # jo egress ab chal gaya wo is call ka nahi rehna chahiye (sirf tab jab hamari start use hui hi nahi).
                if locked is None or locked.recording_egress_id != egress_id:
                    _stop_egress_quietly(egress_id, output_path)
                return blocked_response
            call = locked
            recording_consent.on_started(call, request.user)
            event = 'recording_started'
        else:
            if not call.is_recording or not call.recording_egress_id:
                return Response({"detail": "Koi active recording nahi hai."}, status=400)

            try:
                recording_url = stop_room_recording(call.recording_egress_id, call.recording_output_path)
            except EgressError:
                # See the matching NOTE in the 'start' branch above — this
                # must stay before the plain RuntimeError except clause.
                logger.exception("LiveKit egress stop failed for call=%s", call_id)
                return Response({"detail": "Recording stop nahi ho payi, dobara try karo."}, status=502)
            except RuntimeError as exc:
                return Response({"detail": str(exc)}, status=503)

            call.is_recording = False
            update_fields = ['is_recording']
            if recording_url:
                call.recording_url = recording_url
                update_fields.append('recording_url')
            call.save(update_fields=update_fields)
            recording_consent.on_stopped(call, request.user)   # C16
            event = 'recording_stopped'

        data = {
            'event': event,
            'call_id': str(call_id),
            'is_recording': call.is_recording,
        }
        if event == 'recording_started':
            # C16 — banner + "recording on" sound ke liye notice text (tuitionclass `recording.started` jaisa).
            data['notice'] = recording_consent.broadcast_payload(call)
            data['sound'] = True
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'call_{call_id}',
            {'type': 'call_signal', 'data': data},
        )

        return Response({
            "detail": f"recording {rec_action} done",
            "is_recording": call.is_recording,
            "recording_url": call.recording_url,
        })


def _call_for_participant(call_id, user):
    """C16 — call jisme `user` caller ya CallParticipant ho (warna None => 404, call ka existence leak nahi)."""
    call = CallSession.objects.filter(id=call_id).first()
    if call is None:
        return None
    if call.caller_id == user.id or CallParticipant.objects.filter(call=call, user=user).exists():
        return call
    return None


class CallRecordingNoticeView(APIView):
    """
    GET /calls/<call_id>/recording-notice/   (tuitionclass `recording-notice/` ka call version)

    App isse "Recording on" banner / dialog ka text aur apna `acknowledged` / `ack_required` flag leta hai (call join
    ke baad, aur jab LiveKit room ka recording flag on ho). Sirf call ke caller / participants; baaki 404.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, call_id):
        call = _call_for_participant(call_id, request.user)
        if call is None:
            return Response({"detail": "Call not found", "code": "not_found"}, status=404)
        return Response(recording_consent.notice_for(call, request.user, log_shown=True))


class CallRecordingNoticeAckView(APIView):
    """
    POST /calls/<call_id>/recording-notice/ack/

    "I understand" — consent log me `acknowledged` (ek recording ke liye ek baar; dobara bhejna safe). Sirf abhi
    call me maujood person; 403 `not_in_call`. Adult ke liye ye informational hai (call block nahi hoti), minor
    to recording me aa hi nahi sakta (409 `recording_minor_blocked` accept/join par).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, call_id):
        call = _call_for_participant(call_id, request.user)
        if call is None:
            return Response({"detail": "Call not found", "code": "not_found"}, status=404)
        try:
            payload = recording_consent.acknowledge(call, request.user)
        except PermissionError:
            return Response({"detail": "Aap is call me nahi hain.", "code": "not_in_call"}, status=403)
        return Response(payload)


class CallHistoryViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CallSessionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_queryset(self):
        user = self.request.user
        return CallSession.objects.filter(
            Q(caller=user) | Q(call_participants__user=user) | Q(group__group_members__user=user)
        ).distinct().select_related('caller').order_by('-created_at')

    # 🔥 NAYA — `MissedCallWatcher` (offline→online transition) ye
    # endpoint call karta hai. "Missed" ka matlab: user ko call me invite
    # kiya gaya tha (`CallParticipant` row exists), usne kabhi accept
    # nahi kiya (status abhi bhi RINGING), aur call khatam ho chuka hai.
    # Frontend errors ko silently swallow karta hai (returns []), isliye
    # pehle ye feature chup-chaap kabhi kaam hi nahi karta tha.
    # 🔥 C11 — chat khulte hi "Group call chal rahi hai — Join" banner ke liye.
    # GET /calls/history/active/?conversation_id=<id>
    # Ring cap ke bahar rahe (ya late aaye) member ke paas call ka koi ring/push nahi hota, isliye
    # ye endpoint (aur WS `incoming_call` ka `joinable` flag) hi unka raasta hai.
    @action(detail=False, methods=['get'], url_path='active')
    def active(self, request):
        conversation_id = request.query_params.get('conversation_id')
        if not conversation_id:
            return Response({'detail': 'conversation_id required'}, status=400)
        conversation = Conversation.objects.filter(
            id=conversation_id, memberships__user=request.user, memberships__left_at__isnull=True,
        ).first()
        if conversation is None:
            return Response({'detail': 'Conversation not found'}, status=404)

        call = call_state.active_group_call_for(conversation.id)
        if call is None or not call_state.is_active_member(call, request.user.id):
            return Response({'active': False})

        participants = CallParticipant.objects.filter(call=call)
        active_count = participants.filter(status=CallStatus.ONGOING, left_at__isnull=True).count()
        mine = participants.filter(user=request.user).values_list('status', 'left_at').first()
        my_status = mine[0] if mine else None
        i_am_in = bool(mine and mine[0] == CallStatus.ONGOING and mine[1] is None)
        max_p = call_state.group_max_participants()
        return Response({
            'active': True,
            'call_id': str(call.id),
            'call_type': call.type,
            'call_status': call.status,
            'caller_id': str(call.caller_id),
            'caller_name': get_display_name(call.caller),
            'started_at': call.started_at,
            'participant_count': active_count,
            'max_participants': max_p,
            'is_full': active_count >= max_p,
            'my_status': my_status,
            'i_am_in_call': i_am_in,
            'is_ringing_for_me': my_status == CallStatus.RINGING,
            'can_join': (not i_am_in) and active_count < max_p and my_status != CallStatus.RINGING,
        })

    @action(detail=False, methods=['get'], url_path='missed')
    def missed(self, request):
        since = request.query_params.get('since')
        # 🔥 FIX — pehle `status=RINGING` check karta tha, jo `CallActionView`
        # ke purane bug ki wajah se theek kaam nahi karta tha (never-answered
        # participants hamesha RINGING pade rehte the, call kabhi ENDED hoti
        # hi nahi thi). Ab `CallActionView.end` explicitly `MISSED` status
        # set karta hai jab koi jawab nahi deta, isliye seedha wahi check karo.
        qs = CallParticipant.objects.filter(
            user=request.user,
            status=CallStatus.MISSED,
        ).exclude(call__caller=request.user).select_related('call', 'call__caller')

        if since:
            parsed = parse_datetime(since)
            if parsed is None:
                return Response({'detail': "'since' valid ISO datetime honi chahiye."}, status=400)
            qs = qs.filter(call__created_at__gte=parsed)

        calls = [cp.call for cp in qs.order_by('-call__created_at')]
        serializer = CallSessionSerializer(calls, many=True, context={'request': request})
        return Response(serializer.data)

    # 🔥 NAYA — `_AddParticipantSheet` conversation ke members me se jo
    # abhi call me nahi hain unhe list karta hai (khud ko aur already-
    # invited/participating logon ko exclude karke).
    @action(detail=True, methods=['get'], url_path='addable-participants')
    def addable_participants(self, request, pk=None):
        # 🔥 FIX — `get_object_or_404(CallSession, id=pk)` bypasses this
        # ViewSet's own `get_queryset()` (which is membership-filtered:
        # caller OR call_participant OR group member). That meant ANY
        # authenticated user could hit this on ANY call_id and see who's
        # addable to a call they have nothing to do with — same class of
        # bug `CallActionView.post` already had to fix once with an
        # explicit `is_participant` check. `self.get_object()` runs the
        # filtered queryset (404s for anyone not caller/participant/group
        # member), so this now matches that same guarantee.
        call = self.get_object()

        if call.is_group_call and call.group_id:
            member_ids = set(
                call.group.group_members.filter(is_banned=False).values_list('user_id', flat=True)
            )
        elif call.conversation_id:
            member_ids = set(
                call.conversation.memberships.filter(left_at__isnull=True).values_list('user_id', flat=True)
            )
        else:
            member_ids = set()

        already_in_call = set(
            CallParticipant.objects.filter(call=call).values_list('user_id', flat=True)
        )
        addable_ids = member_ids - already_in_call - {request.user.id}

        users = User.objects.filter(id__in=addable_ids)
        serializer = UserMiniSerializer(users, many=True, context={'request': request})
        return Response(serializer.data)

    # 🔥 NAYA — ongoing (group) call me naya banda add karta hai: uska
    # `CallParticipant` (RINGING) banata hai, normal `incoming_call` push
    # + WS event bhejta hai SAME `call_id` ke saath, taaki accept karne
    # par wo isi LiveKit room me join ho jaaye.
    @action(detail=True, methods=['post'], url_path='add-participant')
    def add_participant(self, request, pk=None):
        # 🔥 FIX — same authorization bypass as `addable_participants`
        # above: `get_object_or_404(CallSession, id=pk)` let any
        # authenticated user add a participant to (i.e. start ringing on)
        # a call they weren't caller/participant/group-member of.
        # `self.get_object()` enforces the ViewSet's membership-filtered
        # `get_queryset()` instead.
        call = self.get_object()
        user_id = request.data.get('user_id')
        if not user_id:
            return Response({'detail': "'user_id' required hai."}, status=400)

        if call.status not in [CallStatus.RINGING, CallStatus.ONGOING]:
            return Response({'detail': 'Ye call ab active nahi hai.'}, status=400)

        # C6 — sirf call me shamil (ONGOING) participant hi kisi ko add kar sakta hai.
        if not CallParticipant.objects.filter(
            call=call, user=request.user, status=CallStatus.ONGOING, left_at__isnull=True,
        ).exists():
            return Response({'detail': 'Aap abhi is call me nahi hain.'}, status=status.HTTP_403_FORBIDDEN)

        target_user = get_object_or_404(User, id=user_id)

        # C6 — pehle koi bhi user_id add ho jaata tha; ab target usi conversation/group
        # ka (abhi tak chhoda/ban na kiya hua) member hona chahiye (addable-participants jaisa).
        if call.is_group_call and call.group_id:
            is_member = call.group.group_members.filter(user_id=target_user.id, is_banned=False).exists()
        elif call.conversation_id:
            is_member = call.conversation.memberships.filter(user_id=target_user.id, left_at__isnull=True).exists()
        else:
            is_member = False
        if not is_member:
            return Response({'detail': 'Ye user is conversation ka member nahi hai.'}, status=status.HTTP_403_FORBIDDEN)

        if is_blocked_pair(request.user.id, target_user.id):
            return Response({'detail': 'Is user ko call me add nahi kiya ja sakta.'}, status=status.HTTP_403_FORBIDDEN)

        # C7 — target dusri call me ho to ring nahi (call-waiting on ho to flag ke saath ring).
        call_waiting = False
        with transaction.atomic():
            call_state.lock_users([target_user.id])
            existing = CallParticipant.objects.select_for_update().filter(call=call, user=target_user).first()
            if existing is not None and existing.status == CallStatus.ONGOING and existing.left_at is None:
                return Response({'detail': 'User already is/was in this call.'}, status=400)
            ring_ids, waiting_ids, busy_ids = call_state.split_callees([target_user.id], exclude_call_id=call.id)
            if busy_ids:
                return Response({
                    'detail': 'Line busy — ye user abhi dusri call par hai.',
                    'code': 'callee_busy',
                }, status=status.HTTP_409_CONFLICT)
            call_waiting = bool(waiting_ids)
            # C11 leftover — DB-level capacity: LiveKit `max_participants` akela rokta tha (ring ke baad join fail).
            # Ab call me maujood (ONGOING) + abhi ring ho rahe (RINGING) milake limit se upar to invite hi nahi.
            # Re-invite (existing row) ko double count na karo.
            occupied = CallParticipant.objects.filter(call=call).filter(
                Q(status=CallStatus.ONGOING, left_at__isnull=True) | Q(status=CallStatus.RINGING)
            ).exclude(user=target_user).count()
            if occupied >= call_state.group_max_participants():
                return Response({
                    'detail': 'Call me jagah nahi hai (participant limit poori).',
                    'code': 'call_full',
                }, status=status.HTTP_409_CONFLICT)
            if existing is None:
                participant = CallParticipant.objects.create(
                    call=call, user=target_user, status=CallStatus.RINGING,
                )
            elif existing.status in (CallStatus.RINGING, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY):
                # Pehle miss/reject/busy hua user dobara bulaya ja sakta hai (re-invite).
                existing.status = CallStatus.RINGING
                existing.left_at = None
                existing.save(update_fields=['status', 'left_at', 'updated_at'])
                participant = existing
            else:
                return Response({'detail': 'User already is/was in this call.'}, status=400)

        if not call.is_group_call:
            call.is_group_call = True
            call.save(update_fields=['is_group_call'])

        caller_name = get_display_name(request.user)

        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_{call.conversation_id}' if call.conversation_id else f'call_{call.id}',
            {
                'type': 'call_event',
                'event': 'incoming_call',
                'call_id': str(call.id),
                'call_type': call.type,
                'caller_id': str(request.user.id),
                'caller_name': caller_name,
                'caller_photo': get_profile_photo_url(request.user, request=request),
                'conversation_id': str(call.conversation_id) if call.conversation_id else None,
                'channel_name': call.channel_name,
                # C7 — sirf invited user ko popup (chat ke baaki sockets ignore karein).
                'ring_user_ids': [str(target_user.id)],
                'call_waiting_user_ids': [str(target_user.id)] if call_waiting else [],
                'busy_user_ids': [],
            }
        )

        send_incoming_call_push(
            recipient_ids=[target_user.id],
            caller_name=caller_name,
            call_type=call.type,
            call_id=call.id,
            conversation_id=call.conversation_id,
            channel_name=call.channel_name,
            call_waiting=call_waiting,
        )

        return Response(CallSessionSerializer(call, context={'request': request}).data, status=201)


# ======================================================================
# 🔥 X1 — OBSERVABILITY: client call telemetry + Prometheus / staff read endpoint (message/call_ops.py)
# ======================================================================
class CallTelemetryView(APIView):
    """
    POST /message/calls/<call_id>/telemetry/    (participant of that call only)

      {"kind": "connect", "ok": true,  "setup_ms": 1840}
      {"kind": "connect", "ok": false, "reason": "ice_failed"}      # reason in call_ops.FAIL_REASONS, else "other"
      {"kind": "summary", "reconnects": 2, "reconnect_ok": 2, "reconnect_fail": 0}

    One `connect` and one `summary` are counted per (call, user) — a retry / double send answers
    `{"counted": false}`. Values are clamped, nothing free-text is stored. Always cheap: no DB write.
    Errors: 400 `invalid_kind`; 403 `not_participant`; 404 `not_found`.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [CallTelemetryThrottle]

    def post(self, request, call_id):
        data = request.data if hasattr(request.data, 'get') else {}
        kind = str(data.get('kind') or '').strip().lower()
        if kind not in ('connect', 'summary'):
            return Response({"detail": "'kind' must be 'connect' or 'summary'.", "code": "invalid_kind"}, status=400)
        if not CallSession.objects.filter(id=call_id).exists():
            return Response({"detail": "Call not found", "code": "not_found"}, status=404)
        if not CallParticipant.objects.filter(call_id=call_id, user=request.user).exists():
            return Response({"detail": "Aap is call ka hissa nahi hain.", "code": "not_participant"}, status=403)

        if kind == 'connect':
            counted = call_ops.record_connect(
                call_id, request.user.id,
                ok=_truthy(data.get('ok')), setup_ms=data.get('setup_ms'), reason=data.get('reason'),
            )
        else:
            counted = call_ops.record_summary(
                call_id, request.user.id,
                reconnects=data.get('reconnects'), reconnect_ok=data.get('reconnect_ok'),
                reconnect_fail=data.get('reconnect_fail'),
            )
        return Response({"counted": bool(counted)})


class CallOpsAPIView(APIView):
    """
    GET /message/calls/ops/?minutes=15        -> JSON (connect-success %, setup ms, ring->answer, reconnects, reasons)
    GET /message/calls/ops/?format=prom       -> Prometheus text (deploy/monitoring/prometheus_call_rules.yml)

    Access: staff JWT OR `Authorization: Bearer <settings.CALL_OPS_SCRAPE_TOKEN>` (falls back to
    FEED_OPS_SCRAPE_TOKEN so one Prometheus credential works for both jobs; empty = token access off).
    Numbers only - no user data.
    """
    permission_classes = [AllowAny]
    authentication_classes = []   # a scrape token is not a user JWT; staff pass their JWT in the header (below)
    throttle_classes = [CallOpsThrottle]

    @staticmethod
    def _allowed(request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False) and (user.is_staff or user.is_superuser):
            return True
        header = request.META.get('HTTP_AUTHORIZATION', '')
        token = (getattr(settings, 'CALL_OPS_SCRAPE_TOKEN', '') or getattr(settings, 'FEED_OPS_SCRAPE_TOKEN', '') or '')
        if token and header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip(), token):
            return True
        if header.startswith('Bearer '):   # a staff user's normal JWT
            try:
                from login.authentication import SlidingSessionAuthentication

                auth = SlidingSessionAuthentication().authenticate(request)
                return bool(auth and (auth[0].is_staff or auth[0].is_superuser))
            except Exception:
                return False
        return False

    def get(self, request):
        if not self._allowed(request):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        if request.query_params.get('format') == 'prom':
            return HttpResponse(call_ops.prometheus_text(), content_type='text/plain; version=0.0.4; charset=utf-8')
        try:
            minutes = min(1440, max(1, int(request.query_params.get('minutes', 15))))
        except (TypeError, ValueError):
            minutes = 15
        summary = call_ops.summarize_last(minutes)
        summary['breaches'] = call_ops.breaches(summary)
        return Response({"success": True, "data": summary})
