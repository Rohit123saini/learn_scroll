"""C5 — long-lived Cache-Control for media served through Django.

Adds `Cache-Control: public, max-age=31536000, immutable` to successful
GET/HEAD responses under MEDIA_URL's path (usually /media/) so a CDN in front
of Django (or nginx proxying to it) caches them for a year.

Fully inert unless settings.MEDIA_CDN_ENABLED is true — one toggle for the
whole C5 feature. Never overrides a Cache-Control header a view already set.

Safe because uploaded filenames are never reused (see settings.py C5 block):
a replaced file gets a new URL, so a stale cached copy can't be served.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed


class MediaCacheControlMiddleware:
    def __init__(self, get_response):
        if not getattr(settings, "MEDIA_CDN_ENABLED", False):
            raise MiddlewareNotUsed  # removed from the chain at startup
        self.get_response = get_response
        # MEDIA_URL is absolute when the CDN is on -> match on its path only.
        path = urlsplit(settings.MEDIA_URL).path or "/media/"
        self.prefix = path if path.endswith("/") else path + "/"
        self.value = settings.MEDIA_CACHE_CONTROL

    def __call__(self, request):
        response = self.get_response(request)
        if (
            request.method in ("GET", "HEAD")
            and request.path.startswith(self.prefix)
            and response.status_code in (200, 206)
            and not response.has_header("Cache-Control")
        ):
            response["Cache-Control"] = self.value
        return response
