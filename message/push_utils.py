import os
import logging

import firebase_admin
from firebase_admin import credentials, messaging

from .models import DeviceToken

logger = logging.getLogger(__name__)

_FIREBASE_CRED_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")

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
            "FIREBASE_CREDENTIALS_PATH set nahi hai. Firebase service-account "
            "JSON ka path .env me daalo, warna push notifications kaam nahi karengi."
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


def send_chat_message_push(recipient_ids, sender_name, message_text, message_type, conversation_id, message_id):
    """
    Naya chat message aane par push.

    ⚠️ FIX (duplicate notification bug): pehle yahan `notification=` block
    bhi bheja ja raha tha. Jab FCM payload me top-level `notification` key
    hoti hai, Android OS khud background/killed state me ek system-tray
    notification bana deta hai — aur uske upar humara apna
    `firebaseBackgroundHandler` -> `_showBackgroundChatNotification()` bhi
    (Reply action ke saath) ek dusra local notification dikhata hai.
    Result: user ko har message ka 2 baar notification milta tha.

    Ab hum WhatsApp jaisa hi DATA-ONLY message bhejte hain (jaisa
    `send_incoming_call_push` pehle se karta hai) — Android tray khud kuch
    nahi banata, sirf humara apna handler ek hi notification (Reply action
    ke saath) dikhata hai.
    """
    body = message_text if message_type == 'text' else f"Sent a {message_type}"
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