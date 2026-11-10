"""python manage.py social_season_tick [--create-first]   - run the season job by hand (local dev / after a deploy)."""
from django.core.management.base import BaseCommand

from social_learning import seasons


class Command(BaseCommand):
    help = "Activate / refresh / close study seasons and create the next one (same as the Celery job)."

    def handle(self, *args, **options):
        self.stdout.write(str(seasons.tick()))
