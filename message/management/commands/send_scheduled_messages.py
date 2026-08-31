# message/management/commands/send_scheduled_messages.py
"""
🔥 UPDATED (this session) — "Send Later" ka delivery job.

⚠️ NOTE ON WHICH PATH IS CANONICAL: `tasks.py`'s Celery task
`message.send_scheduled_messages` is the confirmed production path — it's
registered in `settings.CELERY_BEAT_SCHEDULE` to run every minute (see
CHAT_APP_DOCUMENTATION.md §9.1 item 3 / §9.4 item 4). This management
command is a **manual/backup trigger** (e.g. for a one-off "flush the queue
right now" ops need, or as a fallback if Celery/Beat is ever down) — it is
NOT meant to also be cron-scheduled alongside Celery Beat in normal
operation. Running both on a schedule at the same time is redundant and,
before this fix, was also a double-send risk (see below).

🔥 FIX (this session) — this command used to iterate `Message.objects.
filter(is_scheduled=True, scheduled_for__lte=now)` with NO row locking,
unlike `tasks.send_scheduled_messages` which wraps the same query in
`select_for_update(skip_locked=True)`. If this command were ever run at
the same moment as the Celery beat task (e.g. someone *also* cron'd it
"just in case"), both could pick up the same due message and call
`finalize_scheduled_message()` on it twice — sending it to recipients
twice, double-incrementing unread counts, double push notifications, etc.
This command now uses the identical locking pattern as the Celery task, so
the two are safe to run concurrently (whichever gets the row lock first
wins; the other skips it via `skip_locked=True`).

Har `is_scheduled=True` Message dhoondta hai jiska `scheduled_for` time aa
chuka hai, aur `scheduled_messages.finalize_scheduled_message()` ke through
usko "actually send" karta hai (broadcast + push + unread-count, sab kuch).

Manual run:
    python manage.py send_scheduled_messages

Ek message process karte waqt agar exception aaye (channel-layer down,
group deleted ho chuka, etc.) to sirf wahi message skip hoti hai aur log
hoti hai — baaki saari due messages usi run me deliver hoti rehti hain.
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from message.models import Message
from message.scheduled_messages import finalize_scheduled_message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Deliver every due 'send later' (scheduled) message. Manual/backup "
        "trigger only — the Celery beat task `message.send_scheduled_messages` "
        "is the canonical every-minute production path (see settings.py "
        "CELERY_BEAT_SCHEDULE)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size', type=int, default=200,
            help="Ek run me max kitne due messages process karne hain (default: 200, "
                 "same bound as the Celery task, to keep a single run/lock-hold short).",
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        now = timezone.now()

        # 🔥 FIX — `select_for_update(skip_locked=True)`, same as
        # `tasks.send_scheduled_messages`: if a row is already locked by a
        # concurrent run (this command or the Celery task), skip it instead
        # of blocking or double-processing it. Bounded slice keeps the lock
        # window short even on a large backlog.
        with transaction.atomic():
            due_ids = list(
                Message.objects.select_for_update(skip_locked=True)
                .filter(is_scheduled=True, scheduled_for__lte=now)
                .order_by('scheduled_for')
                .values_list('id', flat=True)[:batch_size]
            )

        sent_count = 0
        failed_count = 0

        for message_id in due_ids:
            try:
                message = Message.objects.select_related(
                    'conversation', 'conversation__group_detail', 'sender',
                ).get(id=message_id)
                finalize_scheduled_message(message)
                sent_count += 1
            except Exception:
                failed_count += 1
                logger.exception(
                    "Scheduled message %s deliver karte waqt fail hua.", message_id
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{sent_count} scheduled message(s) bheji gayi, {failed_count} fail hui."
            )
        )