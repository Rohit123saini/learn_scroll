# message/management/commands/cleanup_expired_messages.py
#
# 🔥 NAYA — models.py aur views.py ke comments me is command ka zikr tha
# ("hard delete cleanup ke liye alag se `cleanup_expired_messages` command
# chalta hai") lekin ye file uploaded files me kahin nahi thi — disappearing
# messages ka sirf "half" implement tha: expiry snapshot save ho raha tha
# aur expired messages GET response se hide ho rahe the, par unhe DB se
# hata kar actually "disappear" karne wala hissa missing tha. Ye command
# wahi kaam poora karta hai.
#
# USAGE (cron ya Celery beat se schedule karo, e.g. har 15 min):
#     python manage.py cleanup_expired_messages
#     python manage.py cleanup_expired_messages --batch-size 500 --dry-run
#
# Batches me delete karta hai taaki ek call me lakhon rows pe DB lock na
# lage (WhatsApp-scale chat app me ye table sabse bada hota hai).

import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from message.models import Message

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Disappearing-messages ki expiry nikal chuki Message rows ko hard-delete karta hai."

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size', type=int, default=1000,
            help="Ek iteration me max kitne messages delete karne hain (default: 1000).",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Kuch delete mat karo, sirf count batao.",
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        dry_run = options['dry_run']
        now = timezone.now()

        base_qs = Message.objects.filter(expires_at__isnull=False, expires_at__lte=now)
        total = base_qs.count()

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Koi expired message nahi mila."))
            return

        if dry_run:
            self.stdout.write(f"{total} expired message(s) milein — dry-run hai, delete nahi kiya.")
            return

        deleted_total = 0
        # `.delete()` sabhi matching rows ek saath uthata hai isliye chunk
        # karne ke liye id-batch nikal ke alag-alag delete calls karte hain.
        while True:
            ids = list(base_qs.values_list('id', flat=True)[:batch_size])
            if not ids:
                break
            with transaction.atomic():
                deleted_count, _ = Message.objects.filter(id__in=ids).delete()
            deleted_total += len(ids)
            self.stdout.write(f"Deleted batch of {len(ids)} message(s)...")

        logger.info("cleanup_expired_messages: deleted %s expired message(s)", deleted_total)
        self.stdout.write(self.style.SUCCESS(f"Total {deleted_total} expired message(s) delete ho gaye."))