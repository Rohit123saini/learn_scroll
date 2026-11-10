# post/views/__init__.py — P-C4: the old god-file post/views.py is now this package.
# Everything that used to be importable from `post.views` still is (urls.py, other apps, tests, management commands).
# Module map: see STATUS_PC4_POST_VIEWS.md. Do NOT add logic here; add it to the matching submodule and re-export it below.

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

from ..models import (
    Post, PostAnswer, PostMedia, PostPoll, PostPollOption, PostPollVote,
    PostView, PostLike, PostSave, PostComment, Story, StoryView,
    StoryReaction, PostChunkedUpload, UserInterest, PostHide, MutedAccount, FeedFeedback,
    CloseFriend, StorySticker, PostEvent,
)
from ..story_sticker_responses import (
    ResponseError, build_interaction_stats, cast_quiz_answer, cast_vote, submit_answer,
)
from .. import content_defaults as _content_defaults  # S4b account-level defaults for new posts
from .. import story_gifs
from ..story_visibility import (
    STORY_RESHARE_OFF, STORY_RESHARE_OFF_MESSAGE, STORY_SAVE_OFF, STORY_SAVE_OFF_MESSAGE,
    can_reply_to_story, can_reshare_story, can_save_story, filter_stories_visible_to, get_visible_story_or_404,
)
from ..close_friends_views import blocked_user_ids
from ..feed_resilience import FeedHomeThrottle, FeedOpsThrottle
from ..serializers import (
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
from ..signals import decrement_posts_count_on_soft_delete
from ..idempotency import idempotent  # H20 duplicate-submit guard
from ..services import (
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

# ---- re-exports (explicit; private helpers included on purpose) ----
from ._common import (  # noqa: E402,F401
    User,
    logger,
    StandardResultsSetPagination,
    HomeFeedPagination,
    _first_error_text,
)
from .create import (  # noqa: E402,F401
    PostCreateAPIView,
)
from .taxonomy import (  # noqa: E402,F401
    category_taxonomy,
    UserInterestsAPIView,
)
from .music import (  # noqa: E402,F401
    FREESOUND_SEARCH_URL,
    FREESOUND_TRENDING_QUERY,
    freesound_music_search,
)
from .feed import (  # noqa: E402,F401
    _without_superseded_reposts,
    _video_and_velocity_boost,
    _home_base_qs,
    HomeFeedView,
)
from .detail import (  # noqa: E402,F401
    PostListAPIView,
    PostDetailAPIView,
)
from .events import (  # noqa: E402,F401
    SEEN_BATCH_MAX,
    PostSeenBatchAPIView,
    POST_EVENTS_MAX,
    POST_EVENT_MAX_DWELL_MS,
    PostEventBulkAPIView,
    PostVideoProgressAPIView,
)
from .post_manage import (  # noqa: E402,F401
    PostRepostAPIView,
    PostShareItemAPIView,
    PostDeleteAPIView,
    PostEditAPIView,
    PostVisibilityAPIView,
    MAX_PINNED_POSTS,
    _pin_state,
    PostPinAPIView,
    _archive_state,
    PostArchiveAPIView,
    ArchivedPostsListAPIView,
    PostTaggedAPIView,
    PostTagAPIView,
)
from .share import (  # noqa: E402,F401
    _share_guard,
    PostShareAPIView,
    PostShareLinkAPIView,
)
from .uploads import (  # noqa: E402,F401
    post_chunked_upload_init,
    post_chunked_upload_status,
    post_chunked_upload_complete,
)
from .stories import (  # noqa: E402,F401
    StoryCreateAPIView,
    StoryListAPIView,
    StoryLimitsAPIView,
    StoryLiveAPIView,
    StoryDetailAPIView,
    StoryMentionCandidatesAPIView,
    _get_interactive_sticker,
    _response_error,
    StoryPollVoteAPIView,
    StoryQuestionAnswerAPIView,
    StoryQuizAnswerAPIView,
    StoryStickerResponsesAPIView,
    StoryViewAPIView,
    StoryReactAPIView,
    StoryReplyAPIView,
    StoryShareAPIView,
    StorySaveAPIView,
    StoryViewersAPIView,
)
from .gifs import (  # noqa: E402,F401
    GIPHY_SEARCH_URL,
    GIPHY_TRENDING_URL,
    GIF_SEARCH_MAX_LIMIT,
    GIF_SEARCH_DEFAULT_LIMIT,
    GifSearchAPIView,
)
from .media import (  # noqa: E402,F401
    serve_media_with_range,
)
from .reactions import (  # noqa: E402,F401
    PostReactionAPIView,
)
from .doubts import (  # noqa: E402,F401
    PostAnswerListCreateAPIView,
    PostAnswerMarkBestAPIView,
    DoubtConfigAPIView,
    DoubtSimilarAPIView,
)
from .counts import (  # noqa: E402,F401
    _post_counts_etag,
    _if_none_match_hits,
    PostCountsAPIView,
)
from .feed_ads import (  # noqa: E402,F401
    build_feed_ad_config,
    FeedAdConfigAPIView,
)
from .saves import (  # noqa: E402,F401
    PostSaveToggleAPIView,
    SavedPostsListAPIView,
)
from .discovery import (  # noqa: E402,F401
    HashtagPostsAPIView,
    TrendingHashtagsAPIView,
    ExploreFeedAPIView,
)
from .feedback import (  # noqa: E402,F401
    _visible_post_or_404,
    NotInterestedAPIView,
    NotInterestedListAPIView,
    MutedAccountsAPIView,
    UnmuteAccountAPIView,
    ShowFewerAPIView,
    FeedFeedbackListAPIView,
    FeedFeedbackDeleteAPIView,
    WhyAmISeeingThisAPIView,
)
from .ops import (  # noqa: E402,F401
    FeedOpsAPIView,
    FeedMetricsAPIView,
)
