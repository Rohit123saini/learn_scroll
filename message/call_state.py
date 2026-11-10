# message/call_state.py
#
# C6 + C7 — call state machine + busy / one-active-call rule.
#
# Ye module sirf DB ka kaam karta hai (koi push / websocket nahi): views.calls
# yahan se `ActionResult` leke broadcast/push karta hai, taaki logic bina
# network ke test ho sake.
#
# STATE MACHINE (CallSession.status)
#
#   initiated ─┐
#   ringing  ──┼──► ongoing ──► ended
#              ├──► rejected      (1:1 me callee ne reject / group me sab ne reject)
#              ├──► cancelled     (caller ne uthne se pehle cut kiya)
#              ├──► missed        (ring timeout — sweeper / koi nahi utha)
#              └──► busy          (callee dusri call me tha)
#
# Terminal (ended/missed/rejected/cancelled/busy) se wapas koi transition nahi.
#
# PARTICIPANT STATE (CallParticipant.status)
#   ringing ──accept──► ongoing ──end/leave──► ended
#   ringing ──reject──► rejected | ──timeout──► missed | busy (group me busy invitee)
#   Group call me `ended` participant (leave kiya) call ongoing hone tak REJOIN kar sakta hai.
#
# Har action:
#   * `select_for_update` se call row lock (accept/reject/end/sweeper ek doosre ke saath race nahi karte)
#   * idempotent — dobara bhejne par 200 + `changed=False` (CallKit timeout / retry safe)
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import CallParticipant, CallSession, CallStatus

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (CallStatus.INITIATED, CallStatus.RINGING, CallStatus.ONGOING)
PRE_CONNECT_STATUSES = (CallStatus.INITIATED, CallStatus.RINGING)
TERMINAL_STATUSES = (
    CallStatus.ENDED, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.BUSY, CallStatus.CANCELLED,
)

ALLOWED_TRANSITIONS = {
    CallStatus.INITIATED: {
        CallStatus.RINGING, CallStatus.ONGOING, CallStatus.ENDED, CallStatus.MISSED,
        CallStatus.REJECTED, CallStatus.CANCELLED, CallStatus.BUSY,
    },
    CallStatus.RINGING: {
        CallStatus.ONGOING, CallStatus.MISSED, CallStatus.REJECTED, CallStatus.CANCELLED,
        CallStatus.BUSY,
    },
    CallStatus.ONGOING: {CallStatus.ENDED, CallStatus.MISSED},
}


def can_transition(old, new):
    return new in ALLOWED_TRANSITIONS.get(old, set())


def _set_status(call, new_status, **fields):
    """Call ka status badlo (sirf valid transition). Save caller karta hai."""
    if call.status != new_status and not can_transition(call.status, new_status):
        logger.warning("call %s: invalid transition %s -> %s ignored", call.id, call.status, new_status)
        return False
    call.status = new_status
    for k, v in fields.items():
        setattr(call, k, v)
    return True


# ----------------------------------------------------------------------
# result type
# ----------------------------------------------------------------------
@dataclass
class ActionResult:
    http_status: int = 200
    code: str = 'ok'
    detail: str = ''
    changed: bool = False
    call: Optional[CallSession] = None
    # WS events (dicts) jo view broadcast karega; har dict me 'event' key.
    events: List[dict] = field(default_factory=list)
    # jinhe ring-cancel push bhejna hai (jo RINGING se MISSED hue)
    cancel_push_user_ids: List = field(default_factory=list)

    @property
    def ok(self):
        return 200 <= self.http_status < 300


def _fail(http_status, code, detail, call=None):
    return ActionResult(http_status=http_status, code=code, detail=detail, call=call)


def _lock_call(call_id):
    return CallSession.objects.select_for_update().filter(id=call_id).first()


def _duration(call, now):
    if not call.connected_at:
        return 0
    return max(int((now - call.connected_at).total_seconds()), 0)


def _counts(call):
    qs = CallParticipant.objects.filter(call=call)
    pending = qs.filter(status=CallStatus.RINGING).count()
    active = qs.filter(status=CallStatus.ONGOING, left_at__isnull=True).count()
    return pending, active


def _event(name, call, user_id, **extra):
    data = {'event': name, 'user_id': str(user_id), 'call_id': str(call.id),
            'call_status': call.status, 'is_group_call': bool(call.is_group_call)}
    data.update(extra)
    return data


def _miss_pending(call, now):
    """Jo abhi RINGING hain unhe MISSED karo; unke user ids return."""
    ids = list(
        CallParticipant.objects.filter(call=call, status=CallStatus.RINGING)
        .values_list('user_id', flat=True)
    )
    if ids:
        CallParticipant.objects.filter(call=call, status=CallStatus.RINGING).update(
            status=CallStatus.MISSED, left_at=now,
        )
    return ids


# ----------------------------------------------------------------------
# C16 — recording + minor rule (call_recording_consent.py). Row lock ke ANDAR chalta hai, isliye host ke
# `start` (jo wahi row lock karke dobara check karta hai) se race nahi karta.
# ----------------------------------------------------------------------
def _recording_minor_block(call, user_id):
    """ActionResult (409 `recording_minor_blocked`) agar `user_id` minor hai aur call record ho rahi hai, warna None."""
    from . import call_recording_consent as consent

    if consent.join_blocked_for(call, user_id):
        return _fail(409, consent.MINOR_BLOCK_CODE, consent.MINOR_JOIN_MESSAGE_EN, call)
    return None


# ----------------------------------------------------------------------
# ACCEPT
# ----------------------------------------------------------------------
def accept_call(call_id, user_id) -> ActionResult:
    now = timezone.now()
    with transaction.atomic():
        call = _lock_call(call_id)
        if call is None:
            return _fail(404, 'not_found', 'Call not found')
        part = CallParticipant.objects.select_for_update().filter(call=call, user_id=user_id).first()
        if part is None:
            return _fail(403, 'not_participant', 'Aap is call ka hissa nahi hain.', call)

        if call.status in TERMINAL_STATUSES:
            return _fail(400, 'call_closed', 'Ye call already end ho chuki hai.', call)

        if part.status == CallStatus.ONGOING and part.left_at is None:
            # Idempotent re-accept (retry / cold-start / reconnect): token dobara milega.
            return ActionResult(code='already_joined', call=call)

        rejoin = (
            part.status == CallStatus.ENDED and call.is_group_call
            and call.status == CallStatus.ONGOING
        )
        if part.status != CallStatus.RINGING and not rejoin:
            return _fail(400, 'invalid_state',
                         'Is call me ab join nahi kiya ja sakta (aap reject/miss/leave kar chuke hain).', call)

        blocked = _recording_minor_block(call, user_id)   # C16
        if blocked is not None:
            return blocked

        part.status = CallStatus.ONGOING
        part.left_at = None
        part.save(update_fields=['status', 'left_at', 'updated_at'])

        if call.status in PRE_CONNECT_STATUSES:
            _set_status(call, CallStatus.ONGOING)
        if call.connected_at is None:
            call.connected_at = now
        call.save(update_fields=['status', 'connected_at', 'updated_at'])

    res = ActionResult(code='accepted', changed=True, call=call)
    res.events.append(_event('call_accept', call, user_id))
    return res


# ----------------------------------------------------------------------
# REJECT
# ----------------------------------------------------------------------
def reject_call(call_id, user_id) -> ActionResult:
    now = timezone.now()
    with transaction.atomic():
        call = _lock_call(call_id)
        if call is None:
            return _fail(404, 'not_found', 'Call not found')
        part = CallParticipant.objects.select_for_update().filter(call=call, user_id=user_id).first()
        if part is None:
            return _fail(403, 'not_participant', 'Aap is call ka hissa nahi hain.', call)

        if call.status in TERMINAL_STATUSES:
            return ActionResult(code='already_closed', call=call)  # idempotent no-op

        # Caller khud "reject" bheje (CallKit timeout) = apni call cancel/end karna.
        if call.caller_id == user_id:
            pass  # neeche end_call me delegate (transaction ke bahar)
        elif part.status in (CallStatus.REJECTED, CallStatus.MISSED, CallStatus.BUSY, CallStatus.ENDED):
            return ActionResult(code='already_left', call=call)
        elif part.status == CallStatus.ONGOING and part.left_at is None:
            return _fail(409, 'already_joined', 'Aap call me pehle se join ho chuke hain.', call)
        else:
            part.status = CallStatus.REJECTED
            part.left_at = now
            part.save(update_fields=['status', 'left_at', 'updated_at'])

            res = ActionResult(code='rejected', changed=True, call=call)
            pending, active = _counts(call)
            closed = False
            if not call.is_group_call:
                # 1:1 — callee ne mana kiya = call khatam.
                if _set_status(call, CallStatus.REJECTED, ended_at=now):
                    CallParticipant.objects.filter(call=call, left_at__isnull=True).update(
                        status=CallStatus.ENDED, left_at=now)
                    call.is_recording = False
                    call.save(update_fields=['status', 'ended_at', 'is_recording', 'updated_at'])
                    closed = True
            elif call.status in PRE_CONNECT_STATUSES and pending == 0 and active <= 1:
                # Group — sabne reject/miss kar diya, sirf caller bacha: call khatam.
                if _set_status(call, CallStatus.REJECTED, ended_at=now):
                    CallParticipant.objects.filter(
                        call=call, status=CallStatus.ONGOING, left_at__isnull=True,
                    ).update(status=CallStatus.ENDED, left_at=now)
                    call.is_recording = False
                    call.save(update_fields=['status', 'ended_at', 'is_recording', 'updated_at'])
                    closed = True

            # Group me bhi baaki sabko pata chale ki kisne reject kiya aur kitne bache.
            res.events.append(_event('call_reject', call, user_id,
                                     pending_count=pending, active_count=active))
            if closed:
                res.events.append(_event('call_end', call, call.caller_id,
                                         reason='declined' if not call.is_group_call else 'all_declined',
                                         call_closed=True))
            return res

    # Caller ne reject bheja — end me route karo.
    return end_call(call_id, user_id)


# ----------------------------------------------------------------------
# END
# ----------------------------------------------------------------------
def end_call(call_id, user_id) -> ActionResult:
    now = timezone.now()
    with transaction.atomic():
        call = _lock_call(call_id)
        if call is None:
            return _fail(404, 'not_found', 'Call not found')
        part = CallParticipant.objects.select_for_update().filter(call=call, user_id=user_id).first()
        if part is None:
            return _fail(403, 'not_participant', 'Aap is call ka hissa nahi hain.', call)

        if call.status in TERMINAL_STATUSES:
            return ActionResult(code='already_closed', call=call)  # idempotent

        is_caller = call.caller_id == user_id

        # --- A) abhi connect nahi hui (ringing) ---
        if call.status in PRE_CONNECT_STATUSES:
            if not is_caller:
                if part.status == CallStatus.RINGING:
                    pass  # reject me delegate (transaction ke bahar)
                else:
                    return ActionResult(code='already_left', call=call)
            else:
                missed_ids = _miss_pending(call, now)
                CallParticipant.objects.filter(
                    call=call, status=CallStatus.ONGOING, left_at__isnull=True,
                ).update(status=CallStatus.ENDED, left_at=now)
                _set_status(call, CallStatus.CANCELLED, ended_at=now)
                call.is_recording = False
                call.save(update_fields=['status', 'ended_at', 'is_recording', 'updated_at'])
                res = ActionResult(code='cancelled', changed=True, call=call,
                                   cancel_push_user_ids=missed_ids)
                res.events.append(_event('call_end', call, user_id, reason='cancelled', call_closed=True))
                return res

        # --- B) ongoing ---
        else:
            if part.status != CallStatus.ONGOING or part.left_at is not None:
                return ActionResult(code='already_left', call=call)

            part.status = CallStatus.ENDED
            part.left_at = now
            part.save(update_fields=['status', 'left_at', 'updated_at'])

            missed_ids = []
            closed = False
            if not call.is_group_call:
                # 1:1 — ek ke end karte hi dono taraf ki call khatam.
                CallParticipant.objects.filter(
                    call=call, status=CallStatus.ONGOING, left_at__isnull=True,
                ).update(status=CallStatus.ENDED, left_at=now)
                missed_ids = _miss_pending(call, now)
                closed = True
            else:
                _pending, active = _counts(call)
                if active == 0:
                    missed_ids = _miss_pending(call, now)
                    closed = True

            if closed:
                _set_status(call, CallStatus.ENDED, ended_at=now, duration_seconds=_duration(call, now))
                call.is_recording = False
                call.save(update_fields=['status', 'ended_at', 'duration_seconds', 'is_recording', 'updated_at'])

            res = ActionResult(code='ended' if closed else 'left', changed=True, call=call,
                               cancel_push_user_ids=missed_ids)
            res.events.append(_event('call_end', call, user_id,
                                     reason='ended' if closed else 'left', call_closed=closed))
            return res

    # Callee ne ringing me "end" bheja = reject.
    return reject_call(call_id, user_id)


# ----------------------------------------------------------------------
# BUSY / ONE ACTIVE CALL  (C7)
# ----------------------------------------------------------------------
def _ring_window_seconds():
    # sweeper ke ring timeout + chhota grace (sweeper har 30s chalta hai)
    return int(getattr(settings, 'CALL_RING_TIMEOUT_SECONDS', 45)) + 15


def _stale_ongoing_seconds():
    return int(getattr(settings, 'CALL_STALE_ONGOING_SECONDS', 3 * 3600))


def call_max_duration_seconds():
    """🔥 C13 — call ki hard cap = wahi jo sweeper (`sweep_stale_calls`, 'max_duration') lagata hai:
    `started_at + CALL_STALE_ONGOING_SECONDS`. Token refresh aur client warning isi se align hain."""
    return _stale_ongoing_seconds()


def call_waiting_enabled():
    return bool(getattr(settings, 'CALL_WAITING_ENABLED', False))


BUSY_IN_CALL = 'in_call'              # abhi kisi call me hai
BUSY_RINGING_ELSEWHERE = 'ringing'    # kisi aur call ki ring abhi chal rahi hai


def busy_status(user_ids, exclude_call_id=None):
    """
    user_ids me se jo log abhi dusri call me hain unka {user_id: (kind, call)}.
    Ghost rows (sweeper ne abhi tak band nahi kiye) freshness window se ignore
    hote hain, taaki ek atki hui row kisi ko permanently "busy" na kare.
    """
    ids = [u for u in user_ids if u is not None]
    if not ids:
        return {}
    now = timezone.now()
    ring_cut = now - timedelta(seconds=_ring_window_seconds())
    ongoing_cut = now - timedelta(seconds=_stale_ongoing_seconds())

    rows = CallParticipant.objects.filter(
        user_id__in=ids, left_at__isnull=True, call__status__in=ACTIVE_STATUSES,
    ).select_related('call')
    if exclude_call_id is not None:
        rows = rows.exclude(call_id=exclude_call_id)

    out = {}
    for cp in rows:
        call = cp.call
        kind = None
        if cp.status == CallStatus.ONGOING:
            if call.status in PRE_CONNECT_STATUSES:
                if call.started_at >= ring_cut:      # caller ki outgoing ring
                    kind = BUSY_IN_CALL
            elif call.started_at >= ongoing_cut:
                kind = BUSY_IN_CALL
        elif cp.status == CallStatus.RINGING:
            if call.status in PRE_CONNECT_STATUSES and call.started_at >= ring_cut:
                kind = BUSY_RINGING_ELSEWHERE
            elif call.status == CallStatus.ONGOING and cp.joined_at >= ring_cut:
                kind = BUSY_RINGING_ELSEWHERE        # add-participant invite
        if kind and (cp.user_id not in out or kind == BUSY_IN_CALL):
            out[cp.user_id] = (kind, call)
    return out


def split_callees(callee_ids, exclude_call_id=None):
    """
    Returns (ring_ids, waiting_ids, busy_ids):
      ring_ids    — normal ring
      waiting_ids — in-call par `CALL_WAITING_ENABLED` ho to ring + call_waiting flag
      busy_ids    — ring nahi hogi
    """
    info = busy_status(callee_ids, exclude_call_id=exclude_call_id)
    ring, waiting, busy = [], [], []
    for uid in callee_ids:
        entry = info.get(uid)
        if entry is None:
            ring.append(uid)
        elif entry[0] == BUSY_IN_CALL and call_waiting_enabled():
            waiting.append(uid)
        else:
            busy.append(uid)
    return ring, waiting, busy


# ----------------------------------------------------------------------
# C11 — group call scale: ring cap + late JOIN
# ----------------------------------------------------------------------
def group_ring_cap():
    """Group call me max kitne invitees ko ring/push (baaki ko chat me Join banner)."""
    return max(int(getattr(settings, 'CALL_GROUP_RING_CAP', 10)), 1)


def group_max_participants():
    """Ek group call me max ONGOING participants (LiveKit room cap bhi yahi)."""
    return max(int(getattr(settings, 'CALL_GROUP_MAX_PARTICIPANTS', 50)), 2)


def _online_first(user_ids):
    """Stable order, par abhi online wale pehle (ring unhe milni chahiye jo sach me phone dekh rahe hain)."""
    ids = list(user_ids)
    if len(ids) <= 1:
        return ids
    try:
        from .models import UserPresence
        online = set(UserPresence.objects.filter(user_id__in=ids, is_online=True)
                     .values_list('user_id', flat=True))
    except Exception:
        online = set()
    return [u for u in ids if u in online] + [u for u in ids if u not in online]


def apply_ring_cap(ring_ids, waiting_ids, cap=None):
    """
    (ring_ids, waiting_ids) me se sirf `cap` invitees ko ring; baaki `overflow_ids`.
    Free (ring) users ko pehle slot milte hain (online pehle), call-waiting wale bache
    hue slots me. Returns (ring, waiting, overflow).
    """
    cap = cap or group_ring_cap()
    ordered = _online_first(ring_ids)
    ring = ordered[:cap]
    left = cap - len(ring)
    waiting_all = _online_first(waiting_ids)
    waiting = waiting_all[:max(left, 0)]
    overflow = ordered[cap:] + waiting_all[max(left, 0):]
    return ring, waiting, overflow


def is_active_member(call, user_id):
    """User is call ki conversation (aur group ho to group) ka abhi-tak-chhoda/ban-na-kiya member hai?"""
    if not call.conversation_id:
        return False
    from .models import ConversationParticipant, GroupMember
    if not ConversationParticipant.objects.filter(
        conversation_id=call.conversation_id, user_id=user_id, left_at__isnull=True,
    ).exists():
        return False
    if call.group_id:
        return GroupMember.objects.filter(group_id=call.group_id, user_id=user_id, is_banned=False).exists()
    return True


def join_call(call_id, user_id) -> ActionResult:
    """
    C11 — group call me bina ring ke JOIN (ring cap se bahar rah gaye members, ya busy/miss/reject
    kar chuke member jo baad me judna chahte hain). Sirf group call, sirf ringing/ongoing.

    Rules: active member hona chahiye; room full (`CALL_GROUP_MAX_PARTICIPANTS`) => 409 `call_full`;
    user dusri call me ho => 409 `already_in_call`; already ONGOING => idempotent `already_joined`.
    Participant row yahin banti hai (socket / webhook se nahi — wahan invite wali rule hi rahe).
    """
    now = timezone.now()
    with transaction.atomic():
        call = _lock_call(call_id)
        if call is None:
            return _fail(404, 'not_found', 'Call not found')
        if not call.is_group_call:
            return _fail(400, 'not_group_call', 'Sirf group call me join kiya ja sakta hai.', call)
        if call.status in TERMINAL_STATUSES:
            return _fail(400, 'call_closed', 'Ye call already end ho chuki hai.', call)
        if not is_active_member(call, user_id):
            return _fail(403, 'not_member', 'Aap is group ke member nahi hain.', call)

        part = CallParticipant.objects.select_for_update().filter(call=call, user_id=user_id).first()
        if part is not None and part.status == CallStatus.ONGOING and part.left_at is None:
            return ActionResult(code='already_joined', call=call)

        blocked = _recording_minor_block(call, user_id)   # C16
        if blocked is not None:
            return blocked
        active = CallParticipant.objects.filter(
            call=call, status=CallStatus.ONGOING, left_at__isnull=True,
        ).count()
        if active >= group_max_participants():
            return _fail(409, 'call_full', 'Call me jagah nahi hai (participant limit poori).', call)

        if busy_status([user_id], exclude_call_id=call.id):
            return _fail(409, 'already_in_call', 'Aap pehle se dusri call me hain.', call)

        if part is None:
            CallParticipant.objects.create(call=call, user_id=user_id, status=CallStatus.ONGOING)
        else:
            part.status = CallStatus.ONGOING
            part.left_at = None
            part.save(update_fields=['status', 'left_at', 'updated_at'])

        if call.status in PRE_CONNECT_STATUSES:
            _set_status(call, CallStatus.ONGOING)
        if call.connected_at is None:
            call.connected_at = now
        call.save(update_fields=['status', 'connected_at', 'updated_at'])

    pending, active = _counts(call)
    res = ActionResult(code='joined', changed=True, call=call)
    res.events.append(_event('call_accept', call, user_id, pending_count=pending,
                             active_count=active, joined_via='join'))
    return res


def active_group_call_for(conversation_id):
    """Conversation ki abhi chal rahi (ringing/ongoing, ghost nahi) group call, ya None."""
    now = timezone.now()
    ring_cut = now - timedelta(seconds=_ring_window_seconds())
    ongoing_cut = now - timedelta(seconds=_stale_ongoing_seconds())
    qs = CallSession.objects.filter(
        conversation_id=conversation_id, is_group_call=True, status__in=ACTIVE_STATUSES,
    ).select_related('caller').order_by('-started_at')
    for call in qs[:3]:
        if call.status in PRE_CONNECT_STATUSES:
            if call.started_at >= ring_cut:
                return call
        elif call.started_at >= ongoing_cut:
            return call
    return None


def lock_users(user_ids):
    """
    Ek hi user par do saath-saath initiate (A->B aur C->B) ke race ko
    serialize karne ke liye user rows lock karo (id order me => deadlock nahi).
    Sirf transaction.atomic ke andar kaam karta hai; SQLite par no-op.
    """
    from django.contrib.auth import get_user_model
    ids = sorted({str(u) for u in user_ids if u is not None})
    if not ids:
        return
    list(get_user_model().objects.select_for_update().filter(pk__in=ids).order_by('pk').values_list('pk', flat=True))


def force_leave_stale_calls(user_id, now=None):
    """
    Caller ki apni purani atki hui call (replace_active=true) ko uski taraf se
    end karo. Returns list[ActionResult] (view ko events/pushes bhejne hain).
    """
    results = []
    for _uid, (_kind, call) in busy_status([user_id]).items():
        res = end_call(call.id, user_id)
        if res.ok:
            results.append(res)
    return results
