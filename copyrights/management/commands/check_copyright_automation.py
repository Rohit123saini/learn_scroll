"""
Is the copyright automation actually RUNNING in production? (task 13)

  python manage.py check_copyright_automation            # human readable, exit 1 if unhealthy
  python manage.py check_copyright_automation --alert    # also send a Sentry/log error when unhealthy
  python manage.py check_copyright_automation --skip-ping   # don't talk to the broker / workers

Checks
  1. at least one Celery WORKER answers a ping           (skipped with --skip-ping)
  2. each of the scheduled copyrights.* tasks has a recent heartbeat, i.e. it SUCCEEDED within
     `STALE_FACTOR` x its CELERY_BEAT_SCHEDULE interval. A stale/missing heartbeat means the
     beat scheduler is down, the worker is down, or the task keeps failing.

Run it every ~15 min from a cron job / uptime monitor / container health-check, and ALSO rely on
the Sentry Cron Monitors (settings: CeleryIntegration(monitor_beat_tasks=True)).
--grace-minutes (default 90) is the MINIMUM staleness limit, so a task that runs hourly isn't
flagged after one slow run. A task that has NEVER succeeded is always reported (right after a fresh
deploy, wait for the first scheduled run before wiring this into paging).
"""
import logging
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from copyrights import tasks

logger = logging.getLogger(__name__)
STALE_FACTOR = 2.5


def _interval_seconds(schedule):
    """Seconds between runs for a number / timedelta / celery schedule(), else None (crontab)."""
    if isinstance(schedule, (int, float)):
        return float(schedule)
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    run_every = getattr(schedule, "run_every", None)
    if isinstance(run_every, timedelta):
        return run_every.total_seconds()
    return None


def expected_intervals():
    out = {}
    for entry in getattr(settings, "CELERY_BEAT_SCHEDULE", {}).values():
        name = entry.get("task")
        if name in tasks.TASK_NAMES:
            sec = _interval_seconds(entry.get("schedule"))
            out[name] = sec if sec else 24 * 3600.0  # crontab: assume at most daily
    return out


class Command(BaseCommand):
    help = "Verify the Celery worker/beat are running the copyright automations."

    def add_arguments(self, parser):
        parser.add_argument("--alert", action="store_true", help="log an ERROR (-> Sentry) when unhealthy")
        parser.add_argument("--skip-ping", action="store_true", help="don't ping Celery workers")
        parser.add_argument("--grace-minutes", type=int, default=90)

    def handle(self, *args, **opts):
        problems = []
        now = datetime.now(dt_timezone.utc)

        if not opts["skip_ping"]:
            try:
                from LearnScroll.celery import app

                replies = app.control.ping(timeout=3.0) or []
            except Exception as exc:  # broker unreachable
                replies = []
                problems.append(f"cannot reach the Celery broker: {exc!r}")
            if not replies and not any("broker" in p for p in problems):
                problems.append("no Celery worker answered a ping (worker is down?)")
            else:
                self.stdout.write(f"workers online: {len(replies)}")

        intervals = expected_intervals()
        missing_in_schedule = [n for n in tasks.TASK_NAMES if n not in intervals]
        for n in missing_in_schedule:
            problems.append(f"{n} is not in CELERY_BEAT_SCHEDULE (it will never run)")

        for name, interval in sorted(intervals.items()):
            hb = tasks.get_heartbeat(name)
            limit = timedelta(seconds=max(interval * STALE_FACTOR, opts["grace_minutes"] * 60))
            if hb is None:
                problems.append(f"{name}: no successful run recorded yet")
                self.stdout.write(f"  {name:<46} NEVER RAN")
                continue
            age = now - hb["at"]
            stale = age > limit
            self.stdout.write(f"  {name:<46} last ok {int(age.total_seconds() // 60):>5} min ago "
                              f"(limit {int(limit.total_seconds() // 60)} min) {'STALE' if stale else 'ok'}")
            if stale:
                problems.append(f"{name}: last success {int(age.total_seconds() // 60)} min ago, "
                                f"expected every {int(interval // 60)} min")

        if problems:
            msg = "Copyright automation UNHEALTHY:\n - " + "\n - ".join(problems)
            if opts["alert"]:
                logger.error(msg)
            raise CommandError(msg)
        self.stdout.write(self.style.SUCCESS("Copyright automation healthy."))
