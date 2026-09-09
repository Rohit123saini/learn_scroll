# liveclass/throttles.py
"""
Task 9 — dedicated throttle(s) for this app.

This file didn't exist before Task 9 — it's created here purely because
`ClassSessionViewSet.parent_join` needs a throttle keyed by IP rather
than by user (see ParentJoinIPThrottle's own docstring for why), and
DRF's `ScopedRateThrottle` (used everywhere else in this app, e.g.
`session_join`/`session_token` on this same ViewSet) always keys off
`request.user`, which is `AnonymousUser` here — so the everyday
ScopedRateThrottle pattern doesn't fit this one endpoint. Same
single-purpose-throttles-file pattern `message/throttles.py` already
uses for its own custom-scoped throttles (`MessageSendIPThrottle`,
`CallInitiateIPThrottle`, ...).

Add any future custom (non-ScopedRateThrottle) throttle classes for this
app here rather than growing views.py's import block one-off at a time.
"""

from rest_framework.throttling import SimpleRateThrottle


class ParentJoinIPThrottle(SimpleRateThrottle):
    """POST /sessions/<id>/parent-join/ is unauthenticated — a parent has
    no platform account, only a signed `parent_token` link, so there's no
    `request.user` to key a per-user throttle off of the way every other
    scoped throttle in this app does (see session_join/session_token
    above it in views.py). Per-IP is the only signal available, and it's
    also the right one: this endpoint's abuse case is brute-forcing or
    replaying a `parent_token` value as fast as possible, which is an
    IP-driven attack regardless of which token is being tried.

    `SimpleRateThrottle.get_cache_key`'s default behaviour already keys
    off `self.get_ident(request)` (client IP, X-Forwarded-For aware)
    whenever `request.user.is_authenticated` is False — which it always
    will be here — so no override is needed beyond setting `scope`.

    Rate lives in settings.py's DEFAULT_THROTTLE_RATES under the
    "session_parent_join_ip" key.
    """

    scope = "session_parent_join_ip"