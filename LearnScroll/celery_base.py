"""P-C3 -- default base class for EVERY Celery task (LearnScroll/celery.py: `Celery(..., task_cls=...)`).

What it adds (only for tasks that do not set their own): a retry policy for *transient infrastructure* errors.
  * autoretry_for  = connection / timeout style errors only (broker, DB, HTTP). A bug (KeyError, ValueError ...) is NOT
    retried - retrying a bug just repeats it (and can repeat a half-done side effect such as a push or an e-mail).
  * retry_backoff  = 30 s, 60 s, 120 s ... capped at 10 min, with jitter, max 3 retries.
Tasks that already declare `autoretry_for` / `max_retries` / `base=` themselves (copyrights.CopyrightTask, bound tasks with
manual self.retry) keep their own behaviour - class attributes on the task or `@shared_task(...)` options win.
"""
from __future__ import annotations

from celery import Task


def _transient_errors():
    errs = [ConnectionError, TimeoutError]
    try:
        from django.db.utils import InterfaceError, OperationalError
        errs += [OperationalError, InterfaceError]
    except Exception:  # pragma: no cover - django missing
        pass
    try:
        import requests
        errs += [requests.exceptions.ConnectionError, requests.exceptions.Timeout]
    except Exception:  # pragma: no cover - requests missing
        pass
    try:
        from kombu.exceptions import OperationalError as KombuOperationalError
        errs.append(KombuOperationalError)
    except Exception:  # pragma: no cover
        pass
    return tuple(errs)


class RetryingTask(Task):
    autoretry_for = _transient_errors()
    retry_backoff = 30
    retry_backoff_max = 600
    retry_jitter = True
    max_retries = 3
