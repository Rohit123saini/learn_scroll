# login/sensitive.py
"""
X6 -- security hardening helpers for SENSITIVE SETTINGS endpoints
(password / e-mail / phone / username / privacy / wallet / withdrawal).

Three small, independent pieces. Nothing here touches the database, so no migration.

1. ``SensitiveWriteThrottle``
   A ``ScopedRateThrottle`` that
     * only counts unsafe methods (GET / HEAD / OPTIONS pass untouched, so opening a settings screen is never
       throttled -- only the writes are),
     * writes an ``rate_limited`` audit row when it refuses a request (de-duplicated by login/audit.py, because anyone
       can trigger it), so "who is hammering the password / e-mail change endpoint" is visible in the admin.
   Use it exactly like ScopedRateThrottle: ``throttle_classes = [SensitiveWriteThrottle]`` + ``throttle_scope = "..."``.
   Every scope MUST have a rate in settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] (DRF raises
   ImproperlyConfigured -> HTTP 500 otherwise; that already bit this project once).

2. ``ReauthGuard``
   "Wrong CURRENT password" counter for a logged-in user. The hourly throttle above caps requests, but a thief
   holding an unlocked phone could still try ~30 passwords an hour. After REAUTH_MAX_FAILURES (default 5) wrong tries
   inside REAUTH_FAILURE_WINDOW_SECONDS (default 15 min) the action is locked for REAUTH_LOCKOUT_SECONDS (default
   15 min), even for the right password. The owner is not stuck: the normal "forgot password" flow still works and
   clears nothing here on purpose (the lock simply expires).

3. ``record_change`` / ``changed_fields``
   One safe way to leave an audit row for a sensitive change. Stores field NAMES only -- never the old or new value
   (no e-mail, phone, username text, bank detail). Never raises.
"""
from __future__ import annotations

import logging
import time
from typing import Iterable, Mapping, Optional

from django.conf import settings
from django.core.cache import cache
from rest_framework.permissions import SAFE_METHODS
from rest_framework.throttling import ScopedRateThrottle

from . import audit
from .login_guard import incr_with_ttl

logger = logging.getLogger("security.sensitive")


# ---------------------------------------------------------------------------------------------------------------
# 1. throttle
# ---------------------------------------------------------------------------------------------------------------
class SensitiveWriteThrottle(ScopedRateThrottle):
    """Counts writes only, and audits a refusal. See the module docstring."""

    def allow_request(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        allowed = super().allow_request(request, view)
        if not allowed:
            try:
                user = getattr(request, "user", None)
                audit.record(
                    audit.Event.RATE_LIMITED,
                    request,
                    outcome=audit.BLOCKED,
                    user=user if getattr(user, "is_authenticated", False) else None,
                    reason=str(getattr(self, "scope", "") or "")[:64],
                )
            except Exception:  # auditing must never turn a 429 into a 500
                logger.warning("rate_limited audit failed", exc_info=True)
        return allowed


# ---------------------------------------------------------------------------------------------------------------
# 2. wrong-current-password lock
# ---------------------------------------------------------------------------------------------------------------
def _cfg(name: str, default: int) -> int:
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


class ReauthGuard:
    """Per-user, per-action counter of wrong CURRENT passwords (cache only, nothing is stored in the DB)."""

    def __init__(self, user, action: str = "password_change"):
        self._uid = getattr(user, "pk", None) or "anon"
        self._action = action
        self._limit = _cfg("REAUTH_MAX_FAILURES", 5)
        self._window = _cfg("REAUTH_FAILURE_WINDOW_SECONDS", 900)
        self._lockout = _cfg("REAUTH_LOCKOUT_SECONDS", 900)

    @property
    def _count_key(self) -> str:
        return f"reauthguard:n:{self._action}:{self._uid}"

    @property
    def _lock_key(self) -> str:
        return f"reauthguard:lock:{self._action}:{self._uid}"

    def retry_after(self) -> int:
        """Seconds until the next attempt is allowed (0 = not locked)."""
        unlock_at = cache.get(self._lock_key)
        if not unlock_at:
            return 0
        return max(int(float(unlock_at) - time.time() + 0.999), 0)

    def record_failure(self) -> tuple[int, int]:
        """Count one wrong password. Returns ``(retry_after, attempts_left)``; ``retry_after`` > 0 = just locked."""
        n = incr_with_ttl(self._count_key, self._window)
        if n >= self._limit:
            cache.set(self._lock_key, time.time() + self._lockout, self._lockout)
            cache.delete(self._count_key)
            return self._lockout, 0
        return 0, max(self._limit - n, 0)

    def clear(self) -> None:
        """A correct password resets the counter."""
        cache.delete(self._count_key)


# ---------------------------------------------------------------------------------------------------------------
# 3. audit helper
# ---------------------------------------------------------------------------------------------------------------
def changed_fields(
    request_data: Optional[Mapping], allowed: Iterable[str], before: Optional[Mapping] = None,
    after: Optional[Mapping] = None,
) -> list[str]:
    """Names (sorted) of the ``allowed`` fields that really changed.

    * ``before`` + ``after`` given  -> a field counts only when its value differs (a PATCH that re-sends the same
      value is not a "change" and must not fill the audit trail).
    * only ``request_data`` given   -> a field counts when it was sent.
    """
    names = []
    sent = request_data or {}
    for name in allowed:
        if name not in sent and not (before is not None and after is not None):
            continue
        if before is not None and after is not None:
            if before.get(name) == after.get(name):
                continue
        names.append(name)
    return sorted(names)


def record_change(event: str, request, user, fields: Iterable[str] = (), *, outcome: str = audit.SUCCESS,
                  reason: str = "", **meta):
    """Write one audit row for a sensitive change. NEVER raises, never stores a value, only field names."""
    try:
        names = ",".join(sorted({str(f)[:30] for f in fields}))[:200]
        extra = dict(meta)
        if names:
            extra["fields"] = names
        return audit.record(event, request, outcome=outcome, user=user, reason=reason, **extra)
    except Exception:
        logger.warning("sensitive-change audit failed (event=%s)", event, exc_info=True)
        return None
