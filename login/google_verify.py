"""
login/google_verify.py  (T1)

Google ID-token verification - EK jagah. `GoogleAuthView` (login/signup) aur `connected_accounts.verify_google_id_token`
(link) dono yahi use karte hain.

Pehle kya toot-ta tha
  * `google_requests.Request()` bina timeout ke chalta tha - Google ke certs fetch karte waqt network atak jaye
    (dev PC pe IPv6 / DNS slow) to worker latak jata tha ("took too long to shut down and was killed").
  * Har login pe certs dobara fetch hote the.
  * Sirf `ValueError` pakda jata tha; `TransportError` / timeout pe 500 ya hang.
  * Sirf ek `GOOGLE_CLIENT_ID` chalta tha - web / android / ios ke token ka `aud` alag hota hai.

Ab
  * har HTTP call pe timeout (`GOOGLE_VERIFY_TIMEOUT`, default 5 s),
  * Google ke certs ka process-level cache (`GOOGLE_CERTS_CACHE_SECONDS`, default 1 ghanta),
  * `clock_skew_in_seconds=10` (phone / PC ki ghadi thodi aage-peeche ho to "Token used too early" na aaye),
  * network problem -> `GoogleVerifyError("google_unreachable", 503)`; bad token -> `("google_token_invalid", 401)`,
  * `GOOGLE_CLIENT_IDS` (comma list) = web + android + ios audience; purana `GOOGLE_CLIENT_ID` fallback.
"""
from __future__ import annotations

import logging
import threading
import time

import requests
from django.conf import settings
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 5
DEFAULT_CERTS_CACHE_SECONDS = 3600
CLOCK_SKEW_SECONDS = 10

MSG_UNREACHABLE = "Google se connect nahi ho paya, dobara try karo."
MSG_INVALID = "Invalid Google token"
MSG_NOT_CONFIGURED = "Google Sign-In is not configured on the server."


class GoogleVerifyError(Exception):
    """Machine code + HTTP status; callers turn it into their own error envelope."""

    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# ------------------------------------------------------------------ config
def client_ids() -> list[str]:
    """`GOOGLE_CLIENT_IDS` (comma list / list) + purana `GOOGLE_CLIENT_ID`, duplicates hata ke."""
    raw = getattr(settings, "GOOGLE_CLIENT_IDS", None) or []
    if isinstance(raw, str):
        raw = raw.split(",")
    ids = [str(x).strip() for x in raw if str(x).strip()]
    single = str(getattr(settings, "GOOGLE_CLIENT_ID", "") or "").strip()
    if single and single not in ids:
        ids.append(single)
    return ids


def is_configured() -> bool:
    return bool(client_ids())


def _timeout() -> float:
    return float(getattr(settings, "GOOGLE_VERIFY_TIMEOUT", DEFAULT_TIMEOUT_SECONDS) or DEFAULT_TIMEOUT_SECONDS)


def _cache_ttl() -> float:
    value = getattr(settings, "GOOGLE_CERTS_CACHE_SECONDS", DEFAULT_CERTS_CACHE_SECONDS)
    return float(DEFAULT_CERTS_CACHE_SECONDS if value is None else value)


# ------------------------------------------------------------------ transport: timeout + certs cache
_certs_lock = threading.Lock()
_certs_cache: dict[str, tuple[float, bytes, dict]] = {}


class _CachedResponse:
    """Minimum jo `google.oauth2.id_token._fetch_certs` padhta hai: `.status`, `.data`, `.headers`."""

    def __init__(self, data: bytes, headers: dict):
        self.status = 200
        self.data = data
        self.headers = headers


def clear_certs_cache() -> None:
    with _certs_lock:
        _certs_cache.clear()


class _TimeoutRequest(google_requests.Request):
    """`google.auth.transport.requests.Request` + (a) HAR call pe timeout, (b) certs GET ka TTL cache."""

    def __init__(self, timeout: float | None = None):
        super().__init__(session=requests.Session())
        self._timeout = timeout or _timeout()

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        # google-auth ka default timeout 120 s hai; hum hamesha chhota wala use karte hain.
        effective = min(float(timeout), self._timeout) if timeout else self._timeout
        cacheable = method.upper() == "GET" and "googleapis.com" in url and _cache_ttl() > 0
        if cacheable:
            with _certs_lock:
                hit = _certs_cache.get(url)
            if hit and time.monotonic() - hit[0] < _cache_ttl():
                return _CachedResponse(hit[1], hit[2])
        response = super().__call__(url, method=method, body=body, headers=headers, timeout=effective, **kwargs)
        if cacheable and getattr(response, "status", None) == 200:
            with _certs_lock:
                _certs_cache[url] = (time.monotonic(), response.data, dict(getattr(response, "headers", {}) or {}))
        return response


# ------------------------------------------------------------------ verify
_NETWORK_ERRORS = (
    google_exceptions.TransportError,
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.RequestException,
    ConnectionError,
    TimeoutError,
)


def _verify_once(token: str, audience):
    return google_id_token.verify_oauth2_token(
        token, _TimeoutRequest(), audience, clock_skew_in_seconds=CLOCK_SKEW_SECONDS,
    )


def verify_google_token(raw) -> dict:
    """Verified ID-token claims (`sub`, `email`, `aud` ...) ya `GoogleVerifyError`.

    Network / timeout / Google 5xx -> 503 `google_unreachable` (user ki galti nahi, dobara try kar sakta hai).
    Token kharab / expire / alag audience -> 401 `google_token_invalid`.
    """
    ids = client_ids()
    if not ids:
        raise GoogleVerifyError("google_not_configured", MSG_NOT_CONFIGURED, 500)
    if not isinstance(raw, str) or not raw.strip():
        raise GoogleVerifyError("google_token_invalid", MSG_INVALID, 401)
    token = raw.strip()
    audience = ids[0] if len(ids) == 1 else ids

    try:
        try:
            return _verify_once(token, audience)
        except ValueError as exc:
            # Google ne keys rotate ki hon to cached certs me naya `kid` nahi milta: cache saaf karke ek baar aur.
            if "certificate for key id" in str(exc).lower():
                clear_certs_cache()
                return _verify_once(token, audience)
            raise
    except GoogleVerifyError:
        raise
    except _NETWORK_ERRORS as exc:
        logger.warning("google token verify: Google unreachable (%s: %s)", type(exc).__name__, exc)
        raise GoogleVerifyError("google_unreachable", MSG_UNREACHABLE, 503) from None
    except ValueError:
        raise GoogleVerifyError("google_token_invalid", MSG_INVALID, 401) from None
    except google_exceptions.GoogleAuthError as exc:
        # TransportError ke alawa baaki GoogleAuthError (malformed / refresh) - token ya certs ka masla.
        logger.info("google token verify failed: %s: %s", type(exc).__name__, exc)
        raise GoogleVerifyError("google_token_invalid", MSG_INVALID, 401) from None


def check_configuration() -> None:
    """T1.5 - startup check: kuch bhi configured na ho to warning (login/apps.py ready() se)."""
    if not is_configured():
        logger.warning(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_IDS is empty in .env - Google sign-in will answer 500 "
            "'google_not_configured'. Set the Web client id (and the Android / iOS ids, comma separated)."
        )
