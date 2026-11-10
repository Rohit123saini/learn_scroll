"""aiquality/tasks.py - P-F6 Celery jobs.

aiquality.prune_logs     daily: delete AiCallLog older than AI_CALL_LOG_RETENTION_DAYS (90) and REVIEWED feedback older
                         than AI_FEEDBACK_RETENTION_DAYS (365). Unreviewed 👎 are never pruned.
aiquality.check_budget   every 30 min: logs ONE critical line per day when estimated spend crosses 80 % / 100 % of
                         AI_DAILY_BUDGET_USD (Sentry picks up critical logs). Does nothing when no budget is set.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)


def _days(name, default):
    try:
        return max(int(getattr(settings, name, default)), 7)
    except (TypeError, ValueError):
        return default


@shared_task(name="aiquality.prune_logs")
def prune_logs():
    from .models import AiCallLog, AiFeedback

    now = timezone.now()
    calls, _ = AiCallLog.objects.filter(created_at__lt=now - timedelta(days=_days("AI_CALL_LOG_RETENTION_DAYS", 90))).delete()
    fb, _ = (AiFeedback.objects.exclude(status=AiFeedback.Status.NEW)
             .filter(created_at__lt=now - timedelta(days=_days("AI_FEEDBACK_RETENTION_DAYS", 365))).delete())
    return {"calls_deleted": calls, "feedback_deleted": fb}


@shared_task(name="aiquality.check_budget")
def check_budget():
    from . import dashboard

    budget = dashboard._budget()
    if budget <= 0:
        return {"budget": 0}
    spent = dashboard.today_spend_micro() / 1_000_000
    level = dashboard.alert_level(spent, budget)
    if level != "none":
        day = timezone.localtime(timezone.now()).strftime("%Y%m%d")
        if cache.add(f"aiq:budget_alert:{day}:{level}", 1, 36 * 3600):
            logger.critical("AI cost guard: estimated spend today is $%.2f of the $%.2f daily budget (%s)", spent, budget, level)
    return {"budget": budget, "spent": round(spent, 4), "level": level}
