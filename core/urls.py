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
    core/notifications/mark-all-read/     # optional ?source=&category=
    core/notifications/{id}/snooze/       # N6 - POST {"minutes"?} remind again later
    core/notifications/{id}/turn-off-type/  # N6 - POST push off for this type (undo: turn-on-type/)
    core/notifications/mark-read-batch/   # POST {"ids": [..]} (N1)
    core/notifications/bulk/              # POST {"ids": [..], "action": read|unread|delete|archive|unarchive} (N4)
    core/notifications/clear-read/        # DELETE — remove all already-read rows
    core/notification-preferences/me/
    core/notification-preferences/timezone/        # N5 - POST {"timezone": "Asia/Kolkata"} phone -> server zone sync
    core/notification-preferences/digest-preview/  # N5 - GET "aisa dikhega" roll-up + study digest preview
    core/notification-mutes/{user_id}/    # N6-BE — POST mute / DELETE unmute
    core/notification-scope-mutes/        # N9 - GET list / POST {scope_type,scope_id,duration} / DELETE ?scope_type=&scope_id=
    core/notifications/{id}/opened/       # N10 - POST {"surface": push|banner|list} app reports an open (telemetry only)
    core/notification-ops/                # N10 - staff / scrape token: push numbers + SLO; ?format=prom for Prometheus
    core/analytics/config/                # X3 - JWT: may the app collect (consent) + this user's experiment variants
    core/analytics/events/                # X3 - JWT: settings_opened / profile_edit_opened / experiment_exposure (consent-gated)
    core/analytics/product/               # X3 - staff / scrape token: funnels + A/B results; ?format=prom for Grafana
    core/notification-preferences/channels/                 # N8 - GET status of e-mail / sms / whatsapp
    core/notification-preferences/channels/email/send-code/ # N8 - POST
    core/notification-preferences/channels/email/verify/    # N8 - POST {"code"}
    core/notification-preferences/channels/consent/         # N8 - POST {"channel","granted"}
    core/notifications/unsubscribe/{token}/                 # N8 - PUBLIC GET confirm page / POST one-click
    core/channel-webhooks/{email|sms|whatsapp}/             # N8 - provider bounce / complaint / STOP (X-Webhook-Secret)
    core/search/                          # Task 18 — unified cross-app search
    core/notice-board/                    # Task 12 — home-screen Notice Board
    core/app-config/                      # T1.8 — PUBLIC force-update / maintenance config
    core/onboarding/suggestions/          # Task G18 — post-signup onboarding
    core/onboarding/complete/             # Task G18 — mark onboarding done/skipped
    core/onboarding/options/              # quick-start picker choices
    core/onboarding/quick-start/          # class + exam + <=3 interests, one call
    core/funnel/events/                   # T3.8 — app reports install / app_open (public)
    core/funnel/first-feed-latency/       # P-A — app reports first-feed wait (ms), once (JWT)
    core/onboarding/step/                 # P-F1 — app reports an onboarding step viewed/completed/skipped (JWT)
    core/analytics/funnel/                # T3.8 — staff JSON report
    core/analytics/funnel/dashboard/      # T3.8 — staff HTML dashboard
    core/me/context/                      # who am I for UI rules: is_minor / is_staff / ads_enabled / safe_defaults
    core/me/safe-defaults/                # POST - apply the safe defaults a minor account must have
    core/me/capabilities/                 # S12 - roles + what Settings may show (teacher / parent / staff / campus admin)
    core/guest/options/                   # PUBLIC - guest interest picker choices
    core/guest/suggested-accounts/        # PUBLIC - popular public accounts for a guest
    core/home/starter/                    # signed-in: starter content for an empty Home
    core/continue-learning/               # H14 - resume / homework / next test / streak
    core/home-bootstrap/                  # H4 - me, role signals, unread, daily challenge, ad config, stories, classrooms, invite (feed stays separate)
"""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .analytics_views import AnalyticsConfigView, ProductAnalyticsOpsView, ProductEventsView  # X3
from .funnel_views import FirstFeedLatencyView, FunnelEventsView, FunnelReportView, OnboardingStepView, funnel_dashboard  # T3.8 (+ P-A latency)
from .onboarding_quickstart import OnboardingOptionsView, OnboardingQuickStartView
from .daily_challenge import DailyChallengeView  # T2.3 - Home daily-challenge card
from .guest import GuestOptionsView, GuestSuggestedAccountsView, HomeStarterView  # guest browsing + starter content
from .me_context import ApplySafeDefaultsView, MeContextView  # role / minor / staff context for the app
from .capabilities import MeCapabilitiesView  # S12 - role-aware settings
from .home_bootstrap import HomeBootstrapView  # H4 - one call for everything Home needs next to the feed
from .continue_learning import ContinueLearningView, HabitTodayView  # H14 - Home "Continue learning" strip
from .views import (
    NoticeBoardView,
    NotificationOpsView,
    NotificationPreferenceView,
    NotificationViewSet,
    OnboardingCompleteView,
    OnboardingSuggestionsView,
    SearchView,
    AppConfigView,
    NotificationMuteView,
    NotificationScopeMuteView,
    NotificationTimezoneSyncView,
    NotificationDigestPreviewView,
)

router = DefaultRouter()
router.register(r"notifications", NotificationViewSet, basename="notification")

from .channel_views import (  # noqa: E402 - N8
    ChannelConsentView,
    ChannelStatusView,
    ChannelWebhookView,
    EmailSendCodeView,
    EmailVerifyView,
    UnsubscribeView,
)

from .unified_search_views import SearchTrendingView, UnifiedSearchView  # P-F7

urlpatterns = [
    # Plain APIView — router.register() won't pick this up on its own,
    # same reasoning as every other bare APIView path() in tuitionclass/urls.py.
    path("notification-preferences/me/", NotificationPreferenceView.as_view(), name="notification-preferences"),
    # N5 - phone timezone sync + digest preview. Plain APIViews, same reasoning as above.
    path("notification-preferences/timezone/", NotificationTimezoneSyncView.as_view(), name="notification-timezone-sync"),
    path("notification-preferences/digest-preview/", NotificationDigestPreviewView.as_view(), name="notification-digest-preview"),
    # N8 - e-mail / SMS / WhatsApp channel setup (verify, consent, status), public unsubscribe, provider webhooks.
    # NOTE: the unsubscribe path must sit BEFORE the router include (router has notifications/<pk>/).
    path("notifications/unsubscribe/<str:token>/", UnsubscribeView.as_view(), name="notification-unsubscribe"),
    path("notification-preferences/channels/", ChannelStatusView.as_view(), name="notification-channels"),
    path("notification-preferences/channels/email/send-code/", EmailSendCodeView.as_view(), name="notification-email-send-code"),
    path("notification-preferences/channels/email/verify/", EmailVerifyView.as_view(), name="notification-email-verify"),
    path("notification-preferences/channels/consent/", ChannelConsentView.as_view(), name="notification-channel-consent"),
    path("channel-webhooks/<str:provider>/", ChannelWebhookView.as_view(), name="channel-webhook"),
    # N6-BE — mute/unmute one account's notifications. Plain APIView,
    # same reasoning as notification-preferences/me/ above.
    path("notification-mutes/<int:user_id>/", NotificationMuteView.as_view(), name="notification-mute"),
    path("notification-scope-mutes/", NotificationScopeMuteView.as_view(), name="notification-scope-mutes"),  # N9
    path("notification-ops/", NotificationOpsView.as_view(), name="notification-ops"),  # N10
    # X3 -- privacy-safe product analytics + experiments (consent-gated aggregate counters, no ids)
    path("analytics/config/", AnalyticsConfigView.as_view(), name="analytics-config"),
    path("analytics/events/", ProductEventsView.as_view(), name="analytics-events"),
    path("analytics/product/", ProductAnalyticsOpsView.as_view(), name="analytics-product"),
    # Task 18 — unified "search everything" endpoint. Plain APIView, same
    # reasoning as notification-preferences/me/ above.
    path("search/", SearchView.as_view(), name="search"),
    # P-F7 - unified search over people / posts / classes / tests / doubts (+ trending). Plain APIViews.
    path("search/unified/", UnifiedSearchView.as_view(), name="search-unified"),
    path("search/trending/", SearchTrendingView.as_view(), name="search-trending"),
    # Task 12 — merged campus + tuition-class notice feed for the home
    # screen. Plain APIView, same reasoning as the two paths above.
    path("notice-board/", NoticeBoardView.as_view(), name="notice-board"),
    # Task G18 — post-signup onboarding flow (pick interests -> suggested
    # follows/campuses -> sample test). Plain APIViews, same reasoning as
    # the three paths above.
    # T1.8 — PUBLIC force-update / maintenance config (no auth by design).
    path("app-config/", AppConfigView.as_view(), name="app-config"),
    path("onboarding/suggestions/", OnboardingSuggestionsView.as_view(), name="onboarding-suggestions"),
    path("onboarding/complete/", OnboardingCompleteView.as_view(), name="onboarding-complete"),
    # 30-second first-run step: class + exam + up to 3 interests in one call.
    path("onboarding/options/", OnboardingOptionsView.as_view(), name="onboarding-options"),
    path("onboarding/quick-start/", OnboardingQuickStartView.as_view(), name="onboarding-quick-start"),
    path("daily-challenge/", DailyChallengeView.as_view(), name="daily-challenge"),
    # Who am I for the app's UI rules (minor -> no ads, staff -> Review/Remove), + the minor safe-defaults fix.
    path("me/context/", MeContextView.as_view(), name="me-context"),
    path("me/safe-defaults/", ApplySafeDefaultsView.as_view(), name="me-safe-defaults"),
    # S12 - which role sections Settings may show (teacher / parent / staff / campus admin). UI hint only.
    path("me/capabilities/", MeCapabilitiesView.as_view(), name="me-capabilities"),
    # Guest browsing (PUBLIC, no token) + starter content for an empty Home (signed in).
    path("guest/options/", GuestOptionsView.as_view(), name="guest-options"),
    path("guest/suggested-accounts/", GuestSuggestedAccountsView.as_view(), name="guest-suggested-accounts"),
    path("home/starter/", HomeStarterView.as_view(), name="home-starter"),
    # H4 - Home open: ONE call instead of ~11 loaders (the feed keeps its own call).
    path("home-bootstrap/", HomeBootstrapView.as_view(), name="home-bootstrap"),
    # H14 - resume PDF page, pending homework, next test, streak (each section fails independently)
    path("continue-learning/", ContinueLearningView.as_view(), name="continue-learning"),
    path("habit/today/", HabitTodayView.as_view(), name="habit-today"),
    # T3.8 -- growth funnel: client install/app_open pings + staff-only report / dashboard
    path("funnel/events/", FunnelEventsView.as_view(), name="funnel-events"),
    path("funnel/first-feed-latency/", FirstFeedLatencyView.as_view(), name="funnel-first-feed-latency"),  # P-A
    path("onboarding/step/", OnboardingStepView.as_view(), name="onboarding-step"),  # P-F1
    path("analytics/funnel/", FunnelReportView.as_view(), name="funnel-report"),
    path("analytics/funnel/dashboard/", funnel_dashboard, name="funnel-dashboard"),
    path("", include(router.urls)),
]