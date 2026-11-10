"""P-C8 -- optional read-replica routing (OFF by default).

Nothing here does anything unless DB_REPLICA_ENABLED=true AND DB_REPLICA_URL is set (settings.py then adds
DATABASES["replica"], this router and ReplicaReadMiddleware). With the toggle off the whole module is inert.

Rules (conservative on purpose -- a replica lags the primary by some milliseconds to seconds):
  * Writes, select_for_update, migrations and Celery/management commands ALWAYS use the primary ("default").
  * A read goes to the replica only when ALL of these hold:
      - the request is GET/HEAD and its path starts with one of DB_REPLICA_PATH_PREFIXES (default: none),
      - this client did not write in the last DB_REPLICA_PIN_SECONDS (read-your-writes, see below),
      - the model's app is not in DB_REPLICA_EXCLUDE_APPS (auth / sessions / tokens must see a fresh login),
      - we are not inside an atomic block on the primary,
      - the replica is not marked down (see mark_replica_down).
  * Read-your-writes: after any successful POST/PUT/PATCH/DELETE the client is "pinned" to the primary for a few
    seconds. The client is identified by a hash of its Authorization header (mobile app, no cookies) or its session
    cookie. Pin store = Django cache (Redis in production). Cache error -> primary (fail safe).
  * Replica connection error -> replica marked down for DB_REPLICA_DOWN_SECONDS in this process, reads go back to
    the primary. The request that hit the error still fails (we cannot replay a half-run view safely).
"""
from __future__ import annotations

import contextvars
import hashlib
import time
from contextlib import contextmanager

REPLICA_ALIAS = "replica"
PRIMARY_ALIAS = "default"
SAFE_METHODS = ("GET", "HEAD")
PIN_KEY_PREFIX = "dbreplica:pin:"

_use_replica: contextvars.ContextVar[bool] = contextvars.ContextVar("db_use_replica", default=False)
_down_until = 0.0  # time.monotonic() deadline, per process


def _conf(name, default):
    from django.conf import settings
    return getattr(settings, name, default)


def replica_active() -> bool:
    return bool(_conf("DB_REPLICA_ENABLED", False)) and _down_until <= time.monotonic()


def mark_replica_down(seconds=None) -> None:
    global _down_until
    _down_until = time.monotonic() + float(seconds if seconds is not None else _conf("DB_REPLICA_DOWN_SECONDS", 30))


def reset_replica_state() -> None:  # tests / admin shell
    global _down_until
    _down_until = 0.0


@contextmanager
def replica_reads():
    """Opt-in for code outside HTTP requests (e.g. a report task): `with replica_reads(): qs = ...`.
    Still obeys every rule of the router except the request-path / pin ones."""
    token = _use_replica.set(True)
    try:
        yield
    finally:
        _use_replica.reset(token)


class ReplicaRouter:
    def _excluded(self, model) -> bool:
        label = getattr(getattr(model, "_meta", None), "app_label", "")
        return label in set(_conf("DB_REPLICA_EXCLUDE_APPS", ()))

    def db_for_read(self, model, **hints):
        if not _use_replica.get() or not replica_active() or self._excluded(model):
            return None
        from django.db import connections
        if connections[PRIMARY_ALIAS].in_atomic_block:
            return None
        return REPLICA_ALIAS

    def db_for_write(self, model, **hints):
        return None  # -> "default"; select_for_update() also asks for a write DB

    def allow_relation(self, obj1, obj2, **hints):
        return True  # same data, two connections

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        return db == PRIMARY_ALIAS  # never migrate the replica (it replicates)


def client_key(request) -> str:
    raw = request.META.get("HTTP_AUTHORIZATION") or request.COOKIES.get(_conf("SESSION_COOKIE_NAME", "sessionid")) or ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32] if raw else ""


class ReplicaReadMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _prefixes(self):
        return tuple(_conf("DB_REPLICA_PATH_PREFIXES", ()))

    def _pinned(self, key) -> bool:
        if not key:
            return False  # anonymous: public data, no read-your-writes to protect
        try:
            from django.core.cache import cache
            return bool(cache.get(PIN_KEY_PREFIX + key))
        except Exception:  # noqa: BLE001 - cache down -> primary
            return True

    def _pin(self, key) -> None:
        if not key:
            return
        try:
            from django.core.cache import cache
            cache.set(PIN_KEY_PREFIX + key, 1, timeout=max(1, int(_conf("DB_REPLICA_PIN_SECONDS", 5))))
        except Exception:  # noqa: BLE001
            pass

    def __call__(self, request):
        key = client_key(request)
        if request.method not in SAFE_METHODS:
            response = self.get_response(request)
            if response.status_code < 500:
                self._pin(key)  # even a 4xx may have written before failing validation -> be safe
            return response
        use = (
            replica_active()
            and any(request.path.startswith(p) for p in self._prefixes())
            and not self._pinned(key)
        )
        request._db_replica = use
        token = _use_replica.set(use)
        try:
            return self.get_response(request)
        finally:
            _use_replica.reset(token)

    def process_exception(self, request, exception):
        if getattr(request, "_db_replica", False):
            from django.db import InterfaceError, OperationalError
            if isinstance(exception, (OperationalError, InterfaceError)):
                mark_replica_down()
        return None
