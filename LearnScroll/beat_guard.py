"""P-C3 -- "exactly one Celery beat" guard.

Two beat processes (or `worker -B` on two hosts) fire EVERY periodic task twice: double notifications, double refunds,
double digests. systemd only protects one host. This guard takes a Redis lock (SET NX EX) when beat starts and keeps
renewing it from a daemon thread. A second beat finds the lock taken, logs CRITICAL and exits with code 3
(deploy/systemd/learnscroll-celery-beat.service has RestartPreventExitStatus=3, so it does not restart-loop).

Fail-open on purpose: if Redis is unreachable the guard logs a warning and beat keeps running (Redis is also the broker, so
nothing would be scheduled anyway). The lock expires by itself (BEAT_LOCK_TTL) after a crash, so a restart never waits long.
Disable with BEAT_SINGLE_INSTANCE_GUARD=false (e.g. a local dev machine with two terminals on purpose).
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from typing import Any, Callable, Optional

logger = logging.getLogger("celery.beat_guard")

LOCK_KEY = "learnscroll:celery-beat-lock"
EXIT_CODE = 3


class BeatLock:
    def __init__(self, client: Any, key: str = LOCK_KEY, ttl: int = 60, token: Optional[str] = None):
        self.client = client
        self.key = key
        self.ttl = max(10, int(ttl))
        self.token = token or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

    def acquire(self) -> bool:
        """True if we hold the lock now (taken, or already ours)."""
        if self.client.set(self.key, self.token, nx=True, ex=self.ttl):
            return True
        return self.holder() == self.token

    def holder(self) -> Optional[str]:
        val = self.client.get(self.key)
        if isinstance(val, bytes):
            val = val.decode("utf-8", "replace")
        return val

    def renew(self) -> bool:
        """Extend our lock. False only when somebody ELSE holds it. A missing key is re-taken."""
        cur = self.holder()
        if cur is None:
            return bool(self.client.set(self.key, self.token, nx=True, ex=self.ttl))
        if cur != self.token:
            return False
        self.client.expire(self.key, self.ttl)
        return True

    def release(self) -> None:
        try:
            if self.holder() == self.token:
                self.client.delete(self.key)
        except Exception:
            pass


def _exit(code: int) -> None:  # separate function so tests can replace it
    os._exit(code)


def start_guard(client: Any, ttl: int = 60, exit_fn: Callable[[int], None] = _exit, start_thread: bool = True) -> Optional[BeatLock]:
    """Take the lock or exit(3). Returns the lock (None when Redis could not be reached: fail-open)."""
    lock = BeatLock(client, ttl=ttl)
    try:
        ok = lock.acquire()
    except Exception as exc:
        logger.warning("beat guard: Redis not reachable (%s) - starting WITHOUT the single-instance guard.", type(exc).__name__)
        return None
    if not ok:
        logger.critical("beat guard: another beat already runs (lock %s held by %s). This process exits with code %d so tasks are not fired twice.",
                        lock.key, lock.holder(), EXIT_CODE)
        exit_fn(EXIT_CODE)
        return lock
    logger.info("beat guard: lock taken (%s, ttl %ss).", lock.token, lock.ttl)
    if start_thread:
        def _loop():
            stop = threading.Event()
            while not stop.wait(max(3, lock.ttl // 3)):
                try:
                    if not lock.renew():
                        logger.critical("beat guard: lock lost to another beat - exiting with code %d.", EXIT_CODE)
                        exit_fn(EXIT_CODE)
                        return
                except Exception as exc:
                    logger.warning("beat guard: renew failed (%s); will retry.", type(exc).__name__)
        threading.Thread(target=_loop, name="beat-guard", daemon=True).start()
    return lock


def on_beat_start(sender=None, **kwargs) -> None:
    """Signal handler for celery.signals.beat_init / beat_embedded_init."""
    try:
        from django.conf import settings
        if not getattr(settings, "BEAT_SINGLE_INSTANCE_GUARD", True) or getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
            return
        import redis
        client = redis.Redis.from_url(settings.CELERY_BROKER_URL, socket_connect_timeout=2, socket_timeout=2)
        start_guard(client, ttl=int(getattr(settings, "BEAT_LOCK_TTL", 60)))
    except SystemExit:
        raise
    except Exception as exc:
        logger.warning("beat guard not started: %s", type(exc).__name__)
