# message/management/commands/cleanup_expired_messages.py
#
# [TASK 40 — reconciled against message/tasks.py]
#
# Confirmed: `message.cleanup_expired_messages` (message/tasks.py) IS
# already registered in `settings.CELERY_BEAT_SCHEDULE` as
# "message-cleanup-expired-messages", running every 15 minutes. This
# command is NOT separately cron-scheduled anywhere in the project (no
# crontab entry, no other `call_command("cleanup_expired_messages", ...)`
# call site) — so the two are NOT a redundant double-sweep in
# production. This command is a manual/ad-hoc entry point: an operator
# running a one-off catch-up sweep, or inspecting the pending count
# with `--dry-run`, from a shell.
#
# It previously had its own, second copy of the delete loop, which had
# drifted from the Celery task in two ways:
#   - batch size: 1000 here vs 500 in the task.
#   - queryset: `Message.objects` (default manager) here vs
#     `Message.all_objects` (includes soft-deleted rows) in the task —
#     a real bug, not just a cosmetic difference: any message that was
#     soft-deleted AND expired would never be hard-deleted by this
#     command, silently.
#
# Fixed by deleting the duplicate loop entirely and calling
# `message.tasks.hard_delete_expired_messages()` — the same function
# the Celery task itself now calls — so there is exactly one
# implementation and the two entry points can never again disagree on
# batch size or which rows count as "expired".
#
# USAGE:
#     python manage.py cleanup_expired_messages
#     python manage.py cleanup_expired_messages --batch-size 500 --dry-run

import logging

from django.core.management.base import BaseCommand

from message.tasks import hard_delete_expired_messages

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Disappearing-messages ki expiry nikal chuki Message rows ko "
        "hard-delete karta hai. Manual/ad-hoc entry point — Celery beat "
        "'message-cleanup-expired-messages' already runs this sweep every "
        "15 min in production; use this for a one-off catch-up run or "
        "--dry-run inspection."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size', type=int, default=500,
            help="Ek iteration me max kitne messages delete karne hain "
                 "(default: 500 — Celery task ke sweep se aligned).",
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Kuch delete mat karo, sirf count batao.",
        )

    def handle(self, *args, **options):
        result = hard_delete_expired_messages(
            batch_size=options['batch_size'],
            dry_run=options['dry_run'],
        )

        if result["dry_run"]:
            self.stdout.write(
                f"{result['would_delete']} expired message(s) milein — "
                "dry-run hai, delete nahi kiya."
            )
            return

        deleted_total = result["deleted"]
        if deleted_total == 0:
            self.stdout.write(self.style.SUCCESS("Koi expired message nahi mila."))
            return

        logger.info("cleanup_expired_messages (management command): deleted %s expired message(s)", deleted_total)
        self.stdout.write(self.style.SUCCESS(f"Total {deleted_total} expired message(s) delete ho gaye."))