import os
import logging

import firebase_admin
from firebase_admin import credentials, messaging

from .models import DeviceToken

logger = logging.getLogger(__name__)

_FIREBASE_CRED_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")

if not firebase_admin._apps:
    if not _FIREBASE_CRED_PATH:
        raise RuntimeError(
            "FIREBASE_CREDENTIALS_PATH set nahi hai. Firebase service-account "
            "JSON ka path .env me daalo, warna push notifications kaam nahi karengi."
        )
    cred = credentials.Certificate(_FIREBASE_CRED_PATH)
    firebase_admin.initialize_app(cred)


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