"""user_profile/featured_views.py - P6 Featured slot endpoints. NOT RUN. Rules + docs: user_profile/featured.py."""
from django.contrib.auth import get_user_model
from rest_framework import permissions, serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from . import featured
from .achievements import viewer_may_see
from .views import is_blocked_between


def _error(exc):
    body = {"detail": exc.message, "code": exc.code}
    if exc.field:
        body[exc.field] = [exc.message]
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


class _FeaturedBase(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [UserRateThrottle, ScopedRateThrottle]
    throttle_scope = "profile_featured"


class ProfileFeaturedView(_FeaturedBase):
    """GET /profile/profile/<username>/featured/"""

    def get(self, request, username):
        owner = get_user_model().objects.filter(username__iexact=username, is_active=True).first()
        if owner is None:
            raise NotFound("No such user.")
        if owner.pk != request.user.pk and is_blocked_between(request.user, owner):
            raise NotFound("No such user.")
        if not viewer_may_see(owner, request.user):
            return Response({"featured": None, "locked": True})
        return Response({"featured": featured.current_card(owner, request), "locked": False})


class FeaturedWriteSerializer(serializers.Serializer):
    kind = serializers.CharField(max_length=10)
    target_id = serializers.CharField(max_length=40)


class MyFeaturedView(_FeaturedBase):
    """PUT / DELETE /profile/featured/  (me)"""

    def put(self, request):
        ser = FeaturedWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            featured.set_featured(request.user, ser.validated_data["kind"], ser.validated_data["target_id"])
        except featured.FeaturedError as exc:
            return _error(exc)
        return Response({"featured": featured.current_card(request.user, request)})

    def delete(self, request):
        featured.clear_featured(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


class FeaturedCandidatesView(_FeaturedBase):
    """GET /profile/featured/candidates/?kind=test|class|resource  (me)"""

    def get(self, request):
        kind = (request.query_params.get("kind") or "").strip()
        try:
            results = featured.candidates(request.user, kind, request)
        except featured.FeaturedError as exc:
            return _error(exc)
        return Response({"kind": kind, "results": results})
