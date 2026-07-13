from django.urls import path,re_path
from .views import *
from django.conf import settings
from django.conf.urls.static import static
urlpatterns = [
    path('create/', PostCreateAPIView.as_view(), name='post-create'),
    path('list/', PostListAPIView.as_view(), name='post-list'), # GET list
    path('details/<uuid:id>/', PostDetailAPIView.as_view(), name='post-detail'),
    path('feed/', HomeFeedView.as_view(), name='home-feed'),
]
if settings.DEBUG:
    urlpatterns += [
        re_path(r'^media/(?P<path>.*)$', serve_media_with_range, name='serve-media'),
    ]