# message/tasks.py
#
# 🔥 NAYA — Celery tasks. Doc/§9.3 ne flag kiya tha ki `scheduled_messages.py`
# ka delivery half (`finalize_scheduled_message`) sirf ek imaginary
# `send_scheduled_messages` management command se call hone wala tha — par
# na wo command kabhi exist karta tha, na `message` app ka koi entry hi
# `settings.CELERY_BEAT_SCHEDULE` me tha (jabki `liveclass` app ke 6+ tasks
# already wahan registered hain — same Celery/beat infra already running
# hai, `message` app ne bas use hi nahi kiya tha).
#
# Is wajah se do poore features silently broken the:
#   1. "Send later" — `ConversationViewSet.schedule_message` sirf row banata
#      hai, `is_scheduled=True` ke saath — koi bhi cheez use kabhi
#      "actually send" nahi karti thi. User schedule karta, message us
#      time pe kabhi kisi ko deliver hi nahi hota.
#   2. Disappearing messages — `expires_at` cross hone ke baad message sirf
#      *list API se hide* hota tha (views.py ka defensive filter), DB row
#      hamesha ke liye reh jaati — "disappearing" sirf UI-level tha, storage
#      se kabhi nahi hatta tha.
#
# Dono tasks Celery `shared_task` hain (`liveclass/tasks.py` jaisa hi
# pattern) — `settings.CELERY_BEAT_SCHEDULE` me register karo (neeche
# instructions), poora sweep worker process me chalega, request-response
# cycle se bilkul alag.

import json
import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)


def _broadcast_meta_update(message, **extra_fields):
    """
    🔥 SHARED HELPER (used by the two new advanced-feature tasks below) —
    ek message ka `meta` background me (Celery task ke andar) update hone
    ke baad, khuli hui chat screens ko turant pata chalna chahiye — warna
    link-preview card ya transcript sirf refresh/reopen karne par dikhega,
    jo "advanced"/real-time feel ko defeat kar deta hai.

    Naya WS event type `meta_update` use karta hai (consumers.py me
    handler add kiya gaya hai, `edit_event`/`disappearing_messages_updated`
    jaisa hi plain-passthrough pattern) — sirf `meta` field diff bhejta hai,
    poora message dobara nahi.
    """
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'chat_{message.conversation_id}',
        {
            'type': 'meta_update',
            'message_id': str(message.id),
            'meta': message.meta,
            **extra_fields,
        },
    )


@shared_task(name="message.send_scheduled_messages")
def send_scheduled_messages():
    """
    Har due `scheduled_for <= now` scheduled message ko actually deliver
    karta hai (`finalize_scheduled_message` — WS broadcast + push +
    denorm-field updates, normal send jaisa hi).

    Suggested schedule: har 1 minute (`scheduled_for` minute-precision hai,
    query khud sasti hai — `is_scheduled` + `scheduled_for` par composite
    index already model pe hai).

    `select_for_update(skip_locked=True)`: agar kisi wajah se ek run abhi
    khatam nahi hua aur agla beat-tick already shuru ho jaaye (slow DB,
    worker restart, waghera), to dono ek hi message ko double-send nahi
    karenge — jo bhi row pehle se locked hai use dusra worker skip kar
    dega, agli tick pe pick ho jaayegi agar pehla abhi tak fail ho gaya ho.
    Ek bar `finalize_scheduled_message` ke andar `is_scheduled=False` save
    ho jaane ke baad wo row is queryset se khud hi bahar ho jaati hai.
    """
    from .models import Message  # local import — avoid app-loading order issues
    from .scheduled_messages import finalize_scheduled_message

    now = timezone.now()
    sent, failed = 0, 0

    with transaction.atomic():
        due_ids = list(
            Message.objects.select_for_update(skip_locked=True)
            .filter(is_scheduled=True, scheduled_for__lte=now)
            .order_by('scheduled_for')
            .values_list('id', flat=True)[:200]  # ek run me bounded batch — bahut zyada due ho to next tick uthayega
        )

    for message_id in due_ids:
        try:
            message = Message.objects.select_related('conversation', 'sender').get(id=message_id)
            finalize_scheduled_message(message)
            sent += 1
        except Exception:
            # Ek bad message poori batch ko na roke — baaki due messages
            # is run me hi deliver hote rahein.
            failed += 1
            logger.exception("send_scheduled_messages: failed to finalize message=%s", message_id)

    if sent or failed:
        logger.info("send_scheduled_messages: sent=%s failed=%s", sent, failed)

    return {"sent": sent, "failed": failed}


@shared_task(name="message.cleanup_expired_messages")
def cleanup_expired_messages():
    """
    Disappearing-messages hard-delete sweep. `views.py`'s message-list GET
    already defensively hides `expires_at <= now` rows (in case this sweep
    runs late), but that's UI-level only — the DB row stays forever unless
    something actually deletes it. This is that something.

    Suggested schedule: every 15 min (expiry is minute-precision at best —
    the coarsest disappearing-duration option is "1 month" — so a 15 min
    sweep lag is invisible to users, same lookback-vs-cadence reasoning
    `liveclass`'s beat entries already use).

    Bounded batch per run (same reasoning as `send_scheduled_messages`) so
    one huge backlog (e.g. sweep was off for a while) can't hold the DB
    connection / worker for an unbounded amount of time — it just clears
    itself over a few ticks instead of one giant transaction.
    """
    from .models import Message

    now = timezone.now()
    deleted_total = 0

    while True:
        expired_ids = list(
            Message.all_objects.filter(expires_at__isnull=False, expires_at__lte=now)
            .values_list('id', flat=True)[:500]
        )
        if not expired_ids:
            break
        count, _ = Message.all_objects.filter(id__in=expired_ids).delete()
        deleted_total += len(expired_ids)
        if len(expired_ids) < 500:
            break

    if deleted_total:
        logger.info("cleanup_expired_messages: hard-deleted %s expired message(s)", deleted_total)

    return {"deleted": deleted_total}


# ======================================================================
# 🔥 FIX (this session) — Smart notification batching / digest.
# `push_utils.send_chat_message_push` already does the accumulate-in-cache
# half (per (user, conversation) counter + "schedule me once" flag) and
# unconditionally does `from .tasks import flush_chat_push_digest` +
# `.apply_async(...)` — but this task never actually existed here. That
# meant EVERY normal chat-message push (REST, WS, AND scheduled-message
# delivery — all three call `send_chat_message_push`) raised an
# `ImportError` the moment it tried to schedule the flush, i.e. push
# notifications for ordinary messages were silently broken end-to-end
# (mentions/calls were unaffected — those use `send_mention_push` /
# `send_incoming_call_push` directly, bypassing this path entirely).
#
# This is that missing counterpart: it runs once, `CHAT_PUSH_DEBOUNCE_
# SECONDS` after the FIRST message in a window, reads back whatever
# accumulated during that window, and sends either a normal single-
# message push (count == 1) or a "X sent N messages" digest push
# (count > 1) — exactly what `send_chat_digest_push` already exists for.
# ======================================================================
@shared_task(name="message.flush_chat_push_digest")
def flush_chat_push_digest(user_id, conversation_id):
    """
    Scheduled once per debounce window by `push_utils.send_chat_message_
    push` (via `cache.add` on the scheduled-flag key, so a burst of N
    messages only ever enqueues this once). Reads back the count + "most
    recent message" snapshot that accumulated in cache during the window
    and sends exactly one push for it.

    Keys are read-then-explicitly-cleared here (not just left to
    TTL-expire) so a message that arrives in the split-second after this
    task starts reading, but before it finishes, cleanly starts a
    brand-new window (via `cache.add` back in `send_chat_message_push`)
    instead of silently folding into a window whose flush is already in
    flight.
    """
    from django.core.cache import cache
    from .push_utils import (
        _DIGEST_COUNT_KEY, _DIGEST_LAST_KEY, _DIGEST_SCHEDULED_KEY,
        _send_single_chat_push, send_chat_digest_push,
    )

    uid = str(user_id)
    count_key = _DIGEST_COUNT_KEY.format(user=uid, conv=conversation_id)
    last_key = _DIGEST_LAST_KEY.format(user=uid, conv=conversation_id)
    scheduled_key = _DIGEST_SCHEDULED_KEY.format(user=uid, conv=conversation_id)

    count = cache.get(count_key)
    last_raw = cache.get(last_key)

    cache.delete(count_key)
    cache.delete(last_key)
    cache.delete(scheduled_key)

    # Window already flushed/cleared by something else, or expired before
    # we got to it (very slow/delayed worker) — nothing to send.
    if not count or not last_raw:
        return {"sent": False, "reason": "empty_window"}

    try:
        last = json.loads(last_raw)
    except (TypeError, ValueError):
        logger.exception(
            "flush_chat_push_digest: bad last-message payload user=%s conv=%s",
            uid, conversation_id,
        )
        return {"sent": False, "reason": "bad_payload"}

    if count <= 1:
        _send_single_chat_push(
            [uid],
            last.get('sender_name'),
            last.get('text'),
            conversation_id,
            last.get('message_id'),
        )
    else:
        send_chat_digest_push(uid, conversation_id, last.get('sender_name'), count)

    return {"sent": True, "count": count}


# ======================================================================
# 🔥 NAYE — ADVANCED FEATURE #1: Link Previews (background generation)
# ======================================================================
@shared_task(
    name="message.generate_link_preview",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def generate_link_preview_task(self, message_id):
    """
    `views.py` (REST) aur `consumers.py` (WS) — dono, TEXT message me URL
    milne par `.delay(message.id)` se ye task enqueue karte hain (message
    save hone ke turant baad, request ko block kiye bina).

    Fetch fail ho (dead link, timeout, unsafe/internal URL — `link_preview.
    py` ka SSRF-guard) to bas chup-chaap return ho jaata hai — message
    bina preview ke normal text message jaisa hi reh jaata hai, koi error
    user tak nahi jaata.
    """
    from .models import Message, MessageType
    from .link_preview import extract_first_url, fetch_link_preview

    try:
        message = Message.objects.select_related('conversation').get(id=message_id)
    except Message.DoesNotExist:
        return

    if message.type != MessageType.TEXT or not message.text:
        return

    url = extract_first_url(message.text)
    if not url:
        return

    try:
        preview = fetch_link_preview(url)
    except Exception:
        logger.exception("generate_link_preview_task: fetch failed for message=%s", message_id)
        return

    if not preview:
        return

    with transaction.atomic():
        message = Message.objects.select_for_update().get(id=message_id)
        meta = dict(message.meta or {})
        meta['link_preview'] = preview
        message.meta = meta
        message.save(update_fields=['meta', 'updated_at'])

    _broadcast_meta_update(message)


# ======================================================================
# 🔥 NAYE — ADVANCED FEATURE #2: Auto Voice-Message Transcription
# ======================================================================
@shared_task(
    name="message.transcribe_voice_message",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def transcribe_voice_message_task(self, message_id):
    """
    `VoiceTranscribeView` (views_ai.py) already existed — par sirf
    CLIENT-TRIGGERED tha (user ko manually "View transcript" dabana padta
    tha, jo har voice-note me se recipient ko pata bhi nahi chalta ki
    dabane layak cheez hai). Ye task wahi `ai_service.transcribe_audio`
    reuse karta hai, bas AUTOMATICALLY — voice message bhejte hi
    background me transcript ban jaata hai aur `Message.meta['transcript']`
    me save ho jaata hai. Client ab bina extra API-call ke seedha
    message list se transcript dikha sakta hai (agar available ho).

    AI service down/not-configured ho (`AI_ENABLED=False`) to bhi voice
    message normally deliver ho chuka hota hai already — sirf transcript
    add nahi hota, poora chat flow unaffected rehta hai.
    """
    from .models import Message, MessageType
    from .ai_service import transcribe_audio, AI_ENABLED

    if not AI_ENABLED:
        return

    try:
        message = Message.objects.select_related('conversation').get(id=message_id)
    except Message.DoesNotExist:
        return

    if message.type != MessageType.AUDIO or not message.file_url:
        return

    try:
        mime_type = (message.meta or {}).get('mime_type', 'audio/ogg')
        transcript = transcribe_audio(message.file_url, mime_type=mime_type)
    except Exception as e:
        logger.warning("transcribe_voice_message_task: failed for message=%s: %s", message_id, e)
        return

    if not transcript:
        return

    with transaction.atomic():
        message = Message.objects.select_for_update().get(id=message_id)
        meta = dict(message.meta or {})
        meta['transcript'] = transcript
        message.meta = meta
        message.save(update_fields=['meta', 'updated_at'])

    _broadcast_meta_update(message)