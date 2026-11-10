"""
user_profile/achievement_views.py - P3. NOT RUN.

    GET /profile/profile/<username>/achievements/     (the app is mounted at profile/, see urls.py)

Read-only. A blocked person (either direction) gets 404 - identical to "no such user", same no-leak rule as the
badges / certificates endpoints. A private account the viewer does not follow answers `locked: true` with empty
lists (200), so the client can show its "Follow to see" state instead of an error.
"""
from django.contrib.auth import get_user_model
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from . import achievements
from .views import is_blocked_between


class AchievementsView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_achievements"

    def get(self, request, username):
        owner = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            raise NotFound("No such user.")
        if owner.pk != request.user.pk and is_blocked_between(request.user, owner):
            raise NotFound("No such user.")
        return Response(achievements.build(owner, request.user))
