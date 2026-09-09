from collections import defaultdict

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Follow, BlockUser

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
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
        # 🔥 FIX: these are denormalized counters + a balance maintained by
        # views/other apps — never writable from the profile-update
        # payload, so they belong in read_only_fields, not just left out
        # of ProfileUpdateSerializer's field list (defense in depth).
        read_only_fields = [
            "id",
            "username",
            "coin",
            "is_verified",
            "followers_count",
            "following_count",
            "posts_count",
        ]


class UserSearchSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "profile_photo"]


def accepted_connection_ids(user):
    """
    Jinke saath is user ka "real" (accepted) follow-relation hai — chahe
    is user ne unhe follow kiya ho ya unhone is user ko — dono taraf se.
    Mutual-friends count isi set ke overlap se nikalta hai.

    ⚠️ Naam me leading underscore JAANBUJH KAR nahi rakha — views.py
    ab explicit imports use karta hai, lekin agar kahin wildcard import
    reh gaya ho to bhi ye safe rahe isliye convention same rakha hai.
    """
    following_ids = set(
        Follow.objects.filter(follower=user, status=Follow.Status.ACCEPTED)
        .values_list("following_id", flat=True)
    )
    follower_ids = set(
        Follow.objects.filter(following=user, status=Follow.Status.ACCEPTED)
        .values_list("follower_id", flat=True)
    )
    return following_ids | follower_ids


def bulk_accepted_connection_ids(user_ids):
    """
    🔥 FIX (N+1): `accepted_connection_ids()` runs 2 queries per user. The
    followers/following/chat-search list views were calling it once per
    row being serialized — for a page of 50 users that's 100 extra
    queries just to compute `mutual_friends`.

    This does the same computation for a whole batch of user ids in
    exactly 2 queries and returns {user_id: set(connected_user_ids)}, so a
    view can call this once per request and hand each row's set to the
    serializer via context instead of re-querying per row.
    """
    user_ids = list({uid for uid in user_ids if uid is not None})
    if not user_ids:
        return {}

    connections = defaultdict(set)

    as_follower = Follow.objects.filter(
        follower_id__in=user_ids, status=Follow.Status.ACCEPTED
    ).values_list("follower_id", "following_id")
    for follower_id, following_id in as_follower:
        connections[follower_id].add(following_id)

    as_following = Follow.objects.filter(
        following_id__in=user_ids, status=Follow.Status.ACCEPTED
    ).values_list("following_id", "follower_id")
    for following_id, follower_id in as_following:
        connections[following_id].add(follower_id)

    return connections


class MessageContactSearchSerializer(serializers.ModelSerializer):
    """
    Message/group "add members" search ke response ke liye — sirf ye 5
    fields, koi profile_photo/bio waghera nahi.
    """
    mutual_friends = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "mutual_friends"]

    def get_mutual_friends(self, obj):
        request = self.context.get("request")
        if not request or not request.user.is_authenticated:
            return 0

        my_connections = self.context.get("_my_connections")
        if my_connections is None:
            my_connections = accepted_connection_ids(request.user)
            self.context["_my_connections"] = my_connections

        # 🔥 FIX: prefer a precomputed batch map from the view (no extra
        # query at all). Falls back to the old per-object query so this
        # serializer still works standalone if a caller doesn't pass one.
        connections_map = self.context.get("connections_map")
        if connections_map is not None:
            their_connections = connections_map.get(obj.id, set())
        else:
            their_connections = accepted_connection_ids(obj)

        return len(my_connections & their_connections)


class TargetUserProfileSerializer(serializers.ModelSerializer):
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


class RestrictedTargetUserProfileSerializer(serializers.ModelSerializer):
    """
    🔥 NEW: what a private account shows to a viewer who isn't an accepted
    follower (and isn't the account owner) — Instagram-style "this account
    is private" card. Before this, `UserProfileDetailView` returned the
    full `TargetUserProfileSerializer` payload (bio, counts, photo) to
    *anyone*, regardless of `is_private` — the field existed on the model
    but nothing ever checked it.
    """

    class Meta:
        model = User
        fields = ["id", "username", "first_name", "last_name", "is_private", "is_verified"]


class FollowSerializer(serializers.ModelSerializer):
    follower_username = serializers.CharField(source="follower.username", read_only=True)
    following_username = serializers.CharField(source="following.username", read_only=True)

    class Meta:
        model = Follow
        fields = ["id", "follower", "follower_username", "following", "following_username", "status", "created_at"]
        read_only_fields = ["follower", "created_at"]


class FollowActionResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    status = serializers.CharField(allow_null=True)
    follow_id = serializers.IntegerField(required=False)


class UserProfileDetailResponseSerializer(serializers.Serializer):
    """
    🔥 FIX: this class was defined TWICE in the original file (an old
    one-way-follow shape, then a two-way shape). Python silently keeps
    only the second definition, so the first was already dead — but it's
    confusing dead code and a trap for the next edit. Keeping only the
    two-way shape that views.py actually returns.
    """

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
    is_restricted_view = serializers.BooleanField()
    data = serializers.DictField()


class ProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # 🔥 FIX: `is_private` is a user-facing privacy toggle — it was
        # defined on the model and referenced everywhere in views, but
        # there was no way for a user to actually flip it via the API.
        fields = ["username", "first_name", "last_name", "bio", "profile_photo", "is_private"]
        extra_kwargs = {
            "username": {"required": False},
            "first_name": {"required": False},
            "last_name": {"required": False},
            # 🔥 FIX: unbounded TextField + MultiPartParser form field with
            # no cap is an easy abuse/DoS vector. 500 chars is a reasonable
            # Instagram-style bio limit — adjust to taste.
            "bio": {"required": False, "max_length": 500},
            "profile_photo": {"required": False},
            "is_private": {"required": False},
        }

    def validate_username(self, value):
        user = self.context["request"].user
        # 🔥 FIX: case-sensitive uniqueness lets "Sam" and "sam" coexist,
        # which is a common source of impersonation/confusion complaints
        # in production. Compare case-insensitively.
        if User.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError("Ye username already taken hai.")
        return value


# 🔥 Block / Unblock user
# Model (BlockUser) profile app me hi hai, isliye API bhi yahin banai
# hai — message app ise sirf consume karega (chat screen "blocked?"
# check waghera ke liye).
class BlockUserSerializer(serializers.ModelSerializer):
    # Flutter POST body me sirf {"blocked": "<user_id>"} bhejta hai.
    blocked_detail = UserSearchSerializer(source="blocked", read_only=True)

    class Meta:
        model = BlockUser
        fields = ["id", "blocked", "blocked_detail", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_blocked(self, value):
        request = self.context["request"]
        if value == request.user:
            raise serializers.ValidationError("Aap khud ko block nahi kar sakte.")
        return value