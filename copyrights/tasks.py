"""Celery tasks for the copyright automations (schedules: settings.CELERY_BEAT_SCHEDULE).

Task 13 -- these tasks decide legal deadlines (content auto-restore after a counter-notice,
strike expiry, court-order warnings, SLA escalation). A silent failure is a COMPLIANCE problem, so:

  * every task retries with back-off on transient errors (DB blip, Redis hiccup);
  * when a task finally FAILS (retries exhausted) `CopyrightTask.on_failure` raises a loud alert:
    logger.error(exc_info) -> Sentry event (LoggingIntegration) + an explicit tagged Sentry event,
    so you can route "copyright_task" alerts to the right person;
  * every SUCCESS stamps a heartbeat in the cache, read by `manage.py check_copyright_automation`
    (detects "beat/worker not running", which produces no exception at all);
  * Sentry Cron Monitors (CeleryIntegration(monitor_beat_tasks=True) in settings) alert on missed runs.

All six scheduled tasks are idempotent scans (they re-select rows under select_for_update), so retries and
acks_late re-delivery are safe.
"""

import logging
from datetime import datetime, timezone as dt_timezone

from celery import Task, shared_task
from django.core.cache import cache

from . import services

logger = logging.getLogger(__name__)

HEARTBEAT_KEY = "copyrights:heartbeat:{name}"
TASK_NAMES = (
    "copyrights.auto_restore_counter_notices",
    "copyrights.expire_strikes",
    "copyrights.escalate_stale_claims",
    "copyrights.expire_needs_info",
    "copyrights.warn_due_legal_orders",
    "copyrights.purge_personal_data",
)


def record_heartbeat(name, result=None):
    cache.set(
        HEARTBEAT_KEY.format(name=name),
        {"at": datetime.now(dt_timezone.utc).isoformat(), "result": str(result)[:200]},
        timeout=None,
    )


def get_heartbeat(name):
    """-> {"at": datetime, "result": str} or None if the task has never succeeded."""
    raw = cache.get(HEARTBEAT_KEY.format(name=name))
    if not raw:
        return None
    return {"at": datetime.fromisoformat(raw["at"]), "result": raw.get("result", "")}


def alert_failure(name, exc, task_id=None):
    """One loud, tagged alert. Never raises (an alerting bug must not mask the real failure)."""
    logger.error("COPYRIGHT TASK FAILED: %s (task_id=%s): %r", name, task_id, exc, exc_info=exc)
    try:
        import sentry_sdk

        new_scope = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope  # sdk 2.x / 1.x
        with new_scope() as scope:
            scope.set_tag("copyright_task", name)
            scope.set_tag("alert_type", "copyright_automation_failed")
            scope.set_level("fatal")
            scope.fingerprint = ["copyright-task-failed", name]
            sentry_sdk.capture_exception(exc)
    except Exception:  # sentry not installed / not configured
        pass


class CopyrightTask(Task):
    autoretry_for = (Exception,)
    retry_backoff = 60          # 1 min, 2 min, 4 min ... (doubles)
    retry_backoff_max = 900     # never wait longer than 15 min between tries
    retry_jitter = True
    max_retries = 3

    def on_success(self, retval, task_id, args, kwargs):
        record_heartbeat(self.name, retval)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        alert_failure(self.name, exc, task_id)


class CopyrightEventTask(Task):
    """Base for the ON-DEMAND tasks (fired by an action, not by beat): quarantine / snapshot / e-mail / upload check.

    Alert on final failure exactly like the scheduled ones, but do NOT auto-retry: these move files and send
    e-mails, so a blind retry could act twice. They have no beat schedule, hence no heartbeat either.
    """

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        alert_failure(self.name, exc, task_id)


@shared_task(base=CopyrightTask, name="copyrights.auto_restore_counter_notices")
def auto_restore_counter_notices():
    return services.auto_restore_due_counter_notices()


@shared_task(base=CopyrightTask, name="copyrights.expire_strikes")
def expire_strikes():
    return services.expire_strikes()


@shared_task(base=CopyrightTask, name="copyrights.escalate_stale_claims")
def escalate_stale_claims():
    return services.escalate_stale_claims()


@shared_task(base=CopyrightTask, name="copyrights.expire_needs_info")
def expire_needs_info():
    return services.expire_needs_info()


@shared_task(base=CopyrightTask, name="copyrights.warn_due_legal_orders")
def warn_due_legal_orders():
    from . import legal_orders

    return legal_orders.warn_due_orders()


@shared_task(base=CopyrightTask, name="copyrights.purge_personal_data")
def purge_personal_data():
    """DPDP Act 2023: erase claimant personal data whose retention period is over (copyrights/retention.py)."""
    from . import retention

    return retention.purge_personal_data()


# --- on-demand tasks (copyrights/media.py, notify.py, signals.py) -- failures alert, no auto-retry ----------
@shared_task(base=CopyrightEventTask, name="copyrights.quarantine_media")
def quarantine_media_task(hold_id):
    from . import media

    return media.quarantine_media(hold_id)


@shared_task(base=CopyrightEventTask, name="copyrights.snapshot_claim")
def snapshot_claim_task(claim_id):
    from . import media

    return media._snapshot_now(claim_id)


@shared_task(base=CopyrightEventTask, name="copyrights.send_email")
def send_email_task(to, subject, body):
    from . import notify

    return notify.send_email_now(to, subject, body)


@shared_task(base=CopyrightEventTask, name="copyrights.check_upload")
def check_upload_task(target_type, target_id, name):
    from . import signals

    return bool(signals.check_upload(target_type, target_id, name))
