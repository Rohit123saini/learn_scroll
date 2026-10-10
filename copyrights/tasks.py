"""Celery tasks for the copyright automations (schedules: settings.CELERY_BEAT_SCHEDULE)."""

import logging

from celery import shared_task

from . import services

logger = logging.getLogger(__name__)


@shared_task(name="copyrights.auto_restore_counter_notices")
def auto_restore_counter_notices():
    return services.auto_restore_due_counter_notices()


@shared_task(name="copyrights.expire_strikes")
def expire_strikes():
    return services.expire_strikes()


@shared_task(name="copyrights.escalate_stale_claims")
def escalate_stale_claims():
    return services.escalate_stale_claims()


@shared_task(name="copyrights.expire_needs_info")
def expire_needs_info():
    return services.expire_needs_info()
