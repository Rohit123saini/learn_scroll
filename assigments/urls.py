# assigments/urls.py
"""
Mount this at whatever prefix the project's root URLconf uses, e.g.:

    path("api/assigments/", include("assigments.urls")),

which gives:
    /api/assigments/assigmentss/                       (list, create)
    /api/assigments/assigmentss/{id}/                   (retrieve, update, delete)
    /api/assigments/submissions/                        (list, create)
    /api/assigments/submissions/{id}/                   (retrieve, update, delete)
    /api/assigments/submissions/{id}/submit_freeform/    (PATCH)
    /api/assigments/submissions/{id}/submit_structured/  (POST)
    /api/assigments/submissions/{id}/grade/              (PATCH)
    /api/assigments/submissions/{id}/answer/{qid}/review/ (POST)
    /api/assigments/submissions/{id}/publish/            (POST)
    /api/assigments/submissions/{id}/unpublish/          (POST)
    /api/assigments/public/{slug}/                       (GET, AllowAny)
"""
from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import assigmentsSubmissionViewSet, assigmentsViewSet, PublicSubmissionView

router = DefaultRouter()
router.register("assigmentss", assigmentsViewSet, basename="assigments")
router.register("submissions", assigmentsSubmissionViewSet, basename="assigments-submission")

urlpatterns = router.urls + [
    path("public/<str:slug>/", PublicSubmissionView.as_view(), name="assigments-public-submission"),
]