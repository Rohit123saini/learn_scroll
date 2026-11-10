# user_profile/views/profile.py — split out of the old user_profile/views.py (P-C4). Code is moved verbatim.

from ..models import (
    BlockUser,
    Follow,
)
from ..serializers import (
    BlockedTargetUserProfileSerializer,
    RestrictedTargetUserProfileSerializer,
    TargetUserProfileSerializer,
    UserProfileDetailResponseSerializer,
    UserProfileSerializer,
)
from rest_framework.generics import GenericAPIView
from django.http import Http404
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
)
from drf_spectacular.types import OpenApiTypes
from django.db.models import Q
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .. import profile_state
from rest_framework import status
from ._common import (
    User,
    is_restricted_between,
)


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
        is_self = target_user == request.user

        # 🔥 FIX: block wasn't checked anywhere — a blocked/blocking user
        # could still look up the full profile. Mimic "user not found"
        # rather than a 403, so blocking doesn't leak who blocked whom.
        # Instagram behaviour: the person who got blocked sees "not found";
        # the person who did the blocking still gets a minimal card so the
        # app can show "You blocked this account" + an Unblock button.
        # P10: both directions in ONE query (was two), both follow rows in ONE query (was two).
        is_blocked_by_me = False
        my_follow_obj = None
        their_follow_obj = None
        if not is_self:
            blockers = set(
                BlockUser.objects.filter(
                    Q(blocker=target_user, blocked=request.user) | Q(blocker=request.user, blocked=target_user)
                ).values_list("blocker_id", flat=True)
            )
            if target_user.pk in blockers:
                raise Http404
            is_blocked_by_me = request.user.pk in blockers

            for f in Follow.objects.filter(
                Q(follower=request.user, following=target_user) | Q(follower=target_user, following=request.user)
            ):
                if f.follower_id == request.user.pk:
                    my_follow_obj = f
                else:
                    their_follow_obj = f

        is_accepted_follower = bool(
            my_follow_obj and my_follow_obj.status == Follow.Status.ACCEPTED
        )

        # P9 - one profile open counts as one visit for the owner's insights (counts only, never who; not for
        # yourself, blocked pairs or under-18 visitors; failures are swallowed inside record_visit).
        if not is_self and not is_blocked_by_me:
            from ..profile_insights import record_visit

            record_visit(target_user, request.user, blocks_checked=True)

        # 🔥 FIX: `is_private` existed on the model and was even returned
        # in the response body, but nothing ever *enforced* it — anyone
        # authenticated could read a private account's bio/photo/counts
        # just by knowing the username. Now: full data only for the owner,
        # public accounts, or accepted followers; everyone else gets a
        # minimal "this account is private" style payload.
        is_muted_by_me = False
        mute_posts = False
        mute_stories = False
        if not is_self:
            from post.models import MutedAccount

            mute_row = MutedAccount.objects.filter(user=request.user, muted_user=target_user).first()
            if mute_row is not None:
                is_muted_by_me = True
                mute_posts, mute_stories = mute_row.mute_posts, mute_row.mute_stories  # P8

        is_restricted_view = target_user.is_private and not is_self and not is_accepted_follower
        # P10: a suspended / deactivated account is "not available" to everybody but its owner (blocker still gets the
        # Unblock card first - they need that button).
        is_suspended = (not is_self) and not target_user.is_active
        if is_blocked_by_me:
            # Only identity (photo/name) — no bio, counts, links or posts.
            is_restricted_view = True
            profile_data = BlockedTargetUserProfileSerializer(target_user).data
        elif is_suspended:
            is_restricted_view = True
            profile_data = profile_state.unavailable_card(target_user)
        elif is_restricted_view:
            profile_data = RestrictedTargetUserProfileSerializer(target_user).data
        else:
            # P1: `viewer` lets the serializer hide location/links of under-18 accounts and show
            # owner-only extras (completeness, username_change) on your own profile.
            profile_data = TargetUserProfileSerializer(target_user, context={"viewer": request.user}).data

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
                "profile_state": profile_state.classify(
                    is_blocked_by_me=is_blocked_by_me, is_suspended=is_suspended, is_restricted_view=is_restricted_view
                ),  # P10 - ok | private | blocked_by_me | suspended (blocked-ME = 404 on purpose)
                "is_restricted_view": is_restricted_view,
                "am_i_restricting": am_i_restricting,
                "is_blocked_by_me": is_blocked_by_me,
                "is_muted_by_me": is_muted_by_me,
                "mute_posts": mute_posts,  # P8 - what the mute covers (both False when not muted)
                "mute_stories": mute_stories,
                "data": profile_data,
            },
            status=status.HTTP_200_OK,
        )
