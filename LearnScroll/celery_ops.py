"""P-C3 -- failed-task alert for Celery.

`task_failure` fires only for a FINAL failure (after autoretry / self.retry gave up), so one flaky minute does not page anyone.
Each failure is
  1. logged as ERROR (Sentry's LoggingIntegration turns that into an event, already wired in settings.py),
  2. counted per task name in the Django cache (Redis in production) in a CELERY_FAIL_ALERT_WINDOW_SECONDS window, and
  3. when one task name fails CELERY_FAIL_ALERT_THRESHOLD times in that window ONE loud, fingerprinted alert goes out
     (log CRITICAL + Sentry "fatal" message). Not one alert per failure: a broken task that runs every minute would otherwise
     drown the channel.
Hard time-limit kills (TimeLimitExceeded) arrive here too. This module never raises into Celery.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger("celery.ops")


def _cfg(name: str, default):
    try:
        from django.conf import settings
        return getattr(settings, name, default)
    except Exception:
        return default


def record_failure(task_name: str, task_id: Optional[str], exc: BaseException, cache: Any = None, now: Optional[float] = None) -> Optional[int]:
    """Count one failure. Returns the failure count inside the current window, or None when counting was not possible."""
    window = max(60, int(_cfg("CELERY_FAIL_ALERT_WINDOW_SECONDS", 900)))
    threshold = max(1, int(_cfg("CELERY_FAIL_ALERT_THRESHOLD", 5)))
    logger.error("CELERY TASK FAILED: %s (task_id=%s): %s", task_name, task_id, type(exc).__name__)
    count: Optional[int] = None
    try:
        if cache is None:
            from django.core.cache import cache as _cache
            cache = _cache
        bucket = int((now if now is not None else time.time()) // window)
        key = f"celery:fail:{task_name}:{bucket}"
        cache.add(key, 0, timeout=window * 2)
        count = int(cache.incr(key))
    except Exception:  # cache down: the ERROR log above still reached Sentry
        return None
    if count == threshold:  # exactly once per window per task
        send_alert(task_name, count, window, exc)
    return count


def send_alert(task_name: str, count: int, window: int, exc: BaseException) -> None:
    logger.critical("CELERY ALERT: task %s failed %d times in %d min (last: %s). Check the worker log / Sentry.",
                    task_name, count, window // 60, type(exc).__name__)
    try:
        import sentry_sdk
        new_scope = getattr(sentry_sdk, "new_scope", None) or sentry_sdk.push_scope  # sdk 2.x / 1.x
        with new_scope() as scope:
            scope.set_tag("alert_type", "celery_task_failing")
            scope.set_tag("celery_task", task_name)
            scope.set_level("fatal")
            scope.fingerprint = ["celery-task-failing", task_name]
            sentry_sdk.capture_message(f"Celery task {task_name} failed {count}x in {window // 60} min", level="fatal")
    except Exception:  # sentry not installed / not configured
        pass


def on_task_failure(sender=None, task_id=None, exception=None, **kwargs) -> None:
    try:
        record_failure(getattr(sender, "name", None) or "unknown", task_id, exception or Exception("unknown"))
    except Exception:  # an alerting bug must never mask the real failure
        logger.exception("celery_ops.on_task_failure itself failed")
