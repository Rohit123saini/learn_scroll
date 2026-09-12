# user_profile/urls.py
from django.urls import path

from .views import (
    AcceptFollowRequestView,
    BlockedUsersView,
    BuyCoinConfirmView,
    BuyCoinView,
    CoinLedgerListView,
    CoinWithdrawalRequestView,
    FollowAPIView,
    FollowersListView,
    FollowingListView,
    MessageContactSearchView,
    ProfileView,
    RejectFollowRequestView,
    RestrictedUsersView,
    UnblockUserView,
    UnrestrictUserView,
    UpdateProfileView,
    UserProfileDetailView,
    UserSearchView,
)

urlpatterns = [
    path("", ProfileView.as_view(), name="profile"),
    path("search/", UserSearchView.as_view(), name="user-search"),
    path("chat-search/", MessageContactSearchView.as_view(), name="message-contact-search"),
    path("profile/<str:username>/", UserProfileDetailView.as_view(), name="user-profile-detail"),
    path("profile/<str:username>/followers/", FollowersListView.as_view(), name="user-followers"),
    path("profile/<str:username>/following/", FollowingListView.as_view(), name="user-following"),
    path("follow/<int:user_id>/", FollowAPIView.as_view(), name="follow-user"),
    path("accept-request/<int:follow_id>/", AcceptFollowRequestView.as_view(), name="accept-request"),
    path("reject-request/<int:follow_id>/", RejectFollowRequestView.as_view(), name="reject-request"),
    path("update/", UpdateProfileView.as_view(), name="update-profile"),
    path("blocked-users/", BlockedUsersView.as_view(), name="blocked-users"),
    # 🔥 FIX: was `<str:id>` even though `UnblockUserView` only ever
    # compares it against integer PKs (`Q(pk=id) | Q(blocked_id=id)`).
    # `<int:id>` makes Django itself 404 on non-numeric input instead of
    # letting a bad value fall through to the ORM.
    path("blocked-users/<int:id>/", UnblockUserView.as_view(), name="unblock-user"),
    # TASK 18 — same URL shape as blocked-users/ above, for RestrictUser.
    path("restricted-users/", RestrictedUsersView.as_view(), name="restricted-users"),
    path("restricted-users/<int:id>/", UnrestrictUserView.as_view(), name="unrestrict-user"),
    # TASK 19 — read-only coin transaction history.
    path("coin-ledger/", CoinLedgerListView.as_view(), name="coin-ledger"),
    # TASK 3 — buy-coin flow: start a purchase, then confirm it
    # (success/failed). See BuyCoinConfirmView's docstring for the
    # caveat that this confirm route stands in for a real payment-
    # gateway webhook and isn't signature-verified yet.
    path("buy-coin/", BuyCoinView.as_view(), name="buy-coin"),
    path("buy-coin/confirm/", BuyCoinConfirmView.as_view(), name="buy-coin-confirm"),
    # TASK 4 — withdraw-coin flow: request a withdrawal (debits
    # immediately) / list your own withdrawal requests. Same
    # /profile/... URL-shape consistency RestrictUser's docstring
    # (models.py) calls out for restricted-users/ vs blocked-users/.
    path("coin-withdrawals/", CoinWithdrawalRequestView.as_view(), name="coin-withdrawal-requests"),
]