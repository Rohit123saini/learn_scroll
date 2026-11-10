"""
core/guest.py - first-launch / guest browsing + starter content for an empty Home.

PUBLIC (no login, no token - authentication is switched off so a stale token never turns these into 401):
    GET core/guest/options/                       interests the picker shows (+ max picks)
    GET core/guest/suggested-accounts/?interests=tech,education&limit=8
                                                  popular public accounts to look at (cannot be followed
                                                  until the guest signs up)
    (the read-only guest feed itself is post/guest_views.py -> GET post/guest/feed/)

SIGNED IN:
    GET core/home/starter/                        what to show instead of a blank Home: a few popular posts in
                                                  the user's interests, people to follow, a free sample test

Same safety rules everywhere a stranger is shown something: public + approved posts only, never a private,
inactive or KNOWN-minor account (login.age.minor_cutoff_date). The post rules live in
post.guest_views.public_posts_queryset so there is exactly one definition of "safe to show".
"""
from django.contrib.auth import get_user_model
from django.db.models import Case, Exists, IntegerField, OuterRef, Value, When
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from .onboarding_quickstart import MAX_INTERESTS

User = get_user_model()

GUEST_ACCOUNTS_DEFAULT = 8
GUEST_ACCOUNTS_MAX = 12
STARTER_POSTS = 6


class GuestThrottle(AnonRateThrottle):
    scope = "guest_public"
    rate = "60/min"


class StarterThrottle(UserRateThrottle):
    scope = "home_starter"
    rate = "30/min"


def _interest_options():
    from post.models import Post

    # "general" / "other" say nothing about taste (same rule as the signed-in onboarding picker)
    return [{"key": k, "label": l} for k, l in Post.CATEGORY_CHOICES if k not in ("general", "other")]


def _public_accounts_queryset(categories=()):
    """Public accounts that actually have something to look at, best first. A guest has no follows / blocks,
    so unlike discovery.suggested_users_queryset there is no viewer to exclude."""
    from login.age import minor_cutoff_date
    from post.models import Post

    has_public_post = Exists(
        Post.objects.filter(user_id=OuterRef("pk"), is_deleted=False, moderation_status="approved",
                            visibility="public", is_archived=False, is_scheduled=False)
    )
    qs = (
        User.objects.filter(is_active=True, is_private=False)
        .exclude(date_of_birth__gt=minor_cutoff_date())
        .filter(has_public_post)
    )
    if categories:
        match = Exists(
            Post.objects.filter(user_id=OuterRef("pk"), category__in=list(categories), is_deleted=False,
                                moderation_status="approved", visibility="public")
        )
        qs = qs.annotate(_interest_match=Case(When(match, then=Value(1)), default=Value(0),
                                              output_field=IntegerField()))
        return qs.order_by("-_interest_match", "-followers_count", "-id")
    return qs.order_by("-followers_count", "-id")


class GuestOptionsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestThrottle]

    def get(self, request):
        return Response({"max_interests": MAX_INTERESTS, "interests": _interest_options()})


class GuestSuggestedAccountsView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [GuestThrottle]

    def get(self, request):
        from post.guest_views import parse_interests
        from user_profile.serializers import UserSearchSerializer

        categories = parse_interests(request.query_params.get("interests"))
        try:
            limit = int(request.query_params.get("limit", GUEST_ACCOUNTS_DEFAULT))
        except (TypeError, ValueError):
            limit = GUEST_ACCOUNTS_DEFAULT
        limit = max(1, min(limit, GUEST_ACCOUNTS_MAX))
        rows = _public_accounts_queryset(categories)[:limit]
        return Response({"results": UserSearchSerializer(rows, many=True, context={"request": request}).data})


class HomeStarterView(APIView):
    """Signed-in: the content an EMPTY Home shows instead of a blank page (the Following tab of someone who
    follows nobody yet). Nothing here is stored; it is the same data the onboarding screens use."""

    permission_classes = [IsAuthenticated]
    throttle_classes = [StarterThrottle]

    def get(self, request):
        from post.guest_views import public_posts_queryset, with_engagement_score
        from post.models import UserInterest
        from post.serializers import PostListSerializer
        from post.services import exclude_hidden_and_muted
        from user_profile.models import Follow

        from .views import OnboardingSuggestionsView

        user = request.user
        interests = list(UserInterest.objects.filter(user=user).values_list("category", flat=True))

        following = Follow.objects.filter(follower=user).values("following_id")
        qs = exclude_hidden_and_muted(public_posts_queryset().exclude(user=user).exclude(user_id__in=following), user)
        if interests:
            narrowed = qs.filter(category__in=interests)
            if narrowed.exists():
                qs = narrowed
        ranked = with_engagement_score(qs, interests).order_by("-engagement_score", "-created_at", "-id")[:STARTER_POSTS]
        posts = PostListSerializer(ranked, many=True, context={"request": request, "following_ids": set()}).data

        suggestions = OnboardingSuggestionsView()
        return Response({
            "interests": interests,
            "has_interests": bool(interests),
            "posts": posts,
            "suggested_users": suggestions._suggested_users(user),
            "sample_test_series": suggestions._sample_test_series(limit=2, user=user),
        })
