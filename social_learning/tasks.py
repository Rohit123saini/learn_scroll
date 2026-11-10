"""
social_learning/tasks.py - Celery jobs (wired in LearnScroll/settings.py CELERY_BEAT_SCHEDULE, "social-*" entries).

    social_learning.season_tick           every 15 min   activate / refresh standings / close + reward / create next season
    social_learning.pomodoro_sweep        every 10 min   stop shared timers of rooms nobody is in
    social_learning.partner_daily         00:20 IST      shared-streak milestones (streak-freeze reward, once per pair+milestone)
    social_learning.partner_evening       19:30 IST      "your partner is done" reminder to the one who is behind
Every task swallows its own errors and returns a small summary dict (same style as leaderboard/tasks.py).
"""
import logging

from celery import shared_task

from . import accountability, conf, pomodoro, seasons

logger = logging.getLogger(__name__)


@shared_task(name="social_learning.season_tick")
def season_tick():
    if not conf.enabled():
        return {"skipped": "disabled"}
    summary = seasons.tick()
    logger.info("social_learning.season_tick: %s", summary)
    return summary


@shared_task(name="social_learning.pomodoro_sweep")
def pomodoro_sweep():
    if not conf.enabled():
        return {"skipped": "disabled"}
    try:
        return {"stopped": pomodoro.sweep_idle_timers()}
    except Exception:
        logger.exception("pomodoro sweep failed")
        return {"stopped": 0, "error": True}


@shared_task(name="social_learning.partner_daily")
def partner_daily():
    if not conf.enabled():
        return {"skipped": "disabled"}
    try:
        return {"milestones": accountability.grant_milestones()}
    except Exception:
        logger.exception("partner daily failed")
        return {"milestones": 0, "error": True}


@shared_task(name="social_learning.partner_evening")
def partner_evening():
    if not conf.enabled():
        return {"skipped": "disabled"}
    try:
        return {"reminders": accountability.evening_nudges()}
    except Exception:
        logger.exception("partner evening failed")
        return {"reminders": 0, "error": True}
