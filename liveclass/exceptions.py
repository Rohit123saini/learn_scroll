# liveclass/exceptions.py
"""
Standardised error envelope for the whole liveclass API.

WHY THIS FILE EXISTS (production/UX gap this closes):
    Right now, different failure paths in this app return different JSON
    shapes to the client:
      - PermissionDenied()                 -> {"detail": "..."}
      - ValidationError("plain string")    -> ["plain string"]  (a bare list!)
      - ValidationError({"field": "..."})  -> {"field": ["..."]}
      - an unhandled IntegrityError/etc.   -> DRF's generic 500 HTML/JSON,
                                               shape depends on DEBUG
    A frontend (the Flutter app referenced in the docstrings elsewhere in
    this codebase) has to special-case every one of those shapes just to
    show the user a single error message — that's exactly the kind of
    inconsistency that makes an API feel unfinished. Every error response
    from this app should look the same to a client, always:

        {
          "detail": "Human-readable message.",
          "code": "validation_error",        # stable, machine-matchable
          "errors": {"field": ["..."]}        # present only for field errors
        }

HOW TO WIRE IT UP (nothing else in this app needs to change):
    In settings.py:
        REST_FRAMEWORK = {
            ...
            "EXCEPTION_HANDLER": "liveclass.exceptions.liveclass_exception_handler",
        }
That's it — DRF calls this for every exception raised inside a view/
viewset, including everything already in views.py (PermissionDenied,
NotFound, ValidationError) and anything DRF's default handler already
covers (auth failures, throttling, method-not-allowed, unhandled
IntegrityError etc., once DEBUG=False).
"""

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions as drf_exceptions
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

# NOTE (fix — LiveKit errors were bypassing this file entirely): LiveKitError
# used to be a bare `Exception`, so every `except LiveKitError as exc:` in
# views.py caught it and hand-built a Response({"detail": str(exc)},
# status=...) directly — never raised, so it never reached this handler and
# never got a "code" field. LiveKitError is now a `rest_framework.exceptions.
# APIException` subclass (see livekit_utils.py), so it's raised like any
# other DRF exception and flows through `drf_default_handler` below just
# like PermissionDenied/ValidationError/etc. It only needs an entry here so
# `_code_for` gives it a real code instead of falling back to "error".
from .livekit_utils import LiveKitError

logger = logging.getLogger(__name__)

# Stable, machine-matchable codes a frontend can switch on instead of
# string-matching `detail` (which is meant for humans and may be reworded).
_CODE_BY_EXC = {
    drf_exceptions.ValidationError: "validation_error",
    drf_exceptions.PermissionDenied: "permission_denied",
    drf_exceptions.NotFound: "not_found",
    drf_exceptions.AuthenticationFailed: "authentication_failed",
    drf_exceptions.NotAuthenticated: "not_authenticated",
    drf_exceptions.Throttled: "throttled",
    drf_exceptions.MethodNotAllowed: "method_not_allowed",
    drf_exceptions.ParseError: "parse_error",
    LiveKitError: "livekit_error",
}


def _code_for(exc) -> str:
    for exc_type, code in _CODE_BY_EXC.items():
        if isinstance(exc, exc_type):
            return code
    return "error"


def _first_message(value, field_name=None):
    """Descend into a DRF error-detail structure and return the first
    human-readable leaf message, as a plain string.

    NOTE (fix — this used to be `data[first_field][0]` inline, which
    assumed every field's error value was a flat list of strings. DRF
    doesn't guarantee that: a nested/writable serializer field produces a
    nested dict (e.g. {"user": {"email": ["invalid"]}}), and a
    ListSerializer (many=True) produces a LIST of per-item dicts (e.g.
    [{}, {"title": ["required"]}]). Indexing either of those with [0] the
    old way raised KeyError/TypeError — INSIDE this exception handler,
    which meant the client got a raw unhandled 500 instead of the clean
    envelope this file exists to guarantee. Nothing in the current
    serializers.py triggers this today (no writable nested serializers),
    but the next bulk/nested endpoint added would have hit it silently.
    This recurses through dict/list nesting of arbitrary depth so that
    can't happen.
    """
    if isinstance(value, dict):
        # ErrorDetail dicts preserve insertion order (Python 3.7+) — the
        # first key is whichever field DRF's serializer validated first.
        next_field = next(iter(value), None)
        if next_field is None:
            return "Validation failed."
        return _first_message(value[next_field], field_name=next_field)
    if isinstance(value, (list, tuple)):
        if not value:
            return "Validation failed."
        return _first_message(value[0], field_name=field_name)
    # Leaf value — a plain string or DRF's ErrorDetail (str subclass).
    text = str(value)
    # non_field_errors is DRF's internal bookkeeping name, not something a
    # user should see prefixed onto their message; every other field name
    # is genuinely useful context ("email: This field is required.").
    if field_name and field_name != "non_field_errors":
        return f"{field_name}: {text}"
    return text


def liveclass_exception_handler(exc, context):
    """Wraps DRF's default handler; normalises the response body shape and
    catches a couple of exception types DRF's default handler doesn't
    convert to a clean HTTP response on its own (Django's own
    PermissionDenied/Http404, and IntegrityError from a race an explicit
    check upstream didn't catch)."""

    # Translate Django's own exceptions to DRF equivalents first, same as
    # DRF's default handler does, so they flow through the branch below.
    if isinstance(exc, Http404):
        exc = drf_exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = drf_exceptions.PermissionDenied()

    response = drf_default_handler(exc, context)

    if response is None:
        # Not a DRF-recognised exception. An IntegrityError slipping through
        # here means some write path didn't pre-check a constraint (unique
        # pair, FK, etc.) — log it with the view name so it's traceable,
        # and hand the client a clean 400/409 instead of a bare 500.
        if isinstance(exc, IntegrityError):
            view = context.get("view")
            logger.exception(
                "Unhandled IntegrityError in %s — add an explicit pre-check "
                "for this constraint instead of relying on this fallback.",
                getattr(view, "__class__", type(exc)).__name__,
            )
            return Response(
                {
                    "detail": "This action conflicts with existing data (e.g. a duplicate entry).",
                    "code": "conflict",
                },
                status=status.HTTP_409_CONFLICT,
            )
        # Truly unexpected (bug) — let it propagate so Django's own error
        # reporting (logging/Sentry/whatever's configured) still fires;
        # DEBUG=False in production turns this into a generic 500 for the
        # client, which is the right behavior for something this handler
        # doesn't know how to describe safely.
        return None

    data = response.data

    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        # {"detail": "..."} shape (PermissionDenied, NotFound, etc.)
        normalised = {"detail": str(data["detail"]), "code": _code_for(exc)}
    elif isinstance(data, list):
        # ValidationError("plain string") or ValidationError(["a", "b"])
        # serializes to a bare list — flatten to one human-readable string.
        normalised = {"detail": " ".join(str(item) for item in data), "code": "validation_error"}
    else:
        # Field-level ValidationError: {"field": ["msg", ...], ...} — or,
        # with a nested/list-of-dicts serializer, something deeper. See
        # _first_message()'s docstring above for why this can't just index
        # data[first_field][0] directly.
        normalised = {
            "detail": _first_message(data),
            "code": "validation_error",
            "errors": data,
        }

    response.data = normalised
    return response