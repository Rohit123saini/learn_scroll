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
from copyrights.views import policy_page as copyright_policy_page, transparency_page as copyright_transparency_page
#
# urlpatterns = [
#     path(settings.ADMIN_URL, admin.site.urls),  # P-B6: env ADMIN_URL (default "admin/")
#     path("login/", include("login.urls")),
# ]

from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.urls import re_path
from post.views import serve_media_with_range
from common import web_links  # TASK 11 — https share links / QR
from core import ops_health  # T3.7 — liveness / readiness probes
from common import seo_pages  # TASK T2.6 — public SEO pages / sitemap / robots
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from common.private_media_views import serve_private_media  # T0.3
from common import legal_pages  # T0.8
urlpatterns = [
    # T3.7 — uptime monitors / load balancer (unauthenticated, no secrets in the body)
    path("healthz/", ops_health.healthz, name="healthz"),
    path("healthz/deep/", ops_health.healthz_deep, name="healthz-deep"),
    path("readyz/", ops_health.readyz, name="readyz"),
    path(settings.ADMIN_URL, admin.site.urls),  # P-B6: env ADMIN_URL (default "admin/")
    # TASK 11 — verified app links + web fallback for https share links / QR codes.
    path(".well-known/assetlinks.json", web_links.assetlinks),
    path(".well-known/apple-app-site-association", web_links.apple_app_site_association),
    path("apple-app-site-association", web_links.apple_app_site_association),
    path("u/<str:username>/", web_links.profile_landing),
    path("p/<uuid:post_id>/", web_links.post_landing),  # feed post "Copy link"
    path("p/<uuid:post_id>", web_links.post_landing),
    path("parent-link", web_links.parent_link_landing),
    path("qr-login", web_links.qr_login_landing),  # S10 -- QR sign-in landing
    # tuition-class / test-series share links (commission links) - see common/web_links.py
    path("classroom/<str:class_id>", web_links.classroom_landing),
    path("classroom/<str:class_id>/", web_links.classroom_landing),
    path("test-series/<str:slug>/", web_links.test_series_landing),
    path("test-series/<str:slug>", web_links.test_series_landing),
    # TASK T2.6 — public, indexable pages (see common/seo_pages.py)
    path("certificate/<str:code>/", seo_pages.certificate_page, name="certificate-verify-page"),  # P3 - public verify link
    path("assignments/<str:slug>/", seo_pages.assignment_page),
    path("doubts/<str:slug>/", seo_pages.doubt_page),
    path("sitemap.xml", seo_pages.sitemap_xml),
    path("robots.txt", seo_pages.robots_txt),
    path("login/", include("login.urls")),
    path("profile/", include("user_profile.urls")),
    path("post/",include("post.urls")),
    # P-B1: these three (and tuitionclass/schema/, tuitionclass/schema/docs/) are STAFF-ONLY unless API_DOCS_PUBLIC=true -
    # the lock is SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"] in settings.py, so every docs view shares one rule.
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("swagger/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path('media/<path:path>', serve_media_with_range, name='media'),
    path('pmedia/<path:path>', serve_private_media, name='private-media'),  # T0.3 signed-URL private files
    path("message/",include("message.urls")),
    path("tuitionclass/", include("tuitionclass.urls")),
    # Task G12 (growth list) — referral program surfaced app-wide, not
    # nested under /tuitionclass/. Same ReferralViewSet, see
    # tuitionclass/referral_urls.py for why this is a second route rather
    # than a move.
    path("referrals/", include("tuitionclass.referral_urls")),
    path("core/", include("core.urls")),
    path("support/", include("support.urls")),  # Help & feedback
    path("ai-quality/", include("aiquality.urls")),  # P-F6: AI feedback + staff quality / cost screens
    path("copyright/", include("copyrights.urls")),  # Copyright claims / counter-notices / strikes
    path("legal/privacy/", legal_pages.privacy_policy, name="legal-privacy"),  # T0.8
    path("legal/terms/", legal_pages.terms_of_service, name="legal-terms"),  # T0.8
    path("legal/refund-policy/", legal_pages.refund_policy, name="legal-refund-policy"),  # P-G2
    path("legal/delete-account/", legal_pages.delete_account_page, name="legal-delete-account"),  # T0.8
    path("legal/copyright-policy/", copyright_policy_page),  # public page for store listing / Terms / Privacy
    path("legal/transparency-report/", copyright_transparency_page),  # public: claims / removals / restored
    path('campus/', include('campus.urls')),
    path('testseries/', include('testseries.urls')),
    path('assigments/', include('assignments.urls')),
    # TASK G7 (growth_and_feature_tasks.md â Leaderboards)
    path('leaderboard/', include('leaderboard.urls')),
    # P-F5 - study-with-me Pomodoro, accountability partner, leaderboard seasons
    path('social/', include('social_learning.urls')),
    # P-F8 - spaced repetition (SM-2) over the Revision deck
    path('revision/', include('spaced_repetition.urls')),
    # T3.4 (D8) — unified Doubts hub (list/ask/answer across all five doubt systems)
    path('doubt-hub/', include('doubts.urls')),  # NOT 'doubts/': that prefix is the public SEO Q&A page (doubts/<slug>/, T2.6)
]

# if settings.DEBUG:
#     urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
# if settings.DEBUG:
#     urlpatterns += [
#         re_path(r'^media/(?P<path>.*)$', serve_media_with_range, name='media'),
#     ]