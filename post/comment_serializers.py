# from rest_framework import serializers
# from .models import PostComment, CommentMedia
# from django.contrib.auth import get_user_model
#
# User = get_user_model()
#
#
# class CommentMediaSerializer(serializers.ModelSerializer):
#     # Flutter ke liye camelCase me bhi bhej rahe hain + absolute url
#     file = serializers.SerializerMethodField()
#     file_name = serializers.CharField(read_only=True)
#     file_size = serializers.IntegerField(read_only=True)
#
#     # Flutter me fileName fileSize use hota hai
#     fileName = serializers.CharField(source='file_name', read_only=True)
#     fileSize = serializers.IntegerField(source='file_size', read_only=True)
#     mimeType = serializers.CharField(source='mime_type', read_only=True)
#     mediaType = serializers.CharField(source='media_type', read_only=True)
#
#     class Meta:
#         model = CommentMedia
#         fields = ['id', 'media_type', 'mediaType', 'file', 'file_name', 'fileName', 'file_size', 'fileSize',
#                   'mime_type', 'mimeType', 'created_at']
#         read_only_fields = ['id', 'file_name', 'file_size']
#
#     def get_file(self, obj):
#         if not obj.file:
#             return None
#         request = self.context.get('request')
#         try:
#             url = obj.file.url
#             if request:
#                 return request.build_absolute_uri(url)
#             return url
#         except:
#             return str(obj.file)
#
#
# class UserShortSerializer(serializers.ModelSerializer):
#     # Tere User model me profile_photo hai
#     profile_picture = serializers.SerializerMethodField()
#     profilePicture = serializers.SerializerMethodField()  # Flutter ke liye camelCase bhi
#
#     class Meta:
#         model = User
#         fields = ['id', 'username', 'profile_picture', 'profilePicture']
#
#     def get_profile_picture(self, obj):
#         request = self.context.get('request')
#         field = None
#         if hasattr(obj, 'profile_photo') and obj.profile_photo:
#             field = obj.profile_photo
#         elif hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
#             field = obj.profile_picture
#
#         if field:
#             try:
#                 url = field.url
#                 if request:
#                     return request.build_absolute_uri(url)
#                 return url
#             except:
#                 return str(field)
#         return None
#
#     def get_profilePicture(self, obj):
#         return self.get_profile_picture(obj)
#
#
# class PostCommentSerializer(serializers.ModelSerializer):
#     user = UserShortSerializer(read_only=True)
#     media = CommentMediaSerializer(many=True, read_only=True)
#
#     # Flutter compatibility - snake + camel dono
#     likes_count = serializers.IntegerField(read_only=True)
#     replies_count = serializers.IntegerField(read_only=True)
#     likesCount = serializers.IntegerField(source='likes_count', read_only=True)
#     repliesCount = serializers.IntegerField(source='replies_count', read_only=True)
#
#     class Meta:
#         model = PostComment
#         fields = [
#             'id', 'post', 'user', 'parent', 'content', 'media',
#             'likes_count', 'likesCount', 'replies_count', 'repliesCount',
#             'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at'
#         ]
#         read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']
#
#
# class CreateCommentSerializer(serializers.Serializer):
#     post_id = serializers.UUIDField(required=False, allow_null=True)
#     parent_id = serializers.UUIDField(required=False, allow_null=True)
#     content = serializers.CharField(required=False, allow_blank=True, default='')
#
#     def to_internal_value(self, data):
#         mutable = data.copy() if hasattr(data, 'copy') else dict(data)
#         if mutable.get('parent_id') in ['', 'string', 'null']:
#             mutable['parent_id'] = None
#         if mutable.get('post_id') in ['', 'string', 'null']:
#             mutable['post_id'] = None
#         return super().to_internal_value(mutable)
#
#     def validate(self, attrs):
#         if not attrs.get('post_id') and not attrs.get('parent_id'):
#             raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
#         return attrs



















from rest_framework import serializers
from django.db.models import Count
from.models import PostComment, CommentMedia, CommentLike
from django.contrib.auth import get_user_model

User = get_user_model()

class CommentMediaSerializer(serializers.ModelSerializer):
    # Flutter ke liye camelCase me bhi bhej rahe hain + absolute url
    file = serializers.SerializerMethodField()
    file_name = serializers.CharField(read_only=True)
    file_size = serializers.IntegerField(read_only=True)

    # Flutter me fileName fileSize use hota hai
    fileName = serializers.CharField(source='file_name', read_only=True)
    fileSize = serializers.IntegerField(source='file_size', read_only=True)
    mimeType = serializers.CharField(source='mime_type', read_only=True)
    mediaType = serializers.CharField(source='media_type', read_only=True)

    class Meta:
        model = CommentMedia
        fields = ['id', 'media_type', 'mediaType', 'file', 'file_name', 'fileName', 'file_size', 'fileSize',
                  'mime_type', 'mimeType', 'created_at']
        read_only_fields = ['id', 'file_name', 'file_size']

    def get_file(self, obj):
        if not obj.file:
            return None
        request = self.context.get('request')
        try:
            url = obj.file.url
            if request:
                return request.build_absolute_uri(url)
            return url
        except:
            return str(obj.file)

class UserShortSerializer(serializers.ModelSerializer):
    # Tere User model me profile_photo hai
    profile_picture = serializers.SerializerMethodField()
    profilePicture = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'profile_picture', 'profilePicture']

    def get_profile_picture(self, obj):
        request = self.context.get('request')
        field = None
        if hasattr(obj, 'profile_photo') and obj.profile_photo:
            field = obj.profile_photo
        elif hasattr(obj, 'profile_picture') and getattr(obj, 'profile_picture', None):
            field = obj.profile_picture

        if field:
            try:
                url = field.url
                if request:
                    return request.build_absolute_uri(url)
                return url
            except:
                return str(field)
        return None

    def get_profilePicture(self, obj):
        return self.get_profile_picture(obj)

class PostCommentSerializer(serializers.ModelSerializer):
    user = UserShortSerializer(read_only=True)
    media = CommentMediaSerializer(many=True, read_only=True)

    # Flutter compatibility - snake + camel dono
    likes_count = serializers.IntegerField(read_only=True)
    replies_count = serializers.IntegerField(read_only=True)
    likesCount = serializers.IntegerField(source='likes_count', read_only=True)
    repliesCount = serializers.IntegerField(source='replies_count', read_only=True)

    # NEW - Comment reaction like post
    my_reaction = serializers.SerializerMethodField()
    myReaction = serializers.SerializerMethodField()
    reaction_counts = serializers.SerializerMethodField()
    reactionCounts = serializers.SerializerMethodField()

    class Meta:
        model = PostComment
        fields = [
            'id', 'post', 'user', 'parent', 'content', 'media',
            'likes_count', 'likesCount', 'replies_count', 'repliesCount',
            'is_edited', 'is_pinned', 'is_hidden', 'created_at', 'updated_at',
            'my_reaction', 'myReaction', 'reaction_counts', 'reactionCounts'
        ]
        read_only_fields = ['id', 'likes_count', 'replies_count', 'is_edited', 'created_at', 'updated_at']

    def get_my_reaction(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            like = CommentLike.objects.filter(comment=obj, user=request.user).first()
            return like.reaction_type if like else None
        return None

    def get_myReaction(self, obj):
        return self.get_my_reaction(obj)

    def get_reaction_counts(self, obj):
        qs = CommentLike.objects.filter(comment=obj).values('reaction_type').annotate(c=Count('id'))
        counts = {r['reaction_type']: r['c'] for r in qs}
        return {
            'like': counts.get('like', 0),
            'confuse': counts.get('confuse', 0),
            'wrong': counts.get('wrong', 0),
            'imp': counts.get('imp', 0),
            'explain': counts.get('explain', 0),
            'total': sum(counts.values())
        }

    def get_reactionCounts(self, obj):
        return self.get_reaction_counts(obj)

class CreateCommentSerializer(serializers.Serializer):
    post_id = serializers.UUIDField(required=False, allow_null=True)
    parent_id = serializers.UUIDField(required=False, allow_null=True)
    content = serializers.CharField(required=False, allow_blank=True, default='')

    def to_internal_value(self, data):
        mutable = data.copy() if hasattr(data, 'copy') else dict(data)
        if mutable.get('parent_id') in ['', 'string', 'null']:
            mutable['parent_id'] = None
        if mutable.get('post_id') in ['', 'string', 'null']:
            mutable['post_id'] = None
        return super().to_internal_value(mutable)

    def validate(self, attrs):
        if not attrs.get('post_id') and not attrs.get('parent_id'):
            raise serializers.ValidationError({"post_id": "post_id is required for top-level comment"})
        return attrs