"""
user_profile/badge_views.py

P13-BE — GET /profile/<username>/badges/ (wire it in user_profile/urls.py,
see the URL line at the bottom). Read-only: badges are only ever written by
services.award_badge() (signals / celery), never from a request.
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BlockUser, UserBadge


class UserBadgeSerializer(serializers.ModelSerializer):
    code = serializers.CharField(source="badge.code", read_only=True)
    title = serializers.CharField(source="badge.title", read_only=True)
    description = serializers.CharField(source="badge.description", read_only=True)
    icon = serializers.CharField(source="badge.icon", read_only=True)
    rule_type = serializers.CharField(source="badge.rule_type", read_only=True)

    class Meta:
        model = UserBadge
        fields = ["code", "title", "description", "icon", "rule_type", "earned_at", "context"]
        read_only_fields = fields


class UserBadgesView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, username):
        target = get_object_or_404(get_user_model(), username=username)

        # A block in either direction looks exactly like "no such user" —
        # same no-leak rule the rest of this app follows for blocks.
        if target.pk != request.user.pk and BlockUser.objects.filter(
            Q(blocker=request.user, blocked=target) | Q(blocker=target, blocked=request.user)
        ).exists():
            raise Http404

        badges = (
            UserBadge.objects.filter(user=target, badge__is_active=True)
            .select_related("badge")
            .order_by("-earned_at")
        )
        return Response({
            "username": target.username,
            "count": len(badges),
            "badges": UserBadgeSerializer(badges, many=True).data,
        })


# user_profile/urls.py:
#   path("profile/<str:username>/badges/", UserBadgesView.as_view(), name="user-badges"),
