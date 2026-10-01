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
    core/notification-mutes/{user_id}/    # N6-BE — POST mute / DELETE unmute
    core/search/                          # Task 18 — unified cross-app search
    core/notice-board/                    # Task 12 — home-screen Notice Board
    core/onboarding/suggestions/          # Task G18 — post-signup onboarding
    core/onboarding/complete/             # Task G18 — mark onboarding done/skipped
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    NoticeBoardView,
    NotificationPreferenceView,
    NotificationViewSet,
    OnboardingCompleteView,
    OnboardingSuggestionsView,
    SearchView,
    NotificationMuteView,
)

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")

urlpatterns = [
    # Plain APIView — router.register() won't pick this up on its own,
    # same reasoning as every other bare APIView path() in tuitionclass/urls.py.
    path("notification-preferences/me/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    # N6-BE — mute/unmute one account's notifications. Plain APIView,
    # same reasoning as notification-preferences/me/ above.
    path("notification-mutes/<int:user_id>/", NotificationMuteView.as_view(), name="notification-mute"),
    # Task 18 — unified "search everything" endpoint. Plain APIView, same
    # reasoning as notification-preferences/me/ above.
    path("search/", SearchView.as_view(), name="search"),
    # Task 12 — merged campus + tuition-class notice feed for the home
    # screen. Plain APIView, same reasoning as the two paths above.
    path("notice-board/", NoticeBoardView.as_view(), name="notice-board"),
    # Task G18 — post-signup onboarding flow (pick interests -> suggested
    # follows/campuses -> sample test). Plain APIViews, same reasoning as
    # the three paths above.
    path("onboarding/suggestions/", OnboardingSuggestionsView.as_view(), name="onboarding-suggestions"),
    path("onboarding/complete/", OnboardingCompleteView.as_view(), name="onboarding-complete"),
    path("", include(router.urls)),
]