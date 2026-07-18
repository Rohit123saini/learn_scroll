from rest_framework import serializers
from .models import PostComment, CommentMedia
from django.contrib.auth import get_user_model

User = get_user_model()


class CommentMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'file', 'file_name', 'file_size', 'mime_type', 'created_at']
        read_only_fields = ['id', 'file_name', 'file_size']


class UserShortSerializer(serializers.ModelSerializer):
    # 🔥 FIX: tere User model me profile_photo hai
    profile_picture = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'profile_picture']

    def get_profile_picture(self, obj):
        # profile_photo ya avatar jo bhi ho usko handle karega
        if hasattr(obj, 'profile_photo') and obj.profile_photo:
            try:
                return obj.profile_photo.url
            except:
                return str(obj.profile_photo)
        if hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
            try:
                return obj.profile_picture.url
            except:
                return str(obj.profile_picture)
        return None


class PostCommentSerializer(serializers.ModelSerializer):
    user = UserShortSerializer(read_only=True)
    media = CommentMediaSerializer(many=True, read_only=True)

    class Meta:
        model = PostComment
        fields = ['id', 'post', 'user', 'parent', 'content', 'media', 'likes_count', 'replies_count', 'is_edited',
                  'is_pinned', 'is_hidden', 'created_at', 'updated_at']
        read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']


class CreateCommentSerializer(serializers.Serializer):
    post_id = serializers.UUIDField(required=False, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    content = serializers.CharField(required=False, allow_blank=True, default='')

    def to_internal_value(self, data):
        # Swagger "" empty string bhejta hai, usko None banao
        mutable = data.copy() if hasattr(data, 'copy') else dict(data)
        if mutable.get('parent_id') in ['', 'string', 'null']:
            mutable['parent_id'] = None
        if mutable.get('post_id') in ['', 'string', 'null']:
            mutable['post_id'] = None
        return super().to_internal_value(mutable)

    def validate(self, attrs):
        if not attrs.get('post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
        # content check view me files ke sath hoga, text only / file only dono allowed
        return attrs