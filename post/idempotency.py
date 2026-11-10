"""
post/idempotency.py  (H20)

Duplicate-submit guard. A client that sends an `Idempotency-Key` header on a create request gets exactly ONE object,
however many times the request is repeated (double tap, lost response + retry, queue replay after a crash):

    first request   -> runs the view, stores the answer when it is a success (2xx)
    same key again  -> the stored answer is replayed (header `Idempotent-Replay: true`), nothing is created
    same key while the first is still running -> 409 {"code": "request_in_progress"}
    a failed first request (4xx / 5xx / exception) leaves no record, so the same key can be retried

No header = the old behaviour, untouched. Applied (decorator below) to POST /post/create/, POST /post/stories/create/
and POST /post/resources/. The chunked UploadSession flow has its own key (UploadSession.idempotency_key).

Keys are scoped per (user, scope), so two users (or two endpoints) can never collide.
"""
import functools
import json
import logging
import re
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.response import Response

from .models import IdempotencyRecord

logger = logging.getLogger(__name__)

IDEMPOTENCY_META_KEY = "HTTP_IDEMPOTENCY_KEY"
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
# A first request that never finished (worker killed) stops blocking the key after this long.
IN_PROGRESS_STALE_AFTER = timedelta(minutes=2)
RECORD_TTL = timedelta(hours=48)


def json_safe(data):
    """`Response.data` (ReturnDict / lists / UUIDs / datetimes) -> plain JSON values for a JSONField."""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def _claim(user, scope, key):
    """-> (record, created). `created` False = somebody already owns this key."""
    try:
        with transaction.atomic():
            return IdempotencyRecord.objects.get_or_create(user=user, scope=scope, key=key)
    except IntegrityError:  # lost a race against a parallel request with the same key
        return IdempotencyRecord.objects.get(user=user, scope=scope, key=key), False


def _error(code, message, http_status):
    return Response({"success": False, "code": code, "message": message}, status=http_status)


def idempotent(scope):
    """Decorator for an APIView method `post(self, request)`. Put it ABOVE `@transaction.atomic` so the claim
    commits on its own and survives a rollback of the view's own transaction."""

    def decorate(method):
        @functools.wraps(method)
        def wrapper(self, request, *args, **kwargs):
            key = (request.META.get(IDEMPOTENCY_META_KEY) or "").strip()
            if not key:
                return method(self, request, *args, **kwargs)
            if not KEY_RE.match(key):
                return _error("idempotency_key_invalid",
                              "Idempotency-Key must be 8-64 characters: letters, digits, . _ : -", 400)

            record, created = _claim(request.user, scope, key)
            if not created:
                if record.state == IdempotencyRecord.STATE_DONE and record.response_status:
                    response = Response(record.response_body, status=record.response_status)
                    response["Idempotent-Replay"] = "true"
                    return response
                if timezone.now() - record.created_at < IN_PROGRESS_STALE_AFTER:
                    return _error("request_in_progress",
                                  "This request is already being processed. Please wait a moment.", 409)
                # stale in-progress record (the first request died): take the key over
                IdempotencyRecord.objects.filter(pk=record.pk).update(created_at=timezone.now())

            try:
                response = method(self, request, *args, **kwargs)
            except Exception:
                IdempotencyRecord.objects.filter(pk=record.pk).delete()
                raise

            status_code = getattr(response, "status_code", 500)
            if 200 <= status_code < 300:
                try:
                    IdempotencyRecord.objects.filter(pk=record.pk).update(
                        state=IdempotencyRecord.STATE_DONE,
                        response_status=status_code,
                        response_body=json_safe(getattr(response, "data", None)),
                    )
                except Exception:  # storing the answer must never turn a created post into an error
                    logger.warning("idempotency: could not store the answer for %s:%s", scope, key, exc_info=True)
                    IdempotencyRecord.objects.filter(pk=record.pk).delete()
            else:
                IdempotencyRecord.objects.filter(pk=record.pk).delete()  # retry with the same key stays possible
            return response

        return wrapper

    return decorate
