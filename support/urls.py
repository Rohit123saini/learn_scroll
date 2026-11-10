from django.urls import path

from .views import (
    BugReportView,
    FaqFeedbackView,
    FeatureRequestListCreateView,
    FeatureVoteView,
    TicketCloseView,
    TicketDetailView,
    TicketListCreateView,
    TicketMessageCreateView,
)

urlpatterns = [
    path("tickets/", TicketListCreateView.as_view(), name="support-tickets"),
    path("tickets/<uuid:pk>/", TicketDetailView.as_view(), name="support-ticket-detail"),
    path("tickets/<uuid:pk>/messages/", TicketMessageCreateView.as_view(), name="support-ticket-message"),
    path("tickets/<uuid:pk>/close/", TicketCloseView.as_view(), name="support-ticket-close"),
    path("bug-reports/", BugReportView.as_view(), name="support-bug-reports"),
    path("faq-feedback/", FaqFeedbackView.as_view(), name="support-faq-feedback"),
    path("feature-requests/", FeatureRequestListCreateView.as_view(), name="support-feature-requests"),
    path("feature-requests/<uuid:pk>/vote/", FeatureVoteView.as_view(), name="support-feature-vote"),
]
