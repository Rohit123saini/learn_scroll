from django.urls import path

from .views import LeaderboardBoardAPIView, MyLeaderboardRankAPIView

app_name = "leaderboard"

urlpatterns = [
    path("board/", LeaderboardBoardAPIView.as_view(), name="board"),
    path("my-rank/", MyLeaderboardRankAPIView.as_view(), name="my-rank"),
]
