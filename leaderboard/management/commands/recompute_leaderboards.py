"""
python manage.py recompute_leaderboards [--period weekly|all_time|both]

Manual/local-dev trigger for `leaderboard.tasks.recompute_*` — the same
jobs CELERY_BEAT_SCHEDULE runs on a timer (LearnScroll/settings.py). Useful
right after seeding demo data, or when Celery/beat isn't running locally
and you just want the leaderboard tab to show something.
"""
from django.core.management.base import BaseCommand

from leaderboard import tasks


class Command(BaseCommand):
    help = "Recompute all leaderboard boards (test series, campus sections, engagement)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--period", choices=["weekly", "all_time", "both"], default="both",
            help="Which period(s) to recompute (default: both).",
        )

    def handle(self, *args, **options):
        period = options["period"]
        if period in ("weekly", "both"):
            summary = tasks.recompute_weekly_boards()
            self.stdout.write(self.style.SUCCESS(f"weekly: {summary}"))
        if period in ("all_time", "both"):
            summary = tasks.recompute_all_time_boards()
            self.stdout.write(self.style.SUCCESS(f"all_time: {summary}"))
