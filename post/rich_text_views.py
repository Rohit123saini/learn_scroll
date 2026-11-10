"""
post/rich_text_views.py - POST /post/link-preview/  {"url": "https://..."}  ->  {"preview": {url,title,description,image} | null}

The link-preview card under a caption / in the composer. It reuses message.link_preview.fetch_link_preview, which already
does the hard part: http(s) only, private / loopback / link-local IPs refused (SSRF), DNS pinned, redirects re-checked,
300 KB cap, 4 s timeout, 7-day cache (also for "no preview"). This view only adds: auth, a throttle, URL clean-up and
sanitising of what comes back (title / description are plain text, image must be an http(s) URL).
Never raises: any failure is `{"preview": null}` with 200 - a missing card must not look like an error.
"""
import logging
from urllib.parse import urlparse

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .rich_text import first_url, sanitize_rich_text

logger = logging.getLogger(__name__)

URL_MAX = 2000


class LinkPreviewThrottle(UserRateThrottle):
    scope = "post_link_preview"
    rate = "60/min"


def _http_url(value):
    value = str(value or "").strip()
    p = urlparse(value)
    return value if p.scheme in ("http", "https") and p.hostname and len(value) <= URL_MAX else ""


class LinkPreviewAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [LinkPreviewThrottle]

    @extend_schema(summary="Link preview card for a URL in a caption", request=OpenApiTypes.OBJECT,
                   responses={200: OpenApiTypes.OBJECT}, tags=["Rich text"])
    def post(self, request):
        raw = request.data.get("url") or ""
        url = _http_url(raw) or _http_url(first_url(str(raw)))
        if not url:
            return Response({"preview": None})
        try:
            from message.link_preview import fetch_link_preview

            data = fetch_link_preview(url)
        except Exception:
            logger.exception("link-preview failed")
            return Response({"preview": None})
        if not data:
            return Response({"preview": None})
        image = _http_url(data.get("image"))
        return Response({
            "preview": {
                "url": _http_url(data.get("url")) or url,
                "title": sanitize_rich_text(data.get("title"))[:200],
                "description": sanitize_rich_text(data.get("description"))[:400],
                "image": image or None,
            }
        })
