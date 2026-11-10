"""
T3.7 -- are the backups actually happening, and does a restore actually work?

  python manage.py check_backups            # human readable, exit 1 if anything is wrong
  python manage.py check_backups --alert    # also logger.error(...) -> Sentry

Reads the markers written by deploy/backup/*.sh (BACKUP_DIR/postgres/LATEST.json,
media/LATEST.json, drills/LAST_DRILL.json).
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from core.ops_health import backup_dir, check_backups

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Verify the Postgres/media backups are fresh and the last restore drill passed."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true", help="log an ERROR (-> Sentry) when unhealthy")

    def handle(self, *args, **opts):
        ok, detail = check_backups()
        self.stdout.write(f"backup dir: {backup_dir()}")
        if ok:
            self.stdout.write(self.style.SUCCESS("Backups healthy."))
            return
        msg = f"Backups UNHEALTHY: {detail}"
        if opts["alert"]:
            logger.error(msg)
        raise CommandError(msg)
