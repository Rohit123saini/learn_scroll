# login/errors.py
"""
L1 gap 16 -- ONE error envelope for the whole login app.

Before this, a failing login-app call could answer in three different shapes:

    {"status": False, "message": "...", "code": "..."}    (hand-built in views)
    {"field": ["..."]}                                    (raw serializer.errors)
    {"detail": "...", "code": "..."}                      (the global handler)

so the Flutter app had to guess which keys to read. Now EVERY failure from
these views looks like this:

    {
      "detail":  "Human-readable message (English fallback).",
      "code":    "stable_machine_code",     # the app translates THIS (Hindi/English)
      "message": "same text as detail",     # legacy alias -- app builds from before
                                            # this change only know `message`
      "errors":  {"field": ["..."]},        # field-level validation failures only
      "error_codes": {"field": ["password_too_common"]},   # same shape as `errors`, L1 gap 17
      ...extras                             # attempts_left / retry_after / blocking_reasons
    }

``code`` is the contract. ``detail``/``message`` are only a fallback for codes
an older app build doesn't know; the app shows its own localised text per code
(lib/utils/app_error.dart), which is how Hindi/English works without the server
having to guess the user's language.

Two ways to produce it, both giving the same shape:

  * a view builds the failure itself          -> ``api_error()`` / ``retry_error()``
  * a view raises / ``is_valid(raise_exception=True)`` -> the project handler
    (tuitionclass.exceptions) normalises it, ``UnifiedErrorsMixin`` then adds
    the ``message`` alias.

Success bodies are NOT touched by this file.
"""
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from tuitionclass.exceptions import tuitionclass_exception_handler


def error_body(detail, code, **extras):
    """The envelope as a plain dict. ``None`` extras are dropped (``0`` is kept)."""
    detail = str(detail)
    body = {"detail": detail, "code": code, "message": detail}
    body.update({k: v for k, v in extras.items() if v is not None})
    return body


def api_error(detail, code, http_status=status.HTTP_400_BAD_REQUEST, *, headers=None, **extras):
    """A failure response in the unified envelope."""
    response = Response(error_body(detail, code, **extras), status=http_status)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def retry_error(detail, code, retry_after, http_status=status.HTTP_429_TOO_MANY_REQUESTS):
    """A 'try again later' failure: ``retry_after`` in the body AND a Retry-After header."""
    retry_after = int(retry_after)
    return api_error(
        detail,
        code,
        http_status,
        headers={"Retry-After": str(retry_after)},
        retry_after=retry_after,
    )


def login_exception_handler(exc, context):
    """The project handler + the legacy ``message`` alias, so raised errors
    (serializer validation, DRF throttling, auth failures) match ``api_error``."""
    response = tuitionclass_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict):
        detail = response.data.get("detail")
        if isinstance(detail, str):
            response.data.setdefault("message", detail)
        # L1 gap 17 -- field-level failures also say WHICH rule failed, shaped like
        # `errors` ({"password": ["password_too_common"]}), so the app can show its
        # own Hindi/English sentence per rule instead of the server's English text.
        if isinstance(exc, ValidationError) and "errors" in response.data:
            response.data["error_codes"] = exc.get_codes()
    return response


class UnifiedErrorsMixin:
    """Put FIRST in a login view's bases: ``class Foo(UnifiedErrorsMixin, APIView)``."""

    def get_exception_handler(self):
        return login_exception_handler
