# LearnScroll/log_filters.py
"""
X6b -- keep credentials out of log lines.

Old app builds still open sockets with ``/ws/chat/1/?token=<JWT>``; any access / handshake line that prints the request
path would then hold a live token. This filter rewrites such lines BEFORE any handler formats them:
  * ``?token=...`` / ``&ticket=...`` / ``access_token=...`` / ``refresh=...``  -> value replaced by [redacted]
  * anything shaped like a JWT (``eyJ<b64>.<b64>.<b64>``)                       -> [redacted-jwt]
It is attached to the ``console`` handler in settings.LOGGING, so django / daphne / uvicorn / app loggers all pass
through it. It never raises (a logging bug must not break a request) and leaves ``exc_info`` untouched.
P-B2 additions:
  * install_on_server_loggers()  -- the ASGI server's OWN loggers (uvicorn.access, daphne.*, hypercorn.*) often have their
    own handler, so the console-handler filter above never sees them; the filter is attached to the logger itself.
  * scrub_sentry_event()         -- same redaction for Sentry events / breadcrumbs (request url, query_string, messages).
Reverse-proxy access logs (nginx / ALB) are NOT covered here -- see deploy/nginx/ws-log-redaction.conf.example.
Daphne's `--access-log FILE` is a plain file writer, not a logger: do not enable it (or put it behind that nginx rule).
"""
import logging
import re

_PARAM = re.compile(r"([?&](?:token|ticket|access_token|access|refresh|refresh_token)=)[^&\s\"'#]+", re.IGNORECASE)
_JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}")


def redact(text: str) -> str:
    text = _PARAM.sub(r"\1[redacted]", text)
    return _JWT.sub("[redacted-jwt]", text)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg = cleaned
                record.args = ()
        except Exception:  # never let the filter break logging
            pass
        return True


# --- P-B2 ----------------------------------------------------------------------------------------------------------
# Loggers an ASGI server writes request / handshake lines to. A missing logger is harmless (getLogger creates it).
SERVER_LOGGERS = (
    "uvicorn", "uvicorn.access", "uvicorn.error",
    "daphne", "daphne.server", "daphne.ws_protocol", "daphne.http_protocol",
    "hypercorn.access", "hypercorn.error",
    "gunicorn.access", "gunicorn.error",
    "channels", "django.server",
)


def install_on_server_loggers(names=SERVER_LOGGERS) -> int:
    """Attach RedactSecretsFilter to each logger once (idempotent). Returns how many loggers got it now.
    Called from LearnScroll/asgi.py, i.e. AFTER the server has configured its own logging."""
    added = 0
    for name in names:
        lg = logging.getLogger(name)
        if not any(isinstance(f, RedactSecretsFilter) for f in lg.filters):
            lg.addFilter(RedactSecretsFilter())
            added += 1
    return added


_SENSITIVE_KEYS = frozenset({"token", "ticket", "access_token", "access", "refresh", "refresh_token"})


def _clean(value):
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_clean(v) for v in value)
    return value


def scrub_sentry_event(event):
    """before_send / before_breadcrumb hook: the same redaction over the whole event (urls, query strings, messages).
    Never raises, always returns the event (a scrubbing bug must not drop an error report)."""
    try:
        qs = (event.get("request") or {}).get("query_string") if isinstance(event, dict) else None
        cleaned = _clean(event)
        if isinstance(qs, str) and qs:
            # a bare query string has no leading `?`, so the URL regex above cannot see it
            cleaned["request"]["query_string"] = redact("?" + qs)[1:]
        elif isinstance(qs, (list, tuple)):  # Sentry also sends [[key, value], ...]
            cleaned["request"]["query_string"] = [
                [k, "[redacted]"] if str(k).lower() in _SENSITIVE_KEYS else [k, v] for k, v in qs
            ]
        elif isinstance(qs, dict):
            cleaned["request"]["query_string"] = {
                k: ("[redacted]" if str(k).lower() in _SENSITIVE_KEYS else v) for k, v in qs.items()
            }
        return cleaned
    except Exception:
        return event
