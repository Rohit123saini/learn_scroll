# login/login_guard.py
"""
Brute-force protection for password login (L1 gap 6).

Before this, ``Login`` had no lockout at all: only the global anonymous
throttle (20 requests/min per IP), so one account could be guessed at 20 tries
a minute, forever, and a botnet could do it from many IPs.

Three independent failure counters (all in the shared cache -- Redis in
production, see settings.py; same approach as common/admin_site.py):

    scope "ai"  identifier + IP   LOGIN_MAX_FAILURES          (default 5)
                -> the normal "you typed it wrong 5 times" lockout. Keyed on
                   IP as well so a stranger can't lock YOU out of your own
                   account from their phone.
    scope "a"   identifier only   LOGIN_ACCOUNT_MAX_FAILURES  (default 15)
                -> catches a distributed guess (many IPs, one account).
    scope "ip"  IP only           LOGIN_IP_MAX_FAILURES       (default 30)
                -> catches credential stuffing (one IP, many accounts).

Hitting a limit locks that scope for LOGIN_LOCKOUT_SECONDS (default 15 min) and
the response tells the app how long is left (``retry_after`` seconds), so the
UI can show a real countdown instead of "try later".

The counters are keyed on the identifier the user TYPED, whether or not an
account exists, so a lockout (or the lack of one) reveals nothing about which
accounts exist. A locked identifier is rejected even with the correct password;
the owner can still sign in with an OTP or Google, or reset the password
(ResetPasswordView clears the lock via ``LoginGuard.unlock``).

NOTE: the IP comes from DRF's ``get_ident`` which honours
``REST_FRAMEWORK["NUM_PROXIES"]`` -- set the NUM_PROXIES env var to the number
of reverse proxies in front of Django, otherwise a client-supplied
X-Forwarded-For can dodge the per-IP counters.
"""
import hashlib
import logging
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import BaseThrottle

logger = logging.getLogger("security.login")


def _cfg(name, default):
    return int(getattr(settings, name, default))


def _hash(value):
    return hashlib.sha256((value or "").encode()).hexdigest()[:32]


def client_ip(request):
    """Client IP as DRF's throttles see it (proxy-aware via NUM_PROXIES)."""
    try:
        return BaseThrottle().get_ident(request) or "unknown"
    except Exception:  # never let IP detection break a login
        return "unknown"


def incr_with_ttl(key, ttl):
    """Atomic-ish counter: create with TTL on first hit, then increment."""
    if cache.add(key, 1, ttl):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, ttl)
        return 1


class LoginGuard:
    def __init__(self, request, identifier):
        self._ident = _hash((identifier or "").strip().lower())
        self._ip = _hash(client_ip(request))
        self._limits = {
            "ai": (f"{self._ident}:{self._ip}", _cfg("LOGIN_MAX_FAILURES", 5)),
            "a": (self._ident, _cfg("LOGIN_ACCOUNT_MAX_FAILURES", 15)),
            "ip": (self._ip, _cfg("LOGIN_IP_MAX_FAILURES", 30)),
        }
        self._window = _cfg("LOGIN_FAILURE_WINDOW_SECONDS", 900)
        self._lockout = _cfg("LOGIN_LOCKOUT_SECONDS", 900)

    @staticmethod
    def _count_key(scope, suffix):
        return f"loginguard:n:{scope}:{suffix}"

    @staticmethod
    def _lock_key(scope, suffix):
        return f"loginguard:lock:{scope}:{suffix}"

    def retry_after(self):
        """Seconds until this attempt may be made again (0 = not locked)."""
        now = time.time()
        wait = 0
        for scope, (suffix, _limit) in self._limits.items():
            unlock_at = cache.get(self._lock_key(scope, suffix))
            if unlock_at:
                wait = max(wait, int(unlock_at - now + 0.999))
        return max(wait, 0)

    def failure_count(self):
        """Wrong passwords counted so far for this identifier (L1 gap 18: the login
        captcha is asked once this reaches CAPTCHA_LOGIN_AFTER_FAILURES)."""
        counts = [cache.get(self._count_key(scope, self._limits[scope][0])) or 0 for scope in ("ai", "a")]
        return max(int(c) for c in counts)

    def record_failure(self):
        """Count a wrong password. Returns ``(retry_after, attempts_left)``.

        ``retry_after`` > 0 means THIS failure just tripped a lock.
        """
        now = time.time()
        attempts_left = None
        for scope, (suffix, limit) in self._limits.items():
            n = incr_with_ttl(self._count_key(scope, suffix), self._window)
            if scope == "ai":
                attempts_left = max(limit - n, 0)
            if n >= limit:
                cache.set(self._lock_key(scope, suffix), now + self._lockout, self._lockout)
                cache.delete(self._count_key(scope, suffix))
                logger.warning(
                    "login locked: scope=%s ident=%s ip=%s for %ss",
                    scope, self._ident[:8], self._ip[:8], self._lockout,
                )
        return self.retry_after(), attempts_left

    def record_success(self):
        """Correct password: forget this identifier's failures."""
        for scope in ("ai", "a"):
            suffix, _ = self._limits[scope]
            cache.delete(self._count_key(scope, suffix))

    @classmethod
    def unlock(cls, request, identifiers):
        """Clear counters + locks for these identifiers (after a password reset)."""
        ip = _hash(client_ip(request))
        for raw in identifiers:
            if not raw:
                continue
            ident = _hash(str(raw).strip().lower())
            for scope, suffix in (("a", ident), ("ai", f"{ident}:{ip}")):
                cache.delete(cls._count_key(scope, suffix))
                cache.delete(cls._lock_key(scope, suffix))
