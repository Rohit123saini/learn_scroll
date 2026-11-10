#post/views.py
import os
import re
import uuid
import logging
import mimetypes
from collections import Counter
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, F, Case, When, IntegerField, FloatField, ExpressionWrapper, Exists, OuterRef, Value
from django.utils import timezone
from django.utils.text import slugify
from django.http import StreamingHttpResponse, HttpResponse, Http404
from django.core.exceptions import SuspiciousFileOperation
from django.utils._os import safe_join
from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from rest_framework import status, parsers, generics, filters
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import PermissionDenied

# Story reactions/replies broadcast over the same channel-layer used by
# message/services.py — see StoryReactAPIView/StoryReplyAPIView below.
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes

from.models import (
    Post, PostAnswer, PostMedia, PostPoll, PostPollOption, PostPollVote,
    PostView, PostLike, PostSave, PostComment, Story, StoryView,
    StoryReaction, PostChunkedUpload, UserInterest, PostHide, MutedAccount, FeedFeedback,
    CloseFriend, StorySticker, PostEvent,
)
from .story_sticker_responses import (
    ResponseError, build_interaction_stats, cast_quiz_answer, cast_vote, submit_answer,
)
from . import content_defaults as _content_defaults  # S4b account-level defaults for new posts
from . import story_gifs
from .story_visibility import (
    STORY_RESHARE_OFF, STORY_RESHARE_OFF_MESSAGE, STORY_SAVE_OFF, STORY_SAVE_OFF_MESSAGE,
    can_reply_to_story, can_reshare_story, can_save_story, filter_stories_visible_to, get_visible_story_or_404,
)
from .close_friends_views import blocked_user_ids
from .feed_resilience import FeedHomeThrottle, FeedOpsThrottle
from.serializers import (
    PostCreateSerializer,
    PostListSerializer,
    PostDetailSerializer,
    PostMediaSerializer,
    PostSaveSerializer,
    StoryCreateSerializer,
    StorySerializer,
    StoryMentionCandidateSerializer,
    get_profile_pic_url,
    serialize_sticker,
    story_sticker_prefetch,
    StoryReactionSerializer,
    StoryViewerEntrySerializer,
    ReactionRequestSerializer,
    RepostRequestSerializer,
    PostEditSerializer,
    PostVisibilitySerializer,
    PostShareRequestSerializer,
    UserInterestsUpdateSerializer,
    # TASK G6 — polls (live voting) + "Ask a doubt" answers.
    PollVoteRequestSerializer,
    PostPollSerializer,
    PostAnswerSerializer,
    PostAnswerCreateSerializer,
    normalize_category,
    category_error_message,
    # Feed feedback controls (Part 1).
    NotInterestedRequestSerializer,
    PostHideSerializer,
    MuteAccountRequestSerializer,
    MuteScopeSerializer,  # P8
    MutedAccountSerializer,
    # Feed feedback controls (Part 2).
    ShowFewerRequestSerializer,
    FeedFeedbackSerializer,
    WhyResponseSerializer,
)
from.signals import decrement_posts_count_on_soft_delete
from .idempotency import idempotent  # H20 duplicate-submit guard
from .services import (
    notify_post_liked, notify_post_answered, notify_post_reposted, unnotify_post_reposted,
    notify_story_reacted, unnotify_story_reacted, save_uploaded_chunk, assemble_chunks,
    list_received_chunks, CHUNK_UPLOAD_MAX_SIZE, share_post_to_conversation,
    exclude_hidden_and_muted, apply_show_fewer, ShowFewerError,
)
from user_profile.models import Follow
from common.pagination import EngagementCursorPagination  # cursor (keyset) pagination for engagement_score feeds
from common.pagination import StandardPagination  # story mention-candidate search

# TASK 4 — freesound_music_search's HTTP client. Guarded the same way
# Services.py guards its `core` app import: if `requests` genuinely isn't
# installed in some environment, the music-search endpoint degrades to a
# clean 503 instead of an ImportError crashing this whole module (and
# every other view in it) at import time.
try:
    import requests
except ImportError:  # pragma: no cover - requests should be a real dependency
    requests = None

User = get_user_model()
logger = logging.getLogger(__name__)

# ===================== PAGINATION =====================
class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

class HomeFeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 50

# ===================== VALIDATION MESSAGE HELPER =====================
def _first_error_text(errors, prefix=""):
    """Flattens DRF's {field: [msg, ...]} error dict into one readable line
    ("category: "x" is not a valid category. ...") for the top-level
    `message`, so a client that only shows `message` (api_service.dart
    does) still tells the user WHAT failed instead of "Validation failed".
    The full dict stays available under `errors`."""
    if isinstance(errors, dict):
        for field, value in errors.items():
            text = _first_error_text(value, field)
            if text:
                return text
    elif isinstance(errors, (list, tuple)):
        for value in errors:
            text = _first_error_text(value, prefix)
            if text:
                return text
    elif errors:
        if prefix and prefix != "non_field_errors":
            return f"{prefix}: {errors}"
        return str(errors)
    return ""


# ===================== POST CREATE =====================
class PostCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]
    serializer_class = PostCreateSerializer

    @extend_schema(
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'title': {'type': 'string', 'maxLength': 255},
                    'content': {'type': 'string'},
                    'category': {
                        'type': 'string',
                        'enum': ['general', 'tech', 'jobs', 'news', 'education',
                                'business', 'entertainment', 'sports', 'lifestyle', 'other']
                    },
                    'post_type': {
                        'type': 'string',
                        'enum': ['text', 'image', 'video', 'document', 'poll', 'article', 'carousel', 'link']
                    },
                    'visibility': {
                        'type': 'string',
                        'enum': ['public', 'connections', 'private']
                    },
                    'hashtags': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'Hashtags without #'
                    },
                    'mentioned_user_ids': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'UUIDs of mentioned users'
                    },
                    'metadata': {'type': 'object'},
                    'location': {'type': 'object'},
                    'media_files': {
                        'type': 'array',
                        'items': {'type': 'string', 'format': 'binary'},
                    },
                    'media_types': {
                        'type': 'array',
                        'items': {
                            'type': 'string',
                            'enum': ['image', 'video', 'document', 'audio', 'gif']
                        },
                    }
                },
                'required': ['post_type', 'category']
            }
        },
        responses={201: PostCreateSerializer},
        description='Create post with multiple media files. Max 10 files, 100MB each.'
    )
    @idempotent("post_create")  # H20: header `Idempotency-Key` = one post however often the request repeats
    @transaction.atomic
    def post(self, request):
        # Copyright repeat-infringer policy: 2 active strikes block uploads (copyrights app).
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)
        # Refuse an exact copy of a file that was removed for copyright (similar copies are held after saving).
        # Poll option images (`poll_option_image_<i>`) are uploads too: they go through the same copyright pre-check
        # and publish gate as the media files (appended AFTER them, so `media_types` still lines up with media_files).
        from .polls import option_image_files
        media_files = list(request.FILES.getlist("media_files")) + option_image_files(request.FILES)
        ok, code, why = precheck_upload(request.user, files=media_files)
        if not ok:
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)
        # H23 publish gate: spam rate-limit, abusive/NSFW text, duplicates, audio-copyright flag, minor privacy.
        from . import publish_gate
        verdict = publish_gate.evaluate(
            request.user, "post",
            texts=publish_gate.texts_from_request("post", request.data),
            files=media_files, media_types=request.data.get("media_types"),
        )
        if not verdict.ok:
            return verdict.response()
        serializer = PostCreateSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            try:
                save_extra = {"moderation_status": verdict.moderation_status}  # 'flagged' = held for staff review
                if not verdict.location_allowed:
                    save_extra["location"] = {}  # minor accounts never publish a location
                post = serializer.save(user=request.user, **save_extra)
                publish_gate.after_create(verdict, request.user, "post", post)
                # 🔥 FIX: posts_count increment moved to signals.py
                # (increment_posts_count_on_create, post_save on Post).
                # Doing it here too would double-count — see signals.py's
                # module docstring for why the signal is now the single
                # source of truth for this counter.
                logger.info(f'Post created: {post.id} by {request.user.id}')
                output_serializer = PostCreateSerializer(post, context={'request': request})
                return Response({"success": True, "message": "Post created successfully","data": output_serializer.data,
                                 **verdict.held_payload()}, status=status.HTTP_201_CREATED)
            except Exception as e:
                logger.error(f'Post creation failed: {e}', exc_info=True)
                return Response({"success": False, "message": "Failed to create post"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed","errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

# ===================== CATEGORY TAXONOMY (TASK 4) =====================
# GET /post/categories/ — ApiService.getCategoryTaxonomy() (api_service.dart)
# was already calling this exact path and reading response['data']; nothing
# backing it existed, so both composers' category pickers 404'd on load.
@api_view(['GET'])
@permission_classes([AllowAny])
@extend_schema(summary="Category / subcategory taxonomy", tags=["Post"])
def category_taxonomy(request):
    """Category picker source for both composers (new_post.dart /
    quick_post.dart, via ApiService.getCategoryTaxonomy()).

    `Post.CATEGORY_CHOICES` (models.py) is the single source of truth, and
    the shape below is exactly what the composers parse:

        data.categories               [{key, value, label}, ...]
        data.subcategories            [{key, label}, ...]   (flat list)
        data.category_subcategory_map {category_key: [subcategory_key, ...]}

    `key` is the value the client must send back as `category`; `value` is
    the same string, kept as an alias for any client that reads `value`.

    History (the bug this shape fixes): this used to return
    `categories=[{value,label}]` and `subcategories={cat: []}` (a dict) and
    no `category_subcategory_map`. The composers read `c['key']` (-> the
    string "null", which quick_post then POSTed as its category) and
    `List.from(data['subcategories'])` (a TypeError on a dict -> empty
    category picker in new_post).

    Subcategories: `Post.subcategory` is deliberately a free-form column
    (models.py) and no subcategory taxonomy exists server-side, so the list
    and every per-category entry are empty - the composers hide the
    subcategory picker when a category has none. Add real entries here
    (and validate them in PostCreateSerializer.validate_subcategory) once
    product defines them.
    """
    categories = [
        {"key": key, "value": key, "label": label}
        for key, label in Post.CATEGORY_CHOICES
    ]
    return Response({
        "success": True,
        "data": {
            "categories": categories,
            "subcategories": [],
            "category_subcategory_map": {key: [] for key, _ in Post.CATEGORY_CHOICES},
        },
    })


# ===================== INTERESTS (production_readiness_tasks.md TASK 3) =====================
# GET  /post/interests/  -> caller's currently-selected categories (plus the
#                            same fixed `categories` taxonomy category_taxonomy
#                            above returns, so a chip-row UI needs exactly one
#                            call to render itself fully selected/unselected).
# PUT  /post/interests/  -> replace the full set in one call, body
#                            {"categories": ["tech", "sports", ...]}. Chip UIs
#                            toggle a whole set at once rather than issuing
#                            one add/remove call per chip, so PUT-replace is
#                            the right shape here (not POST-append/DELETE).
#
# Deliberately reuses `Post.CATEGORY_CHOICES` — the same curated, fixed list
# the composer's category picker already uses — rather than a second tag
# vocabulary (see `UserInterest` model docstring). `HomeFeedView` and
# `ExploreFeedAPIView` (below) both read this back to bias ranking towards a
# user's selected categories.
class UserInterestsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get my selected feed interests",
        description="Returns the caller's selected categories plus the full "
                     "category taxonomy, so a chip-picker UI can render in "
                     "one call.",
        tags=["Interests"],
    )
    def get(self, request):
        selected = list(
            UserInterest.objects.filter(user=request.user).values_list('category', flat=True)
        )
        categories = [
            {"key": key, "label": label, "selected": key in selected}
            for key, label in Post.CATEGORY_CHOICES
        ]
        return Response({
            "success": True,
            "data": {
                "selected_categories": selected,
                "categories": categories,
            },
        })

    @extend_schema(
        summary="Replace my selected feed interests",
        request=UserInterestsUpdateSerializer,
        tags=["Interests"],
    )
    def put(self, request):
        serializer = UserInterestsUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        categories = serializer.validated_data['categories']

        with transaction.atomic():
            UserInterest.objects.filter(user=request.user).exclude(category__in=categories).delete()
            existing = set(
                UserInterest.objects.filter(user=request.user).values_list('category', flat=True)
            )
            UserInterest.objects.bulk_create([
                UserInterest(user=request.user, category=c)
                for c in categories if c not in existing
            ])
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5: recommendations depend on interests
        if categories:  # H17: first save = cold-start milestone (best effort, never raises)
            from core import funnel
            funnel.record_interests_saved(request.user, interests=len(categories))

        return Response({
            "success": True,
            "data": {"selected_categories": categories},
        })

    # Same replace semantics as PUT — some HTTP clients/proxies handle POST
    # more reliably than PUT for JSON bodies, so both are wired to this.
    def post(self, request):
        return self.put(request)


# ===================== FREESOUND MUSIC SEARCH (TASK 4) =====================
# GET /post/music/search/?q=&page= — ApiService.searchFreesoundMusic()
# (api_service.dart) was already calling this exact path; nothing backing
# it existed, so the Music tab in media_edit_screen.dart/auto_edit_screen.dart
# couldn't search. Thin server-side proxy so settings.FREESOUND_API_KEY
# (already configured via the FREESOUND_API_KEY env var — settings.py)
# never has to ship inside the app.
FREESOUND_SEARCH_URL = "https://freesound.org/apiv2/search/text/"
FREESOUND_TRENDING_QUERY = "music"   # ?trending=1 without q: sorted by downloads, so the generic word is enough


@api_view(['GET'])
@permission_classes([AllowAny])
@extend_schema(
    summary="Search Freesound for CC0 music",
    parameters=[
        OpenApiParameter(name="q", type=OpenApiTypes.STR, required=False),
        OpenApiParameter(name="trending", type=OpenApiTypes.BOOL, required=False),
        OpenApiParameter(name="page", type=OpenApiTypes.INT, required=False),
    ],
    tags=["Post"],
)
def freesound_music_search(request):
    """Matches searchFreesoundMusic()'s contract exactly:
    {"success", "data": {"results": [...]}}, each result
    {id, name, artist, duration, preview_url, license, tags}.

    Only CC0 ("Creative Commons 0" / public-domain) results are requested
    from Freesound — per that Dart method's own comment ("sirf
    CC0-licensed (copyright-free) results deta hai"). CC0 needs no
    attribution, so it's the only license this app's Music tab can safely
    let someone attach to a post without a credit-line feature to go
    with it — anything else (CC-BY etc.) would need attribution UI this
    app doesn't have yet.
    """
    if requests is None:
        logger.error("Freesound search unavailable: the 'requests' package isn't installed")
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    api_key = getattr(settings, 'FREESOUND_API_KEY', None)
    if not api_key:
        return Response({"success": False, "message": "Music search is not configured"}, status=503)

    query = (request.query_params.get('q') or '').strip()
    # Reel studio music sheet: `?trending=1` (no q) = the most downloaded CC0 sounds, short ones first-class for reels.
    trending = (request.query_params.get('trending') or '').strip().lower() in ('1', 'true', 'yes')
    if not query and not trending:
        return Response({"success": False, "message": "q is required"}, status=400)
    if not query:
        query = FREESOUND_TRENDING_QUERY

    try:
        page = int(request.query_params.get('page', 1))
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)

    try:
        resp = requests.get(
            FREESOUND_SEARCH_URL,
            params={
                'query': query,
                'token': api_key,
                'page': page,
                # CC0 only — see docstring above for why.
                'filter': 'license:"Creative Commons 0"',
                **({'sort': 'downloads_desc'} if trending else {}),
                'fields': 'id,name,username,duration,previews,license,tags',
            },
            timeout=8,
        )
    except requests.RequestException as e:
        logger.error(f'Freesound search request failed: {e}')
        return Response({"success": False, "message": "Music search unavailable"}, status=503)

    if resp.status_code != 200:
        logger.error(f'Freesound returned {resp.status_code}: {resp.text[:300]}')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    try:
        payload = resp.json()
    except ValueError:
        logger.error('Freesound returned non-JSON response')
        return Response({"success": False, "message": "Music search failed"}, status=502)

    results = []
    # X7: sounds staff took down are not listed any more (post/sounds.py).
    from . import sounds as _sounds
    blocked_sounds = _sounds.blocked_ids()
    for item in payload.get('results', []):
        if str(item.get('id')) in blocked_sounds:
            continue
        previews = item.get('previews') or {}
        preview_url = previews.get('preview-hq-mp3') or previews.get('preview-lq-mp3')
        results.append({
            "id": item.get('id'),
            "name": item.get('name'),
            "artist": item.get('username'),
            "duration": item.get('duration'),
            "preview_url": preview_url,
            "license": "CC0",
            "tags": item.get('tags') or [],
        })

    return Response({
        "success": True,
        "data": {
            "results": results,
            "count": payload.get('count', len(results)),
            # Freesound's own `next` is a full API URL (with the token in
            # it) — never pass that through to the client. Just tell it
            # whether another page exists; it already knows how to ask
            # for `page + 1` itself (searchFreesoundMusic's `page` param).
            "next_page": (page + 1) if payload.get('next') else None,
        },
    })


# ===================== REPOST HELPER =====================
def _without_superseded_reposts(qs):
    """Duplicate-repost policy (see PostRepostAPIView): the same user may
    repost the same post more than once, but a feed/list should only show
    the LATEST of those. Hides any repost row that has a newer, non-deleted
    repost of the same original by the same user. Plain posts
    (`original_post IS NULL`) are never touched."""
    newer_repost = Post.objects.filter(
        user=OuterRef('user'), original_post=OuterRef('original_post'),
        is_deleted=False, created_at__gt=OuterRef('created_at'),
    )
    # A repost of a post its author archived disappears with it (the embedded preview would leak it).
    return qs.exclude(Q(original_post__isnull=False) & Exists(newer_repost)).exclude(original_post__is_archived=True)


# TASK G4 (growth_and_feature_tasks.md) — "Short-form video priority in
# feed ranking". Two boost components, shared by HomeFeedView and
# ExploreFeedAPIView so both rank the same way:
#
#   * video_boost — rewards videos people actually watch, not just scroll
#     past. NOW built by feed_mix.video_watch_boost() (completion rate x
#     confidence + average watch seconds; settings.FEED_WATCH_TIME); the
#     paragraph below describes the raw input. `video_completion_rate` (0.0-1.0, models.py) is 0.0 for every
#     non-video post and for videos with no watch-progress data yet, so
#     this is a pure no-op until PostVideoProgressAPIView starts recording
#     real watches — it never demotes anything.
#   * recent_velocity_boost — "likes/comments per hour", approximated with
#     recency buckets (3h/12h/48h) rather than a literal division. A
#     literal `(likes*3+comments*5) / hours_since_created` would need a
#     DB-side EXTRACT(epoch FROM ...) on a duration, which doesn't behave
#     the same way across this project's Postgres/SQLite split
#     (LearnScroll/settings.py) — bucketed multipliers give the same
#     "fresh + fast-moving beats old + steady" ranking shape and stay
#     portable. Same shape as the existing `is_recent` Case/When just
#     below in HomeFeedView.
def _video_and_velocity_boost():
    # Watch-time ranking (feed task, Part 1): confidence-weighted completion
    # rate + absolute watch time, see feed_mix.video_watch_boost().
    from . import feed_mix

    video_boost = feed_mix.video_watch_boost()
    now = timezone.now()
    engagement = F('likes_count') * 3.0 + F('comments_count') * 5.0
    # Multipliers are fractions of the post's own engagement score, not
    # flat points — a fresh post with 0 engagement still gets 0 boost, and
    # a heavily-liked-but-old post never gets demoted, it just stops
    # earning the freshness bonus.
    recent_velocity_boost = Case(
        When(created_at__gte=now - timedelta(hours=3), then=engagement * 0.5),
        When(created_at__gte=now - timedelta(hours=12), then=engagement * 0.25),
        When(created_at__gte=now - timedelta(hours=48), then=engagement * 0.1),
        default=Value(0.0), output_field=FloatField(),
    )
    return video_boost, recent_velocity_boost


def _home_base_qs(user):
    """The Home feed's shared "safe to show" queryset (all three sources).

    Module-level (not a HomeFeedView method) so GET /post/<id>/why/ can build
    the exact same queryset the feed pools are made from.
    exclude_hidden_and_muted: "Not interested" posts + muted accounts never
    come back (also applied when a frozen snapshot is served, because the ids
    are re-fetched through this queryset)."""
    qs = exclude_hidden_and_muted(_without_superseded_reposts(
        Post.objects.select_related('user', 'original_post__user')
        .prefetch_related('media', 'original_post__media')
        .filter(is_deleted=False, is_archived=False, moderation_status='approved', is_sensitive=False, is_scheduled=False)
        .exclude(user=user)
    ), user)
    # T1 item 6: heavily reported posts are out of EVERY source (post/feed_quality.py, layer 1).
    from . import feed_quality
    return feed_quality.exclude_reported_heavy(qs)


# ===================== HOME FEED =====================
# DISCOVERY MIX — the feed is no longer "following only". Every page blends
# three sources (default 60% following / 30% recommended / 10% trending,
# tunable via settings.FEED_MIX_RATIOS). All the mixing/ranking lives in
# post/feed_mix.py; this view only wires it to HTTP. If one source runs dry
# (new user follows nobody, follows haven't posted) its slots are refilled
# from the others, so the feed is never empty. Response shape is unchanged:
# {count, next, previous, results}; each post additionally carries
# `feed_source` = "following" | "recommended" | "trending".
class HomeFeedView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = HomeFeedPagination
    # H10: explicit per-user limit for the feed (scope `feed_home`, settings DEFAULT_THROTTLE_RATES). Replaces the
    # project-wide default throttles for this view, and fails OPEN when the cache (Redis) is down - the stock
    # throttle would turn a Redis outage into a 500 on every feed request.
    throttle_classes = [FeedHomeThrottle]

    def get_serializer_context(self):
        # TASK 2: hand the already-computed following-id set to the
        # serializer so each post card's `user.is_following` (Follow
        # button) doesn't run its own query per post. Falls back to None
        # (per-post query) when list() hasn't run, e.g. drf-spectacular.
        return {
            'request': self.request,
            'following_ids': getattr(self, '_following_ids', None),
            'feed_sources': getattr(self, '_feed_sources', None) or {},
            # T1 Part 3: ids that were woven in as exploration (new creator / test audience)
            'explore_ids': getattr(self, '_explore_ids', None) or set(),
        }

    def _filter(self):
        """H12: the chip filter of this request (?subject= / ?category= / ?exam=, see post/feed_chips.py), parsed
        once. A bad value is a 400 (DRF ValidationError); no request (schema generation) = no filter."""
        flt = getattr(self, '_feed_filter', None)
        if flt is None:
            from . import feed_chips
            request = getattr(self, 'request', None)
            flt = self._feed_filter = (
                feed_chips.parse(request.query_params) if request is not None else feed_chips.NO_FILTER
            )
        return flt

    def _base_qs(self):
        """Shared "safe to show" queryset for all three sources (+ the H12 chip filter, so the ranking pools, the
        id -> post fetch of every page and the H10 fallback feed all see the same narrowed set)."""
        from . import feed_chips
        return feed_chips.apply(_home_base_qs(self.request.user), self._filter())

    def get_queryset(self):
        # Kept for schema generation / backwards compatibility; the real
        # (mixed) result set is built in list().
        return self._base_qs().filter(visibility='public').order_by('-created_at')

    @extend_schema(
        summary="Home feed (following + recommended + trending mix)",
        parameters=[
            OpenApiParameter(
                name='cursor', type=str, required=False,
                description="Opaque cursor. Do NOT set it on the first request; follow the `next` URL "
                            "from the previous response. The ranking is frozen for the whole scrolling "
                            "session (see post/feed_snapshot.py)."),
            OpenApiParameter(name='page', type=int, required=False,
                             description="DEPRECATED - only used when no cursor is sent and page > 1 "
                                         "(old app versions)."),
            OpenApiParameter(name='page_size', type=int, required=False),
            OpenApiParameter(
                name='source', type=str, required=False,
                description="'mixed' (default, 60/30/10 blend) or 'following' (following-first; "
                            "discovery only fills in when there is nothing from followed accounts).",
            ),
            OpenApiParameter(
                name='subject', type=str, required=False,
                description="H12 chip: only posts of this subject (`subcategory` or a hashtag such as #physics). "
                            "Aliases: math/mathematics -> maths, phy -> physics, chem -> chemistry. Part of the "
                            "snapshot key and of the cursor: keep it the same on every page (a cursor of another "
                            "chip is a 404)."),
            OpenApiParameter(
                name='category', type=str, required=False,
                description="H12 chip: only posts of this `Post.category` (e.g. education). Same rules as `subject`."),
            OpenApiParameter(
                name='exam', type=str, required=False,
                description="H12 chip: only posts hashtagged for this exam: jee | neet | board (`boards` ok) | upsc. "
                            "Same rules as `subject`."),
            OpenApiParameter(
                name='seen_cutoff', type=str, required=False,
                description="Paging-stability token. Do NOT set it on page 1 - the server picks it and "
                            "returns it inside `next`/`previous`; clients just follow those URLs. Posts "
                            "marked seen AFTER this instant don't change the pages of this session.",
            ),
        ],
        tags=["Post Feed"],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """H10: records latency + outcome of every feed request for the SLO (post/feed_ops.py), then runs the
        H2 stats wrapper. 4xx (bad cursor, auth) are the client's problem and do not count as errors; a 5xx, or a
        page that was empty BECAUSE even the fallback failed, does."""
        import time as _time
        from rest_framework.exceptions import APIException
        from . import feed_ops, feed_resilience

        started = _time.perf_counter()
        status_code = 500
        self._degraded = False
        self._slo_error = False
        try:
            response = self._list_measured(request, *args, **kwargs)
            status_code = response.status_code
            if self._degraded:
                response[feed_resilience.DEGRADED_HEADER] = "1"
            return response
        except APIException as exc:
            status_code = exc.status_code
            raise
        finally:
            if status_code >= 500 or self._slo_error:
                outcome = feed_ops.OUTCOME_ERROR
            elif self._degraded:
                outcome = feed_ops.OUTCOME_DEGRADED
            else:
                outcome = feed_ops.OUTCOME_OK
            feed_ops.record_request((_time.perf_counter() - started) * 1000.0, outcome)

    def _list_measured(self, request, *args, **kwargs):
        """H2: thin wrapper. Normally just `_list_guarded`; with settings.FEED_LOADTEST_STATS (staging
        load test only) it also records how many SQL statements this page cost (post/feed_stats.py)."""
        from . import feed_stats

        if not feed_stats.enabled():
            return self._list_guarded(request, *args, **kwargs)
        self._stat_label = 'legacy'  # overwritten by _list_snapshot
        response, n_queries = feed_stats.measure(lambda: self._list_guarded(request, *args, **kwargs))
        if response.status_code == 200:
            feed_stats.record_queries(self._stat_label, n_queries)
        return response

    def _list_guarded(self, request, *args, **kwargs):
        """H10 last line of defence: any unexpected error while ranking / paging / serializing is answered with
        the simple fallback feed (following by recency + recent public) instead of a 500. Deliberate HTTP errors
        (bad cursor 404, 401/403, ...) pass through untouched."""
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
        from rest_framework.exceptions import APIException
        from . import feed_ops, feed_resilience

        if not feed_resilience.is_enabled():
            return self._list_dispatch(request, *args, **kwargs)
        try:
            return self._list_dispatch(request, *args, **kwargs)
        except (APIException, Http404, DjangoPermissionDenied):
            raise
        except Exception as exc:
            feed_ops.note("pool_timeout" if feed_resilience.is_timeout_error(exc) else "pool_error")
            logger.error("feed: unexpected error - serving the fallback feed", exc_info=True)
            try:
                return self._list_snapshot(request, force_degraded=True)
            except (APIException, Http404, DjangoPermissionDenied):
                raise
            except Exception:
                feed_ops.note("fallback_failed")
                logger.error("feed: fallback feed failed too - empty page", exc_info=True)
                self._degraded = True
                self._slo_error = True
                return Response({'count': 0, 'next': None, 'previous': None, 'results': []})

    def _list_dispatch(self, request, *args, **kwargs):
        """Cursor + frozen Redis snapshot by default; the old page/offset code
        path stays for (a) old app builds that send `?page=N` (N > 1) without
        a cursor and (b) settings.FEED_SNAPSHOT["enabled"] = False."""
        from . import feed_snapshot

        self._filter()  # H12: a bad ?subject= / ?category= / ?exam= is a clean 400 before any ranking work
        if feed_snapshot.is_enabled() and (
            request.query_params.get('cursor') or self._requested_page(request) <= 1
        ):
            return self._list_snapshot(request)
        return self._list_legacy(request)

    @staticmethod
    def _requested_page(request):
        try:
            return max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            return 1

    def _page_size(self, request):
        paginator = self.paginator
        try:
            page_size = int(request.query_params.get(paginator.page_size_query_param, paginator.page_size))
        except (TypeError, ValueError):
            page_size = paginator.page_size
        return max(1, min(page_size, paginator.max_page_size))

    def _variant_overrides(self):
        """T1 item 7: the viewer's A/B-variant overrides (mix / diversity / author_cap / quality /
        explore / signals / context), resolved once per request."""
        ov = getattr(self, '_variant_overrides_cache', None)
        if ov is None:
            from . import feed_experiment
            ov = self._variant_overrides_cache = feed_experiment.resolve(self.request.user.pk)['overrides']
        return ov

    def _ratios(self, request):
        from . import feed_mix

        if request.query_params.get('source') == 'following':
            # Following-first tab: only following has ratio; discovery is
            # used purely as a last-resort fill (see allocate_page).
            return {feed_mix.SOURCE_FOLLOWING: 1.0}
        return feed_mix.get_ratios(self._variant_overrides().get('mix'))

    def _serialize_slices(self, pools, slices, ratios, history=None):
        """ids slice per source -> interleaved, serialized post list."""
        from . import feed_mix

        chunks = {src: pools[src][start:end] for src, (start, end) in slices.items()}

        wanted_ids = [pid for ids in chunks.values() for pid in ids]
        posts_by_id = {
            p.id: p for p in
            self._base_qs().filter(id__in=wanted_ids)
        } if wanted_ids else {}

        # a post can be deleted/moderated between the id query and this
        # fetch - just skip it instead of failing the whole page.
        chunk_posts = {
            src: [posts_by_id[pid] for pid in ids if pid in posts_by_id]
            for src, ids in chunks.items()
        }
        merged = feed_mix.interleave(chunk_posts, ratios)
        # T1 Part 1: spread clumps (same author / type / category) inside this
        # page. Reorder only - never drops a post, so paging is unaffected.
        from django.conf import settings as dj_settings
        from . import feed_diversity
        div_cfg = getattr(dj_settings, 'FEED_DIVERSITY', None)
        if self._variant_overrides().get('diversity'):  # T1 item 7: A/B variant override
            div_cfg = {**(div_cfg or {}), **self._variant_overrides()['diversity']}
        # `history` = tail of the previous page (from the cursor, Part 2), so the
        # spacing rules also hold across the page border. None = first page.
        merged = feed_diversity.diversify_posts(merged, div_cfg, history)
        self._diversity_tail = feed_diversity.page_tail(merged, div_cfg, history)
        ordered_posts = [post for _, post in merged]
        self._feed_sources = {post.id: src for src, post in merged}
        self._explore_ids = {post.id for _, post in merged if str(post.id) in getattr(self, '_explore_id_strs', ())}
        return self.get_serializer(ordered_posts, many=True).data

    def _list_snapshot(self, request, force_degraded=False):
        """PART 2 of the cursor-pagination task: rank once, freeze in Redis,
        page through it with an opaque cursor (post/feed_snapshot.py).

        H10: ranking runs under a DB timeout; if it fails (or `force_degraded`) the simple fallback feed is frozen
        into the snapshot instead. Parallel page-1 calls of one user build the pools once (single-flight lock).
        Redis (snapshot / candidate cache) down -> the page is still served, only the freezing is lost."""
        from rest_framework.utils.urls import replace_query_param, remove_query_param
        from . import feed_cache, feed_mix, feed_ops, feed_resilience, feed_snapshot

        page_size = self._page_size(request)
        token = request.query_params.get('cursor')
        state = feed_snapshot.decode_cursor(token) if token else None  # bad cursor -> 404
        flt = self._filter()
        if state and state['filter'] != flt.token:
            # H12: the cursor belongs to a scrolling session of ANOTHER chip (or none). Its offsets are meaningless
            # for this list - 404 like any bad cursor; the app starts a fresh session for the chip it shows.
            from rest_framework.exceptions import NotFound
            raise NotFound("Invalid cursor.")

        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        self._following_ids = following_ids

        snapshot = None if force_degraded else (
            feed_snapshot.load(request.user.pk, state['snapshot_id'], scope=flt.token or None) if state else None
        )
        # H2: which kind of page this is, for post/feed_stats.py (only read when FEED_LOADTEST_STATS is on)
        self._stat_label = 'page1' if not state else ('next_hit' if snapshot is not None else 'next_miss')
        if snapshot is not None:
            # HIT: the ranking is exactly what page 1 saw. No pool queries.
            pools, ratios = snapshot['pools'], snapshot['ratios']
            self._explore_id_strs = set((snapshot.get('meta') or {}).get('explore_ids') or [])
            self._degraded = bool((snapshot.get('meta') or {}).get('degraded'))
            snapshot_id, offsets = state['snapshot_id'], state['offsets']
            cutoff_str = state['seen_cutoff']
        else:
            # NEW session (no cursor) or MISS (expired / cache flushed / another
            # user's cursor): (re)build the pools. On a miss reuse the cursor's
            # own seen_cutoff so the rebuilt pools match the original ones as
            # closely as possible, and continue from its offsets.
            ratios = self._ratios(request)
            if state:
                snapshot_id, offsets = state['snapshot_id'], state['offsets']
                seen_cutoff = feed_mix.resolve_seen_cutoff(state['seen_cutoff'])
            else:
                snapshot_id = feed_snapshot.new_snapshot_id()
                offsets = {src: 0 for src in feed_mix.SOURCES}
                seen_cutoff = feed_mix.resolve_seen_cutoff(request.query_params.get('seen_cutoff'))
            cutoff_str = feed_mix.format_seen_cutoff(seen_cutoff)
            meta = {}
            pools = None
            saved = False
            if not force_degraded:
                pools, meta, ratios, saved = self._rank_pools(
                    request, state, snapshot_id, seen_cutoff, following_ids, ratios,
                )
            if pools is None:
                # ranking failed / timed out (or the outer guard asked for it): fallback feed
                meta = {}
                pools = feed_resilience.fallback_pools(request.user, self._base_qs(), following_ids, meta)
                if request.query_params.get('source') != 'following':
                    ratios = feed_resilience.fallback_ratios()
                if meta.get('fallback_failed'):
                    self._slo_error = True
                saved = False
            # H17: a brand-new user must never get an empty feed - last-resort backfill (page 1 / rebuilds only,
            # never for a chip filter or Exam Mode, see post/feed_coldstart.py).
            if (not flt.token and not meta.get('fallback_failed') and not any(pools.get(s) for s in pools)
                    and not self._exam_mode_on(request.user)):
                from . import feed_coldstart
                filled = feed_coldstart.backfill(request.user, self._base_qs(), following_ids, pools, meta)
                if filled is not pools:
                    pools = filled
                    saved = False
                    if request.query_params.get('source') != 'following':
                        ratios = {src: (1.0 if src == feed_mix.SOURCE_TRENDING else 0.0) for src in feed_mix.SOURCES}
                    self._slo_error = False
            self._degraded = bool(meta.get('degraded'))
            self._explore_id_strs = set(meta.get('explore_ids') or [])
            self._cold_start_backfill = bool(meta.get('cold_start_backfill'))
            if not saved and not meta.get('fallback_failed'):
                if not feed_snapshot.save(request.user.pk, snapshot_id, pools, ratios, meta=meta, scope=flt.token or None):
                    feed_ops.note("snapshot_store_down")

        sizes = {src: len(ids) for src, ids in pools.items()}
        total = sum(sizes.values())
        slices = feed_mix.allocate_next(offsets, page_size, sizes, ratios)
        data = self._serialize_slices(pools, slices, ratios, history=(state or {}).get('tail'))

        new_offsets = {src: end for src, (_, end) in slices.items()}
        has_next = any(new_offsets[src] < sizes[src] for src in feed_mix.SOURCES)
        if force_degraded and not feed_snapshot.is_enabled():
            has_next = False  # the legacy path ignores cursors; one fallback page is all we can honestly page
        next_url = None
        if has_next:
            url = remove_query_param(request.build_absolute_uri(), 'page')
            url = replace_query_param(url, 'cursor', feed_snapshot.encode_cursor(
                snapshot_id, new_offsets, cutoff_str, tail=getattr(self, '_diversity_tail', None),
                filter_token=flt.token,
            ))
            # kept for older clients / tooling that read it; the cursor is what counts
            if cutoff_str:
                url = replace_query_param(url, 'seen_cutoff', cutoff_str)
            next_url = url
        if data:
            self._note_first_feed(request, total, state)
        payload = {
            'count': total,
            'next': next_url,
            'previous': None,
            'results': data,
        }
        if getattr(self, '_cold_start_backfill', False):
            payload['cold_start'] = True  # H17: additive flag - old clients ignore it
        return Response(payload)

    @staticmethod
    def _exam_mode_on(user):
        try:
            from . import feed_exam
            return bool(feed_exam.load_profile(user).exam_mode)
        except Exception:
            return True  # cannot tell -> do not break the Exam Mode promise

    def _note_first_feed(self, request, total, state):
        """H17: funnel - the first page-1 feed of this user (server-side, once; a cache flag keeps the 2nd..Nth
        request from touching the DB). Best effort, never raises."""
        if state or request.query_params.get('source') == 'following':
            return
        try:
            from django.core.cache import cache
            from core import funnel
            from . import feed_coldstart

            user = request.user
            if not cache.add(f'funnel:first_feed:{user.pk}', 1, 24 * 3600):
                return
            funnel.record_first_feed(
                user, count=total, personalised=feed_coldstart.is_personalised(user),
                cold_start_backfill=getattr(self, '_cold_start_backfill', False),
            )
        except Exception:
            logger.warning("feed: first_feed funnel event failed", exc_info=True)

    def _rank_pools(self, request, state, snapshot_id, seen_cutoff, following_ids, ratios):
        """H10: the real ranking, behind the DB timeout and the single-flight lock.

        -> (pools, meta, ratios, snapshot_saved). pools is None when ranking failed / timed out; the caller then
        serves the fallback feed. Which lock applies:
          * new session, cache allowed  -> one builder per (user, ratio): racers read the 30 s candidate cache
          * cursor miss (snapshot gone) -> one builder per snapshot id: racers read the rebuilt snapshot
          * pull-to-refresh / pinned seen_cutoff -> no lock (these must rebuild by definition)"""
        from . import feed_cache, feed_mix, feed_resilience, feed_snapshot

        user = request.user
        flt = self._filter()
        scope = flt.token or None  # H12: part of every cache / snapshot / lock key below
        rec_ratio = feed_mix.normalize_ratios(ratios).get(feed_mix.SOURCE_RECOMMENDED, 0.0)
        # T1 Part 5: short-TTL candidate cache. Skipped for pull-to-refresh (?refresh=1), for a pinned
        # seen_cutoff and for cursor rebuilds, whose pools must match the original session exactly.
        use_cache = (
            not state
            and not request.query_params.get('seen_cutoff')
            and request.query_params.get('refresh') not in ('1', 'true', 'True')
        )
        salt = feed_cache.salt_for(user)

        def build():
            # Seen ids are loaded ONCE and handed to build_pool_ids (window / cap from settings.FEED_SEEN_LIMITS).
            built_meta = {}
            with feed_resilience.statement_timeout():  # Postgres: cancel a runaway ranking query
                seen_ids = feed_mix.get_seen_post_ids(user, until=seen_cutoff)
                built = feed_mix.build_pool_ids(
                    user, self._base_qs(), following_ids, _video_and_velocity_boost,
                    seen_ids=seen_ids, ratios=ratios, meta=built_meta, category=flt.category or None,
                )
            if use_cache:
                feed_cache.put(user.pk, scope, rec_ratio, built, built_meta, salt)
            res = {"pools": built, "meta": built_meta, "ratios": ratios, "saved": False}
            if state:  # cursor rebuild: freeze it NOW so waiting racers find it
                res["saved"] = feed_snapshot.save(user.pk, snapshot_id, built, ratios, meta=built_meta, scope=scope)
            return res

        def probe_candidates():
            hit = feed_cache.get(user.pk, scope, rec_ratio, salt)
            return None if hit is None else {"pools": hit["pools"], "meta": hit["meta"], "ratios": ratios, "saved": False}

        def probe_snapshot():
            hit = feed_snapshot.load(user.pk, snapshot_id, scope=scope)
            return None if hit is None else {"pools": hit["pools"], "meta": hit["meta"], "ratios": hit["ratios"], "saved": True}

        def run():
            if use_cache:
                hit = probe_candidates()
                if hit is not None:
                    return hit
                return feed_resilience.single_flight(
                    feed_resilience.lock_key_for("cand", user.pk, salt, int(round(rec_ratio * 100)), scope or "-"),
                    probe_candidates, build,
                )
            if state:
                return feed_resilience.single_flight(
                    feed_resilience.lock_key_for("snap", user.pk, snapshot_id), probe_snapshot, build,
                )
            return build()

        result, failed = feed_resilience.guarded_build(run)
        if failed:
            return None, {}, ratios, False
        return result["pools"], result["meta"], result["ratios"], result["saved"]

    def _list_legacy(self, request, *args, **kwargs):
        """Old stateless page/offset path (pools rebuilt on every request)."""
        from rest_framework.utils.urls import replace_query_param, remove_query_param
        from . import feed_mix

        paginator = self.paginator
        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get(paginator.page_size_query_param, paginator.page_size))
        except (TypeError, ValueError):
            page_size = paginator.page_size
        page_size = max(1, min(page_size, paginator.max_page_size))

        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        self._following_ids = following_ids

        ratios = feed_mix.get_ratios(self._variant_overrides().get('mix'))
        if request.query_params.get('source') == 'following':
            # Following-first tab: only following has ratio; discovery is
            # used purely as a last-resort fill (see allocate_page).
            ratios = {feed_mix.SOURCE_FOLLOWING: 1.0}

        # PAGING STABILITY: pools are rebuilt on every request, so posts the
        # client marks as seen while scrolling (POST /post/feed/seen/) would
        # shrink/reorder them and shift page 2+. Page 1 fixes a `seen_cutoff`
        # (now); `next`/`previous` carry it along, and only PostView rows with
        # viewed_at <= cutoff count as seen for the whole session. Same code
        # path for source=mixed and source=following.
        seen_cutoff = feed_mix.resolve_seen_cutoff(request.query_params.get('seen_cutoff'))
        # Seen ids are loaded ONCE here and handed to build_pool_ids. Window /
        # cap / on-off switch come from settings.FEED_SEEN_LIMITS.
        seen_ids = feed_mix.get_seen_post_ids(request.user, until=seen_cutoff)

        meta = {}
        pools = feed_mix.build_pool_ids(
            request.user, self._base_qs(), following_ids, _video_and_velocity_boost,
            seen_ids=seen_ids, ratios=ratios, meta=meta, category=self._filter().category or None,
        )
        self._explore_id_strs = set(meta.get('explore_ids') or [])
        sizes = {src: len(ids) for src, ids in pools.items()}
        total = sum(sizes.values())
        slices = feed_mix.allocate_page(page, page_size, sizes, ratios)
        data = self._serialize_slices(pools, slices, ratios)

        consumed_after = sum(end for _, end in slices.values())
        has_next = consumed_after < total
        # `source`, `page_size`, ... are already part of the request URL, so
        # they are forwarded as-is; `seen_cutoff` is pinned on top of them.
        url = replace_query_param(
            request.build_absolute_uri(), 'seen_cutoff', feed_mix.format_seen_cutoff(seen_cutoff),
        )
        next_url = replace_query_param(url, 'page', page + 1) if has_next else None
        prev_url = None
        if page > 1:
            prev_url = replace_query_param(url, 'page', page - 1) if page > 2 else remove_query_param(url, 'page')
        return Response({
            'count': total,
            'next': next_url,
            'previous': prev_url,
            'results': data,
        })

# ===================== USER POSTS LIST =====================
class PostListAPIView(generics.ListAPIView):
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'content', 'hashtags']
    ordering_fields = ['created_at', 'likes_count', 'views_count']
    ordering = ['-is_pinned', '-created_at']  # P3: pinned posts first; an explicit ?ordering= overrides this
    def get_serializer_context(self):
        return {'request': self.request}
    def filter_queryset(self, queryset):
        # P2: ?sort=latest|popular - pinned posts stay on top either way; an explicit ?ordering= still wins.
        qs = super().filter_queryset(queryset)
        if self.request.query_params.get('ordering'):
            return qs
        from .profile_tabs import order_profile_posts
        return order_profile_posts(qs, self.request.query_params)
    @extend_schema(
        parameters=[
            OpenApiParameter(name='target_user_id', type=str, description='User UUID'),
            OpenApiParameter(name='category', type=str, description='Filter by category'),
            OpenApiParameter(name='post_type', type=str, description='Filter by type'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def get_queryset(self):
        request_user = self.request.user
        target_user_id = self.request.query_params.get('target_user_id')
        base_qs = _without_superseded_reposts(Post.objects.select_related('user', 'original_post__user').prefetch_related('media', 'original_post__media').filter(is_deleted=False,is_archived=False,moderation_status='approved',is_scheduled=False))
        from .profile_tabs import filter_profile_posts  # P2
        if not target_user_id or str(request_user.id) == target_user_id:
            return filter_profile_posts(base_qs.filter(user=request_user), self.request.query_params).order_by('-created_at')
        try:
            target_user = User.objects.get(id=target_user_id)
        except User.DoesNotExist:
            return Post.objects.none()
        # Block (either direction) => the grid is empty, same as "no such user".
        from user_profile.views import is_blocked_between
        if is_blocked_between(request_user, target_user):
            return Post.objects.none()
        is_following = Follow.objects.filter(follower=request_user,following=target_user,status=Follow.Status.ACCEPTED).exists()
        if target_user.is_private and not is_following:
            return Post.objects.none()
        queryset = filter_profile_posts(base_qs.filter(user=target_user), self.request.query_params)  # P2: post_type list / kind / category
        if is_following:
            return queryset.filter(Q(visibility='public') | Q(visibility='connections')).order_by('-created_at')
        return queryset.filter(visibility='public').order_by('-created_at')

# ===================== POST DETAIL =====================
class PostDetailAPIView(generics.RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostDetailSerializer
    queryset = Post.objects.select_related('user', 'original_post__user').prefetch_related('media','comments__user','comments__replies','original_post__media')
    lookup_field = 'id'
    def get_serializer_context(self):
        return {'request': self.request}
    @extend_schema(description='Get single post with comments and view tracking')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        from .moderation_guard import post_hidden_for
        if post_hidden_for(request.user, instance):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, instance.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if instance.visibility == 'private' and instance.user!= request.user:
            return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility == 'connections':
            is_following = Follow.objects.filter(follower=request.user,following=instance.user,status=Follow.Status.ACCEPTED).exists()
            if not is_following and instance.user!= request.user:
                return Response({"success": False, "message": "Only connections can view"}, status=status.HTTP_403_FORBIDDEN)
        if instance.visibility in ('close_friends', 'class_campus') and instance.user_id != request.user.id:
            # P1 audiences. 404 (not 403) so the post's existence is not leaked.
            from .audience import can_view_post
            if not can_view_post(instance, request.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        # FIX (post_app.md §14 issue #1): `views_count` used to increment
        # unconditionally on every request, even repeat visits by the same
        # user, even though `PostView` itself was already correctly deduped
        # via get_or_create. Now the counter only moves on a genuinely new
        # (post, user) pair — "unique viewers" semantics, matching what
        # `PostView`'s own uniqueness already implies.
        #
        # A row may already exist because the feed reported the post as
        # "seen" (PostSeenBatchAPIView, `is_counted=False`). The first real
        # open still has to count once, so promote that row atomically —
        # the conditional UPDATE guarantees only one concurrent request
        # wins the promotion, so views_count can't double-increment.
        view, is_new_view = PostView.objects.get_or_create(post=instance, user=request.user)
        if is_new_view:
            should_count = True
        else:
            should_count = PostView.objects.filter(pk=view.pk, is_counted=False).update(is_counted=True) > 0
        if should_count:
            Post.objects.filter(id=instance.id).update(views_count=F('views_count') + 1)
        serializer = self.get_serializer(instance)
        return Response({"success": True,"data": serializer.data})

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
            from . import feed_cache
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

# ===================== POST REPOST =====================
# Instagram/Twitter-style repost. A repost is a REAL `Post` row
# (`post_type='repost'`, `original_post` -> the post being reposted), so
# feeds, profile lists, likes, comments, delete and `posts_count` all work
# on it with no special casing. `Post.reposts_count` on the original is
# kept in sync by `update_reposts_count` (models.py signal), not here.
#
# Policy decisions (all easy to flip in one place):
#   * Chains are flattened: reposting a repost points the new row at the
#     ROOT original, so the embedded preview is always one level deep.
#   * Duplicates ARE allowed (same user, same original, any number of
#     times) — but `_without_superseded_reposts` (above) makes feeds show
#     only the latest one. To forbid instead, add an `.exists()` check on
#     (user, original_post, is_deleted=False) below and return 409.
#   * Only public, approved, already-published posts can be reposted, and
#     not from a private account the reposter doesn't follow, and not
#     across a block — a repost re-publishes the content to the
#     reposter's own audience, so it must not widen who can see it.
#   * Undo = the existing `DELETE /post/<repost_id>/delete/` (soft-delete;
#     the signal recounts `reposts_count` without it).
class PostRepostAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Repost a post (optionally with a caption)",
        description="Creates a new Post owned by the caller with `original_post` "
                    "set to the target (or to the target's root original if the "
                    "target is itself a repost). Body is optional: "
                    "`{\"repost_caption\": \"...\"}` (max 500 chars).",
        request=RepostRequestSerializer,
        responses={201: OpenApiTypes.OBJECT},
        tags=["Post Repost"],
    )
    @transaction.atomic
    def post(self, request, post_id):
        from user_profile.views import is_blocked_between  # lazy, same as services.py's cross-app imports

        source = get_object_or_404(
            Post.objects.select_related('user', 'original_post__user'), id=post_id, is_deleted=False,
        )
        original = source.original_post if source.original_post_id else source

        if original.is_deleted or original.is_archived or original.moderation_status != 'approved' or original.is_scheduled:
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if original.visibility != 'public':
            return Response({"success": False, "message": "Only public posts can be reposted"}, status=status.HTTP_403_FORBIDDEN)
        from .post_options import RESHARE_OFF_CODE, RESHARE_OFF_TEXT, can_reshare_post  # S4b
        if not can_reshare_post(original, request.user):
            return Response({"success": False, "code": RESHARE_OFF_CODE, "message": RESHARE_OFF_TEXT},
                            status=status.HTTP_403_FORBIDDEN)
        if original.user_id != request.user.id:
            if is_blocked_between(request.user, original.user):
                return Response({"success": False, "message": "You can't repost this post"}, status=status.HTTP_403_FORBIDDEN)
            if original.user.is_private and not Follow.objects.filter(
                follower=request.user, following=original.user, status=Follow.Status.ACCEPTED,
            ).exists():
                return Response({"success": False, "message": "This account is private"}, status=status.HTTP_403_FORBIDDEN)

        body = RepostRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        repost = Post.objects.create(
            user=request.user,
            post_type='repost',
            original_post=original,
            repost_caption=body.validated_data.get('repost_caption'),
            # Keeps category-filtered lists working on the repost row; it
            # carries no content/media/hashtags of its own.
            category=original.category,
            subcategory=original.subcategory,
            # S4b: a repost starts from the author's account-level audience (public unless they chose otherwise).
            visibility=_content_defaults.post_visibility_default(request.user, None),
        )

        # Task 3.4: tell the original's owner (batched, never raises).
        notify_post_reposted(repost)

        # `update_reposts_count` (signal) already recounted on create.
        original.refresh_from_db(fields=['reposts_count'])
        return Response({
            "success": True,
            "data": PostListSerializer(repost, context={'request': request}).data,
            "original": {
                "id": str(original.id),
                "reposts_count": original.reposts_count,
                "is_reposted_by_me": True,
            },
        }, status=status.HTTP_201_CREATED)


# ===================== SHARE A CLASS / TEST SERIES TO THE FEED =====================
# POST /post/share-item/  {"type": "classroom"|"testseries", "id": "<uuid>", "caption": "..."}
# Creates an ordinary public Post (post_type='text') that carries the item card.
# Commission is NOT decided here: the card's `ref_code` (see shared_items.py)
# is only present when the item's owner allows commission, and is applied
# through the normal link attribution when someone opens the card.
class PostShareItemAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_create"

    @transaction.atomic
    def post(self, request):
        from .shared_items import SHARE_TYPES, can_share

        shared_type = str(request.data.get("type", "")).strip().lower()
        shared_id = str(request.data.get("id", "")).strip()
        caption = str(request.data.get("caption", "") or "").strip()[:500]
        if shared_type not in SHARE_TYPES or not shared_id:
            return Response({"success": False, "message": "Invalid item."}, status=status.HTTP_400_BAD_REQUEST)
        if not can_share(shared_type, shared_id):
            return Response({"success": False, "message": "This item can't be shared right now."},
                            status=status.HTTP_404_NOT_FOUND)
        post = Post.objects.create(
            user=request.user, post_type='text', content=caption or None, category='education',
            visibility=_content_defaults.post_visibility_default(request.user, None),  # S4b account default
            shared_type=shared_type, shared_id=shared_id,
        )
        return Response(
            {"success": True, "data": PostListSerializer(post, context={'request': request}).data},
            status=status.HTTP_201_CREATED,
        )


# ===================== POST DELETE =====================
# NEW — checklist item 57 ("create/list/delete Post") mentioned delete but
# no such endpoint existed anywhere in urls.py/views.py. Soft-delete only,
# author-or-staff, mirroring CommentDeleteAPIView's pattern exactly
# (is_deleted/deleted_at, never a real row removal).
class PostDeleteAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Soft-delete a post", tags=["Post"])
    @transaction.atomic
    def delete(self, request, id):
        from django.shortcuts import get_object_or_404

        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id and not request.user.is_staff:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)

        post.is_deleted = True
        post.deleted_at = timezone.now()
        post.save(update_fields=["is_deleted", "deleted_at"])
        decrement_posts_count_on_soft_delete(post)
        from .hashtags import release_post_hashtags   # P1: keep Hashtag.posts_count honest
        release_post_hashtags(post)
        if post.post_type == "repost" and post.original_post_id:
            unnotify_post_reposted(post)  # Task 3.4: undo repost drops it from the open batch

        return Response({"success": True, "message": "Post deleted"}, status=status.HTTP_204_NO_CONTENT)


# ===================== POST EDIT =====================
# NEW — no PATCH/edit endpoint existed anywhere in this app before this
# (urls.py had create/list/detail/delete/repost and nothing else touching
# a post's own fields), and singlepost.dart had no edit UI either. Text/
# category fields only — see PostEditSerializer's docstring for exactly
# what's editable and why media/visibility/post_type are excluded.
class PostEditAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Edit a post's own text/category fields", request=PostEditSerializer, tags=["Post"])
    @transaction.atomic
    def patch(self, request, id):
        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        from .moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.post_type == "repost":
            # A repost row carries no content/media of its own to edit —
            # the embedded preview always reflects `original_post` live.
            return Response({"success": False, "message": "Reposts can't be edited"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PostEditSerializer(post, data=request.data, partial=True, context={"request": request})
        if not serializer.is_valid():
            return Response(
                {"success": False, "message": _first_error_text(serializer.errors) or "Validation failed", "errors": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not serializer.validated_data:
            return Response({"success": False, "message": "Nothing to update"}, status=status.HTTP_400_BAD_REQUEST)

        from .minor_privacy import location_blocked
        if location_blocked(request.user) and "location" in serializer.validated_data:
            serializer.validated_data["location"] = {}  # H23: minors never store a location
        serializer.save()
        return Response({"success": True, "message": "Post updated", "data": PostDetailSerializer(post, context={"request": request}).data})


# ===================== POST VISIBILITY =====================
# NEW — `Post.visibility` was previously set once, at create time
# (PostCreateSerializer), with no way to change it afterwards. Kept as
# its own endpoint rather than folded into PostEditAPIView above: a
# privacy change is a different action from a content edit (doesn't set
# `is_edited` / show an "edited" label — same distinction Instagram makes)
# and has no other field it needs to travel with.
class PostVisibilityAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Change a post's visibility", request=PostVisibilitySerializer, tags=["Post"])
    @transaction.atomic
    def patch(self, request, id):
        post = get_object_or_404(Post, id=id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        from .moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.post_type == "repost":
            return Response({"success": False, "message": "Reposts can't be edited"}, status=status.HTTP_400_BAD_REQUEST)

        body = PostVisibilitySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        new_visibility = body.validated_data["visibility"]
        from .audience import class_recording_widen_error
        widen = class_recording_widen_error(post, request.user, new_visibility)
        if widen:
            return Response({"success": False, "code": "class_recording_locked", "message": widen},
                            status=status.HTTP_403_FORBIDDEN)
        if new_visibility != post.visibility:
            post.visibility = new_visibility
            post.save(update_fields=["visibility"])

        return Response({"success": True, "message": "Visibility updated", "data": {"id": str(post.id), "visibility": post.visibility}})


# ===================== PIN / UNPIN (P3-BE) =====================
MAX_PINNED_POSTS = 3


def _pin_state(user, post):
    return {
        "is_pinned": post.is_pinned,
        "pinned_count": Post.objects.filter(user=user, is_pinned=True, is_deleted=False).count(),
        "max_pinned": MAX_PINNED_POSTS,
    }


class PostPinAPIView(APIView):
    """POST /post/<id>/pin/ pins one of MY posts to the top of my profile grid (max 3, idempotent);
    DELETE unpins (idempotent). Owner only."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Pin a post to my profile", tags=["Post"])
    @transaction.atomic
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        if post.moderation_status != "approved" or post.is_scheduled or post.is_archived:
            return Response({"success": False, "code": "post_not_pinnable",
                             "message": "Only published, approved posts can be pinned."},
                            status=status.HTTP_400_BAD_REQUEST)
        # serialise concurrent pins of the same user so the limit can't be raced past
        User.objects.select_for_update().filter(pk=request.user.pk).first()
        if not post.is_pinned:
            if Post.objects.filter(user=request.user, is_pinned=True, is_deleted=False).count() >= MAX_PINNED_POSTS:
                return Response({"success": False, "code": "pin_limit_reached", "max_pinned": MAX_PINNED_POSTS,
                                 "message": f"You can pin up to {MAX_PINNED_POSTS} posts. Unpin one first."},
                                status=status.HTTP_400_BAD_REQUEST)
            post.is_pinned = True
            post.save(update_fields=["is_pinned"])
        return Response({"success": True, "data": _pin_state(request.user, post)})

    @extend_schema(summary="Unpin a post from my profile", tags=["Post"])
    @transaction.atomic
    def delete(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        if post.is_pinned:
            post.is_pinned = False
            post.save(update_fields=["is_pinned"])
        return Response({"success": True, "data": _pin_state(request.user, post)})


# ===================== POST ARCHIVE (feed card ⋮ menu) =====================
# Archive = "hide it from everyone, keep it". Unlike delete it is reversible, so the app calls it
# straight away and offers Undo (DELETE below). Owner only; idempotent both ways.
#   POST   /post/<id>/archive/   archive (also unpins; leaves posts_count + hashtag counts)
#   DELETE /post/<id>/archive/   restore (counts come back; it is NOT re-pinned)
#   GET    /post/archived/       my archived posts, most recently archived first
# Where archived posts are hidden: home feed, explore, reels, hashtag, profile grid, saved, tagged tab,
# direct link / react / comment (post_hidden_for), reposts of it, leaderboard, doubts.
def _archive_state(post):
    return {"id": str(post.id), "is_archived": post.is_archived, "archived_at": post.archived_at}


class PostArchiveAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_own(self, request, post_id):
        post = get_object_or_404(Post.objects.select_for_update(), id=post_id, is_deleted=False)
        if post.user_id != request.user.id:
            return None, Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        return post, None

    @extend_schema(summary="Archive one of my posts", tags=["Post"])
    @transaction.atomic
    def post(self, request, post_id):
        post, err = self._get_own(request, post_id)
        if err is not None:
            return err
        from .moderation_guard import post_locked_by_copyright
        if post_locked_by_copyright(post):
            return Response({"success": False, "message": "This post is under a copyright review and can't be changed."},
                            status=status.HTTP_403_FORBIDDEN)
        if post.is_scheduled:
            return Response({"success": False, "code": "post_not_archivable",
                             "message": "A scheduled post can't be archived. Edit or delete the schedule instead."},
                            status=status.HTTP_400_BAD_REQUEST)
        if not post.is_archived:
            post.is_archived = True
            post.archived_at = timezone.now()
            post.is_pinned = False
            post.save(update_fields=["is_archived", "archived_at", "is_pinned"])
            decrement_posts_count_on_soft_delete(post)
            from .hashtags import release_post_hashtags
            release_post_hashtags(post)
        return Response({"success": True, "data": _archive_state(post)})

    @extend_schema(summary="Restore (unarchive) one of my posts", tags=["Post"])
    @transaction.atomic
    def delete(self, request, post_id):
        post, err = self._get_own(request, post_id)
        if err is not None:
            return err
        if post.is_archived:
            post.is_archived = False
            post.archived_at = None
            post.save(update_fields=["is_archived", "archived_at"])
            from .signals import increment_posts_count_on_unarchive
            increment_posts_count_on_unarchive(post)
            from .hashtags import sync_post_hashtags
            sync_post_hashtags(post, old_tags=[])
        return Response({"success": True, "data": _archive_state(post)})


class ArchivedPostsListAPIView(generics.ListAPIView):
    """GET /post/archived/ - my own archived posts, newest archive first (paginated)."""
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(summary="My archived posts", tags=["Post"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return (
            Post.objects.select_related('user', 'original_post__user')
            .prefetch_related('media', 'original_post__media')
            .filter(user=self.request.user, is_deleted=False, is_archived=True)
            .order_by('-archived_at', '-created_at')
        )


# ===================== TAGGED TAB + TAG MANAGEMENT (P5a-BE) =====================
class PostTaggedAPIView(generics.ListAPIView):
    """GET /post/tagged/<username>/ - posts `username` is tagged in (newest tag first), paginated.
    Each row is a normal post row plus `tag: {x, y}`; `is_hidden` is added for the tab's owner only.
    ?include_hidden=1 (owner only) also lists posts the owner hid from their tab."""
    permission_classes = [IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def list(self, request, username):
        from user_profile.services import blocked_user_ids as _blocked_ids
        from .audience import filter_posts_visible_to
        from .models import PostTag

        owner = User.objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            return Response({"success": False, "message": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        viewer = request.user
        is_owner = owner.pk == viewer.pk
        following_ids = set(Follow.objects.filter(
            follower=viewer, status=Follow.Status.ACCEPTED).values_list("following_id", flat=True))

        qs = PostTag.objects.none()
        if is_owner or not (owner.is_private and owner.pk not in following_ids) and owner.pk not in _blocked_ids(viewer):
            visible = filter_posts_visible_to(Post.objects.all(), viewer).values("pk")
            qs = PostTag.objects.filter(
                tagged_user=owner, post__in=visible,
                post__is_deleted=False, post__is_archived=False, post__moderation_status="approved", post__is_scheduled=False,
            ).exclude(
                Q(post__user__is_private=True) & ~Q(post__user_id__in=following_ids) & ~Q(post__user_id=viewer.pk)
            ).exclude(post__user_id__in=_blocked_ids(viewer))
            if not (is_owner and request.query_params.get("include_hidden") in ("1", "true", "True")):
                qs = qs.filter(is_hidden=False)
        qs = qs.select_related("post__user", "post__original_post__user").prefetch_related(
            "post__media", "post__original_post__media",
        ).order_by("-created_at", "-id")

        page = self.paginate_queryset(qs)
        tags = list(page) if page is not None else list(qs)
        data = PostListSerializer([t.post for t in tags], many=True, context={"request": request}).data
        rows = []
        for tag, row in zip(tags, data):
            row = dict(row)
            row["tag"] = {"x": tag.x, "y": tag.y}
            if is_owner:
                row["tag"]["is_hidden"] = tag.is_hidden
            rows.append(row)
        if page is not None:
            return self.get_paginated_response(rows)
        return Response(rows)


class PostTagAPIView(APIView):
    """PATCH /post/<id>/tag/ {"hidden": bool} - the tagged user hides/unhides the post on THEIR Tagged tab.
    DELETE /post/<id>/tag/ - remove MY tag (idempotent); the post's author (or staff) may pass ?user_id=<n>
    to remove someone else's."""
    permission_classes = [IsAuthenticated]

    def patch(self, request, post_id):
        from .models import PostTag
        hidden = request.data.get("hidden")
        if not isinstance(hidden, bool):
            return Response({"success": False, "message": "'hidden' must be true or false."},
                            status=status.HTTP_400_BAD_REQUEST)
        tag = PostTag.objects.filter(post_id=post_id, post__is_deleted=False, tagged_user=request.user).first()
        if tag is None:
            return Response({"success": False, "message": "You are not tagged in this post."},
                            status=status.HTTP_404_NOT_FOUND)
        if tag.is_hidden != hidden:
            tag.is_hidden = hidden
            tag.save(update_fields=["is_hidden"])
        return Response({"success": True, "data": {"hidden": tag.is_hidden}})

    @transaction.atomic
    def delete(self, request, post_id):
        from .models import PostTag
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        target_id = request.user.pk
        raw = request.query_params.get("user_id")
        if raw not in (None, ""):
            try:
                target_id = int(raw)
            except (TypeError, ValueError):
                return Response({"success": False, "message": "Invalid user_id"}, status=status.HTTP_400_BAD_REQUEST)
            if target_id != request.user.pk and post.user_id != request.user.pk and not request.user.is_staff:
                return Response({"success": False, "message": "Not allowed"}, status=status.HTTP_403_FORBIDDEN)
        deleted, _ = PostTag.objects.filter(post=post, tagged_user_id=target_id).delete()
        return Response({"success": True, "data": {"removed": bool(deleted)}})


# ===================== POST SHARE (internal "forward to chat") =====================
# NEW — checklist item 61. The actual send/broadcast logic lives in
# `share_post_to_conversation` (services.py, now fixed — see its comment
# for the ImportError bug this replaces); this view is just the missing
# URL-facing half: auth, post visibility check, request validation, and
# turning the service's exceptions into the right HTTP status. This is
# NOT the OS share sheet (`Share.share(...)` in singlepost.dart, which
# never touches the backend) — this is forwarding a post into an
# in-app chat as a message.
def _share_guard(request, post):
    """Shared by PostShareAPIView (send in chat) and PostShareLinkAPIView (share a link):
    `None` when `request.user` may share `post`, else the ready-made error Response
    (never reveals more than the post being "not found" / private)."""
    from .moderation_guard import post_hidden_for

    if post_hidden_for(request.user, post):
        return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    if post.visibility == "private" and post.user_id != request.user.id:
        return Response({"success": False, "message": "Post is private"}, status=status.HTTP_403_FORBIDDEN)
    if post.visibility in ("close_friends", "class_campus") and post.user_id != request.user.id:
        from .audience import can_view_post
        if not can_view_post(post, request.user):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    if post.user_id != request.user.id:
        from user_profile.views import is_blocked_between
        if is_blocked_between(request.user, post.user):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
    # S4b: the author switched resharing off (send in chat + share link both come through here).
    from .post_options import RESHARE_OFF_CODE, RESHARE_OFF_TEXT, can_reshare_post
    if not can_reshare_post(post, request.user):
        return Response({"success": False, "code": RESHARE_OFF_CODE, "message": RESHARE_OFF_TEXT},
                        status=status.HTTP_403_FORBIDDEN)
    return None


class PostShareAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Forward a post into a chat conversation", request=PostShareRequestSerializer, tags=["Post"])
    def post(self, request, id):
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

        from message.models import Conversation

        post = get_object_or_404(Post, id=id, is_deleted=False)
        denied = _share_guard(request, post)
        if denied is not None:
            return denied

        body = PostShareRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)

        try:
            message = share_post_to_conversation(post, request.user, body.validated_data["conversation_id"])
        except Conversation.DoesNotExist:
            return Response({"success": False, "message": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
        except DjangoPermissionDenied as exc:
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_403_FORBIDDEN)

        return Response(
            {
                "success": True,
                "message": "Post shared",
                "data": {"message_id": str(message.id), "conversation_id": str(message.conversation_id)},
            },
            status=status.HTTP_201_CREATED,
        )


class PostShareLinkAPIView(APIView):
    """POST /post/<id>/share-link/ - the app is about to hand the post's DEEP LINK to the OS share sheet
    (WhatsApp, Instagram, ...). Records one `PostShare` per (post, user) so `shares_count` is right
    (idempotent) and answers with the canonical link built from `settings.APP_WEB_BASE_URL`:
    `<base>/p/<post_id>` (+ `?v=reel` for videos, which the app opens in Reels). The raw media file
    URL is never part of a share."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Record a link share and get the post's deep link", request=None, tags=["Post"])
    def post(self, request, id):
        from .models import PostShare

        post = get_object_or_404(Post, id=id, is_deleted=False, is_archived=False)
        denied = _share_guard(request, post)
        if denied is not None:
            return denied
        PostShare.objects.get_or_create(post=post, user=request.user)
        post.refresh_from_db(fields=["shares_count"])
        base = str(getattr(settings, "APP_WEB_BASE_URL", "") or "").rstrip("/")
        url = f"{base}/p/{post.id}" + ("?v=reel" if post.post_type == "video" else "") if base else ""
        return Response({"success": True, "data": {"url": url, "shares_count": post.shares_count}})


# ===================== CHUNKED UPLOAD — POST (TASK 3 / TASK 4) =====================
# Fixes the 404: api_service.dart's initPostChunkedUpload/
# completePostChunkedUpload hit /post/chunked/init/ and /post/chunked/
# complete/, and getChunkedUploadStatus hits /post/chunked/status/<id>/ —
# none of these routes existed before this (see urls.py). This is Option B
# from the task: dedicated post routes/views, backed by their own
# `PostChunkedUpload` model (models.py) rather than overloading the
# comment-shaped `ChunkedUpload`, sharing the actual chunk-storage
# mechanics with the comment flow via Services.save_uploaded_chunk /
# assemble_chunks / list_received_chunks / CHUNK_UPLOAD_MAX_SIZE.
#
# The chunk-upload step itself is NOT duplicated here on purpose:
# api_service.dart's uploadPostChunk() deliberately posts to the existing
# /post/comment/chunked/chunk/ route (comment_view.py's
# chunked_upload_chunk, now updated to look up either ChunkedUpload or
# PostChunkedUpload by upload_id) — see that file's own comment on why.
# Only init/complete/status are post-specific, because only they touch
# post-creation fields.

@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser])
def post_chunked_upload_init(request):
    """Start a chunked (large-video) post upload. Stores the post-creation
    payload (title/content/category/... — same fields api_service.dart's
    non-chunked createPost() sends) on a PostChunkedUpload row, to be
    applied once the chunks are assembled in post_chunked_upload_complete.
    """
    from .legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/init')
    if _legacy is not None:
        return _legacy
    try:
        file_name = request.data.get('file_name')
        total_chunks = int(request.data.get('total_chunks', 0))
        total_size = int(request.data.get('total_size', 0))

        if not file_name or total_chunks == 0 or total_size == 0:
            return Response({"error": "file_name, total_chunks, total_size required"}, status=400)

        if total_size > CHUNK_UPLOAD_MAX_SIZE:
            return Response({"error": "File too large. Max 4GB allowed"}, status=400)

        # Polls / quizzes are not carried by this legacy path (it holds ONE video file and no poll rows, option
        # images or poll_settings). Say so instead of silently creating a video post without its poll - the app's
        # upload queue (/post/uploads/...) and POST /post/create/ both support polls.
        if (request.data.get('post_type') == 'poll' or request.data.get('poll_options')
                or request.data.get('poll_settings')):
            return Response(
                {"error": "Polls cannot be created with a chunked upload. Use POST /post/create/.",
                 "code": "poll_not_supported_chunked"},
                status=400,
            )

        hashtags = request.data.get('hashtags')
        if not isinstance(hashtags, list):
            hashtags = []
        location = request.data.get('location')
        if not isinstance(location, dict):
            location = {}
        from .minor_privacy import scrub_location
        location = scrub_location(request.user, location)  # H23: minors never store a location

        # Same category rule as PostCreateSerializer — this path used to
        # store any string the client sent (e.g. "null"), which then landed
        # on the final Post unvalidated in post_chunked_upload_complete.
        raw_category = request.data.get('category')
        if raw_category in (None, ''):
            category = 'general'
        else:
            category = normalize_category(raw_category)
            if category is None:
                message = category_error_message(raw_category)
                return Response(
                    {"success": False, "message": message, "error": message,
                     "errors": {"category": [message]}},
                    status=400,
                )

        upload_id = str(uuid.uuid4())
        PostChunkedUpload.objects.create(
            upload_id=upload_id,
            file_name=file_name,
            total_chunks=total_chunks,
            total_size=total_size,
            user=request.user,
            title=request.data.get('title') or '',
            content=request.data.get('content') or '',
            category=category,
            subcategory=(str(request.data.get('subcategory') or '').strip() or None),
            post_type=request.data.get('post_type') or 'video',
            visibility=_content_defaults.post_visibility_default(request.user, request.data.get('visibility')),  # S4b
            hashtags=hashtags,
            location=location,
            media_caption=request.data.get('media_caption') or '',
            media_type=request.data.get('media_type') or '',
        )
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'temp_chunks', upload_id), exist_ok=True)
        return Response({"upload_id": upload_id, "message": "Ready for chunks"}, status=200)
    except Exception as e:
        logger.error(f'post_chunked_upload_init failed: {e}', exc_info=True)
        return Response({"error": str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def post_chunked_upload_status(request, upload_id):
    """TASK 4 — tells the client which chunks the server already has, so
    ApiService.createPostWithChunkedUpload can resume an interrupted
    upload by only re-sending the missing ones instead of starting over.
    """
    from .legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/status')
    if _legacy is not None:
        return _legacy
    upload = get_object_or_404(PostChunkedUpload, upload_id=upload_id, user=request.user)
    return Response({
        "upload_id": upload.upload_id,
        "is_completed": upload.is_completed,
        "total_chunks": upload.total_chunks,
        "received_chunks": list_received_chunks(upload.upload_id),
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@parser_classes([parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser])
@transaction.atomic
def post_chunked_upload_complete(request):
    """Assemble the uploaded chunks into the final video file and create
    the Post + its single PostMedia row from the payload captured at
    init() time. Response shape matches PostCreateAPIView.post()
    ({"success", "message", "data"}, 201) since
    ApiService.completePostChunkedUpload reads `data['message']` on
    failure the same way the non-chunked create path's error does.
    """
    from .legacy_chunked import legacy_chunked_gate
    _legacy = legacy_chunked_gate(request, 'chunked/complete')
    if _legacy is not None:
        return _legacy
    try:
        upload_id = request.data.get('upload_id')
        if not upload_id:
            return Response({"success": False, "message": "upload_id required"}, status=400)

        upload = get_object_or_404(PostChunkedUpload, upload_id=upload_id, user=request.user)
        if upload.is_completed:
            # Guards against a double-tap/retry re-creating a second Post
            # for the same upload — the client's own resume logic checks
            # is_completed via the status endpoint first, but that's a
            # separate request/race, not a guarantee.
            return Response({"success": False, "message": "Upload already completed"}, status=400)

        # The chunked path used to skip the copyright repeat-infringer gate that /post/create/ and
        # stories apply - a blocked account could still publish a video this way.
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)

        final_dir = os.path.join(
            settings.MEDIA_ROOT, 'posts',
            str(timezone.now().year), f"{timezone.now().month:02d}", f"{timezone.now().day:02d}",
        )
        final_file_name = f"{uuid.uuid4()}_{upload.file_name}"
        try:
            final_path = assemble_chunks(upload.upload_id, upload.total_chunks, final_dir, final_file_name)
        except FileNotFoundError as e:
            return Response({"success": False, "message": str(e)}, status=400)

        relative_path = os.path.relpath(final_path, settings.MEDIA_ROOT)

        ok, code, why = precheck_upload(request.user, paths=[final_path])
        if not ok:
            try:
                os.remove(final_path)  # nothing references it yet
            except OSError:
                logger.warning("could not delete blocked chunked upload %s", final_path, exc_info=True)
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)

        # H23 publish gate (same checks as POST /post/create/; the chunked path used to skip all of them).
        from . import minor_privacy, publish_gate
        verdict = publish_gate.evaluate(
            request.user, "post",
            texts=[t for t in (upload.title, upload.content) if t],
            paths=[final_path], media_types=[upload.media_type or "video"],
        )
        if not verdict.ok:
            try:
                os.remove(final_path)  # nothing references it yet
            except OSError:
                logger.warning("could not delete refused chunked upload %s", final_path, exc_info=True)
            return verdict.response()

        # Same hashtag extraction + normalization PostCreateSerializer.create()
        # does for the non-chunked path (serializers.py) — kept identical so
        # a chunked video post's tags are searchable the same way a regular
        # post's are (HashtagPostsAPIView / TrendingHashtagsAPIView both
        # match on normalized, lowercase, no-'#' tags).
        hashtags = upload.hashtags or []
        if not hashtags and upload.content:
            hashtags = re.findall(r"#(\w+)", upload.content)
        seen = []
        for tag in hashtags:
            normalized = tag.strip().lstrip('#').lower()
            if normalized and normalized not in seen:
                seen.append(normalized)
        hashtags = seen

        base_text = upload.title or (upload.content[:50] if upload.content else '') or str(uuid.uuid4())[:8]
        slug = slugify(base_text)[:200] or uuid.uuid4().hex[:8]
        if Post.objects.filter(slug=slug).exists():
            slug = f"{slug}-{uuid.uuid4().hex[:6]}"

        post = Post.objects.create(
            user=request.user,
            title=upload.title or None,
            content=upload.content,
            category=upload.category or 'general',
            subcategory=upload.subcategory or None,
            post_type=upload.post_type or 'video',
            visibility=upload.visibility or 'public',
            hashtags=hashtags,
            location=minor_privacy.scrub_location(request.user, upload.location),
            slug=slug,
            moderation_status=verdict.moderation_status,
            **_content_defaults.post_extra_fill(request.user, {}),  # S4b: comments limit / reshare / save defaults
        )

        media_type = upload.media_type or 'video'
        if media_type not in dict(PostMedia.MEDIA_TYPE_CHOICES):
            media_type = 'video'
        mime_type = 'video/mp4' if media_type == 'video' else 'application/octet-stream'

        PostMedia.objects.create(
            post=post,
            media_type=media_type,
            file=relative_path,
            file_name=upload.file_name,
            file_size_bytes=upload.total_size,
            mime_type=mime_type,
            display_order=0,
        )

        upload.is_completed = True
        upload.save(update_fields=['is_completed'])
        publish_gate.after_create(verdict, request.user, "post", post)

        logger.info(f'Post created via chunked upload: {post.id} by {request.user.id}')
        output_serializer = PostCreateSerializer(post, context={'request': request})
        return Response(
            {"success": True, "message": "Post created successfully", "data": output_serializer.data,
             **verdict.held_payload()},
            status=status.HTTP_201_CREATED,
        )
    except Exception as e:
        logger.error(f'post_chunked_upload_complete failed: {e}', exc_info=True)
        return Response({"success": False, "message": "Failed to create post"}, status=500)


# ===================== STORIES =====================
# NEW — checklist items 54/55/57/60. Story model added in models.py.
class StoryCreateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Create a Story (24h auto-expiry)", tags=["Stories"])
    @idempotent("story_create")  # H20
    def post(self, request):
        from copyrights.services import can_upload, precheck_upload
        allowed, why = can_upload(request.user)
        if not allowed:
            return Response({"success": False, "code": "copyright_upload_blocked", "message": why},
                            status=status.HTTP_403_FORBIDDEN)
        ok, code, why = precheck_upload(request.user, files=request.FILES.getlist("media"))
        if not ok:
            return Response({"success": False, "code": code, "message": why}, status=status.HTTP_403_FORBIDDEN)
        # F2: shelf size. Checked BEFORE the publish gate so a refused story does not use up the spam-rate allowance.
        from . import story_limits
        over = story_limits.check_active_limit(request.user)
        if over is not None:
            res = Response(
                {"success": False, "code": over["code"], "message": over["message"], "retry_after": over["retry_after"],
                 "limit": over["limit"], "active": over["active"]},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
            res["Retry-After"] = str(over["retry_after"])
            return res
        from . import publish_gate  # H23 publish gate (a story has no hold state: abusive/NSFW text is refused)
        verdict = publish_gate.evaluate(
            request.user, "story",
            texts=publish_gate.texts_from_request("story", request.data),
            files=request.FILES.getlist("media"),
        )
        if not verdict.ok:
            return verdict.response()
        serializer = StoryCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        story = serializer.save()
        publish_gate.after_create(verdict, request.user, "story", story)
        # Re-read with the stickers (and each mentioned user) joined in, so the
        # response has the same shape as the list endpoint without N+1 queries.
        story = (
            Story.objects.select_related("user")
            .prefetch_related(story_sticker_prefetch())
            .get(pk=story.pk)
        )
        return Response(StorySerializer(story, context={"request": request}).data, status=status.HTTP_201_CREATED)


class StoryListAPIView(generics.ListAPIView):
    """Active (non-expired, non-deleted) stories from people the requester
    follows, plus their own. Expiry is enforced here in real time — the
    `expire_old_stories` celery task (tasks.py) only does housekeeping
    (soft-deleting rows so they don't pile up), it isn't what makes an
    expired story stop showing up."""
    serializer_class = StorySerializer
    permission_classes = [IsAuthenticated]

    def get_serializer_context(self):
        # LV1: every story item gets `live` (author live right now, visible to
        # me) — computed once here, not per story.
        from .story_live import live_map_for

        return {"request": self.request, "live_map": live_map_for(self.request.user)}

    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        # F3-AUD: a class story also reaches class members who do not follow the teacher.
        from .audience import classroom_ids_of
        my_classrooms = list(classroom_ids_of(request_user))
        return (
            filter_stories_visible_to(
                Story.objects.select_related("user")
                .filter(is_deleted=False, expires_at__gt=timezone.now())
                .filter(
                    Q(user_id__in=following_ids) | Q(user=request_user)
                    | Q(audience=Story.AUDIENCE_CLASS, audience_classroom_id__in=my_classrooms)
                )
                # Mute covers stories too (P8: unless the mute is posts-only): a muted account's stories stay out of my tray.
                .exclude(user_id__in=MutedAccount.objects.filter(user=request_user, mute_stories=True).values("muted_user_id"))
                .prefetch_related(story_sticker_prefetch()),
                request_user,
            )
            .order_by("user_id", "-created_at")
        )


class StoryLimitsAPIView(APIView):
    """GET /post/stories/limits/ - F2: how many story slots I have left and how long a story lives."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My story limits (active cap, lifetime)", tags=["Stories"])
    def get(self, request):
        from . import story_limits

        return Response(story_limits.limits_for(request.user))


class StoryLiveAPIView(APIView):
    """GET /post/stories/live/  — LV1: who is LIVE right now (for my story tray).

    The story list only returns users who HAVE an active story; a teacher who
    goes live without posting one still needs a ring, so the home screen polls
    this (~every 5s) and merges it into the tray. Each row:

        {"user": {"id", "username", "profile_picture"},
         "live": {"session_id", "classroom_id", "title", "started_at",
                  "viewer_count", "can_join"}}

    Newest first. Visibility = class members + followers of the teacher
    (see post/story_live.py); blocked / muted teachers are left out. When the
    teacher ends the class (or their app stops heartbeating) the row is gone
    on the next call. Has its own throttle scope so the 5s polling does not eat
    into the shared per-user rate budget.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "story_live"

    @extend_schema(summary="Users who are live right now (story tray LIVE rings)", tags=["Stories"])
    def get(self, request):
        from .story_live import live_map_for

        live_map = live_map_for(request.user)
        users = User.objects.in_bulk(list(live_map.keys()))
        items = []
        for tid, live in live_map.items():
            user = users.get(tid)
            if user is None or not user.is_active:
                continue
            items.append({
                "user": {
                    "id": str(user.id),
                    "username": user.username,
                    "profile_picture": get_profile_pic_url(user, request),
                },
                "live": live,
            })
        # F2: same priority as the app's tray - a class I can JOIN (member / staff / teacher) before a class I only
        # follow, then newest start first. (sort is stable: two passes = can_join desc, then started_at desc inside.)
        items.sort(key=lambda i: i["live"].get("started_at") or "", reverse=True)
        items.sort(key=lambda i: 0 if i["live"].get("can_join") else 1)
        return Response({"results": items})


class StoryDetailAPIView(APIView):
    """One active story by id - what a `story_mention` notification opens.

    Same visibility rule as view/react/reply (post/story_visibility.py): a
    Close Friends story the caller may not see is a 404, not a 403. An expired
    or deleted story is a 404 too, and so is one whose author has a block with
    the caller in either direction."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get one active story (with its stickers)", tags=["Stories"])
    def get(self, request, story_id):
        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired or story.user_id in blocked_user_ids(request.user):
            raise Http404("Story not found.")
        story = (
            Story.objects.select_related("user")
            .prefetch_related(story_sticker_prefetch())
            .get(pk=story.pk)
        )
        return Response(StorySerializer(story, context={"request": request}).data)

    @extend_schema(summary="Delete my own story (owner only)", request=None, tags=["Stories"])
    def delete(self, request, story_id):
        """ST-A (2): the owner's \"Delete\" in the story viewer. Soft delete (`Story.soft_delete`), 204. Someone else's story
        (or an unknown / already deleted one) is a 404, never a 403, so existence is not leaked. A story deleted BEFORE it
        expires is also gone from highlights (`highlightable_stories_q`), the same rule the highlight docs state."""
        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.user_id != request.user.id:
            raise Http404("Story not found.")
        story.soft_delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class StoryMentionCandidatesAPIView(APIView):
    """People the composer can @mention.

    GET /post/stories/mention-candidates/?q=<text>&audience=everyone|close_friends

    * no `q`  -> only the caller's own people (accepted followers + people they follow)
    * with `q`-> any active user whose username / name contains it
    * never the caller, never anyone with a block in either direction
    * `audience=close_friends` narrows to the caller's Close Friends list, because
      only those people may be tagged in a Close Friends story
    * `audience=class&classroom_id=<id>` (X5) narrows to the members of that ONE class (its teacher + students with an
      active pass), because only they may be tagged in a Class story; `class` without a usable `classroom_id` -> an
      empty list (never "everybody") so the picker cannot offer someone the server will refuse
    Connected people sort first, then by username. Row: {id, username, name, profile_picture}.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search people to @mention in a story",
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("audience", str, required=False, enum=["everyone", "followers", "close_friends", "class"]),
            OpenApiParameter("classroom_id", int, required=False),
        ],
        tags=["Stories"],
    )
    def get(self, request):
        me = request.user
        q = (request.query_params.get("q") or "").strip()
        audience = (request.query_params.get("audience") or Story.AUDIENCE_EVERYONE).strip()

        qs = (
            User.objects.filter(is_active=True)
            .exclude(id=me.id)
            .exclude(id__in=blocked_user_ids(me))
            .annotate(
                _is_following=Exists(Follow.objects.filter(
                    follower=me, following=OuterRef("pk"), status=Follow.Status.ACCEPTED,
                )),
                _is_follower=Exists(Follow.objects.filter(
                    following=me, follower=OuterRef("pk"), status=Follow.Status.ACCEPTED,
                )),
            )
        )
        if audience == Story.AUDIENCE_CLOSE_FRIENDS:
            qs = qs.filter(id__in=CloseFriend.objects.filter(owner=me).values("friend_id"))
        elif audience == Story.AUDIENCE_FOLLOWERS:  # F3-AUD: only people who could open the story
            qs = qs.filter(_is_follower=True)
        elif audience == Story.AUDIENCE_CLASS:  # X5 (4): only members of the story's class
            from .audience import classroom_member_ids
            qs = qs.filter(id__in=classroom_member_ids(request.query_params.get("classroom_id")))
        if q:
            qs = qs.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
        elif audience != Story.AUDIENCE_CLASS:  # a class story lists the whole (small) class, connected or not
            qs = qs.filter(Q(_is_following=True) | Q(_is_follower=True))
        qs = qs.order_by("-_is_following", "-_is_follower", "username")

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        data = StoryMentionCandidateSerializer(page, many=True, context={"request": request}).data
        return paginator.get_paginated_response(data)


def _get_interactive_sticker(request, story_id, sticker_id):
    """The sticker a viewer wants to respond to. Same gate as reacting: the
    story must be visible to them (404 otherwise, so a Close Friends story is
    not leaked), not expired, and not written by someone with a block against
    the caller. A sticker id that belongs to another story is a 404 too."""
    story = get_visible_story_or_404(request.user, story_id)
    if story.is_expired or story.user_id in blocked_user_ids(request.user):
        raise Http404("Story not found.")
    sticker = get_object_or_404(StorySticker.objects.select_related("story"), id=sticker_id, story=story)
    return story, sticker


def _response_error(exc, request, sticker):
    body = {"detail": exc.message, "code": exc.code}
    if exc.status == status.HTTP_409_CONFLICT:
        # Show the state that already exists so the client can just render it.
        body["sticker"] = serialize_sticker(sticker, request)
    return Response(body, status=exc.status)


class StoryPollVoteAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/vote/   {"option": 0}

    One vote per viewer and it is final (409 with the existing state if they
    try again). The story owner can't vote (400). 201 returns the poll sticker
    with `my_vote` and `results` filled in."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Vote on a story poll sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            cast_vote(sticker, request.user, request.data.get("option"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryQuestionAnswerAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/answer/   {"text": "..."}

    One private answer per viewer (max 300 chars); only the story owner can
    read them (StoryStickerResponsesAPIView). The owner can't answer their own
    question (400); a second answer is a 409."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Answer a story question sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            submit_answer(sticker, request.user, request.data.get("text"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryQuizAnswerAPIView(APIView):
    """POST /post/stories/<story_id>/stickers/<sticker_id>/quiz-answer/   {"option": 1}

    F3. One final answer per viewer (409 with the existing state if they try again). The story owner can't answer
    (400). 201 returns the quiz sticker with `my_answer`, `correct_index`, `explanation` and `results` filled in."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Answer a story quiz sticker", tags=["Stories"])
    def post(self, request, story_id, sticker_id):
        story, sticker = _get_interactive_sticker(request, story_id, sticker_id)
        try:
            cast_quiz_answer(sticker, request.user, request.data.get("option"))
        except ResponseError as exc:
            return _response_error(exc, request, sticker)
        return Response({"sticker": serialize_sticker(sticker, request)}, status=status.HTTP_201_CREATED)


class StoryStickerResponsesAPIView(generics.GenericAPIView):
    """GET /post/stories/<story_id>/stickers/<sticker_id>/responses/  - OWNER ONLY.

    poll     -> rows {id, user, option_index, option, created_at} + summary {counts, total}
    quiz     -> rows {id, user, option_index, option, is_correct, created_at}
                + summary {counts, total, correct, correct_index}                               (F3)
    question -> rows {id, user, text, created_at}               + summary {count}
    Paginated like the other lists (`count/next/previous/results`) with the
    extra `summary` key. Works on an expired story too (as long as it isn't
    deleted) so the owner can still read the answers."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List a poll's votes / a question's answers - owner only", tags=["Stories"])
    def get(self, request, story_id, sticker_id):
        story = get_object_or_404(Story, id=story_id, is_deleted=False)
        if story.user_id != request.user.id:
            raise PermissionDenied("Sirf apni story ke responses dekh sakte hain.")
        sticker = get_object_or_404(
            StorySticker, id=sticker_id, story=story,
            kind__in=[StorySticker.KIND_POLL, StorySticker.KIND_QUESTION, StorySticker.KIND_QUIZ],
        )

        def brief(user):
            return {
                "id": str(user.id),
                "username": user.username,
                "profile_picture": get_profile_pic_url(user, request),
            }

        paginator = StandardPagination()
        if sticker.kind == StorySticker.KIND_POLL:
            options = list((sticker.data or {}).get("options", []))
            qs = sticker.poll_votes.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {
                    "id": str(v.id), "user": brief(v.user), "option_index": v.option_index,
                    "option": options[v.option_index] if v.option_index < len(options) else "",
                    "created_at": v.created_at.isoformat(),
                }
                for v in page
            ]
            stats = build_interaction_stats([sticker], request.user)[sticker.id]
            summary = {"counts": stats["counts"], "total": stats["total"]}
        elif sticker.kind == StorySticker.KIND_QUIZ:
            options = list((sticker.data or {}).get("options", []))
            qs = sticker.quiz_answers.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {
                    "id": str(a.id), "user": brief(a.user), "option_index": a.option_index,
                    "option": options[a.option_index] if a.option_index < len(options) else "",
                    "is_correct": a.is_correct, "created_at": a.created_at.isoformat(),
                }
                for a in page
            ]
            stats = build_interaction_stats([sticker], request.user)[sticker.id]
            summary = {
                "counts": stats["counts"], "total": stats["total"], "correct": stats["correct"],
                "correct_index": (sticker.data or {}).get("correct_index"),
            }
        else:
            qs = sticker.answers.select_related("user").order_by("-created_at")
            page = paginator.paginate_queryset(qs, request, view=self)
            rows = [
                {"id": str(a.id), "user": brief(a.user), "text": a.text, "created_at": a.created_at.isoformat()}
                for a in page
            ]
            summary = {"count": qs.count()}

        response = paginator.get_paginated_response(rows)
        response.data["summary"] = summary
        return response


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


class StoryViewAPIView(APIView):
    """Records a view (deduped per user via unique_together) and returns
    the current view count — mirrors PostDetailAPIView's PostView tracking."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark a story as viewed", tags=["Stories"])
    def post(self, request, story_id):
        from django.shortcuts import get_object_or_404

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"success": False, "message": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        StoryView.objects.get_or_create(story=story, user=request.user)
        story.refresh_from_db(fields=["views_count"])
        return Response({"success": True, "views_count": story.views_count})


class StoryReactAPIView(APIView):
    """Instagram-style quick reaction — toggle semantics: tapping the same
    emoji again removes it, tapping a different one replaces it. One
    reaction per (story, user), enforced by StoryReaction's
    unique_together (models.py)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="React to / un-react from a story", tags=["Stories"])
    def post(self, request, story_id):
        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)

        if not can_reply_to_story(story, request.user):
            return Response({"detail": "Replies are turned off for this story.", "code": "story_replies_off"},
                            status=status.HTTP_403_FORBIDDEN)

        emoji = (request.data.get("emoji") or "").strip()
        if not emoji:
            return Response({"detail": "'emoji' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        existing = StoryReaction.objects.filter(story=story, user=request.user).first()
        if existing and existing.emoji == emoji:
            existing.delete()
            reacted = False
            unnotify_story_reacted(story, request.user)  # Task 3.4
        else:
            StoryReaction.objects.update_or_create(
                story=story, user=request.user, defaults={"emoji": emoji},
            )
            reacted = True
            if existing is None:  # a changed emoji is not a new reaction
                notify_story_reacted(story, request.user, emoji)  # Task 3.4

        # Real-time — story owner's "who reacted" list/analytics updates
        # immediately, no polling. Same `user_<id>` inbox group
        # message/services.py's broadcasts already use; InboxConsumer's
        # `story_reaction` handler (consumers.py) is the only new bit.
        if str(story.user_id) != str(request.user.id):
            channel_layer = get_channel_layer()
            if channel_layer is not None:
                async_to_sync(channel_layer.group_send)(
                    f"user_{story.user_id}",
                    {
                        "type": "story_reaction",
                        "story_id": str(story.id),
                        "user_id": str(request.user.id),
                        "username": request.user.username,
                        "emoji": emoji,
                        "reacted": reacted,
                    },
                )

        return Response({"success": True, "reacted": reacted, "emoji": emoji if reacted else None})


class StoryReplyAPIView(APIView):
    """Reply to a story. This does NOT invent a new reply model/thread —
    it reuses message app's Conversation/Message pipeline directly
    (`message.services.get_or_create_conversation` +
    `create_message_and_broadcast`), so the reply lands in the recipient's
    normal DM inbox exactly like any other message, with the story's
    thumbnail attached via `Message.story`/`story_reply_snapshot`."""
    permission_classes = [IsAuthenticated]
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]

    @extend_schema(summary="Reply to a story (delivered as a DM)", tags=["Stories"])
    def post(self, request, story_id):
        # Local imports — post app must not need message app importable at
        # module-load time (avoids a hard circular-import dependency
        # between the two apps; message app already reaches back into
        # post.Story via a string FK for the same reason).
        from message.services import get_or_create_conversation, create_message_and_broadcast
        from message.models import MessageType
        from message.serializers import MessageSerializer

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if story.user_id == request.user.id:
            return Response(
                {"detail": "Apni khud ki story ko reply nahi kar sakte."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not can_reply_to_story(story, request.user):  # F3-AUD: reply_setting = followers / off
            return Response({"detail": "Replies are turned off for this story.", "code": "story_replies_off"},
                            status=status.HTTP_403_FORBIDDEN)

        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "'text' required hai."}, status=status.HTTP_400_BAD_REQUEST)

        # S4: the story owner limited who may start a chat with them (an existing chat is never cut off).
        from user_profile import privacy as privacy_rules

        if privacy_rules.new_chat_blocked(request.user, story.user):
            return Response(
                {"detail": privacy_rules.MESSAGE_BLOCKED_TEXT, "code": privacy_rules.MESSAGE_BLOCKED_CODE},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Minor-safety: no new chat from an adult to a minor who doesn't follow them.
        from message.minor_safety import MINOR_DM_BLOCKED_MESSAGE, dm_blocked_for_minor
        from message.models import Conversation as _Conv

        if dm_blocked_for_minor(request.user, story.user) and not _Conv.objects.filter(
            private_key=_Conv.make_private_key(request.user.id, story.user_id)
        ).exists():
            return Response({"detail": MINOR_DM_BLOCKED_MESSAGE}, status=status.HTTP_403_FORBIDDEN)

        conversation, _ = get_or_create_conversation(request.user, story.user)

        try:
            snapshot_url = request.build_absolute_uri(story.media.url) if story.media else None
        except ValueError:
            snapshot_url = None

        message = create_message_and_broadcast(
            conversation=conversation,
            sender=request.user,
            message_type=MessageType.STORY_REPLY,
            text=text,
            extra_fields={"story": story, "story_reply_snapshot": snapshot_url},
        )

        return Response(
            MessageSerializer(message, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class StoryShareAPIView(APIView):
    """X1: POST /post/stories/<id>/share/ {"conversation_id": "..."} - send a story into a chat.

    Order of checks: visible (else 404, never leaks) -> not expired (404) -> `Story.allow_reshare` (403
    `story_reshare_off`; the owner is always allowed) -> conversation (404 / 403 participant)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Send a story into a chat conversation", request=PostShareRequestSerializer, tags=["Stories"])
    def post(self, request, story_id):
        from django.core.exceptions import PermissionDenied as DjangoPermissionDenied

        from message.models import Conversation

        from .story_share import share_story_to_conversation

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if not can_reshare_story(story, request.user):
            return Response({"detail": STORY_RESHARE_OFF_MESSAGE, "code": STORY_RESHARE_OFF},
                            status=status.HTTP_403_FORBIDDEN)

        body = PostShareRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            message = share_story_to_conversation(
                story, request.user, body.validated_data["conversation_id"], request=request,
            )
        except Conversation.DoesNotExist:
            return Response({"detail": "Conversation not found"}, status=status.HTTP_404_NOT_FOUND)
        except DjangoPermissionDenied as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(
            {"success": True, "data": {"message_id": str(message.id), "conversation_id": str(message.conversation_id)}},
            status=status.HTTP_201_CREATED,
        )


class StorySaveAPIView(APIView):
    """X1: POST /post/stories/<id>/save/ - the app asks "may I download this story?" right before it writes the file
    to the gallery. Same checks as share but with `Story.allow_save` (403 `story_save_off`). Answers
    {"success": true, "data": {"url", "media_type", "filename"}}. (A client that skips this call can still fetch the
    public media URL by hand - this gate keeps the app and honest clients in line, it is not DRM.)"""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get the downloadable media of a story (checks allow_save)", request=None, tags=["Stories"])
    def post(self, request, story_id):
        from .story_share import saveable_media

        story = get_visible_story_or_404(request.user, story_id)
        if story.is_expired:
            return Response({"detail": "Story expired"}, status=status.HTTP_404_NOT_FOUND)
        if not can_save_story(story, request.user):
            return Response({"detail": STORY_SAVE_OFF_MESSAGE, "code": STORY_SAVE_OFF},
                            status=status.HTTP_403_FORBIDDEN)
        data = saveable_media(story, request)
        if data is None:
            return Response({"detail": "Story has no media"}, status=status.HTTP_404_NOT_FOUND)
        return Response({"success": True, "data": data})


class StoryViewersAPIView(generics.ListAPIView):
    """Owner-only — Instagram's "Activity" list under your own story: who
    viewed it, with each viewer's reaction (if any) folded in."""
    serializer_class = StoryViewerEntrySerializer
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List a story's viewers (+ their reactions) — owner only", tags=["Stories"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        story = get_object_or_404(Story, id=self.kwargs["story_id"], is_deleted=False)
        if story.user_id != self.request.user.id:
            raise PermissionDenied("Sirf apni story ke viewers dekh sakte hain.")
        self._story = story
        from user_profile.services import blocked_user_ids
        return (
            story.views.select_related("user")
            .exclude(user_id__in=blocked_user_ids(self.request.user))
            .order_by("-viewed_at")
        )

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        reactions = StoryReaction.objects.filter(story=self._story).select_related("user")
        ctx["reactions_by_user_id"] = {r.user_id: r for r in reactions}
        return ctx


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

# ===================== REACTION API - NEW FUNCTION ADDED =====================
# 🔥 TASK 22 — consolidated. This file used to define its own local
# `ReactionRequestSerializer` (identical `choices` list to the one in
# `serializers.py`, just single- vs double-quoted — no actual drift, so
# safe to collapse) instead of importing the canonical one. Now imported
# from `.serializers` above, like every other serializer this view uses.
class PostReactionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    def get_permissions(self):
        if self.request.method == 'GET':
            return [AllowAny()]
        return [IsAuthenticated()]

    @extend_schema(
        summary="Toggle Reaction - Like / Unlike",
        description="Like: count +1, Same reaction dubara -> Unlike count -1, Alag reaction -> change",
        request=ReactionRequestSerializer,
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Reactions"]
    )
    def post(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        from .moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, post.user):
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        serializer = ReactionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reaction_type = serializer.validated_data['reaction']
        user = request.user
        # H5 - optional explicit intent so a client can REPLAY a queued offline tap safely:
        #   desired="set"   -> make sure I have THIS reaction (never toggles it off)
        #   desired="clear" -> make sure I do NOT have THIS reaction (never adds one)
        # Absent / anything else = the old toggle behaviour (older app builds keep working).
        desired = str(request.data.get('desired', '')).lower()
        existing = PostLike.objects.filter(post=post, user=user).first()
        if desired == 'clear' and (existing is None or existing.reaction_type != reaction_type):
            status_msg = "unchanged"
            my_reaction = existing.reaction_type if existing else None
        elif desired == 'set' and existing is not None and existing.reaction_type == reaction_type:
            status_msg = "unchanged"
            my_reaction = reaction_type
        elif existing:
            if existing.reaction_type == reaction_type:
                existing.delete() # UNLIKE -> count auto -1 by signal
                status_msg = "unliked"
                my_reaction = None
            else:
                existing.reaction_type = reaction_type
                existing.save() # CHANGE -> signal handle
                status_msg = "changed"
                my_reaction = reaction_type
        else:
            PostLike.objects.create(post=post, user=user, reaction_type=reaction_type) # LIKE -> +1
            status_msg = "liked"
            my_reaction = reaction_type
            # Task 11 fix — only a genuinely new like notifies; a
            # reaction *change* (the `existing.reaction_type != reaction_type`
            # branch above) intentionally does not, same as unlike doesn't.
            notify_post_liked(post, user)

        post.refresh_from_db()
        return Response({
            "status": status_msg,
            "my_reaction": my_reaction,
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            }
        })

    @extend_schema(summary="Get Reaction Counts", tags=["Post Reactions"])
    def get(self, request, post_id):
        from django.shortcuts import get_object_or_404
        post = get_object_or_404(Post, id=post_id)
        from .moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
        my_reaction = None
        if request.user.is_authenticated:
            obj = PostLike.objects.filter(post=post, user=request.user).first()
            if obj:
                my_reaction = obj.reaction_type
        return Response({
            "post_id": str(post.id),
            "counts": {
                "like": post.like_count,
                "confuse": post.confuse_count,
                "wrong": post.wrong_count,
                "imp": post.imp_count,
                "explain": post.explain_count,
                "total": post.likes_count,
            },
            "my_reaction": my_reaction
        })


# ---------------------------------------------------------------------------
# TASK G6 (growth_and_feature_tasks.md) — Polls & Q&A post types.
#
# Poll CREATION already existed (PostCreateSerializer's `poll_options` +
# PostPoll/PostPollOption in models.py) but nothing let a viewer actually
# cast a vote — PostPollSerializer's own docstring flagged this as "a
# separate endpoint (not in scope of this task's files)". This is that
# endpoint. "Ask a doubt" (question + answers + best-answer pin) is
# entirely new: PostAnswer (models.py) + this view for listing/creating
# answers + PostAnswerMarkBestAPIView for pinning one.
# ---------------------------------------------------------------------------
# The poll / quiz endpoints (vote, results, CSV export, leaderboard, convert-to-test-series) now live in
# post/poll_views.py; this name stays importable from here for older imports.
from .poll_views import PostPollVoteAPIView  # noqa: E402,F401


class PostAnswerListCreateAPIView(generics.ListCreateAPIView):
    """Answers on a 'doubt' (Ask-a-doubt) post. Listing is public (matches
    PostReactionAPIView's GET-is-AllowAny pattern); posting an answer
    needs auth. Best answer first, then most-liked, then oldest-first
    (PostAnswer.Meta.ordering)."""
    serializer_class = PostAnswerSerializer
    pagination_class = PageNumberPagination
    # Doubt post upgrade: an answer may carry a step-wise solution, pictures and a video (multipart); JSON still works.
    parser_classes = [parsers.JSONParser, parsers.MultiPartParser, parsers.FormParser]
    throttle_scope = "post_doubt_answer"

    def get_permissions(self):
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get_throttles(self):
        if self.request.method == "POST":
            return [ScopedRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        from .moderation_guard import post_hidden_for
        from user_profile.services import blocked_user_ids as _blocked_ids

        post = Post.objects.filter(id=self.kwargs["post_id"], is_deleted=False).select_related("user").first()
        if post is None or post_hidden_for(self.request.user, post):
            return PostAnswer.objects.none()
        qs = PostAnswer.objects.filter(post_id=post.id).select_related("user").prefetch_related("steps", "attachments")
        user = self.request.user
        if user.is_authenticated:
            blocked = _blocked_ids(user)
            if post.user_id in blocked:  # blocked with the post's author: same as "no such post"
                return PostAnswer.objects.none()
            qs = qs.exclude(user_id__in=blocked)
        return qs

    @extend_schema(summary="List answers on a doubt post", tags=["Post Doubts"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        summary="Answer a doubt post",
        request=PostAnswerCreateSerializer,
        responses={201: PostAnswerSerializer},
        tags=["Post Doubts"],
    )
    def create(self, request, *args, **kwargs):
        post = get_object_or_404(Post, id=self.kwargs["post_id"], is_deleted=False)
        from .moderation_guard import post_hidden_for
        if post_hidden_for(request.user, post):
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            from user_profile.views import is_blocked_between
            if is_blocked_between(request.user, post.user):
                return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.post_type != "doubt":
            return Response(
                {"detail": "Answers are only for 'Ask a doubt' posts."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        from . import doubts

        doubts.check_answer_quota(post, request.user)
        input_serializer = PostAnswerCreateSerializer(data=request.data, context={"request": request})
        input_serializer.is_valid(raise_exception=True)
        cleaned = input_serializer.validated_data

        # H23 publish gate on the answer (already sanitised by doubts.clean_answer_input): spam rate limit, abusive /
        # NSFW text (an answer has no hold state, so it is refused 422), duplicate text / file, audio-music flag.
        from . import publish_gate

        gate_files = [s["image"] for s in cleaned["steps"] if s.get("image") is not None]
        gate_files += list(cleaned["images"])
        gate_types = ["image"] * len(gate_files)
        if cleaned["video"] is not None:
            gate_files.append(cleaned["video"])
            gate_types.append("video")
        gate_texts = [t for t in [cleaned["content"]] + [s["text"] for s in cleaned["steps"]] if t]
        verdict = publish_gate.evaluate(
            request.user, "answer", texts=gate_texts, files=gate_files, media_types=gate_types,
        )
        if not verdict.ok:
            return verdict.response()

        answer = doubts.create_answer(post, request.user, cleaned)
        publish_gate.after_create(verdict, request.user, "answer", answer)
        notify_post_answered(post, answer)
        out = PostAnswerSerializer(answer, context={"request": request})
        return Response(out.data, status=status.HTTP_201_CREATED)


class PostAnswerMarkBestAPIView(APIView):
    """Pin/unpin the best answer on a doubt post. Only the post's own
    author can pin (they're the one who asked); pinning an already-pinned
    answer again unpins it. Exactly one best answer per post — pinning a
    new one clears any previous pin first.

    Doubt post upgrade: pinning also marks the doubt SOLVED (`doubt.is_solved`, the "Solved" badge) and pays a held
    coin bounty to the answerer (post/doubts.toggle_best_answer; the coins were taken from the asker when the doubt
    was posted). Once a bounty is paid the best answer is locked: 409 `bounty_paid`. Wallet busy: 503 + Retry-After."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark/unmark an answer as the best answer", tags=["Post Doubts"])
    def post(self, request, answer_id):
        from user_profile.models import CoinLedgerBusy

        from . import doubts

        answer = get_object_or_404(PostAnswer.objects.select_related("post", "user"), id=answer_id)
        post = answer.post
        if post.is_deleted:
            return Response({"detail": "Post not found."}, status=status.HTTP_404_NOT_FOUND)
        if post.user_id != request.user.id:
            raise PermissionDenied("Only the person who asked can mark the best answer.")

        try:
            result = doubts.toggle_best_answer(answer)
        except doubts.BestAnswerLocked as exc:
            return Response({"detail": str(exc), "code": "bounty_paid"}, status=status.HTTP_409_CONFLICT)
        except CoinLedgerBusy:
            resp = Response({"detail": "Wallet busy hai, thodi der baad dobara try karo.", "code": "wallet_busy"},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
            resp["Retry-After"] = "2"
            return resp

        if result["state"] == "pinned":
            doubts.notify_best_answer(post, result["answer"], result)

        return Response({
            "status": result["state"],
            "answer": PostAnswerSerializer(result["answer"], context={"request": request}).data,
            "is_solved": result["is_solved"],
            "bounty_coins": result["bounty_coins"],
            "bounty_paid": result["bounty_paid"],
            "bounty_refunded": result["bounty_refunded"],
            "wallet_balance": doubts._wallet_balance(request.user),
        })


class DoubtConfigAPIView(APIView):
    """GET /post/doubts/config/ - limits for the doubt composer / answer sheet (bounty range, steps, files) and the
    caller's wallet balance, so the app never hard-codes them."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Doubt composer limits + wallet balance", tags=["Post Doubts"])
    def get(self, request):
        from . import doubts

        return Response(doubts.doubt_config(request.user))


class DoubtSimilarAPIView(APIView):
    """GET /post/doubts/similar/?q=<question or OCR text>&subject=<optional>&limit=5

    "Is this doubt already asked?" - the composer calls it (debounced) while the student types / after the photo
    is read, and shows the matches (solved ones first, with their best answer) before posting, so duplicates are
    not created. Returns {"results": [...], "min_chars": n}; a query shorter than `min_chars` gives no results."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "post_doubt_similar"

    @extend_schema(summary="Similar existing doubts (duplicate check)", tags=["Post Doubts"])
    def get(self, request):
        from . import doubts

        query = doubts.normalize_query(request.query_params.get("q"))
        results = []
        if len(query) >= doubts.SIMILAR_MIN_CHARS:
            results = doubts.find_similar(
                request.user, query, subject=request.query_params.get("subject", ""),
                limit=doubts.clamp_limit(request.query_params.get("limit")), request=request)
        return Response({"results": results, "min_chars": doubts.SIMILAR_MIN_CHARS})


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
        from .audience import filter_posts_visible_to

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


# ===================== FEED AD/INTERSTITIAL CONFIG =====================
# NEW — SujhaavFayda1 item 3. kAdEveryPosts / kInterstitialEveryPosts used
# to be hardcoded Dart consts — changing the cadence meant an app release.
# The client now fetches this once per session and falls back to its own
# hardcoded defaults if the call fails.
#
# Reads from settings for now (override per-environment via env vars,
# still needs a process restart to change — NOT true hot A/B testing). For
# real without-a-release A/B testing, swap the body of get() for a lookup
# against an admin-editable model, django-waffle, or whatever feature-flag
# service this project standardizes on — no such model was in the files
# reviewed for this pass, so it wasn't guessed at here.
def build_feed_ad_config(user=None) -> dict:
    """Payload of `GET /post/feed/ad-config/` (also embedded in `GET /core/home-bootstrap/`).

    Minors see no ads. The public endpoint can only know the viewer when the app sends the token; the app ALSO
    hides ads from /core/me/context/ (`ads_enabled`), so either is enough. 0 = "no ad slots" (the app treats
    <= 0 as off)."""
    ad_every = getattr(settings, "FEED_AD_EVERY_POSTS", 5)
    if user is not None and user.is_authenticated and user.is_minor and getattr(settings, "ADS_OFF_FOR_MINORS", True):
        ad_every = 0
    return {
        "ad_every_posts": ad_every,
        "interstitial_every_posts": getattr(settings, "FEED_INTERSTITIAL_EVERY_POSTS", 18),
    }


class FeedAdConfigAPIView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        summary="Feed ad/interstitial cadence config",
        responses={200: OpenApiTypes.OBJECT},
        tags=["Post Feed"],
    )
    def get(self, request):
        return Response(build_feed_ad_config(getattr(request, "user", None)))


class PostSaveToggleAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Save / Unsave Post - Toggle",
        description="Pehli baar call -> Saved, Dubara call -> Unsaved",
        request=OpenApiTypes.OBJECT,
        tags=["Post Save"]
    )
    def post(self, request, post_id):
        post = get_object_or_404(Post, id=post_id, is_deleted=False)
        collection_name = request.data.get('collection_name', 'default')
        from .profile_tabs import collection_exists, normalize_collection_name  # P2
        collection_name = normalize_collection_name(collection_name) or 'default'
        if not collection_exists(request.user, collection_name):
            return Response({"success": False, "code": "collection_not_found", "message": "That folder does not exist."},
                            status=status.HTTP_404_NOT_FOUND)

        # Check pehle se saved hai kya?
        saved_obj = PostSave.objects.filter(post=post, user=request.user).first()

        # H5 - optional explicit intent (safe replay of a queued offline tap): desired="save" / "unsave" never
        # toggles; it only reports the state when the post is already in that state. Absent = old toggle.
        desired = str(request.data.get('desired', '')).lower()
        if desired == 'save' and saved_obj is not None:
            return Response({"status": "saved", "is_saved": True, "saves_count": post.saves_count},
                            status=status.HTTP_200_OK)
        if desired == 'unsave' and saved_obj is None:
            return Response({"status": "unsaved", "is_saved": False, "saves_count": post.saves_count},
                            status=status.HTTP_200_OK)

        # Saving a hidden post / a blocked person's post is refused; UN-saving is always allowed.
        if saved_obj is None:
            from .moderation_guard import post_hidden_for
            hidden = post_hidden_for(request.user, post)
            if not hidden and post.user_id != request.user.id:
                from user_profile.views import is_blocked_between
                hidden = is_blocked_between(request.user, post.user)
            if hidden:
                return Response({"success": False, "message": "Post not found"}, status=status.HTTP_404_NOT_FOUND)
            # S4b: the author switched saving off (only ADDING a save is refused; removing one never is).
            from .post_options import SAVE_OFF_CODE, SAVE_OFF_TEXT, can_save_post
            if not can_save_post(post, request.user):
                return Response({"success": False, "code": SAVE_OFF_CODE, "message": SAVE_OFF_TEXT},
                                status=status.HTTP_403_FORBIDDEN)

        if saved_obj:
            # Already saved hai -> ab unsave karo
            saved_obj.delete()
            post.refresh_from_db()
            return Response({
                "status": "unsaved",
                "is_saved": False,
                "saves_count": post.saves_count
            }, status=status.HTTP_200_OK)
        else:
            # Save karo
            PostSave.objects.create(
                post=post,
                user=request.user,
                collection_name=collection_name
            )
            post.refresh_from_db()
            return Response({
                "status": "saved",
                "is_saved": True,
                "saves_count": post.saves_count
            }, status=status.HTTP_201_CREATED)

# ===================== SAVED POSTS LIST =====================
class SavedPostsListAPIView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PostListSerializer
    pagination_class = StandardResultsSetPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Mere Saved Posts ki List",
        parameters=[
            OpenApiParameter(name='collection_name', type=str, required=False, description='Filter by collection'),
        ],
        tags=["Post Save"]
    )
    def get_queryset(self):
        # P2: built from PostSave (profile_tabs.saved_posts_queryset) - the old `saved_by__...` joins could match
        # OTHER users' saves of the same post once a collection filter was added. Same visibility rules as before.
        from .profile_tabs import saved_posts_queryset
        collection = self.request.query_params.get('collection') or self.request.query_params.get('collection_name')
        return saved_posts_queryset(self.request.user, collection or None)

# ===================== HASHTAG DISCOVERY (checklist: "Hashtag") =====================
# post_app.md's overview table lists Hashtag as one of this app's core
# responsibilities, but until now `Post.hashtags` (models.py) was
# write-only — populated on create, never queried back out. These two
# views are what actually make it a discovery feature instead of just
# metadata sitting on the row.
class HashtagPostsAPIView(generics.ListAPIView):
    """GET /post/hashtag/<tag>/ — public posts carrying that hashtag.

    Relies on PostCreateSerializer normalizing hashtags to lowercase at
    write time (serializers.py fix) — without that, `hashtags__contains`
    below would miss posts whose tag casing didn't happen to match.

    `hashtags__contains=[tag]` is a JSONField containment lookup:
    native on Postgres (jsonb `@>`), and supported on SQLite via the
    JSON1 extension on Django ≥3.1 — if this app ends up on an older
    SQLite without JSON1, swap this for a `Q(hashtags__icontains=tag)`
    fallback (less precise — can substring-match inside a longer tag).
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    # Cursor (keyset) pagination: ranking is by the annotated, ever-changing
    # engagement_score, so OFFSET pages used to shift while scrolling.
    pagination_class = EngagementCursorPagination

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        summary="Posts by hashtag",
        parameters=[OpenApiParameter(name='tag', type=str, location=OpenApiParameter.PATH)],
        tags=["Hashtag"],
    )
    def get_queryset(self):
        tag = self.kwargs['tag'].strip().lstrip('#').lower()
        return exclude_hidden_and_muted(Post.objects.select_related('user', 'original_post__user').prefetch_related('media', 'original_post__media').filter(
            is_deleted=False, is_archived=False, moderation_status='approved', visibility='public', is_scheduled=False,
            hashtags__contains=[tag],
        ), self.request.user).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')


class TrendingHashtagsAPIView(APIView):
    """GET /post/hashtags/trending/?days=7&limit=20

    ⚠️ Application-level counting over a bounded recent sample, not a
    real SQL aggregation — JSONField list elements aren't natively
    GROUP-BY-able portably across Postgres/SQLite. Same "note it, don't
    block on it" call as checklist item 60 made for feed fan-out: fine
    at this app's current scale (bounded to the most recent 2000 public
    posts in the window), replace with a Redis sorted set incremented
    in PostCreateSerializer.create() once volume actually demands it.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Trending hashtags",
        parameters=[
            OpenApiParameter(name='days', type=int, required=False, description='Lookback window, default 7'),
            OpenApiParameter(name='limit', type=int, required=False, description='Max tags returned, default 20'),
        ],
        tags=["Hashtag"],
    )
    def get(self, request):
        days = int(request.query_params.get('days', 7))
        limit = min(int(request.query_params.get('limit', 20)), 50)
        since = timezone.now() - timedelta(days=days)

        recent_hashtag_lists = Post.objects.filter(
            is_deleted=False, moderation_status='approved', visibility='public',
            created_at__gte=since,
        ).exclude(hashtags=[]).order_by('-created_at').values_list('hashtags', flat=True)[:2000]

        counts = Counter()
        for tags in recent_hashtag_lists:
            counts.update(tags)

        results = [{"hashtag": tag, "count": count} for tag, count in counts.most_common(limit)]
        return Response({"success": True, "days": days, "results": results})


# ===================== EXPLORE / DISCOVER (checklist: "Explore-content") =====================
class ExploreFeedAPIView(generics.ListAPIView):
    """GET /post/explore/?category=...

    HomeFeedView (above) already has a public-posts fallback branch for
    when a user follows nobody / their follows haven't posted recently
    — but that only ever surfaces as a fallback *inside* the following
    feed. There was no standalone discovery surface a user could open
    any time regardless of who they follow (the "Explore" grid pattern)
    — this is that endpoint.

    Deliberately excludes the requester's own posts AND posts from
    accounts they already follow, so Explore stays genuinely about
    finding new accounts rather than duplicating the home feed.
    """
    serializer_class = PostListSerializer
    permission_classes = [IsAuthenticated]
    # Cursor (keyset) pagination - see common/pagination.py.
    pagination_class = EngagementCursorPagination

    def get_serializer_context(self):
        # TASK 2: every post here is already guaranteed to be from an
        # account the viewer doesn't follow (see get_queryset's
        # `.exclude(user_id__in=following_ids)` below), so pass an empty
        # set rather than re-querying Follow per post — `user.is_following`
        # will correctly come back False for every card.
        return {'request': self.request, 'following_ids': set()}

    @extend_schema(
        summary="Explore / discover public posts",
        parameters=[OpenApiParameter(name='category', type=str, required=False)],
        tags=["Explore"],
    )
    def get_queryset(self):
        request_user = self.request.user
        following_ids = Follow.objects.filter(
            follower=request_user, status=Follow.Status.ACCEPTED
        ).values_list('following_id', flat=True)

        thirty_days_ago = timezone.now() - timedelta(days=30)
        qs = _without_superseded_reposts(Post.objects.select_related('user', 'original_post__user').prefetch_related('media', 'original_post__media').filter(
            is_deleted=False, is_archived=False, moderation_status='approved', is_sensitive=False, visibility='public', is_scheduled=False,
        ).exclude(user=request_user).exclude(user_id__in=following_ids))
        qs = exclude_hidden_and_muted(qs, request_user)

        category = self.request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)

        # TASK 3 — same interest bonus as HomeFeedView, applied only when
        # the caller hasn't already narrowed to one explicit category
        # (an explicit `?category=` request is a stronger, direct signal
        # than the standing interest set and shouldn't be re-weighted).
        interest_categories = [] if category else list(
            UserInterest.objects.filter(user=request_user).values_list('category', flat=True)
        )
        interest_boost = Case(
            When(category__in=interest_categories, then=Value(15.0)),
            default=Value(0.0), output_field=FloatField(),
        ) if interest_categories else Value(0.0, output_field=FloatField())

        # TASK G4 — same video-completion + recent-velocity boosts as
        # HomeFeedView, on top of the existing engagement/interest score.
        video_boost, recent_velocity_boost = _video_and_velocity_boost()

        recent_qs = qs.filter(created_at__gte=thirty_days_ago).annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1 + interest_boost + video_boost + recent_velocity_boost,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')

        # Same defensive fallback pattern as HomeFeedView: a brand-new
        # platform / a narrow category filter can easily have zero posts
        # in the last 30 days — fall back to all-time top public posts
        # rather than showing an empty grid.
        if recent_qs.exists():
            return recent_qs
        return qs.annotate(
            engagement_score=ExpressionWrapper(
                F('likes_count') * 3.0 + F('comments_count') * 5.0 + F('shares_count') * 10.0 + F('views_count') * 0.1 + interest_boost + video_boost + recent_velocity_boost,
                output_field=FloatField(),
            )
        ).order_by('-engagement_score', '-created_at')


# ===================== FEED FEEDBACK CONTROLS - PART 1 =====================
# "Not interested" (PostHide) and "Mute this account" (MutedAccount).
# Private, silent, per-user; they only change what the CALLER's feeds show
# (Home / Explore / Hashtag - see exclude_hidden_and_muted in services.py).
# Part 2 (below): "Show fewer like this" (FeedFeedback ranking signal) + "Why am I seeing this".
def _visible_post_or_404(user, post_id):
    """A post the caller could legitimately have in a feed (so we don't leak
    the existence of private / connections-only posts they can't see)."""
    post = Post.objects.filter(id=post_id, is_deleted=False, is_archived=False, moderation_status='approved', is_scheduled=False).select_related('user').first()
    if post is None:
        raise Http404
    if post.user_id != user.id:
        if post.visibility == 'private':
            raise Http404
        if post.visibility == 'connections' and not Follow.objects.filter(
            follower=user, following_id=post.user_id, status=Follow.Status.ACCEPTED,
        ).exists():
            raise Http404
        if post.visibility in ('close_friends', 'class_campus'):
            from .audience import can_view_post
            if not can_view_post(post, user):
                raise Http404
    return post


class NotInterestedAPIView(APIView):
    """POST   /post/<post_id>/not-interested/  {"reason": "not_interested"|"not_relevant"|"seen_too_often"|"other"}
    DELETE /post/<post_id>/not-interested/  -> undo (snackbar "Undo")

    Idempotent: first call 201, repeats 200 (a new `reason` overwrites the old one).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Not interested (hide this post from my feeds)",
        request=NotInterestedRequestSerializer,
        responses={201: PostHideSerializer, 200: PostHideSerializer},
        tags=["Feed Feedback"],
    )
    def post(self, request, post_id):
        post = _visible_post_or_404(request.user, post_id)
        if post.user_id == request.user.id:
            return Response({"success": False, "message": "You can't hide your own post."},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = NotInterestedRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        reason = serializer.validated_data['reason']
        hide, created = PostHide.objects.get_or_create(user=request.user, post=post, defaults={'reason': reason})
        if not created and hide.reason != reason:
            hide.reason = reason
            hide.save(update_fields=['reason'])
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({
            "success": True,
            "message": "We'll show you less like this." if created else "Already hidden.",
            "data": PostHideSerializer(hide).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @extend_schema(summary="Undo 'Not interested'", tags=["Feed Feedback"])
    def delete(self, request, post_id):
        removed, _ = PostHide.objects.filter(user=request.user, post_id=post_id).delete()
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "Post restored." if removed else "Post was not hidden."},
                        status=status.HTTP_200_OK)


class NotInterestedListAPIView(generics.ListAPIView):
    """GET /post/not-interested/ - posts I hid (for a 'manage' screen). Newest first."""
    permission_classes = [IsAuthenticated]
    serializer_class = PostHideSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return PostHide.objects.filter(user=self.request.user)

    @extend_schema(summary="List posts I marked 'Not interested'", tags=["Feed Feedback"])
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class MutedAccountsAPIView(APIView):
    """GET  /post/muted-accounts/            -> accounts I muted
    POST /post/muted-accounts/ {"user_id"} -> mute (idempotent: 201 first time, 200 after)

    Mute != block: the follow relationship, profile access, DMs and search are
    untouched; the account's posts (and reposts of them) just stop appearing
    in MY Home / Explore / Hashtag feeds, and its stories drop out of MY story tray.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Muted accounts", responses={200: MutedAccountSerializer(many=True)}, tags=["Feed Feedback"])
    def get(self, request):
        qs = MutedAccount.objects.filter(user=request.user).select_related('muted_user')
        # P8: ?q= searches my muted list by username / name.
        q = (request.query_params.get('q') or '').strip()
        if q:
            qs = qs.filter(
                Q(muted_user__username__icontains=q)
                | Q(muted_user__first_name__icontains=q)
                | Q(muted_user__last_name__icontains=q)
            )
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MutedAccountSerializer(page, many=True, context={'request': request}).data)

    @extend_schema(summary="Mute an account", request=MuteAccountRequestSerializer,
                   responses={201: MutedAccountSerializer, 200: MutedAccountSerializer}, tags=["Feed Feedback"])
    def post(self, request):
        serializer = MuteAccountRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        target_id = serializer.validated_data['user_id']
        if target_id == request.user.id:
            return Response({"success": False, "message": "You can't mute yourself."},
                            status=status.HTTP_400_BAD_REQUEST)
        target = User.objects.filter(id=target_id).first()
        if target is None:
            return Response({"success": False, "message": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        # P8: what to mute. Nothing sent = everything (old contract). If the app sends only one of
        # posts / stories, the other one is OFF ("posts only" must not also hide stories).
        sent = serializer.validated_data
        if 'posts' in sent or 'stories' in sent:
            want_posts, want_stories = bool(sent.get('posts', False)), bool(sent.get('stories', False))
        else:
            want_posts, want_stories = True, True
        mute, created = MutedAccount.objects.get_or_create(
            user=request.user, muted_user=target,
            defaults={'mute_posts': want_posts, 'mute_stories': want_stories},
        )
        changed = False
        if not created and (mute.mute_posts != want_posts or mute.mute_stories != want_stories):
            mute.mute_posts, mute.mute_stories = want_posts, want_stories
            mute.save(update_fields=['mute_posts', 'mute_stories'])
            changed = True
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({
            "success": True,
            "message": "Account muted." if created else ("Mute updated." if changed else "Account already muted."),
            "data": MutedAccountSerializer(mute, context={'request': request}).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnmuteAccountAPIView(APIView):
    """DELETE /post/muted-accounts/<user_id>/ - unmute (idempotent).

    PATCH  /post/muted-accounts/<user_id>/ {"posts": bool, "stories": bool}  (P8)
        change WHAT is muted without unmuting. A key you leave out keeps its current value. If the result
        has both off the account is simply unmuted (`removed: true`). 404 when the account is not muted.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Change what is muted (posts / stories)", request=MuteScopeSerializer,
                   responses={200: MutedAccountSerializer}, tags=["Feed Feedback"])
    def patch(self, request, user_id):
        serializer = MuteScopeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        mute = MutedAccount.objects.filter(user=request.user, muted_user_id=user_id).select_related('muted_user').first()
        if mute is None:
            return Response({"success": False, "message": "Account is not muted."}, status=status.HTTP_404_NOT_FOUND)
        sent = serializer.validated_data
        posts = bool(sent.get('posts', mute.mute_posts))
        stories = bool(sent.get('stories', mute.mute_stories))
        from . import feed_cache
        if not posts and not stories:
            mute.delete()
            feed_cache.invalidate(request.user.pk)
            return Response({"success": True, "removed": True, "message": "Account unmuted."},
                            status=status.HTTP_200_OK)
        if (posts, stories) != (mute.mute_posts, mute.mute_stories):
            mute.mute_posts, mute.mute_stories = posts, stories
            mute.save(update_fields=['mute_posts', 'mute_stories'])
            feed_cache.invalidate(request.user.pk)
        return Response({"success": True, "removed": False, "message": "Mute updated.",
                         "data": MutedAccountSerializer(mute, context={'request': request}).data},
                        status=status.HTTP_200_OK)

    @extend_schema(summary="Unmute an account", tags=["Feed Feedback"])
    def delete(self, request, user_id):
        removed, _ = MutedAccount.objects.filter(user=request.user, muted_user_id=user_id).delete()
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "Account unmuted." if removed else "Account was not muted."},
                        status=status.HTTP_200_OK)


# ===================== FEED FEEDBACK CONTROLS - PART 2 =====================
# "Show fewer like this" + "Why am I seeing this". Details / contract:
# post/FEED_FEEDBACK_CONTROLS_TASK.md. Ranking lives in post/feed_mix.py
# (penalty_expression), the explanation in post/feed_explain.py.
class ShowFewerAPIView(APIView):
    """POST /post/<post_id>/show-fewer/
    {"targets": [{"kind": "category"}, {"kind": "hashtag", "key": "python"}, {"kind": "author"}],
     "reason": "not_interested"}            # reason optional (same choices as Not interested)

    = "Not interested" (the post is hidden at once) + a decaying negative
    ranking signal for each chosen target. `kind=category|author` are derived
    from the post; `kind=hashtag` needs `key` = one of the post's hashtags.
    Tapping again adds another step (capped). 201 when the post was hidden
    just now, 200 when it already was (the signals are still added).
    Undo: DELETE /post/<id>/not-interested/ + DELETE /post/feedback/<id>/ for
    every item of `data.feedback`.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Show fewer like this (hide + dampen category / hashtag / author)",
        request=ShowFewerRequestSerializer,
        responses={201: OpenApiTypes.OBJECT, 200: OpenApiTypes.OBJECT},
        tags=["Feed Feedback"],
    )
    def post(self, request, post_id):
        post = _visible_post_or_404(request.user, post_id)
        if post.user_id == request.user.id:
            return Response({"success": False, "message": "You can't hide your own post."},
                            status=status.HTTP_400_BAD_REQUEST)
        serializer = ShowFewerRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({"success": False, "message": _first_error_text(serializer.errors) or "Validation failed",
                             "errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            hide, hide_created, rows = apply_show_fewer(
                request.user, post, [(t['kind'], t.get('key')) for t in data['targets']], data['reason'],
            )
        except ShowFewerError as exc:
            return Response({"success": False, "message": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        context = {'request': request, 'author_labels': {str(post.user_id): post.user.username}}
        return Response({
            "success": True,
            "message": "We'll show you fewer posts like this.",
            "data": {
                "hidden": PostHideSerializer(hide).data,
                "feedback": FeedFeedbackSerializer(rows, many=True, context=context).data,
            },
        }, status=status.HTTP_201_CREATED if hide_created else status.HTTP_200_OK)


class FeedFeedbackListAPIView(generics.ListAPIView):
    """GET /post/feedback/[?kind=category|hashtag|author] - what I asked to see
    fewer of (for a "manage" screen), strongest/most recent first. Rows that
    decayed away completely are not listed."""
    permission_classes = [IsAuthenticated]
    serializer_class = FeedFeedbackSerializer
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        from . import feed_mix

        qs = FeedFeedback.objects.filter(user=self.request.user)
        kind = self.request.query_params.get('kind')
        if kind in FeedFeedback.Kind.values:
            qs = qs.filter(kind=kind)
        horizon = feed_mix.feedback_horizon_days(feed_mix.get_feedback_config())
        if horizon is not None:
            qs = qs.filter(updated_at__gte=timezone.now() - timedelta(days=horizon))
        return qs

    @extend_schema(
        summary="List my 'Show fewer' preferences",
        parameters=[OpenApiParameter(name='kind', type=str, required=False,
                                     description="category | hashtag | author")],
        tags=["Feed Feedback"],
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else list(queryset)
        author_ids = []
        for row in rows:
            if row.kind == FeedFeedback.Kind.AUTHOR:
                try:
                    # User primary keys are integers (BigAutoField), NOT UUIDs. Parsing as uuid.UUID raised
                    # ValueError for every row, so author preferences were listed with label=None.
                    author_ids.append(int(row.key))
                except (TypeError, ValueError):
                    continue
        labels = {str(pk): name for pk, name in User.objects.filter(id__in=author_ids).values_list('id', 'username')} if author_ids else {}
        serializer = FeedFeedbackSerializer(rows, many=True, context={'request': request, 'author_labels': labels})
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)


class FeedFeedbackDeleteAPIView(APIView):
    """DELETE /post/feedback/<feedback_id>/ - stop dampening that category /
    hashtag / author (also the undo of one 'Show fewer' item). Idempotent."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Remove one 'Show fewer' preference", tags=["Feed Feedback"])
    def delete(self, request, feedback_id):
        removed, _ = FeedFeedback.objects.filter(user=request.user, id=feedback_id).delete()
        from . import feed_cache
        feed_cache.invalidate(request.user.pk)  # T1 Part 5
        return Response({"success": True, "removed": bool(removed),
                         "message": "We'll show you this again." if removed else "Nothing to remove."},
                        status=status.HTTP_200_OK)


class WhyAmISeeingThisAPIView(APIView):
    """GET /post/<post_id>/why/ - the reasons this post is in MY feed, taken
    from the same signals the ranking uses (post/feed_explain.py):
    following, interest_category, liked_category, friend_of_follow, trending
    (+ popular / own_post / not_in_feed). `reasons[0]` is the headline.
    `dampened` lists my active 'Show fewer' rows that match this post.
    Same visibility rules as Not interested (404 for private / connections-
    only-not-followed / deleted posts)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Why am I seeing this post?",
        responses={200: WhyResponseSerializer},
        tags=["Feed Feedback"],
    )
    def get(self, request, post_id):
        from . import feed_explain

        post = _visible_post_or_404(request.user, post_id)
        following_ids = set(
            Follow.objects.filter(follower=request.user, status=Follow.Status.ACCEPTED)
            .values_list('following_id', flat=True)
        )
        payload = feed_explain.explain_post(
            request.user, post, _home_base_qs(request.user), following_ids, _video_and_velocity_boost,
        )
        return Response({"success": True, "data": payload}, status=status.HTTP_200_OK)

# ===================== FEED METRICS (T1 Part 5) =====================
class FeedOpsAPIView(APIView):
    """H10: `GET /post/feed/ops/?minutes=15` -> availability / error + degraded rate / p95 / alert events of the home
    feed vs the SLO (post/feed_ops.py). `?format=prom` -> Prometheus text for Grafana (deploy/monitoring/).

    Access: staff users (JWT) OR `Authorization: Bearer <settings.FEED_OPS_SCRAPE_TOKEN>` (for the Prometheus
    scraper; disabled while the token is empty). Numbers only - no user data."""
    permission_classes = [AllowAny]
    authentication_classes = []  # a scrape token is not a user JWT; staff users pass their JWT in the header below
    throttle_classes = [FeedOpsThrottle]  # scope `feed_ops`; fails open if the cache is down

    @staticmethod
    def _allowed(request):
        import hmac

        user = getattr(request, 'user', None)  # already authenticated (e.g. forced in tests / future auth backends)
        if user is not None and getattr(user, 'is_authenticated', False) and (user.is_staff or user.is_superuser):
            return True
        header = request.META.get('HTTP_AUTHORIZATION', '')
        token = getattr(settings, 'FEED_OPS_SCRAPE_TOKEN', '') or ''
        if token and header.startswith('Bearer ') and hmac.compare_digest(header[7:].strip(), token):
            return True
        if header.startswith('Bearer '):  # a staff user's normal JWT
            try:
                from login.authentication import SlidingSessionAuthentication  # the project's JWT auth

                auth = SlidingSessionAuthentication().authenticate(request)
                return bool(auth and (auth[0].is_staff or auth[0].is_superuser))
            except Exception:
                return False
        return False

    @extend_schema(summary="Home feed SLO numbers (staff / scrape token)", tags=["Feed"])
    def get(self, request):
        from . import feed_ops

        if not self._allowed(request):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        if request.query_params.get("format") == "prom":
            return HttpResponse(feed_ops.prometheus_text(), content_type="text/plain; version=0.0.4; charset=utf-8")
        try:
            minutes = min(1440, max(1, int(request.query_params.get("minutes", 15))))
        except (TypeError, ValueError):
            minutes = 15
        summary = feed_ops.summarize_last(minutes)
        summary["breaches"] = feed_ops.breaches(summary)
        return Response({"success": True, "data": summary})


class FeedMetricsAPIView(APIView):
    """`GET /post/feed/metrics/?days=7&surface=feed` - staff only. Same numbers
    as `manage.py feed_metrics` (CTR, dwell, session length, show-fewer rate,
    diversity - overall and per A/B variant)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Feed quality metrics (staff)", tags=["Feed"])
    def get(self, request):
        from datetime import timedelta as _td
        from . import feed_metrics

        if not (request.user.is_staff or request.user.is_superuser):
            return Response({"success": False, "message": "Staff only."}, status=status.HTTP_403_FORBIDDEN)
        try:
            days = min(90, max(1, int(request.query_params.get("days", 1))))
        except (TypeError, ValueError):
            days = 1
        surface = request.query_params.get("surface", "feed")
        if surface not in ("feed", "reels", "profile", "explore"):
            return Response({"success": False, "message": "Invalid surface."}, status=status.HTTP_400_BAD_REQUEST)
        end = timezone.now()
        return Response({"success": True, "data": feed_metrics.compute_metrics(end - _td(days=days), end, surface)})
