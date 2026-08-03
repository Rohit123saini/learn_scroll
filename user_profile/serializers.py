from django.contrib.auth import get_user_model
from rest_framework import serializers
from .models import Follow

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    followers_count = serializers.IntegerField(read_only=True)
    following_count = serializers.IntegerField(read_only=True)
    posts_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "profile_photo",
            "bio",
            "is_private",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
            "coin",
        ]
        read_only_fields = ["id", "username", "coin", "is_verified"]


class UserSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'profile_photo']


def accepted_connection_ids(user):
    """
    Jinke saath is user ka "real" (accepted) follow-relation hai — chahe
    is user ne unhe follow kiya ho ya unhone is user ko — dono taraf se.
    Mutual-friends count isi set ke overlap se nikalta hai.

    ⚠️ Naam me leading underscore JAANBUJH KAR nahi rakha — views.py
    `from .serializers import *` karta hai, aur wildcard import
    underscore-prefixed names ko skip kar deta hai.
    """
    following_ids = set(
        Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED)
        .values_list('following_id', flat=True)
    )
    follower_ids = set(
        Follow.objects.filter(following=user, status=Follow.Status.ACCEPTED)
        .values_list('follower_id', flat=True)
    )
    return following_ids | follower_ids


class MessageContactSearchSerializer(serializers.ModelSerializer):
    """
    Message/group "add members" search ke response ke liye — sirf ye 5
    fields, koi profile_photo/bio waghera nahi.
    """
    mutual_friends = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'first_name', 'last_name', 'mutual_friends']

    def get_mutual_friends(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0
        my_connections = self.context.get('_my_connections')
        if my_connections is None:
            my_connections = accepted_connection_ids(request.user)
            self.context['_my_connections'] = my_connections
        their_connections = accepted_connection_ids(obj)
        return len(my_connections & their_connections)


class TargetUserProfileSerializer(serializers.ModelSerializer):
    followers_count = serializers.IntegerField(read_only=True)
    following_count = serializers.IntegerField(read_only=True)
    posts_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "first_name",
            "last_name",
            "profile_photo",
            "bio",
            "is_private",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
        ]


class FollowSerializer(serializers.ModelSerializer):
    follower_username = serializers.CharField(source='follower.username', read_only=True)
    following_username = serializers.CharField(source='following.username', read_only=True)

    class Meta:
        model = Follow
        fields = ['id', 'follower', 'follower_username', 'following', 'following_username', 'status', 'created_at']
        read_only_fields = ['follower', 'created_at']


class FollowActionResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    status = serializers.CharField(allow_null=True)
    follow_id = serializers.IntegerField(required=False)


class UserProfileDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    message = serializers.CharField()
    my_id = serializers.IntegerField()
    my_username = serializers.CharField()
    target_user_id = serializers.IntegerField()
    target_username = serializers.CharField()
    follow_status = serializers.CharField(allow_null=True)
    follow_id = serializers.IntegerField(allow_null=True)
    data = TargetUserProfileSerializer()


class UserProfileDetailResponseSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    message = serializers.CharField()
    my_id = serializers.IntegerField()
    my_username = serializers.CharField()
    target_user_id = serializers.IntegerField()
    target_username = serializers.CharField()
    my_follow_status = serializers.CharField(allow_null=True)
    my_follow_id = serializers.IntegerField(allow_null=True)
    their_follow_status = serializers.CharField(allow_null=True)
    their_follow_id = serializers.IntegerField(allow_null=True)
    data = TargetUserProfileSerializer()

class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'bio', 'profile_photo']
        extra_kwargs = {
            'username': {'required': False},
            'first_name': {'required': False},
            'last_name': {'required': False},
            'bio': {'required': False},
            'profile_photo': {'required': False},
        }

    def validate_username(self, value):
        user = self.context['request'].user
        if User.objects.filter(username=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Ye username already taken hai.")
        return value