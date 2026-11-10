# post/views/discovery.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from django.db.models import (
    Case,
    ExpressionWrapper,
    F,
    FloatField,
    Value,
    When,
)
from collections import Counter
from common.pagination import EngagementCursorPagination
from user_profile.models import Follow
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from ..models import (
    Post,
    UserInterest,
)
from ..serializers import PostListSerializer
from rest_framework.response import Response
from ..services import exclude_hidden_and_muted
from rest_framework import generics
from rest_framework.decorators import permission_classes
from datetime import timedelta
from django.utils import timezone
from .feed import (
    _video_and_velocity_boost,
    _without_superseded_reposts,
)


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
