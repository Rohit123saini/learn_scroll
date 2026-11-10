"""
T3.7 -- one command for "is the platform healthy?" (systemd timer every 5 min).

  python manage.py check_system_health [--alert] [--skip-celery] [--skip-backups]

Checks DB, cache, Celery broker + worker ping, disk space, backup freshness / last restore drill.
--alert logs ONE aggregated ERROR (-> Sentry, grouped by a stable fingerprint) and, if the Sentry SDK
is active, sends a Cron check-in (monitor slug `learnscroll-system-health`) so Sentry also alerts when
THIS timer itself stops running.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from core import ops_health

logger = logging.getLogger(__name__)
MONITOR_SLUG = "learnscroll-system-health"


def _checkin(status):
    try:
        from sentry_sdk.crons import capture_checkin

        return capture_checkin(monitor_slug=MONITOR_SLUG, status=status)
    except Exception:  # noqa: BLE001 - sentry optional
        return None


class Command(BaseCommand):
    help = "Run all operational health checks; exit 1 on any failure."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true")
        parser.add_argument("--skip-celery", action="store_true")
        parser.add_argument("--skip-backups", action="store_true")

    def handle(self, *args, **opts):
        checks = [("database", ops_health.check_database), ("cache", ops_health.check_cache),
                  ("disk", ops_health.check_disk)]
        if not opts["skip_celery"]:
            checks.append(("celery", ops_health.check_celery))
        if not opts["skip_backups"]:
            checks.append(("backups", ops_health.check_backups))

        problems = []
        for name, fn in checks:
            ok, detail = fn()
            self.stdout.write(f"  {name:<10} {'ok' if ok else 'FAIL':<5} {detail}")
            if not ok:
                problems.append(f"{name}: {detail}")

        if problems:
            msg = "System health UNHEALTHY:\n - " + "\n - ".join(problems)
            if opts["alert"]:
                try:
                    import sentry_sdk

                    with sentry_sdk.new_scope() as scope:
                        scope.fingerprint = ["system-health", *sorted(p.split(":")[0] for p in problems)]
                        logger.error(msg)
                except Exception:  # noqa: BLE001 - older sentry-sdk / not installed
                    logger.error(msg)
                _checkin("error")
            raise CommandError(msg)
        _checkin("ok")
        self.stdout.write(self.style.SUCCESS("System healthy."))
