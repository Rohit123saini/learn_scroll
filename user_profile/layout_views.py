"""user_profile/layout_views.py - P5. NOT RUN.   GET /profile/profile/<username>/layout/

Blocked either way -> 404 (same as "no such user"). Private + not following -> 200 `locked: true` (see profile_layout)."""
from django.contrib.auth import get_user_model
from rest_framework import permissions
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from . import profile_layout
from .views import is_blocked_between


class ProfileLayoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_layout"

    def get(self, request, username):
        owner = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            raise NotFound("No such user.")
        if owner.pk != request.user.pk and is_blocked_between(request.user, owner):
            raise NotFound("No such user.")
        return Response(profile_layout.build(owner, request.user))
