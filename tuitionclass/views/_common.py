"""
tuitionclass/views/_common.py

Split out of the former monolithic views.py (T3.2) — see views/__init__.py.
"""

import logging
from rest_framework import pagination


class TuitionClassPagination(pagination.PageNumberPagination):
    """NOTE (production): this project's DRF settings weren't in scope here,
    so there's no visibility into whether DEFAULT_PAGINATION_CLASS is set
    globally. Applied explicitly on the two endpoints most likely to grow
    unbounded over a classroom's lifetime (chat history, coin ledger) so
    neither can return a multi-thousand-row response in one call regardless
    of the project-wide default. If a global default IS already configured
    in settings.py, this override is harmless (page_size still applies);
    if it isn't, every other list endpoint in this file should get one too
    — see the settings snippet delivered alongside this file.
    """

    page_size = 30
    page_size_query_param = "page_size"
    max_page_size = 100


def _safe_delay(task, *args, **kwargs):
    """Queue a Celery task without ever letting a broker problem hang or
    fail the request that triggered it.

    NOTE (fix): every notify_*.delay() call site in this file used to call
    .delay() directly and unguarded — unlike the equivalent calls in
    signals.py, which are always wrapped in try/except (see that module's
    docstring, "PRODUCTION-HARDENING NOTES"). If the Celery broker
    (Redis/RabbitMQ) isn't reachable — common in local/dev setups where the
    broker just isn't running — .delay() blocks on the connection attempt
    (or raises) INSIDE the request-response cycle, since these calls aren't
    deferred to transaction.on_commit() either. From the client's
    perspective this looks exactly like a hung/spinning request even though
    the actual DB write (the join request, the purchase, the accept) already
    landed successfully before this call — a broker outage should never be
    able to make a successful write look like a failed/stuck one. Mirrors
    signals.py's existing philosophy: notification dispatch is best-effort
    and must never block or break the caller.
    """
    try:
        task.delay(*args, **kwargs)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to queue Celery task %s (broker unreachable?) — request still succeeds.",
            getattr(task, "name", task),
        )


def _safe_apply_async(task, args=None, countdown=None):
    """Same contract as _safe_delay above, but for a task that needs to run
    LATER (e.g. expire_trial_access) rather than immediately — .delay()
    doesn't support that, .apply_async(countdown=...) does."""
    try:
        task.apply_async(args=args or [], countdown=countdown)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to schedule Celery task %s (broker unreachable?) — request still succeeds.",
            getattr(task, "name", task),
        )


def _safe_broadcast_to_user(user_id, event_type, data):
    """Push a realtime event to one user's own WebSocket channel, the same
    best-effort/never-block-the-request spirit as _safe_delay() above.

    CONFIRMED (realtime fix pass): `broadcast_to_user()` now exists in
    `realtime.py`, backed by `UserConsumer` in `consumers.py` and the
    `ws/tuitionclass/user/` route in `routing.py` — group naming/routing
    verified against those real files, not guessed. The try/except below
    is kept regardless (same best-effort contract every other realtime/
    notification call in this app follows, e.g. _safe_delay() above,
    notifications.send_notification()) — a channel-layer hiccup should
    still degrade to "no live badge push, poll/reopen still works"
    rather than ever breaking join-request creation/accept/reject or
    staff promotion.
    """
    try:
        from ..realtime import broadcast_to_user

        broadcast_to_user(user_id, event_type, data)
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to push realtime event %r to user %s (broadcast_to_user missing/unreachable?) — "
            "request still succeeds; frontend falls back to poll/reopen.",
            event_type,
            user_id,
        )


def _is_truthy(value) -> bool:
    """Parse a query-string flag like ?mine=1 / ?mine=true properly.

    NOTE (fix): several endpoints below used bare `if request.query_params.get("flag")`
    to decide a boolean, which means ANY non-empty value — including the very
    literal string "false" or "0" — was treated as True. `?mine=false` silently
    behaved like `?mine=true`, and `?include_expired=false` silently behaved
    like `?include_expired=true`. This helper treats only "0"/"false"/"no"/""
    (case-insensitive) as False, absence of the param as False, and everything
    else — including bare presence, e.g. "?mine" — as True.
    """
    if value is None:
        return False
    return str(value).strip().lower() not in ("", "0", "false", "no")
