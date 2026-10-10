from django.urls import path

from . import views

app_name = "copyrights"

urlpatterns = [
    path("claims/", views.ClaimListCreateView.as_view(), name="claims"),
    path("claims/<uuid:claim_id>/", views.ClaimDetailView.as_view(), name="claim-detail"),
    path("claims/<uuid:claim_id>/withdraw/", views.ClaimWithdrawView.as_view(), name="claim-withdraw"),
    path("claims/<uuid:claim_id>/info/", views.ClaimInfoView.as_view(), name="claim-info"),
    path("claims/<uuid:claim_id>/court-action/", views.ClaimCourtActionView.as_view(), name="claim-court-action"),
    path("notices/", views.NoticeListView.as_view(), name="notices"),
    path("notices/<uuid:claim_id>/", views.NoticeDetailView.as_view(), name="notice-detail"),
    path("notices/<uuid:claim_id>/counter/", views.NoticeCounterView.as_view(), name="notice-counter"),
    path("standing/", views.StandingView.as_view(), name="standing"),
]
