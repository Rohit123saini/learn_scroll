from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import LeaderboardEntry


class LeaderboardUserSerializer(serializers.ModelSerializer):
    """Same minimal shape as `tuitionclass.serializers.UserMiniSerializer` /
    `message.serializers.UserMiniSerializer` — kept as its own copy (not a
    shared import) for the same reason those two don't import each other:
    this app must never force a load-order dependency on tuitionclass/message."""

    full_name = serializers.SerializerMethodField()
    profile_picture = serializers.SerializerMethodField()

    class Meta:
        model = get_user_model()
        fields = ["id", "username", "full_name", "profile_picture"]

    def get_full_name(self, obj):
        name = obj.get_full_name()
        return name if name else obj.username

    def get_profile_picture(self, obj):
        for attr in ("profile_photo", "profile_picture", "avatar", "photo", "image"):
            value = getattr(obj, attr, None)
            if value:
                try:
                    return value.url
                except (AttributeError, ValueError):
                    return None
        return None


class LeaderboardEntrySerializer(serializers.ModelSerializer):
    user = LeaderboardUserSerializer(read_only=True)
    is_me = serializers.SerializerMethodField()

    class Meta:
        model = LeaderboardEntry
        fields = ["rank", "score", "user", "metadata", "is_me"]

    def get_is_me(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_authenticated", False) and obj.user_id == user.id)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # P-F16 -- on a PUBLIC board a minor's name / photo are hidden from adult strangers (view passes the id set in
        # context; empty / missing = nobody masked, e.g. campus-section boards where everybody is a classmate).
        masked = self.context.get("masked_minor_ids") or ()
        if instance.user_id in masked and isinstance(data.get("user"), dict):
            from common.minor_policy import masked_person

            data["user"] = {"id": data["user"].get("id"), **masked_person(instance.user)}
        return data
