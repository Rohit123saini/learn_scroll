# post/views/counts.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

import uuid
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from ..models import Post
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from ..close_friends_views import blocked_user_ids
from rest_framework.decorators import permission_classes
from rest_framework import status


# ===================== BULK POST COUNTS (polling) =====================
# H9 - ETag / If-None-Match: the app polls this endpoint every 20-90 s and most of the time nothing changed. The
# ETag is a hash of the answer THIS viewer would get (so it can never leak a hidden post's numbers and it changes
# when a count, a visibility or a block changes); an unchanged answer is a 304 with an empty body.
def _post_counts_etag(results):
    """Weak ETag of a /post/counts/ answer (order independent)."""
    import hashlib
    import json
    payload = json.dumps(sorted(results, key=lambda r: r["id"]), sort_keys=True, separators=(",", ":"))
    return 'W/"%s"' % hashlib.md5(payload.encode("utf-8"), usedforsecurity=False).hexdigest()


def _if_none_match_hits(header_value, etag):
    """True when the request's If-None-Match matches [etag] (weak comparison, `*` matches any)."""
    if not header_value:
        return False
    def norm(tag):
        tag = tag.strip()
        return tag[2:] if tag.startswith("W/") else tag
    wanted = norm(etag)
    for tag in header_value.split(","):
        tag = tag.strip()
        if tag == "*" or (tag and norm(tag) == wanted):
            return True
    return False


# NEW — SujhaavFayda1 item 2 ("feed zinda feel"). PostReactionAPIView's GET
# above returns counts for ONE post; the Flutter feed polls this instead so
# a screenful of ~10-20 visible posts costs one request, not one-per-post.
#
# NOTE: if this project's `message` app already has Django Channels set up
# (asgi.py routing + consumers.py), a WebSocket push would be strictly
# better than polling here — this was built as REST polling because no
# Channels routing was present in the files reviewed for this pass. Share
# the message app's consumers.py/routing.py and this can be swapped for a
# real subscription instead.
class PostCountsAPIView(APIView):
    """GET /post/counts/?ids=<id>,<id>,...  (authenticated, throttled)

    Live reaction/comment counts for the posts a client currently has on screen.
    Only posts the CALLER may legitimately see are answered - approved, not
    deleted / archived / scheduled, viewer-visible (public / followers / close
    friends / class-campus / own), author not in a block relationship with the
    caller. Anything else is silently absent from `results` (same as an unknown
    id), so the endpoint cannot be used to probe whether a hidden post exists.
    Throttle scope `post_counts` (settings.DEFAULT_THROTTLE_RATES).

    H9: the answer carries a weak `ETag`; a request with a matching `If-None-Match` gets `304 Not Modified`
    (empty body) instead of the JSON. The client keeps the last answer per id-list and reuses it on a 304.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_counts"
    MAX_IDS = 100

    @extend_schema(
        summary="Bulk post reaction/comment counts (polling)",
        description="Comma-separated `ids` query param - returns just the "
                     "reaction + comment counts for those posts, capped at "
                     "100 ids per call. Auth required; only approved, non-deleted "
                     "posts visible to the caller are returned.",
        parameters=[OpenApiParameter(name="ids", type=OpenApiTypes.STR, required=True)],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Reactions"],
    )
    def get(self, request):
        from ..audience import filter_posts_visible_to

        raw_ids = request.query_params.get('ids', '')
        ids = []
        seen = set()
        for raw in raw_ids.split(','):
            raw = raw.strip()
            if not raw:
                continue
            try:
                pid = uuid.UUID(raw)  # a malformed id must not turn into a 500
            except (ValueError, AttributeError, TypeError):
                continue
            if pid in seen:
                continue
            seen.add(pid)
            ids.append(pid)
            # Lightweight polling endpoint, not a feed replacement - cap it.
            if len(ids) >= self.MAX_IDS:
                break
        if not ids:
            return Response({"results": []})

        qs = Post.objects.filter(
            id__in=ids, is_deleted=False, is_archived=False,
            moderation_status='approved', is_scheduled=False,
        )
        qs = filter_posts_visible_to(qs, request.user)
        blocked = blocked_user_ids(request.user)
        if blocked:
            qs = qs.exclude(user_id__in=list(blocked))
        posts = qs.values(
            'id', 'comments_count', 'likes_count',
            'like_count', 'confuse_count', 'wrong_count', 'imp_count', 'explain_count',
        )
        results = [
            {
                "id": str(p['id']),
                "comments_count": p['comments_count'],
                "counts": {
                    "like": p['like_count'],
                    "confuse": p['confuse_count'],
                    "wrong": p['wrong_count'],
                    "imp": p['imp_count'],
                    "explain": p['explain_count'],
                    "total": p['likes_count'],
                },
            }
            for p in posts
        ]
        etag = _post_counts_etag(results)
        cache_headers = {"ETag": etag, "Cache-Control": "private, no-cache", "Vary": "Authorization"}
        if _if_none_match_hits(request.META.get("HTTP_IF_NONE_MATCH"), etag):
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
        return Response({"results": results}, headers=cache_headers)
