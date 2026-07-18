from .models import *
from rest_framework import serializers
from .models import PostComment, CommentMedia
from django.contrib.auth import get_user_model

User = get_user_model()

class CommentMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'file', 'file_name', 'file_size', 'mime_type', 'created_at']

class UserShortSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'profile_picture']

class PostCommentSerializer(serializers.ModelSerializer):
    user = UserShortSerializer(read_only=True)
    media = CommentMediaSerializer(many=True, read_only=True)
    replies_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PostComment
        fields = ['id', 'post', 'user', 'parent', 'content', 'media', 'likes_count', 'replies_count', 'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at']

class CreateCommentSerializer(serializers.Serializer):
    post_id = serializers.UUIDField(required=False)
    parent_id = serializers.UUIDField(required=False)
    content = serializers.CharField(required=False, allow_blank=True)
    files = serializers.ListField(child=serializers.FileField(), required=False, max_length=5)

    def validate(self, attrs):
        if not attrs.get('content') and not attrs.get('files'):
            raise serializers.ValidationError("Content or media file is required")
        if not attrs.get('post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError("post_id is required")
        return attrs