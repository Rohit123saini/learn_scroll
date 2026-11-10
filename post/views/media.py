# post/views/media.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

import mimetypes
import os
import re
from django.http import (
    Http404,
    HttpResponse,
    StreamingHttpResponse,
)
from django.core.exceptions import SuspiciousFileOperation
from django.utils._os import safe_join
from django.conf import settings


# ===================== MEDIA SERVE WITH RANGE =====================
# TASK 25 — dev-only fallback. Only ever mounted when
# `settings.SERVE_MEDIA_VIA_DJANGO` is True (see post/urls.py and
# settings.py's SERVE_MEDIA_VIA_DJANGO comment) — in production, media is
# served by nginx straight off disk (deploy/nginx.conf) or by S3/CloudFront
# (deploy/S3_CLOUDFRONT_SETUP.md), never through this view.
def serve_media_with_range(request, path):
    if settings.USE_S3_STORAGE:
        # Shouldn't be reachable — settings.py raises ImproperlyConfigured
        # at startup if SERVE_MEDIA_VIA_DJANGO=true and USE_S3_STORAGE=true
        # together. Fail loudly instead of a confusing "file not found" if
        # someone still manages to wire this route in anyway (e.g. a
        # `re_path` added by hand elsewhere): with S3 storage active,
        # MEDIA_ROOT is not where uploaded files live.
        raise Http404("Media is served from S3/CloudFront when USE_S3_STORAGE=True, not local disk.")
    # T0.3 (B3): private uploads are only reachable through signed /pmedia/ URLs — never here,
    # not even for a logged-in user, and never with the 1-year public cache below. Pure string
    # check, so it runs before any filesystem access (no existence oracle).
    from common.private_media import is_private_path
    if is_private_path(path):
        return HttpResponse('Forbidden', status=403, content_type='text/plain')
    # 🔒 T1.11 (R11) — path resolution goes through Django's safe_join():
    # it normalises the joined path and raises SuspiciousFileOperation if the
    # result escapes MEDIA_ROOT (`..`, absolute paths, symlink-free tricks,
    # Windows drive letters, …). That replaces the old substring check
    # (`'..' in path`), which was both bypassable and rejected legitimate
    # names like "a..b.mp4". Still runs BEFORE any filesystem access, so it
    # can't be used as an existence oracle.
    try:
        file_path = safe_join(settings.MEDIA_ROOT, path)
    except (SuspiciousFileOperation, ValueError):
        raise Http404("Invalid path")
    if not os.path.isfile(file_path):
        raise Http404("File not found")
    content_type, _ = mimetypes.guess_type(file_path)
    content_type = content_type or 'application/octet-stream'
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    range_header = request.META.get('HTTP_RANGE', '')
    if range_header and (content_type.startswith('video/') or content_type.startswith('audio/')):
        # RFC 7233 single byte-range: `bytes=a-b`, `bytes=a-` and the
        # suffix form `bytes=-N` (= "the last N bytes", used by players to
        # read an MP4's moov atom at the end of the file). Previously the
        # suffix form didn't match the regex at all, so the whole file was
        # returned with 200, and `a-b` with b >= file_size produced a bogus
        # Content-Range / Content-Length past EOF.
        range_match = re.match(r'^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*(?:,.*)?$', range_header)
        if range_match and (range_match.group(1) or range_match.group(2)):
            start_s, end_s = range_match.group(1), range_match.group(2)
            if start_s == '':
                # suffix range: last N bytes
                suffix_len = int(end_s)
                if suffix_len == 0 or file_size == 0:
                    first_byte, last_byte = file_size, file_size  # forces 416 below
                else:
                    first_byte = max(file_size - suffix_len, 0)
                    last_byte = file_size - 1
            else:
                first_byte = int(start_s)
                last_byte = int(end_s) if end_s else file_size - 1
                # clamp an end past EOF to the last real byte
                last_byte = min(last_byte, file_size - 1)
            if first_byte >= file_size or first_byte > last_byte:
                bad = HttpResponse(status=416)
                bad['Content-Range'] = f'bytes */{file_size}'
                return bad
            length = last_byte - first_byte + 1
            def file_gen():
                with open(file_path, 'rb') as f:
                    f.seek(first_byte)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(remaining, 65536))
                        if not chunk:
                            break
                        yield chunk
                        remaining -= len(chunk)
            response = StreamingHttpResponse(file_gen(), status=206, content_type=content_type)
            response['Content-Range'] = f'bytes {first_byte}-{last_byte}/{file_size}'
            response['Content-Length'] = str(length)
            response['Accept-Ranges'] = 'bytes'
            response['Content-Disposition'] = f'inline; filename="{file_name}"'
            response['Cache-Control'] = 'public, max-age=31536000'
            return response
    def file_gen():
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(8192)
                if not chunk:
                    break
                yield chunk
    response = StreamingHttpResponse(file_gen(), content_type=content_type)
    response['Content-Length'] = str(file_size)
    response['Accept-Ranges'] = 'bytes'
    doc_extensions = ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.txt']
    is_doc = any(file_name.lower().endswith(ext) for ext in doc_extensions)
    if content_type == 'application/pdf':
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
    elif is_doc:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
        response['Content-Type'] = 'application/octet-stream'
    elif content_type.startswith(('image/', 'video/', 'audio/')):
        response['Content-Disposition'] = f'inline; filename="{file_name}"'
        response['Cache-Control'] = 'public, max-age=31536000'
    else:
        response['Content-Disposition'] = f'attachment; filename="{file_name}"'
    response['X-Content-Type-Options'] = 'nosniff'
    return response
