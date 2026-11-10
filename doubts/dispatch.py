"""
doubts/dispatch.py — call a SOURCE app's own DRF view in-process.

Why: every doubt system already enforces its own permissions, throttles,
notifications and realtime broadcasts inside its view. Re-implementing any
of that here would create a second copy that drifts (the exact failure
shape `check_config_drift` exists for). Instead the hub builds a synthetic
request for the already-authenticated user and runs the original view.

Auth is injected with `force_authenticate` (the hub already authenticated
the caller via JWT), the caller's IP is copied so IP-keyed throttles still
see the real client, and query-string/JSON body are passed explicitly.
The original view's response (status + data) is returned untouched — the
hub never swallows or rewrites a source app's validation error.
"""
from __future__ import annotations

from rest_framework.test import APIRequestFactory, force_authenticate


def call_view(view, *, user, method: str, path: str, data=None, query=None,
              origin_request=None, **view_kwargs):
    """Run `view` (an `as_view()` callable) and return its DRF `Response`.

    `origin_request` is the hub's own request; only REMOTE_ADDR / the
    forwarded-for header are copied from it."""
    factory = APIRequestFactory()
    extra = {}
    if origin_request is not None:
        meta = getattr(origin_request, "META", {}) or {}
        for key in ("REMOTE_ADDR", "HTTP_X_FORWARDED_FOR", "HTTP_USER_AGENT"):
            if meta.get(key):
                extra[key] = meta[key]

    method = method.lower()
    if method == "get":
        request = factory.get(path, query or {}, **extra)
    else:
        if query:
            from urllib.parse import urlencode
            path = f"{path}?{urlencode(query)}"
        request = getattr(factory, method)(path, data or {}, format="json", **extra)
    force_authenticate(request, user=user)
    return view(request, **view_kwargs)
