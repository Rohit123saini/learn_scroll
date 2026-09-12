# core/notification_batching.py
"""
core/notification_batching.py

create_batched_notification() — burst events (5 likes on the same post
within a few seconds) ko ek notification me collapse karta hai, taaki
recipient ko "New like" x5 push na milein, sirf ek "5 people liked your
post" mile.

⚠️ Fix note: is file me pehle real implementation ki jagah galti se
`test_notification_batching.py` ka ek duplicate/earlier draft (ek
Django TestCase class, khud apne hi module se `create_batched_notification`
import karte hue) save ho gaya tha — jo module-load pe ImportError degata,
kyunki us function ki definition kahin thi hi nahi. Us test-class content
`test_notification_batching.py` me already properly maujood hai (real
file), isliye yahan sirf real implementation rehti hai.

Design — sliding window, Redis/Django-cache-based:
- Cache key = (recipient_id, notif_type, target_id) — har target apna
  alag batch rakhta hai.
- Pehla event: real `Notification` row banta hai, push turant bhejta hai
  (`send_push_fn`, agar diya ho), aur cache window khulta hai
  (`window_seconds`, default 120s) jisme notification id + actor-id set
  store hota hai.
- Baad ke events (usi window me): actor set me add hota hai (repeat
  actor se count nahi badhta), SAME Notification row update hoti hai
  (naya row nahi, naya push nahi).
- Sliding window: har naya event window ko `window_seconds` se ABHI SE
  extend karta hai (fixed window se nahi) — isliye steady trickle of
  events ek hi notification me batch hote rehte hain jab tak activity
  na ruke.
- Agar cache batch ki taraf point karta hai lekin wo row DB me nahi milti
  (user ne beech me clear kar diya), gracefully fresh batch start ho
  jaata hai — DoesNotExist kabhi raise nahi hoti.

✅ Resolved (was an open item): `message/push_utils.py` ab mil gaya hai
aur check kar liya — uska "debounce" (`_DIGEST_COUNT_KEY`, `cache.add`/
`incr`) sirf ek plain per-(user, conversation) COUNTER hai jo push-TRAY
ki copy decide karta hai ("1 message" vs "X sent N messages"); wo kabhi
kisi `Notification` row ko merge/update nahi karta — har chat message ki
apni alag bell-row banti rehti hai (`create_notification()` se, har
baar). Ye module fundamentally alag kaam karta hai: same *Notification*
row ko update karta hai jab tak actor-set badhta rahe (5 logo ne ek post
like kiya = 1 row, "5 people liked" ban ke). Dono mechanisms ko merge
karna galat hota — chat ko per-message row chahiye (unread count,
message-level tap-through), burst-events (likes/reactions) ko
collapsed-row chahiye. Isliye ye do jaanboojh kar ALAG implementations
hain, "parallel duplicate" nahi — merge nahi karna.
"""
import logging

from django.core.cache import cache

from .models import Notification

logger = logging.getLogger(__name__)


def _batch_cache_key(recipient_id, notif_type: str, target_id) -> str:
    return f"core:notif_batch:{recipient_id}:{notif_type}:{target_id}"


def _hydrate_actors(actor_ids):
    """
    🔧 FIX — pehle `actors` list ka TYPE call-to-call inconsistent tha:
    fresh-batch branch me `actors = [actor]` (jo bhi caller ne diya —
    User instance YA raw id, docstring dono allow karta hai), lekin
    folded-batch branch me `actors` HAMESHA hydrated `User` instances ki
    list thi (`User.objects.filter(id__in=actor_ids)`). Koi bhi `title_fn`
    jo `actor.display_name`/`actor.username` jaisi attribute access karta
    (jaisa "X and 4 others liked your post" banane ke liye zaroori hai)
    pehle event pe silently crash ya galat output deta agar caller ne
    raw id pass kiya tha. Ab dono branches isi ek helper se guzarte hain —
    `actors` HAMESHA hydrated `User` list hoti hai, har call pe, chahe
    pehla event ho ya 50wa. Local import (module-level nahi) — login/core
    ke beech circular-import se bachne ke liye, jaisa pehle bhi tha.
    """
    from login.models import User

    return list(User.objects.filter(id__in=actor_ids))


def create_batched_notification(
    *,
    recipient,
    notif_type: str,
    actor,
    target_id,
    title_fn,
    message_fn=None,
    classroom=None,
    session=None,
    extra_data: dict | None = None,
    window_seconds: int = 120,
    send_push_fn=None,
):
    """
    recipient / actor: User instance ya raw id, dono chalte hain (jaisa
    services.create_notification() karta hai).
    target_id: HAMESHA zaroori hai — single-target types (jaise "follow")
    ke liye bhi recipient ka apna id pass karo, taaki per-recipient ek
    alag batch bane.
    title_fn(actor_count, actors) -> str — har call pe chalta hai.
    message_fn(actor_count, actors, latest_actor) -> str — optional, sirf
    diya ho to chalta hai; na diya ho to message pehle event ke baad se
    nahi badalta.
    send_push_fn(recipient, title, message, data) -> None — SIRF pehle
    event pe call hota hai.

    Deliberately decoupled kisi specific push function se — caller
    (liveclass/message/future Posts app) apna send_push_fn pass karta hai.
    """
    recipient_id = getattr(recipient, "id", recipient)
    actor_id = getattr(actor, "id", actor)
    key = _batch_cache_key(recipient_id, notif_type, target_id)

    batch = cache.get(key)
    notification = None
    if batch is not None:
        # Cache says there's an open batch — but the row it points at may
        # have been deleted mid-window (user cleared their notifications).
        notification = Notification.objects.filter(pk=batch.get("notification_id")).first()

    if notification is None:
        # Fresh batch: no cache entry, expired window, or a dangling
        # cache entry whose row no longer exists.
        actor_ids = {actor_id}
        # 🔧 FIX — hydrate here too (see `_hydrate_actors` docstring above)
        # so `title_fn`/`message_fn` get the same `User`-instance type on
        # the first event as on every folded event after it.
        actors = _hydrate_actors(actor_ids)
        latest_actor = next((a for a in actors if a.id == actor_id), actor)
        title = title_fn(len(actor_ids), actors)
        message = message_fn(len(actor_ids), actors, latest_actor) if message_fn else ""

        notification = Notification.objects.create(
            recipient_id=recipient_id,
            notif_type=notif_type,
            title=title,
            message=message,
            classroom=classroom,
            session=session,
            data=extra_data or {},
        )

        cache.set(
            key,
            {"notification_id": notification.id, "actor_ids": list(actor_ids)},
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

        return notification

    # Same window, existing row — fold this event into it. Repeat actors
    # don't inflate the count (it's a set).
    actor_ids = set(batch.get("actor_ids") or [])
    actor_ids.add(actor_id)

    actors = _hydrate_actors(actor_ids)
    # 🔧 FIX — 3rd arg to `message_fn` is documented as "latest_actor";
    # it was being passed the raw `actor` param verbatim (could be a raw
    # id per the docstring's own contract), while `actors` right next to
    # it was always hydrated `User` instances — same inconsistency as the
    # fresh-batch branch above, fixed the same way.
    latest_actor = next((a for a in actors if a.id == actor_id), actor)
    title = title_fn(len(actor_ids), actors)

    update_fields = ["title"]
    notification.title = title
    if message_fn is not None:
        notification.message = message_fn(len(actor_ids), actors, latest_actor)
        update_fields.append("message")
    notification.save(update_fields=update_fields)

    # Sliding window — extend from now, not from the original open time,
    # so a steady trickle of events stays batched until activity dies down.
    cache.set(
        key,
        {"notification_id": notification.id, "actor_ids": list(actor_ids)},
        timeout=window_seconds,
    )

    return notification