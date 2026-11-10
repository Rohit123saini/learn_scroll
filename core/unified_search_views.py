"""P-F7 -- HTTP layer of the unified search (logic: core/unified_search.py).

    GET /core/search/unified/   ?q=&tab=&limit=&offset=&<filters>      login required, throttle scope `search_unified`
    GET /core/search/trending/                                         login required, throttle scope `search_trending`

Response of /search/unified/ for one tab:
    {"query": "...", "tab": "posts", "items": [...], "has_more": true, "next_offset": 10,
     "fuzzy": false, "did_you_mean": "physics"|absent, "min_chars": 2}
for tab=all:
    {"query": "...", "tab": "all", "sections": {"people": {"items": [...], "has_more": false}, ...},
     "errors": [], "fuzzy": false, "did_you_mean": ..., "min_chars": 2}
A query shorter than `min_chars` is a 400 {"detail": ..., "code": "query_too_short", "min_chars": 2} (the app does
not call below that; it shows the trending / recent screen instead).
"""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import unified_search


class UnifiedSearchView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "search_unified"

    @extend_schema(
        summary="Unified search (people / posts / classes / tests / doubts)",
        tags=["Search"],
        parameters=[
            OpenApiParameter("q", str, description="Search text (min 2 chars). '#tag' = hashtag, '@name' = people."),
            OpenApiParameter("tab", str, description="all | people | posts | classes | tests | doubts"),
            OpenApiParameter("limit", int, description="1..30 (all tab: max 4 per section)"),
            OpenApiParameter("offset", int, description="0..200"),
        ],
    )
    def get(self, request):
        params = request.query_params
        tab = (params.get("tab") or "all").strip().lower()
        try:
            data = unified_search.run_search(
                request.user, params.get("q"), tab=tab, params=params,
                limit=params.get("limit"), offset=params.get("offset"), request=request)
        except ValueError as exc:
            code = str(exc)
            if code == "query_too_short":
                return Response(
                    {"detail": "Type at least %d characters to search." % unified_search.MIN_QUERY_CHARS,
                     "code": code, "min_chars": unified_search.MIN_QUERY_CHARS}, status=400)
            return Response({"detail": "Unknown search tab.", "code": code, "tabs": list(unified_search.TABS)},
                            status=400)
        return Response(data)


class SearchTrendingView(APIView):
    """What to show before the user types: trending hashtags + subjects. Same for everyone, cached 10 minutes."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "search_trending"

    @extend_schema(summary="Trending searches (hashtags, subjects)", tags=["Search"])
    def get(self, request):
        return Response(unified_search.trending(request.user))
