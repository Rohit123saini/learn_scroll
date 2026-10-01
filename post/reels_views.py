"""
post/reels_views.py - GET /post/reels/ (see post/reels.py for the design).

Response shape is the same as the Home feed: {count, next, previous, results}
(`previous` is always null - Reels is swipe-forward only). Every result is the
lean `ReelSerializer` payload (video / caption / hashtags / author / counts /
viewer state / `feed_source` = "following" | "recommended"), NOT the full
PostListSerializer. Per page the queries do not grow with the page size: one
following-id query, one posts query (+ author join, + one media prefetch), two
batched viewer-state queries (reactions, saves).

"Seen" is reported with the existing `POST /post/feed/seen/`; there is no
Reels-specific action endpoint.
"""
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.utils.urls import remove_query_param, replace_query_param

from user_profile.models import Follow

from . import feed_mix, feed_snapshot, reels
from .serializers import ReelSerializer, reel_video_media
from .views import _home_base_qs, _video_and_velocity_boost


class ReelsFeedView(generics.ListAPIView):
    serializer_class = ReelSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = None  # cursor + frozen snapshot are handled below

    def get_serializer_context(self):
        return {
            "request": self.request,
            "following_ids": getattr(self, "_following_ids", None),
            "feed_sources": getattr(self, "_feed_sources", None) or {},
            "reel_reactions": getattr(self, "_reel_reactions", None),
            "reel_saved_ids": getattr(self, "_reel_saved_ids", None),
        }

    def get_queryset(self):
        # schema generation only; the real result set is built in list().
        return reels.page_queryset(self.request.user).order_by("-created_at")

    def _page_size(self, request, cfg):
        try:
            size = int(request.query_params.get("page_size", cfg["page_size"]))
        except (TypeError, ValueError):
            size = cfg["page_size"]
        return max(1, min(size, cfg["max_page_size"]))

    @extend_schema(
        summary="Reels feed (vertical short videos, ranked, frozen per scrolling session)",
        parameters=[
            OpenApiParameter(
                name="cursor", type=str, required=False,
                description="Opaque cursor. Do NOT set it on the first request; follow the `next` URL "
                            "of the previous response. The order is frozen for the whole session."),
            OpenApiParameter(
                name="start", type=str, required=False,
                description="Post id of a video to show FIRST (tap a video on a profile / Home and "
                            "continue in Reels). Ignored when the caller may not watch it."),
            OpenApiParameter(name="page_size", type=int, required=False),
            OpenApiParameter(
                name="seen_cutoff", type=str, required=False,
                description="Paging-stability token, returned inside `next`; clients just follow that URL."),
        ],
        tags=["Post Feed"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        cfg = reels.get_config()
        if not cfg["enabled"]:
            return Response({"count": 0, "next": None, "previous": None, "results": []})

        user = request.user
        page_size = self._page_size(request, cfg)
        token = request.query_params.get("cursor")
        state = feed_snapshot.decode_list_cursor(token, reels.NAMESPACE) if token else None  # bad cursor -> 404

        following_ids = set(
            Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True)
        )
        self._following_ids = following_ids

        ids = feed_snapshot.load_list(reels.NAMESPACE, user.pk, state["snapshot_id"]) if state else None
        if ids is not None:
            # HIT: exactly the ranking page 1 saw, no ranking queries at all.
            snapshot_id, offset, cutoff_str = state["snapshot_id"], state["offset"], state["seen_cutoff"]
        else:
            # NEW session, or MISS (expired / cache flushed / someone else's cursor):
            # rank again. On a miss reuse the cursor's seen_cutoff and offset.
            if state:
                snapshot_id, offset = state["snapshot_id"], state["offset"]
                seen_cutoff = feed_mix.resolve_seen_cutoff(state["seen_cutoff"])
            else:
                snapshot_id, offset = feed_snapshot.new_snapshot_id(), 0
                seen_cutoff = feed_mix.resolve_seen_cutoff(request.query_params.get("seen_cutoff"))
            cutoff_str = feed_mix.format_seen_cutoff(seen_cutoff)
            seen_ids = feed_mix.get_seen_post_ids(user, until=seen_cutoff)
            ids = reels.build_pool_ids(user, _home_base_qs(user), _video_and_velocity_boost, seen_ids=seen_ids)
            start_id = reels.resolve_start(user, request.query_params.get("start"))
            ids = reels.with_start_first(ids, start_id)
            feed_snapshot.save_list(reels.NAMESPACE, user.pk, snapshot_id, ids)

        window = ids[offset:offset + page_size]
        posts_by_id = {
            p.id: p for p in reels.page_queryset(user).filter(id__in=window)
        } if window else {}
        # deleted / made private / blocked / hidden / muted since the snapshot -> just skipped;
        # so is a video post that has no video file row (nothing to play).
        posts = [
            posts_by_id[pid] for pid in window
            if pid in posts_by_id and reel_video_media(posts_by_id[pid]) is not None
        ]
        self._reel_reactions, self._reel_saved_ids = reels.viewer_state(user, [p.id for p in posts])
        self._feed_sources = {
            p.id: (feed_mix.SOURCE_FOLLOWING if p.user_id in following_ids else feed_mix.SOURCE_RECOMMENDED)
            for p in posts
        }
        data = self.get_serializer(posts, many=True).data

        new_offset = offset + len(window)
        next_url = None
        if new_offset < len(ids):
            url = remove_query_param(request.build_absolute_uri(), "page")
            url = replace_query_param(
                url, "cursor", feed_snapshot.encode_list_cursor(reels.NAMESPACE, snapshot_id, new_offset, cutoff_str),
            )
            next_url = replace_query_param(url, "seen_cutoff", cutoff_str) if cutoff_str else url
        return Response({"count": len(ids), "next": next_url, "previous": None, "results": data})
