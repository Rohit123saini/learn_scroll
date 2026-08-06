# chat/management/commands/cleanup_expired_messages.py
#
# 🔥 NAYA — Temporary chat (disappearing messages) ka actual cleanup.
# `expires_at` set karne se message list se chhup to jaata hai (views.py
# me query filter hai), lekin DB me row/media pada rehta hai. Ye command
# expire ho chuke messages ko hard-delete karta hai (media ke saath, kyunki
# `Message` delete hote hi `Presentation`/`GroupMedia`/`MessageStatus`/
# `MessageReaction` sab CASCADE se apne aap delete ho jaate hain).
#
# Chalane ka tareeka (cron ya celery beat se, roz ek baar kaafi hai):
#   python manage.py cleanup_expired_messages
#
# Celery beat schedule example (settings.py me):
#   CELERY_BEAT_SCHEDULE = {
#       "cleanup-expired-messages": {
#           "task": "chat.tasks.cleanup_expired_messages_task",
#           "schedule": crontab(hour=3, minute=0),  # roz raat 3 baje
#       },
#   }

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from chat.models import Conversation, Message


class Command(BaseCommand):
    help = "Disappearing-messages jinki expiry nikal chuki hai unhe hard-delete karta hai."

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help="Ek baar me kitne messages delete karne hain (default 500) — bada dataset pe DB lock lambe time ke liye na pakde isliye chunks me delete karte hain.",
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        now = timezone.now()
        total_deleted = 0
        touched_conversations = set()

        while True:
            expired_ids = list(
                Message.objects.filter(expires_at__lte=now)
                .values_list('id', flat=True)[:batch_size]
            )
            if not expired_ids:
                break

            with transaction.atomic():
                touched_conversations.update(
                    Message.objects.filter(id__in=expired_ids)
                    .values_list('conversation_id', flat=True).distinct()
                )
                deleted_count, _ = Message.objects.filter(id__in=expired_ids).delete()

            total_deleted += deleted_count

        # 🔥 Jin conversations me delete hua wahan `last_message_*` denormalized
        # fields ko sabse latest bache hue message se refresh kar do, warna
        # chat list me delete ho chuka message hi "last message" dikhta rahega.
        for conversation in Conversation.objects.filter(id__in=touched_conversations):
            latest = conversation.all_messages.order_by('-created_at').first()
            conversation.last_message_text = (latest.text or '')[:500] if latest else ''
            conversation.last_message_at = latest.created_at if latest else None
            conversation.last_message_sender = latest.sender if latest else None
            conversation.last_message_type = latest.type if latest else None
            conversation.save(update_fields=[
                'last_message_text', 'last_message_at', 'last_message_sender', 'last_message_type',
            ])

        self.stdout.write(self.style.SUCCESS(
            f"{total_deleted} expired message(s) delete ho gaye ({len(touched_conversations)} conversation(s) me)."
        ))