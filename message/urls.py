#
# # message/urls.py
# from django.urls import include, path
# from rest_framework.routers import DefaultRouter
#
# from .views import (
#     BlockedUserViewSet,
#     CallHistoryViewSet,
#     ConversationViewSet,
#     GroupViewSet,
#     MessageViewSet,
#     UserPresenceView,
#     CallInitiateView,
#     CallActionView,
#     DeviceTokenView
# )
# from .upload_view import MessageUploadAPIView  # 🔥 NAYA
#
# router = DefaultRouter()
# router.register('conversations', ConversationViewSet, basename='conversation')
# router.register('messages', MessageViewSet, basename='message')
# router.register('groups', GroupViewSet, basename='group')
# router.register('blocked-users', BlockedUserViewSet, basename='blocked-user')
# router.register('calls', CallHistoryViewSet, basename='call')
# # router.register('calls', CallHistoryViewSet, basename='call')
#
# urlpatterns = [
#     path('', include(router.urls)),
#     path('users/<uuid:user_id>/presence/', UserPresenceView.as_view(), name='user-presence'),
#     path('upload/', MessageUploadAPIView.as_view(), name='message-upload'),  # 🔥 NAYA
#     path('calls/initiate/', CallInitiateView.as_view(), name='call-initiate'),
#     path('calls/<uuid:call_id>/action/', CallActionView.as_view(), name='call-action'),
# ]
# urlpatterns += [
#     path('devices/register/', DeviceTokenView.as_view(), name='device-register'),
# ]
#
# # ======================================================================
# # ENDPOINT SUMMARY (paths shown relative to wherever this urls.py is
# # mounted in your project's root urls.py, e.g. path('', include('message.urls')))
# # ======================================================================
# # Conversations
# #   GET    /conversations/                      chat list (private + group)
# #   GET    /conversations/<id>/                  conversation detail
# #   POST   /conversations/start_private/         {"user_id"} -> start/open 1-1 chat
# #   PATCH  /conversations/<id>/settings/          {"is_muted"/"is_archived"/"is_pinned"}
# #   GET    /conversations/<id>/messages/          paginated message history
# #   POST   /conversations/<id>/messages/          send message (media + REST fallback; now broadcasts realtime)
# #   POST   /conversations/<id>/read_all/          mark all unread as read
# #
# # Messages
# #   GET    /messages/<id>/                        message detail
# #   PATCH  /messages/<id>/                         {"text"} edit (sender only)
# #   DELETE /messages/<id>/?for_everyone=true|false delete for me / for everyone
# #   POST   /messages/<id>/react/                   {"emoji"} add/update reaction
# #   DELETE /messages/<id>/react/                   remove my reaction
# #   POST   /messages/<id>/read/                    mark read receipt
# #
# # Groups
# #   POST   /groups/                                {"name","member_ids",...} create group
# #   GET    /groups/<id>/                            group detail + members
# #   PATCH  /groups/<id>/                             update group info (admin/mod)
# #   POST   /groups/<id>/members/                     {"user_ids"} add members (admin/mod)
# #   PATCH  /groups/<id>/members/<user_id>/            update role/mute/ban (admin/mod)
# #   DELETE /groups/<id>/members/<user_id>/            remove member / leave group
# #   GET    /groups/<id>/media/                        group gallery
# #
# # Blocked users
# #   GET    /blocked-users/                         who I've blocked
# #   POST   /blocked-users/                          {"blocked": "<user_id>"}
# #   DELETE /blocked-users/<id>/                      unblock
# #
# # Presence
# #   GET    /users/<user_id>/presence/               online/last-seen
# #
# # Calls
# #   GET    /calls/                                  my call history
# #   GET    /calls/<id>/                              call detail
# #
# # Chat media upload (NAYA)
# #   POST   /upload/                                 multipart "file" -> {file_url, file_type, ...}















# message/urls.py
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from.views_ai import AiStudyRoomView
from .views import (
    BlockedUserViewSet,
    CallHistoryViewSet,
    ConversationViewSet,
    GroupViewSet,
    MessageViewSet,
    UserPresenceView,
    CallInitiateView,
    CallActionView,
    DeviceTokenView,
    StudyRoomJoinView,
    StudyRoomStateView,
)
from .upload_view import MessageUploadAPIView

# 1. Custom Explicit Views (Router se PEHLE declare karein taaki route conflict na ho)
urlpatterns = [
    path('calls/initiate/', CallInitiateView.as_view(), name='call-initiate'),
    path('calls/<uuid:call_id>/action/', CallActionView.as_view(), name='call-action'),
    path('users/<uuid:user_id>/presence/', UserPresenceView.as_view(), name='user-presence'),
    path('upload/', MessageUploadAPIView.as_view(), name='message-upload'),
    path('devices/register/', DeviceTokenView.as_view(), name='device-register'),

    # 🔥 NAYA — study room: koi ringing/accept-reject nahi, seedha
    # persistent LiveKit room join karta hai (FormatException fix — pehle
    # ye route maujood hi nahi tha, isliye HTML 404 page wapas aata tha).
    path('study-room/<uuid:conversation_id>/join/', StudyRoomJoinView.as_view(), name='study-room-join'),
    path(
        'conversations/<uuid:conversation_id>/study-room-state/',
        StudyRoomStateView.as_view(),
        name='study-room-state',
    ),

    #_________________________   ai _____________________________
    path('calls/initiate/', CallInitiateView.as_view(), name='call-initiate'),
    path('calls/<uuid:call_id>/action/', CallActionView.as_view(), name='call-action'),
    path('users/<uuid:user_id>/presence/', UserPresenceView.as_view(), name='user-presence'),
    path('upload/', MessageUploadAPIView.as_view(), name='message-upload'),
    path('devices/register/', DeviceTokenView.as_view(), name='device-register'),
    path('study-room/ai-tools/', AiStudyRoomView.as_view(), name='study-room-ai-tools'),
# path('study-room/ai-tools/', AiStudyRoomView.as_view(), name='study-room-ai-tools'),
]

# 2. ViewSets via Router
router = DefaultRouter()
router.register('conversations', ConversationViewSet, basename='conversation')
router.register('messages', MessageViewSet, basename='message')
router.register('groups', GroupViewSet, basename='group')
router.register('blocked-users', BlockedUserViewSet, basename='blocked-user')
router.register('calls', CallHistoryViewSet, basename='call')

# 3. Router URLs include karein
urlpatterns += [
    path('', include(router.urls)),
]