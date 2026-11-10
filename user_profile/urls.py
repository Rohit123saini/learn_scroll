# user_profile/urls.py
from django.urls import path

from . import trust_views
from .achievement_views import AchievementsView  # P3
from .layout_views import ProfileLayoutView  # P5
from .profile_insights_views import ProfileInsightsView  # P9
from .featured_views import FeaturedCandidatesView, MyFeaturedView, ProfileFeaturedView  # P6
from .badge_views import UserBadgesView  # P13 (was never wired) / P3
from .wallet_views import WalletHistoryView  # T3.10
from .role_settings_views import (  # S12
    ParentChildLimitView,
    ParentChildPrivacyView,
    ParentChildrenView,
    TeacherLiveDefaultsView,
)
from . import receipts_views  # P-G2
from .wallet_settings_views import (  # S9
    WalletPaymentsView,
    WalletPurchaseReceiptView,
    WalletSettingsView,
    WalletSummaryView,
    WalletWithdrawalReceiptView,
)

from .views import (
    AcceptFollowRequestView,
    ActivityHeartbeatView,
    ActivityView,
    AdminCoinPurchaseConfirmView,
    BlockedUsersView,
    BulkFollowRequestsView,  # P8
    BuyCoinConfirmView,
    BuyCoinView,
    CoinLedgerListView,
    CoinWithdrawalAdminActionView,
    CoinWithdrawalAdminListView,
    CoinWithdrawalRequestView,
    FollowAPIView,
    FollowRequestsListView,
    FollowersListView,
    FollowingListView,
    MessageContactSearchView,
    MutualFollowersView,  # P8-BE
    PrivacySettingsView,  # S4
    ProfileView,
    RejectFollowRequestView,
    RelationshipSummaryView,  # P8
    RemoveFollowerView,
    ContentReportView,
    RestrictedUsersView,
    SimilarUsersView,  # P8-BE
    StreakView,
    StreakFreezeBuyView,
    DailyGoalView,
    UnblockUserView,
    UnrestrictUserView,
    UpdateProfileView,
    UsernameCheckView,  # P1
    UserPreferenceView,
    LearningPreferencesView,
    UserProfileDetailView,
    UserSearchView,
    WeeklyRecapCardAPIView,
    WeeklyRecapView,
)

from .contact_discovery import ContactMatchView  # S4b
from .payments import CreateCoinOrderView, RazorpayWebhookView  # T0.4
urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("chat-search/", MessageContactSearchView.as_view(), name="message-contact-search"),
    path("privacy/", PrivacySettingsView.as_view(), name="privacy-settings"),  # S4
    path("contacts/match/", ContactMatchView.as_view(), name="contact-match"),  # S4b find-by-phone / e-mail
    path("profile/<str:username>/", UserProfileDetailView.as_view(), name="user-profile-detail"),
    path("profile/<str:username>/followers/", FollowersListView.as_view(), name="user-followers"),
    path("profile/<str:username>/following/", FollowingListView.as_view(), name="user-following"),
    # P8-BE — "Followed by X, Y + 5 others" and "Suggested for you" on a profile.
    path("profile/<str:username>/mutuals/", MutualFollowersView.as_view(), name="user-mutuals"),
    path("profile/<str:username>/similar/", SimilarUsersView.as_view(), name="user-similar"),
    path("profile/<str:username>/badges/", UserBadgesView.as_view(), name="user-badges"),  # P13 / P3
    path("profile/<str:username>/achievements/", AchievementsView.as_view(), name="user-achievements"),  # P3
    path("profile/<str:username>/layout/", ProfileLayoutView.as_view(), name="user-profile-layout"),  # P5
    path("profile/<str:username>/featured/", ProfileFeaturedView.as_view(), name="user-profile-featured"),  # P6
    path("featured/", MyFeaturedView.as_view(), name="my-featured"),  # P6
    path("featured/candidates/", FeaturedCandidatesView.as_view(), name="my-featured-candidates"),  # P6
    path("follow/<int:user_id>/", FollowAPIView.as_view(), name="follow-user"),
    path("accept-request/<int:follow_id>/", AcceptFollowRequestView.as_view(), name="accept-request"),
    path("reject-request/<int:follow_id>/", RejectFollowRequestView.as_view(), name="reject-request"),
    # P11-BE — pending incoming requests (paginated, rows carry follow_id for
    # accept-/reject-request above) + silent "remove follower".
    path("follow-requests/", FollowRequestsListView.as_view(), name="follow-requests"),
    path("followers/<int:user_id>/", RemoveFollowerView.as_view(), name="remove-follower"),
    # P8 - bulk accept / decline of pending requests + the counts the Relationships hub shows.
    path("follow-requests/bulk/", BulkFollowRequestsView.as_view(), name="follow-requests-bulk"),
    path("relationships/summary/", RelationshipSummaryView.as_view(), name="relationships-summary"),
    # P9 - my own profile insights (visits, follower growth, reach, top posts).
    path("insights/", ProfileInsightsView.as_view(), name="profile-insights"),
    path("update/", UpdateProfileView.as_view(), name="update-profile"),
    path("username/check/", UsernameCheckView.as_view(), name="username-check"),  # P1
    path("blocked-users/", BlockedUsersView.as_view(), name="blocked-users"),
    # 🔥 FIX: was `<str:id>` even though `UnblockUserView` only ever
    # compares it against integer PKs (`Q(pk=id) | Q(blocked_id=id)`).
    # `<int:id>` makes Django itself 404 on non-numeric input instead of
    # letting a bad value fall through to the ORM.
    path("blocked-users/<int:id>/", UnblockUserView.as_view(), name="unblock-user"),
    # TASK 18 — same URL shape as blocked-users/ above, for RestrictUser.
    path("reports/", ContentReportView.as_view(), name="content-report"),
    # Trust & safety: notices + appeals for held/hidden content, child-safety help
    path("notices/", trust_views.NoticeListView.as_view(), name="moderation-notices"),
    path("appeals/", trust_views.AppealView.as_view(), name="moderation-appeals"),
    # P-F15: staff report queue sorted by priority + review deadline (SLA)
    path("staff/report-queue/", trust_views.StaffReportQueueView.as_view(), name="staff-report-queue"),
    path("staff/report-queue/summary/", trust_views.StaffReportQueueView.as_view(),
         {"summary": True}, name="staff-report-queue-summary"),
    path("child-safety/help/", trust_views.ChildSafetyHelpView.as_view(), name="child-safety-help"),
    path("grievance-officer/", trust_views.GrievanceOfficerView.as_view(), name="grievance-officer"),
    path("restricted-users/", RestrictedUsersView.as_view(), name="restricted-users"),
    path("restricted-users/<int:id>/", UnrestrictUserView.as_view(), name="unrestrict-user"),
    # TASK 19 — read-only coin transaction history.
    path("coin-ledger/", CoinLedgerListView.as_view(), name="coin-ledger"),
    # T3.10 — one history over CoinLedger + the legacy tuitionclass ledger
    path("wallet/history/", WalletHistoryView.as_view(), name="wallet-history"),
    # S9 - wallet / payments settings (limits, confirm toggle, summary, payment history, PDF receipts)
    path("wallet/settings/", WalletSettingsView.as_view(), name="wallet-settings"),
    path("wallet/summary/", WalletSummaryView.as_view(), name="wallet-summary"),
    path("wallet/payments/", WalletPaymentsView.as_view(), name="wallet-payments"),
    path("wallet/payments/<int:pk>/receipt/", WalletPurchaseReceiptView.as_view(), name="wallet-payment-receipt"),
    path("wallet/withdrawals/<int:pk>/receipt/", WalletWithdrawalReceiptView.as_view(), name="wallet-withdrawal-receipt"),
    # P-G2 - one receipts list (coins / pass / test series / campus fee) + PDFs for the coin-spend kinds
    path("wallet/receipts/", receipts_views.WalletReceiptsView.as_view(), name="wallet-receipts"),
    path("wallet/receipts/pass/<int:pk>/pdf/", receipts_views.WalletPassReceiptView.as_view(), name="wallet-pass-receipt"),
    path("wallet/receipts/testseries/<uuid:pk>/pdf/", receipts_views.WalletTestSeriesReceiptView.as_view(),
         name="wallet-testseries-receipt"),
    # TASK 3 — buy-coin flow: start a purchase, then confirm it
    # (success/failed). buy-coin/confirm/ is a signature-verified
    # gateway webhook (§11 item 10) — see BuyCoinConfirmView's own
    # docstring, and buy-coin/admin-confirm/ below for the separate
    # staff-only path manual/admin-initiated (gateway-less) top-ups use.
    path("buy-coin/", BuyCoinView.as_view(), name="buy-coin"),
    # T0.4 — server-priced Razorpay order + real-format Razorpay webhook (user_profile/payments.py)
    path("buy-coin/order/", CreateCoinOrderView.as_view(), name="buy-coin-order"),
    path("buy-coin/webhook/razorpay/", RazorpayWebhookView.as_view(), name="buy-coin-webhook-razorpay"),
    path("buy-coin/confirm/", BuyCoinConfirmView.as_view(), name="buy-coin-confirm"),
    # Same webhook, but the gateway is named in the URL — configure THIS one
    # in each gateway's dashboard. It is what disambiguates a reference
    # string that two gateways happen to share (see BuyCoinConfirmView).
    path("buy-coin/confirm/<slug:gateway>/", BuyCoinConfirmView.as_view(), name="buy-coin-confirm-gateway"),
    # TASK 16 — staff-only replacement confirm path for manual/admin-
    # initiated top-ups (blank-`gateway` CoinPurchaseRequests), which
    # buy-coin/confirm/ above can no longer serve now that it's a
    # signature-verified gateway webhook only. See AdminCoinPurchaseConfirmView's
    # own docstring (views.py) for why this is a separate endpoint
    # rather than a permission branch on that one.
    path("buy-coin/admin-confirm/", AdminCoinPurchaseConfirmView.as_view(), name="buy-coin-admin-confirm"),
    # TASK 4 — withdraw-coin flow: request a withdrawal (debits
    # immediately) / list your own withdrawal requests. Same
    # /profile/... URL-shape consistency RestrictUser's docstring
    # (models.py) calls out for restricted-users/ vs blocked-users/.
    path("coin-withdrawals/", CoinWithdrawalRequestView.as_view(), name="coin-withdrawal-requests"),
    # TASK 1 (feature: Admin Coin-Withdrawal Review Dashboard) — staff-only
    # "every user's requests" queue. Placed before the <int:withdrawal_id>/
    # action/ route below on purpose isn't actually required (this is a
    # literal "admin/" segment, not an int, so Django's URL resolver can't
    # confuse the two either way) but keeps the three coin-withdrawals/
    # routes grouped in read -> list -> act order for anyone scanning this
    # file.
    path("coin-withdrawals/admin/", CoinWithdrawalAdminListView.as_view(), name="coin-withdrawal-admin-list"),
    # §11 item 12 — staff-only lifecycle actions (processing/success/
    # reject) on someone else's withdrawal request. Deliberately a
    # separate path/view from coin-withdrawals/ above rather than a
    # PATCH on the same route: that route is scoped to "my own
    # requests" (filters by request.user); this one acts on any
    # user's request and needs a different permission class entirely.
    path(
        "coin-withdrawals/<int:withdrawal_id>/action/",
        CoinWithdrawalAdminActionView.as_view(),
        name="coin-withdrawal-admin-action",
    ),
    # TASK 1 — theme/language preferences, same URL shape as core's
    # notification-preferences/me/.
    path("preferences/me/", UserPreferenceView.as_view(), name="user-preferences"),
    path("learning/", LearningPreferencesView.as_view(), name="learning-preferences"),
    # S12 - role-aware settings: teacher defaults for new classrooms; parent -> linked child daily limit.
    path("teacher/live-defaults/", TeacherLiveDefaultsView.as_view(), name="teacher-live-defaults"),
    path("parent/children/", ParentChildrenView.as_view(), name="parent-children"),
    path("parent/children/<int:child_id>/limit/", ParentChildLimitView.as_view(), name="parent-child-limit"),
    path("parent/children/<int:child_id>/privacy/", ParentChildPrivacyView.as_view(), name="parent-child-privacy"),  # P-F16
    # TASK G1 (growth_and_feature_tasks.md — Streaks) — GET current streak /
    # POST today's check-in. Same /profile/... URL-shape consistency this
    # file's other comments already call out for restricted-users/ vs
    # blocked-users/, coin-withdrawals/ vs buy-coin/, etc.
    path("streak/", StreakView.as_view(), name="streak"),
    path("streak/freeze/", StreakFreezeBuyView.as_view(), name="streak-freeze-buy"),
    path("daily-goal/", DailyGoalView.as_view(), name="daily-goal"),
    # P14-BE — "Your activity": GET last-7-days time spent + likes/comments/
    # shares + saved/liked posts, PATCH the daily-limit reminder; the
    # foreground heartbeat feeds DailyUsage.
    path("activity/", ActivityView.as_view(), name="activity"),
    path("activity/heartbeat/", ActivityHeartbeatView.as_view(), name="activity-heartbeat"),
    # TASK G2 (growth_and_feature_tasks.md — recap screen) — GET the
    # latest "Your Week" recap, and a PNG share-card render of a specific
    # one. Same /profile/... URL-shape consistency streak/ above follows.
    path("recap/latest/", WeeklyRecapView.as_view(), name="weekly-recap-latest"),
    path(
        "recap/<int:recap_id>/card/",
        WeeklyRecapCardAPIView.as_view(),
        name="weekly-recap-card",
    ),
]