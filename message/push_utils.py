import os
import logging

import firebase_admin
from firebase_admin import credentials, messaging
from django.core.cache import cache
from django.utils import timezone

from .models import DeviceToken, FocusSession  # 🔥 NAYA — FocusSession, Feature 12 ke baad models.py me merge hone se yahan aayega

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


def _send_multicast(tokens, *, notification=None, data=None, android_priority='high', channel_id='chat_messages'):
    """
    tokens: list[str]
    notification: messaging.Notification | None  -> None rakhne se ye
        DATA-ONLY message ban jaata hai (calls ke liye zaroori — data-only
        messages hi background/killed state me app ko jagate hain aur
        `firebaseBackgroundHandler` (Flutter) trigger karte hain).
    channel_id: 🔥 NAYA (Feature 11) — Android notification channel.
        Flutter side (`push_notification_service.dart`) ko is naam ka
        alag `AndroidNotificationChannel` banana hoga (`'announcements'`)
        taaki teacher ke messages alag sound/priority/color ke saath
        dikhein, normal chat se visually separate — ye hi is poore
        feature ka "automatic pinned lane" wala push-side hissa hai.
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
                messaging.AndroidNotification(channel_id=channel_id)
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
        data={
            'type': 'announcement' if is_announcement else 'chat_message',
            'conversation_id': str(conversation_id),
            'message_id': str(message_id),
            'sender_name': sender_name or '',
            'text': body or '',
        },
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
    if not recipient_ids:
        return

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