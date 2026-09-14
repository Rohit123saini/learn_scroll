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
    lookback-vs-cadence reasoning `liveclass`'s beat entries already
    use). Registered in `settings.CELERY_BEAT_SCHEDULE` as
    "message-cleanup-expired-messages" — this IS already running
    periodically; the management command is a manual/ad-hoc
    supplement, not a second schedule (see that file — TASK 40).
    """
    result = hard_delete_expired_messages(batch_size=500)
    if result["deleted"]:
        logger.info("cleanup_expired_messages: hard-deleted %s expired message(s)", result["deleted"])
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