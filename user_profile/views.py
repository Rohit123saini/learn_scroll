# user_profile/views.py
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from . import fraud
from .models import (
    BlockUser,
    CoinLedger,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
    UserPreference,
)
from .serializers import (
    BlockUserSerializer,
    CoinLedgerSerializer,
    CoinPurchaseConfirmSerializer,
    CoinPurchaseRequestSerializer,
    CoinWithdrawalRequestSerializer,
    FollowActionResponseSerializer,
    MessageContactSearchSerializer,
    ProfileUpdateSerializer,
    RestrictedTargetUserProfileSerializer,
    RestrictUserSerializer,
    TargetUserProfileSerializer,
    UserPreferenceSerializer,
    UserProfileDetailResponseSerializer,
    UserProfileSerializer,
    UserSearchSerializer,
    accepted_connection_ids,
    bulk_accepted_connection_ids,
)

User = get_user_model()


def is_blocked_between(user_a, user_b):
    """
    🔥 NEW: true if either user has blocked the other. Nothing in the
    original code checked this — you could follow, message-search-match,
    and view the full profile of someone who blocked you (or whom you'd
    blocked), which defeats the point of blocking.
    """
    return BlockUser.objects.filter(
        Q(blocker=user_a, blocked=user_b) | Q(blocker=user_b, blocked=user_a)
    ).exists()


def is_restricted_between(user, other):
    """
    TASK 18 — true if `user` has restricted `other`. Deliberately ONE-
    WAY (unlike `is_blocked_between`, which is symmetric): restrict
    only affects what *the restricting user* experiences from `other`,
    never the reverse, and `other` must never be able to detect it from
    this check's result — see RestrictUser's docstring in models.py.

    Other apps (posts, message, notifications) should filter through
    this the same way they'd filter through `is_blocked_between` for
    block, once they're ready to apply restrict's actual effects
    (hiding comments from everyone but their author, muting read-
    receipts/online-status, suppressing notifications) — that
    integration is out of scope for user_profile itself.
    """
    return RestrictUser.objects.filter(user=user, restricted=other).exists()


class ProfileView(GenericAPIView):
    """
    Get logged-in user's profile
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        responses={200: UserProfileSerializer},
        description="Get current authenticated user's profile",
    )
    def get(self, request):
        serializer = self.get_serializer(request.user)
        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class UserProfileDetailView(GenericAPIView):
    """
    Get any user's profile by username, with two-way follow status.

    🔥 FIX: this class was defined TWICE in the original file — once as a
    one-way-follow version, and again (further down) as a two-way version
    that also, oddly, declared `parser_classes = [MultiPartParser,
    FormParser]` on a GET-only view (parsers only matter for request
    bodies; harmless but meaningless here, so dropped). Since Python keeps
    the last class body, the first definition was already dead code and
    `urls.py` was always hitting the second one — keeping only that,
    cleaned up, plus the block/privacy checks neither version had.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileDetailResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="username",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description="Username of target user",
            )
        ],
        responses={200: UserProfileDetailResponseSerializer, 404: OpenApiTypes.OBJECT},
        description="Get user profile with two-way follow status. Full profile data is only "
        "returned if the account is public, it's your own profile, or you're an accepted "
        "follower; otherwise a minimal card is returned.",
    )
    def get(self, request, username):
        target_user = get_object_or_404(User, username=username)

        # 🔥 FIX: block wasn't checked anywhere — a blocked/blocking user
        # could still look up the full profile. Mimic "user not found"
        # rather than a 403, so blocking doesn't leak who blocked whom.
        if target_user != request.user and is_blocked_between(request.user, target_user):
            raise Http404

        my_follow_obj = Follow.objects.filter(
            follower=request.user, following=target_user
        ).first()
        their_follow_obj = Follow.objects.filter(
            follower=target_user, following=request.user
        ).first()

        is_self = target_user == request.user
        is_accepted_follower = bool(
            my_follow_obj and my_follow_obj.status == Follow.Status.ACCEPTED
        )

        # 🔥 FIX: `is_private` existed on the model and was even returned
        # in the response body, but nothing ever *enforced* it — anyone
        # authenticated could read a private account's bio/photo/counts
        # just by knowing the username. Now: full data only for the owner,
        # public accounts, or accepted followers; everyone else gets a
        # minimal "this account is private" style payload.
        is_restricted_view = target_user.is_private and not is_self and not is_accepted_follower
        if is_restricted_view:
            profile_data = RestrictedTargetUserProfileSerializer(target_user).data
        else:
            profile_data = TargetUserProfileSerializer(target_user).data

        # TASK 18: whether *I* restrict the target — never the reverse
        # (see is_restricted_between's docstring). False for your own
        # profile since self-restrict is impossible.
        am_i_restricting = (
            not is_self and is_restricted_between(request.user, target_user)
        )

        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "my_id": request.user.id,
                "my_username": request.user.username,
                "target_user_id": target_user.id,
                "target_username": target_user.username,
                "my_follow_status": my_follow_obj.status if my_follow_obj else None,
                "my_follow_id": my_follow_obj.id if my_follow_obj else None,
                "their_follow_status": their_follow_obj.status if their_follow_obj else None,
                "their_follow_id": their_follow_obj.id if their_follow_obj else None,
                "is_restricted_view": is_restricted_view,
                "am_i_restricting": am_i_restricting,
                "data": profile_data,
            },
            status=status.HTTP_200_OK,
        )


class UserSearchView(ListAPIView):
    """
    Search users by username, first_name, last_name
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        # 🔥 FIX: search used to return literally every user, including
        # yourself and anyone in a block relationship with you.
        blocked_ids = BlockUser.objects.filter(
            Q(blocker=self.request.user) | Q(blocked=self.request.user)
        ).values_list("blocker_id", "blocked_id")
        excluded_ids = {self.request.user.id}
        for blocker_id, blocked_id in blocked_ids:
            excluded_ids.add(blocker_id)
            excluded_ids.add(blocked_id)

        return User.objects.filter(is_active=True).exclude(id__in=excluded_ids)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search users",
    )
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Users fetched successfully.",
            "data": response.data,
        })


class MessageContactSearchView(ListAPIView):
    """
    GET /profile/chat-search/?search=<query>

    🔥 Message/group ke "add members" step ke liye — `UserSearchView` se
    ALAG hai: yahan poore app ke users nahi, sirf wahi log aate hain
    jinko maine follow kiya hua hai YA jinhone mujhe follow kiya hua hai
    (dono me se ek bhi kaafi hai, pura mutual hona zaroori nahi — warna
    list bahut chhoti reh jaati). Response me profile_photo/bio waghera
    nahi, sirf id/username/first_name/last_name/mutual_friends.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["username", "first_name", "last_name"]

    def get_queryset(self):
        connected_ids = accepted_connection_ids(self.request.user)
        connected_ids.discard(self.request.user.id)

        # 🔥 FIX: someone you've since blocked (or who blocked you) could
        # still show up here as a "connection" and be pickable as a chat
        # contact.
        blocked_ids = BlockUser.objects.filter(
            Q(blocker=self.request.user) | Q(blocked=self.request.user)
        ).values_list("blocker_id", "blocked_id")
        for blocker_id, blocked_id in blocked_ids:
            connected_ids.discard(blocker_id)
            connected_ids.discard(blocked_id)

        return User.objects.filter(id__in=connected_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search query",
            )
        ],
        description="Search only within users who follow you or whom you follow (for chat/group member picking)",
    )
    def list(self, request, *args, **kwargs):
        # 🔥 FIX (N+1): precompute mutual_friends connections for the whole
        # page in 2 queries instead of 2 queries per row (see
        # bulk_accepted_connection_ids docstring).
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Contacts fetched successfully.",
            "data": data,
        })


class FollowersListView(ListAPIView):
    """
    GET /profile/profile/<username>/followers/

    Target user (URL me diye gaye username) ke saare ACCEPTED followers —
    same `MessageContactSearchSerializer` reuse kiya hai isliye response
    me id/username/first_name/last_name ke saath tumhare (request.user)
    sath unka mutual_friends count bhi milta hai.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer

    def get_queryset(self):
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        follower_ids = Follow.objects.filter(
            following=target_user, status=Follow.Status.ACCEPTED
        ).values_list("follower_id", flat=True)
        return User.objects.filter(id__in=follower_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(description="List of a user's followers, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Followers fetched successfully.",
            "data": data,
        })


class FollowingListView(ListAPIView):
    """
    GET /profile/profile/<username>/following/

    Target user (URL me diye gaye username) jinhe follow karta hai unki
    list — same shape/serializer jaisa `FollowersListView`.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = MessageContactSearchSerializer

    def get_queryset(self):
        target_user = get_object_or_404(User, username=self.kwargs["username"])
        following_ids = Follow.objects.filter(
            follower=target_user, status=Follow.Status.ACCEPTED
        ).values_list("following_id", flat=True)
        return User.objects.filter(id__in=following_ids)

    def get_serializer_context(self):
        return {"request": self.request, "connections_map": self._connections_map}

    @extend_schema(description="List of who a user is following, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        rows = page if page is not None else queryset
        self._connections_map = bulk_accepted_connection_ids(u.id for u in rows)

        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(rows, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Following fetched successfully.",
            "data": data,
        })


class FollowAPIView(GenericAPIView):
    """
    Follow/Unfollow a user with count update
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="user_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of user to follow/unfollow",
            )
        ],
        responses={
            200: FollowActionResponseSerializer,
            201: FollowActionResponseSerializer,
            400: OpenApiTypes.OBJECT,
        },
        description="Follow or unfollow a user",
    )
    @transaction.atomic
    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"error": "You cannot follow yourself"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        following_user = get_object_or_404(User, id=user_id)

        # 🔥 FIX: blocking wasn't checked — you could still send a follow
        # request to (or be followed by) someone in a block relationship.
        if is_blocked_between(request.user, following_user):
            return Response(
                {"error": "You can't follow this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        follow_obj = Follow.objects.filter(
            follower=request.user, following=following_user
        ).first()

        if follow_obj:
            # Unfollow: count minus karo
            if follow_obj.status == Follow.Status.ACCEPTED:
                User.objects.filter(id=request.user.id).update(
                    following_count=F("following_count") - 1
                )
                User.objects.filter(id=following_user.id).update(
                    followers_count=F("followers_count") - 1
                )

            follow_obj.delete()
            return Response({
                "message": "Unfollowed successfully",
                "status": None,
            }, status=status.HTTP_200_OK)

        # Naya follow create karo
        is_private = following_user.is_private
        try:
            # 🔥 FIX: two rapid duplicate requests (double-tap, retry after
            # a slow response, etc) could both pass the `.filter().first()`
            # check above before either commits, then both try to
            # `.create()` — the DB's UniqueConstraint would correctly
            # reject the second one, but as an unhandled IntegrityError
            # that surfaces as a raw 500 instead of a clean response.
            new_follow = Follow.objects.create(
                follower=request.user,
                following=following_user,
                status=Follow.Status.PENDING if is_private else Follow.Status.ACCEPTED,
            )
        except IntegrityError:
            existing = Follow.objects.filter(
                follower=request.user, following=following_user
            ).first()
            return Response({
                "message": "Follow request sent" if existing and existing.status == Follow.Status.PENDING else "Followed successfully",
                "status": existing.status if existing else None,
                "follow_id": existing.id if existing else None,
            }, status=status.HTTP_200_OK)

        if not is_private:
            User.objects.filter(id=request.user.id).update(
                following_count=F("following_count") + 1
            )
            User.objects.filter(id=following_user.id).update(
                followers_count=F("followers_count") + 1
            )

        # TASK 2: notify the other side of a follow action. Lazy imports
        # (core.models for the NotifType enum, .services for the
        # _notify wrapper) — user_profile must not hard-depend on core
        # at module-import time, since core.services.create_notification
        # already lazy-imports back into user_profile.views the other
        # way (is_restricted_between, for the restrict check).
        from core.models import Notification
        from .services import _notify

        if new_follow.status == Follow.Status.PENDING:
            _notify(
                following_user,
                Notification.NotifType.FOLLOW_REQUEST_RECEIVED,
                "New follow request",
                f"{request.user.username} wants to follow you.",
                actor=request.user,
            )
        else:
            # Public account, auto-accept — no NEW_FOLLOWER type exists
            # in the enum (see core/models.py module docstring, point
            # 5: "product-decision"), so this reuses
            # FOLLOW_REQUEST_ACCEPTED for "someone just started
            # following you" too, same as a request that was actually
            # accepted.
            _notify(
                following_user,
                Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
                "New follower",
                f"{request.user.username} started following you.",
                actor=request.user,
            )

        return Response({
            "message": "Follow request sent" if new_follow.status == Follow.Status.PENDING else "Followed successfully",
            "status": new_follow.status,
            "follow_id": new_follow.id,
        }, status=status.HTTP_201_CREATED)


class AcceptFollowRequestView(GenericAPIView):
    """
    Accept a follow request with count update
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to accept",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Accept a pending follow request",
    )
    @transaction.atomic
    def post(self, request, follow_id):
        # Sirf jis user ko request aayi hai wahi accept kar sakta hai
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        follow_request.status = Follow.Status.ACCEPTED
        follow_request.save(update_fields=["status"])

        User.objects.filter(id=follow_request.follower_id).update(
            following_count=F("following_count") + 1
        )
        User.objects.filter(id=request.user.id).update(
            followers_count=F("followers_count") + 1
        )

        # TASK 2: notify the original requester. Same lazy-import
        # pattern as FollowAPIView.post above — see that comment for why.
        from core.models import Notification
        from .services import _notify

        _notify(
            follow_request.follower_id,
            Notification.NotifType.FOLLOW_REQUEST_ACCEPTED,
            "Follow request accepted",
            f"{request.user.username} accepted your follow request.",
            actor=request.user,
        )

        return Response({
            "message": "Follow request accepted",
            "status": follow_request.status,
        }, status=status.HTTP_200_OK)


class RejectFollowRequestView(GenericAPIView):
    """
    Reject a follow request
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="follow_id",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description="ID of follow request to reject",
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Reject a pending follow request",
    )
    def post(self, request, follow_id):
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING,
        )

        follow_request.delete()

        return Response({
            "message": "Follow request rejected",
            "status": None,
        }, status=status.HTTP_200_OK)


class UpdateProfileView(GenericAPIView):
    """
    Update logged-in user's profile
    Allowed fields: username, first_name, last_name, bio, profile_photo, is_private
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProfileUpdateSerializer
    parser_classes = [MultiPartParser, FormParser]  # Image upload ke liye zaruri

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={200: ProfileUpdateSerializer},
        description="Update current user's profile. Send only fields you want to update.",
    )
    def patch(self, request):
        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True,
            context={"request": request},
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": True,
                "message": "Profile updated successfully.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)

        return Response({
            "status": False,
            "message": "Validation failed.",
            "errors": serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)


# Block / Unblock user
# Model (BlockUser) profile app me hai isliye API bhi yahin — message
# app sirf inhe consume karega (chat screen "is-blocked?" check).
class BlockedUsersView(GenericAPIView):
    """
    GET  /profile/blocked-users/          -> maine jinko block kiya hai unki list
    POST /profile/blocked-users/  {"blocked": <user_id>} -> block karo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer

    @extend_schema(
        responses={200: BlockUserSerializer(many=True)},
        description="List of users blocked by the current user",
    )
    def get(self, request):
        qs = BlockUser.objects.filter(blocker=request.user).select_related("blocked")
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Blocked users fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=BlockUserSerializer,
        responses={201: BlockUserSerializer, 200: BlockUserSerializer},
        description="Block a user",
    )
    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        blocked_user = serializer.validated_data["blocked"]

        block_obj, created = BlockUser.objects.get_or_create(
            blocker=request.user,
            blocked=blocked_user,
        )

        if created:
            # Block hote hi dono taraf ka follow-relation khatam karo, aur
            # jo ACCEPTED tha uska count bhi ghata do (FollowAPIView ke
            # unfollow wale logic jaisa hi).
            follow_qs = Follow.objects.filter(
                Q(follower=request.user, following=blocked_user)
                | Q(follower=blocked_user, following=request.user)
            )
            for f in follow_qs:
                if f.status == Follow.Status.ACCEPTED:
                    User.objects.filter(id=f.follower_id).update(
                        following_count=F("following_count") - 1
                    )
                    User.objects.filter(id=f.following_id).update(
                        followers_count=F("followers_count") - 1
                    )
            follow_qs.delete()

        return Response({
            "status": True,
            "message": "User blocked successfully." if created else "User already blocked.",
            "data": self.get_serializer(block_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnblockUserView(GenericAPIView):
    """
    DELETE /profile/blocked-users/<id>/

    `<id>` ya to BlockUser record ki apni id ho sakti hai, ya seedha
    target USER ki id — dono support karte hain (chat screen seedha
    otherParticipant.id pass karta hai, alag se record-id track nahi karta).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = BlockUserSerializer

    @extend_schema(description="Unblock a user (accepts BlockUser id or target user id)")
    def delete(self, request, id):
        block_obj = BlockUser.objects.filter(
            Q(pk=id) | Q(blocked_id=id),
            blocker=request.user,
        ).first()

        if not block_obj:
            return Response({
                "status": False,
                "message": "Block record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        block_obj.delete()
        return Response({
            "status": True,
            "message": "User unblocked successfully.",
        }, status=status.HTTP_200_OK)


# TASK 18 — Restrict / Unrestrict user
# Deliberately mirrors BlockedUsersView/UnblockUserView just above (same
# request/response shape, POST body {"restricted": <user_id>}) for
# frontend consistency. The one functional difference from block's POST
# handler: restricting someone does NOT touch Follow rows or
# followers/following counts — see RestrictUser's docstring in
# models.py for why (restrict is silent and non-blocking by design).
class RestrictedUsersView(GenericAPIView):
    """
    GET  /profile/restricted-users/                -> maine jinko restrict kiya hai unki list
    POST /profile/restricted-users/  {"restricted": <user_id>} -> restrict karo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(
        responses={200: RestrictUserSerializer(many=True)},
        description="List of users restricted by the current user",
    )
    def get(self, request):
        qs = RestrictUser.objects.filter(user=request.user).select_related("restricted")
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Restricted users fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=RestrictUserSerializer,
        responses={201: RestrictUserSerializer, 200: RestrictUserSerializer},
        description="Restrict a user (silent — the restricted user is never notified).",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={"request": request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        restricted_user = serializer.validated_data["restricted"]

        # get_or_create, not create — same idempotency reasoning as
        # BlockedUsersView.post: a double-tap/retry should return the
        # existing record instead of a 400 from the UniqueConstraint.
        restrict_obj, created = RestrictUser.objects.get_or_create(
            user=request.user,
            restricted=restricted_user,
        )

        # No Follow/count changes here on purpose (unlike block) —
        # restrict must not change what either party can see or do,
        # only what the restricting user is exposed to from the other
        # side, and only once posts/message/notifications consume
        # `is_restricted_between()`.

        return Response({
            "status": True,
            "message": "User restricted successfully." if created else "User already restricted.",
            "data": self.get_serializer(restrict_obj).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class UnrestrictUserView(GenericAPIView):
    """
    DELETE /profile/restricted-users/<id>/

    Same `<id>` flexibility as UnblockUserView — either the RestrictUser
    record's own id, or the target user's id directly.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = RestrictUserSerializer

    @extend_schema(description="Unrestrict a user (accepts RestrictUser id or target user id)")
    def delete(self, request, id):
        restrict_obj = RestrictUser.objects.filter(
            Q(pk=id) | Q(restricted_id=id),
            user=request.user,
        ).first()

        if not restrict_obj:
            return Response({
                "status": False,
                "message": "Restrict record not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        restrict_obj.delete()
        return Response({
            "status": True,
            "message": "User unrestricted successfully.",
        }, status=status.HTTP_200_OK)


# TASK 19 — coin transaction history
# Read-only, deliberately (see CoinLedgerSerializer's docstring for why
# there's no POST here). The actual write path —
# `CoinLedger.objects.record_transaction()` — is called from wherever a
# coin-changing action happens (a purchase completing in the liveclass
# app, a gift being sent in the message app, an admin adjustment
# endpoint if/when one gets built); none of those views were part of
# this upload, so this is the read side only: "let me see why my
# balance is what it is", which had no path at all before this.
class CoinLedgerListView(ListAPIView):
    """
    GET /profile/coin-ledger/

    The authenticated user's own coin transaction history, newest
    first (CoinLedger.Meta.ordering already gives us that for free).
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinLedgerSerializer

    def get_queryset(self):
        return CoinLedger.objects.filter(user=self.request.user)

    @extend_schema(
        responses={200: CoinLedgerSerializer(many=True)},
        description="List of the current user's coin transaction history (newest first).",
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = self.get_paginated_response(serializer.data).data
        else:
            serializer = self.get_serializer(queryset, many=True)
            data = serializer.data

        return Response({
            "status": True,
            "message": "Coin transaction history fetched successfully.",
            "data": data,
        }, status=status.HTTP_200_OK)


# 🔥 TASK 3 — Buy-Coin flow
# Two-step, same shape as any pending -> confirmed payment flow: this
# view only ever creates/returns a PENDING `CoinPurchaseRequest` — it
# never touches `User.coin`. The actual credit happens in
# `BuyCoinConfirmView` below, via `CoinPurchaseRequest.objects.
# confirm_success()`, which is the only path that calls
# `CoinLedger.objects.record_transaction()` for a purchase.
class BuyCoinView(GenericAPIView):
    """
    POST /profile/buy-coin/
    {"gateway_reference": "<gateway's txn id>", "amount": "99.00", "coins": 100, "gateway": "razorpay"}

    Starts a coin purchase. Idempotent on `gateway_reference`: calling
    this again with the same reference returns the existing request
    (whatever its current status) instead of creating a duplicate — safe
    for a client retrying after a dropped response.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinPurchaseRequestSerializer

    @extend_schema(
        request=CoinPurchaseRequestSerializer,
        responses={201: CoinPurchaseRequestSerializer, 200: CoinPurchaseRequestSerializer},
        description="Start a coin purchase (creates a pending request; idempotent on "
        "gateway_reference). Does not credit coins — see /buy-coin/confirm/.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        purchase, created = CoinPurchaseRequest.objects.start_purchase(
            user=request.user,
            gateway_reference=data["gateway_reference"],
            amount=data["amount"],
            coins=data["coins"],
            gateway=data.get("gateway", ""),
        )

        # `gateway_reference` is globally unique (it's the gateway's own
        # id), so if it already exists under a DIFFERENT user, this is
        # either a client bug or a replayed/guessed reference — never
        # silently let the caller read or "adopt" someone else's pending
        # purchase.
        if purchase.user_id != request.user.id:
            return Response({
                "status": False,
                "message": "This gateway_reference is already associated with another purchase.",
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": "Coin purchase started." if created
            else "Coin purchase already exists for this reference.",
            "data": self.get_serializer(purchase).data,
        }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class BuyCoinConfirmView(GenericAPIView):
    """
    POST /profile/buy-coin/confirm/
    {"gateway_reference": "<gateway's txn id>", "status": "success", "failure_reason": ""}

    Confirms a pending purchase as successful (credits `coins` through
    `CoinLedger.objects.record_transaction()`) or failed (wallet
    untouched). Idempotent and safe to call more than once for the same
    `gateway_reference` — see `CoinPurchaseRequest.objects.
    confirm_success()`/`mark_failed()` (models.py) for exactly what
    happens on a repeat call.

    🚧 NOT a real webhook endpoint as-is: no payment-gateway integration
    was part of this upload, so there's no gateway signature to verify
    here — `IsAuthenticated` + "must be your own purchase" stand in so
    the flow is testable end-to-end. Before this goes live behind an
    actual gateway callback, that verification should replace (or gate)
    the checks below; a genuine webhook call isn't "acting as" any
    particular authenticated user.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={200: CoinPurchaseRequestSerializer, 404: OpenApiTypes.OBJECT},
        description="Confirm a coin purchase as success or failed. Idempotent — safe to retry "
        "(e.g. a duplicated webhook delivery).",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        gateway_reference = serializer.validated_data["gateway_reference"]
        outcome = serializer.validated_data["status"]

        purchase = CoinPurchaseRequest.objects.filter(
            gateway_reference=gateway_reference
        ).first()
        if purchase is None:
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        # See the class docstring above re: this check standing in for
        # real webhook-signature verification.
        if purchase.user_id != request.user.id:
            raise Http404

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference
                )
                message = "Coin purchase confirmed and wallet credited."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                )
                message = "Coin purchase marked as failed."
        except ValueError as exc:
            # confirm_success()/mark_failed() raise this for an invalid
            # state transition (e.g. trying to fail an already-succeeded
            # purchase) — a 409, not a 400: the request body was valid,
            # the request's current state just doesn't allow this move.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinPurchaseRequestSerializer(purchase).data,
        }, status=status.HTTP_200_OK)

# 🔥 TASK 4 — Withdraw-Coin flow
# Mirror image of BuyCoinView/BuyCoinConfirmView above (money direction
# reversed), but ONE view instead of two: CoinWithdrawalRequestManager.
# request_withdrawal() debits the coins the moment the request is made
# (escrow-style — see its own docstring in models.py for why), so
# there's no separate "confirm" step the way a coin purchase needs one.
# mark_processing()/confirm_success()/reject() (models.py) aren't wired
# to an endpoint in this pass — same "views weren't part of this
# upload" scope-out CoinLedger's docstring already applies to the
# actions that would eventually create a ledger entry from outside this
# app; here it applies to whatever admin/ops surface will eventually
# call those three.
class CoinWithdrawalRequestView(GenericAPIView):
    """
    GET  /profile/coin-withdrawals/            -> current user's own withdrawal requests
    POST /profile/coin-withdrawals/ {"coins": 200, "payout_method": "upi", "payout_details": {"upi_id": "a@bank"}}

    Debits `coins` immediately via CoinWithdrawalRequestManager.
    request_withdrawal(). Insufficient balance is a clean 402 with no
    partial debit and no request row left behind — the debit and the
    row creation share one transaction.atomic() block inside the
    manager, so a ValueError there (bubbled up from CoinLedger.objects.
    record_transaction) rolls both back together.

    TASK 5 (this pass): before any of that, `fraud.
    is_withdrawal_eligible(request.user, coins)` is checked. This is a
    read-only check — it runs BEFORE `request_withdrawal()`, so a
    rejection here never touches the balance and never creates a
    request row (satisfies the "balance must not shrink" acceptance
    criterion directly, rather than relying on a rollback). Distinct
    from the 402 below on purpose: 402 means "you don't have enough
    coins, period"; this is "you have enough coins, but not enough
    *withdrawal-eligible* ones" — a different, policy-level rejection,
    so it gets its own 403 rather than reusing 402's "add more funds"
    implication.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = CoinWithdrawalRequestSerializer

    @extend_schema(
        responses={200: CoinWithdrawalRequestSerializer(many=True)},
        description="List of the current user's coin withdrawal requests (newest first).",
    )
    def get(self, request):
        qs = CoinWithdrawalRequest.objects.filter(user=request.user)
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Withdrawal requests fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=CoinWithdrawalRequestSerializer,
        responses={
            201: CoinWithdrawalRequestSerializer,
            402: OpenApiTypes.OBJECT,
            403: OpenApiTypes.OBJECT,
        },
        description="Request a coin withdrawal. Only coins purchased or received as a gift are "
        "withdrawal-eligible (403 if the requested amount isn't covered by eligible coins); "
        "debits the wallet immediately once eligible, with insufficient balance returning 402 "
        "and no partial debit or request row left behind either way.",
    )
    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data

        # TASK 5: fraud/eligibility check runs before any coins move —
        # see the class docstring above for why this is a 403, distinct
        # from the 402 below.
        is_eligible, reason = fraud.is_withdrawal_eligible(request.user, data["coins"])
        if not is_eligible:
            return Response({
                "status": False,
                "message": reason,
            }, status=status.HTTP_403_FORBIDDEN)

        try:
            withdrawal = CoinWithdrawalRequest.objects.request_withdrawal(
                user=request.user,
                coins=data["coins"],
                payout_method=data.get("payout_method", ""),
                payout_details=data.get("payout_details", {}),
            )
        except ValueError as exc:
            # Insufficient balance — 402, same "request was well-formed,
            # the wallet just can't cover it" shape campus's
            # FeePaymentViewSet.pay uses. Distinct from the 409s above
            # (BuyCoinConfirmView) which are for an invalid *state
            # transition*, not a money shortfall.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_402_PAYMENT_REQUIRED)

        return Response({
            "status": True,
            "message": "Withdrawal requested successfully.",
            "data": self.get_serializer(withdrawal).data,
        }, status=status.HTTP_201_CREATED)


class UserPreferenceView(GenericAPIView):
    """
    TASK 1 — GET/PATCH /user-profile/preferences/me/, same shape as
    core's `notification-preferences/me/`.

    GET always returns 200, never 404: `UserPreference.for_user()`
    get-or-creates the row, so a user who's never touched their
    theme/language still gets defaults back on first read instead of
    an empty state the frontend has to special-case.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserPreferenceSerializer

    @extend_schema(
        responses={200: UserPreferenceSerializer},
        description="Get current user's theme/language preferences.",
    )
    def get(self, request):
        preference = UserPreference.for_user(request.user)
        serializer = self.get_serializer(preference)
        return Response({
            "status": True,
            "message": "Preferences fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=UserPreferenceSerializer,
        responses={200: UserPreferenceSerializer},
        description="Update current user's theme/language preferences. Send only fields you want to update.",
    )
    def patch(self, request):
        preference = UserPreference.for_user(request.user)
        serializer = self.get_serializer(preference, data=request.data, partial=True)

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": True,
                "message": "Preferences updated successfully.",
                "data": serializer.data,
            }, status=status.HTTP_200_OK)

        return Response({
            "status": False,
            "message": "Validation failed.",
            "errors": serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)