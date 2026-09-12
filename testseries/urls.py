# testseries/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    QuestionViewSet, TestAttemptViewSet, TestSeriesReviewViewSet, TestSeriesViewSet,
)

router = DefaultRouter()
router.register(r"testseries", TestSeriesViewSet, basename="testseries")
router.register(r"attempts", TestAttemptViewSet, basename="testseries-attempt")

# Questions are nested under a series (creator-owned, draft-only writes —
# see QuestionViewSet) rather than routed through the DefaultRouter, so
# series_pk is always explicit in the URL instead of relying on a
# request-body field.
question_list = QuestionViewSet.as_view({"get": "list", "post": "create"})
question_detail = QuestionViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update", "delete": "destroy"}
)

# Reviews (Task 15): same "explicit series_pk in the URL, no
# DefaultRouter" reasoning as questions above, plus one extra
# non-nested route for the creator's cross-series review dashboard —
# see TestSeriesReviewViewSet's docstring for why that one has no
# series_pk at all.
review_list = TestSeriesReviewViewSet.as_view({"get": "list", "post": "create"})
review_my_view = TestSeriesReviewViewSet.as_view({"get": "my_view"})

urlpatterns = [
    path("", include(router.urls)),
    path("testseries/<uuid:series_pk>/questions/", question_list, name="testseries-question-list"),
    path("testseries/<uuid:series_pk>/questions/<uuid:pk>/", question_detail, name="testseries-question-detail"),
    # Placed before the nested <uuid:series_pk> route below: harmless
    # either way since "reviews" can never match the uuid converter,
    # but keeping the literal path first reads more clearly.
    path("testseries/reviews/my-view/", review_my_view, name="testseries-review-my-view"),
    path("testseries/<uuid:series_pk>/reviews/", review_list, name="testseries-review-list"),
]