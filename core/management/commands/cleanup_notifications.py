"""Retention for the shared Notification table (run daily from cron / Celery beat).

    python manage.py cleanup_notifications              # real run
    python manage.py cleanup_notifications --dry-run    # only count

Why: the bell list keeps every row forever otherwise, so the table (and the
unread/feed indexes) only ever grow. Defaults, overridable in settings.py:

    NOTIFICATION_READ_RETENTION_DAYS   = 30   # read rows older than this go
    NOTIFICATION_UNREAD_RETENTION_DAYS = 90   # anything older than this goes

Deletes in bounded batches so one run never holds a huge transaction/lock.
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Notification

BATCH = 2000


def _delete_in_batches(qs) -> int:
    total = 0
    while True:
        ids = list(qs.values_list("pk", flat=True)[:BATCH])
        if not ids:
            return total
        deleted, _ = Notification.objects.filter(pk__in=ids).delete()
        total += deleted


class Command(BaseCommand):
    help = "Delete old read / very old notifications (retention policy)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only report how many rows would be deleted.")
        parser.add_argument("--read-days", type=int, default=None)
        parser.add_argument("--max-days", type=int, default=None)

    def handle(self, *args, **opts):
        read_days = opts["read_days"] or int(getattr(settings, "NOTIFICATION_READ_RETENTION_DAYS", 30))
        max_days = opts["max_days"] or int(getattr(settings, "NOTIFICATION_UNREAD_RETENTION_DAYS", 90))
        now = timezone.now()

        old_read = Notification.objects.filter(is_read=True, created_at__lt=now - timedelta(days=read_days))
        too_old = Notification.objects.filter(created_at__lt=now - timedelta(days=max_days))

        if opts["dry_run"]:
            self.stdout.write(f"[dry-run] read>{read_days}d: {old_read.count()} rows, any>{max_days}d: {too_old.count()} rows")
            return

        n1 = _delete_in_batches(old_read)
        n2 = _delete_in_batches(too_old)
        self.stdout.write(self.style.SUCCESS(f"Deleted {n1} read (> {read_days}d) + {n2} old (> {max_days}d) notifications."))
