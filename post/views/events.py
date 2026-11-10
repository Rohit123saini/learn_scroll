# post/views/events.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

import uuid
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.types import OpenApiTypes
from ..models import (
    Post,
    PostEvent,
    PostView,
)
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from drf_spectacular.utils import extend_schema
from django.shortcuts import get_object_or_404
from rest_framework.decorators import permission_classes
from rest_framework import status
from django.db import transaction
from ._common import logger


# ===================== FEED "SEEN" SIGNAL (BATCH) =====================
SEEN_BATCH_MAX = 50


class PostSeenBatchAPIView(APIView):
    """`POST /post/feed/seen/` — body `{"post_ids": ["<uuid>", ...]}`.

    Lightweight batch endpoint the feed calls with the posts that actually
    scrolled into view. It only records "this user has seen this post"
    (a `PostView` row with `is_counted=False`); it does NOT touch
    `Post.views_count` — that still moves only when the post is opened
    (PostDetailAPIView), once per (post, user).

    * Idempotent: `bulk_create(ignore_conflicts=True)` leans on the
      UniqueConstraint(post, user), so re-sending ids, racing requests, or
      posts already seen/opened are silently skipped (existing rows —
      including their watch progress and first `viewed_at` — are never
      modified).
    * At most 50 unique ids per call (400 above that). Duplicate ids in
      the payload are collapsed. Unknown / soft-deleted posts are ignored
      rather than failing the whole batch.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Mark feed posts as seen (batch, does not bump views_count)",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def post(self, request):
        raw_ids = request.data.get('post_ids')
        if not isinstance(raw_ids, (list, tuple)):
            return Response({"success": False, "message": "'post_ids' must be a list of post ids"}, status=status.HTTP_400_BAD_REQUEST)

        post_ids = []
        seen_in_payload = set()
        for raw in raw_ids:
            try:
                pid = uuid.UUID(str(raw))
            except (TypeError, ValueError, AttributeError):
                return Response({"success": False, "message": f"Invalid post id: {raw!r}"}, status=status.HTTP_400_BAD_REQUEST)
            if pid not in seen_in_payload:
                seen_in_payload.add(pid)
                post_ids.append(pid)

        if len(post_ids) > SEEN_BATCH_MAX:
            return Response(
                {"success": False, "message": f"At most {SEEN_BATCH_MAX} post ids per request"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not post_ids:
            return Response({"success": True, "accepted": 0})

        # Filter to real posts first: an unknown id would otherwise be a
        # FK violation, which ON CONFLICT DO NOTHING does not swallow.
        valid_ids = list(
            Post.objects.filter(id__in=post_ids, is_deleted=False).values_list('id', flat=True)
        )
        if valid_ids:
            PostView.objects.bulk_create(
                [PostView(post_id=pid, user=request.user, is_counted=False) for pid in valid_ids],
                ignore_conflicts=True,
            )
            # T1 Part 5: cached candidates still contain what was just seen.
            from .. import feed_cache
            feed_cache.invalidate(request.user.pk)
        return Response({"success": True, "accepted": len(valid_ids)})

# ===================== ANALYTICS EVENTS (C1-BE) =====================
POST_EVENTS_MAX = 100          # max events accepted per request
POST_EVENT_MAX_DWELL_MS = 60 * 60 * 1000   # 1h; anything above is a client bug


class PostEventBulkAPIView(APIView):
    """`POST /post/events/` — body:

        {"events": [
            {"post_id": "<uuid>", "event_type": "impression|dwell|tap|skip",
             "surface": "feed|reels|profile|explore", "dwell_ms": 1234},
            ...
        ]}

    Bulk analytics ingest for the feed / reels / profile / explore surfaces
    (impression, dwell time, tap, skip). Writes append-only `PostEvent` rows;
    it never touches `PostView`, `Post.views_count` or any ranking counter.

    * At most `POST_EVENTS_MAX` (100) events per request — 101+ is a 400.
    * Validation is all-or-nothing: a malformed event (bad uuid / event_type /
      surface / dwell_ms) rejects the whole batch with a 400 that names the
      offending index, so the client never silently loses part of a batch.
    * `dwell` events must carry `dwell_ms` (int, 0..1h). For the other types
      `dwell_ms` is optional (stored as sent for `skip`, forced to 0 for
      `impression`/`tap`).
    * Events for unknown / soft-deleted posts are skipped (counted in
      `ignored`) instead of failing the batch — an unknown id would be an FK
      violation, and a post deleted between render and flush is not the
      client's fault.
    * Throttled by scope `post_events` (rate in settings.py
      `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`). This view REPLACES the
      project-wide default throttle classes, so only the scoped rate applies.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_events"

    @staticmethod
    def _bad(message):
        return Response({"success": False, "message": message}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Bulk-ingest feed/profile analytics events (impression, dwell, tap, skip)",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def post(self, request):
        raw_events = request.data.get('events') if hasattr(request.data, 'get') else None
        if not isinstance(raw_events, (list, tuple)):
            return self._bad("'events' must be a list of event objects")
        if len(raw_events) > POST_EVENTS_MAX:
            return self._bad(f"At most {POST_EVENTS_MAX} events per request")
        if not raw_events:
            return Response({"success": True, "accepted": 0, "ignored": 0})

        valid_types = set(PostEvent.EventType.values)
        valid_surfaces = set(PostEvent.Surface.values)
        parsed = []
        for idx, raw in enumerate(raw_events):
            if not isinstance(raw, dict):
                return self._bad(f"events[{idx}] must be an object")
            try:
                pid = uuid.UUID(str(raw.get('post_id')))
            except (TypeError, ValueError, AttributeError):
                return self._bad(f"events[{idx}]: invalid post_id")
            event_type = raw.get('event_type')
            if event_type not in valid_types:
                return self._bad(f"events[{idx}]: event_type must be one of {sorted(valid_types)}")
            surface = raw.get('surface')
            if surface not in valid_surfaces:
                return self._bad(f"events[{idx}]: surface must be one of {sorted(valid_surfaces)}")

            raw_dwell = raw.get('dwell_ms')
            if raw_dwell is None:
                if event_type == PostEvent.EventType.DWELL:
                    return self._bad(f"events[{idx}]: dwell_ms is required for dwell events")
                dwell_ms = 0
            else:
                # bool is an int subclass in Python — reject it explicitly.
                if isinstance(raw_dwell, bool) or not isinstance(raw_dwell, (int, float)):
                    return self._bad(f"events[{idx}]: dwell_ms must be a number")
                if raw_dwell != raw_dwell or raw_dwell < 0 or raw_dwell > POST_EVENT_MAX_DWELL_MS:
                    return self._bad(f"events[{idx}]: dwell_ms must be between 0 and {POST_EVENT_MAX_DWELL_MS}")
                dwell_ms = int(raw_dwell)
            if event_type in (PostEvent.EventType.IMPRESSION, PostEvent.EventType.TAP):
                dwell_ms = 0
            parsed.append((pid, event_type, surface, dwell_ms))

        # One query to find which posts are real; anything else is skipped.
        valid_post_ids = set(
            Post.objects.filter(id__in={p[0] for p in parsed}, is_deleted=False).values_list('id', flat=True)
        )
        rows = [
            PostEvent(user=request.user, post_id=pid, event_type=et, surface=sf, dwell_ms=dm)
            for (pid, et, sf, dm) in parsed
            if pid in valid_post_ids
        ]
        if rows:
            # T0.1 — ye pehle se EK bulk INSERT per request hai (batched). Ab: ek hi
            # chhota transaction (`batch_size` ke andar), transient "database is
            # locked" par ek retry, aur phir bhi fail ho to 500 nahi — 503 +
            # Retry-After. Validation all-or-nothing hai, isliye client poora batch
            # wapas bhej sakta hai (analytics append-only hai, duplicate ka risk sirf
            # tab jab pehla attempt commit hua ho aur reply kho gaya — wo bhi rare).
            from django.db import OperationalError as _OpError
            from common.db_retry import retry_on_db_lock

            def _insert():
                with transaction.atomic():
                    PostEvent.objects.bulk_create(rows, batch_size=100)

            try:
                retry_on_db_lock(_insert)
            except _OpError:
                logger.warning("post/events: DB busy even after retry user=%s n=%d", request.user.pk, len(rows))
                return Response(
                    {"success": False, "message": "Server busy, please retry shortly."},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    headers={"Retry-After": "2"},
                )
        return Response({"success": True, "accepted": len(rows), "ignored": len(parsed) - len(rows)})

# ===================== VIDEO WATCH PROGRESS (TASK G4) =====================
class PostVideoProgressAPIView(APIView):
    """TASK G4 (growth_and_feature_tasks.md) — records how far into a video
    the caller actually watched, so HomeFeedView/ExploreFeedAPIView's
    video-completion-rate boost (`_video_and_velocity_boost()` above) has
    real data to rank on. Meant to be called sparingly by the feed video
    widget (e.g. on pause/dispose/scroll-away), not once per frame.

    Body: {"watched_seconds": <number>}.

    The `PostView` row for (post, user) may already exist — created by
    PostDetailAPIView.retrieve's own get_or_create when the post was
    opened full-screen — this reuses that same row rather than creating a
    duplicate, same (post, user) pairing PostView.Meta already assumes
    elsewhere in this file.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Record video watch progress for feed ranking (Task G4)",
        request=OpenApiTypes.OBJECT,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False, post_type='video')

        try:
            watched_seconds = float(request.data.get('watched_seconds'))
        except (TypeError, ValueError):
            return Response({"success": False, "message": "'watched_seconds' must be a number"}, status=status.HTTP_400_BAD_REQUEST)
        if watched_seconds < 0:
            return Response({"success": False, "message": "'watched_seconds' can't be negative"}, status=status.HTTP_400_BAD_REQUEST)

        # Server-known duration, never whatever the client claims — keeps
        # a spoofed/huge `watched_seconds` from inflating the ratio past
        # 1.0 (the signal in models.py also clamps, this is belt-and-braces
        # closer to the source).
        media_duration = post.media.filter(media_type='video').exclude(
            duration_seconds__isnull=True
        ).values_list('duration_seconds', flat=True).first()
        if not media_duration:
            return Response({"success": False, "message": "No known duration for this video yet"}, status=status.HTTP_409_CONFLICT)

        watched_seconds = min(watched_seconds, float(media_duration))

        view, _ = PostView.objects.get_or_create(post=post, user=request.user)
        # Furthest point reached only ever moves forward — feed autoplay
        # loops the first video (home.dart: `c.setLooping(true)`), and a
        # loop/replay shouldn't erase a genuine earlier watch.
        if view.watch_seconds is None or watched_seconds > view.watch_seconds:
            view.watch_seconds = watched_seconds
            view.video_duration_seconds = float(media_duration)
            view.save(update_fields=['watch_seconds', 'video_duration_seconds'])

        return Response({"success": True})
