# assignment/urls.py
"""
Mount this at whatever prefix the project's root URLconf uses, e.g.:

    path("api/assignment/", include("assignment.urls")),

which gives:
    /api/assignment/assignments/                       (list, create)
    /api/assignment/assignments/{id}/                   (retrieve, update, delete)
    /api/assignment/submissions/                        (list, create)
    /api/assignment/submissions/{id}/                   (retrieve, update, delete)
    /api/assignment/submissions/{id}/submit_freeform/    (PATCH)
    /api/assignment/submissions/{id}/submit_structured/  (POST)
    /api/assignment/submissions/{id}/grade/              (PATCH)
    /api/assignment/submissions/{id}/answer/{qid}/review/ (POST)
    /api/assignment/submissions/{id}/publish/            (POST)
    /api/assignment/submissions/{id}/unpublish/          (POST)
    /api/assignment/public/{slug}/                       (GET, AllowAny)
"""
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AssignmentSubmissionViewSet, AssignmentViewSet, PublicSubmissionView

router = DefaultRouter()
router.register("assignments", AssignmentViewSet, basename="assignment")
router.register("submissions", AssignmentSubmissionViewSet, basename="assignment-submission")

urlpatterns = router.urls + [
    path("public/<str:slug>/", PublicSubmissionView.as_view(), name="assignment-public-submission"),
]