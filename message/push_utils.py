# message/push_utils.py
import os
import json
import logging
import time
from datetime import timedelta

import firebase_admin
from firebase_admin import credentials, messaging
from django.core.cache import cache
from django.utils import timezone

from .models import DeviceToken, FocusSession, Message  # 🔥 NAYA — FocusSession, Feature 12 ke baad models.py me merge hone se yahan aayega

# 🔥 NAYA (task 44 — core-app migration) — is module ka kaam ab sirf FCM
# push bhejna nahi hai; har push ke saath ek matching bell-row
# (core.models.Notification) bhi banta hai, taaki in-app notification
# list bhi update ho, sirf phone ka push tray nahi. create_notification()
# apna alag choke-point hai (core/services.py) — ye KABHI khud push nahi
# bhejta, sirf DB row likhta hai, isliye yahan se call karna safe hai
# (double-push ka risk nahi).
from core.models import Notification, NotificationPreference
from core import notification_reminders as reminders  # N6 - daily cap / collapse / snooze
from core import notification_rich as rich  # N7 - avatar / group key / Mark as read + Join now / icon badge
from core import notification_center as center  # N9 - class / chat / post mute + per-category sound / vibration
from core import notification_ops as ops  # N10 - sent / delivered / skipped / failed counters per notif type
from core.services import build_push_payload, create_notification

from . import push_reliability  # N3 - dedupe / retry / dead-token hygiene
from .push_channels import CHANNEL_CALLS, channel_for_notif_type  # N3 - Android channel per category

logger = logging.getLogger(__name__)

# 🔥 NAYA (Feature 12 — Smart DND) — kisi bhi chat-push ko bhejne se
# PEHLE, recipient list me se un users ko hata do jinka Focus Mode active
# hai aur ye push unke exception-rule me qualify nahi karta. Ye single
# choke-point hai (har chat push isi se ho kar guzarta hai), isliye
# `views.py`/`consumers.py` me kahin bhi extra check nahi lagani padi.
def _filter_recipients_for_focus(recipient_ids, *, is_announcement):
    """
    recipient_ids: list of user-id (str/int mix chalega)
    is_announcement: True agar sender teacher/staff/group-admin hai
        (caller `views.py`/`consumers.py` se, jahan group role check
        already ho chuka hota hai message-send-permission ke waqt —
        yahan dobara role-lookup query nahi karni padi).

    Returns: filtered list (jinhe push jaana chahiye).
    """
    if not recipient_ids:
        return recipient_ids

    ids = [str(r) for r in recipient_ids]
    now = timezone.now()

    # Ek hi query se sabke active sessions utha lo (N+1 se bachne ke liye).
    active_sessions = {
        str(s.user_id): s
        for s in FocusSession.objects.filter(
            user_id__in=ids, ends_at__gt=now, cancelled_at__isnull=True
        )
    }

    if not active_sessions:
        return recipient_ids  # fast path — kisi ka bhi focus mode on nahi

    allowed = []
    for uid in ids:
        session = active_sessions.get(uid)
        if session is None:
            allowed.append(uid)
            continue
        if session.exception_rule == FocusSession.ExceptionRule.NOBODY:
            continue  # hard mode — koi bhi exception nahi, teacher bhi mute
        if session.exception_rule == FocusSession.ExceptionRule.TEACHERS_ONLY and is_announcement:
            allowed.append(uid)  # teacher/staff ka message — through jaane do
        # warna (chit-chat, focus on) — silently drop, koi push nahi
    return allowed

# Issue #2 (RestrictUser) — recipient A who has restricted sender B must
# get NO push and NO bell row for B's messages/mentions; B must never be
# able to tell (the message itself is still delivered/stored normally, and
# nothing here errors). The sender is looked up from `message_id` rather
# than a new parameter so the ~8 existing call-sites (views/consumers/
# offline_queue/scheduled_messages) need no change. Never raises: on any
# lookup failure the recipients are returned unfiltered (a leaked push is
# better than a silently dropped one for everyone).
def _filter_recipients_for_restrict(recipient_ids, message_id):
    if not recipient_ids or not message_id:
        return recipient_ids
    try:
        from user_profile.services import restrictor_ids_of

        sender_id = (
            Message.objects.filter(pk=message_id).values_list("sender_id", flat=True).first()
        )
        if sender_id is None:
            return recipient_ids
        restrictors = {str(r) for r in restrictor_ids_of(sender_id, among=recipient_ids)}
        if not restrictors:
            return recipient_ids
        return [r for r in recipient_ids if str(r) not in restrictors]
    except Exception:
        logger.exception("restrict filter failed for message %s; sending unfiltered", message_id)
        return recipient_ids


# 🔥 NAYA (M1-BE — Message requests) — pending request ke receiver ko normal
# chat push/bell row NAHI milta (message ka text kisi anjaan ka hai, tray me
# nahi dikhna chahiye). Unhe ek hi generic "Message request" push milta hai,
# har (user, conversation) ke liye `MESSAGE_REQUEST_PUSH_TTL_SECONDS` me sirf
# ek baar — baaki messages silent rehte hain. Declined receivers ko kuch nahi.
# Returns (accepted_ids, pending_ids). Never raises: lookup fail ho to sab
# recipients accepted maane jaate hain (fail-open, jaise restrict filter).
MESSAGE_REQUEST_PUSH_TTL_SECONDS = int(os.getenv("MESSAGE_REQUEST_PUSH_TTL_SECONDS", "86400"))
_REQUEST_PUSH_KEY = "msgreq:push:{user}:{conv}"


def _split_recipients_for_message_request(recipient_ids, conversation_id):
    if not recipient_ids or not conversation_id:
        return recipient_ids, []
    try:
        from .message_requests import partition_recipients

        return partition_recipients(conversation_id, recipient_ids)
    except Exception:
        logger.exception("message-request split failed for conversation %s; sending unfiltered", conversation_id)
        return recipient_ids, []


def send_message_request_push(recipient_ids, sender_name, conversation_id, message_id):
    """Pending receivers ke liye data-only "Message request" push (message text NAHI bhejta)."""
    fresh = [
        str(uid) for uid in recipient_ids
        if cache.add(
            _REQUEST_PUSH_KEY.format(user=uid, conv=conversation_id), 1,
            timeout=MESSAGE_REQUEST_PUSH_TTL_SECONDS,
        )
    ]
    if not fresh:
        return
    _send_multicast(
        _tokens_for_users(fresh),
        notification=None,
        data={
            'type': 'message_request',
            'title': 'Message request',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': f"{sender_name} wants to send you a message" if sender_name else "You have a new message request",
        },
        android_priority='high',
        channel_id='chat_messages',
    )


# 🔥 UPDATED — Notification batching / digest, now WhatsApp-style
# (immediate send, no artificial wait). Pehle ye ek fixed
# `CHAT_PUSH_DEBOUNCE_SECONDS` (30s) tak har push HOLD karta tha taaki
# ek window ke messages ek saath batch ho sakein — real WhatsApp aisa
# nahi karta, wo har message pe TURANT push bhejta hai; agar recipient
# ne pichla push abhi dekha/padha nahi hai to naya push usi
# conversation ke liye ek hi notification me "X sent N messages" ban
# ke replace ho jaata hai, N messages tak intezaar nahi karta.
#
# Ab: sirf ek rolling "unread streak" counter per (user, conversation)
# hai, jo har naye message pe turant increment hoke turant push bhejta
# hai (count==1 -> normal single-message push, count>1 -> digest push
# jisme sirf latest sender/count hota hai — jaisa asli WhatsApp
# tray me dikhta hai). Counter apne aap `CHAT_PUSH_SESSION_SECONDS`
# baad reset ho jaata hai agar itni der koi naya message na aaye (i.e.
# maan lo user ne dekh liya) — taaki agla naya message dobara "1
# message" jaisa fresh single push de, na ki purana count aage badhaye.
#
# `CHAT_PUSH_DEBOUNCE_SECONDS` env var abhi bhi read hota hai (agar
# kisi ne pehle se .env me set kar rakha hai) taaki deployment break na
# ho, bas ab uska matlab "wait time" nahi, "unread-session TTL" hai.
CHAT_PUSH_SESSION_SECONDS = int(
    os.getenv("CHAT_PUSH_SESSION_SECONDS")
    or os.getenv("CHAT_PUSH_DEBOUNCE_SECONDS", "300")
)
_DIGEST_COUNT_KEY = "chatpush:count:{user}:{conv}"

# 🔥 NAYA — configurable escape hatch. Digest-counting already sends every
# push immediately (no delay), but it still MERGES pushes into "X sent N
# messages" if the recipient hasn't opened the chat. Kuch teams isse bhi
# nahi chahte — har message ka apna alag push chahiye, bilkul plain,
# grouping bhi nahi. `CHAT_PUSH_DIGEST_ENABLED=False` set karne se
# `send_chat_message_push` counter/digest logic poori tarah skip kar
# deta hai aur hamesha `_send_single_chat_push` seedha call karta hai —
# koi cache counter bhi involve nahi hota is mode me.
CHAT_PUSH_DIGEST_ENABLED = os.getenv("CHAT_PUSH_DIGEST_ENABLED", "True") == "True"

# 🔥 FIX (this session) — `settings.py` defines `FCM_SERVICE_ACCOUNT_JSON_PATH`
# but this module was reading a DIFFERENT env var (`FIREBASE_CREDENTIALS_PATH`)
# directly via `os.getenv()`. Anyone deploying by following `settings.py`
# would set the former and pushes would stay silently dead (falls into the
# lazy "not configured" branch below — logged, never crashes, so it could go
# unnoticed for a while). Now accepts either, preferring the env var this
# module always used (back-compat) and falling back to the Django setting so
# both conventions work without needing a settings.py change.
from django.conf import settings as _dj_settings

_FIREBASE_CRED_PATH = (
    os.getenv("FIREBASE_CREDENTIALS_PATH")
    or getattr(_dj_settings, "FCM_SERVICE_ACCOUNT_JSON_PATH", None)
    or os.getenv("FCM_SERVICE_ACCOUNT_JSON_PATH")
)

# 🔥 FIX (production readiness) — same class of bug as `livekit_utils.py`:
# this used to raise at IMPORT time, and `push_utils` gets imported by
# `views.py` at Django startup — so a missing FIREBASE_CREDENTIALS_PATH
# used to crash the entire process, including plain chat/REST endpoints
# that never touch push notifications at all. Init is now lazy: it only
# runs the first time a push actually needs to be sent, and any failure
# there is logged + swallowed by `_send_multicast`'s own try/except
# instead of taking the whole app down.
_firebase_init_error = None


def _ensure_firebase_initialized():
    global _firebase_init_error
    if firebase_admin._apps:
        return
    if _firebase_init_error is not None:
        raise _firebase_init_error
    if not _FIREBASE_CRED_PATH:
        _firebase_init_error = RuntimeError(
            "FIREBASE_CREDENTIALS_PATH (ya settings.FCM_SERVICE_ACCOUNT_JSON_PATH) "
            "set nahi hai. Firebase service-account JSON ka path .env me daalo, "
            "warna push notifications kaam nahi karengi."
        )
        raise _firebase_init_error
    try:
        cred = credentials.Certificate(_FIREBASE_CRED_PATH)
        firebase_admin.initialize_app(cred)
    except Exception as e:
        _firebase_init_error = e
        raise


def _tokens_for_users(recipient_ids):
    return list(
        DeviceToken.objects.filter(user_id__in=recipient_ids).values_list('token', flat=True)
    )


def _build_apns_config(notification, apns_alert, extra_headers=None, thread_id=None):
    # C5 — extra_headers: calls ke liye `apns-expiration` / `apns-collapse-id`.
    base_headers = {'apns-priority': '10'}
    if extra_headers:
        base_headers.update(extra_headers)
    if not apns_alert:
        return messaging.APNSConfig(
            headers=dict(base_headers),
            payload=messaging.APNSPayload(
                aps=messaging.Aps(content_available=True, sound='default' if notification else None)
            ),
        )
    badge = apns_alert.get('badge')  # N7 - app-icon number (None = leave the icon alone)
    if apns_alert.get('badge_only'):
        # N7 - badge sync: no banner, no sound. Priority 5 = "does not alert the user" (APNs rule for such pushes).
        silent_headers = dict(base_headers)
        silent_headers['apns-priority'] = '5'
        return messaging.APNSConfig(
            headers=silent_headers,
            payload=messaging.APNSPayload(aps=messaging.Aps(badge=badge)),
        )
    image = apns_alert.get('image')
    return messaging.APNSConfig(
        headers=dict(base_headers),
        payload=messaging.APNSPayload(
            aps=messaging.Aps(
                alert=messaging.ApsAlert(title=apns_alert.get('title'), body=apns_alert.get('body')),
                sound='default',
                badge=badge,
                content_available=True,
                mutable_content=True,  # lets an iOS service extension attach the image
                category=apns_alert.get('category'),
                thread_id=(str(thread_id)[:64] if thread_id else None),  # N3 - iOS notification-centre grouping
            ),
        ),
        fcm_options=messaging.APNSFCMOptions(image=image) if image else None,
    )


_FCM_MULTICAST_LIMIT = 500   # FCM `send_each_for_multicast` ki hard limit (C11)


def _send_multicast(tokens, *, notification=None, data=None, android_priority='high', channel_id='chat_messages', apns_alert=None,
                    ttl_seconds=None, collapse_key=None, apns_thread_id=None, apns_collapse_id=None,
                    retry_attempt=0, expires_at=None):
    """
    tokens: list[str]
    notification: messaging.Notification | None  -> None rakhne se ye
        DATA-ONLY message ban jaata hai (calls ke liye zaroori — data-only
        messages hi background/killed state me app ko jagate hain aur
        `firebaseBackgroundHandler` (Flutter) trigger karte hain).
    apns_alert: N8-BE — optional {title, body, category, image}. Android
        stays data-only (client builds the local notification with image +
        action buttons), but iOS cannot show anything from a silent
        data-only push, so this adds a visible APNs alert (+ mutable-content
        and the image via fcm_options) to the iOS side ONLY. None (the
        default) keeps the old behaviour byte-for-byte.
    ttl_seconds / collapse_key: C5 — call pushes ke liye. FCM ka default TTL
        4 hafte hai, to phone offline ho to ring push baad me achanak
        "incoming call" dikha deta tha. ttl_seconds Android `ttl` aur iOS
        `apns-expiration` dono set karta hai; collapse_key Android
        `collapse_key` + iOS `apns-collapse-id` (same call ka cancel push
        undelivered ring push ko replace kar deta hai). Dono None => purana
        behaviour.
    N3 (push reliability) - naye kwargs:
        apns_thread_id    iOS notification-centre grouping (`aps.thread-id`).
        apns_collapse_id  iOS `apns-collapse-id` (same event dobara aaye to replace, duplicate banner nahi). collapse_key
                          diya ho to wahi use hota hai.
        retry_attempt / expires_at   SIRF `message.retry_push` task set karta hai (0 = pehli koshish). expires_at = epoch
                          seconds jab tak push ka koi matlab hai (call ring TTL) - uske baad retry nahi.
        Har send ke baad: dead FCM token (Unregistered / SenderIdMismatch ...) DB se delete, transient error (503/500/quota)
        par exponential backoff retry (Celery), same event (notification_id / call_id / message_id) ek token ko 2 baar nahi.
    channel_id: 🔥 NAYA (Feature 11) — Android notification channel.
        Flutter side (`push_notification_service.dart`) ko is naam ka
        alag `AndroidNotificationChannel` banana hoga (`'announcements'`)
        taaki teacher ke messages alag sound/priority/color ke saath
        dikhein, normal chat se visually separate — ye hi is poore
        feature ka "automatic pinned lane" wala push-side hissa hai.
    """
    if not tokens:
        ops.record_skip(data, ops.SKIP_NO_TOKENS)  # N10 - user has no registered device
        return

    # C11 — FCM ek multicast me max 500 tokens leta hai; bade group call (har member ke kai
    # devices) me isse zyada ho sakte hain. Chunk karke wahi function dobara chalao.
    if len(tokens) > _FCM_MULTICAST_LIMIT:
        for i in range(0, len(tokens), _FCM_MULTICAST_LIMIT):
            _send_multicast(
                tokens[i:i + _FCM_MULTICAST_LIMIT],
                notification=notification, data=data, android_priority=android_priority,
                channel_id=channel_id, apns_alert=apns_alert,
                ttl_seconds=ttl_seconds, collapse_key=collapse_key,
                apns_thread_id=apns_thread_id, apns_collapse_id=apns_collapse_id,
                retry_attempt=retry_attempt, expires_at=expires_at,
            )
        return

    try:
        _ensure_firebase_initialized()
    except Exception as e:
        logger.error("Firebase not initialized, push skipped: %s", e)
        # N10 - FCM unusable: every token counts as a failed send (alert-worthy), not as a silent skip.
        ops.record_send(data, attempted=len(tokens), ok=0, failed_by_code={'fcm_down': len(tokens)},
                        retry_attempt=retry_attempt)
        return

    now_ts = time.time()
    if ttl_seconds and expires_at is None:
        expires_at = now_ts + int(ttl_seconds)
    if retry_attempt and expires_at is not None and now_ts >= expires_at:
        return  # retry ke waqt tak push ki apni zindagi khatam (call ring) - bhejna bekaar

    # N3 - dedupe: same event (notification_id / call_id / message_id) ek token ko 2 baar nahi. Retry me dedupe nahi
    # (pehli koshish ki reservation fail hone par release ho chuki hai).
    reserved = {}
    if not retry_attempt:
        before = len(tokens)
        tokens, reserved = push_reliability.reserve_dedupe(list(tokens), push_reliability.event_fingerprint(data))
        if len(tokens) < before:
            ops.record_skip(data, ops.SKIP_DEDUPE, before - len(tokens))  # N10 - same event already sent to those tokens
        if not tokens:
            return
    tokens = list(tokens)

    # data ke saare values FCM me STRING hone chahiye
    str_data = {str(k): str(v) for k, v in (data or {}).items()}

    android_kwargs = {}
    apns_extra_headers = {}
    if ttl_seconds:
        # Retry me ttl_seconds = bachi hui zindagi (expires_at - now), poori TTL dobara nahi milti.
        remaining = int(ttl_seconds) if not retry_attempt else max(1, int(expires_at - now_ts))
        android_kwargs['ttl'] = timedelta(seconds=remaining)
        apns_extra_headers['apns-expiration'] = str(int(now_ts) + remaining)
    if collapse_key:
        # FCM collapse_key max 64 chars (UUID 36 + prefix theek hai)
        android_kwargs['collapse_key'] = str(collapse_key)[:64]
        apns_extra_headers['apns-collapse-id'] = str(collapse_key)[:64]
    elif apns_collapse_id:
        apns_extra_headers['apns-collapse-id'] = str(apns_collapse_id)[:64]  # N3 - sirf iOS; Android collapse_key nahi

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=notification,
        data=str_data,
        android=messaging.AndroidConfig(
            priority=android_priority,  # calls/urgent ke liye 'high'
            notification=(
                messaging.AndroidNotification(channel_id=channel_id)
                if notification is not None else None
            ),
            **android_kwargs,
        ),
        apns=_build_apns_config(notification, apns_alert, apns_extra_headers or None, thread_id=apns_thread_id),
    )

    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as e:
        # Poori call fail (network / FCM down): har token transient maano -> backoff retry, token mat hatao.
        logger.exception("FCM send failed: %s", e)
        _after_send(
            tokens, reserved, ok=[], dead=[], transient=list(tokens), codes={t: push_reliability.error_code(e) for t in tokens},
            retry_attempt=retry_attempt, expires_at=expires_at,
            retry_kwargs=dict(
                notification=notification, data=str_data, android_priority=android_priority, channel_id=channel_id,
                apns_alert=apns_alert, ttl_seconds=ttl_seconds, collapse_key=collapse_key,
                apns_thread_id=apns_thread_id, apns_collapse_id=apns_collapse_id,
            ),
            whole_call=True,  # N10 - the whole FCM call failed (network / FCM down), not one bad token
        )
        return

    # N3 - result classify: ok / dead (delete) / transient (retry) / other (sirf log)
    ok, dead, transient, other, codes = [], [], [], [], {}
    for idx, result in enumerate(response.responses):
        token = tokens[idx]
        if result.success:
            ok.append(token)
            continue
        kind = push_reliability.classify_error(result.exception)
        codes[token] = push_reliability.error_code(result.exception)
        if kind == push_reliability.DEAD:
            dead.append(token)
        elif kind == push_reliability.TRANSIENT:
            transient.append(token)
        else:
            other.append(token)
            logger.warning("FCM send failed (not retried) code=%s err=%s", codes[token], result.exception)
    _after_send(
        tokens, reserved, ok=ok, dead=dead, transient=transient, other=other, codes=codes,
        retry_attempt=retry_attempt, expires_at=expires_at,
        retry_kwargs=dict(
            notification=notification, data=str_data, android_priority=android_priority, channel_id=channel_id,
            apns_alert=apns_alert, ttl_seconds=ttl_seconds, collapse_key=collapse_key,
            apns_thread_id=apns_thread_id, apns_collapse_id=apns_collapse_id,
        ),
    )


def _notification_to_dict(notification):
    if notification is None:
        return None
    return {
        'title': getattr(notification, 'title', None),
        'body': getattr(notification, 'body', None),
        'image': getattr(notification, 'image', None),
    }


def _schedule_retry(tokens, *, attempt, expires_at, retry_kwargs) -> bool:
    """Transient-failed tokens ke liye `message.retry_push` Celery task (exponential backoff). Kabhi raise nahi karta;
    False => retry schedule nahi ho paya (broker down) - caller ise final failure ginta hai."""
    try:
        from .tasks import retry_push

        retry_push.apply_async(
            kwargs=dict(
                tokens=list(tokens), attempt=attempt, expires_at=expires_at,
                notification=_notification_to_dict(retry_kwargs.get('notification')),
                **{k: v for k, v in retry_kwargs.items() if k != 'notification'},
            ),
            countdown=push_reliability.retry_countdown(attempt - 1),
        )
        return True
    except Exception:
        logger.warning("push retry could not be scheduled", exc_info=True)
        return False


def _after_send(tokens, reserved, *, ok, dead, transient, codes, retry_attempt, expires_at, retry_kwargs, other=None,
                whole_call=False):
    """Ek multicast ke baad ka sara hygiene: dedupe reservation release, dead delete, transient retry, failure bookkeeping."""
    other = other or []
    failed_now = list(dead) + list(transient) + list(other)
    if reserved and failed_now:
        push_reliability.release_dedupe(reserved, failed_now)  # retry / agla sender wahi event bhej sake

    final_failed = []
    if transient:
        remaining = None if expires_at is None else (expires_at - time.time())
        if push_reliability.should_retry(retry_attempt, remaining) and _schedule_retry(
            transient, attempt=retry_attempt + 1, expires_at=expires_at, retry_kwargs=retry_kwargs,
        ):
            pass
        else:
            final_failed = list(transient)
    _record_ops(tokens, ok, dead, other, transient, final_failed, codes, retry_attempt, retry_kwargs, whole_call)
    push_reliability.record_send_result(ok, dead, final_failed, codes)


def _record_ops(tokens, ok, dead, other, transient, final_failed, codes, retry_attempt, retry_kwargs, whole_call):
    """N10 - one multicast's outcome -> counters (sent / delivered / failed by FCM code / retried). Never raises."""
    try:
        by_code = {}
        dead_set = set(dead)
        for tok in list(dead) + list(other or []) + list(final_failed):
            if whole_call:
                code = 'fcm_down'
            else:
                code = ops.normalize_fail_code(codes.get(tok, ''))
                if tok in dead_set and code not in ops.DEAD_CODES:
                    code = ops.FAIL_UNREGISTERED  # e.g. INVALID_ARGUMENT "registration token" = a dead token too
            by_code[code] = by_code.get(code, 0) + 1
        ops.record_send(
            (retry_kwargs or {}).get('data'), attempted=len(tokens), ok=len(ok), failed_by_code=by_code,
            retried=max(0, len(transient) - len(final_failed)), retry_attempt=retry_attempt,
        )
    except Exception:  # pragma: no cover
        logger.debug('push ops recording failed', exc_info=True)


def send_push_to_users(recipient_ids, title, body, data=None):
    """Generic push — normal notification (title + body) ke saath."""
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=messaging.Notification(title=title, body=body),
        data=data,
        android_priority='high',
    )


# N8-BE — rich push (image + action buttons). Data-only on Android on
# purpose: FCM *notification* messages cannot carry action buttons, and a
# notification block next to a client-built local notification is exactly
# the double-notification bug the chat pushes above already avoid. So the
# client builds the notification from these data keys:
#   title, body, image_url, actions (JSON string: [{"id","label"}, ...]),
#   channel_id, plus the deep-link ids (post_id, comment_id, follow_id...).
RICH_PUSH_CHANNEL_ID = os.getenv("RICH_PUSH_CHANNEL_ID", "notifications")
# Per-action APNs category the iOS app registers; actions themselves are
# declared client-side per category (iOS has no server-sent buttons).
# N7 - the table now lives in core/notification_rich.py (APNS_CATEGORIES = category -> its buttons, mirrored in Flutter
# `kPushIosCategories`, parity tested). Reply = text-input action; REMINDER = Snooze / Turn off (N6); JOIN_NOW / MARK_READ new.
_APNS_CATEGORY_BY_ACTION_ID = rich.CATEGORY_BY_ACTION_ID

# Same JSON shape send_rich_push uses for `actions`.
_REPLY_MESSAGE_ACTIONS = json.dumps([{"id": "reply_message", "label": "Reply", "input": True}])


def _chat_sender_avatar(message_id):
    """N7 - sender's profile photo (absolute URL) for a client-built CHAT notification: the phone shows it as the round
    Android large icon, like every other rich push. Two indexed lookups per MESSAGE (not per recipient). None when the
    message / photo is missing or anything fails - a chat push must never be held back for a nice-to-have picture."""
    try:
        sender_id = Message.objects.filter(pk=message_id).values_list('sender_id', flat=True).first()
        if sender_id is None:
            return None
        return rich.avatar_url({}, actor_id=sender_id)
    except Exception:
        logger.debug("chat sender avatar lookup failed (message=%s)", message_id, exc_info=True)
        return None


def _with_chat_avatar(data, message_id):
    """`data` + `avatar_url` when the sender has a photo (same data key the rich pushes use)."""
    avatar = _chat_sender_avatar(message_id)
    if avatar:
        data['avatar_url'] = avatar
    return data


def send_rich_push(recipient_ids, title, body, *, image_url=None, actions=None, data=None,
                   channel_id=None, apns_thread_id=None, apns_collapse_id=None, avatar_url=None, badge=None):
    """Generic rich push: title/body + optional image + optional action
    buttons. Returns nothing; never raises (same as every sender here).

    N7: `avatar_url` = small round picture of the actor (Android large icon; with `image_url` the big picture stays),
    `badge` = number for the iOS app icon (None = leave it alone), and every push carries `group_key` (one Android bundle /
    iOS thread per channel) so the client can post the grouped summary notification."""
    tokens = _tokens_for_users(recipient_ids)
    if not tokens:
        ops.record_skip(data, ops.SKIP_NO_TOKENS)  # N10
        return
    actions = list(actions or [])
    payload = dict(data or {})
    payload.update({
        'title': title or '',
        'body': body or '',
        'channel_id': channel_id or RICH_PUSH_CHANNEL_ID,
    })
    if image_url:
        payload['image_url'] = image_url
    if avatar_url:
        payload['avatar_url'] = avatar_url
    payload.setdefault('group_key', rich.group_key(payload['channel_id']))
    if badge is not None:
        payload['badge_count'] = rich.clamp_badge(badge)
    if actions:
        payload['actions'] = json.dumps(actions)
    category = rich.category_for_actions(actions)
    _send_multicast(
        tokens,
        notification=None,
        data=payload,
        android_priority='high',
        channel_id=payload['channel_id'],
        apns_alert={'title': title, 'body': body, 'category': category, 'image': image_url,
                    'badge': (rich.clamp_badge(badge) if badge is not None else None)},
        apns_thread_id=apns_thread_id,
        apns_collapse_id=apns_collapse_id,
    )


def send_badge_sync_push(user_id):
    """N7 - iOS only: after the user read something (mark-read / mark-all-read / a push button / another device) the app
    icon must show the NEW unread number even if the app is closed. One badge-only APNs push (no banner, no sound) to the
    user's iOS tokens; Android launchers count the tray themselves, so Android tokens are skipped. Data key `type` =
    `badge_sync` lets an iOS app that is awake update its own controller too. Never raises, never sends when the count is
    unknown (a wrong 0 would wipe a right number)."""
    try:
        count = rich.badge_count_for(user_id)
        if count is None:
            return
        tokens = list(
            DeviceToken.objects.filter(user_id=user_id, platform='ios').values_list('token', flat=True)
        )
        if not tokens:
            return
        _send_multicast(
            tokens,
            notification=None,
            data={'type': 'badge_sync', 'badge_count': count},
            android_priority='normal',
            channel_id='silent',
            apns_alert={'badge_only': True, 'badge': count},
        )
    except Exception:
        logger.exception("badge sync push failed (user=%s)", user_id)


def send_push_for_notification(notification, extra_data=None, reminder_gate=True):
    """Push for an already-saved core.Notification row (the row that
    create_notification() returned). This is how core/in-app types get a
    rich push: core.services itself never sends (module contract), so the
    call-site does

        row = create_notification(user, TYPE, title, message, actor=..., data={...})
        send_push_for_notification(row)

    `None` (create_notification skipped it: restricted/muted actor, or a
    swallowed error) is a no-op. Honours NotificationPreference: push
    disabled, or the type in `muted_types`, means no push (the bell row
    still exists, same as allowed_channels_for's own contract)."""
    if notification is None:
        return
    try:
        pref = NotificationPreference.objects.filter(user_id=notification.recipient_id).first()
        if pref is not None and 'push' not in pref.allowed_channels_for(notification.notif_type):
            # N10 - count WHY (push off / type muted / matrix chip / focus / DND / quiet hours ...).
            ops.record_skip(notification.notif_type,
                            ops.push_skip_reason(pref, notification.notif_type) or ops.SKIP_NOT_ALLOWED)
            return
        # N9 - the user muted THIS class / chat / post (for a while or for good): bell row stays, the buzz does not
        # happen. Checked before the N6 cap below so a muted push never uses up a daily slot. Safety / calls ignore it.
        if center.push_muted_for_row(notification):
            ops.record_skip(notification.notif_type, ops.SKIP_SCOPE_MUTED)  # N10
            return
        # N6 - daily cap + same-type collapse for non-urgent reminder / nudge types (bell row already exists, only the
        # buzz is dropped). `reminder_gate=False` is for a push the user asked for himself (snooze delivery).
        if reminder_gate:
            slot = reminders.claim_slot(notification.recipient_id, notification.notif_type, pref)
            if slot:
                # N10 - "cap" (daily limit) vs "collapsed" (same type pushed a moment ago)
                ops.record_skip(notification.notif_type,
                                ops.SKIP_REMINDER_CAP if slot == reminders.REASON_CAP else ops.SKIP_REMINDER_COLLAPSED)
                return
        p = build_push_payload(notification)
        data = dict(p['data'])
        # X3 - notification-copy A/B test: the user's variant of a running experiment for this type (control included).
        # Only the PUSH text changes (the bell row keeps its original text); `exp` rides along for client-side logs.
        # Measurement (pushed / opened per variant) is consent-gated inside product_analytics. Never raises.
        try:
            from core import experiments as _exp, product_analytics as _pa

            _user = getattr(notification, 'recipient', None)
            _title, _body, _tag = _exp.apply_copy(_user, notification.notif_type, p['title'], p['body'])
            if _tag:
                p['title'], p['body'] = _title, _body
                data['exp'] = _tag
                _pa.record_variant_pushed(_user, notification.id, _tag)
        except Exception:
            logger.exception("notification copy experiment failed (notification %s)", getattr(notification, 'id', None))
        if extra_data:
            data.update(extra_data)  # e.g. {'type': 'doubt_answered'} — client routing key
        # N3 - Android channel category se (calls/classes/tests/social/wallet/silent...); iOS: same category ek thread me
        # group, aur ek notification row = ek collapse-id (redelivery par banner replace hota hai, duplicate nahi).
        channel = channel_for_notif_type(notification.notif_type)
        # N9 - the user's sound / vibration choice for this category rides in the push (`snd`, `vib`), so the phone
        # applies it even in a background isolate and on a second device. Empty (no keys) when nothing was changed.
        data.update(center.push_sound_data(pref, channel))
        send_rich_push(
            [p['recipient_id']], p['title'], p['body'],
            image_url=p['image_url'], actions=p['actions'], data=data,
            channel_id=channel, apns_thread_id=channel,
            # N7 - actor photo + the unread number for the iOS app icon (the new bell row is already counted).
            avatar_url=p.get('avatar_url'), badge=rich.badge_count_for(p['recipient_id']),
            # N6 - a reminder replaces the previous reminder of the SAME type in the tray (collapse by type); every
            # other row keeps its own id, so redelivery of the same row replaces itself.
            apns_collapse_id=(
                f'rem-{notification.notif_type}'[:64] if reminders.is_reminder(notification.notif_type)
                else f'n{notification.id}'
            ),
        )
    except Exception:
        logger.exception("rich push failed for notification %s", getattr(notification, 'id', None))
        ops.record_skip(getattr(notification, 'notif_type', None), ops.SKIP_ERROR)  # N10


def send_doubt_answered_push(notification):
    """T1.2 — push for a `DOUBT_ANSWERED` bell row (someone answered the
    user's Doubt Queue question). Same preference/quiet-hours/mute gating as
    every other rich push (`send_push_for_notification`), plus a chat-style
    `type: 'doubt_answered'` data key so the Flutter push handler can route a
    tap straight to that group's Doubts screen (`group_id`/`conversation_id`/
    `doubt_id`/`answer_id` already ride in the whitelisted data keys — see
    core.services.PUSH_DATA_KEYS). `None` row (restricted/muted/blocked actor)
    is a no-op. Never raises."""
    send_push_for_notification(notification, extra_data={'type': 'doubt_answered'})


def _send_single_chat_push(recipient_ids, sender_name, body, conversation_id, message_id, is_announcement=False):
    """
    Actual single-message FCM call — DATA-ONLY (see class-level note on
    the double-notification bug this avoids). Called by
    `send_chat_message_push` for the first message of a new unread
    session (count == 1) — subsequent messages in the same unread
    session go through `send_chat_digest_push` instead.

    🔥 NAYA — `is_announcement` (Feature 11): teacher/staff ka message ho
    to `type` aur `channel_id` dono alag jaate hain, taaki client ek
    visually/audibly alag notification bana sake (§ push_notification_
    service.dart me naya channel banana hoga — dekho `_send_multicast`
    docstring).
    """
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,  # data-only — client hi apna local notification banayega
        data=_with_chat_avatar({  # N7 - + sender photo (`avatar_url`) for the round large icon
            'type': 'announcement' if is_announcement else 'chat_message',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': body or '',
            # Inline Reply button (Android: client builds it from this JSON).
            'actions': _REPLY_MESSAGE_ACTIONS,
        }, message_id),
        android_priority='high',
        channel_id='announcements' if is_announcement else 'chat_messages',
    )


def send_chat_digest_push(recipient_id, conversation_id, sender_name, count, is_announcement=False):
    """
    Batched summary push — bhejta hai TURANT (koi debounce/wait nahi) jab
    `send_chat_message_push` dekhta hai ki is (user, conversation) ke
    current "unread session" me ye 2nd ya usse aage ka message hai
    ("Riya sent 5 messages"). Pehla message us session ka
    `_send_single_chat_push` se normal single-message push ban chuka
    hota hai; ye function sirf usi session ke baad-wale messages ke liye
    call hota hai — koi scheduled/delayed Celery task involved nahi hai
    (purana `flush_chat_push_digest` task hata diya gaya hai, see
    `tasks.py`). Data-only, jaisa baaki chat pushes — client apna khud ka
    local notification banata hai `type: 'chat_digest'` dekh kar aur us
    par tap karke seedha conversation khol sakta hai (`message_id` nahi
    diya kyunki digest kisi ek specific message ka nahi hai).
    """
    tokens = _tokens_for_users([recipient_id])
    _send_multicast(
        tokens,
        notification=None,
        data={
            'type': 'announcement_digest' if is_announcement else 'chat_digest',
            'conversation_id': str(conversation_id),
            'sender_name': sender_name or '',
            'count': count,
        },
        android_priority='high',
        channel_id='announcements' if is_announcement else 'chat_messages',
    )


def send_chat_message_push(recipient_ids, sender_name, message_text, message_type, conversation_id, message_id, is_announcement=False):
    """
    Naya chat message aane par push.

    🔥 UPDATED (WhatsApp-style — no artificial delay) — pehle ye function
    har message ko `CHAT_PUSH_DEBOUNCE_SECONDS` (30s) tak hold karta tha
    aur ek Celery task se baad me flush karta tha. Ab har message ka push
    TURANT jaata hai — koi wait nahi. Agar recipient ne pichla push abhi
    dekha/padha nahi hai (yahan "session" cache counter se approximate
    kiya hai), to naya push single-message ki jagah digest ("X sent N
    messages") ban jaata hai — bilkul jaisa WhatsApp notification tray me
    ek hi conversation ke multiple unread messages ek notification me
    update ho jaate hain, N messages tak ruk kar ek saath nahi bhejta.

    🔥 NAYA — `is_announcement` (naya param, DEFAULT False — purane sab
    call-sites bina change kiye chalte rahenge):
      • Feature 11: True pass karo jab sender group admin/moderator
        ("teacher/staff") ho — caller (`views.py`/`consumers.py`) ko ye
        already pata hota hai (`group_rules`/`cache_utils.
        get_group_role_cached` se, jo message-send-permission check ke
        waqt hi call hota hai — dobara query nahi karni).
      • Feature 12: isi flag se `_filter_recipients_for_focus()` decide
        karta hai ki jinka Focus Mode "teachers_only" active hai unhe
        bhi ye push milna chahiye ya nahi.

    `cache.add` phir `incr` — race-safe counting, jaisa pehle tha (bas ab
    increment hote hi turant push bhi bhej dete hain, kisi delayed task
    ka intezaar nahi).
    """
    body = message_text if message_type == 'text' else f"Sent a {message_type}"

    # 🔥 NAYA (Feature 12) — Focus Mode wale recipients ko yahin, sabse
    # pehle, hata do. Iske baad ka poora digest-counting logic un logon
    # ke liye bilkul chalta hi nahi — na push jaata hai, na unka unread-
    # session counter badhta hai (taaki focus khatam hone ke baad unhe
    # ek fresh "1 message" push mile, beech ke saare messages ka count
    # nahi — jo sahi hai, kyunki unhe koi individual push mila hi nahi).
    recipient_ids = _filter_recipients_for_focus(recipient_ids, is_announcement=is_announcement)
    recipient_ids = _filter_recipients_for_restrict(recipient_ids, message_id)

    # 🔥 NAYA (M1-BE) — pending requests alag push (title "Message request"),
    # declined silent; sirf accepted recipients aage normal flow me jaate hain
    # (bell row + digest-counting bhi unhi ke liye).
    recipient_ids, request_recipient_ids = _split_recipients_for_message_request(
        recipient_ids, conversation_id,
    )
    if request_recipient_ids:
        send_message_request_push(request_recipient_ids, sender_name, conversation_id, message_id)
    if not recipient_ids:
        return

    # 🔥 NAYA (task 44) — bell-row per recipient, ek baar per incoming
    # message, CHAHE push digest/instant kisi bhi branch se jaaye. Bell
    # row jaanboojh kar push-digest counting se INDEPENDENT hai — bell
    # icon me har message apni alag row honi chahiye ("5 unread" list me
    # 5 alag entries), sirf push tray hi "X sent N messages" me merge
    # hota hai (WhatsApp jaisa). Isi liye ye loop yahan hai, digest/
    # instant branch ke andar nahi (warna digest wale messages ka bell
    # row hi nahi banta).
    for uid in recipient_ids:
        create_notification(
            recipient=uid,
            notif_type=Notification.NotifType.CHAT_MESSAGE,
            title=sender_name or "New message",
            message=body,
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "is_announcement": is_announcement,
            },
        )

    # N9 - people who muted THIS chat for a while ("Is chat ko 8h mute"): they keep the bell row made above, but get no
    # push (and no digest counting). One query for the whole audience.
    recipient_ids = center.filter_unmuted_users(
        recipient_ids, center.SCOPE_CONVERSATION, conversation_id, Notification.NotifType.CHAT_MESSAGE,
    )
    if not recipient_ids:
        return

    if not CHAT_PUSH_DIGEST_ENABLED:
        # Pure instant mode — har message ka apna alag push, koi
        # counting/merging nahi. WhatsApp jaisa grouping nahi chahiye to
        # `CHAT_PUSH_DIGEST_ENABLED=False` isi path pe le aata hai.
        _send_single_chat_push(
            [str(uid) for uid in recipient_ids], sender_name, body, conversation_id, message_id,
            is_announcement=is_announcement,
        )
        return

    for uid in recipient_ids:
        uid = str(uid)
        count_key = _DIGEST_COUNT_KEY.format(user=uid, conv=conversation_id)

        # `add` phir `incr` — agar key already thi to `add` kuch nahi
        # badalta, `incr` se count 1 badh jaata hai. Agar key nahi thi
        # (naya "unread session" shuru) to `add` isse 0 pe set karta hai,
        # phir `incr` se wo 1 ban jaata hai.
        cache.add(count_key, 0, timeout=CHAT_PUSH_SESSION_SECONDS)
        try:
            new_count = cache.incr(count_key)
        except ValueError:
            # extreme race: key `add` ke turant baad expire ho gayi.
            cache.set(count_key, 1, timeout=CHAT_PUSH_SESSION_SECONDS)
            new_count = 1

        # Har naye message pe "unread session" TTL ko refresh karo, taaki
        # jab tak messages aate rahein session zinda rahe (jaisa hi ek
        # gap aa jaata hai, key khud expire ho jaati hai aur agla message
        # dobara "1 message" jaisa fresh single push deta hai).
        try:
            cache.touch(count_key, CHAT_PUSH_SESSION_SECONDS)
        except AttributeError:
            # kuch cache backends `touch` support nahi karte — non-fatal,
            # bas TTL refresh nahi hoga is ek call ke liye.
            pass

        if new_count <= 1:
            _send_single_chat_push([uid], sender_name, body, conversation_id, message_id, is_announcement=is_announcement)
        else:
            send_chat_digest_push(uid, conversation_id, sender_name, new_count, is_announcement=is_announcement)


def _call_push_ttl_seconds():
    """C5 — ring push kitni der tak valid rahe (settings.CALL_PUSH_TTL_SECONDS,
    default 30s — client ke 30s ring timeout ke barabar)."""
    try:
        return max(5, int(getattr(_dj_settings, 'CALL_PUSH_TTL_SECONDS', 30)))
    except (TypeError, ValueError):
        return 30


def _call_collapse_key(call_id):
    # incoming_call aur call_cancelled DONO ek hi key use karte hain: agar phone
    # offline tha aur caller ne cut kar diya, to cancel push undelivered ring
    # push ko replace kar deti hai — phone online aane par ring bajti hi nahi.
    return f'call_{call_id}'


def send_incoming_call_push(recipient_ids, caller_name, call_type, call_id, conversation_id, channel_name,
                            caller_avatar=None, call_waiting=False):
    """
    C5 — delivery rules:
      * iOS device with a registered PushKit token -> APNs VoIP push
        (message/voip_push.py). Wahan FCM incoming_call NAHI jaati (duplicate
        ring se bachne ke liye).
      * baaki sab (Android, iOS without VoIP token, VoIP failure) -> FCM
        data-only push, `ttl` = CALL_PUSH_TTL_SECONDS, collapse key = call id.

    Incoming call push — ⚠️ JAAN-BOOJH KAR `notification` block NAHI bheja
    (pure DATA message). Wajah: agar `notification` block bheja jaaye to
    Android system tray khud ek plain notification bana deta hai aur app ka
    background handler kabhi call hi nahi hota (data-only messages hi
    firebaseBackgroundHandler() ko trigger karte hain, jisse hum
    CallKitService.showIncomingCall() call karke native full-screen call UI
    dikhate hain). android_priority='high' zaroori hai warna Android call ko
    turant deliver nahi karega (khaaskar Doze/background restriction me).
    """
    ttl = _call_push_ttl_seconds()
    rows = list(
        DeviceToken.objects.filter(user_id__in=recipient_ids)
        .values_list('token', 'platform', 'voip_token')
    )

    # 1) iOS VoIP (PushKit) — killed state me reliable
    voip_devices = [(tok, voip) for tok, plat, voip in rows if plat == 'ios' and voip]
    voip_delivered = set()
    if voip_devices:
        try:
            from .voip_push import send_voip_call_push
            voip_delivered = send_voip_call_push(
                voip_devices,
                call_id=call_id,
                caller_name=caller_name,
                call_type=call_type,
                conversation_id=conversation_id,
                channel_name=channel_name,
                caller_avatar=caller_avatar,
                ttl_seconds=ttl,
                call_waiting=call_waiting,
            )
        except Exception:
            logger.exception("VoIP call push failed — FCM fallback")
            voip_delivered = set()

    # 2) FCM — baaki devices + jahan VoIP fail hui
    fcm_tokens = [tok for tok, _plat, _voip in rows if tok not in voip_delivered]
    fcm_data = {
        'type': 'incoming_call',
        'call_id': str(call_id),
        'caller_name': caller_name,
        'call_type': call_type,
        'conversation_id': str(conversation_id),
        'channel_name': channel_name,
    }
    if caller_avatar:
        fcm_data['caller_avatar'] = caller_avatar
    if call_waiting:
        # C7 — receiver pehle se dusri call me hai; client call-waiting banner dikhaye.
        fcm_data['call_waiting'] = 'true'
    _send_multicast(
        fcm_tokens,
        notification=None,  # data-only — CallKit UI khud banayega
        data=fcm_data,
        android_priority='high',
        channel_id=CHANNEL_CALLS,  # N3 - calls channel (importance max)
        ttl_seconds=ttl,
        collapse_key=_call_collapse_key(call_id),
    )

    # 🔥 NAYA (task 44) — missed-call ke liye bhi bell row chahiye (agar
    # recipient turant answer na kare, unhe baad me "X called you" list
    # me dikhna chahiye, sirf ek CallKit popup jo miss ho gaya wo nahi).
    # ⚠️ Jaan-boojh kar Focus Mode filter NAHI lagaya yahan (push khud
    # kisi filter se nahi guzarta upar) — bell row bhi wahi recipients
    # paate hain jinhe push mila.
    for uid in recipient_ids:
        create_notification(
            recipient=uid,
            notif_type=Notification.NotifType.INCOMING_CALL,
            title=caller_name or "Incoming call",
            message=f"{call_type} call",
            data={
                "call_id": str(call_id),
                "conversation_id": str(conversation_id),
                "channel_name": channel_name,
            },
        )


def send_mention_push(recipient_ids, sender_name, message_text, conversation_id, message_id, is_announcement=False):
    """
    🔥 NAYA — @mention push. Normal `send_chat_message_push` sabhi
    (non-muted) participants ko generic "naya message" push deta hai;
    isse ALAG rakha hai kyunki jinhe @mention kiya gaya hai unhe hamesha
    (chat mute ho tab bhi — WhatsApp isi tarah karta hai, mention normal
    mute se override karta hai) ek specific "X ne aapko mention kiya"
    notification milna chahiye, generic "naya message" nahi.

    🔥 NAYA (Feature 12) — mute ko override karta hai, LEKIN Focus Mode
    ko nahi — agar Focus Mode "teachers_only" hai to student ka mention
    bhi (jo teacher nahi hai) block hoga; teacher ka mention (`is_
    announcement=True`) hamesha through jaayega. Product-decision hai,
    agar chaho to `is_announcement` regardless True bhej ke mentions ko
    hamesha bypass karwaya ja sakta hai — filhaal safe default rakha hai.

    Data-only rakha hai (jaisa baaki chat pushes) taaki duplicate
    notification na bane — client apna khud ka local notification banata
    hai `type: 'mention'` dekh kar.
    """
    recipient_ids = _filter_recipients_for_focus(recipient_ids, is_announcement=is_announcement)
    recipient_ids = _filter_recipients_for_restrict(recipient_ids, message_id)
    # 🔥 NAYA (M1-BE) — pending/declined request me mention push bhi nahi
    # (mute override sirf accepted chats pe lagta hai).
    recipient_ids, _ = _split_recipients_for_message_request(recipient_ids, conversation_id)
    if not recipient_ids:
        return

    body = (message_text or '')[:200]

    # 🔥 NAYA (task 44) — mention ka apna alag bell row, generic
    # CHAT_MESSAGE row se ALAG (jaisa push already alag hai) — recipient
    # ko apni notification list me bhi "X mentioned you" dikhna chahiye,
    # generic "new message" nahi.
    for uid in recipient_ids:
        create_notification(
            recipient=uid,
            notif_type=Notification.NotifType.MENTION,
            title=f"{sender_name} mentioned you" if sender_name else "You were mentioned",
            message=body,
            data={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
            },
        )

    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,
        data=_with_chat_avatar({  # N7 - + sender photo
            'type': 'mention',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': body,
            'actions': _REPLY_MESSAGE_ACTIONS,
        }, message_id),
        android_priority='high',
        channel_id='announcements' if is_announcement else 'chat_messages',
    )


def send_call_cancelled_push(recipient_ids, call_id, conversation_id):
    """
    🔥 NAYA — Caller answer se PEHLE hi call cancel/end kar de to jo log
    abhi tak answer nahi kar paaye unhe ye push milta hai. Data-only rakha
    hai jaisa `send_incoming_call_push` — background/killed state me bhi
    `firebaseBackgroundHandler` trigger karke `CallKitService.
    endCallUiByCallId(call_id)` call kare taaki native incoming-call popup
    turant dismiss ho jaaye (warna ringtone/popup bajta reh jaata jab tak
    khud-ba-khud timeout na ho).
    """
    # C5 — VoIP push se cancel NAHI bhejte (Apple: har VoIP push par call report
    # karni padti hai). iOS par cancel normal FCM silent push se jaata hai; agar
    # wo bhi na pahunche to CallKit ring timeout (30s) call khatam kar deta hai.
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,
        data={
            'type': 'call_cancelled',
            'call_id': str(call_id),
            'conversation_id': str(conversation_id) if conversation_id else '',
        },
        android_priority='high',
        channel_id=CHANNEL_CALLS,  # N3 - calls channel (importance max)
        ttl_seconds=_call_push_ttl_seconds(),
        collapse_key=_call_collapse_key(call_id),
    )


def send_missed_call_push(recipient_ids, caller_name, call_type, call_id, conversation_id, *,
                          is_group_call=False, call_status='missed', caller_avatar=None, body='',
                          missed_at=None, ttl_seconds=None):
    """
    C12 — "Missed call" push. `message/missed_calls.py` ye tab bhejta hai jab kisi ki ring miss hui
    (ring timeout / caller ne cancel kiya / user busy tha / group call me bina uthaye khatam).

    * DATA-ONLY (Android): client apna local notification banata hai (`type: 'missed_call'`), jaisa
      `incoming_call` / `mention` — aur wahi handler CallKit ring UI bhi hata deta hai (cancel push ke saath
      race me ye push use replace kar sakta hai: collapse key same).
    * iOS: silent push kuch dikhata nahi, isliye visible APNs alert (title/body) alag se.
    * Collapse key = ring/cancel wali (`call_<id>`): phone offline tha to undelivered ring push replace ho jaata
      hai — online aane par ring bajti hi nahi, sirf "Missed call" dikhta hai. TTL lamba (default 24h) taaki
      thodi der baad online aane par bhi missed-call notification mile.
    * Bell row yahan nahi banti (caller `missed_calls.upsert_missed_bell_row` ne pehle hi bana di).
    """
    tokens = _tokens_for_users(recipient_ids)
    if not tokens:
        return
    title = caller_name or 'Missed call'
    text = body or ('Missed video call' if call_type == 'video' else 'Missed voice call')
    data = {
        'type': 'missed_call',
        'call_id': str(call_id),
        'conversation_id': str(conversation_id) if conversation_id else '',
        'caller_name': caller_name or '',
        'call_type': str(call_type),
        'is_group_call': 'true' if is_group_call else 'false',
        'call_status': str(call_status),
    }
    if caller_avatar:
        data['caller_avatar'] = caller_avatar
    if missed_at is not None:
        try:
            data['missed_at'] = missed_at.isoformat()
        except AttributeError:
            data['missed_at'] = str(missed_at)
    _send_multicast(
        tokens,
        notification=None,  # data-only — Android client local notification banata hai
        data=data,
        android_priority='high',
        channel_id=CHANNEL_CALLS,  # N3 - calls channel (importance max)
        apns_alert={'title': title, 'body': text},
        ttl_seconds=ttl_seconds or 24 * 3600,
        collapse_key=_call_collapse_key(call_id),
    )


def send_parent_push(*, fcm_token, title, body, data=None):
    """
    🔧 GAP FIX — `tuitionclass/parent_link_views.py` (ClassroomParentCodeGenerateView,
    ParentQueryReplyView) calls this for Parent Mode notifications. Every
    other push function above resolves FCM tokens FROM a `recipient_ids`
    list via `_tokens_for_users()` (DeviceToken.objects.filter(user_id__in=...)),
    which only works for actual `User` rows — a parent has none (see
    `ParentAccessCode`/`ParentToken` in message/models.py: Parent Mode is
    deliberately loginless). The caller already has the raw FCM token in
    hand (`ParentToken.token`, stored on the device at verify-time), so
    this sends straight to that ONE token instead of doing a user lookup.

    Uses a real `notification` (not data-only like the chat pushes above)
    since the parent-side app has no separate local-notification-building
    logic the way the main chat client does — a plain FCM notification is
    simplest here and sufficient for this surface.
    """
    if not fcm_token:
        return
    _send_multicast(
        [fcm_token],
        notification=messaging.Notification(title=title, body=body),
        data=data,
        android_priority='high',
    )
