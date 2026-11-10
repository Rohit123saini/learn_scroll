from django.urls import path

from . import views

urlpatterns = [
    path("feedback/", views.AiFeedbackAPIView.as_view(), name="ai-feedback"),
    path("feedback/mine/", views.AiFeedbackMineAPIView.as_view(), name="ai-feedback-mine"),
    path("staff/feedback/", views.StaffAiFeedbackListAPIView.as_view(), name="ai-quality-staff-feedback"),
    path("staff/feedback/<uuid:pk>/", views.StaffAiFeedbackDetailAPIView.as_view(), name="ai-quality-staff-feedback-detail"),
    path("staff/summary/", views.StaffAiQualitySummaryAPIView.as_view(), name="ai-quality-staff-summary"),
    path("staff/cost/", views.StaffAiCostAPIView.as_view(), name="ai-quality-staff-cost"),
]
