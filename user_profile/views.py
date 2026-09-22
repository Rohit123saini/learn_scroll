# user_profile/views.py
import hashlib
import hmac
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import Http404
from django.http.request import RawPostDataException
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import filters, status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from common.pagination import get_max_page_size

from rest_framework.throttling import UserRateThrottle

from . import fraud
from .models import (
    BlockUser,
    CoinLedger,
    CoinLedgerBusy,
    CoinPurchaseRequest,
    CoinWithdrawalRequest,
    Follow,
    RestrictUser,
    UserPreference,
    WithdrawalNotEligible,
)
from .throttles import CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle
from .serializers import (
    BlockUserSerializer,
    CoinLedgerSerializer,
    CoinPurchaseConfirmSerializer,
    CoinPurchaseRequestSerializer,
    CoinWithdrawalActionSerializer,
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
    bulk_accepted_connection_ids,
)

User = get_user_model()
logger = logging.getLogger(__name__)


def _wallet_busy_response():
    """503 + Retry-After for CoinLedgerBusy (a wallet/purchase row lock timed
    out). Nothing was written, so retrying is safe — and a payment gateway
    retries a 5xx webhook on its own."""
    response = Response({
        "status": False,
        "message": "The wallet is busy right now. Please retry in a moment.",
    }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    response["Retry-After"] = "2"
    return response


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
        # Issue #17: this used to pull ALL of the viewer's connection ids into
        # a Python set and then run `id IN (<that whole set>)` — for a user
        # with tens of thousands of connections that is a huge in-memory set
        # AND a huge SQL statement (PostgreSQL also caps bind parameters),
        # before pagination could limit anything. Now the whole thing stays in
        # the database as subqueries, and only the requested page is fetched.
        me = self.request.user
        accepted = Follow.objects.filter(status=Follow.Status.ACCEPTED)

        # 🔥 FIX: someone you've since blocked (or who blocked you) could
        # still show up here as a "connection" and be pickable as a chat
        # contact — excluded in both directions.
        return (
            User.objects.filter(
                Q(id__in=accepted.filter(follower=me).values("following_id"))
                | Q(id__in=accepted.filter(following=me).values("follower_id"))
            )
            .exclude(id=me.id)
            .exclude(id__in=BlockUser.objects.filter(blocker=me).values("blocked_id"))
            .exclude(id__in=BlockUser.objects.filter(blocked=me).values("blocker_id"))
        )

    def get_serializer_context(self):
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
        }

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
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        self._connections_map = bulk_accepted_connection_ids(
            (u.id for u in rows), restrict_to_user=request.user
        )

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
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
        }

    @extend_schema(description="List of a user's followers, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        self._connections_map = bulk_accepted_connection_ids(
            (u.id for u in rows), restrict_to_user=request.user
        )

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
        return {
            "request": self.request,
            "connections_map": self._connections_map,
            "connections_map_is_mutual_only": True,  # built with restrict_to_user — see serializer
        }

    @extend_schema(description="List of who a user is following, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is None:
            # No paginator configured: never materialise the whole table
            # (issue #5) — fall back to one bounded page.
            page_rows = list(queryset[:get_max_page_size()])
            rows = page_rows
        else:
            rows = page
        self._connections_map = bulk_accepted_connection_ids(
            (u.id for u in rows), restrict_to_user=request.user
        )

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
            # Unfollow. followers_count/following_count are NOT touched
            # here any more — the Follow post_delete signal
            # (user_profile/signals.py) recounts both users from real rows.
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
            #
            # The create runs inside its OWN savepoint (`atomic()`): this
            # whole view is already wrapped in @transaction.atomic, and on
            # PostgreSQL an IntegrityError leaves the OUTER transaction
            # in an aborted state — the very next query (the `existing`
            # lookup in the except-block below) would then raise
            # TransactionManagementError -> a 500, i.e. exactly the
            # failure this except-block exists to prevent. The savepoint
            # rolls back only the failed INSERT and keeps the outer
            # transaction usable.
            with transaction.atomic():
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

        # No manual counter update: Follow's post_save signal
        # (user_profile/signals.py) recounts both users once this
        # transaction commits (a PENDING request changes no count).

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

        # save() fires Follow's post_save signal, which recounts both
        # users' followers_count/following_count (user_profile/signals.py).
        follow_request.status = Follow.Status.ACCEPTED
        follow_request.save(update_fields=["status"])

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
        # Cheap early reject: an oversized upload is refused from the
        # Content-Length header alone, before the body is parsed/spooled.
        # (Not trusted on its own — SafeProfilePhotoField re-checks the
        # real file size — this just avoids parsing an obviously huge body.)
        max_bytes = getattr(settings, "PROFILE_PHOTO_MAX_BYTES", 5 * 1024 * 1024)
        try:
            declared = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            declared = 0
        if declared > max_bytes + 1024 * 1024:  # +1 MB slack for the other form fields
            return Response({
                "status": False,
                "message": "Upload too large.",
                "errors": {"profile_photo": [f"Photo must be at most {max_bytes // (1024 * 1024)} MB."]},
            }, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

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
            # `QuerySet.delete()` sends post_delete per Follow row (a
            # receiver is registered), so the counters are corrected by
            # user_profile/signals.py — no manual F() decrement needed.
            Follow.objects.filter(
                Q(follower=request.user, following=blocked_user)
                | Q(follower=blocked_user, following=request.user)
            ).delete()

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
    # DB-bloat guard: keeps the global per-user default AND adds the two
    # purchase-specific limits (see user_profile/throttles.py).
    throttle_classes = [UserRateThrottle, CoinPurchaseBurstThrottle, CoinPurchaseDailyThrottle]

    @extend_schema(
        request=CoinPurchaseRequestSerializer,
        responses={201: CoinPurchaseRequestSerializer, 200: CoinPurchaseRequestSerializer, 429: OpenApiTypes.OBJECT},
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

        # Cap on unconfirmed rows per user. A retry of an EXISTING reference
        # is idempotent (creates nothing), so it is never blocked by the cap.
        max_pending = getattr(settings, "COIN_PURCHASE_MAX_PENDING_PER_USER", 20)
        if (
            not CoinPurchaseRequest.objects.filter(
                gateway=data.get("gateway", ""), gateway_reference=data["gateway_reference"],
            ).exists()
            and CoinPurchaseRequest.objects.filter(
                user=request.user, status=CoinPurchaseRequest.Status.PENDING,
            ).count() >= max_pending
        ):
            return Response({
                "status": False,
                "message": "Too many pending purchases. Complete or wait for the "
                           "existing ones before starting a new one.",
            }, status=status.HTTP_429_TOO_MANY_REQUESTS)

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


class GatewayWebhookSignatureError(Exception):
    """
    Raised by `_verify_gateway_webhook_signature()` below for any
    signature failure — missing secret (misconfiguration), missing
    header, or a mismatch. Callers turn this into an HTTP response;
    kept as one exception type (not three) so the view's except-block
    can't accidentally leak *which* of the three failed to the caller —
    "invalid signature" is the only thing a webhook caller should ever
    learn either way.
    """

    def __init__(self, message, *, is_misconfiguration=False):
        super().__init__(message)
        self.is_misconfiguration = is_misconfiguration


def _verify_gateway_webhook_signature(request, gateway, raw_body=None):
    """
    TASK (this pass) — user_profile_app_reference.md §11 item 10:
    `BuyCoinConfirmView` had no real payment-gateway signature check,
    only `IsAuthenticated` + "must be your own purchase" standing in for
    one, since no gateway integration was part of any upload for this
    app.

    ⚠️ ASSUMPTION — `campus`/`liveclass`'s own gateway-verify code (the
    reference the person doing this task pointed at) was NOT part of
    this pass's upload either, so this isn't copied from an established
    in-repo pattern — it's a generic, gateway-agnostic HMAC-SHA256
    webhook-signature check, the same mechanism every major payment
    gateway (Razorpay, Stripe, PayU, ...) uses for webhook auth, just
    without any one gateway's specific header name/payload-canonicalization
    quirks baked in (those differ per gateway and aren't confirmable from
    here). If `campus`/`liveclass` turns out to already have gateway
    client code with its own verification helper, prefer reusing that
    over this — this exists so the endpoint isn't left unverified in the
    meantime, not to duplicate a real gateway SDK's verification call.

    How it works: HMAC-SHA256 over the raw request body, keyed by a
    per-gateway secret, compared against a per-gateway signature header
    — both looked up from two new settings this pass introduces (NOT
    added to settings.py in this pass — out of scope for a views.py-only
    change; see this function's docstring for the exact shape needed):

        PAYMENT_GATEWAY_WEBHOOK_SECRETS = {
            "razorpay": os.environ["RAZORPAY_WEBHOOK_SECRET"],
            ...
        }
        PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS = {
            "razorpay": "X-Razorpay-Signature",
            ...
        }
        # Falls back to "X-Webhook-Signature" for any gateway not listed
        # in the headers map above.

    `gateway` is read from the ALREADY-PERSISTED `CoinPurchaseRequest.
    gateway` (set back when `BuyCoinView.post()` created the pending
    request), never from anything in this webhook call itself — a
    request body can claim to be from any gateway it likes, but it can
    only produce a signature that verifies against the secret this
    server has on file for the gateway `start_purchase()` was actually
    given.

    Raises `GatewayWebhookSignatureError` for every failure case (no
    secret configured, no signature header present, signature mismatch)
    — see that class's own docstring for why these three collapse into
    one exception/one message rather than three distinguishable ones.
    Returns None (no exception) on success.
    """
    if not gateway:
        # A blank `gateway` is a real, valid state (CoinPurchaseRequest.
        # gateway's own field comment: "not every caller may have a
        # gateway name handy (e.g. a manual admin-initiated top-up)")
        # — but exactly BECAUSE it's blank, there is no gateway secret
        # to verify a signature against, so a request with no gateway on
        # file can never be confirmed through this now-webhook-only
        # endpoint. See BuyCoinConfirmView's own docstring — this is a
        # deliberate, flagged behavior change from before this pass
        # (when IsAuthenticated + ownership let ANY caller, gateway-less
        # purchases included, confirm their own request), not an
        # oversight.
        raise GatewayWebhookSignatureError(
            "This purchase has no gateway on file — it cannot be confirmed "
            "via a gateway webhook.",
            is_misconfiguration=True,
        )

    secrets_by_gateway = getattr(settings, "PAYMENT_GATEWAY_WEBHOOK_SECRETS", {})
    secret = secrets_by_gateway.get(gateway)
    if not secret:
        # Distinct from "signature didn't match" — this is OUR config
        # missing an entry for a gateway we otherwise recognize, not the
        # caller's fault. Surfaced as 503 by the view, not 401/403.
        raise GatewayWebhookSignatureError(
            f"No webhook secret configured for gateway {gateway!r}.",
            is_misconfiguration=True,
        )

    headers_by_gateway = getattr(settings, "PAYMENT_GATEWAY_WEBHOOK_SIGNATURE_HEADERS", {})
    header_name = headers_by_gateway.get(gateway, "X-Webhook-Signature")
    provided_signature = request.headers.get(header_name, "")
    if not provided_signature:
        raise GatewayWebhookSignatureError(
            f"Missing {header_name!r} header."
        )

    # `request.body` (raw bytes, pre-parsing) — the caller MUST have
    # already forced Django to cache this (e.g. by reading `request.body`
    # once) before ever touching `request.data`. Once DRF parses
    # `request.data` first, the underlying stream is already consumed and
    # Django raises `RawPostDataException` on a later `.body` access — see
    # BuyCoinConfirmView.post()'s own comment on why it reads `.body`
    # before `self.get_serializer(data=request.data)`, not after.
    # Prefer the bytes the view already captured (`raw_body`) so this never
    # depends on a second `.body` access succeeding.
    body_bytes = raw_body if raw_body is not None else request.body
    expected_signature = hmac.new(
        secret.encode("utf-8"), body_bytes, hashlib.sha256,
    ).hexdigest()

    # `compare_digest`, not `==` — constant-time, so a caller can't use
    # response-timing differences to guess the correct signature one
    # byte at a time. Compared as BYTES: `compare_digest(str, str)` raises
    # TypeError if either str contains a non-ASCII character, and the
    # header value is attacker-controlled — a header like "é" would turn
    # a plain 401 into an unhandled 500.
    if not hmac.compare_digest(
        provided_signature.encode("utf-8"), expected_signature.encode("utf-8"),
    ):
        raise GatewayWebhookSignatureError("Signature verification failed.")


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

    TASK (this pass) — user_profile_app_reference.md §11 item 10: this is
    now a real (gateway-agnostic) webhook endpoint. `IsAuthenticated` +
    "must be your own purchase" is GONE — a genuine webhook call isn't
    "acting as" any particular authenticated user, so that check could
    never be the real gate here; it only ever worked because nothing in
    this upload could call this endpoint except a logged-in client
    testing the flow end-to-end. `permission_classes = [AllowAny]` now,
    gated instead by `_verify_gateway_webhook_signature()` above — see
    that function's own docstring for exactly what it checks and its
    ⚠️ ASSUMPTION about not having `campus`/`liveclass`'s own
    gateway-verify code to copy from.

    Behavior change worth flagging explicitly: a `CoinPurchaseRequest`
    with a BLANK `gateway` (the "manual admin-initiated top-up" case
    `CoinPurchaseRequest.gateway`'s own field comment names) could
    previously be confirmed by its owning user calling this endpoint
    themselves. It no longer can be — there's no gateway secret to
    verify a webhook signature against a blank gateway, and this
    endpoint's whole reason to exist now is verifying a real gateway
    webhook, not accepting a client's say-so.

    TASK 16 — that gap now has a replacement path: `AdminCoinPurchaseConfirmView`
    below is a separate, `IsAdminUser`-gated endpoint for exactly the
    blank-`gateway` case this view can no longer serve. See that view's
    own docstring for why it's a new endpoint rather than a permission
    branch inside this one. `BuyCoinView`, `CoinPurchaseRequest`,
    `CoinPurchaseRequestManager` are all otherwise unchanged.
    """
    permission_classes = [AllowAny]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={200: CoinPurchaseRequestSerializer, 404: OpenApiTypes.OBJECT},
        description="Gateway webhook: confirm a coin purchase as success or failed. "
        "Signature-verified — not callable as a regular authenticated user "
        "action. Idempotent — safe to retry (e.g. a duplicated webhook delivery).",
    )
    def post(self, request, gateway=None):
        # MUST happen before `self.get_serializer(data=request.data)`
        # below — see `_verify_gateway_webhook_signature()`'s own comment
        # on why. Forces Django to cache the raw body now, while the
        # stream hasn't been read yet, so DRF's later `request.data`
        # parse reads from that cached copy instead of consuming the
        # stream directly — without this ordering, the signature check
        # further down would hit Django's `RawPostDataException` instead
        # of a raw body to hash.
        #
        # If something upstream (a middleware, a proxy layer) already
        # consumed the stream WITHOUT caching it, `.body` raises
        # RawPostDataException. We can't verify an HMAC without the raw
        # bytes, so fail CLOSED with a clean 400 (the gateway will retry)
        # instead of an unhandled 500.
        try:
            raw_body = request.body
        except RawPostDataException:
            logger.error("buy-coin webhook: raw body unavailable (stream already consumed)")
            return Response({
                "status": False,
                "message": "Could not read the request body.",
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        gateway_reference = serializer.validated_data["gateway_reference"]
        outcome = serializer.validated_data["status"]

        # A gateway's transaction id is only unique WITHIN that gateway, so
        # the same string can exist on two purchases. Which one is meant is
        # decided by the gateway-specific URL (/buy-coin/confirm/<gateway>/ —
        # configured by us in the gateway's webhook settings, not by the
        # caller) or, failing that, an optional `gateway` in the body. With
        # neither, the reference must identify exactly one purchase.
        # The hint only NARROWS the lookup; the signature below is still
        # verified against the secret of the purchase's own recorded gateway,
        # so it can't be used to confirm anything without that gateway's key.
        gateway_hint = (gateway or serializer.validated_data.get("gateway") or "").strip().lower()
        matches = CoinPurchaseRequest.objects.filter(gateway_reference=gateway_reference)
        if gateway_hint:
            matches = matches.filter(gateway=gateway_hint)
        matches = list(matches.order_by("pk")[:2])
        if not matches:
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)
        if len(matches) > 1:
            return Response({
                "status": False,
                "message": "This reference exists on more than one gateway — use "
                           "/buy-coin/confirm/<gateway>/ to say which.",
            }, status=status.HTTP_409_CONFLICT)
        purchase = matches[0]

        # Replaces the old `purchase.user_id != request.user.id` ownership
        # check — see class docstring. Keyed off `purchase.gateway` (this
        # server's own record of which gateway the purchase was started
        # against), never off anything the caller claims in this request.
        try:
            _verify_gateway_webhook_signature(request, purchase.gateway, raw_body=raw_body)
        except GatewayWebhookSignatureError as exc:
            if exc.is_misconfiguration:
                # Our config's fault (no secret on file / no gateway to
                # verify against at all), not the caller's — 503, not
                # 401/403, and safe to say so explicitly since it's not a
                # signature-guessing hint.
                return Response({
                    "status": False,
                    "message": str(exc),
                }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            # Deliberately generic message for every other failure
            # (missing header, bad signature) — never confirms/denies
            # *which* part was wrong to an unauthenticated caller.
            return Response({
                "status": False,
                "message": "Webhook signature verification failed.",
            }, status=status.HTTP_401_UNAUTHORIZED)

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference, gateway=purchase.gateway,
                )
                message = "Coin purchase confirmed and wallet credited."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                    gateway=purchase.gateway,
                )
                message = "Coin purchase marked as failed."
        except CoinLedgerBusy:
            return _wallet_busy_response()
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


# 🔥 TASK 16 — manual/admin-initiated top-up confirm path.
# BuyCoinConfirmView above went webhook-only for §11 item 10's signature
# hardening, which closed a real hole (any authenticated caller could
# confirm their own purchase with no proof money ever moved) but also
# closed off the one legitimate no-gateway case CoinPurchaseRequest.
# gateway's own field comment names: a manual/admin-initiated top-up,
# where there was never going to be a gateway webhook to verify in the
# first place. This view is that case's replacement confirm path —
# staff-only, separate from the webhook endpoint, not a loosening of it.
class AdminCoinPurchaseConfirmView(GenericAPIView):
    """
    POST /profile/buy-coin/admin-confirm/
    {"gateway_reference": "<manual/internal reference>", "status": "success", "failure_reason": ""}

    Confirms a manual/admin-initiated top-up (a `CoinPurchaseRequest`
    with a BLANK `gateway`) as successful or failed. Staff-only
    (`IsAdminUser`) — same "give ops an API instead of a Django shell"
    pattern `CoinWithdrawalAdminActionView` above already uses.

    Deliberately a SEPARATE endpoint from `BuyCoinConfirmView`, not a
    permission-class change on it. `BuyCoinConfirmView`'s entire reason
    to exist post-item-10 is verifying a real gateway webhook signature
    (`AllowAny` + HMAC — see `_verify_gateway_webhook_signature()`); an
    admin escape hatch folded into that same view would blur "verified
    gateway webhook" and "trusted staff override" into one code path,
    undoing the point of that hardening. Keeping them separate also
    means a compromised/misused staff account can't be used to forge a
    gateway webhook signature — it can only ever touch gateway-less
    purchases (see the scope restriction below), never one a real
    gateway is expected to confirm.

    Scope restriction: ONLY confirms purchases with a BLANK `gateway`
    on file. A `CoinPurchaseRequest` that DOES have a gateway must
    still go through the real webhook (`BuyCoinConfirmView`) — letting
    staff manually confirm a gateway-backed purchase here would let
    anyone with staff access credit coins without the gateway ever
    having actually taken payment, which is exactly the risk item 10's
    signature check exists to prevent. A gateway-backed purchase gets a
    409, not a silent success, if pointed at this endpoint.

    Otherwise mirrors `BuyCoinConfirmView`'s own contract: idempotent
    and safe to retry (`confirm_success()`/`mark_failed()`, models.py),
    404 if `gateway_reference` doesn't match any request, 409 for an
    invalid state transition (e.g. failing an already-succeeded
    purchase).
    """
    permission_classes = [IsAdminUser]
    serializer_class = CoinPurchaseConfirmSerializer

    @extend_schema(
        request=CoinPurchaseConfirmSerializer,
        responses={
            200: CoinPurchaseRequestSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
        },
        description="Staff-only. Confirm a manual/admin-initiated top-up "
        "(a CoinPurchaseRequest with no gateway on file) as success or failed. "
        "Gateway-backed purchases are rejected here (409) — they must go "
        "through the gateway webhook (/buy-coin/confirm/) instead.",
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

        # Only ever look at BLANK-gateway rows: a gateway-backed purchase that
        # merely shares this reference string must never be picked up here.
        purchase = CoinPurchaseRequest.objects.filter(
            gateway_reference=gateway_reference, gateway=""
        ).first()
        if purchase is None:
            if CoinPurchaseRequest.objects.filter(gateway_reference=gateway_reference).exists():
                return Response({
                    "status": False,
                    "message": "This purchase has a gateway on file and must be "
                    "confirmed via the gateway webhook (/buy-coin/confirm/), not "
                    "the admin manual-confirm endpoint.",
                }, status=status.HTTP_409_CONFLICT)
            return Response({
                "status": False,
                "message": "Coin purchase request not found.",
            }, status=status.HTTP_404_NOT_FOUND)

        if purchase.gateway:
            # See class docstring's scope restriction — a gateway-backed
            # purchase must be confirmed by that gateway's own webhook,
            # never by a staff member's say-so.
            return Response({
                "status": False,
                "message": "This purchase has a gateway on file and must be "
                "confirmed via the gateway webhook (/buy-coin/confirm/), not "
                "the admin manual-confirm endpoint.",
            }, status=status.HTTP_409_CONFLICT)

        try:
            if outcome == "success":
                purchase = CoinPurchaseRequest.objects.confirm_success(
                    gateway_reference=gateway_reference, gateway="",
                )
                message = "Coin purchase confirmed and wallet credited (manual admin action)."
            else:
                purchase = CoinPurchaseRequest.objects.mark_failed(
                    gateway_reference=gateway_reference,
                    reason=serializer.validated_data.get("failure_reason", ""),
                    gateway="",
                )
                message = "Coin purchase marked as failed (manual admin action)."
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # Invalid state transition (e.g. failing an already-succeeded
            # purchase) — same 409 shape BuyCoinConfirmView uses above.
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
        except WithdrawalNotEligible as exc:
            # The under-lock re-check inside request_withdrawal() (the one
            # the pre-check above can't make race-proof) rejected it: same
            # policy 403 as the pre-check, no debit, no row.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_403_FORBIDDEN)
        except CoinLedgerBusy:
            return _wallet_busy_response()
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


# 🔥 §11 item 12 — staff/ops endpoint for CoinWithdrawalRequestManager's
# three lifecycle methods. Before this, mark_processing()/
# confirm_success()/reject() (models.py) existed and were unit-tested,
# but nothing in urls.py called them — a withdrawal could only ever
# reach PENDING through the public API (CoinWithdrawalRequestView
# above); moving it further required a Django shell. This view is the
# "future admin action" that item 12 flagged as not existing yet — it
# doesn't touch CoinWithdrawalRequestView itself (that view still only
# lets a user see/create their OWN requests) and doesn't open any new
# path to CoinLedger: it calls the exact same manager methods
# CoinLedgerAdmin's own docstring (admin.py) points to as the
# sanctioned way to move a withdrawal forward, just reachable over the
# API instead of only from a shell.
class CoinWithdrawalAdminActionView(GenericAPIView):
    """
    POST /profile/coin-withdrawals/<int:withdrawal_id>/action/
    {"action": "processing"}                              -> mark_processing()
    {"action": "success"}                                  -> confirm_success()
    {"action": "reject", "reason": "optional explanation"} -> reject()

    Staff-only (`IsAdminUser` — `request.user.is_staff`). This mirrors
    the level Django admin itself already requires to reach these same
    three methods; it does not add a new, looser way in. Ordinary
    authenticated users keep using `CoinWithdrawalRequestView` above
    for their own requests (GET to list, POST to create) — this view
    has no GET and never filters by `request.user`, since ops needs to
    act on *any* user's withdrawal, not just their own.

    Response codes follow the same conventions the rest of this
    module already uses for the underlying manager methods:
      - 404 if `withdrawal_id` doesn't exist at all.
      - 409 if the manager raises `ValueError` for an invalid state
        transition (e.g. trying to reject an already-SUCCESS request)
        — same "well-formed request, wrong current state" shape
        `BuyCoinConfirmView` already uses above for its own 409s.
      - 400 for a missing/unrecognized `action` value — a request-body
        problem, not a state problem.

    Not idempotency-guarded beyond what the manager methods themselves
    already do (`confirm_success`/`reject` are idempotent per their own
    docstrings in models.py; `mark_processing` is not, and calling it
    twice on an already-PROCESSING row is intentionally left to raise
    from a plain equality check there would need — out of scope here,
    same as the other manager-level caveats §11 already tracks).
    """

    permission_classes = [IsAdminUser]
    serializer_class = CoinWithdrawalActionSerializer

    # Kept as attributes for anything that imports them; the accepted values
    # themselves are defined once, on CoinWithdrawalActionSerializer.
    ACTION_PROCESSING = CoinWithdrawalActionSerializer.ACTION_PROCESSING
    ACTION_SUCCESS = CoinWithdrawalActionSerializer.ACTION_SUCCESS
    ACTION_REJECT = CoinWithdrawalActionSerializer.ACTION_REJECT
    VALID_ACTIONS = CoinWithdrawalActionSerializer.VALID_ACTIONS

    @extend_schema(
        request=CoinWithdrawalActionSerializer,
        responses={
            200: CoinWithdrawalRequestSerializer,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            409: OpenApiTypes.OBJECT,
        },
        description="Staff-only. Advance a withdrawal request's lifecycle: "
        "{'action': 'processing'|'success'|'reject', 'reason': '<reject only, optional>'}.",
    )
    def post(self, request, withdrawal_id):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)
        action = serializer.validated_data["action"]

        try:
            if action == self.ACTION_PROCESSING:
                withdrawal = CoinWithdrawalRequest.objects.mark_processing(
                    withdrawal_id=withdrawal_id, reviewed_by=request.user,
                )
                message = "Withdrawal moved to processing."
            elif action == self.ACTION_SUCCESS:
                withdrawal = CoinWithdrawalRequest.objects.confirm_success(
                    withdrawal_id=withdrawal_id
                )
                message = "Withdrawal marked successful."
            else:
                withdrawal = CoinWithdrawalRequest.objects.reject(
                    withdrawal_id=withdrawal_id,
                    reason=serializer.validated_data.get("reason", ""),
                    reviewed_by=request.user,
                )
                message = "Withdrawal rejected and coins refunded."
        except CoinWithdrawalRequest.DoesNotExist:
            return Response({
                "status": False,
                "message": f"No withdrawal request with id {withdrawal_id}.",
            }, status=status.HTTP_404_NOT_FOUND)
        except CoinLedgerBusy:
            return _wallet_busy_response()
        except ValueError as exc:
            # Invalid state transition (e.g. rejecting an already-
            # SUCCESS request) — the manager methods raise ValueError
            # for exactly this; see models.py for each one's own rules.
            return Response({
                "status": False,
                "message": str(exc),
            }, status=status.HTTP_409_CONFLICT)

        return Response({
            "status": True,
            "message": message,
            "data": CoinWithdrawalRequestSerializer(withdrawal).data,
        }, status=status.HTTP_200_OK)


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