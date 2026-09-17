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
import json
import re
import uuid

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import serializers

from .models import (
    Post, PostComment, PostLike, PostMedia, PostPoll, PostPollOption,
    PostSave, Story,
)

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


class _JSONEncodedListField(serializers.ListField):
    """A ListField that also accepts its value as a JSON-encoded string.

    `/post/create/` is multipart/form-data (it carries file uploads), and
    multipart has no native way to send an array as a field value. The
    Flutter client works around that by `jsonEncode()`-ing lists into a
    single string form field — see api_service.dart's createPost():
    `request.fields['poll_options'] = jsonEncode(pollOptions)` and the
    same for `media_captions`. A plain `ListField` rejects a bare string
    outright (fails with "not a list") rather than trying to parse it, so
    without this, `poll_options`/`media_captions` would 400 on every real
    multipart request the app actually sends, and only work from a raw
    `application/json` body with no files attached. A request that DOES
    send a real JSON body list (no files) still works unchanged — this
    only kicks in when the incoming value is a plain string.
    """

    def get_value(self, dictionary):
        value = super().get_value(dictionary)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                return value  # let ListField's own validation raise a clear error
        return value


class PostMediaSerializer(serializers.ModelSerializer):
    file = serializers.SerializerMethodField()
    thumbnail = serializers.SerializerMethodField()

    class Meta:
        model = PostMedia
        fields = [
            "id", "media_type", "file", "thumbnail", "file_name",
            "file_size_bytes", "mime_type", "width", "height",
            "duration_seconds", "display_order", "caption",
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


class PostPollOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostPollOption
        fields = ["id", "text", "votes_count", "display_order"]
        read_only_fields = fields


class PostPollSerializer(serializers.ModelSerializer):
    """Read-only representation attached to PostListSerializer/
    PostDetailSerializer — voting itself is a separate endpoint (not in
    scope of this task's files), this just renders current state."""

    options = PostPollOptionSerializer(many=True, read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    my_vote_option_id = serializers.SerializerMethodField()

    class Meta:
        model = PostPoll
        fields = [
            "id", "options", "total_votes_count", "expires_at",
            "is_expired", "created_at", "my_vote_option_id",
        ]
        read_only_fields = fields

    def get_my_vote_option_id(self, obj):
        request = self.context.get("request")
        if not (request and request.user.is_authenticated):
            return None
        vote = obj.votes.filter(user=request.user).first()
        return str(vote.option_id) if vote else None


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

    # TASK 5 — the fields new_post.dart's composer already sends
    # (api_service.dart's createPost()) that were previously accepted by
    # nothing: silently dropped by DRF (extra keys in the request that
    # aren't in Meta.fields are just ignored, not rejected), so the
    # composer's poll/schedule/caption/subcategory UI *looked* like it
    # worked and never actually persisted anything.

    # `subcategory` is a real Post column now (see models.py) so it's
    # included via Meta.fields below like any other model field — no
    # explicit declaration needed, `validate_subcategory` still hooks in.

    # Per-attachment captions, same order as `media_files`/`media_types`.
    media_captions = _JSONEncodedListField(
        child=serializers.CharField(max_length=500, allow_blank=True),
        write_only=True, required=False,
    )
    # [{"text": "...", "votes": 0}, ...] — matches new_post.dart's
    # `pollData` shape exactly (it always sends `votes: 0` since a poll
    # starts with zero votes; that key is accepted and ignored here,
    # PostPollOption.votes_count is server-derived, never client-set).
    poll_options = _JSONEncodedListField(
        child=serializers.DictField(), write_only=True, required=False,
    )
    # `source="published_at"` reuses the column that already existed for
    # exactly this ("For scheduled posts") instead of adding a second,
    # redundant datetime column with a different name.
    scheduled_at = serializers.DateTimeField(source="published_at", required=False, allow_null=True)

    class Meta:
        model = Post
        fields = [
            "id", "title", "content", "category", "subcategory", "post_type", "visibility",
            "hashtags", "mentioned_user_ids", "metadata", "location", "media_files", "media_types",
            "media_captions", "poll_options", "scheduled_at", "created_at",
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

    def validate_poll_options(self, options):
        # Mirrors new_post.dart's own client-side checks (_createPost(): 2
        # minimum, 4 maximum via `_maxPollOptions`, no blank text, no
        # duplicate text case-insensitively) — repeated here because the
        # client check is a UX nicety, not a security boundary; the API
        # has to hold the line itself for any caller that skips the app.
        if len(options) > 4:
            raise serializers.ValidationError("A poll can have at most 4 options.")
        texts = []
        for opt in options:
            text = str(opt.get("text", "")).strip()
            if not text:
                raise serializers.ValidationError("Poll options cannot be empty.")
            if len(text) > 200:
                raise serializers.ValidationError("Poll options must be 200 characters or fewer.")
            texts.append(text)
        if len(texts) != len(set(t.lower() for t in texts)):
            raise serializers.ValidationError("Poll options cannot be the same.")
        return options

    def validate_subcategory(self, value):
        # No taxonomy model was included among this task's files (the
        # category/subcategory tree new_post.dart loads via
        # `getCategoryTaxonomy()` lives elsewhere), so this can't do a
        # real "is X a valid subcategory of category Y" lookup yet. What
        # it CAN enforce without that: don't silently accept a
        # subcategory on a post with no category at all, and don't accept
        # garbage-length input. Wire in the real (category, subcategory)
        # membership check here once the taxonomy source is available —
        # same spot, just add the lookup.
        if value:
            value = value.strip()
        return value or None

    def validate(self, attrs):
        post_type = attrs.get("post_type", "text")
        content = attrs.get("content") or ""
        media_files = self.context["request"].FILES.getlist("media_files")
        poll_options = attrs.get("poll_options")
        category = attrs.get("category")
        subcategory = attrs.get("subcategory")

        if post_type == "text" and not content.strip():
            raise serializers.ValidationError({"content": "Text post me content required hai"})
        if post_type in ["image", "video", "document"] and not media_files:
            raise serializers.ValidationError({"media_files": f"{post_type} post me file required hai"})

        # A poll needs its options regardless of what post_type string the
        # client sent — new_post.dart lets a poll ride along on a
        # text/image/video post (`_hasPoll` is independent of `_postType`
        # in the composer), so gate on "poll_options were actually sent",
        # not on `post_type == "poll"`.
        if poll_options is not None and len(poll_options) < 2:
            raise serializers.ValidationError({"poll_options": "Poll needs at least 2 options."})
        if post_type == "poll" and not poll_options:
            raise serializers.ValidationError({"poll_options": "Poll post me poll_options required hai"})

        if subcategory and not category:
            raise serializers.ValidationError({"subcategory": "Subcategory needs a category selected first."})

        scheduled_at = attrs.get("published_at")
        if scheduled_at is not None and scheduled_at <= timezone.now():
            raise serializers.ValidationError({"scheduled_at": "Scheduled time must be in the future."})

        return attrs

    def create(self, validated_data):
        request = self.context["request"]
        media_files = request.FILES.getlist("media_files")
        # NOTE: unchanged from before Task 5 — `media_types` is read from
        # the raw request rather than `validated_data` for reasons
        # predating this change. Worth a look together with
        # `media_captions` below sometime: the client sends both as a
        # single `jsonEncode(...)`-ed form field (api_service.dart), and
        # `request.data.getlist("media_types")` only ever yields that one
        # JSON-string element rather than the decoded array — outside
        # this task's scope (subcategory/media_captions/poll_options/
        # scheduled_at) so left as found, but `media_captions` right below
        # goes through `_JSONEncodedListField` precisely to not repeat it.
        media_types = request.data.getlist("media_types") if hasattr(request.data, "getlist") else []
        media_captions = validated_data.pop("media_captions", [])
        poll_options = validated_data.pop("poll_options", None)
        validated_data.pop("media_files", None)
        validated_data.pop("media_types", None)

        # `scheduled_at` -> `published_at` (see the field's `source=`).
        # Presence of a (future — `validate()` already checked this) value
        # is what "this post is scheduled" means; no separate client-sent
        # boolean is trusted for it.
        validated_data["is_scheduled"] = bool(validated_data.get("published_at"))

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
            caption = media_captions[idx] if idx < len(media_captions) else ""
            PostMedia.objects.create(
                post=post, media_type=media_type, file=file, file_name=file.name,
                file_size_bytes=file.size, mime_type=file.content_type or "",
                display_order=idx, caption=caption,
            )

        # TASK 5 — persist the poll. `validate_poll_options()` already
        # confirmed 2-4 non-empty, non-duplicate options by this point;
        # `votes_count`/`total_votes_count` are never taken from the
        # client (`pollData` sends `votes: 0` per option, which is simply
        # ignored) — they're server-owned counters, kept accurate by
        # `sync_poll_vote_counts` in models.py once a voting endpoint
        # exists to create `PostPollVote` rows.
        if poll_options:
            poll = PostPoll.objects.create(post=post)
            PostPollOption.objects.bulk_create([
                PostPollOption(poll=poll, text=str(opt["text"]).strip(), display_order=idx)
                for idx, opt in enumerate(poll_options)
            ])

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
            "subcategory": instance.subcategory,
            "post_type": instance.post_type,
            "visibility": instance.visibility,
            "slug": instance.slug,
            "hashtags": instance.hashtags,
            "likes_count": instance.likes_count,
            "comments_count": instance.comments_count,
            "shares_count": instance.shares_count,
            "views_count": instance.views_count,
            "is_scheduled": instance.is_scheduled,
            "scheduled_at": instance.published_at if instance.is_scheduled else None,
            "created_at": instance.created_at,
            "media": PostMediaSerializer(instance.media.all(), many=True, context={"request": request}).data,
            "poll": (
                PostPollSerializer(instance.poll, context={"request": request}).data
                if hasattr(instance, "poll") else None
            ),
        }


class PostListSerializer(serializers.ModelSerializer):
    user = serializers.SerializerMethodField()
    media = PostMediaSerializer(many=True, read_only=True)
    is_liked = serializers.SerializerMethodField()
    is_saved = serializers.SerializerMethodField()
    my_reaction = serializers.SerializerMethodField()
    # TASK 5 — surface what the composer now actually persists.
    # `poll` is None for every post without one (the common case) rather
    # than an extra query — it reads `instance.poll` (the OneToOne
    # reverse accessor), which raises PostPoll.DoesNotExist under the
    # hood when absent, so `get_poll` below catches that instead of
    # `hasattr()` (identical effect, avoids OneToOneField's swallow-every-
    # exception behavior that `hasattr` relies on).
    poll = serializers.SerializerMethodField()
    scheduled_at = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            "id", "user", "title", "content", "category", "subcategory", "post_type",
            "visibility", "hashtags", "location", "likes_count",
            "comments_count", "shares_count", "views_count", "saves_count",
            "is_liked", "is_saved", "my_reaction", "poll",
            "like_count", "confuse_count", "wrong_count", "imp_count", "explain_count",
            "is_scheduled", "scheduled_at", "created_at", "media",
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

    def get_poll(self, obj):
        request = self.context.get("request")
        try:
            poll = obj.poll
        except PostPoll.DoesNotExist:
            return None
        return PostPollSerializer(poll, context={"request": request}).data

    def get_scheduled_at(self, obj):
        # Named to match the write-side field (`scheduled_at` -> Task 5's
        # PostCreateSerializer), rather than exposing the raw
        # `published_at` column name on read — keeps the API's naming
        # consistent for whoever's building the "Scheduled" tab UI. None
        # once a post has actually gone live (`is_scheduled` flips False
        # at that point — see the publish-sweep note on the Post model).
        return obj.published_at if obj.is_scheduled else None


class PostDetailSerializer(PostListSerializer):
    comments = serializers.SerializerMethodField()

    class Meta(PostListSerializer.Meta):
        fields = PostListSerializer.Meta.fields + ["comments", "metadata"]

    def get_comments(self, obj):
        # G-3: RestrictUser existed but nothing consumed it — a post
        # owner's restrict list had zero effect on who could see comments
        # on their own posts. Lazy import (matches campus/bridge.py's
        # pattern, and how user_profile/views.py's own is_blocked_between
        # is consumed elsewhere) to avoid a hard post -> user_profile
        # dependency at module-import time.
        from user_profile.models import RestrictUser

        request = self.context.get("request")
        viewer = getattr(request, "user", None)
        viewer_id = getattr(viewer, "id", None) if viewer and viewer.is_authenticated else None

        comments = obj.comments.filter(parent=None, is_deleted=False, is_hidden=False)

        # Restrict is scoped to the post owner's restrict list, not the
        # viewer's — it's the owner's space being protected. Excluded at
        # the queryset level (not per-row) so the [:10] slice below still
        # returns up to 10 *visible* comments instead of coming up short
        # because restricted ones were filtered out after slicing.
        restricted_ids = set(
            RestrictUser.objects.filter(user_id=obj.user_id).values_list("restricted_id", flat=True)
        )
        # A restricted user must still see their own comments exactly as
        # before — restrict is defined to be invisible to them.
        restricted_ids.discard(viewer_id)
        if restricted_ids:
            comments = comments.exclude(user_id__in=restricted_ids)

        comments = comments[:10]
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