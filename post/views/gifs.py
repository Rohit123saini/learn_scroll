# post/views/gifs.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.decorators import permission_classes
from django.conf import settings
from .. import story_gifs

# `requests` is guarded the same way it always was: if it genuinely isn't installed the
# endpoint degrades to a clean 503 instead of an ImportError at import time.
try:
    import requests
except ImportError:  # pragma: no cover - requests should be a real dependency
    requests = None
from ._common import logger


# ===================== GIF SEARCH (STORY STUDIO F3) =====================
# GET /post/gifs/search/?q=&limit=&pos=  (no q = trending). Thin server-side proxy so settings.GIPHY_API_KEY never
# ships inside the app. Always `rating=g` (a story reaches minors). Rows are filtered by post/story_gifs.py to hosts +
# file types the story create endpoint accepts, so every row the picker shows is a row that can be posted.
GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
GIPHY_TRENDING_URL = "https://api.giphy.com/v1/gifs/trending"
GIF_SEARCH_MAX_LIMIT = 30
GIF_SEARCH_DEFAULT_LIMIT = 24


class GifSearchAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_gif_search"

    @extend_schema(
        summary="Search GIFs (Giphy, rating g) for the story GIF sticker",
        parameters=[
            OpenApiParameter(name="q", type=OpenApiTypes.STR, required=False),
            OpenApiParameter(name="limit", type=OpenApiTypes.INT, required=False),
            OpenApiParameter(name="pos", type=OpenApiTypes.INT, required=False),
        ],
        tags=["Stories"],
    )
    def get(self, request):
        if requests is None:
            logger.error("GIF search unavailable: the 'requests' package isn't installed")
            return Response({"success": False, "message": "GIF search unavailable"}, status=503)
        api_key = getattr(settings, "GIPHY_API_KEY", None)
        if not api_key:
            return Response({"success": False, "message": "GIF search is not configured"}, status=503)

        query = (request.query_params.get("q") or "").strip()[:60]
        try:
            limit = int(request.query_params.get("limit", GIF_SEARCH_DEFAULT_LIMIT))
            offset = int(request.query_params.get("pos", 0))
        except (TypeError, ValueError):
            return Response({"success": False, "message": "limit and pos must be numbers"}, status=400)
        limit = max(1, min(limit, GIF_SEARCH_MAX_LIMIT))
        offset = max(0, min(offset, 500))

        params = {"api_key": api_key, "limit": limit, "offset": offset, "rating": "g"}
        if query:
            params["q"] = query
            url = GIPHY_SEARCH_URL
        else:
            url = GIPHY_TRENDING_URL
        try:
            resp = requests.get(url, params=params, timeout=8)
        except requests.RequestException as e:
            logger.error(f"Giphy request failed: {type(e).__name__}")   # never log `e`: the URL carries the key
            return Response({"success": False, "message": "GIF search unavailable"}, status=503)
        if resp.status_code != 200:
            logger.error(f"Giphy returned {resp.status_code}")
            return Response({"success": False, "message": "GIF search failed"}, status=502)
        try:
            payload = resp.json()
        except ValueError:
            logger.error("Giphy returned a non-JSON response")
            return Response({"success": False, "message": "GIF search failed"}, status=502)

        pagination = payload.get("pagination") if isinstance(payload, dict) else None
        total = (pagination or {}).get("total_count") if isinstance(pagination, dict) else None
        next_pos = offset + limit if isinstance(total, int) and offset + limit < total else None
        return Response({
            "success": True,
            "data": {"results": story_gifs.map_giphy_results(payload), "next_pos": next_pos},
        })
