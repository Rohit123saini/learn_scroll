"""
post/guest_views.py - READ-ONLY feed for people who have not signed up (guest browsing).

    GET /post/guest/feed/?interests=tech,education&cursor=...&page_size=10     (no login, no token)

Rules (a guest is an anonymous stranger, so the rules are stricter than for the signed-in Explore feed):
  * only public, approved, non-sensitive, published posts - the same "safe to show" filters Explore uses;
  * never an account that is private, inactive or a KNOWN minor (login.age.minor_cutoff_date - same
    cut-off the follow suggestions use; accounts with no date of birth are not assumed anything about);
  * no reposts (the original may belong to a private / minor account) and no Notes / Resource posts
    (their files need a signed-in download);
  * authentication is switched OFF for this view on purpose: a stale or expired token in the app must
    not turn a public read into a 401 - the viewer is always anonymous, so every viewer-state field in
    the card (liked / saved / my_reaction / following) comes back false;
  * small pages (max 10), its own throttle, same keyset cursor as Explore (`engagement_score`).

`public_posts_queryset()` is shared with core/guest.py (the signed-in "starter content" for an empty
Home), so the two can never drift apart on what counts as safe to show a stranger.
"""
from datetime import timedelta

from django.db.models import ExpressionWrapper, F, FloatField
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle

from common.pagination import EngagementCursorPagination

from .models import Post
from .serializers import PostListSerializer

MAX_GUEST_INTERESTS = 3
GUEST_PAGE_SIZE = 10


class GuestFeedThrottle(AnonRateThrottle):
    scope = "guest_feed"
    rate = "60/min"


class GuestCursorPagination(EngagementCursorPagination):
    page_size = GUEST_PAGE_SIZE
    max_page_size = GUEST_PAGE_SIZE  # overrides the parent's settings-driven property: a guest never gets more


def parse_interests(raw):
    """'tech, education,bogus' -> ['tech', 'education'] (valid keys only, de-duped, at most 3)."""
    valid = {k for k, _ in Post.CATEGORY_CHOICES}
    out = []
    for part in str(raw or "").split(","):
        c = part.strip()
        if c and c in valid and c not in out:
            out.append(c)
    return out[:MAX_GUEST_INTERESTS]


def public_posts_queryset():
    """Posts that are safe to show a stranger (see the module docstring for each rule)."""
    from login.age import minor_cutoff_date

    return (
        Post.objects.select_related("user")
        .prefetch_related("media")
        .filter(
            is_deleted=False, is_archived=False, moderation_status="approved", is_sensitive=False,
            visibility="public", is_scheduled=False, user__is_active=True, user__is_private=False,
        )
        .exclude(post_type="repost")
        .exclude(kind=Post.KIND_RESOURCE)
        .exclude(user__date_of_birth__gt=minor_cutoff_date())
    )


def with_engagement_score(qs, categories=()):
    """Annotate `engagement_score` (what the keyset cursor orders by). Posts in the chosen categories
    get the same +15 interest bonus HomeFeedView / Explore give; recent posts are preferred, falling back
    to all time when the last 30 days are empty (a young platform must not show an empty feed)."""
    from django.db.models import Case, Value, When

    from .views import _video_and_velocity_boost

    video_boost, velocity_boost = _video_and_velocity_boost()
    interest_boost = (
        Case(When(category__in=list(categories), then=Value(15.0)), default=Value(0.0), output_field=FloatField())
        if categories else Value(0.0, output_field=FloatField())
    )
    score = ExpressionWrapper(
        F("likes_count") * 3.0 + F("comments_count") * 5.0 + F("shares_count") * 10.0 + F("views_count") * 0.1
        + interest_boost + video_boost + velocity_boost,
        output_field=FloatField(),
    )
    recent = qs.filter(created_at__gte=timezone.now() - timedelta(days=30))
    base = recent if recent.exists() else qs
    return base.annotate(engagement_score=score)


class GuestFeedAPIView(generics.ListAPIView):
    authentication_classes = []  # anonymous by design - see module docstring
    permission_classes = [AllowAny]
    throttle_classes = [GuestFeedThrottle]
    serializer_class = PostListSerializer
    pagination_class = GuestCursorPagination

    def get_serializer_context(self):
        return {"request": self.request, "following_ids": set()}

    @extend_schema(
        summary="Guest feed (read-only, no login)",
        parameters=[OpenApiParameter(name="interests", type=str, required=False,
                                     description="Comma separated categories (max 3), e.g. tech,education")],
        responses={200: OpenApiTypes.OBJECT},
        tags=["Guest"],
    )
    def get_queryset(self):
        categories = parse_interests(self.request.query_params.get("interests"))
        qs = public_posts_queryset()
        if categories:
            narrowed = qs.filter(category__in=categories)
            if narrowed.exists():  # an interest nobody posted in yet must not give an empty feed
                qs = narrowed
        return with_engagement_score(qs, categories)
