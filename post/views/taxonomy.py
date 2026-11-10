# post/views/taxonomy.py — split out of the old post/views.py (P-C4). Code is moved verbatim.

from rest_framework.views import APIView
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
)
from ..models import (
    Post,
    UserInterest,
)
from rest_framework.response import Response
from ..serializers import UserInterestsUpdateSerializer
from rest_framework.decorators import (
    api_view,
    permission_classes,
    throttle_classes,
)
from common.open_throttles import PublicReadThrottle  # P-B3
from drf_spectacular.utils import extend_schema
from django.db import transaction


# ===================== CATEGORY TAXONOMY (TASK 4) =====================
# GET /post/categories/ — ApiService.getCategoryTaxonomy() (api_service.dart)
# was already calling this exact path and reading response['data']; nothing
# backing it existed, so both composers' category pickers 404'd on load.
@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([PublicReadThrottle])  # P-B3
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
        from .. import feed_cache
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
