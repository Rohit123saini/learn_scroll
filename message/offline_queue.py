# message/offline_queue.py
"""
Task 49 — Offline-first chat queue, delivery half (backend side only —
see this module's own note at the bottom on what the Flutter client is
responsible for; that part isn't in scope here since no frontend code
was available to read).

MIRRORS `scheduled_messages.py`'s OWN chosen pattern, deliberately, not
a shared abstraction: this codebase's convention (see
`scheduled_messages.py`'s docstring — "bilkul waisा jaisa
ConversationViewSet.messages POST normal message ke liye karta hai") is
duplicate-but-consistent delivery code per entry-point, not one shared
mega-function every path funnels through. `finalize_queued_message`
below follows that exact convention — same shape as
`finalize_scheduled_message`, adapted for CREATING a new message
(offline queue) instead of flipping an existing `is_scheduled=True` row.

⚠️ `message/models.py` and `message/views.py` were overwritten again in
this upload batch by `login`'s same-named files (same collision as the
classroom-chat-bridge batch — please re-upload those two with distinct
names if anything here needs correcting against your real
`ConversationViewSet`/`Message` model). Everything below is written
against what was already read from those files earlier in this session
(the `Message` model's `client_id`/`text`/`type`/`file_url`/`file_urls`/
`thumbnail_url`/`meta`/`reply_to_id`/`mentioned_users` fields, and
`ConversationViewSet`'s `.messages`/`.schedule_message` actions) — not
re-verified against fresh file content.

WHY IDEMPOTENCY IS THE ACTUAL BACKEND WORK HERE: "local queue +
retry-on-reconnect" is mostly a CLIENT concern (Flutter stores unsent
messages locally, retries POSTing them once connectivity returns) — the
one thing the backend MUST provide for that to be safe is: **sending the
same queued message twice (e.g. the client's first attempt actually
succeeded server-side but the response was lost before the client saw
it, so it retries) must never create a duplicate message.** That's what
`client_id` + the new unique constraint (see migration) gives you.
"""
import logging

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from .mentions import extract_mentioned_user_ids
from .media_utils import create_group_media_for_message
from .models import ConversationParticipant, Message, MessageStatus, MessageType
from .push_utils import send_chat_message_push, send_mention_push
from .services import add_or_reactivate_participant  # re-exported from task 27's services.py
from .user_display import build_user_mini

logger = logging.getLogger(__name__)


def flush_offline_queue(*, conversation, sender, queued_messages: list) -> list:
    """
    `queued_messages`: list of dicts, ONE per locally-queued message, in
    the order the client composed them:
        {
            "client_id": "<client-generated uuid, REQUIRED>",
            "type": "text" | "image" | ... (MessageType value),
            "text": "...",                       # optional depending on type
            "file_url": "...", "file_urls": [...], "meta": {...},  # optional
            "reply_to": "<message id>",           # optional
            "client_created_at": "2026-09-07T10:15:00Z",  # when composed, offline
        }

    Returns a list of per-item results, SAME ORDER as input, each:
        {"client_id": ..., "status": "created", "message_id": "..."}
        {"client_id": ..., "status": "duplicate", "message_id": "..."}  # already existed — safe retry
        {"client_id": ..., "status": "error", "detail": "..."}
    so the Flutter client can reconcile its local queue: drop
    "created"/"duplicate" entries, keep "error" ones queued for a later
    retry (or surface them to the user — e.g. a reply_to referencing a
    message that no longer exists).

    One bad item never aborts the rest of the batch — same fail-safe-per-
    row discipline `scheduled_messages.py`'s own caller (the management
    command) already uses.
    """
    results = []
    for item in queued_messages:
        client_id = item.get("client_id")
        if not client_id:
            results.append({"client_id": None, "status": "error", "detail": "client_id is required."})
            continue
        try:
            message, created = _get_or_create_queued_message(conversation, sender, item)
        except Exception:
            logger.exception(
                "Failed processing offline-queued message client_id=%s for conversation %s.",
                client_id, conversation.id,
            )
            results.append({"client_id": client_id, "status": "error", "detail": "Could not process this message."})
            continue

        if created:
            try:
                _deliver_queued_message(message)
            except Exception:
                # Message row DOES exist at this point — a delivery-side
                # failure (broadcast/push) must not make the client think
                # the send itself failed and retry it (that would risk a
                # SECOND row if the unique constraint's race window is
                # ever hit oddly) — log and still report "created".
                logger.exception("Failed delivering offline-queued message %s.", message.id)
            results.append({"client_id": client_id, "status": "created", "message_id": str(message.id)})
        else:
            results.append({"client_id": client_id, "status": "duplicate", "message_id": str(message.id)})

    return results


def _get_or_create_queued_message(conversation, sender, item: dict):
    """Idempotent create — relies on the DB-level unique constraint
    (see migration) as the real safety net; `get_or_create` alone is not
    race-safe under concurrent duplicate submissions, the
    `IntegrityError` catch below is."""
    client_id = item["client_id"]
    try:
        with transaction.atomic():
            message = Message.objects.create(
                conversation=conversation,
                sender=sender,
                client_id=client_id,
                type=item.get("type", MessageType.TEXT),
                text=item.get("text", ""),
                file_url=item.get("file_url"),
                file_urls=item.get("file_urls") or [],
                meta=item.get("meta") or {},
                reply_to_id=item.get("reply_to"),
            )
        return message, True
    except IntegrityError:
        # Someone (a genuinely concurrent retry, or the same request
        # replayed) already created this exact (conversation, sender,
        # client_id) — fetch and treat as a no-op success, not an error.
        existing = Message.objects.filter(
            conversation=conversation, sender=sender, client_id=client_id,
        ).first()
        if existing is None:
            raise  # constraint fired for some other reason — don't swallow silently
        return existing, False


def _deliver_queued_message(message):
    """Broadcast + unread-count + mentions + media + push — same 4 things
    `finalize_scheduled_message` does for scheduled messages, same thing
    the real-time `ConversationViewSet.messages` POST path does for a
    live send. Kept as its own function (not shared with either of
    those) per this codebase's established convention — see module
    docstring."""
    conversation = message.conversation

    with transaction.atomic():
        conversation.last_message_text = (message.text or "")[:500]
        conversation.last_message_at = message.created_at
        conversation.last_message_sender_id = message.sender_id
        conversation.last_message_type = message.type
        conversation.save(update_fields=[
            "last_message_text", "last_message_at", "last_message_sender", "last_message_type",
        ])

        # Sender might have left+rejoined while offline — same
        # reactivate-on-send safety net the real-time path uses.
        add_or_reactivate_participant(conversation, message.sender)

        ConversationParticipant.objects.filter(conversation=conversation).exclude(
            user_id=message.sender_id
        ).update(unread_count=F("unread_count") + 1)

        other_participant_ids = list(
            ConversationParticipant.objects.filter(conversation=conversation)
            .exclude(user_id=message.sender_id)
            .values_list("user_id", flat=True)
        )
        MessageStatus.objects.bulk_create(
            [MessageStatus(message=message, user_id=uid) for uid in other_participant_ids],
            ignore_conflicts=True,
        )

        mentioned_ids = extract_mentioned_user_ids(message.text, conversation)
        mentioned_ids = [uid for uid in mentioned_ids if uid != message.sender_id]
        if mentioned_ids:
            message.mentioned_users.set(mentioned_ids)

        create_group_media_for_message(message)

    sender = build_user_mini(message.sender)
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"chat_{conversation.id}",
        {
            "type": "chat_message",
            "event": "message",
            "id": str(message.id),
            "conversation_id": str(conversation.id),
            "sender_id": str(message.sender_id),
            "sender_name": sender["display_name"],
            "sender_username": sender["username"],
            "sender_first_name": sender["first_name"],
            "sender_last_name": sender["last_name"],
            "sender_profile_photo": sender["profile_photo"],
            "message_type": message.type,
            "text": message.text,
            "file_url": message.file_url,
            "file_urls": message.file_urls,
            "thumbnail_url": message.thumbnail_url,
            "meta": message.meta,
            "reply_to": str(message.reply_to_id) if message.reply_to_id else None,
            "client_id": message.client_id,
            "mentioned_user_ids": [str(uid) for uid in mentioned_ids],
            "created_at": message.created_at.isoformat(),
            # 🔥 NAYA (task 49) — lets the client tell "sent live" apart
            # from "sent from offline queue" in its own UI if it wants to
            # (e.g. a small "sent while you were offline" indicator).
            "delivered_from_offline_queue": True,
        }
    )

    for uid in other_participant_ids:
        async_to_sync(channel_layer.group_send)(
            f"user_{uid}",
            {
                "type": "inbox_update",
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "sender_id": str(message.sender_id),
                "sender_name": sender["display_name"],
                "last_message_text": message.text,
                "last_message_type": message.type,
                "created_at": message.created_at.isoformat(),
            }
        )

    muted_user_ids = set(
        ConversationParticipant.objects.filter(
            conversation=conversation, user_id__in=other_participant_ids, is_muted=True,
        ).values_list("user_id", flat=True)
    )
    mentioned_set = set(mentioned_ids)
    push_recipients = [
        uid for uid in other_participant_ids if uid not in muted_user_ids and uid not in mentioned_set
    ]

    if push_recipients:
        send_chat_message_push(
            recipient_ids=push_recipients,
            sender_name=sender["display_name"],
            message_text=message.text,
            message_type=message.type,
            conversation_id=conversation.id,
            message_id=message.id,
        )

    if mentioned_ids:
        send_mention_push(
            recipient_ids=mentioned_ids,
            sender_name=sender["display_name"],
            message_text=message.text,
            conversation_id=conversation.id,
            message_id=message.id,
        )


# ---------------------------------------------------------------------------
# FRONTEND CONTRACT (Flutter side — not implemented here, no frontend code
# was available to read this session):
#
# 1. When a send fails due to no connectivity, store the message locally
#    (local DB/Hive/whatever the app already uses) with a locally-
#    generated `client_id` (UUID) and the composed content, marked
#    "pending".
# 2. On reconnect (connectivity listener), POST the full pending queue,
#    in composed order, to the new endpoint below.
# 3. For each item in the response: "created"/"duplicate" -> remove from
#    local queue, render as sent (using the returned `message_id`);
#    "error" -> keep queued, retry on the next reconnect (or surface to
#    the user after N failed attempts — client's call).
# 4. IMPORTANT: reuse the SAME `client_id` on every retry of the same
#    logical message — that's what makes step 3's dedup work. A fresh
#    `client_id` per retry defeats the entire idempotency guarantee this
#    module provides.
# ---------------------------------------------------------------------------