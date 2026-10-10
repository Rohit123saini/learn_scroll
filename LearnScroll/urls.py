"""
URL configuration for LearnScroll project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
# from django.contrib import admin
# from django.urls import path, include
#
# urlpatterns = [
#     path("admin/", admin.site.urls),
#     path("login/", include("login.urls")),
# ]

from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.urls import re_path
from post.views import serve_media_with_range
from common import web_links  # TASK 11 — https share links / QR
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
urlpatterns = [
    path("admin/", admin.site.urls),
    # TASK 11 — verified app links + web fallback for https share links / QR codes.
    path(".well-known/assetlinks.json", web_links.assetlinks),
    path(".well-known/apple-app-site-association", web_links.apple_app_site_association),
    path("apple-app-site-association", web_links.apple_app_site_association),
    path("u/<str:username>/", web_links.profile_landing),
    path("parent-link", web_links.parent_link_landing),
    path("login/", include("login.urls")),
    path("profile/", include("user_profile.urls")),
    path("post/",include("post.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path('media/<path:path>', serve_media_with_range, name='media'),
    path("message/",include("message.urls")),
    path("tuitionclass/", include("tuitionclass.urls")),
    # Task G12 (growth list) — referral program surfaced app-wide, not
    # nested under /tuitionclass/. Same ReferralViewSet, see
    # tuitionclass/referral_urls.py for why this is a second route rather
    # than a move.
    path("referrals/", include("tuitionclass.referral_urls")),
    path("core/", include("core.urls")),
    path("support/", include("support.urls")),  # Help & feedback
    path("copyright/", include("copyrights.urls")),  # Copyright claims / counter-notices / strikes
    path('campus/', include('campus.urls')),
    path('testseries/', include('testseries.urls')),
    path('assigments/', include('assigments.urls')),
    # TASK G7 (growth_and_feature_tasks.md â Leaderboards)
    path('leaderboard/', include('leaderboard.urls')),
]

# if settings.DEBUG:
#     urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# if settings.DEBUG:
#     urlpatterns += [
#         re_path(r'^media/(?P<path>.*)$', serve_media_with_range, name='media'),
#     ]