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

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status, filters
from rest_framework.generics import ListAPIView, GenericAPIView
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import F
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