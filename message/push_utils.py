import os
import json
import logging

import firebase_admin
from firebase_admin import credentials, messaging
from django.core.cache import cache

from .models import DeviceToken

logger = logging.getLogger(__name__)

# 🔥 NAYA — Notification batching / digest window. `send_chat_message_push`
# ab seedha FCM call nahi karta — har naya message ek per-(user,
# conversation) cache counter me accumulate hota hai, aur EK hi debounced
# Celery task (`tasks.flush_chat_push_digest`) us window ke end me actual
# push bhejta hai: agar sirf 1 message aaya to normal single-message push,
# agar zyada aaye ("user 10 min tak app open nahi karta aur 20 messages
# aa jaate hain") to ek batched "X ne N messages bheje" push — WhatsApp
# jaisa hi behaviour, FCM cost aur notification-fatigue dono kam karta hai.
# Mentions is batching se bypass karte hain (see `send_mention_push` —
# wahi immediate/priority path use karta hai, jaisa mute bhi bypass karta
# hai — same priority logic, dono jagah "mention hamesha turant" hi hai).
CHAT_PUSH_DEBOUNCE_SECONDS = int(os.getenv("CHAT_PUSH_DEBOUNCE_SECONDS", "30"))
_DIGEST_COUNT_KEY = "chatpush:count:{user}:{conv}"
_DIGEST_LAST_KEY = "chatpush:last:{user}:{conv}"
_DIGEST_SCHEDULED_KEY = "chatpush:scheduled:{user}:{conv}"

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


def _send_multicast(tokens, *, notification=None, data=None, android_priority='high'):
    """
    tokens: list[str]
    notification: messaging.Notification | None  -> None rakhne se ye
        DATA-ONLY message ban jaata hai (calls ke liye zaroori — data-only
        messages hi background/killed state me app ko jagate hain aur
        `firebaseBackgroundHandler` (Flutter) trigger karte hain).
    """
    if not tokens:
        return

    try:
        _ensure_firebase_initialized()
    except Exception as e:
        logger.error("Firebase not initialized, push skipped: %s", e)
        return

    # data ke saare values FCM me STRING hone chahiye
    str_data = {str(k): str(v) for k, v in (data or {}).items()}

    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=notification,
        data=str_data,
        android=messaging.AndroidConfig(
            priority=android_priority,  # calls/urgent ke liye 'high'
            notification=(
                messaging.AndroidNotification(channel_id='chat_messages')
                if notification is not None else None
            ),
        ),
        apns=messaging.APNSConfig(
            headers={'apns-priority': '10'},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(content_available=True, sound='default' if notification else None)
            ),
        ),
    )

    try:
        response = messaging.send_each_for_multicast(message)
    except Exception as e:
        logger.exception("FCM send failed: %s", e)
        return

    # Invalid/unregistered tokens cleanup karo taaki aage push fail na ho
    if response.failure_count:
        invalid_tokens = []
        for idx, result in enumerate(response.responses):
            if not result.success:
                err = result.exception
                if isinstance(err, (messaging.UnregisteredError,)):
                    invalid_tokens.append(tokens[idx])
        if invalid_tokens:
            DeviceToken.objects.filter(token__in=invalid_tokens).delete()


def send_push_to_users(recipient_ids, title, body, data=None):
    """Generic push — normal notification (title + body) ke saath."""
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=messaging.Notification(title=title, body=body),
        data=data,
        android_priority='high',
    )


def _send_single_chat_push(recipient_ids, sender_name, body, conversation_id, message_id):
    """
    Actual single-message FCM call — DATA-ONLY (see class-level note on
    the double-notification bug this avoids). Called either immediately
    by `flush_chat_push_digest` when a debounce window only accumulated
    one message, or would've been called directly here pre-batching.
    """
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,  # data-only — client hi apna local notification banayega
        data={
            'type': 'chat_message',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': body or '',
        },
        android_priority='high',
    )


def send_chat_digest_push(recipient_id, conversation_id, sender_name, count):
    """
    🔥 NAYA — batched summary push jab debounce window (`flush_chat_push_
    digest`) me 1 se zyada message accumulate ho gaye ("Riya sent 5
    messages"). Data-only, jaisa baaki chat pushes — client apna khud ka
    local notification banata hai `type: 'chat_digest'` dekh kar aur us
    par tap karke seedha conversation khol sakta hai (`message_id` nahi
    diya kyunki digest kisi ek specific message ka nahi hai).
    """
    tokens = _tokens_for_users([recipient_id])
    _send_multicast(
        tokens,
        notification=None,
        data={
            'type': 'chat_digest',
            'conversation_id': str(conversation_id),
            'sender_name': sender_name or '',
            'count': count,
        },
        android_priority='high',
    )


def send_chat_message_push(recipient_ids, sender_name, message_text, message_type, conversation_id, message_id):
    """
    Naya chat message aane par push.

    🔥 UPDATED (notification batching) — pehle ye function seedha FCM call
    karta tha. Ab har recipient ke liye ek per-(user, conversation) cache
    counter me message accumulate karta hai aur (agar is window ke liye
    already scheduled nahi hai) ek debounced Celery task schedule karta
    hai (`CHAT_PUSH_DEBOUNCE_SECONDS`, default 30s) — jo window ke end me
    actual push bhejta hai (single ya batched digest, see
    `tasks.flush_chat_push_digest`). Signature/callers (`views.py`,
    `consumers.py`, `scheduled_messages.py`) ko koi change nahi karna
    pada — sab already isi function ko call kar rahe the.

    `cache.add` isliye use kiya hai schedule-flag ke liye (na ki
    `cache.set`) — race-safe: burst ke 20 messages me se sirf PEHLA
    successfully "add" karega (baaki `False` return honge, wahi flag
    already set hai), isliye is window ke liye sirf EK hi flush task
    schedule hota hai, 20 nahi.
    """
    # local import — avoid `push_utils` <-> `tasks` circular import at
    # module load time (tasks.py already local-imports its own deps for
    # the same reason).
    from .tasks import flush_chat_push_digest

    body = message_text if message_type == 'text' else f"Sent a {message_type}"

    for uid in recipient_ids:
        uid = str(uid)
        count_key = _DIGEST_COUNT_KEY.format(user=uid, conv=conversation_id)
        last_key = _DIGEST_LAST_KEY.format(user=uid, conv=conversation_id)
        scheduled_key = _DIGEST_SCHEDULED_KEY.format(user=uid, conv=conversation_id)

        # `add` phir `incr` — agar key already thi to `add` False return
        # karta hai aur kuch nahi badalta, `incr` se count 1 badh jaata hai.
        # Agar key nahi thi to `add` isse 0 pe set karta hai, phir `incr`
        # se wo 1 ban jaata hai — dono paths se sahi count milta hai.
        cache.add(count_key, 0, timeout=CHAT_PUSH_DEBOUNCE_SECONDS + 15)
        try:
            new_count = cache.incr(count_key)
        except ValueError:
            # extreme race: key `add` ke turant baad expire ho gayi — bahut
            # rare, bas is message ko count=1 maan lo (worst case ek extra
            # single push, data loss nahi).
            cache.set(count_key, 1, timeout=CHAT_PUSH_DEBOUNCE_SECONDS + 15)
            new_count = 1

        # Sirf sabse RECENT message ka sender/text digest me dikhta hai
        # ("Riya sent 5 messages" — WhatsApp bhi last sender ka naam
        # dikhata hai, beech ke sabka nahi).
        cache.set(
            last_key,
            json.dumps({'sender_name': sender_name or '', 'text': (body or '')[:200], 'message_id': str(message_id)}),
            timeout=CHAT_PUSH_DEBOUNCE_SECONDS + 15,
        )

        if cache.add(scheduled_key, '1', timeout=CHAT_PUSH_DEBOUNCE_SECONDS):
            flush_chat_push_digest.apply_async(
                args=[uid, str(conversation_id)],
                countdown=CHAT_PUSH_DEBOUNCE_SECONDS,
            )


def send_incoming_call_push(recipient_ids, caller_name, call_type, call_id, conversation_id, channel_name):
    """
    Incoming call push — ⚠️ JAAN-BOOJH KAR `notification` block NAHI bheja
    (pure DATA message). Wajah: agar `notification` block bheja jaaye to
    Android system tray khud ek plain notification bana deta hai aur app ka
    background handler kabhi call hi nahi hota (data-only messages hi
    firebaseBackgroundHandler() ko trigger karte hain, jisse hum
    CallKitService.showIncomingCall() call karke native full-screen call UI
    dikhate hain). android_priority='high' zaroori hai warna Android call ko
    turant deliver nahi karega (khaaskar Doze/background restriction me).
    """
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,  # data-only — CallKit UI khud banayega
        data={
            'type': 'incoming_call',
            'call_id': str(call_id),
            'caller_name': caller_name,
            'call_type': call_type,
            'conversation_id': str(conversation_id),
            'channel_name': channel_name,
        },
        android_priority='high',
    )


def send_mention_push(recipient_ids, sender_name, message_text, conversation_id, message_id):
    """
    🔥 NAYA — @mention push. Normal `send_chat_message_push` sabhi
    (non-muted) participants ko generic "naya message" push deta hai;
    isse ALAG rakha hai kyunki jinhe @mention kiya gaya hai unhe hamesha
    (chat mute ho tab bhi — WhatsApp isi tarah karta hai, mention normal
    mute se override karta hai) ek specific "X ne aapko mention kiya"
    notification milna chahiye, generic "naya message" nahi.

    Data-only rakha hai (jaisa baaki chat pushes) taaki duplicate
    notification na bane — client apna khud ka local notification banata
    hai `type: 'mention'` dekh kar.
    """
    body = (message_text or '')[:200]
    tokens = _tokens_for_users(recipient_ids)
    _send_multicast(
        tokens,
        notification=None,
        data={
            'type': 'mention',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': body,
        },
        android_priority='high',
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
    )