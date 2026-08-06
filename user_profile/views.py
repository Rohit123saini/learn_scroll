from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, filters
from rest_framework.generics import ListAPIView, GenericAPIView
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes
from rest_framework.parsers import MultiPartParser, FormParser
from .serializers import *
from .models import *
from rest_framework.parsers import MultiPartParser, FormParser
User = get_user_model()


class ProfileView(GenericAPIView):
    """
    Get logged-in user's profile
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileSerializer

    @extend_schema(
        responses={200: UserProfileSerializer},
        description="Get current authenticated user's profile"
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
    Get any user's profile by username with follow status
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileDetailResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='username',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description='Username of target user'
            )
        ],
        responses={200: UserProfileDetailResponseSerializer},
        description="Get user profile with follow status"
    )
    def get(self, request, username):
        target_user = get_object_or_404(User, username=username)

        # Follow ka status nikal
        follow_obj = Follow.objects.filter(
            follower=request.user,
            following=target_user
        ).first()

        serializer = TargetUserProfileSerializer(target_user)

        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "my_id": request.user.id,
                "my_username": request.user.username,
                "target_user_id": target_user.id,
                "target_username": target_user.username,
                "follow_status": follow_obj.status if follow_obj else None,
                "follow_id": follow_obj.id if follow_obj else None,
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class UserSearchView(ListAPIView):
    """
    Search users by username, first_name, last_name
    """
    permission_classes = [IsAuthenticated]
    queryset = User.objects.all()
    serializer_class = UserSearchSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ['username', 'first_name', 'last_name']

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='search',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Search query'
            )
        ],
        description="Search users"
    )
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Users fetched successfully.",
            "data": response.data
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
    search_fields = ['username', 'first_name', 'last_name']

    def get_queryset(self):
        connected_ids = accepted_connection_ids(self.request.user)
        connected_ids.discard(self.request.user.id)
        return User.objects.filter(id__in=connected_ids)

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='search',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Search query'
            )
        ],
        description="Search only within users who follow you or whom you follow (for chat/group member picking)"
    )
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Contacts fetched successfully.",
            "data": response.data
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
        target_user = get_object_or_404(User, username=self.kwargs['username'])
        follower_ids = Follow.objects.filter(
            following=target_user, status=Follow.Status.ACCEPTED
        ).values_list('follower_id', flat=True)
        return User.objects.filter(id__in=follower_ids)

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(description="List of a user's followers, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Followers fetched successfully.",
            "data": response.data
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
        target_user = get_object_or_404(User, username=self.kwargs['username'])
        following_ids = Follow.objects.filter(
            follower=target_user, status=Follow.Status.ACCEPTED
        ).values_list('following_id', flat=True)
        return User.objects.filter(id__in=following_ids)

    def get_serializer_context(self):
        return {'request': self.request}

    @extend_schema(description="List of who a user is following, with mutual_friends relative to you")
    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        return Response({
            "status": True,
            "message": "Following fetched successfully.",
            "data": response.data
        })

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, filters
from rest_framework.generics import ListAPIView, GenericAPIView
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import F, Q
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from .serializers import *
from .models import *

User = get_user_model()


class FollowAPIView(GenericAPIView):
    """
    Follow/Unfollow a user with count update
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='user_id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description='ID of user to follow/unfollow'
            )
        ],
        responses={
            200: FollowActionResponseSerializer,
            201: FollowActionResponseSerializer,
            400: OpenApiTypes.OBJECT
        },
        description="Follow or unfollow a user"
    )
    @transaction.atomic  # 🔥 Important: Sab ek saath hoga ya kuch nahi
    def post(self, request, user_id):
        if request.user.id == user_id:
            return Response(
                {"error": "You cannot follow yourself"},
                status=status.HTTP_400_BAD_REQUEST
            )

        following_user = get_object_or_404(User, id=user_id)

        # Check karo already follow to nahi kar raha
        follow_obj = Follow.objects.filter(
            follower=request.user,
            following=following_user
        ).first()

        if follow_obj:
            # 🔥 Unfollow: Count minus karo
            if follow_obj.status == Follow.Status.ACCEPTED:
                # Sirf accepted follow me count minus hoga
                User.objects.filter(id=request.user.id).update(
                    following_count=F('following_count') - 1
                )
                User.objects.filter(id=following_user.id).update(
                    followers_count=F('followers_count') - 1
                )

            follow_obj.delete()
            return Response({
                "message": "Unfollowed successfully",
                "status": None
            }, status=status.HTTP_200_OK)

        # 🔥 Naya follow create karo
        is_private = following_user.is_private
        new_follow = Follow.objects.create(
            follower=request.user,
            following=following_user,
            status=Follow.Status.PENDING if is_private else Follow.Status.ACCEPTED
        )

        # 🔥 Agar public account hai to turant count plus karo
        if not is_private:
            User.objects.filter(id=request.user.id).update(
                following_count=F('following_count') + 1
            )
            User.objects.filter(id=following_user.id).update(
                followers_count=F('followers_count') + 1
            )

        return Response({
            "message": "Follow request sent" if new_follow.status == Follow.Status.PENDING else "Followed successfully",
            "status": new_follow.status,
            "follow_id": new_follow.id
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
                name='follow_id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description='ID of follow request to accept'
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Accept a pending follow request"
    )
    @transaction.atomic  # 🔥 Important
    def post(self, request, follow_id):
        # Sirf jis user ko request aayi hai wahi accept kar sakta hai
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING
        )

        follow_request.status = Follow.Status.ACCEPTED
        follow_request.save()

        # 🔥 Accept karte hi count plus karo
        User.objects.filter(id=follow_request.follower.id).update(
            following_count=F('following_count') + 1
        )
        User.objects.filter(id=request.user.id).update(
            followers_count=F('followers_count') + 1
        )

        return Response({
            "message": "Follow request accepted",
            "status": follow_request.status
        }, status=status.HTTP_200_OK)


class RejectFollowRequestView(GenericAPIView):
    """
    Reject a follow request - Optional but good to have
    """
    permission_classes = [IsAuthenticated]
    serializer_class = FollowActionResponseSerializer

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='follow_id',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH,
                description='ID of follow request to reject'
            )
        ],
        responses={200: FollowActionResponseSerializer},
        description="Reject a pending follow request"
    )
    def post(self, request, follow_id):
        follow_request = get_object_or_404(
            Follow,
            id=follow_id,
            following=request.user,
            status=Follow.Status.PENDING
        )

        follow_request.delete()

        return Response({
            "message": "Follow request rejected",
            "status": None
        }, status=status.HTTP_200_OK)


class UserProfileDetailView(GenericAPIView):
    """
    Get any user's profile with two-way follow status
    """
    permission_classes = [IsAuthenticated]
    serializer_class = UserProfileDetailResponseSerializer
    parser_classes = [MultiPartParser, FormParser]
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='username',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.PATH,
                description='Username of target user'
            )
        ],
        responses={200: UserProfileDetailResponseSerializer},
        description="Get user profile with two-way follow status"
    )
    def get(self, request, username):
        target_user = get_object_or_404(User, username=username)

        # 🔥 1. Main user ne target ko follow kiya ya nahi
        my_follow_obj = Follow.objects.filter(
            follower=request.user,
            following=target_user
        ).first()

        # 🔥 2. Target user ne main user ko follow kiya ya nahi
        their_follow_obj = Follow.objects.filter(
            follower=target_user,
            following=request.user
        ).first()

        serializer = TargetUserProfileSerializer(target_user)

        return Response(
            {
                "status": True,
                "message": "Profile fetched successfully.",
                "my_id": request.user.id,
                "my_username": request.user.username,
                "target_user_id": target_user.id,
                "target_username": target_user.username,

                # Main → Target
                "my_follow_status": my_follow_obj.status if my_follow_obj else None,
                "my_follow_id": my_follow_obj.id if my_follow_obj else None,

                # Target → Main
                "their_follow_status": their_follow_obj.status if their_follow_obj else None,
                "their_follow_id": their_follow_obj.id if their_follow_obj else None,

                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class UpdateProfileView(GenericAPIView):
    """
    Update logged-in user's profile
    Allowed fields: username, first_name, last_name, bio, profile_photo
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProfileUpdateSerializer
    parser_classes = [MultiPartParser, FormParser] # Image upload ke liye zaruri

    @extend_schema(
        request=ProfileUpdateSerializer,
        responses={200: ProfileUpdateSerializer},
        description="Update current user's profile. Send only fields you want to update."
    )
    def patch(self, request):
        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True, # Partial update allowed
            context={'request': request}
        )

        if serializer.is_valid():
            serializer.save()
            return Response({
                "status": True,
                "message": "Profile updated successfully.",
                "data": serializer.data
            }, status=status.HTTP_200_OK)

        return Response({
            "status": False,
            "message": "Validation failed.",
            "errors": serializer.errors
        }, status=status.HTTP_400_BAD_REQUEST)


# 🔥 NAYA — Block / Unblock user
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
        description="List of users blocked by the current user"
    )
    def get(self, request):
        qs = BlockUser.objects.filter(blocker=request.user).select_related('blocked')
        serializer = self.get_serializer(qs, many=True)
        return Response({
            "status": True,
            "message": "Blocked users fetched successfully.",
            "data": serializer.data,
        }, status=status.HTTP_200_OK)

    @extend_schema(
        request=BlockUserSerializer,
        responses={201: BlockUserSerializer, 200: BlockUserSerializer},
        description="Block a user"
    )
    @transaction.atomic
    def post(self, request):
        serializer = self.get_serializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response({
                "status": False,
                "message": "Validation failed.",
                "errors": serializer.errors,
            }, status=status.HTTP_400_BAD_REQUEST)

        blocked_user = serializer.validated_data['blocked']

        block_obj, created = BlockUser.objects.get_or_create(
            blocker=request.user,
            blocked=blocked_user,
        )

        if created:
            # 🔥 Block hote hi dono taraf ka follow-relation khatam karo,
            # aur jo ACCEPTED tha uska count bhi ghata do (FollowAPIView
            # ke unfollow wale logic jaisa hi).
            follow_qs = Follow.objects.filter(
                Q(follower=request.user, following=blocked_user) |
                Q(follower=blocked_user, following=request.user)
            )
            for f in follow_qs:
                if f.status == Follow.Status.ACCEPTED:
                    User.objects.filter(id=f.follower_id).update(
                        following_count=F('following_count') - 1
                    )
                    User.objects.filter(id=f.following_id).update(
                        followers_count=F('followers_count') - 1
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