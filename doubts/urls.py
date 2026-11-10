from django.urls import path

from .views import (
    DoubtHubActionAPIView,
    DoubtHubAskAPIView,
    DoubtHubDetailAPIView,
    DoubtHubListAPIView,
    DoubtHubSummaryAPIView,
    DoubtHubTargetsAPIView,
)

app_name = "doubts"

# Static segments first: "summary/" must win over "<str:hub_id>/".
urlpatterns = [
    path("", DoubtHubListAPIView.as_view(), name="list"),
    path("summary/", DoubtHubSummaryAPIView.as_view(), name="summary"),
    path("targets/", DoubtHubTargetsAPIView.as_view(), name="targets"),
    path("ask/", DoubtHubAskAPIView.as_view(), name="ask"),
    path("<str:hub_id>/", DoubtHubDetailAPIView.as_view(), name="detail"),
    path("<str:hub_id>/<str:action>/", DoubtHubActionAPIView.as_view(), name="action"),
]
