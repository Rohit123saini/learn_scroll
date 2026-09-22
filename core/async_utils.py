# core/async_utils.py
"""
One place that decides HOW a cross-app side effect (notification, chat-group
sync, ...) leaves the request/response path.

Rule of thumb for this project:
  - Anything the caller needs a RETURN VALUE from (assigments.bridge's
    create_context_assigments / get_submissions_for_context, ...) stays a plain
    synchronous bridge call — a signal or a task can't hand data back.
  - Anything that is a fire-and-forget SIDE EFFECT goes through
    `dispatch_after_commit()` below and runs in a Celery worker.

What `dispatch_after_commit()` guarantees:
  1. Never enqueues before the surrounding transaction commits (a worker must
     never see a half-written row, and a rolled-back request enqueues nothing).
  2. Never raises into the caller — a side effect can't break the main action.
  3. Never blocks a request for long when Redis is down: the publish uses a
     tiny retry policy instead of kombu's default (which can stall for many
     seconds).
  4. Never silently loses the work when the broker is down: it falls back to
     running the task inline (best-effort), exactly like the pre-Celery
     behaviour.
"""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)

# Fail fast if Redis is unreachable — a request must not hang on a side effect.
_PUBLISH_RETRY_POLICY = {
    "max_retries": 1,
    "interval_start": 0,
    "interval_step": 0.2,
    "interval_max": 0.5,
}


def in_celery_worker() -> bool:
    """True when running inside a Celery task (incl. `.apply()` fallbacks).
    Callers that are already in a worker should do the work directly instead
    of enqueueing yet another hop."""
    try:
        from celery import current_task

        return bool(current_task and current_task.request and current_task.request.id)
    except Exception:
        return False


def _task_name(task) -> str:
    return getattr(task, "name", repr(task))


def _run_inline(task, args, kwargs):
    try:
        result = task.apply(args=args, kwargs=kwargs)
        if getattr(result, "failed", None) and result.failed():
            logger.error(
                "Inline fallback for task %s failed: %s", _task_name(task), getattr(result, "traceback", ""),
            )
    except Exception:
        logger.exception("Inline fallback for task %s raised.", _task_name(task))


def dispatch_after_commit(task, *args, **kwargs):
    """Enqueue `task.apply_async(args, kwargs)` once the current transaction
    commits (immediately, if there is none). See module docstring."""

    def _send():
        try:
            task.apply_async(args=args, kwargs=kwargs, retry=True, retry_policy=_PUBLISH_RETRY_POLICY)
        except Exception:
            logger.exception(
                "Could not publish task %s to the broker; running it inline instead.", _task_name(task),
            )
            _run_inline(task, args, kwargs)

    transaction.on_commit(_send)
