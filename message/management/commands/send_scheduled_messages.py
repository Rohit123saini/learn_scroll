# message/management/commands/send_scheduled_messages.py
"""
🔥 NAYA (ADVANCED FEATURE) — "Send Later" ka delivery job.

Har `is_scheduled=True` Message dhoondta hai jiska `scheduled_for` time aa
chuka hai, aur `scheduled_messages.finalize_scheduled_message()` ke through
usko "actually send" karta hai (broadcast + push + unread-count, sab kuch).

Run karo periodically — production me cron ya Celery Beat se, e.g. har 1
minute:

    * * * * * cd /path/to/project && python manage.py send_scheduled_messages

Ek message process karte waqt agar exception aaye (channel-layer down,
group deleted ho chuka, etc.) to sirf wahi message skip hoti hai aur log
hoti hai — baaki saari due messages usi run me deliver hoti rehti hain.
"""
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from message.models import Message
from message.scheduled_messages import finalize_scheduled_message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Deliver every due 'send later' (scheduled) message."

    def handle(self, *args, **options):
        due_messages = Message.objects.filter(
            is_scheduled=True,
            scheduled_for__lte=timezone.now(),
        ).select_related('conversation', 'conversation__group_detail', 'sender')

        sent_count = 0
        failed_count = 0

        for message in due_messages:
            try:
                finalize_scheduled_message(message)
                sent_count += 1
            except Exception:
                failed_count += 1
                logger.exception(
                    "Scheduled message %s deliver karte waqt fail hua.", message.id
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{sent_count} scheduled message(s) bheji gayi, {failed_count} fail hui."
            )
        )