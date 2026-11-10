"""
post/reel_series_views.py - X2: GET /post/reels/<post_id>/series/

The parts of the series a reel belongs to ("Part 2/5"), for the series sheet of the reels viewer.

A series is the `metadata.reel.series = {title, part, total}` block of the reel studio. Parts are the reels of the SAME
author with the same series title (case-insensitive) that the caller may watch in Reels right now (public / own, approved,
not deleted / archived / scheduled, not hidden or blocked - reels.watchable_queryset). Sorted by part, then by date.

Response 200:
  {"series": {"title", "part", "total"} | null,       # the series block of <post_id> (null: the reel is in no series)
   "current_id": "<post_id>",
   "next_id": "<id of the next part>" | null,          # smallest part number above the current one
   "parts": [{"id", "part", "title", "thumbnail", "duration", "is_current"}]}   # max 100, sorted
404 when the reel is not watchable by the caller (same rule as `GET /post/reels/?start=`).
Queries: 1 (the reel) + 1 (parts, with author + media prefetch) - the same whatever the series size.
"""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from . import reels
from .reel_meta import series_of
from .serializers import _absolute_file_url, reel_video_media

MAX_PARTS = 100  # part numbers go 1..99


def _part_title(post):
    """Short label of one part: the post title, else the first line of the caption."""
    title = (getattr(post, "title", "") or "").strip()
    if not title:
        title = ((post.content or "").strip().splitlines() or [""])[0].strip()
    return title[:80]


class ReelSeriesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Parts of the series a reel belongs to (reels viewer: Part 2/5)", tags=["Post Feed"])
    def get(self, request, post_id):
        user = request.user
        post = reels.watchable_queryset(user).filter(pk=post_id).first()
        if post is None:
            return Response({"detail": "Reel not found."}, status=404)
        series = series_of(post)
        if series is None:
            return Response({"series": None, "current_id": str(post.pk), "next_id": None, "parts": []})

        siblings = (
            reels.page_queryset(user)
            .filter(user_id=post.user_id, metadata__reel__series__title__iexact=series["title"])
            .order_by("created_at")[:MAX_PARTS]
        )
        rows = []
        for p in siblings:
            s = series_of(p)
            media = reel_video_media(p)
            if s is None or media is None:
                continue
            rows.append((s.get("part") or 0, p, media))
        rows.sort(key=lambda r: (r[0], r[1].created_at))

        parts = [
            {
                "id": str(p.pk),
                "part": part,
                "title": _part_title(p),
                "thumbnail": _absolute_file_url(request, media.thumbnail),
                "duration": media.duration_seconds,
                "is_current": p.pk == post.pk,
            }
            for part, p, media in rows
        ]
        current_part = series.get("part") or 0
        later = [x for x in parts if x["part"] > current_part]
        return Response({
            "series": {"title": series["title"], "part": current_part, "total": series.get("total") or 0},
            "current_id": str(post.pk),
            "next_id": later[0]["id"] if later else None,
            "parts": parts,
        })
