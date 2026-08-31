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

from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


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