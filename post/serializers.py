
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils.text import slugify
from .models import *
import re
import uuid

User = get_user_model()


# class PostMediaSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = PostMedia
#         fields = ['id', 'media_type', 'file', 'thumbnail', 'file_name',
#                   'file_size_bytes', 'mime_type', 'width', 'height',
#                   'duration_seconds', 'display_order']
#         read_only_fields = ['id', 'file_name', 'file_size_bytes', 'mime_type']
#
#     def validate_file(self, file):
#         if file.size > 100 * 1024 * 1024:
#             raise serializers.ValidationError("File size cannot exceed 100MB")
#         return file



class PostMediaSerializer(serializers.ModelSerializer):
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = ['id', 'media_type', 'file', 'thumbnail', 'file_name',
                  'file_size_bytes', 'mime_type', 'width', 'height',
                  'duration_seconds', 'display_order']
        read_only_fields = ['id', 'file_name', 'file_size_bytes', 'mime_type', 'thumbnail']

    def validate_file(self, file):
        if file.size > 100 * 1024 * 1024:
            raise serializers.ValidationError("File size cannot exceed 100MB")
        return file

    def get_thumbnail(self, obj):
        """
        Agar absolute thumbnail file backend par ready hai, to absolute URL return karega.
        """
        request = self.context.get('request')
        if obj.thumbnail:
            # Custom view local proxy check
            if request is not None:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        return None


# 👇 Ye naya add kar - PostCommentSerializer
class PostCommentSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    replies_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PostComment
        fields = [
            'id', 'user', 'content', 'likes_count', 'replies_count',
            'is_edited', 'is_pinned', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'likes_count', 'created_at', 'updated_at']

    def get_user(self, obj):
        return {
            'id': str(obj.user.id),
            'username': obj.user.username,
            'profile_picture': obj.user.profile_picture.url if hasattr(obj.user,
                                                                       'profile_picture') and obj.user.profile_picture else None
        }


class PostCreateSerializer(serializers.ModelSerializer):
    media_files = serializers.ListField(
        child=serializers.FileField(),
        write_only=True,
        required=False
    )
    media_types = serializers.ListField(
        child=serializers.CharField(),
        write_only=True,
        required=False
    )
    hashtags = serializers.ListField(
        child=serializers.CharField(max_length=100),
        required=False,
        allow_empty=True
    )

    class Meta:
        model = Post
        fields = [
            'id', 'title', 'content', 'category', 'post_type',
            'visibility', 'hashtags', 'mentioned_user_ids',
            'metadata', 'location', 'media_files', 'media_types', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def validate(self, attrs):
        post_type = attrs.get('post_type', 'text')
        content = attrs.get('content', '')
        media_files = self.context['request'].FILES.getlist('media_files')

        if post_type == 'text' and not content.strip():
            raise serializers.ValidationError({"content": "Text post me content required hai"})

        if post_type in ['image', 'video', 'document'] and not media_files:
            raise serializers.ValidationError({"media_files": f"{post_type} post me file required hai"})

        return attrs

    def create(self, validated_data):
        request = self.context['request']
        media_files = request.FILES.getlist('media_files')
        media_types = request.data.getlist('media_types')

        validated_data.pop('media_files', None)
        validated_data.pop('media_types', None)

        content = validated_data.get('content', '')

        if not validated_data.get('hashtags') and content:
            hashtags = re.findall(r'#(\w+)', content)
            validated_data['hashtags'] = list(set(hashtags))

        if not validated_data.get('slug'):
            base_text = validated_data.get('title') or content[:50] or str(uuid.uuid4())[:8]
            slug = slugify(base_text)[:200]
            if Post.objects.filter(slug=slug).exists():
                slug = f"{slug}-{uuid.uuid4().hex[:6]}"
            validated_data['slug'] = slug

        post = Post.objects.create(**validated_data)

        for idx, file in enumerate(media_files):
            media_type = media_types[idx] if idx < len(media_types) else 'image'
            PostMedia.objects.create(
                post=post,
                media_type=media_type,
                file=file,
                file_name=file.name,
                file_size_bytes=file.size,
                mime_type=file.content_type,
                display_order=idx
            )

        return post

    def to_representation(self, instance):
        data = {
            'id': str(instance.id),
            'user': {
                'id': str(instance.user.id),
                'username': instance.user.username,
            },
            'title': instance.title,
            'content': instance.content,
            'category': instance.category,
            'post_type': instance.post_type,
            'visibility': instance.visibility,
            'slug': instance.slug,
            'hashtags': instance.hashtags,
            'likes_count': instance.likes_count,
            'comments_count': instance.comments_count,
            'shares_count': instance.shares_count,
            'views_count': instance.views_count,
            'created_at': instance.created_at,
            'media': PostMediaSerializer(instance.media.all(), many=True).data
        }
        return data


class PostListSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = PostMediaSerializer(many=True, read_only=True)
    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'user', 'title', 'content', 'category', 'post_type',
            'visibility', 'hashtags', 'location', 'likes_count',
            'comments_count', 'shares_count', 'views_count', 'saves_count',
            'is_liked', 'is_saved', 'created_at', 'media'
        ]

    def get_user(self, obj):
        return {
            'id': str(obj.user.id),
            'username': obj.user.username,
            'profile_picture': obj.user.profile_picture.url if hasattr(obj.user,
                                                                       'profile_picture') and obj.user.profile_picture else None
        }

    def get_is_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return PostLike.objects.filter(post=obj, user=request.user).exists()
        return False

    def get_is_saved(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return PostSave.objects.filter(post=obj, user=request.user).exists()
        return False


class PostDetailSerializer(PostListSerializer):
    comments = serializers.SerializerMethodField()

    class Meta(PostListSerializer.Meta):
        fields = PostListSerializer.Meta.fields + ['comments', 'metadata']

    def get_comments(self, obj):
        comments = obj.comments.filter(parent=None, is_deleted=False)[:10]






class ReactionRequestSerializer(serializers.Serializer):
    reaction = serializers.ChoiceField(
        choices=['like', 'confuse', 'wrong', 'imp', 'explain'],
        help_text="Reaction type: like, confuse, wrong, imp, explain"
    )

class ReactionResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    reaction = serializers.CharField(allow_null=True)
    counts = serializers.DictField()
    my_reaction = serializers.CharField(allow_null=True)


class PostSerializer(serializers.ModelSerializer):
    # User ne khud kya reaction diya hai wo bhi dikhana hai
    my_reaction = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'user', 'content', 'title', 'category',
            'post_type', 'created_at',
            # Tera purana wala total count
            'likes_count', 'comments_count',
            # Ye naye wale 5 counts jo frontend pe dikhenge
            'like_count', 'confuse_count', 'wrong_count',
            'imp_count', 'explain_count',
            'my_reaction',
            'media'
        ]

    def get_my_reaction(self, obj):
        # request user ne is post pe kya reaction diya hai
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            # reactions ko prefetch karne se fast hoga
            # warna har post pe query jayegi
            reaction = obj.reactions.filter(user=request.user).first()
            if reaction:
                return reaction.reaction_type
        return None
