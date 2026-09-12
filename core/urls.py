"""
core/urls.py

Wire into the root urlconf as:

    path("core/", include("core.urls")),   # prefix — pick whatever the
                                            # frontend team wants, "core/"
                                            # here just to have SOMETHING
                                            # non-clashing; see the root
                                            # LearnScroll/urls.py patch.

Resulting paths (with the "core/" prefix above):
    core/notifications/
    core/notifications/{id}/
    core/notifications/unread-count/
    core/notifications/{id}/mark-read/
    core/notifications/mark-all-read/
    core/notification-preferences/me/
    core/search/                          # Task 18 — unified cross-app search
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import NotificationPreferenceView, NotificationViewSet, SearchView

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    # Plain APIView — router.register() won't pick this up on its own,
    # same reasoning as every other bare APIView path() in liveclass/urls.py.
    path("notification-preferences/me/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    # Task 18 — unified "search everything" endpoint. Plain APIView, same
    # reasoning as notification-preferences/me/ above.
    path("search/", SearchView.as_view(), name="search"),
    path("", include(router.urls)),
]