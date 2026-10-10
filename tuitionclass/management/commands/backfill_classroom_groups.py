"""T3 — create the chat group for OLD classrooms that never got one.

Before T3 `chat_group_enabled` defaulted to False and was only ever flipped by
the teacher's explicit create_group call, so "False + no linked group" never
meant a deliberate opt-out — those rows are switched on here. Teachers who
disable the group AFTER T3 (the group is archived, `linked_conversation_id`
kept) are left alone. Idempotent: classrooms that already have a group are
skipped, re-running does nothing.

    python manage.py backfill_classroom_groups --dry-run
    python manage.py backfill_classroom_groups --limit 500
"""
from django.core.management.base import BaseCommand

from core.classroom_chat_bridge import ensure_classroom_group
from tuitionclass.models import Classroom


class Command(BaseCommand):
    help = "Create chat groups for existing classrooms that have none (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--limit", type=int, default=0, help="Max classrooms to process (0 = all).")

    def handle(self, *args, **opts):
        dry, limit = opts["dry_run"], opts["limit"]
        # no group link at all (never created, never archived by a teacher)
        qs = Classroom.objects.filter(
            is_active=True, is_deleted=False, linked_conversation_id__isnull=True,
        ).select_related("teacher").order_by("pk")
        if limit:
            qs = qs[:limit]
        done = failed = 0
        for classroom in qs:
            if dry:
                self.stdout.write(f"would create group for classroom {classroom.pk} ({classroom.title[:40]})")
                done += 1
                continue
            try:
                if not classroom.chat_group_enabled:
                    classroom.chat_group_enabled = True
                    classroom.save(update_fields=["chat_group_enabled"])
                ensure_classroom_group(classroom)
                done += 1
            except Exception as exc:  # noqa: BLE001
                failed += 1
                self.stderr.write(f"classroom {classroom.pk}: ERROR {exc}")
        self.stdout.write(self.style.SUCCESS(f"{'DRY-RUN ' if dry else ''}backfill: {done} ok, {failed} failed"))
