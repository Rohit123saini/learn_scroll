from django.urls import path,re_path
from .views import *
from django.conf import settings
from .comment_view import *
from django.conf.urls.static import static
urlpatterns = [
    path('create/', PostCreateAPIView.as_view(), name='post-create'),
    path('list/', PostListAPIView.as_view(), name='post-list'), # GET list
    path('details/<uuid:id>/', PostDetailAPIView.as_view(), name='post-detail'),
    path('feed/', HomeFeedView.as_view(), name='home-feed'),
    path('like/<uuid:post_id>/reaction/', PostReactionAPIView.as_view(), name='post-reaction'),
    #comment
    path('comment/create/', CommentCreateAPIView.as_view(), name='comment-create'),  # POST multipart
    path('comment/<uuid:comment_id>/edit/', CommentUpdateAPIView.as_view(), name='comment-edit'),  # PATCH multipart
    path('comment/<uuid:comment_id>/delete/', CommentDeleteAPIView.as_view(), name='comment-delete'),  # DELETE
    path('comment/post/<uuid:post_id>/', CommentListAPIView.as_view(), name='comment-list'),
    path('comment/<uuid:comment_id>/hide/', CommentHideAPIView.as_view(), name='comment-hide'),
    path('comment/<uuid:comment_id>/replies/', CommentRepliesAPIView.as_view(), name='comment-replies'),
]
if settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve_media_with_range, name='serve-media'),
    ]