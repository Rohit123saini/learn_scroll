"""
post/serializers.py

⚠️ CRITICAL FIX — the file uploaded under this name imported
`Comment, Hashtag, Like, Post, PostMedia, SavedPost, Story, StoryView`
from `.models`. Your actual `models.py` defines `Post`, `PostMedia`,
`PostLike`, `PostComment`, `CommentMedia`, `PostShare`, `PostView`,
`PostSave`, `ChunkedUpload`, `CommentLike` — there is no `Comment`,
`Hashtag`, `Like`, `SavedPost`, `Story`, or `StoryView` model anywhere in
this app. That file would raise `ImportError` the instant Django tried to
load it, which means the whole `post` app (and anything that imports from
it, including `views.py`) would fail at startup. This is a full rewrite
against the real schema, matching what `views.py` / `post_app.md` §4
actually expect.
"""
import re
import uuid

from django.contrib.auth import get_user_model
from django.utils.text import slugify
from rest_framework import serializers

from .models import Post, PostComment, PostLike, PostMedia, PostSave, Story

User = get_user_model()

# 🔥 FIX: extension + size limits were only declared as decoration —
# `PostMediaSerializer.validate_file()` below is never actually invoked by
# `PostCreateSerializer.create()`, which builds `PostMedia` rows directly
# with `.objects.create()`. Django only runs a model field's own
# `FileExtensionValidator` during `full_clean()`, which `.create()` never
# calls — so the extension allowlist on `PostMedia.file` was silently
# dead too. `attach_media_files()` below is the single place that now
# actually enforces both, shared by every place that creates `PostMedia`.
MAX_MEDIA_FILE_SIZE = 100 * 1024 * 1024  # 100MB
ALLOWED_MEDIA_EXTENSIONS = {
    "jpg", "jpeg", "png", "gif", "mp4", "mov", "avi",
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "mp3", "wav", "zip", "txt",
}


def validate_media_file(file):
    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    if ext not in ALLOWED_MEDIA_EXTENSIONS:
        raise serializers.ValidationError(f"'.{ext}' files are not allowed.")
    if file.size > MAX_MEDIA_FILE_SIZE:
        raise serializers.ValidationError("File size cannot exceed 100MB.")
    return file


def get_profile_pic_url(user, request):
    """Common function for profile pic — checks several possible field names
    so this keeps working across small variations in the User model shape."""
    if not user:
        return None
    field = None
    for attr in ("profile_photo", "profile_picture", "avatar", "image"):
        candidate = getattr(user, attr, None)
        if candidate:
            field = candidate
            break
    if not field:
        return None
    try:
        url = field.url
    except ValueError:
        return None
    return request.build_absolute_uri(url) if request is not None else url


class PostMediaSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = [
            "id", "media_type", "file", "thumbnail", "file_name",
            "file_size_bytes", "mime_type", "width", "height",
            "duration_seconds", "display_order",
        ]
        read_only_fields = ["id", "file_name", "file_size_bytes", "mime_type", "thumbnail"]

    def get_file(self, obj):
        request = self.context.get("request")
        if not obj.file:
            return None
        try:
            return request.build_absolute_uri(obj.file.url) if request else obj.file.url
        except ValueError:
            return None

    def get_thumbnail(self, obj):
        request = self.context.get("request")
        if not obj.thumbnail:
            return None
        try:
            return request.build_absolute_uri(obj.thumbnail.url) if request else obj.thumbnail.url
        except ValueError:
            return None


class PostCommentPreviewSerializer(serializers.ModelSerializer):
    """
    🔥 RENAMED from `PostCommentSerializer` (see post_app.md §4's ⚠️ box).
    This is deliberately the *lightweight* comment shape used only for the
    first 10 top-level comments inlined on `PostDetailSerializer` — no
    media, no per-reaction-type breakdown. Every dedicated comment
    endpoint (comment_view.py) uses the full
    `comment_serializers.PostCommentSerializer` instead. Same name on two
    unrelated classes was a real foot-gun for anyone doing
    `from .serializers import PostCommentSerializer` and getting the
    wrong one silently — renaming this one removes the collision without
    changing behavior anywhere (nothing else in this app imported this
    class by name).
    """

    user = serializers.SerializerMethodField()
    replies_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = PostComment
        fields = [
            "id", "user", "content", "likes_count", "replies_count",
            "is_edited", "is_pinned", "created_at", "updated_at",
        ]
        read_only_fields = ["id", "likes_count", "created_at", "updated_at"]

    def get_user(self, obj):
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }


class PostCreateSerializer(serializers.ModelSerializer):
    media_files = serializers.ListField(child=serializers.FileField(), write_only=True, required=False)
    media_types = serializers.ListField(child=serializers.CharField(), write_only=True, required=False)
    hashtags = serializers.ListField(child=serializers.CharField(max_length=100), required=False, allow_empty=True)

    class Meta:
        model = Post
        fields = [
            "id", "title", "content", "category", "post_type", "visibility", "hashtags",
            "mentioned_user_ids", "metadata", "location", "media_files", "media_types", "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_media_files(self, files):
        # 🔥 FIX: this is the hook that was missing entirely — extension/size
        # now actually get checked before any file touches disk.
        if len(files) > 10:
            raise serializers.ValidationError("A post can have at most 10 media files.")
        for f in files:
            validate_media_file(f)
        return files

    def validate(self, attrs):
        post_type = attrs.get("post_type", "text")
        content = attrs.get("content") or ""
        media_files = self.context["request"].FILES.getlist("media_files")
        if post_type == "text" and not content.strip():
            raise serializers.ValidationError({"content": "Text post me content required hai"})
        if post_type in ["image", "video", "document"] and not media_files:
            raise serializers.ValidationError({"media_files": f"{post_type} post me file required hai"})
        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        media_files = request.FILES.getlist("media_files")
        media_types = request.data.getlist("media_types") if hasattr(request.data, "getlist") else []
        validated_data.pop("media_files", None)
        validated_data.pop("media_types", None)

        content = validated_data.get("content") or ""
        if not validated_data.get("hashtags") and content:
            validated_data["hashtags"] = re.findall(r"#(\w+)", content)

        # 🔥 FIX: neither the auto-extracted tags above nor a
        # client-supplied `hashtags` list were ever normalized — "#Django"
        # and "#django" saved as two different strings in the JSONField.
        # Harmless for display, but it silently breaks anything that
        # looks tags up by value: HashtagPostsAPIView's `hashtags__contains`
        # filter (views.py) and TrendingHashtagsAPIView's counting
        # (views.py) would both undercount/miss posts whose tag casing
        # didn't happen to match. Normalize once, here, so every consumer
        # of `Post.hashtags` gets consistent values for free.
        if validated_data.get("hashtags"):
            seen = []
            for tag in validated_data["hashtags"]:
                normalized = tag.strip().lstrip("#").lower()
                if normalized and normalized not in seen:
                    seen.append(normalized)
            validated_data["hashtags"] = seen

        if not validated_data.get("slug"):
            base_text = validated_data.get("title") or content[:50] or str(uuid.uuid4())[:8]
            slug = slugify(base_text)[:200] or uuid.uuid4().hex[:8]
            if Post.objects.filter(slug=slug).exists():
                slug = f"{slug}-{uuid.uuid4().hex[:6]}"
            validated_data["slug"] = slug

        post = Post.objects.create(user=request.user, **validated_data)

        for idx, file in enumerate(media_files):
            media_type = media_types[idx] if idx < len(media_types) else "image"
            PostMedia.objects.create(
                post=post, media_type=media_type, file=file, file_name=file.name,
                file_size_bytes=file.size, mime_type=file.content_type or "", display_order=idx,
            )

        # 🔥 FIX: this used to call a `services.attach_hashtags()` that
        # assumed a separate `Hashtag`/`PostHashtag` model — neither
        # exists. `hashtags` is a plain `Post.hashtags` JSONField, and it
        # was already saved on the row by `Post.objects.create(**validated_data)`
        # above (it's part of `validated_data`, either passed in explicitly
        # or auto-extracted from `#tags` in content a few lines up). There
        # is nothing left to do here — see services.py's module docstring
        # for the full story on why that call was dead/broken.

        return post

    def to_representation(self, instance):
        request = self.context.get("request")
        return {
            "id": str(instance.id),
            "user": {
                "id": str(instance.user.id),
                "username": instance.user.username,
                "profile_picture": get_profile_pic_url(instance.user, request),
            },
            "title": instance.title,
            "content": instance.content,
            "category": instance.category,
            "post_type": instance.post_type,
            "visibility": instance.visibility,
            "slug": instance.slug,
            "hashtags": instance.hashtags,
            "likes_count": instance.likes_count,
            "comments_count": instance.comments_count,
            "shares_count": instance.shares_count,
            "views_count": instance.views_count,
            "created_at": instance.created_at,
            "media": PostMediaSerializer(instance.media.all(), many=True, context={"request": request}).data,
        }


class PostListSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = PostMediaSerializer(many=True, read_only=True)
    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "user", "title", "content", "category", "post_type",
            "visibility", "hashtags", "location", "likes_count",
            "comments_count", "shares_count", "views_count", "saves_count",
            "is_liked", "is_saved", "my_reaction",
            "like_count", "confuse_count", "wrong_count", "imp_count", "explain_count",
            "created_at", "media",
        ]

    def get_user(self, obj):
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }

    def get_is_liked(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            # Prefer an annotated value from the view to avoid N+1 on list pages.
            if hasattr(obj, "is_liked_annotated"):
                return obj.is_liked_annotated
            return obj.likes.filter(user=request.user).exists()
        return False

    def get_is_saved(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            if hasattr(obj, "is_saved_annotated"):
                return obj.is_saved_annotated
            return PostSave.objects.filter(post=obj, user=request.user).exists()
        return False

    def get_my_reaction(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            like = obj.likes.filter(user=request.user).first()
            if like:
                return like.reaction_type
        return None


class PostDetailSerializer(PostListSerializer):
    comments = serializers.SerializerMethodField()

    class Meta(PostListSerializer.Meta):
        fields = PostListSerializer.Meta.fields + ["comments", "metadata"]

    def get_comments(self, obj):
        comments = obj.comments.filter(parent=None, is_deleted=False, is_hidden=False)[:10]
        request = self.context.get("request")
        return PostCommentPreviewSerializer(comments, many=True, context={"request": request}).data


class ReactionRequestSerializer(serializers.Serializer):
    reaction = serializers.ChoiceField(choices=["like", "confuse", "wrong", "imp", "explain"])


class ReactionResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    reaction = serializers.CharField(allow_null=True)
    counts = serializers.DictField()
    my_reaction = serializers.CharField(allow_null=True)


class PostSaveSerializer(serializers.ModelSerializer):
    post_id = serializers.UUIDField(write_only=True)
    post = PostListSerializer(read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = PostSave
        fields = ["id", "post_id", "post", "user", "username", "collection_name", "created_at"]
        read_only_fields = ["id", "user", "created_at"]

    def validate_post_id(self, value):
        if not Post.objects.filter(id=value, is_deleted=False).exists():
            raise serializers.ValidationError("Post not found")
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        post_id = validated_data.pop("post_id")
        post = Post.objects.get(id=post_id)
        save_obj, _ = PostSave.objects.get_or_create(
            post=post, user=request.user,
            defaults={"collection_name": validated_data.get("collection_name", "default")},
        )
        return save_obj

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        data["post"] = PostListSerializer(instance.post, context={"request": request}).data
        return data

# ---------------------------------------------------------------------------
# Story serializers (checklist items 54/55/57/60) — new, matching the
# Story/StoryView models added to models.py.
# ---------------------------------------------------------------------------
class StoryCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Story
        fields = ["id", "media", "media_type", "caption", "expires_at", "created_at"]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"expires_at": {"required": False}}

    def validate_media(self, file):
        # Reuses the same allowlist/size limit as post media — a story is
        # just a short-lived image/video, no reason to allow anything a
        # regular PostMedia upload wouldn't.
        return validate_media_file(file)

    def create(self, validated_data):
        request = self.context["request"]
        return Story.objects.create(user=request.user, **validated_data)


class StorySerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = serializers.SerializerMethodField()
    is_viewed_by_me = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            "id", "user", "media", "media_type", "caption",
            "views_count", "is_viewed_by_me", "created_at", "expires_at",
        ]

    def get_user(self, obj):
        request = self.context.get("request")
        return {
            "id": str(obj.user.id),
            "username": obj.user.username,
            "profile_picture": get_profile_pic_url(obj.user, request),
        }

    def get_media(self, obj):
        request = self.context.get("request")
        if not obj.media:
            return None
        try:
            return request.build_absolute_uri(obj.media.url) if request else obj.media.url
        except ValueError:
            return None

    def get_is_viewed_by_me(self, obj):
        request = self.context.get("request")
        if request and request.user.is_authenticated:
            if hasattr(obj, "is_viewed_annotated"):
                return obj.is_viewed_annotated
            return obj.views.filter(user=request.user).exists()
        return False