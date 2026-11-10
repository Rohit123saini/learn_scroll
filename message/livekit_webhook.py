"""
message/livekit_webhook.py

🔥 C2 — LiveKit webhook for 1-1 / group CALLS (message app).

Pehle sirf `tuitionclass` ka webhook tha; message calls ka asli
participant join/leave, room_finished aur recording `egress_ended`
server ko pata hi nahi chalta tha (livekit_utils.stop_room_recording ka
docstring bhi yahi gap bata raha tha). Ab:

  participant_joined          -> CallParticipant ONGOING (callee ke join par call connected)
  participant_left /
  participant_connection_aborted -> CallParticipant ENDED; connected call me koi
                                    ONGOING nahi bacha to call ended
  room_finished               -> call ended (app-kill ke baad ghost ONGOING participants bhi)
  egress_ended                -> CallSession.recording_url / is_recording
  (S5) study_<conv> rooms: participant joined/left/room_finished -> `study_room_presence` (attendance minutes)

Sab handlers IDEMPOTENT hain (LiveKit retry/duplicate delivery safe) aur kabhi
raise nahi karte — view hamesha 200 deta hai (signature fail par 401).

Wiring (dono me se koi bhi, ya dono — idempotent hai):
  * LiveKit `webhook.urls` me  https://<api-host>/message/livekit/webhook/  add karo, YA
  * purana tuitionclass URL hi rakho — `tuitionclass.views.sessions.LiveKitWebhookView`
    ab `call_` rooms ka event yahin forward karta hai.

Sirf `CallSession.channel_name == room.name` wale rooms (`call_<hex>`) chhue
jaate hain; study rooms / class rooms ignore hote hain.
"""

import logging

from django.db import transaction
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .livekit_utils import (
    WebhookVerificationError,
    _build_recording_url,
    verify_webhook_event,
)
from .models import CallParticipant, CallSession, CallStatus

logger = logging.getLogger(__name__)

CALL_ROOM_PREFIX = 'call_'
_TERMINAL = (CallStatus.ENDED, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY, CallStatus.CANCELLED)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _room_name(event):
    return getattr(getattr(event, 'room', None), 'name', '') or ''


def _user_id_from_identity(participant):
    """LiveKit identity = backend ka user id (`generate_livekit_token`).
    Egress/ingress/agent bots ("EG_...") user nahi hain — unhe None."""
    identity = (getattr(participant, 'identity', '') or '').strip()
    if not identity or identity.startswith(('EG_', 'IN_', 'ST_', 'AJ_')):
        return None
    return identity


def _get_call(room_name):
    if not room_name.startswith(CALL_ROOM_PREFIX):
        return None
    return CallSession.objects.filter(channel_name=room_name).first()


def _broadcast(call, event_name, **extra):
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(
            f'call_{call.id}',
            {'type': 'call_signal', 'data': {'event': event_name, 'call_id': str(call.id), **extra}},
        )
    except Exception:
        logger.warning("livekit_webhook: broadcast %s failed call=%s", event_name, call.id, exc_info=True)


# ----------------------------------------------------------------------
# event handlers
# ----------------------------------------------------------------------
def _minor_joined_recorded_call(call, uid):
    """C16 — True agar `uid` minor hai aur is call ki recording abhi chal rahi hai (taaza DB state, lock ke bina:
    webhook REST se alag process me chalta hai). Consent log me `blocked_minor_join` isi call se likha jaata hai."""
    from . import call_recording_consent as consent

    fresh = CallSession.objects.filter(id=call.id).first()
    return bool(fresh is not None and consent.join_blocked_for(fresh, uid))


def _on_participant_joined(event):
    call = _get_call(_room_name(event))
    uid = _user_id_from_identity(getattr(event, 'participant', None))
    if call is None or uid is None or call.status in _TERMINAL:
        return
    blocked_minor = False
    with transaction.atomic():
        cp = CallParticipant.objects.select_for_update().filter(call=call, user_id=uid).first()
        if cp is None:
            return  # invited nahi hai — token sirf invited ko milta hai, ignore
        if cp.status in (CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY):
            return  # pehle hi missed/reject — late join ko resurrect nahi karna
        # C16 — recording chal rahi hai aur joiner minor hai: REST accept/join pehle hi 409 deta hai, ye us raaste
        # ke liye hai jab minor purane valid LiveKit token se seedha room me aa jaye. ONGOING mat karo, room se nikaalo.
        if _minor_joined_recorded_call(call, uid):
            blocked_minor = True
            if cp.status != CallStatus.ENDED or cp.left_at is None:
                cp.status = CallStatus.ENDED
                cp.left_at = timezone.now()
                cp.save(update_fields=['status', 'left_at', 'updated_at'])
        else:
            cp.status = CallStatus.ONGOING
            cp.left_at = None
            cp.save(update_fields=['status', 'left_at', 'updated_at'])
            # Callee (caller nahi) ke join par call "connected" maani jaati hai
            # (accept REST pehle hi set kar chuka hota hai — ye sirf safety net).
            if call.connected_at is None and call.caller_id != cp.user_id:
                call.connected_at = timezone.now()
                call.status = CallStatus.ONGOING
                call.save(update_fields=['connected_at', 'status', 'updated_at'])
    if blocked_minor:
        from .livekit_utils import remove_participant

        remove_participant(call.channel_name, uid)


def _on_participant_left(event):
    call = _get_call(_room_name(event))
    uid = _user_id_from_identity(getattr(event, 'participant', None))
    if call is None or uid is None or call.status in _TERMINAL:
        return
    now = timezone.now()
    CallParticipant.objects.filter(
        call=call, user_id=uid, status=CallStatus.ONGOING, left_at__isnull=True,
    ).update(status=CallStatus.ENDED, left_at=now)

    # Ringing me (koi connect nahi hua) caller ka disconnect call khatam
    # nahi karta — `sweep_stale_calls` ring timeout / explicit end sambhalta hai.
    call.refresh_from_db(fields=['connected_at', 'status'])
    if call.connected_at is None or call.status in _TERMINAL:
        return
    still_active = CallParticipant.objects.filter(
        call=call, status=CallStatus.ONGOING, left_at__isnull=True,
    ).exists()
    if not still_active:
        from .tasks import _finish_stale_call
        _finish_stale_call(call.id, now, 'webhook_all_left')


def _on_room_finished(event):
    call = _get_call(_room_name(event))
    if call is None or call.status in _TERMINAL:
        return
    # Room khali hokar band hua = media session khatam. Jo participants
    # REST se ONGOING the par kabhi LiveKit me aaye hi nahi / app kill hua,
    # unka bhi ghost status yahin saaf hota hai.
    from .tasks import _finish_stale_call
    _finish_stale_call(call.id, timezone.now(), 'webhook_room_finished')


def _on_egress_ended(event):
    info = getattr(event, 'egress_info', None)
    egress_id = getattr(info, 'egress_id', '') if info is not None else ''
    if not egress_id:
        return
    call = CallSession.objects.filter(recording_egress_id=egress_id).first()
    if call is None:
        return  # tuitionclass / campus recording — unka apna handler hai

    url = ''
    for fr in list(getattr(info, 'file_results', []) or []):
        loc = getattr(fr, 'location', '') or ''
        if loc.startswith(('http://', 'https://')):
            url = loc
            break
    # Private S3 location ke bajay configured public/CDN base ko prefer karo.
    public = _build_recording_url(call.recording_output_path) if call.recording_output_path else None
    url = public or url

    failed = False
    try:
        from livekit import api
        failed = api.EgressStatus.Name(info.status) in ('EGRESS_FAILED', 'EGRESS_ABORTED', 'EGRESS_LIMIT_REACHED')
    except Exception:
        failed = False

    fields = []
    if call.is_recording:
        call.is_recording = False
        fields.append('is_recording')
    if url and not failed and call.recording_url != url[:500]:
        call.recording_url = url[:500]
        fields.append('recording_url')
    if fields:
        call.save(update_fields=fields + ['updated_at'])
    if failed:
        logger.warning("livekit_webhook: egress %s failed for call=%s", egress_id, call.id)
        _broadcast(call, 'recording_failed', is_recording=False)
    elif url:
        _broadcast(call, 'recording_ready', is_recording=False, recording_url=call.recording_url)


_HANDLERS = {
    'participant_joined': _on_participant_joined,
    'participant_left': _on_participant_left,
    'participant_connection_aborted': _on_participant_left,
    'room_finished': _on_room_finished,
    'egress_ended': _on_egress_ended,
}


def handle_message_event(event):
    """Public entry point (tuitionclass webhook bhi ise forward karta hai).
    Kabhi raise nahi karta.

    S5 — `study_<conv>[_suffix]` rooms ke join / leave / room_finished events `study_room_presence` ko jaate hain
    (attendance minutes). Call handlers `call_` rooms tak, presence handlers `study_` rooms tak simit hain —
    dono ek doosre ko nahi chhuate."""
    ok = False
    handler = _HANDLERS.get(getattr(event, 'event', ''))
    if handler is not None:
        try:
            handler(event)
            ok = True
        except Exception:
            logger.exception("livekit_webhook: handler failed event=%s", getattr(event, 'event', ''))

    try:
        from .study_room_presence import handle_webhook_event as handle_study_presence

        if handle_study_presence(event):
            ok = True
    except Exception:
        logger.exception("livekit_webhook: study presence failed event=%s", getattr(event, 'event', ''))
    return ok


# ----------------------------------------------------------------------
# view
# ----------------------------------------------------------------------
class LiveKitCallWebhookView(APIView):
    """POST /message/livekit/webhook/ — server-to-server. Signature hi
    asli access control hai (tuitionclass.LiveKitWebhookView jaisa), isliye
    user auth aur per-IP throttle dono band."""
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []

    def post(self, request):
        try:
            event = verify_webhook_event(request.body, request.headers.get('Authorization', ''))
        except WebhookVerificationError:
            return Response({'detail': 'Invalid webhook signature.'}, status=401)
        except RuntimeError as exc:  # LIVEKIT_API_KEY/SECRET set nahi
            logger.error("livekit_webhook: not configured: %s", exc)
            return Response({'detail': 'LiveKit not configured.'}, status=503)
        handle_message_event(event)
        return Response(status=200)
