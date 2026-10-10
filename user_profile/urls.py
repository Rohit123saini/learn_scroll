# user_profile/urls.py
from django.urls import path

from .views import (
    AcceptFollowRequestView,
    ActivityHeartbeatView,
    ActivityView,
    AdminCoinPurchaseConfirmView,
    BlockedUsersView,
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
    ProfileView,
    RejectFollowRequestView,
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
    UserPreferenceView,
    UserProfileDetailView,
    UserSearchView,
    WeeklyRecapCardAPIView,
    WeeklyRecapView,
)

urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("chat-search/", MessageContactSearchView.as_view(), name="message-contact-search"),
    path("profile/<str:username>/", UserProfileDetailView.as_view(), name="user-profile-detail"),
    path("profile/<str:username>/followers/", FollowersListView.as_view(), name="user-followers"),
    path("profile/<str:username>/following/", FollowingListView.as_view(), name="user-following"),
    # P8-BE — "Followed by X, Y + 5 others" and "Suggested for you" on a profile.
    path("profile/<str:username>/mutuals/", MutualFollowersView.as_view(), name="user-mutuals"),
    path("profile/<str:username>/similar/", SimilarUsersView.as_view(), name="user-similar"),
    path("follow/<int:user_id>/", FollowAPIView.as_view(), name="follow-user"),
    path("accept-request/<int:follow_id>/", AcceptFollowRequestView.as_view(), name="accept-request"),
    path("reject-request/<int:follow_id>/", RejectFollowRequestView.as_view(), name="reject-request"),
    # P11-BE — pending incoming requests (paginated, rows carry follow_id for
    # accept-/reject-request above) + silent "remove follower".
    path("follow-requests/", FollowRequestsListView.as_view(), name="follow-requests"),
    path("followers/<int:user_id>/", RemoveFollowerView.as_view(), name="remove-follower"),
    path("update/", UpdateProfileView.as_view(), name="update-profile"),
    path("blocked-users/", BlockedUsersView.as_view(), name="blocked-users"),
    # 🔥 FIX: was `<str:id>` even though `UnblockUserView` only ever
    # compares it against integer PKs (`Q(pk=id) | Q(blocked_id=id)`).
    # `<int:id>` makes Django itself 404 on non-numeric input instead of
    # letting a bad value fall through to the ORM.
    path("blocked-users/<int:id>/", UnblockUserView.as_view(), name="unblock-user"),
    # TASK 18 — same URL shape as blocked-users/ above, for RestrictUser.
    path("reports/", ContentReportView.as_view(), name="content-report"),
    path("restricted-users/", RestrictedUsersView.as_view(), name="restricted-users"),
    path("restricted-users/<int:id>/", UnrestrictUserView.as_view(), name="unrestrict-user"),
    # TASK 19 — read-only coin transaction history.
    path("coin-ledger/", CoinLedgerListView.as_view(), name="coin-ledger"),
    # TASK 3 — buy-coin flow: start a purchase, then confirm it
    # (success/failed). buy-coin/confirm/ is a signature-verified
    # gateway webhook (§11 item 10) — see BuyCoinConfirmView's own
    # docstring, and buy-coin/admin-confirm/ below for the separate
    # staff-only path manual/admin-initiated (gateway-less) top-ups use.
    path("buy-coin/", BuyCoinView.as_view(), name="buy-coin"),
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