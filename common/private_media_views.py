"""T0.3 — `/pmedia/<path>?exp=&sig=` : serves private files to holders of a valid signed URL."""
import mimetypes
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.http import Http404, HttpResponse, HttpResponseRedirect, StreamingHttpResponse
from django.utils._os import safe_join
from django.views.decorators.http import require_http_methods

from .private_media import is_private_path, verify_signature

_INLINE = ("image/", "video/", "audio/")
_CHUNK = 64 * 1024


def _deny(status, reason):
    r = HttpResponse(reason, status=status, content_type="text/plain")
    r["Cache-Control"] = "no-store"
    return r


def _iter(path, start, length):
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            chunk = f.read(min(_CHUNK, left))
            if not chunk:
                break
            yield chunk
            left -= len(chunk)


@require_http_methods(["GET", "HEAD"])
def serve_private_media(request, path):
    state = verify_signature(path, request.GET.get("exp"), request.GET.get("sig"))
    if state == "missing":
        return _deny(401, "Authentication required")
    if state != "ok":
        return _deny(403, "Link invalid or expired")
    if not is_private_path(path):
        raise Http404("Not a private media path")

    # --- S3: bounce to a short-lived presigned URL -------------------------------------
    if getattr(settings, "USE_S3_STORAGE", False):
        from django.core.files.storage import default_storage

        resp = HttpResponseRedirect(default_storage.url(path))
        resp["Cache-Control"] = "private, no-store"
        return resp

    # --- local disk --------------------------------------------------------------------
    try:
        file_path = safe_join(settings.MEDIA_ROOT, path)
    except (SuspiciousFileOperation, ValueError):
        raise Http404("Invalid path")
    if not os.path.isfile(file_path):
        raise Http404("File not found")

    content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    name = os.path.basename(file_path)
    disp = "inline" if (content_type == "application/pdf" or content_type.startswith(_INLINE)) else "attachment"
    disp_header = f"{disp}; filename*=UTF-8''{quote(name)}"

    # nginx: `internal` location does the actual byte-serving (Range, sendfile, no Python).
    accel = getattr(settings, "PRIVATE_MEDIA_ACCEL_PREFIX", "")
    if accel:
        resp = HttpResponse(content_type=content_type)
        resp["X-Accel-Redirect"] = f"{accel.rstrip('/')}/{quote(path)}"
        resp["Content-Disposition"] = disp_header
        resp["Cache-Control"] = "private, max-age=3600"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp

    size = os.path.getsize(file_path)
    start, end, status = 0, size - 1, 200
    m = re.match(r"bytes=(\d*)-(\d*)$", request.META.get("HTTP_RANGE", "").strip())
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
        end = min(end, size - 1)
        if start >= size or start > end:
            r = HttpResponse(status=416)
            r["Content-Range"] = f"bytes */{size}"
            return r
        status = 206
    length = end - start + 1

    resp = StreamingHttpResponse(
        _iter(file_path, start, length) if request.method == "GET" else iter(()),
        status=status,
        content_type=content_type,
    )
    resp["Content-Length"] = str(length)
    resp["Accept-Ranges"] = "bytes"
    if status == 206:
        resp["Content-Range"] = f"bytes {start}-{end}/{size}"
    resp["Content-Disposition"] = disp_header
    resp["Cache-Control"] = "private, max-age=3600"  # NEVER public: shared caches/CDNs must not keep it
    resp["X-Content-Type-Options"] = "nosniff"
    return resp
