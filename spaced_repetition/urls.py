from django.urls import path

from . import views as v

app_name = "spaced_repetition"

urlpatterns = [
    path("config/", v.RevisionConfigView.as_view(), name="config"),
    path("due/", v.RevisionDueView.as_view(), name="due"),
    path("review/", v.RevisionReviewView.as_view(), name="review"),
    path("stats/", v.RevisionStatsView.as_view(), name="stats"),
]
