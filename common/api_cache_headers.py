"""P-C8 -- CDN safety net: API responses are never shared-cached by accident.

Adds `Cache-Control: private, no-store` to a response ONLY when
  * it is a 2xx/3xx/4xx response without its own Cache-Control header, and
  * its path is not under STATIC_URL / MEDIA_URL / pmedia (those have their own policy, see common/media_cache.py), and
  * it is not a 5xx (those stay untouched), and
  * API_CACHE_DEFAULT_PRIVATE is true (default).
Views that WANT caching just set their own Cache-Control (public SEO pages, ETag feeds...) -- they are never overridden.
Also adds `Vary: Authorization` so a cache that ignores no-store still keeps users apart.
"""
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.utils.cache import patch_vary_headers


class ApiCacheSafetyMiddleware:
    def __init__(self, get_response):
        if not getattr(settings, "API_CACHE_DEFAULT_PRIVATE", True):
            raise MiddlewareNotUsed
        self.get_response = get_response
        skip = {"/static/", "/media/", "/pmedia/", "/.well-known/", "/robots.txt", "/sitemap.xml"}
        for url in (getattr(settings, "STATIC_URL", ""), getattr(settings, "MEDIA_URL", "")):
            path = urlsplit(url or "").path
            if path:
                skip.add(path if path.endswith("/") else path + "/")
        self.skip = tuple(skip)
        self.public = tuple(getattr(settings, "API_CACHE_PUBLIC_PREFIXES", ("/p/", "/u/", "/classroom/", "/test-series/",
                                                                         "/certificate/", "/assignments/", "/doubts/")))

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if (
            response.status_code < 500
            and not response.has_header("Cache-Control")
            and not path.startswith(self.skip)
            and not path.startswith(self.public)
        ):
            response["Cache-Control"] = "private, no-store"
            patch_vary_headers(response, ("Authorization",))
        return response
