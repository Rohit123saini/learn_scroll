from django.urls import path

from . import views

app_name = "copyrights"

urlpatterns = [
    path("claims/", views.ClaimListCreateView.as_view(), name="claims"),
    path("claims/<uuid:claim_id>/", views.ClaimDetailView.as_view(), name="claim-detail"),
    path("claims/<uuid:claim_id>/withdraw/", views.ClaimWithdrawView.as_view(), name="claim-withdraw"),
    path("claims/<uuid:claim_id>/evidence/", views.ClaimEvidenceView.as_view(), name="claim-evidence"),
    path("claims/<uuid:claim_id>/info/", views.ClaimInfoView.as_view(), name="claim-info"),
    path("claims/<uuid:claim_id>/court-action/", views.ClaimCourtActionView.as_view(), name="claim-court-action"),
    path("notices/", views.NoticeListView.as_view(), name="notices"),
    path("notices/<uuid:claim_id>/", views.NoticeDetailView.as_view(), name="notice-detail"),
    path("notices/<uuid:claim_id>/counter/", views.NoticeCounterView.as_view(), name="notice-counter"),
    path("verify-email/", views.EmailCodeSendView.as_view(), name="verify-email"),
    path("verify-email/confirm/", views.EmailCodeConfirmView.as_view(), name="verify-email-confirm"),
    path("standing/", views.StandingView.as_view(), name="standing"),
    path("strikes/<int:strike_id>/appeal/", views.StrikeAppealView.as_view(), name="strike-appeal"),
    path("appeals/", views.MyAppealsView.as_view(), name="my-appeals"),
    path("termination-appeal/", views.TerminationAppealView.as_view(), name="termination-appeal"),
    path("policy/", views.PolicyView.as_view(), name="policy"),
    path("policy/page/", views.policy_page, name="policy-page"),
    path("transparency/", views.TransparencyView.as_view(), name="transparency"),
    path("transparency/page/", views.transparency_page, name="transparency-page"),
]
