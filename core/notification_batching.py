# core/notification_batching.py
"""
create_batched_notification() — burst events (5 likes on the same post
within a few seconds) ko ek notification row me collapse karta hai:
"X and 4 others liked your post", sirf ek push (pehle event pe).

Design — sliding window, Django-cache based:
- Cache key = (recipient_id, notif_type, target_id).
- Pehla event: real `Notification` row banta hai, push turant (send_push_fn),
  cache window khulta hai (`window_seconds`).
- Baad ke events (usi window me): actor list me append, SAME row update
  (title, message, data), naya row/push nahi. Repeat actor count nahi badhata.
- Sliding window: har event window ko abhi se extend karta hai.
  `max_age_seconds` (optional) batch ki total umar cap karta hai — lambe
  windows (follows, 6h) pe bina cap ke viral account ka ek row/push KABHI
  refresh nahi hota.
- Cache dangling ho (row delete ho gayi) to fresh batch — DoesNotExist
  kabhi raise nahi hoti.
- Public functions kabhi raise nahi karte (core.services.create_notification
  jaisa contract): notification fail hone se caller ka @atomic view /
  transaction fail nahi hona chahiye. Error log hota hai, None return.

Per-type window: settings.NOTIFICATION_BATCH_WINDOWS (dict), `get_batch_window()`.

⚠️ YE CHEEZEIN BATCH NAHI HOTIN (jaanboojh kar):
- Follow REQUEST (FOLLOW_REQUEST_RECEIVED) — har request ka apna
  Confirm/Delete action hai (follow_id pe), ek merged row me ye action
  khatam ho jaata.
- Mentions (STORY_MENTION / comment mentions) — tap karne pe ek specific
  story/comment khulna chahiye; merge karne se tap-through toot jaata hai.
- Chat messages — per-message row chahiye (unread count, message-level
  tap-through). message/push_utils.py ka debounce sirf push-tray COPY ka
  counter hai, Notification row ko kabhi merge nahi karta. Do alag cheezein.
Batch sirf unke liye hai jinme "kitne logon ne" hi poori information hai
(likes, comments-on-post, new followers, story reactions).
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from .models import Notification, NotificationMute

logger = logging.getLogger(__name__)

PREVIEW_LIMIT = 3

DEFAULT_BATCH_WINDOWS = {
    "post_liked": 120,
    "post_commented": 120,
    "new_follower": 6 * 3600,
    "story_reaction": 300,
    "post_reposted": 120,
}


def get_batch_window(name: str, default: int = 120) -> int:
    """Per-type window (seconds) from settings.NOTIFICATION_BATCH_WINDOWS."""
    windows = getattr(settings, "NOTIFICATION_BATCH_WINDOWS", None) or {}
    return int(windows.get(name, DEFAULT_BATCH_WINDOWS.get(name, default)))


def get_batch_max_age(name: str):
    """Optional hard cap on a batch's total age (seconds), or None."""
    caps = getattr(settings, "NOTIFICATION_BATCH_MAX_AGE", None) or {}
    value = caps.get(name)
    return int(value) if value else None


def _batch_cache_key(recipient_id, notif_type: str, target_id) -> str:
    return f"core:notif_batch:{recipient_id}:{notif_type}:{target_id}"


def _norm_id(value):
    """Ids may arrive as int or numeric str; keep one canonical form."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _display_name(user) -> str:
    getter = getattr(user, "get_full_name", None)
    full = getter() if callable(getter) else ""
    return full or getattr(user, "username", None) or "Someone"


def batch_title(actors, count: int, tail: str, max_names: int = 1) -> str:
    """'X liked your post' / 'X and 4 others liked your post' /
    'X, Y and 12 others started following you'. `actors` = arrival order,
    so the most recent actors are named first."""
    names = [_display_name(a) for a in reversed(list(actors))][:max_names]
    if not names:
        return f"Someone {tail}"
    remaining = max(count - len(names), 0)
    if remaining == 0:
        return f"{' and '.join([', '.join(names[:-1]), names[-1]] if len(names) > 1 else names)} {tail}"
    noun = "other" if remaining == 1 else "others"
    return f"{', '.join(names)} and {remaining} {noun} {tail}"


def _hydrate_actors(actor_ids):
    """Hydrated `User` list in the SAME order as `actor_ids` (deleted users
    are skipped). Always User instances, on every branch."""
    from login.models import User

    ids = list(actor_ids)
    by_id = {u.id: u for u in User.objects.filter(id__in=ids)}
    return [by_id[i] for i in ids if i in by_id]


def _default_preview(actor) -> dict:
    return {"id": actor.id, "username": getattr(actor, "username", None)}


def _batch_data(actor_ids, actors, actor_preview_fn=None) -> dict:
    preview_fn = actor_preview_fn or _default_preview
    data = {
        "actor_ids": list(actor_ids),
        "actor_count": len(actor_ids),
        # first PREVIEW_LIMIT actors (arrival order)
        "actors_preview": [preview_fn(a) for a in actors[:PREVIEW_LIMIT]],
    }
    if actors:
        # Same keys core.create_notification folds in for single-actor rows
        # (TASK 6) — bell-row tap-through uses the LATEST actor.
        data["actor_id"] = actors[-1].id
        data["actor_username"] = getattr(actors[-1], "username", None)
    return data


def _is_restricted(recipient_id, actor_id) -> bool:
    if actor_id is None:
        return False
    # Lazy import — core must not hard-depend on user_profile at import time.
    from user_profile.views import is_restricted_between

    return bool(is_restricted_between(recipient_id, actor_id))


def _apply_update(notification, current, actor_ids, actor_id, *, title_fn,
                  message_fn, data_fn, actor_preview_fn, bump=False):
    actors = _hydrate_actors(actor_ids)
    latest_actor = actors[-1] if actors else actor_id
    count = len(actor_ids)

    notification.title = title_fn(count, actors)
    update_fields = ["title", "data"]
    if bump:
        # A NEW event folded into an existing row must look like a new
        # notification: unread again (badge) and back at the top of the list.
        # Without this a row the user already opened stays "read" and sinks
        # down, so "X started following you" never shows up on the bell.
        from django.utils import timezone

        notification.is_read = False
        notification.read_at = None
        notification.created_at = timezone.now()
        update_fields += ["is_read", "read_at", "created_at"]
    if message_fn is not None:
        notification.message = message_fn(count, actors, latest_actor)
        update_fields.append("message")

    data = {**current, **_batch_data(actor_ids, actors, actor_preview_fn)}
    if data_fn is not None:
        data.update(data_fn(count, actors, latest_actor) or {})
    notification.data = data
    notification.save(update_fields=update_fields)


def create_batched_notification(
    *,
    recipient,
    notif_type: str,
    actor,
    target_id,
    title_fn,
    message_fn=None,
    data_fn=None,
    classroom=None,
    session=None,
    extra_data: dict | None = None,
    actor_preview_fn=None,
    window_seconds: int = 120,
    max_age_seconds: int | None = None,
    send_push_fn=None,
    send_push_row_fn=None,
):
    """
    recipient / actor: User instance ya raw id.
    target_id: HAMESHA zaroori (post_id for likes, recipient id for follows).
    title_fn(actor_count, actors) -> str — har call pe chalta hai. `actors`
        arrival order me hydrated Users; latest actor = actors[-1].
    message_fn(actor_count, actors, latest_actor) -> str — optional.
    data_fn(actor_count, actors, latest_actor) -> dict — optional, har event
        pe `data` me merge hota hai (jaise latest comment_id).
    extra_data: sirf fresh row pe `data` me jaata hai (aur push payload).
    actor_preview_fn(user) -> dict — `actors_preview` ka ek item (photo yahin).
    send_push_fn(recipient, title, message, data) — SIRF pehle event pe.
    send_push_row_fn(notification) — SIRF pehle event pe, saved row ke saath
        (message.push_utils.send_push_for_notification: prefs / quiet hours
        respect + rich payload + action buttons).

    Returns the Notification, or None (restricted actor, or internal failure
    — logged, never raised).
    """
    try:
        return _create_batched(
            recipient=recipient, notif_type=notif_type, actor=actor,
            target_id=target_id, title_fn=title_fn, message_fn=message_fn,
            data_fn=data_fn, classroom=classroom, session=session,
            extra_data=extra_data, actor_preview_fn=actor_preview_fn,
            window_seconds=window_seconds, max_age_seconds=max_age_seconds,
            send_push_fn=send_push_fn, send_push_row_fn=send_push_row_fn,
        )
    except Exception:
        logger.exception(
            "Failed to create batched notification (%s) for user %s",
            notif_type, getattr(recipient, "id", recipient),
        )
        return None


def _create_batched(
    *, recipient, notif_type, actor, target_id, title_fn, message_fn, data_fn,
    classroom, session, extra_data, actor_preview_fn, window_seconds,
    max_age_seconds, send_push_fn, send_push_row_fn=None,
):
    recipient_id = getattr(recipient, "id", recipient)
    actor_id = _norm_id(getattr(actor, "id", actor))

    if _is_restricted(recipient_id, actor_id):
        return None

    # Block (either direction): no row, no fold into an open batch.
    if actor_id is not None:
        from user_profile.services import is_blocked_pair

        if is_blocked_pair(recipient_id, actor_id):
            return None

    # N6-BE — recipient muted this actor: no row, no fold into an open batch.
    if NotificationMute.is_muted(recipient_id, actor_id):
        return None

    key = _batch_cache_key(recipient_id, notif_type, target_id)

    batch = cache.get(key)
    if batch is not None:
        too_old = (
            max_age_seconds
            and time.time() - batch.get("opened_at", 0) > max_age_seconds
        )
        if not too_old:
            folded = _fold_into_open_batch(
                key=key, batch=batch, actor_id=actor_id, title_fn=title_fn,
                message_fn=message_fn, data_fn=data_fn,
                actor_preview_fn=actor_preview_fn, window_seconds=window_seconds,
            )
            if folded is not None:
                return folded
        # expired by max_age, or dangling cache entry -> fresh batch

    # ---- Fresh batch ----
    actor_ids = [actor_id]
    actors = _hydrate_actors(actor_ids)
    latest_actor = actors[-1] if actors else actor
    title = title_fn(len(actor_ids), actors)
    message = message_fn(len(actor_ids), actors, latest_actor) if message_fn else ""

    data = {**(extra_data or {}), **_batch_data(actor_ids, actors, actor_preview_fn)}
    if data_fn is not None:
        data.update(data_fn(len(actor_ids), actors, latest_actor) or {})

    # Own savepoint: callers are @transaction.atomic views; a DB error here
    # must not leave THEIR transaction aborted on PostgreSQL.
    with transaction.atomic():
        notification = Notification.objects.create(
            recipient_id=recipient_id,
            notif_type=notif_type,
            title=title,
            message=message,
            classroom=classroom,
            session=session,
            data=data,
        )

    cache.set(
        key,
        {"notification_id": notification.id, "actor_ids": actor_ids,
         "opened_at": time.time()},
        timeout=window_seconds,
    )

    if send_push_fn is not None:
        try:
            send_push_fn(recipient, title, message, extra_data or {})
        except Exception:
            logger.exception(
                "send_push_fn failed for batched notification %s (recipient=%s).",
                notification.id, recipient_id,
            )

    if send_push_row_fn is not None:
        try:
            send_push_row_fn(notification)
        except Exception:
            logger.exception(
                "send_push_row_fn failed for batched notification %s (recipient=%s).",
                notification.id, recipient_id,
            )

    return notification


def _fold_into_open_batch(
    *, key, batch, actor_id, title_fn, message_fn, data_fn, actor_preview_fn,
    window_seconds,
):
    """Fold one more actor into the open batch row. Returns the row, or None
    if the row no longer exists (caller then starts a fresh batch)."""
    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update()
            .filter(pk=batch.get("notification_id"))
            .first()
        )
        if notification is None:
            cache.delete(key)
            return None

        current = notification.data or {}
        actor_ids = [
            i for i in (current.get("actor_ids") or batch.get("actor_ids") or [])
            if i != actor_id
        ]
        actor_ids.append(actor_id)  # repeat actor: count same, moves to latest

        _apply_update(
            notification, current, actor_ids, actor_id, title_fn=title_fn,
            message_fn=message_fn, data_fn=data_fn,
            actor_preview_fn=actor_preview_fn, bump=True,
        )

    # Sliding window — extend from now (opened_at stays for max_age).
    cache.set(
        key,
        {"notification_id": notification.id, "actor_ids": actor_ids,
         "opened_at": batch.get("opened_at", time.time())},
        timeout=window_seconds,
    )
    return notification


def remove_actor_from_batch(
    *,
    recipient,
    notif_type: str,
    actor,
    target_id,
    title_fn,
    message_fn=None,
    data_fn=None,
    actor_preview_fn=None,
    window_seconds: int = 120,
):
    """Unlike/unfollow path: actor ko KHULE batch se hata do. Window band ho
    chuki ho (cache miss) to kuch nahi karta — settled row ko chhedna nahi.
    Actor akela tha to row delete. Kabhi raise nahi karta."""
    try:
        return _remove_actor(
            recipient=recipient, notif_type=notif_type, actor=actor,
            target_id=target_id, title_fn=title_fn, message_fn=message_fn,
            data_fn=data_fn, actor_preview_fn=actor_preview_fn,
            window_seconds=window_seconds,
        )
    except Exception:
        logger.exception(
            "Failed to remove actor from batched notification (%s) for user %s",
            notif_type, getattr(recipient, "id", recipient),
        )
        return None


def _remove_actor(
    *, recipient, notif_type, actor, target_id, title_fn, message_fn, data_fn,
    actor_preview_fn, window_seconds,
):
    recipient_id = getattr(recipient, "id", recipient)
    actor_id = _norm_id(getattr(actor, "id", actor))
    key = _batch_cache_key(recipient_id, notif_type, target_id)

    batch = cache.get(key)
    if batch is None:
        return None

    with transaction.atomic():
        notification = (
            Notification.objects.select_for_update()
            .filter(pk=batch.get("notification_id"))
            .first()
        )
        if notification is None:
            cache.delete(key)
            return None

        current = notification.data or {}
        old_ids = list(current.get("actor_ids") or batch.get("actor_ids") or [])
        actor_ids = [i for i in old_ids if i != actor_id]
        if len(actor_ids) == len(old_ids):
            return notification  # actor wasn't in this batch

        if not actor_ids:
            notification.delete()
            cache.delete(key)
            return None

        _apply_update(
            notification, current, actor_ids, actor_id, title_fn=title_fn,
            message_fn=message_fn, data_fn=data_fn,
            actor_preview_fn=actor_preview_fn,
        )

    cache.set(
        key,
        {"notification_id": notification.id, "actor_ids": actor_ids,
         "opened_at": batch.get("opened_at", time.time())},
        timeout=window_seconds,
    )
    return notification