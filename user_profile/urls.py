from django.urls import path
from .views import *

urlpatterns = [
    path("",ProfileView.as_view(),name='profile'),
    path('search/', UserSearchView.as_view(), name='user-search'),
    path('chat-search/', MessageContactSearchView.as_view(), name='message-contact-search'),  # 🔥 NAYA
    path('profile/<str:username>/', UserProfileDetailView.as_view(), name='user-profile-detail'),
    path('profile/<str:username>/followers/', FollowersListView.as_view(), name='user-followers'),  # 🔥 NAYA
    path('profile/<str:username>/following/', FollowingListView.as_view(), name='user-following'),  # 🔥 NAYA
    path('follow/<int:user_id>/', FollowAPIView.as_view(), name='follow-user'),
    path('accept-request/<int:follow_id>/', AcceptFollowRequestView.as_view(), name='accept-request'),
    path('reject-request/<int:follow_id>/', RejectFollowRequestView.as_view(), name='reject-request'),
    path('update/', UpdateProfileView.as_view(), name='update-profile'),
]