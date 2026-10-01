"""
post/close_friends_views.py

STORIES UPGRADE - PART 1: manage the owner's private Close Friends list.

    GET    /post/close-friends/                 my list (paginated, newest first)
    PUT    /post/close-friends/                 replace the WHOLE list  {"user_ids": [..]}
    POST   /post/close-friends/<user_id>/       add one person   (201 created / 200 already there)
    DELETE /post/close-friends/<user_id>/       remove one person (204, idempotent)
    GET    /post/close-friends/candidates/      people I can add (my accepted followers +
                                                 people I follow), `?q=` search, each row has
                                                 `is_close_friend`

Rules: you cannot add yourself, inactive users, or anyone with a block in
either direction. The list is private and one-way - the other person is not
notified and does not see that they are on it. Who can SEE a close-friends
story is decided in post/story_visibility.py.
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from common.pagination import StandardPagination
from user_profile.models import BlockUser, Follow

from .models import CloseFriend
from .serializers import CloseFriendsReplaceSerializer, CloseFriendUserSerializer

User = get_user_model()


def blocked_user_ids(user):
    """Ids with a block in EITHER direction between them and `user`."""
    blocked_by_me = BlockUser.objects.filter(blocker=user).values_list("blocked_id", flat=True)
    blocked_me = BlockUser.objects.filter(blocked=user).values_list("blocker_id", flat=True)
    return set(blocked_by_me) | set(blocked_me)


class CloseFriendsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="My Close Friends list", tags=["Stories"])
    def get(self, request):
        qs = CloseFriend.objects.filter(owner=request.user).select_related("friend")
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        friends = [row.friend for row in page]
        ctx = {"request": request, "close_friend_ids": {f.id for f in friends}}
        data = CloseFriendUserSerializer(friends, many=True, context=ctx).data
        return paginator.get_paginated_response(data)

    @extend_schema(
        summary="Replace the whole Close Friends list",
        request=CloseFriendsReplaceSerializer,
        tags=["Stories"],
    )
    def put(self, request):
        ser = CloseFriendsReplaceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        wanted = [i for i in ser.validated_data["user_ids"] if i != request.user.id]

        excluded = blocked_user_ids(request.user)
        valid_ids = set(
            User.objects.filter(id__in=wanted, is_active=True)
            .exclude(id__in=excluded)
            .values_list("id", flat=True)
        )
        skipped = [i for i in wanted if i not in valid_ids]

        with transaction.atomic():
            CloseFriend.objects.filter(owner=request.user).exclude(friend_id__in=valid_ids).delete()
            existing = set(
                CloseFriend.objects.filter(owner=request.user).values_list("friend_id", flat=True)
            )
            CloseFriend.objects.bulk_create(
                [CloseFriend(owner=request.user, friend_id=i) for i in valid_ids - existing],
                ignore_conflicts=True,
            )
        count = CloseFriend.objects.filter(owner=request.user).count()
        return Response({"success": True, "count": count, "skipped_user_ids": skipped})


class CloseFriendDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Add one person to my Close Friends", tags=["Stories"])
    def post(self, request, user_id):
        if user_id == request.user.id:
            return Response({"detail": "Khud ko close friend nahi bana sakte."},
                            status=status.HTTP_400_BAD_REQUEST)
        target = User.objects.filter(id=user_id, is_active=True).first()
        if target is None or target.id in blocked_user_ids(request.user):
            return Response({"detail": "User not found."}, status=status.HTTP_404_NOT_FOUND)
        _, created = CloseFriend.objects.get_or_create(owner=request.user, friend=target)
        return Response(
            {"success": True, "is_close_friend": True},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(summary="Remove one person from my Close Friends", tags=["Stories"])
    def delete(self, request, user_id):
        CloseFriend.objects.filter(owner=request.user, friend_id=user_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CloseFriendCandidatesAPIView(APIView):
    """People the owner can pick from: accepted followers + people they
    follow (minus themselves, blocked, inactive), with `is_close_friend`."""
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="People I can add to Close Friends (search with ?q=)",
        parameters=[OpenApiParameter("q", str, required=False)],
        tags=["Stories"],
    )
    def get(self, request):
        me = request.user
        follower_ids = Follow.objects.filter(
            following=me, status=Follow.Status.ACCEPTED
        ).values("follower_id")
        following_ids = Follow.objects.filter(
            follower=me, status=Follow.Status.ACCEPTED
        ).values("following_id")

        qs = (
            User.objects.filter(is_active=True)
            .filter(Q(id__in=follower_ids) | Q(id__in=following_ids))
            .exclude(id=me.id)
            .exclude(id__in=blocked_user_ids(me))
            .order_by("username")
        )
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )

        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        page_ids = [u.id for u in page]
        on_list = set(
            CloseFriend.objects.filter(owner=me, friend_id__in=page_ids).values_list("friend_id", flat=True)
        )
        data = CloseFriendUserSerializer(
            page, many=True, context={"request": request, "close_friend_ids": on_list}
        ).data
        return paginator.get_paginated_response(data)
