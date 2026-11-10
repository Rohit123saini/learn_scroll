"""
post/legacy_chunked.py  (H20 round 1)

Gate for the LEGACY post chunked-upload routes (POST /post/chunked/init/, /complete/, GET /status/<id>/).

The Flutter app stopped calling them (ApiService.createPost / createPostWithChunkedUpload were removed; everything goes
through the upload queue, /post/uploads/...). Old installed app versions still do, so by default the routes keep
working and every call is logged as a deprecation. After a force-update set POST_LEGACY_CHUNKED_ENABLED=false and the
routes answer 410 with the stable code `legacy_chunked_removed` (the old app shows `error` text).

Written by reading the code only - nothing was executed.
"""
import logging

from django.conf import settings
from rest_framework.response import Response

logger = logging.getLogger(__name__)

CODE = "legacy_chunked_removed"


def legacy_chunked_enabled():
    return bool(getattr(settings, "POST_LEGACY_CHUNKED_ENABLED", True))


def legacy_chunked_gate(request, route):
    """-> None when the legacy route may run, else a 410 Response. Never raises."""
    try:
        user_id = getattr(getattr(request, "user", None), "id", None)
        if not legacy_chunked_enabled():
            logger.info("legacy chunked route %s refused (disabled) user=%s", route, user_id)
            return Response(
                {
                    "error": "This app version is too old to upload large files. Please update the app.",
                    "message": "This app version is too old to upload large files. Please update the app.",
                    "code": CODE,
                },
                status=410,
            )
        logger.warning("DEPRECATED legacy chunked route %s used by user=%s (old app version?)", route, user_id)
    except Exception:  # a logging / settings problem must never break an upload
        logger.exception("legacy_chunked_gate failed")
    return None
