# message/tasks.py
#
# 🔥 NAYA — Celery tasks. Doc/§9.3 ne flag kiya tha ki `scheduled_messages.py`
# ka delivery half (`finalize_scheduled_message`) sirf ek imaginary
# `send_scheduled_messages` management command se call hone wala tha — par
# na wo command kabhi exist karta tha, na `message` app ka koi entry hi
# `settings.CELERY_BEAT_SCHEDULE` me tha (jabki `tuitionclass` app ke 6+ tasks
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
# Dono tasks Celery `shared_task` hain (`tuitionclass/tasks.py` jaisa hi
# pattern) — `settings.CELERY_BEAT_SCHEDULE` me register karo (neeche
# instructions), poora sweep worker process me chalega, request-response
# cycle se bilkul alag.

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
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


@shared_task(name="message.purge_soft_deleted_conversations")
def purge_soft_deleted_conversations():
    """
    🔧 GAP FIX — `BaseModel.is_deleted`/`soft_delete()` used to be a dead
    field (nothing ever set it). `GroupViewSet.destroy()` (views.py) now
    calls `.soft_delete()` on the group + its conversation instead of a
    hard `conversation.delete()`, so an admin's group-delete tap is
    recoverable for `settings.GROUP_SOFT_DELETE_GRACE_DAYS` days — this
    sweep is the other half: it actually reclaims the storage once that
    grace window has passed, by hard-deleting the `Conversation` row
    (which CASCADEs to Group/GroupMember/ConversationParticipant/Message/
    etc., same as the old immediate hard-delete did — just delayed).

    Uses `Conversation.all_objects` (not `.objects`) since the default
    `SoftDeleteManager` already excludes `is_deleted=True` rows — we need
    to see exactly those rows here.

    Bounded batch per run, same reasoning as the other sweeps in this
    file (a large backlog clears itself over a few daily ticks instead of
    one giant transaction).
    """
    from .models import Conversation

    cutoff = timezone.now() - timedelta(days=settings.GROUP_SOFT_DELETE_GRACE_DAYS)
    due_ids = list(
        Conversation.all_objects.filter(is_deleted=True, updated_at__lte=cutoff)
        .values_list('id', flat=True)[:200]
    )

    deleted = 0
    for conversation_id in due_ids:
        try:
            # `updated_at__lte=cutoff` was checked against the row fetched
            # a moment ago — re-filter on delete so a `.restore()` that
            # landed in between (support recovering it just in time) isn't
            # clobbered by a stale read.
            count, _ = Conversation.all_objects.filter(
                id=conversation_id, is_deleted=True, updated_at__lte=cutoff,
            ).delete()
            if count:
                deleted += 1
        except Exception:
            logger.exception(
                "purge_soft_deleted_conversations: failed to purge conversation=%s", conversation_id
            )

    if deleted:
        logger.info("purge_soft_deleted_conversations: hard-deleted %s conversation(s)", deleted)

    return {"deleted": deleted}


def hard_delete_expired_messages(batch_size=500, dry_run=False):
    """
    Disappearing-messages hard-delete sweep — the actual delete logic,
    factored out so the Celery beat task below AND the
    `cleanup_expired_messages` management command (an ops-facing manual/
    ad-hoc entry point — one-off runs, `--dry-run` inspection, a stuck
    worker needing a manual catch-up sweep) share exactly one
    implementation instead of two that can silently drift apart.

    [TASK 40] This used to be duplicated: the management command had its
    own copy of this loop, querying `Message.objects` (the default,
    NOT-soft-deleted manager) instead of `Message.all_objects` (every
    row, soft-deleted included) used here, and defaulting to a batch
    size of 1000 instead of 500. That manager mismatch meant the
    management command — if anyone actually ran it — would silently
    skip any message that was soft-deleted AND expired, leaving it
    stuck in the DB forever. Both discrepancies are gone now that there
    is only one code path.

    `views.py`'s message-list GET already defensively hides
    `expires_at <= now` rows (in case this sweep runs late), but that's
    UI-level only — the DB row stays forever unless something actually
    deletes it. This is that something.

    Bounded batch per run so one huge backlog (e.g. sweep was off for a
    while, or an operator is running this by hand after downtime) can't
    hold the DB connection / worker for an unbounded amount of time —
    it clears itself over a few iterations instead of one giant
    transaction.
    """
    from .models import Message

    now = timezone.now()
    base_qs = Message.all_objects.filter(expires_at__isnull=False, expires_at__lte=now)

    if dry_run:
        return {"deleted": 0, "would_delete": base_qs.count(), "dry_run": True}

    deleted_total = 0
    while True:
        expired_ids = list(base_qs.values_list('id', flat=True)[:batch_size])
        if not expired_ids:
            break
        with transaction.atomic():
            Message.all_objects.filter(id__in=expired_ids).delete()
        deleted_total += len(expired_ids)
        if len(expired_ids) < batch_size:
            break

    return {"deleted": deleted_total, "dry_run": False}


@shared_task(name="message.cleanup_expired_messages")
def cleanup_expired_messages():
    """
    Celery beat entry point — see `hard_delete_expired_messages()` above
    for the actual sweep logic.

    Suggested/registered schedule: every 15 min (expiry is minute-
    precision at best — the coarsest disappearing-duration option is
    "1 month" — so a 15 min sweep lag is invisible to users, same
    lookback-vs-cadence reasoning `tuitionclass`'s beat entries already
    use). Registered in `settings.CELERY_BEAT_SCHEDULE` as
    "message-cleanup-expired-messages" — this IS already running
    periodically; the management command is a manual/ad-hoc
    supplement, not a second schedule (see that file — TASK 40).
    """
    result = hard_delete_expired_messages(batch_size=500)
    if result["deleted"]:
        logger.info("cleanup_expired_messages: hard-deleted %s expired message(s)", result["deleted"])
    return result


# ================================================================
# 🔥 NAYA (M3b) — "Delete after seen" (true vanish mode)
#
# Rule: `Message.delete_after_seen=True` wala message tab hard-delete hota
# hai jab (1) recipient ne usse padh liya ho (`MessageStatus.is_read`) AUR
# (2) us conversation me ab koi bhi WebSocket khula na ho (chat band).
# Sirf PRIVATE chat — group me "sab ne padha" ka matlab ambiguous hai.
#
# (2) ke liye consumers.py har connect/disconnect pe Redis-cache counter
# `chat_open:<conversation_id>` badhata/ghatata hai. Counter FAIL-SAFE hai:
# worker crash se agar wo atak bhi jaaye to sirf delete late hota hai
# (TTL ke baad khud theek), kabhi galat-jaldi delete nahi hota.
# Unread messages kabhi is rule se delete nahi hote — unka backstop
# `expires_at` (models.py: AFTER_SEEN -> 30 din) existing sweeper sambhalta hai.
# ================================================================
CHAT_OPEN_TTL = 60 * 60 * 24  # counter key ki max zindagi (crash-recovery)


def _chat_open_key(conversation_id):
    return f"chat_open:{conversation_id}"


def chat_open_incr(conversation_id):
    """Naya WS connect — is conversation ka open-connection count +1."""
    from django.core.cache import cache
    key = _chat_open_key(conversation_id)
    cache.add(key, 0, CHAT_OPEN_TTL)
    try:
        n = cache.incr(key)
    except ValueError:  # key add() aur incr() ke beech expire ho gayi
        cache.set(key, 1, CHAT_OPEN_TTL)
        n = 1
    try:
        cache.touch(key, CHAT_OPEN_TTL)  # lambe session me key expire na ho
    except Exception:
        pass
    return n


def chat_open_decr(conversation_id):
    """WS disconnect — count -1; bacha hua count return karta hai (0 = chat band)."""
    from django.core.cache import cache
    key = _chat_open_key(conversation_id)
    try:
        n = cache.decr(key)
    except ValueError:  # key nahi thi (TTL expiry) — band maan lo
        return 0
    if n <= 0:
        cache.delete(key)
        return 0
    return n


def chat_open_count(conversation_id):
    from django.core.cache import cache
    try:
        return int(cache.get(_chat_open_key(conversation_id)) or 0)
    except Exception:
        # cache down ho to SAFE side: "khula hai" maan lo => kuch delete nahi hoga
        return 1


def _refresh_last_message(conversation_ids):
    """
    Hard-delete ke baad chat-list ka denormalized preview (`last_message_*`)
    purane (ab delete ho chuke) message ka text na dikhaye — warna vanish
    message chat list me leak hota rahega.
    """
    from .models import Conversation, Message

    for cid in conversation_ids:
        last = (
            Message.objects.filter(conversation_id=cid, is_scheduled=False)
            .order_by('-created_at').first()
        )
        if last:
            Conversation.objects.filter(id=cid).update(
                last_message_text=(last.text or '')[:500],
                last_message_at=last.created_at,
                last_message_sender_id=last.sender_id,
                last_message_type=last.type,
            )
        else:
            # `last_message_at` jaan-bhujh ke nahi chheda — chat-list ki
            # ordering/position na badle; sirf preview text hatao.
            Conversation.objects.filter(id=cid).update(
                last_message_text=None, last_message_type=None,
            )


def hard_delete_seen_vanish_messages(conversation_id=None, batch_size=500, grace_seconds=0):
    """
    `delete_after_seen` messages ka hard-delete sweep (private chats only).

    - `conversation_id` diya ho to sirf wahi chat (disconnect-trigger);
      None ho to sab chats (periodic safety-net).
    - `grace_seconds`: periodic run me sirf wahi messages jinhe padhe hue
      itna waqt ho chuka — taaki REST se padhne wala client abhi WS connect
      kar raha ho to race na ho.
    - Jis chat me abhi koi WS khula hai wahan kuch delete nahi hota.
    """
    from django.db.models import Exists, OuterRef
    from .models import ConversationType, Message, MessageStatus

    now = timezone.now()
    seen = MessageStatus.objects.filter(
        message=OuterRef('pk'), is_read=True,
        read_at__lte=now - timedelta(seconds=grace_seconds),
    ).filter(~Q(user_id=OuterRef('sender_id')))
    unread = MessageStatus.objects.filter(
        message=OuterRef('pk'), is_read=False,
    ).filter(~Q(user_id=OuterRef('sender_id')))

    base_qs = Message.all_objects.filter(
        delete_after_seen=True,
        is_scheduled=False,
        conversation__type=ConversationType.PRIVATE,
    ).filter(Exists(seen), ~Exists(unread))
    if conversation_id is not None:
        base_qs = base_qs.filter(conversation_id=conversation_id)

    conv_ids = list(base_qs.order_by().values_list('conversation_id', flat=True).distinct())
    deleted_total, touched = 0, []
    for cid in conv_ids:
        if chat_open_count(cid) > 0:
            continue
        conv_qs = base_qs.filter(conversation_id=cid)
        while True:
            ids = list(conv_qs.values_list('id', flat=True)[:batch_size])
            if not ids:
                break
            if chat_open_count(cid) > 0:  # delete se theek pehle dobara check (race)
                break
            with transaction.atomic():
                Message.all_objects.filter(id__in=ids).delete()
            deleted_total += len(ids)
            touched.append(cid)
            if len(ids) < batch_size:
                break

    if touched:
        _refresh_last_message(set(touched))
    return {"deleted": deleted_total}


@shared_task(name="message.sweep_vanish_for_conversation")
def sweep_vanish_for_conversation(conversation_id):
    """ChatConsumer.disconnect se trigger (countdown ke saath — flaky reconnect absorb)."""
    if chat_open_count(conversation_id) > 0:
        return {"deleted": 0, "skipped": "chat_open"}
    result = hard_delete_seen_vanish_messages(conversation_id=conversation_id)
    if result["deleted"]:
        logger.info("sweep_vanish_for_conversation: %s message(s) conv=%s", result["deleted"], conversation_id)
    return result


@shared_task(name="message.cleanup_seen_vanish_messages")
def cleanup_seen_vanish_messages():
    """
    Periodic safety-net (suggested: har 5 min, grace 2 min) — un chats ke liye
    jahan disconnect-trigger miss ho gaya (REST-only read, worker crash, ...).
    """
    result = hard_delete_seen_vanish_messages(grace_seconds=120)
    if result["deleted"]:
        logger.info("cleanup_seen_vanish_messages: hard-deleted %s message(s)", result["deleted"])
    return result


# 🔧 GAP FIX (TASK 24) — `CHAT_APP_DOCUMENTATION.md` (item 25) had already
# claimed this wrapper was added "this pass"; it wasn't — verified
# directly against this file, which had no `expire_stale_parent_access`
# task (or anything `parent_access`/`ParentToken`/`ParentAccessCode`-
# related) at all. `LearnScroll_project_documentation.md` (item 8) was
# the accurate one, flagging exactly this as unconfirmed. If a
# `settings.CELERY_BEAT_SCHEDULE` entry already points at
# `"message.expire_stale_parent_access"` (per that same doc claim), it
# was a silent `NotRegistered` no-op until now — added for real below.
#
# Two independent sweeps, same run, same "bounded batch per run, rest
# picked up next tick" reasoning every other sweep in this file uses:
#
#   1. `ParentAccessCode` rows past their own absolute `expires_at` but
#      still `is_active=True` — `ParentAccessCode.is_expired` (models.py)
#      and every access-time check (`ParentVerifyCodeView`,
#      `HasValidParentToken`) already treat these as dead the moment
#      `expires_at` passes, regardless of the stored `is_active` value —
#      so this sweep changes no *behavior*, it only brings the stored
#      flag in line with what's already true at read time (matters for
#      anything that queries `is_active=True` directly without
#      re-deriving `is_expired`, e.g. an admin/"how many active parent
#      links" count).
#   2. `ParentToken` rows past their own rolling `INACTIVITY_TTL_DAYS`
#      inactivity window (`ParentToken.is_expired`, models.py — measured
#      from `last_seen_at`, or `created_at` if the token was verified but
#      never actually used). Unlike `ParentAccessCode`, `ParentToken` has
#      no `is_active` flag of its own to flip — a token's only expiry
#      signal IS that rolling-inactivity check — so there's nothing to
#      "deactivate" here, only stale rows to reclaim; hard-deleted, same
#      storage-hygiene posture `cleanup_expired_messages` above already
#      takes for messages past their own `expires_at`. `HasValidParentToken`
#      already denies these before this sweep ever runs — deleting them
#      is cleanup, not what makes them stop working.
#
# Suggested schedule: once daily (both windows here are day-granularity —
# `expires_at`/`INACTIVITY_TTL_DAYS=30` — unlike the minute/15-min
# cadence the message-delivery/disappearing-message sweeps above need).
@shared_task(name="message.expire_stale_parent_access")
def expire_stale_parent_access():
    """
    Sweeps two independent "stale" states on the parent-portal auth
    chain (see `models.py`'s `ParentAccessCode`/`ParentToken` — this
    task's own module-level comment above has the full reasoning for
    why each is handled the way it is):

      - `ParentAccessCode.is_active=True` rows whose `expires_at` has
        passed -> flipped to `is_active=False` (bounded batch — a large
        backlog clears over a few ticks, same as every other sweep in
        this file).
      - `ParentToken` rows past `INACTIVITY_TTL_DAYS` of inactivity
        (`last_seen_at`, falling back to `created_at` for a token that
        was verified but never subsequently used) -> hard-deleted
        (bounded batch, same reasoning).

    Both checks are re-derived here via direct queryset filters (not by
    loading every row and checking the `.is_expired` property in Python)
    so the sweep stays a couple of cheap indexed queries regardless of
    table size — `ParentAccessCode` already has an
    `Index(fields=['is_active', 'expires_at'])` (models.py) matching
    exactly this filter shape.
    """
    from .models import ParentAccessCode, ParentToken

    now = timezone.now()

    expired_code_ids = list(
        ParentAccessCode.objects.filter(
            is_active=True, expires_at__isnull=False, expires_at__lte=now,
        ).values_list('id', flat=True)[:200]
    )
    deactivated = 0
    if expired_code_ids:
        deactivated = ParentAccessCode.objects.filter(id__in=expired_code_ids).update(
            is_active=False, updated_at=now,
        )

    cutoff = now - timedelta(days=ParentToken.INACTIVITY_TTL_DAYS)
    stale_token_ids = list(
        ParentToken.objects.filter(
            Q(last_seen_at__lte=cutoff) | Q(last_seen_at__isnull=True, created_at__lte=cutoff)
        ).values_list('id', flat=True)[:200]
    )
    deleted_tokens = 0
    if stale_token_ids:
        deleted_tokens, _ = ParentToken.objects.filter(id__in=stale_token_ids).delete()

    if deactivated or deleted_tokens:
        logger.info(
            "expire_stale_parent_access: deactivated_codes=%s deleted_tokens=%s",
            deactivated, deleted_tokens,
        )

    return {"deactivated_codes": deactivated, "deleted_tokens": deleted_tokens}


# ======================================================================
# 🔧 REMOVED (WhatsApp-style push, this session) — `flush_chat_push_
# digest` used to live here as a `countdown`-scheduled Celery task that
# `push_utils.send_chat_message_push` called to flush a 30s debounce
# window. That artificial wait has been removed: `send_chat_message_push`
# now sends every push immediately (single or digest, based on a rolling
# unread-count) with no delayed task involved. Nothing schedules this
# task anymore, so it's gone — see `push_utils.py`'s `send_chat_message_
# push` for the new immediate-send logic.
# ======================================================================


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


# ======================================================================
# 🔥 NAYA — ADVANCED FEATURE #3: Class transcript (chunk transcription)
# ======================================================================
@shared_task(
    name="message.transcribe_class_chunk",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def transcribe_class_chunk_task(self, segment_id):
    """
    `ClassTranscriptChunkUploadView` (views_ai.py) har naye chunk pe ye
    task enqueue karta hai. Same `ai_service.transcribe_audio()` reuse
    karta hai jo voice-note transcription (upar) already use karta hai —
    ye function file_url se audio download karke Gemini ko bhejta hai,
    result string return karta hai. Yahan sirf destination alag hai:
    `Message.meta['transcript']` ki jagah `ClassTranscriptSegment.text`.

    Fail ho jaaye (AI down, corrupt chunk, waghera) to segment
    `status='failed'` pe chala jaata hai — search/copilot dono failed
    segments ko silently ignore karte hain (STATUS_DONE filter), koi
    user-facing error nahi aata, bas wo chunk transcript me missing rahega.
    """
    from .models import ClassTranscriptSegment
    from .ai_service import transcribe_audio, AI_ENABLED

    try:
        segment = ClassTranscriptSegment.objects.get(id=segment_id)
    except ClassTranscriptSegment.DoesNotExist:
        return

    if not AI_ENABLED:
        segment.status = ClassTranscriptSegment.STATUS_FAILED
        segment.save(update_fields=['status'])
        return

    try:
        # Chunks `record` package se AAC-LC (.m4a) me record hote hain
        # (chat voice-note recording jaisa hi — study_room_call_manager.dart
        # ki chunk-recording usi encoder ko reuse karti hai), isliye same
        # default mime type.
        transcript = transcribe_audio(segment.audio_file_url, mime_type="audio/mp4")
    except Exception as e:
        logger.warning("transcribe_class_chunk_task: failed for segment=%s: %s", segment_id, e)
        segment.status = ClassTranscriptSegment.STATUS_FAILED
        segment.save(update_fields=['status'])
        return

    segment.text = transcript or ''
    segment.status = ClassTranscriptSegment.STATUS_DONE
    segment.save(update_fields=['text', 'status'])

    # Live "recap" screen agar khula hai to turant naya segment dikhe,
    # isliye same `meta_update`-jaisa passthrough pattern — naya WS event
    # type `transcript_segment_ready` (consumers.py me ek chhota handler
    # add karna hoga, bilkul `meta_update` jaisa hi plain-passthrough).
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'chat_{segment.conversation_id}',
        {
            'type': 'transcript_segment_ready',
            'segment': {
                'id': str(segment.id),
                'session_id': segment.session_id,
                'start_offset_seconds': segment.start_offset_seconds,
                'end_offset_seconds': segment.end_offset_seconds,
                'text': segment.text,
                'speaker_id': str(segment.speaker_id) if segment.speaker_id else None,
            },
        },
    )


# ======================================================================
# 🔥 NAYA (M2-BE) — expired Notes ka daily cleanup.
# Correctness is task par NIRBHAR NAHI: `GET /message/notes/` read time par
# `expires_at > now` filter karta hai. Ye sirf purani rows hatata hai.
# Register: settings.CELERY_BEAT_SCHEDULE me
#   "message-cleanup-expired-notes": {
#       "task": "message.cleanup_expired_notes",
#       "schedule": crontab(hour=3, minute=30),
#   }
# ======================================================================
@shared_task(name="message.cleanup_expired_notes")
def cleanup_expired_notes():
    from .user_notes import purge_expired_notes

    result = purge_expired_notes()
    if result["deleted"]:
        logger.info("cleanup_expired_notes: deleted %s expired note(s)", result["deleted"])
    return result


# ======================================================================
# 🔥 C1 — CALL LIFECYCLE SWEEPER (stuck / ghost calls)
# ----------------------------------------------------------------------
# Pehle ring-timeout SIRF client me tha (call_manager.dart `_noAnswerTimer`,
# 30s). Caller ka app kill / net off ho jaye to `CallSession` hamesha
# `ringing`/`ongoing` pada rehta, callee ka native ring kabhi cancel nahi
# hota aur missed-call history bhi nahi banti. Ye task server ko apni
# "source of truth" banata hai:
#
#   1. ringing/initiated + `CALL_RING_TIMEOUT_SECONDS` se purani
#      -> MISSED (participants MISSED, cancel push, `call_end` event).
#   2. ongoing + koi bhi ONGOING participant bacha nahi
#      -> ended (duration ke saath).
#   3. ongoing + `CALL_STALE_ONGOING_SECONDS` se purani
#      -> ended (LiveKit token 2h me expire hota hai, uske baad media
#      possible hi nahi — ye ghost calls ka hard cap hai).
#   4. ongoing call me add-participant se bulaye gaye jo
#      `CALL_RING_TIMEOUT_SECONDS` me nahi uthe -> MISSED (call chalti rahe).
#
# Idempotent + bounded (`CALL_SWEEP_BATCH`) + har call row-lock ke saath
# re-check hoti hai, isliye accept/end ke saath race safe hai.
# Beat: "message-sweep-stale-calls" (settings.py), har 30s.
# ======================================================================
_CALL_TERMINAL_STATUSES = ('ended', 'missed', 'rejected', 'busy', 'cancelled')


def _broadcast_call_closed(call, reason):
    """Best-effort: dono groups (call_<id> + chat_<conv>) ko `call_end`
    bhejo taaki open CallScreen/IncomingCallScreen turant band ho."""
    try:
        layer = get_channel_layer()
        if layer is None:
            return
        user_id = str(call.caller_id)
        async_to_sync(layer.group_send)(
            f'call_{call.id}',
            {
                'type': 'call_signal',
                'data': {'event': 'call_end', 'user_id': user_id,
                         'call_id': str(call.id), 'reason': reason, 'call_closed': True},
            },
        )
        if call.conversation_id:
            async_to_sync(layer.group_send)(
                f'chat_{call.conversation_id}',
                {
                    'type': 'call_event',
                    'event': 'call_end',
                    'call_id': str(call.id),
                    'user_id': user_id,
                    'reason': reason,
                    'call_closed': True,
                },
            )
    except Exception:
        logger.warning("sweep_stale_calls: broadcast failed call=%s", call.id, exc_info=True)


def _cancel_ring_push(user_ids, call):
    if not user_ids:
        return
    try:
        from .push_utils import send_call_cancelled_push
        send_call_cancelled_push(
            recipient_ids=list(user_ids),
            call_id=call.id,
            conversation_id=call.conversation_id,
        )
    except Exception:
        logger.warning("sweep_stale_calls: cancel push failed call=%s", call.id, exc_info=True)


def _finish_stale_call(call_id, now, reason):
    """Ek call ko safely band karo. True = is run ne band ki."""
    from .models import CallParticipant, CallSession, CallStatus

    with transaction.atomic():
        call = CallSession.objects.select_for_update().filter(id=call_id).first()
        if call is None or call.status in _CALL_TERMINAL_STATUSES:
            return False

        ringing_user_ids = list(
            CallParticipant.objects.filter(call=call, status=CallStatus.RINGING)
            .values_list('user_id', flat=True)
        )
        CallParticipant.objects.filter(call=call, status=CallStatus.RINGING).update(
            status=CallStatus.MISSED, left_at=now,
        )
        CallParticipant.objects.filter(
            call=call, status=CallStatus.ONGOING, left_at__isnull=True,
        ).update(status=CallStatus.ENDED, left_at=now)

        call.status = CallStatus.ENDED if call.connected_at else CallStatus.MISSED
        call.ended_at = now
        if call.connected_at:
            call.duration_seconds = max(int((now - call.connected_at).total_seconds()), 0)
        call.is_recording = False
        call.save(update_fields=['status', 'ended_at', 'duration_seconds', 'is_recording', 'updated_at'])

    if call.status == CallStatus.MISSED:
        # X1 — kabhi connect na hui call = "missed" ring outcome (once per call; sweeper hi nahi, webhook room_finished bhi yahin aata hai).
        from . import call_ops
        call_ops.note_ring_outcome('missed', call_id=call.id)
    _cancel_ring_push(ringing_user_ids, call)
    # C12 — jo ring me the (ab MISSED) unko "Missed call" push + bell row; kabhi connect na hui call ka chat bubble.
    if ringing_user_ids or call.status == CallStatus.MISSED:
        enqueue_missed_call(call.id, ringing_user_ids)
    _broadcast_call_closed(call, reason)
    return True


@shared_task(name="message.sweep_stale_calls")
def sweep_stale_calls():
    from django.db.models import Exists, OuterRef
    from .models import CallParticipant, CallSession, CallStatus

    now = timezone.now()
    ring_timeout = int(getattr(settings, 'CALL_RING_TIMEOUT_SECONDS', 45))
    stale_ongoing = int(getattr(settings, 'CALL_STALE_ONGOING_SECONDS', 3 * 3600))
    batch = int(getattr(settings, 'CALL_SWEEP_BATCH', 200))
    ring_cutoff = now - timedelta(seconds=ring_timeout)
    ongoing_cutoff = now - timedelta(seconds=stale_ongoing)

    # 1) kisi ne uthayi hi nahi
    ring_ids = list(
        CallSession.objects.filter(
            status__in=[CallStatus.INITIATED, CallStatus.RINGING],
            started_at__lte=ring_cutoff,
        ).values_list('id', flat=True)[:batch]
    )
    timed_out = sum(1 for cid in ring_ids if _finish_stale_call(cid, now, 'ring_timeout'))

    # 2) ongoing par koi active participant nahi (ring_cutoff grace accept
    #    ke saath race se bachne ke liye)
    active = CallParticipant.objects.filter(
        call=OuterRef('pk'), status=CallStatus.ONGOING, left_at__isnull=True,
    )
    empty_ids = list(
        CallSession.objects.filter(status=CallStatus.ONGOING, started_at__lte=ring_cutoff)
        .annotate(has_active=Exists(active)).filter(has_active=False)
        .values_list('id', flat=True)[:batch]
    )
    emptied = sum(1 for cid in empty_ids if _finish_stale_call(cid, now, 'no_participants'))

    # 3) hard cap
    old_ids = list(
        CallSession.objects.filter(status=CallStatus.ONGOING, started_at__lte=ongoing_cutoff)
        .values_list('id', flat=True)[:batch]
    )
    capped = sum(1 for cid in old_ids if _finish_stale_call(cid, now, 'max_duration'))

    # 4) chalti call me add-participant se bulaye, jawab nahi diya
    late = list(
        CallParticipant.objects.filter(
            status=CallStatus.RINGING, joined_at__lte=ring_cutoff,
            call__status=CallStatus.ONGOING,
        ).values_list('id', 'call_id', 'user_id')[:batch]
    )
    missed_late = 0
    if late:
        by_call = {}
        for pid, cid, uid in late:
            by_call.setdefault(cid, []).append((pid, uid))
        for cid, rows in by_call.items():
            updated = CallParticipant.objects.filter(
                id__in=[r[0] for r in rows], status=CallStatus.RINGING,
            ).update(status=CallStatus.MISSED, left_at=now)
            missed_late += updated
            call = CallSession.objects.filter(id=cid).first()
            if call:
                _cancel_ring_push([r[1] for r in rows], call)
                # C12 — chalti call me bulaye gaye jo nahi uthe: missed push + bell row (bubble nahi — call connected hai).
                enqueue_missed_call(call.id, [r[1] for r in rows])

    if timed_out or emptied or capped or missed_late:
        logger.info(
            "sweep_stale_calls: ring_timeout=%s no_participants=%s max_duration=%s late_invites_missed=%s",
            timed_out, emptied, capped, missed_late,
        )


# ======================================================================
# 🔥 C11 — group call ring push + bell rows (request ke andar nahi, Celery me)
# ----------------------------------------------------------------------
# 200-member group me initiate ke andar hi sync FCM multicast + har user ka
# `create_notification` request ko slow/timeout kar deta tha. Ab view sirf DB
# + ek WS event karta hai; push `CALL_PUSH_CHUNK` recipients ke chhote parallel
# tasks me jaata hai. Har task call ko dobara fetch karke check karta hai ki
# call abhi bhi ring ho rahi hai aur recipient abhi bhi RINGING hai (caller ne
# queue me rehte rehte cancel kar diya ho to koi "ghost ring" na jaaye).
# 1:1 call par ye path use nahi hota (single user — sync hi fast hai).
# ======================================================================
@shared_task(
    name="message.send_call_ring_push",
    bind=True,
    max_retries=1,
    default_retry_delay=2,
    acks_late=True,
)
def send_call_ring_push_task(self, call_id, recipient_ids, caller_name="", caller_avatar=None,
                             call_waiting=False):
    from .models import CallParticipant, CallSession, CallStatus
    from .push_utils import send_incoming_call_push

    call = CallSession.objects.filter(id=call_id).first()
    if call is None or call.status not in (CallStatus.INITIATED, CallStatus.RINGING, CallStatus.ONGOING):
        return 0

    still_ringing = list(
        CallParticipant.objects.filter(
            call=call, user_id__in=recipient_ids, status=CallStatus.RINGING, left_at__isnull=True,
        ).values_list('user_id', flat=True)
    )
    if not still_ringing:
        return 0

    try:
        send_incoming_call_push(
            recipient_ids=still_ringing,
            caller_name=caller_name,
            call_type=call.type,
            call_id=call.id,
            conversation_id=call.conversation_id,
            channel_name=call.channel_name,
            caller_avatar=caller_avatar,
            call_waiting=call_waiting,
        )
    except Exception as exc:
        logger.warning("send_call_ring_push failed call=%s (%s recipients)", call_id, len(still_ringing),
                       exc_info=True)
        raise self.retry(exc=exc)
    return len(still_ringing)


def enqueue_call_ring_push(call_id, recipient_ids, *, caller_name="", caller_avatar=None, call_waiting=False):
    """
    Ring push ko `CALL_PUSH_CHUNK` ke tukdon me Celery me bhejo (commit ke baad).
    Broker down ho to `dispatch_after_commit` inline chala deta hai. Kabhi raise nahi karta.
    """
    ids = [str(u) for u in recipient_ids]
    if not ids:
        return 0
    try:
        from core.async_utils import dispatch_after_commit
        chunk = max(int(getattr(settings, 'CALL_PUSH_CHUNK', 100)), 1)
        for i in range(0, len(ids), chunk):
            dispatch_after_commit(
                send_call_ring_push_task,
                str(call_id), ids[i:i + chunk],
                caller_name=caller_name, caller_avatar=caller_avatar, call_waiting=call_waiting,
            )
    except Exception:
        logger.exception("enqueue_call_ring_push failed call=%s", call_id)
        return 0
    return len(ids)


# ======================================================================
# 🔥 C12 — missed-call push + bell row + chat bubble (Celery me)
# ----------------------------------------------------------------------
# Sweeper / cancel / busy / socket-leave jaise hooks sirf `enqueue_missed_call` bulate hain (commit ke baad);
# asli kaam `message/missed_calls.py::process_missed_call` me hai. Task call + participant rows dobara
# padhta hai (sirf abhi bhi MISSED wale), isliye retry / duplicate enqueue safe hai (bell row `data.missed`,
# bubble `meta.call_id` se dedupe).
# `CALL_MISSED_NOTIFY_ASYNC=false` => request/sweeper ke andar hi sync (broker ke bina bhi chalta hai).
# ======================================================================
@shared_task(
    name="message.notify_missed_call",
    bind=True,
    max_retries=2,
    default_retry_delay=3,
    acks_late=True,
)
def notify_missed_call_task(self, call_id, user_ids):
    from .missed_calls import process_missed_call

    try:
        return process_missed_call(call_id, list(user_ids or []))
    except Exception as exc:  # process_missed_call khud swallow karta hai; ye sirf unexpected infra errors ke liye
        logger.warning("notify_missed_call failed call=%s", call_id, exc_info=True)
        raise self.retry(exc=exc)


def enqueue_missed_call(call_id, user_ids):
    """
    Missed-call notification (push + bell row + bubble) schedule karo. Kabhi raise nahi karta.
    `user_ids` khali ho sakta hai — tab sirf call-level chat bubble (agar call connect hi nahi hui) banta hai.
    """
    ids = [str(u) for u in (user_ids or []) if u is not None]
    try:
        if not getattr(settings, 'CALL_MISSED_NOTIFY_ASYNC', True):
            from .missed_calls import process_missed_call
            process_missed_call(str(call_id), ids)
            return len(ids)
        from core.async_utils import dispatch_after_commit
        dispatch_after_commit(notify_missed_call_task, str(call_id), ids)
    except Exception:
        logger.exception("enqueue_missed_call failed call=%s", call_id)
        return 0
    return len(ids)



# ======================================================================
# S5 — study room presence sweeper
# ----------------------------------------------------------------------
# LiveKit ka `participant_left` webhook kho jaye (server down / network) to interval khula reh jata hai.
# Ye task unhe LiveKit se verify karke band karta hai (study_room_presence.sweep_stale).
# Beat: "message-sweep-stale-study-presence" (settings.CELERY_BEAT_SCHEDULE) — har 10 min.
# ======================================================================
@shared_task(name="message.sweep_stale_study_presence")
def sweep_stale_study_presence():
    from .study_room_presence import sweep_stale

    summary = sweep_stale()
    if summary.get('closed'):
        logger.info("message.sweep_stale_study_presence: %s", summary)
    return summary


# ======================================================================
# X1 — call SLO fast-burn watch. Beat: "message-call-slo-watch" (settings.py), har 5 min.
# connect-success < CALL_SLO["connect_success_pct"] (default 95 %) over the last `alert_window_minutes` with at least
# `alert_min_attempts` client connect attempts => ONE ERROR log + Sentry message (message/call_ops.py).
# ======================================================================
@shared_task(name="message.call_slo_watch")
def call_slo_watch():
    from . import call_ops

    summary = call_ops.check_and_alert()
    c = summary["connect"]
    return {"attempts": c["attempts"], "success_pct": c["success_pct"], "problems": summary["problems"]}


# ======================================================================
# N3 — push reliability. Dono tasks ka logic message/push_reliability.py + push_utils.py me hai.
#   message.retry_push                    transient FCM failure (503 / 500 / quota / network) ka exponential-backoff retry.
#                                         push_utils._send_multicast khud schedule karta hai (5s, 20s, 80s; call ring ki
#                                         apni TTL khatam to retry nahi).
#   message.cleanup_stale_device_tokens   Beat: "message-cleanup-stale-device-tokens" (settings.CELERY_BEAT_SCHEDULE), roz 04:30.
#                                         60 din se dikhe nahi / lagataar fail / orphan session / per-user 10+ tokens.
# ======================================================================
@shared_task(name="message.retry_push", ignore_result=True)
def retry_push(tokens, attempt=1, expires_at=None, notification=None, data=None, android_priority="high",
               channel_id="chat_messages", apns_alert=None, ttl_seconds=None, collapse_key=None,
               apns_thread_id=None, apns_collapse_id=None):
    import time

    from firebase_admin import messaging

    from . import push_utils
    from .models import DeviceToken

    if expires_at is not None and time.time() >= float(expires_at):
        return {"sent": 0, "reason": "expired"}
    # Is beech logout / token rotate / dead-token delete ho chuka ho to wo token ab bhejna nahi.
    live = set(DeviceToken.objects.filter(token__in=list(tokens or [])).values_list("token", flat=True))
    tokens = [t for t in (tokens or []) if t in live]
    if not tokens:
        return {"sent": 0, "reason": "no_live_tokens"}
    note = None
    if notification:
        note = messaging.Notification(
            title=notification.get("title"), body=notification.get("body"), image=notification.get("image"),
        )
    push_utils._send_multicast(
        tokens, notification=note, data=data, android_priority=android_priority, channel_id=channel_id,
        apns_alert=apns_alert, ttl_seconds=ttl_seconds, collapse_key=collapse_key,
        apns_thread_id=apns_thread_id, apns_collapse_id=apns_collapse_id,
        retry_attempt=int(attempt), expires_at=float(expires_at) if expires_at is not None else None,
    )
    return {"sent": len(tokens), "attempt": int(attempt)}


@shared_task(name="message.cleanup_stale_device_tokens")
def cleanup_stale_device_tokens():
    from .push_reliability import cleanup_stale_tokens

    summary = cleanup_stale_tokens()
    if any(summary.values()):
        logger.info("message.cleanup_stale_device_tokens: %s", summary)
    return summary
