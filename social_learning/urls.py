from django.urls import path

from . import views as v

app_name = "social_learning"

urlpatterns = [
    path("config/", v.SocialConfigView.as_view(), name="config"),
    path("prefs/", v.SocialPrefsView.as_view(), name="prefs"),
    # --- study-with-me Pomodoro
    path("pomodoro/claim/", v.PomodoroClaimView.as_view(), name="pomodoro-claim"),
    path("pomodoro/stats/", v.PomodoroStatsView.as_view(), name="pomodoro-stats"),
    path("pomodoro/<uuid:conversation_id>/", v.PomodoroStateView.as_view(), name="pomodoro-state"),
    path("pomodoro/<uuid:conversation_id>/<str:action>/", v.PomodoroControlView.as_view(), name="pomodoro-control"),
    # --- accountability partner
    path("partners/", v.PartnerListView.as_view(), name="partners"),
    path("partners/suggestions/", v.PartnerSuggestionsView.as_view(), name="partner-suggestions"),
    path("partners/request/", v.PartnerRequestView.as_view(), name="partner-request"),
    path("partners/<uuid:pair_id>/", v.PartnerDetailView.as_view(), name="partner-detail"),
    path("partners/<uuid:pair_id>/respond/", v.PartnerRespondView.as_view(), name="partner-respond"),
    path("partners/<uuid:pair_id>/goal/", v.PartnerGoalView.as_view(), name="partner-goal"),
    path("partners/<uuid:pair_id>/nudge/", v.PartnerNudgeView.as_view(), name="partner-nudge"),
    path("partners/<uuid:pair_id>/end/", v.PartnerEndView.as_view(), name="partner-end"),
    path("study-log/", v.StudyLogView.as_view(), name="study-log"),
    # --- seasons
    path("seasons/current/", v.SeasonCurrentView.as_view(), name="season-current"),
    path("seasons/history/", v.SeasonHistoryView.as_view(), name="season-history"),
    path("seasons/rewards/", v.SeasonRewardsView.as_view(), name="season-rewards"),
    path("seasons/<uuid:season_id>/standings/", v.SeasonStandingsView.as_view(), name="season-standings"),
]
