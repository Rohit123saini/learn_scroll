# testseries/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import QuestionViewSet, TestAttemptViewSet, TestSeriesViewSet

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

urlpatterns = [
    path("", include(router.urls)),
    path("testseries/<uuid:series_pk>/questions/", question_list, name="testseries-question-list"),
    path("testseries/<uuid:series_pk>/questions/<uuid:pk>/", question_detail, name="testseries-question-detail"),
]
