"""
common/db_retry.py  (T0.1)

Chhota retry helper: "database is locked" (SQLite) / deadlock-ya-serialization
(Postgres) jaise TEMPORARY `OperationalError` par ek-do baar dobara try karo.

Kab use karein: chhote, idempotent writes (device-token register, presence
`last_seen`, heartbeat...) jahan ek retry safe hai. Kab NAHI: aisa write jisme
side-effects hain (coins, notifications) — wahan retry se double-apply ho sakta hai.

    from common.db_retry import retry_on_db_lock

    retry_on_db_lock(lambda: Model.objects.filter(...).update(...))

    @with_db_retry()
    def do_write(...): ...
"""
import functools
import logging
import random
import time

from django.db import OperationalError, close_old_connections, connection

logger = logging.getLogger(__name__)

# Sirf wahi OperationalError retry karo jo "temporary lock" jaisa dikhe —
# "no such table" jaisi asli galti ko retry karna bekaar (aur confusing) hai.
_TRANSIENT_MARKERS = (
    "database is locked",
    "database table is locked",
    "deadlock detected",
    "could not serialize access",
)


def is_transient_db_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if any(marker in msg for marker in _TRANSIENT_MARKERS):
        return True
    cause = getattr(exc, "__cause__", None)
    code = getattr(cause, "pgcode", None) or getattr(cause, "sqlstate", None)
    # 40001 serialization_failure, 40P01 deadlock_detected, 55P03 lock_not_available
    return code in ("40001", "40P01", "55P03")


DEFAULT_WRITE_RETRIES = 3  # T2.2 - hot writes (heartbeat / streak / session / snapshot / upload complete)


def retry_on_db_lock(fn, *, retries: int = 1, delay: float = 0.15):
    """`fn()` chalao; transient lock error par `retries` baar (backoff ke saath) dobara.

    Default `retries=1` => total 2 attempts ("ek retry"). Aakhri attempt bhi fail
    ho to original exception raise hota hai — caller decide kare use swallow karna hai ya nahi.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except OperationalError as exc:
            if attempt >= retries or not is_transient_db_error(exc):
                raise
            attempt += 1
            logger.warning(
                "db write hit a transient lock (%s) — retry %d/%d", exc, attempt, retries
            )
            # Agar connection kharab/atka ho to naya le lo; transaction ke andar
            # ho to caller ka kaam hai (hum yahan atomic block ke andar retry nahi karte).
            if not connection.in_atomic_block:
                close_old_connections()
            # T2.2 - chhota exponential backoff (0.15, 0.3, 0.6 ...) + jitter, taaki do writer ek saath na takrayein.
            time.sleep(delay * (2 ** (attempt - 1)) * (1 + random.random() * 0.25))


def with_db_retry(retries: int = 1, delay: float = 0.15):
    """Decorator form of `retry_on_db_lock`."""

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return retry_on_db_lock(lambda: func(*args, **kwargs), retries=retries, delay=delay)

        return wrapper

    return decorator


def best_effort_write(fn, *, default=None, retries: int = DEFAULT_WRITE_RETRIES, delay: float = 0.1, label: str = "write"):
    """T2.3 - "best effort" write: retry on a transient lock, and if it STILL fails return `default` (never raise).

    Heartbeat / streak jaise writes ke liye - ye analytics-type hain, inke fail hone se user ko 500 nahi dikhna chahiye.
    Sirf transient lock error swallow hota hai; asli bug (no such table, ValueError ...) phir bhi raise hota hai.
    """
    try:
        return retry_on_db_lock(fn, retries=retries, delay=delay)
    except OperationalError as exc:
        if not is_transient_db_error(exc):
            raise
        logger.warning("best-effort %s gave up after %d retries: %s", label, retries, exc)
        return default
