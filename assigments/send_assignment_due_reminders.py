# assignment/management/commands/send_assignment_due_reminders.py
"""
Thin CLI wrapper around `assignment.tasks.send_due_reminders` — lets the
project's existing scheduler (system cron, Celery beat's
`CrontabSchedule` calling this via `django.core.management.call_command`,
etc.) invoke this the same way as any other periodic Django management
command, without this app assuming which one the project uses (see
tasks.py's own docstring on being scheduler-agnostic).
"""
from django.core.management.base import BaseCommand, CommandParser

from assignment.tasks import send_due_reminders


class Command(BaseCommand):
    help = "Send due-date reminder notifications for MISSING assignment submissions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--lookahead-hours",
            type=int,
            default=24,
            help="Notify for assignments due within this many hours (default: 24).",
        )

    def handle(self, *args, **options):
        count = send_due_reminders(lookahead_hours=options["lookahead_hours"])
        self.stdout.write(self.style.SUCCESS(f"Sent {count} assignment due-date reminder(s)."))
